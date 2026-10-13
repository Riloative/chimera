"""A mock of llama.cpp's ``llama-server`` HTTP API for tests and UI development.

It implements the endpoints Chimera uses with realistic payload shapes:
``/health``, ``/props``, ``/v1/models``, ``/v1/chat/completions`` (SSE and
JSON) and ``/v1/embeddings``. Behaviour is steered by markers in the last
user message:

* ``[slow]``             – ~50 tokens/s for ~300 tokens (use to test Stop)
* ``[malformed]``        – interleaves malformed SSE events
* ``[truncate]``         – stream ends without finish_reason / [DONE]
* ``[error]``            – HTTP 500 with an OpenAI-style error body
* ``[midstream-error]``  – an error event after some content
* ``[reasoning]``        – emits ``reasoning_content`` deltas first
* ``[markdown]``         – returns a rich Markdown sample

Run standalone for development::

    python tests/mock_llama_server.py --port 8080
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import math
import re
import time
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse

MARKDOWN_SAMPLE = """Here is a quick overview of **binary search**.

## How it works

1. Start with a sorted array.
2. Compare the target with the middle element.
3. Discard the half that cannot contain the target.

```python
def binary_search(items: list[int], target: int) -> int:
    lo, hi = 0, len(items) - 1
    while lo <= hi:
        mid = (lo + hi) // 2
        if items[mid] == target:
            return mid
        if items[mid] < target:
            lo = mid + 1
        else:
            hi = mid - 1
    return -1
```

| Case    | Complexity |
|---------|------------|
| Best    | O(1)       |
| Average | O(log n)   |

> Binary search only works on sorted data.

