"""Native Anthropic adapter (Messages API over plain HTTP; no SDK).

Endpoints: ``GET {api_path}/models`` (models, context window, output limit and
capabilities) and ``POST {api_path}/messages`` with ``stream: true`` (SSE).
Anthropic's OpenAI-compatible endpoint ignores ``reasoning_effort`` and returns
no thinking text, so thinking needs this adapter: effort levels through
``output_config.effort`` with adaptive thinking (a fixed ``budget_tokens`` on
Claude 4.5 and earlier), and thinking summaries streamed as reasoning.

Earlier turns are sent as plain text. Chimera uses no tools, so thinking
blocks from earlier answers (and their signatures) are not sent back.
"""

from __future__ import annotations

import json
import logging
import re
import time
from typing import Any, AsyncIterator

import httpx

from . import reasoning
from .base import (
    ChatRequest,
    HealthReport,
    ModelInfo,
    Provider,
    ProviderError,
    StreamEvent,
    context_overflow_error,
    error_from_response,
    is_context_overflow,
    translate_http_error,
)
from .sse import iter_sse

log = logging.getLogger("chimera.providers")

API_VERSION = "2023-06-01"
DEFAULT_MAX_TOKENS = 32_000
OLD_MAX_TOKENS = 8_192  # Claude 3.x models have small output limits


def _uses_sampling(model: str) -> bool:
    """Claude 4.7 and later reject temperature, top_p and top_k (HTTP 400)."""
    m = model.lower()
    return not re.search(r"claude-(fable|mythos)|(opus|sonnet|haiku)-5|opus-4-[7-9]", m)


