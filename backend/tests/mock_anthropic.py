"""A mock of Anthropic's Messages API for tests and the end-to-end suite.

``GET /v1/models`` (with ``capabilities`` like the real Models API) and
``POST /v1/messages`` (SSE streaming). It enforces the rules of each model
family that Chimera's thinking mapping depends on, so a wrong request shape
fails here the way it would against the real API:

* Claude Opus 5.5 / Fable: ``thinking.type`` "disabled" and ``budget_tokens``
  are rejected (thinking can't be turned off; effort is the control);
* Claude Sonnet 5.5: "disabled" is rejected, "between_tools" turns it off;
* Claude 4.7+ and 5.x: ``temperature``/``top_p``/``top_k`` are rejected;
* Claude 4.5 and earlier: only ``{"type": "enabled", "budget_tokens": N}``
  with ``1024 <= N < max_tokens``; ``output_config.effort`` is rejected;
* ``max_tokens`` is required.

Thinking summaries stream as ``thinking_delta`` when ``display`` is
"summarized". Every request body is recorded in ``app.state.requests``.

Run standalone::

    python tests/mock_anthropic.py --port 18081
"""

from __future__ import annotations

import argparse
import json
import re
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse

MODELS = {
    "claude-opus-5-5": {"display_name": "Claude Opus 5.5", "family": "adaptive-on", "max_tokens": 128_000},
    "claude-sonnet-5-5": {"display_name": "Claude Sonnet 5.5", "family": "between", "max_tokens": 128_000},
    "claude-sonnet-4-6": {"display_name": "Claude Sonnet 4.6", "family": "adaptive", "max_tokens": 64_000},
    "claude-haiku-4-5": {"display_name": "Claude Haiku 4.5", "family": "budget", "max_tokens": 64_000},
    "claude-3-haiku-20240307": {"display_name": "Claude Haiku 3", "family": "none", "max_tokens": 4096},
}


def capabilities(family: str) -> dict[str, Any]:
    adaptive = family in ("adaptive-on", "between", "adaptive")
    effort = adaptive or False
    return {
        "image_input": {"supported": True},
        "thinking": {"supported": family != "none",
                     "types": {"enabled": {"supported": family == "budget"}, "adaptive": {"supported": adaptive}}},
        "effort": {"supported": effort, **{lv: {"supported": effort} for lv in ("low", "medium", "high", "xhigh", "max")}},
    }


def bad(message: str, status: int = 400) -> JSONResponse:
    return JSONResponse({"type": "error", "error": {"type": "invalid_request_error", "message": message}}, status)


def validate(body: dict, family: str) -> JSONResponse | None:
    if not isinstance(body.get("max_tokens"), int):
        return bad("max_tokens: Field required")
    thinking = body.get("thinking") or {}
    kind = thinking.get("type")
    effort = (body.get("output_config") or {}).get("effort")
    modern = family in ("adaptive-on", "between")
    if family == "none" and (thinking or effort):
        return bad("thinking: this model does not support extended thinking")
    if modern and any(k in body for k in ("temperature", "top_p", "top_k")):
        return bad("temperature: sampling parameters are not supported on this model")
    if kind == "enabled" and family != "budget":
        return bad("thinking.type.enabled: budget_tokens is not supported on this model; use thinking.type.adaptive")
    if kind == "disabled" and family in ("adaptive-on", "between"):
        return bad("thinking.type: 'disabled' is not supported on this model")
    if kind == "between_tools" and family != "between":
        return bad("thinking.type: 'between_tools' is not supported on this model")
    if kind == "adaptive" and family == "budget":
        return bad("thinking.type: 'adaptive' is not supported on this model")
    if effort and family in ("budget", "none"):
        return bad("output_config.effort: effort is not supported on this model")
    if kind == "enabled":
        budget = thinking.get("budget_tokens")
        if not isinstance(budget, int) or budget < 1024 or budget >= body["max_tokens"]:
            return bad("thinking.budget_tokens: must be at least 1024 and less than max_tokens")
    if kind in ("adaptive", "enabled") and any(k in body for k in ("temperature", "top_k")):
        return bad("temperature: may only be set to 1 when thinking is enabled")
    return None