The recurrence is $T(n) = T(n/2) + O(1)$, which gives $$T(n) = O(\\log n).$$
"""


def tokenize(text: str) -> list[str]:
    """Split into small pieces that look like LLM tokens (keeps whitespace)."""
    return re.findall(r"\s*\S{1,4}|\s+", text)


def embed_text(text: str, dims: int = 64) -> list[float]:
    vec = [0.0] * dims
    for word in re.findall(r"\w+", text.lower()):
        h = int(hashlib.md5(word.encode()).hexdigest(), 16)
        vec[h % dims] += 1.0 if (h >> 8) & 1 else -1.0
    norm = math.sqrt(sum(v * v for v in vec)) or 1.0
    return [v / norm for v in vec]


def create_mock_app(
    models: list[str] | None = None,
    api_key: str | None = None,
    vision: bool = False,
    loading: bool = False,
) -> FastAPI:
    app = FastAPI()
    app.state.models = models or ["qwen2.5-7b-instruct-q4_k_m.gguf"]
    app.state.requests: list[dict[str, Any]] = []
    app.state.cancelled = 0
    app.state.completed = 0
    app.state.loading = loading
    app.state.vision = vision
    app.state.api_key = api_key

    def unauthorized(request: Request) -> JSONResponse | None:
        if app.state.api_key and request.headers.get("authorization") != f"Bearer {app.state.api_key}":
            return JSONResponse({"error": {"code": 401, "message": "Invalid API Key", "type": "authentication_error"}}, 401)
        return None

    @app.get("/health")
    async def health():
        if app.state.loading:
            return JSONResponse({"error": {"code": 503, "message": "Loading model", "type": "unavailable_error"}}, 503)
        return {"status": "ok"}

    @app.get("/props")
    async def props():
        return {
            "default_generation_settings": {"n_ctx": 8192, "params": {"temperature": 0.8, "top_k": 40}},
            "total_slots": 1,
            "model_path": f"/models/{app.state.models[0]}",
            "chat_template": "{{ messages }}",
            "modalities": {"vision": app.state.vision, "audio": False},
            "build_info": "b9999-mock",
        }

    @app.get("/v1/models")
    async def list_models(request: Request):
        if (resp := unauthorized(request)) is not None:
            return resp
        return {
            "object": "list",
            "data": [
                {
                    "id": m,
                    "object": "model",
                    "created": int(time.time()),
                    "owned_by": "llamacpp",
                    "meta": {"vocab_type": 2, "n_vocab": 151936, "n_ctx_train": 32768, "n_embd": 3584, "n_params": 7615616512},
                }
                for m in app.state.models
            ],
        }

    @app.post("/v1/embeddings")
    async def embeddings(request: Request):
        if (resp := unauthorized(request)) is not None:
            return resp
        body = await request.json()
        inputs = body.get("input")
        inputs = [inputs] if isinstance(inputs, str) else inputs
        return {
            "object": "list",
            "model": body.get("model"),
            "data": [{"object": "embedding", "index": i, "embedding": embed_text(t)} for i, t in enumerate(inputs)],
            "usage": {"prompt_tokens": 0, "total_tokens": 0},
        }

    @app.post("/v1/chat/completions")
    async def chat(request: Request):
        if (resp := unauthorized(request)) is not None:
            return resp
        body = await request.json()
        app.state.requests.append(body)
        if body.get("model") not in app.state.models:
            return JSONResponse({"error": {"code": 400, "message": f"model '{body.get('model')}' not found"}}, 400)
        messages = body.get("messages") or []
        last = messages[-1] if messages else {"content": ""}
        content = last.get("content")
        if isinstance(content, list):
            text = " ".join(p.get("text", "") for p in content if p.get("type") == "text")
            images = sum(1 for p in content if p.get("type") == "image_url")
        else:
            text, images = str(content or ""), 0

        if "[error]" in text:
            return JSONResponse({"error": {"code": 500, "message": "the mock server exploded", "type": "server_error"}}, 500)

        if "Write a short, specific title" in text:
            answer = "Mock Conversation Title"
        elif "[markdown]" in text:
            answer = MARKDOWN_SAMPLE
        elif "[slow]" in text:
            answer = " ".join(f"word{i}" for i in range(150))
        else:
            visible = re.sub(r"<attachments>.*?</attachments>\s*", "", text, flags=re.S).strip()
            answer = f"You said: {visible}"
            if images:
                answer += f" (I can see {images} image(s).)"
        reasoning = "Let me think about this carefully." if "[reasoning]" in text else ""

        if not body.get("stream"):
            app.state.completed += 1
            return {
                "id": "chatcmpl-mock",
                "object": "chat.completion",
                "model": body.get("model"),
                "choices": [{"index": 0, "finish_reason": "stop", "message": {"role": "assistant", "content": answer, "reasoning_content": reasoning or None}}],
                "usage": {"prompt_tokens": 10, "completion_tokens": len(tokenize(answer)), "total_tokens": 10 + len(tokenize(answer))},
            }

        delay = 0.02 if "[slow]" in text else 0.0

        def chunk(delta: dict, finish: str | None = None, **extra) -> str:
            payload = {
                "id": "chatcmpl-mock",
                "object": "chat.completion.chunk",
                "created": int(time.time()),
                "model": body.get("model"),
                "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
                **extra,
            }
            return f"data: {json.dumps(payload)}\n\n"

        async def stream():
            try:
                yield chunk({"role": "assistant", "content": None})
                for piece in tokenize(reasoning):
                    yield chunk({"reasoning_content": piece})
                tokens = tokenize(answer)
                buffer = ""
                for i, tok in enumerate(tokens):
                    if "[malformed]" in text and i % 5 == 2:
                        buffer += "data: {not valid json\n\n"
                    buffer += chunk({"content": tok})
                    if "[midstream-error]" in text and i == 3:
                        buffer += 'data: {"error": {"code": 500, "message": "slot crashed"}}\n\n'
                        yield buffer
                        return
                    # Flush at odd boundaries to exercise the incremental parser.
                    if delay or len(buffer) > 300 or i % 3 == 0:
                        cut = len(buffer) // 2 if i % 2 else len(buffer)
                        yield buffer[:cut]
                        buffer = buffer[cut:]
                        if delay:
                            await asyncio.sleep(delay)
                if buffer:
                    yield buffer
                if "[truncate]" in text:
                    return
                yield chunk({}, "stop", timings={"prompt_n": 12, "prompt_ms": 40.5, "predicted_n": len(tokens), "predicted_ms": 900.0, "predicted_per_second": 42.3})
                yield chunk({}, None, usage={"prompt_tokens": 12, "completion_tokens": len(tokens), "total_tokens": 12 + len(tokens)})
                yield "data: [DONE]\n\n"
                app.state.completed += 1
            except asyncio.CancelledError:
                app.state.cancelled += 1
                raise

        return StreamingResponse(stream(), media_type="text/event-stream")

    return app


def main() -> None:
    import uvicorn

    parser = argparse.ArgumentParser(description="Mock llama-server for Chimera development")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--model", action="append", help="Model id to advertise (repeatable)")
    parser.add_argument("--vision", action="store_true")
    args = parser.parse_args()
    uvicorn.run(create_mock_app(models=args.model, vision=args.vision), host=args.host, port=args.port)


if __name__ == "__main__":
    main()
