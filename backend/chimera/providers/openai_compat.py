"""OpenAI-compatible providers: llama.cpp ``llama-server`` and generic servers.

llama-server (https://github.com/ggml-org/llama.cpp/tree/master/tools/server)
endpoints used:

* ``GET  /health``               – readiness (200 ok, 503 while loading). Optional.
* ``GET  /props``                – n_ctx, slots, modalities, build info. Optional.
* ``GET  {api_path}/models``     – model discovery (``/v1/models``).
* ``POST {api_path}/chat/completions`` – streaming chat via SSE.
* ``POST {api_path}/embeddings`` – only when started with ``--embeddings``.

Generation is cancelled by closing the HTTP connection, which llama-server
detects and uses to stop decoding and free the slot.
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import PurePosixPath
from typing import Any, AsyncIterator

import httpx

from .base import (
    ChatMessage,
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

MAX_MALFORMED_EVENTS = 20
IMAGE_TOKENS = 768  # rough cost of one image in multimodal models (not reported by /tokenize)
# Optional endpoints a server lacks, remembered for a while (re-checked after
# UNSUPPORTED_TTL so an upgraded llama-server is noticed without a restart).
_UNSUPPORTED: dict[tuple[str, str], float] = {}
UNSUPPORTED_TTL = 600.0


def _display_name(model_id: str) -> str:
    name = PurePosixPath(model_id.replace("\\", "/")).name or model_id
    for suffix in (".gguf", ".bin"):
        if name.lower().endswith(suffix):
            name = name[: -len(suffix)]
    return name


def encode_messages(messages: list[ChatMessage], allow_images: bool) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for msg in messages:
        if msg.images and allow_images:
            parts: list[dict[str, Any]] = [{"type": "text", "text": msg.content}]
            for mime, data in msg.images:
                parts.append({"type": "image_url", "image_url": {"url": f"data:{mime};base64,{data}"}})
            out.append({"role": msg.role, "content": parts})
        else:
            out.append({"role": msg.role, "content": msg.content})
    return out


class OpenAICompatibleProvider(Provider):
    kind = "openai"
    supported_params = (
        "temperature",
        "top_p",
        "max_tokens",
        "stop",
        "presence_penalty",
        "frequency_penalty",
        "seed",
    )
    has_health_endpoint = False
    has_props_endpoint = False

    @property
    def api_url(self) -> str:
        path = (self.config.api_path or "").strip()
        if path and not path.startswith("/"):
            path = "/" + path
        return self.root_url + path.rstrip("/")

    # -- discovery ----------------------------------------------------------
    def _parse_models(self, payload: Any) -> list[ModelInfo]:
        if not isinstance(payload, dict):
            raise ProviderError("Unexpected response from the models endpoint.", code="protocol")
        items = payload.get("data")
        if not isinstance(items, list):
            items = payload.get("models") if isinstance(payload.get("models"), list) else None
        if items is None:
            raise ProviderError("The models endpoint did not return a model list.", code="protocol")
        models: list[ModelInfo] = []
        seen: set[str] = set()
        for item in items:
            if not isinstance(item, dict):
                continue
            model_id = str(item.get("id") or item.get("model") or item.get("name") or "").strip()
            if not model_id or model_id in seen:
                continue
            seen.add(model_id)
            meta = item.get("meta") if isinstance(item.get("meta"), dict) else {}
            status = item.get("status")
            loaded: bool | None = None
            if isinstance(status, dict) and "value" in status:  # llama-server router mode
                loaded = status.get("value") == "loaded"
            ctx = meta.get("n_ctx_train") or item.get("context_length") or item.get("max_model_len")
            models.append(
                ModelInfo(
                    id=model_id,
                    provider_id=self.config.id,
                    name=_display_name(model_id),
                    context_length=int(ctx) if isinstance(ctx, (int, float)) else None,
                    loaded=loaded,
                    meta={k: v for k, v in meta.items() if isinstance(v, (int, float, str, bool))},
                )
            )
        return models

    async def list_models(self) -> list[ModelInfo]:
        try:
            resp = await self.client.get(
                f"{self.api_url}/models", headers=self.headers(), timeout=self.timeout(read=30)
            )
        except Exception as exc:  # noqa: BLE001
            raise translate_http_error(exc, self.config.name) from exc
        if resp.status_code != 200:
            raise error_from_response(resp.status_code, resp.content, self.config.name)
        try:
            payload = resp.json()
        except ValueError as exc:
            raise ProviderError("The models endpoint returned invalid JSON.", code="protocol") from exc
        models = self._parse_models(payload)
        vision = self.vision_override()
        for m in models:
            m.vision = vision
        return models

    async def _optional_json(self, url: str) -> tuple[int | None, Any, float | None, ProviderError | None]:
        start = time.perf_counter()
        try:
            resp = await self.client.get(url, headers=self.headers(), timeout=self.timeout(read=15))
        except Exception as exc:  # noqa: BLE001
            return None, None, None, translate_http_error(exc, self.config.name)
        latency = (time.perf_counter() - start) * 1000
        try:
            body = resp.json()
        except ValueError:
            body = None
        return resp.status_code, body, latency, None

    async def check(self) -> HealthReport:
        report = HealthReport(ok=False)
        caps: dict[str, Any] = {"chat": True, "streaming": True}

        if self.has_health_endpoint:
            status, body, latency, err = await self._optional_json(f"{self.root_url}/health")
            if err:
                report.error, report.error_code, report.error_params = err.message, err.code, err.params
                report.checks.append({"name": "health", "ok": False, "code": err.code, "params": err.params, "detail": err.message})
                return report
            if status == 200:
                report.checks.append({"name": "health", "ok": True, "code": "health_ready", "detail": "ready", "latency_ms": latency})
                caps["health_endpoint"] = True
            elif status == 503:
                msg = "Server is up but not ready (loading model)."
                server_msg = None
                if isinstance(body, dict) and isinstance(body.get("error"), dict):
                    server_msg = body["error"].get("message")
                params = {"name": self.config.name, **({"detail": server_msg} if server_msg else {})}
                report.checks.append({"name": "health", "ok": False, "code": "unavailable", "params": params, "detail": server_msg or msg})
                report.error, report.error_code, report.error_params = server_msg or msg, "unavailable", params
                report.capabilities = caps
                return report
            else:
                report.checks.append(
                    {"name": "health", "ok": None, "code": "endpoint_unavailable", "params": {"status": status}, "detail": f"not available (HTTP {status})"}
                )
                caps["health_endpoint"] = False

        start = time.perf_counter()
        try:
            report.models = await self.list_models()
            report.models_ok = True
            report.latency_ms = round((time.perf_counter() - start) * 1000, 1)
            report.checks.append(
                {"name": "models", "ok": True, "code": "models_count", "params": {"count": len(report.models)},
                 "detail": f"{len(report.models)} model(s)", "latency_ms": report.latency_ms}
            )
        except ProviderError as exc:
            report.error, report.error_code, report.error_params = exc.message, exc.code, exc.params
            report.checks.append({"name": "models", "ok": False, "code": exc.code, "params": exc.params, "detail": exc.message})
            report.capabilities = caps
            return report

        if self.has_props_endpoint:
            status, body, _, _ = await self._optional_json(f"{self.root_url}/props")
            if status == 200 and isinstance(body, dict):
                caps.update(self._parse_props(body))
                report.checks.append({"name": "props", "ok": True, "code": "props_available", "detail": "server properties available"})
            else:
                report.checks.append({"name": "props", "ok": None, "code": "not_available", "detail": "not available"})

        override = self.vision_override()
        if override is not None:
            caps["vision"] = override
        if "vision" in caps:
            for m in report.models:
                m.vision = caps["vision"]
        if caps.get("n_ctx"):
            for m in report.models:
                m.meta.setdefault("n_ctx", caps["n_ctx"])
        caps["params"] = list(self.supported_params)
        report.capabilities = caps
        report.ok = True
        return report

    @staticmethod
    def _parse_props(body: dict[str, Any]) -> dict[str, Any]:
        caps: dict[str, Any] = {}
        dgs = body.get("default_generation_settings")
        n_ctx = body.get("n_ctx")
        if isinstance(dgs, dict):
            n_ctx = dgs.get("n_ctx", n_ctx)
        if isinstance(n_ctx, int):
            caps["n_ctx"] = n_ctx
        if isinstance(body.get("total_slots"), int):
            caps["slots"] = body["total_slots"]
        modalities = body.get("modalities")
        if isinstance(modalities, dict):
            caps["vision"] = bool(modalities.get("vision"))
            caps["audio"] = bool(modalities.get("audio"))
        if isinstance(body.get("build_info"), str):
            caps["build"] = body["build_info"]
        if isinstance(body.get("model_path"), str):
            caps["model_path"] = body["model_path"]
        if isinstance(body.get("model_alias"), str):
            caps["model_alias"] = body["model_alias"]
        return caps

    # -- chat ---------------------------------------------------------------
    def build_body(self, request: ChatRequest, stream: bool = True) -> dict[str, Any]:
        allow_images = self.vision_override() is not False
        body: dict[str, Any] = {
            "model": request.model,
            "messages": encode_messages(request.messages, allow_images),
            "stream": stream,
        }
        if stream:
            body["stream_options"] = {"include_usage": True}
        body.update(self.filter_params(request.params))
        return body

    async def stream_chat(self, request: ChatRequest) -> AsyncIterator[StreamEvent]:
        body = self.build_body(request, stream=True)
        headers = {**self.headers(), "Accept": "text/event-stream", "Content-Type": "application/json"}
        url = f"{self.api_url}/chat/completions"
        try:
            async with self.client.stream("POST", url, json=body, headers=headers, timeout=self.timeout()) as resp:
                if resp.status_code != 200:
                    raw = await resp.aread()
                    raise error_from_response(resp.status_code, raw, self.config.name)
                ctype = resp.headers.get("content-type", "")
                if "text/event-stream" not in ctype and "application/json" in ctype:
                    # Server ignored ``stream``; fall back to a single response.
                    raw = await resp.aread()
                    for event in self._events_from_full_response(raw):
                        yield event
                    return
                async for event in self._parse_stream(resp):
                    yield event
        except ProviderError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise translate_http_error(exc, self.config.name) from exc

    def _events_from_full_response(self, raw: bytes) -> list[StreamEvent]:
        try:
            data = json.loads(raw)
        except ValueError as exc:
            raise ProviderError("The server returned an invalid response.", code="protocol") from exc
        if isinstance(data, dict) and data.get("error"):
            err = data["error"]
            detail = str(err.get("message") if isinstance(err, dict) else err)
            raise ProviderError(detail, code="server", name=self.config.name, detail=detail)
        choice = (data.get("choices") or [{}])[0]
        message = choice.get("message") or {}
        events = []
        reasoning = message.get("reasoning_content") or message.get("reasoning")
        if reasoning:
            events.append(StreamEvent(type="reasoning", text=str(reasoning)))
        events.append(StreamEvent(type="content", text=str(message.get("content") or "")))
        usage = self._usage(data)
        if usage:
            events.append(StreamEvent(type="usage", usage=usage))
        events.append(StreamEvent(type="done", finish_reason=choice.get("finish_reason") or "stop"))
        return events

    @staticmethod
    def _usage(data: dict[str, Any]) -> dict[str, Any]:
        usage: dict[str, Any] = {}
        u = data.get("usage")
        if isinstance(u, dict):
            for key in ("prompt_tokens", "completion_tokens", "total_tokens"):
                if isinstance(u.get(key), (int, float)):
                    usage[key] = u[key]
        t = data.get("timings")  # llama-server specific
        if isinstance(t, dict):
            if isinstance(t.get("predicted_per_second"), (int, float)):
                usage["tokens_per_second"] = round(float(t["predicted_per_second"]), 2)
            if isinstance(t.get("prompt_ms"), (int, float)):
                usage["prompt_ms"] = round(float(t["prompt_ms"]), 1)
            if isinstance(t.get("predicted_n"), (int, float)) and "completion_tokens" not in usage:
                usage["completion_tokens"] = int(t["predicted_n"])
            if isinstance(t.get("prompt_n"), (int, float)) and "prompt_tokens" not in usage:
                usage["prompt_tokens"] = int(t["prompt_n"])
        return usage

    async def _parse_stream(self, resp: httpx.Response) -> AsyncIterator[StreamEvent]:
        finish_reason: str | None = None
        saw_done = False
        malformed = 0
        usage: dict[str, Any] = {}
        async for sse in iter_sse(resp.aiter_bytes()):
            data = sse.data.strip()
            if not data:
                continue
            if data == "[DONE]":
                saw_done = True
                break
            try:
                payload = json.loads(data)
            except ValueError:
                malformed += 1
                log.warning("provider=%s malformed SSE event skipped (%d)", self.config.id, malformed)
                if malformed > MAX_MALFORMED_EVENTS:
                    raise ProviderError("The server sent too many malformed stream events.", code="malformed_stream", name=self.config.name)
                continue
            if not isinstance(payload, dict):
                malformed += 1
                continue
            if sse.event == "error" or payload.get("error"):
                err = payload.get("error", payload)
                msg = err.get("message") if isinstance(err, dict) else str(err)
                if is_context_overflow(err, msg or ""):
                    raise context_overflow_error(err if isinstance(err, dict) else {}, self.config.name, msg or "")
                raise ProviderError(
                    f"{self.config.name}: {msg or 'stream error'}", code="server", name=self.config.name, detail=msg or None
                )
            usage.update(self._usage(payload))
            for choice in payload.get("choices") or []:
                if not isinstance(choice, dict):
                    continue
                delta = choice.get("delta") or {}
                if not isinstance(delta, dict):
                    delta = {}
                reasoning = delta.get("reasoning_content") or delta.get("reasoning")
                if isinstance(reasoning, str) and reasoning:
                    yield StreamEvent(type="reasoning", text=reasoning)
                content = delta.get("content")
                if isinstance(content, str) and content:
                    yield StreamEvent(type="content", text=content)
                if choice.get("finish_reason"):
                    finish_reason = str(choice["finish_reason"])
        if usage:
            yield StreamEvent(type="usage", usage=usage)
        if not saw_done and finish_reason is None:
            raise ProviderError("The response stream ended before the model finished.", code="incomplete", name=self.config.name)
        yield StreamEvent(type="done", finish_reason=finish_reason or "stop")

    # -- embeddings ---------------------------------------------------------
    async def embed(self, model: str, texts: list[str]) -> list[list[float]]:
        try:
            resp = await self.client.post(
                f"{self.api_url}/embeddings",
                json={"model": model, "input": texts},
                headers={**self.headers(), "Content-Type": "application/json"},
                timeout=self.timeout(),
            )
        except Exception as exc:  # noqa: BLE001
            raise translate_http_error(exc, self.config.name) from exc
        if resp.status_code != 200:
            raise error_from_response(resp.status_code, resp.content, self.config.name)
        try:
            data = resp.json()["data"]
            data = sorted(data, key=lambda d: d.get("index", 0))
            vectors = [list(map(float, d["embedding"])) for d in data]
        except (ValueError, KeyError, TypeError) as exc:
            raise ProviderError("Invalid embeddings response.", code="protocol") from exc
        if len(vectors) != len(texts):
            raise ProviderError("Embedding count does not match input count.", code="protocol")
        return vectors


class LlamaCppProvider(OpenAICompatibleProvider):
    kind = "llamacpp"
    supported_params = (
        "temperature",
        "top_p",
        "top_k",
        "min_p",
        "max_tokens",
        "stop",
        "repeat_penalty",
        "presence_penalty",
        "frequency_penalty",
        "seed",
    )
    has_health_endpoint = True
    has_props_endpoint = True

    # -- exact token counting (/tokenize, /apply-template) ----------------------
    async def _post_json(self, path: str, body: dict) -> object | None:
        """POST to an optional llama-server endpoint; ``None`` if this build
        doesn't have it (remembered per server, so it isn't retried every turn)."""
        key = (self.root_url, path)
        if _UNSUPPORTED.get(key, 0) > time.monotonic():
            return None
        try:
            resp = await self.client.post(f"{self.root_url}{path}", json=body, headers={**self.headers(), "Content-Type": "application/json"},
                                          timeout=self.timeout(read=30))
        except Exception:  # noqa: BLE001 - counting is best effort
            return None
        if resp.status_code in (404, 405, 501):
            _UNSUPPORTED[key] = time.monotonic() + UNSUPPORTED_TTL
            return None
        if resp.status_code != 200:
            return None
        try:
            return resp.json()
        except ValueError:
            return None

    async def tokenize_count(self, text: str) -> int | None:
        data = await self._post_json("/tokenize", {"content": text, "add_special": False})
        tokens = data.get("tokens") if isinstance(data, dict) else None
        return len(tokens) if isinstance(tokens, list) else None

    async def prompt_tokens(self, request: ChatRequest) -> int | None:
        data = await self._post_json("/apply-template", {"messages": encode_messages(request.messages, False)})
        prompt = data.get("prompt") if isinstance(data, dict) else None
        if not isinstance(prompt, str):
            return None
        counted = await self._post_json("/tokenize", {"content": prompt, "add_special": True})
        tokens = counted.get("tokens") if isinstance(counted, dict) else None
        if not isinstance(tokens, list):
            return None
        # Images are not part of the rendered text; count them conservatively.
        return len(tokens) + IMAGE_TOKENS * sum(len(m.images) for m in request.messages)
