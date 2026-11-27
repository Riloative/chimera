"""A mock of llama.cpp's ``llama-server`` HTTP API for tests and UI development.

It implements the endpoints Chimera uses with realistic payload shapes:
``/health``, ``/props``, ``/v1/models``, ``/v1/chat/completions`` (SSE and
JSON) and ``/v1/embeddings``. Behaviour is steered by markers in the last
user message:

* ``[slow]``             – ~50 tokens/s for ~300 tokens (use to test Stop)
* ``[waitfirst]``        – 2 s before the first token (waiting state)
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


def mock_queries(prompt: str) -> str:
    """Answer a query-generation prompt like a small model would."""
    message = prompt.rsplit("Latest user message:", 1)[-1].strip()
    if "[badjson]" in message:
        return "Sure! Here are some good queries: weather today, news"
    words = re.sub(r"https?://\S+|\[[a-z]+\]", " ", message).split()
    base = " ".join(words[:6]) or "query"
    return "<think>planning</think>" + json.dumps({"queries": [base, f"{base} latest"]})


def mock_decision(prompt: str) -> str:
    """Answer an Auto search decision: search for "[auto-yes]" and for
    time-sensitive words; "[auto-fail]" gives an unusable reply."""
    message = prompt.rsplit("Latest user message:", 1)[-1].strip()
    if "[auto-fail]" in message:
        return "Hmm, hard to say."
    needed = "[auto-yes]" in message or bool(re.search(r"\b(latest|news|today|weather|festival|price)\b", message, re.I))
    words = re.sub(r"https?://\S+|\[[a-z-]+\]", " ", message).split()
    base = " ".join(words[:6]) or "query"
    return "<think>deciding</think>" + json.dumps(
        {"search": needed, "reason": "current information" if needed else "general question", "queries": [base] if needed else []}
    )


def mock_grounded_answer(text: str) -> str:
    """Cite every source the app provided, quoting the first line of each, and
    say so truthfully when a source could not be read."""
    parts = []
    for m in re.finditer(r'<source id="(\d+)" token="(\w+)">\n(.*?)\n---\n?(.*?)</source token="\2">', text, re.S):
        n, head, body = m.group(1), m.group(3), m.group(4).strip()
        if "COULD NOT BE READ" in head:
            parts.append(f"Source [{n}] could not be read.")
            continue
        first = next((ln.strip("#*- ").strip() for ln in body.splitlines() if ln.strip()), "")
        parts.append(f"According to [{n}], {first[:120]}")
    if "[badcite]" in text:
        parts.append("See also [42].")
    return "\n\n".join(parts) or "No sources were provided."


def mock_tokens(text: str) -> int:
    """Deterministic "tokenizer": words and punctuation marks (deliberately not
    the 3.5-characters estimate, so tests can tell exact counts apart)."""
    return len(re.findall(r"\w+|[^\w\s]", text))


def render_template(messages: list[dict]) -> str:
    parts = []
    for m in messages:
        content = m.get("content")
        if isinstance(content, list):
            content = " ".join(p.get("text", "") for p in content if isinstance(p, dict))
        parts.append(f"<|im_start|>{m.get('role')}\n{content}<|im_end|>")
    return "\n".join(parts) + "\n<|im_start|>assistant\n"


def configure_context(app, n_ctx: int = 8192, slots: int = 1) -> None:
    app.state.n_ctx = n_ctx  # total; /props reports n_ctx // slots like llama-server
    app.state.slots = slots
    app.state.enforce_ctx = None  # enforce a different size than reported (estimate errors)
    app.state.n_ctx_error = None  # n_ctx claimed in the overflow error (default: the enforced size)
    app.state.tokenize_supported = True
    app.state.tokenize_calls = 0
    app.state.prompt_sizes = []
    app.state.summaries = 0
    app.state.summary_fail = False
    app.state.think_titles = False


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
    configure_context(app)

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
        # Like llama-server: n_ctx here is the per-slot context (n_ctx / --parallel).
        return {
            "default_generation_settings": {"n_ctx": app.state.n_ctx // app.state.slots, "params": {"temperature": 0.8, "top_k": 40}},
            "total_slots": app.state.slots,
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

    @app.post("/__mock/think-titles")
    async def set_think_titles(request: Request):
        """Test control: behave like a reasoning model when writing titles."""
        app.state.think_titles = bool((await request.json()).get("enabled"))
        return {"think_titles": app.state.think_titles}

    @app.post("/tokenize")
    async def tokenize_endpoint(request: Request):
        if not app.state.tokenize_supported:
            return JSONResponse({"error": {"code": 404, "message": "File Not Found"}}, 404)
        body = await request.json()
        app.state.tokenize_calls += 1
        return {"tokens": list(range(mock_tokens(str(body.get("content", "")))))}

    @app.post("/apply-template")
    async def apply_template(request: Request):
        if not app.state.tokenize_supported:
            return JSONResponse({"error": {"code": 404, "message": "File Not Found"}}, 404)
        body = await request.json()
        return {"prompt": render_template(body.get("messages") or [])}

    @app.post("/v1/chat/completions")
    async def chat(request: Request):
        if (resp := unauthorized(request)) is not None:
            return resp
        body = await request.json()
        app.state.requests.append(body)
        if body.get("model") not in app.state.models:
            return JSONResponse({"error": {"code": 400, "message": f"model '{body.get('model')}' not found"}}, 400)
        n_prompt = mock_tokens(render_template(body.get("messages") or []))
        app.state.prompt_sizes.append(n_prompt)
        limit = (app.state.enforce_ctx or app.state.n_ctx) // app.state.slots
        if n_prompt >= limit:
            # Exactly what llama-server sends (tools/server/server-context.cpp).
            return JSONResponse({"error": {
                "code": 400, "type": "exceed_context_size_error", "n_prompt_tokens": n_prompt,
                "n_ctx": app.state.n_ctx_error or limit,
                "message": f"request ({n_prompt} tokens) exceeds the available context size ({limit} tokens), try increasing it",
            }}, 400)
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
        elif "Summarize the earlier part of a conversation" in text:
            if app.state.summary_fail:
                return JSONResponse({"error": {"code": 500, "message": "summary failed"}}, 500)
            app.state.summaries += 1
            words = re.findall(r"text\d+|\w+", text.rsplit("Messages to summarize:", 1)[-1])
            answer = f"SUMMARY#{app.state.summaries}: " + " ".join(words[:12])
        elif "needs a web search" in text and "Latest user message:" in text:
            answer = mock_decision(text)
        elif "Write web search queries" in text:
            answer = mock_queries(text)
            if "[slowquery]" in text:
                await asyncio.sleep(5)
        elif "[cite-earlier]" in text:
            # A follow-up answer that cites the pages sent with earlier turns.
            whole = "\n".join(str(m.get("content") or "") for m in messages)
            answer = mock_grounded_answer(whole)
        elif "<web_sources>" in text and "[slow]" not in text:
            answer = mock_grounded_answer(text)
        elif "[markdown]" in text:
            answer = MARKDOWN_SAMPLE
        elif "[slow]" in text:
            answer = " ".join(f"word{i}" for i in range(150))
        else:
            visible = re.sub(r"<attachments>.*?</attachments>\s*", "", text, flags=re.S)
            visible = re.sub(r"<web_sources>.*?</web_sources>\s*", "", visible, flags=re.S).strip()
            answer = f"You said: {visible}"
            if images:
                answer += f" (I can see {images} image(s).)"
        reasoning = "Let me think about this carefully." if "[reasoning]" in text else ""
        if ("Write a short, specific title" in text and app.state.think_titles
                and (body.get("chat_template_kwargs") or {}).get("enable_thinking") is not False):
            # Like a reasoning model (e.g. Qwen3): it thinks first, and the
            # thinking uses up max_tokens before any answer is written.
            reasoning = " ".join(f"thought{i}" for i in range(120))
            if int(body.get("max_tokens") or 100_000) < 200:
                answer = ""

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
                if "[waitfirst]" in text:
                    await asyncio.sleep(2)  # the model takes a while before the first token
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
