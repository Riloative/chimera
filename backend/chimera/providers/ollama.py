"""Native Ollama adapter (``/api/tags``, ``/api/chat`` NDJSON streaming)."""

from __future__ import annotations

import asyncio
import codecs
import json
import re
import time
from typing import Any, AsyncIterator

from .base import (
    ChatRequest,
    HealthReport,
    ModelInfo,
    Provider,
    ProviderError,
    StreamEvent,
    error_from_response,
    translate_http_error,
)
from . import reasoning

PARAM_MAP = {
    "temperature": "temperature",
    "top_p": "top_p",
    "top_k": "top_k",
    "min_p": "min_p",
    "max_tokens": "num_predict",
    "stop": "stop",
    "repeat_penalty": "repeat_penalty",
    "presence_penalty": "presence_penalty",
    "frequency_penalty": "frequency_penalty",
    "seed": "seed",
}


class OllamaProvider(Provider):
    kind = "ollama"
    supported_params = tuple(PARAM_MAP)

    async def list_models(self) -> list[ModelInfo]:
        try:
            resp = await self.client.get(
                f"{self.root_url}/api/tags", headers=self.headers(), timeout=self.timeout(read=30)
            )
        except Exception as exc:  # noqa: BLE001
            raise translate_http_error(exc, self.config.name) from exc
        if resp.status_code != 200:
            raise error_from_response(resp.status_code, resp.content, self.config.name)
        try:
            items = resp.json().get("models", [])
        except (ValueError, AttributeError) as exc:
            raise ProviderError("Unexpected response from /api/tags.", code="protocol") from exc
        vision = self.vision_override()
        models = []
        for item in items:
            if not isinstance(item, dict) or not item.get("name"):
                continue
            details = item.get("details") if isinstance(item.get("details"), dict) else {}
            meta = {k: v for k, v in details.items() if isinstance(v, (str, int, float))}
            if isinstance(item.get("size"), int):
                meta["size"] = item["size"]
            models.append(
                ModelInfo(id=item["name"], provider_id=self.config.id, name=item["name"], vision=vision, meta=meta)
            )
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
        report.checks.append(
            {"name": "models", "ok": True, "code": "models_count", "params": {"count": len(report.models)},
             "detail": f"{len(report.models)} model(s)", "latency_ms": report.latency_ms}
        )
        try:
            resp = await self.client.get(f"{self.root_url}/api/version", timeout=self.timeout(read=10))
            if resp.status_code == 200:
                version = resp.json().get("version")
                report.checks.append({"name": "version", "ok": True, "code": "version", "params": {"version": str(version)}, "detail": str(version)})
                report.capabilities["build"] = version
        except Exception:  # noqa: BLE001
            pass
        await self._show_all(report.models)
        report.capabilities.update({"chat": True, "streaming": True, "params": list(self.supported_params)})
        override = self.vision_override()
        if override is not None:
            report.capabilities["vision"] = override
        report.ok = True
        return report

    async def _show(self, model: ModelInfo) -> None:
        """``/api/show`` lists a model's capabilities ("thinking", "vision")."""
        try:
            resp = await self.client.post(f"{self.root_url}/api/show", json={"model": model.id}, headers=self.headers(),
                                          timeout=self.timeout(read=15))
            data = resp.json() if resp.status_code == 200 else None
        except Exception:  # noqa: BLE001 - detection is best effort
            return
        if not isinstance(data, dict):
            return
        caps = data.get("capabilities") if isinstance(data.get("capabilities"), list) else []
        if model.vision is None and caps:
            model.vision = "vision" in caps
        if "thinking" not in caps:
            model.reasoning = reasoning.Caps(source="detected").to_dict()
            return
        details = data.get("details") if isinstance(data.get("details"), dict) else {}
        values = (data.get("thinking") or {}).get("values") if isinstance(data.get("thinking"), dict) else None
        names = tuple(v for v in values if v in reasoning.ORDER) if isinstance(values, list) else ()
        if names or re.match(r"gpt-?oss", model.id.lower()) or str(details.get("family", "")).lower() in ("gptoss", "gpt-oss"):
            # Thinks with levels and can't be turned off (gpt-oss).
            model.reasoning = reasoning.levels("ollama", "detected", names or ("low", "medium", "high"), default="medium").to_dict()
        else:
            model.reasoning = reasoning.toggle("ollama", "detected").to_dict()

    async def _show_all(self, models: list[ModelInfo]) -> None:
        await asyncio.gather(*(self._show(m) for m in models[:100]))

    def build_body(self, request: ChatRequest) -> dict[str, Any]:
        allow_images = self.vision_override() is not False
        messages = []
        for m in request.messages:
            entry: dict[str, Any] = {"role": m.role, "content": m.content}
            if m.images and allow_images:
                entry["images"] = [data for _, data in m.images]
            messages.append(entry)
        options = {PARAM_MAP[k]: v for k, v in self.filter_params(request.params).items()}
        body: dict[str, Any] = {"model": request.model, "messages": messages, "stream": True}
        if options:
            body["options"] = options
        caps, level = self.thinking_level(request)
        body.update(reasoning.ollama_fields(caps, level))
        return body

    async def stream_chat(self, request: ChatRequest) -> AsyncIterator[StreamEvent]:
        body = self.build_body(request)
        try:
            async for event in self._stream(body):
                yield event
        except ProviderError as exc:
            # "think" on a model that can't: one retry without it.
            if exc.status != 400 or "think" not in body or "think" not in f"{exc.message} {exc.params.get('detail') or ''}".lower():
                raise
            body.pop("think")
            reasoning.mark_rejected(self.config.id, request.model)
            request.notes.setdefault("thinking", {}).update(applied="default", rejected=True)
            async for event in self._stream(body):
                yield event

    async def _stream(self, body: dict[str, Any]) -> AsyncIterator[StreamEvent]:
        try:
            async with self.client.stream(
                "POST", f"{self.root_url}/api/chat", json=body, headers=self.headers(), timeout=self.timeout()
            ) as resp:
                if resp.status_code != 200:
                    raise error_from_response(resp.status_code, await resp.aread(), self.config.name)
                decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
                buffer = ""
                finished = False
                async for chunk in resp.aiter_bytes():
                    buffer += decoder.decode(chunk)
                    while "\n" in buffer:
                        line, buffer = buffer.split("\n", 1)
                        for event in self._parse_line(line):
                            finished = finished or event.type == "done"
                            yield event
                for event in self._parse_line(buffer + decoder.decode(b"", final=True)):
                    finished = finished or event.type == "done"
                    yield event
                if not finished:
                    raise ProviderError("The response stream ended before the model finished.", code="incomplete", name=self.config.name)
        except ProviderError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise translate_http_error(exc, self.config.name) from exc

    def _parse_line(self, line: str) -> list[StreamEvent]:
        line = line.strip()
        if not line:
            return []
        try:
            data = json.loads(line)
        except ValueError:
            return []
        if not isinstance(data, dict):
            return []
        if data.get("error"):
            raise ProviderError(f"{self.config.name}: {data['error']}", code="server", name=self.config.name, detail=str(data["error"]))
        events: list[StreamEvent] = []
        msg = data.get("message") if isinstance(data.get("message"), dict) else {}
        if msg.get("thinking"):
            events.append(StreamEvent(type="reasoning", text=str(msg["thinking"])))
        if msg.get("content"):
            events.append(StreamEvent(type="content", text=str(msg["content"])))
        if data.get("done"):
            usage: dict[str, Any] = {}
            if isinstance(data.get("prompt_eval_count"), int):
                usage["prompt_tokens"] = data["prompt_eval_count"]
            if isinstance(data.get("eval_count"), int):
                usage["completion_tokens"] = data["eval_count"]
                dur = data.get("eval_duration")
                if isinstance(dur, (int, float)) and dur > 0:
                    usage["tokens_per_second"] = round(data["eval_count"] / (dur / 1e9), 2)
            if usage:
                events.append(StreamEvent(type="usage", usage=usage))
            events.append(StreamEvent(type="done", finish_reason=str(data.get("done_reason") or "stop")))
        return events

    async def embed(self, model: str, texts: list[str]) -> list[list[float]]:
        try:
            resp = await self.client.post(
                f"{self.root_url}/api/embed",
                json={"model": model, "input": texts},
                headers=self.headers(),
                timeout=self.timeout(),
            )
        except Exception as exc:  # noqa: BLE001
            raise translate_http_error(exc, self.config.name) from exc
        if resp.status_code != 200:
            raise error_from_response(resp.status_code, resp.content, self.config.name)
        try:
            vectors = [list(map(float, v)) for v in resp.json()["embeddings"]]
        except (ValueError, KeyError, TypeError) as exc:
            raise ProviderError("Invalid embeddings response.", code="protocol") from exc
        if len(vectors) != len(texts):
            raise ProviderError("Embedding count does not match input count.", code="protocol")
        return vectors