def create_app(api_key: str | None = None) -> FastAPI:
    app = FastAPI()
    app.state.requests = []
    app.state.api_key = api_key
    app.state.reject_thinking = set()  # models answering 400 to any thinking field (a newer rule)

    def unauthorized(request: Request) -> JSONResponse | None:
        if request.headers.get("anthropic-version") != "2023-06-01":
            return bad("anthropic-version: header is required")
        if app.state.api_key and request.headers.get("x-api-key") != app.state.api_key:
            return JSONResponse({"type": "error", "error": {"type": "authentication_error", "message": "invalid x-api-key"}}, 401)
        return None

    @app.get("/v1/models")
    async def models(request: Request):
        if (resp := unauthorized(request)) is not None:
            return resp
        data = [{"type": "model", "id": mid, "display_name": m["display_name"], "created_at": "2026-01-01T00:00:00Z",
                 "max_input_tokens": 200_000, "max_tokens": m["max_tokens"], "capabilities": capabilities(m["family"])}
                for mid, m in MODELS.items()]
        return {"data": data, "has_more": False, "first_id": data[0]["id"], "last_id": data[-1]["id"]}

    @app.post("/__mock/config")
    async def config(request: Request):
        body = await request.json()
        if "reject_thinking" in body:
            app.state.reject_thinking = set(body["reject_thinking"])
        if body.get("clear"):
            app.state.requests.clear()
        return {"requests": app.state.requests[-20:]}

    @app.post("/v1/messages")
    async def messages(request: Request):
        if (resp := unauthorized(request)) is not None:
            return resp
        body = await request.json()
        app.state.requests.append(body)
        model = MODELS.get(body.get("model"))
        if not model:
            return JSONResponse({"type": "error", "error": {"type": "not_found_error", "message": f"model: {body.get('model')}"}}, 404)
        if body.get("model") in app.state.reject_thinking and ("thinking" in body or "output_config" in body):
            return bad("thinking: Extra inputs are not permitted")
        if (resp := validate(body, model["family"])) is not None:
            return resp
        msgs = body.get("messages") or []
        if not msgs or msgs[0].get("role") != "user" or any(a["role"] == b["role"] for a, b in zip(msgs, msgs[1:])):
            return bad("messages: roles must alternate, starting with user")
        last = msgs[-1].get("content")
        text = last if isinstance(last, str) else " ".join(p.get("text", "") for p in last if isinstance(p, dict))
        thinking = body.get("thinking") or {}
        effort = (body.get("output_config") or {}).get("effort")
        thinks = thinking.get("type") in ("adaptive", "enabled") or (model["family"] == "adaptive-on" and not thinking)
        shown = thinks and (thinking.get("display") == "summarized" or thinking.get("type") == "enabled")
        if "Write a short, specific title" in text:
            answer = "Mock Claude Title"
        else:
            answer = f"Claude says: {re.sub(r'<[^>]+>', '', text).strip()[:200]}"
        summary = f"Thinking at {effort or ('budget ' + str(thinking.get('budget_tokens')) if thinking.get('type') == 'enabled' else 'default')}."

        def event(name: str, data: dict) -> str:
            return f"event: {name}\ndata: {json.dumps({'type': name, **data})}\n\n"

        async def stream():
            yield event("message_start", {"message": {"id": "msg_mock", "type": "message", "role": "assistant", "model": body["model"],
                                                      "content": [], "usage": {"input_tokens": 25, "output_tokens": 1}}})
            index = 0
            if thinks:
                yield event("content_block_start", {"index": index, "content_block": {"type": "thinking", "thinking": ""}})
                if shown:
                    for word in summary.split(" "):
                        yield event("content_block_delta", {"index": index, "delta": {"type": "thinking_delta", "thinking": word + " "}})
                yield event("content_block_delta", {"index": index, "delta": {"type": "signature_delta", "signature": "c2lnbmF0dXJl"}})
                yield event("content_block_stop", {"index": index})
                index += 1
            yield event("content_block_start", {"index": index, "content_block": {"type": "text", "text": ""}})
            for word in answer.split(" "):
                yield event("content_block_delta", {"index": index, "delta": {"type": "text_delta", "text": word + " "}})
            yield event("content_block_stop", {"index": index})
            yield event("message_delta", {"delta": {"stop_reason": "end_turn", "stop_sequence": None}, "usage": {"output_tokens": 30}})
            yield event("message_stop", {})

        return StreamingResponse(stream(), media_type="text/event-stream")

    return app


def main() -> None:
    import uvicorn

    parser = argparse.ArgumentParser(description="Mock Anthropic Messages API for Chimera development")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=18081)
    args = parser.parse_args()
    uvicorn.run(create_app(), host=args.host, port=args.port)


if __name__ == "__main__":
    main()