class AnthropicProvider(Provider):
    kind = "anthropic"
    supported_params = ("temperature", "top_p", "top_k", "max_tokens", "stop")

    @property
    def api_url(self) -> str:
        path = (self.config.api_path or "").strip() or "/v1"
        if not path.startswith("/"):
            path = "/" + path
        return self.root_url + path.rstrip("/")

    def headers(self) -> dict[str, str]:
        headers = {"Accept": "application/json", "anthropic-version": API_VERSION}
        key = (self.config.api_key or "").strip()
        if key:
            header = (self.config.auth_header or "").strip() or "x-api-key"
            if header.lower() == "authorization" and not key.lower().startswith("bearer "):
                key = f"Bearer {key}"
            headers[header] = key
        return headers

    # -- discovery ----------------------------------------------------------
    async def list_models(self) -> list[ModelInfo]:
        models: list[ModelInfo] = []
        after: str | None = None
        vision = self.vision_override()
        for _ in range(10):  # pages of 100
            params = {"limit": 100, **({"after_id": after} if after else {})}
            try:
                resp = await self.client.get(f"{self.api_url}/models", params=params, headers=self.headers(), timeout=self.timeout(read=30))
            except Exception as exc:  # noqa: BLE001
                raise translate_http_error(exc, self.config.name) from exc
            if resp.status_code != 200:
                raise error_from_response(resp.status_code, resp.content, self.config.name)
            try:
                payload = resp.json()
                items = payload["data"]
            except (ValueError, KeyError, TypeError) as exc:
                raise ProviderError("The models endpoint did not return a model list.", code="protocol") from exc
            for item in items:
                if not isinstance(item, dict) or not item.get("id"):
                    continue
                caps = item.get("capabilities") if isinstance(item.get("capabilities"), dict) else None
                image = (caps or {}).get("image_input")
                meta: dict[str, Any] = {}
                if isinstance(item.get("max_tokens"), int):
                    meta["max_output"] = item["max_tokens"]
                models.append(ModelInfo(
                    id=str(item["id"]), provider_id=self.config.id, name=str(item.get("display_name") or item["id"]),
                    context_length=item.get("max_input_tokens") if isinstance(item.get("max_input_tokens"), int) else None,
                    vision=vision if vision is not None else (image.get("supported") is True if isinstance(image, dict) else True),
                    meta=meta, reasoning=reasoning.anthropic_caps(str(item["id"]), caps).to_dict(),
                ))
            if not payload.get("has_more") or not payload.get("last_id"):
                break
            after = str(payload["last_id"])
        return models

    async def check(self) -> HealthReport:
        report = HealthReport(ok=False)
        start = time.perf_counter()
        try:
            report.models = await self.list_models()
        except ProviderError as exc:
            report.error, report.error_code, report.error_params = exc.message, exc.code, exc.params
            report.checks.append({"name": "models", "ok": False, "code": exc.code, "params": exc.params, "detail": exc.message})
            return report
        report.latency_ms = round((time.perf_counter() - start) * 1000, 1)
        report.models_ok = True
        report.checks.append({"name": "models", "ok": True, "code": "models_count", "params": {"count": len(report.models)},
                              "detail": f"{len(report.models)} model(s)", "latency_ms": report.latency_ms})
        report.capabilities.update({"chat": True, "streaming": True, "vision": True, "params": list(self.supported_params)})
        override = self.vision_override()
        if override is not None:
            report.capabilities["vision"] = override
        report.ok = True
        return report

    # -- chat ---------------------------------------------------------------
    def _messages(self, request: ChatRequest) -> tuple[str, list[dict[str, Any]]]:
        system = "\n\n".join(m.content for m in request.messages if m.role == "system" and m.content.strip())
        allow_images = self.vision_override() is not False
        out: list[dict[str, Any]] = []
        for m in request.messages:
            if m.role not in ("user", "assistant"):
                continue
            if m.images and allow_images and m.role == "user":
                content: Any = [{"type": "image", "source": {"type": "base64", "media_type": mime, "data": data}} for mime, data in m.images]
                content.append({"type": "text", "text": m.content or "(image)"})
            else:
                content = m.content
            # The API wants alternating turns: merge consecutive messages of one role.
            if out and out[-1]["role"] == m.role:
                prev = out[-1]["content"]
                if isinstance(prev, str) and isinstance(content, str):
                    out[-1]["content"] = f"{prev}\n\n{content}"
                else:
                    as_list = lambda c: c if isinstance(c, list) else [{"type": "text", "text": c}]  # noqa: E731
                    out[-1]["content"] = as_list(prev) + as_list(content)
            else:
                out.append({"role": m.role, "content": content})
        if out and out[0]["role"] != "user":
            out.insert(0, {"role": "user", "content": "(continued)"})
        return system, out

    def build_body(self, request: ChatRequest) -> dict[str, Any]:
        system, messages = self._messages(request)
        params = self.filter_params(request.params)
        hint = self.hints.get(request.model) or {}
        limit = (hint.get("meta") or {}).get("max_output")
        fallback = OLD_MAX_TOKENS if request.model.lower().startswith("claude-3") else DEFAULT_MAX_TOKENS
        cap = limit if isinstance(limit, int) and limit > 0 else fallback
        max_tokens = int(params.pop("max_tokens", 0) or min(DEFAULT_MAX_TOKENS, cap))
        body: dict[str, Any] = {"model": request.model, "max_tokens": max_tokens, "messages": messages, "stream": True}
        if system:
            body["system"] = system
        if params.get("stop"):
            body["stop_sequences"] = params.pop("stop")
        params.pop("stop", None)
        caps, level = self.thinking_level(request)
        thinking = self.thinking_fields(caps, level, request.model)
        body.update(thinking)
        thinks = "thinking" in thinking and thinking["thinking"].get("type") in ("adaptive", "enabled")
        if _uses_sampling(request.model) and not thinks:
            body.update({k: v for k, v in params.items() if k in ("temperature", "top_p", "top_k")})
        if thinks and request.quick:
            body["max_tokens"] = max(body["max_tokens"], 2048)  # room for the answer after thinking
        if thinking.get("thinking", {}).get("type") == "enabled":
            budget = thinking["thinking"]["budget_tokens"]
            body["max_tokens"] = max(body["max_tokens"], budget + 4096)
        return body

    @staticmethod
    def thinking_fields(caps: reasoning.Caps, level: str, model: str) -> dict[str, Any]:
        """The mapping for Claude (see docs/ARCHITECTURE.md, "Thinking")."""
        if caps.flavor == "anthropic_budget":
            if level in reasoning.ANTHROPIC_BUDGETS:
                return {"thinking": {"type": "enabled", "budget_tokens": reasoning.ANTHROPIC_BUDGETS[level]}}
            return {}
        if caps.flavor != "anthropic":
            return {}
        if level == "default":
            # Models that think by default would hide it ("omitted"): ask for the summary.
            return {"thinking": {"type": "adaptive", "display": "summarized"}} if reasoning.anthropic_family(model).get("thinks") else {}
        if level == "off":
            if caps.off == "between_tools":
                return {"thinking": {"type": "between_tools"}}
            return {"thinking": {"type": "disabled"}} if caps.off else {}
        effort = level if level in ("low", "medium", "high", "max") else (caps.default if caps.default in ("low", "medium", "high", "max") else "high")
        return {"thinking": {"type": "adaptive", "display": "summarized"}, "output_config": {"effort": effort}}

    def without_rejected(self, body: dict[str, Any], exc: ProviderError, request: ChatRequest) -> dict[str, Any] | None:
        if exc.status != 400 or exc.code == "context_exceeded":
            return None
        text = f"{exc.params.get('detail') or ''} {exc.message}".lower()
        fixed = dict(body)
        changed = False
        if any(k in fixed for k in ("thinking", "output_config")) and re.search(r"think|effort|output_config|budget", text):
            fixed.pop("thinking", None)
            fixed.pop("output_config", None)
            reasoning.mark_rejected(self.config.id, request.model)
            request.notes.setdefault("thinking", {}).update(applied="default", rejected=True)
            changed = True
        for key in ("temperature", "top_p", "top_k"):
            if key in fixed and key in text:
                fixed.pop(key)
                changed = True
        return fixed if changed else None

    async def stream_chat(self, request: ChatRequest) -> AsyncIterator[StreamEvent]:
        body = self.build_body(request)
        headers = {**self.headers(), "Accept": "text/event-stream", "Content-Type": "application/json"}
        for attempt in (1, 2):
            try:
                async with self.client.stream("POST", f"{self.api_url}/messages", json=body, headers=headers, timeout=self.timeout()) as resp:
                    if resp.status_code != 200:
                        raise error_from_response(resp.status_code, await resp.aread(), self.config.name)
                    async for event in self._parse_stream(resp):
                        yield event
                return
            except ProviderError as exc:
                fixed = self.without_rejected(body, exc, request) if attempt == 1 else None
                if fixed is None:
                    raise
                log.info("provider=%s retrying once without rejected fields", self.config.id)
                body = fixed
            except Exception as exc:  # noqa: BLE001
                raise translate_http_error(exc, self.config.name) from exc

    async def _parse_stream(self, resp: httpx.Response) -> AsyncIterator[StreamEvent]:
        usage: dict[str, Any] = {}
        stop: str | None = None
        finished = False
        async for sse in iter_sse(resp.aiter_bytes()):
            try:
                data = json.loads(sse.data) if sse.data.strip() else None
            except ValueError:
                continue
            if not isinstance(data, dict):
                continue
            kind = data.get("type") or sse.event
            if kind == "error":
                err = data.get("error") if isinstance(data.get("error"), dict) else {}
                msg = str(err.get("message") or "stream error")
                if is_context_overflow(err, msg):
                    raise context_overflow_error(err, self.config.name, msg)
                code = "unavailable" if err.get("type") == "overloaded_error" else "server"
                raise ProviderError(f"{self.config.name}: {msg}", code=code, name=self.config.name, detail=msg)
            if kind == "message_start":
                u = (data.get("message") or {}).get("usage") or {}
                if isinstance(u.get("input_tokens"), int):
                    usage["prompt_tokens"] = u["input_tokens"] + int(u.get("cache_read_input_tokens") or 0) + int(u.get("cache_creation_input_tokens") or 0)
            elif kind == "content_block_delta":
                delta = data.get("delta") or {}
                if delta.get("type") == "text_delta" and delta.get("text"):
                    yield StreamEvent(type="content", text=str(delta["text"]))
                elif delta.get("type") == "thinking_delta" and delta.get("thinking"):
                    yield StreamEvent(type="reasoning", text=str(delta["thinking"]))
            elif kind == "message_delta":
                stop = (data.get("delta") or {}).get("stop_reason") or stop
                u = data.get("usage") or {}
                if isinstance(u.get("output_tokens"), int):
                    usage["completion_tokens"] = u["output_tokens"]
            elif kind == "message_stop":
                finished = True
                break
        if usage:
            if "prompt_tokens" in usage and "completion_tokens" in usage:
                usage["total_tokens"] = usage["prompt_tokens"] + usage["completion_tokens"]
            yield StreamEvent(type="usage", usage=usage)
        if not finished:
            raise ProviderError("The response stream ended before the model finished.", code="incomplete", name=self.config.name)
        if stop == "refusal":
            raise ProviderError(f"{self.config.name} declined to answer this request.", code="refusal", name=self.config.name)
        yield StreamEvent(type="done", finish_reason={"end_turn": "stop", "max_tokens": "length", "stop_sequence": "stop"}.get(stop or "", stop or "stop"))
