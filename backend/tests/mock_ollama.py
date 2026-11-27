"""A mock of Ollama's native API (``/api/tags``, ``/api/show``, ``/api/chat``).

Models and their capabilities are like Ollama's: ``qwen3`` thinks and can be
switched off (``think: true/false``), ``gpt-oss`` takes a level
(``"low"``/``"medium"``/``"high"``) and can't be switched off, ``llama3.2``
doesn't think and answers 400 to ``think`` like Ollama does. Request bodies
are recorded in ``app.state.requests``.
"""

from __future__ import annotations

import json

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse

MODELS = {
    "qwen3:8b": {"capabilities": ["completion", "tools", "thinking"], "family": "qwen3"},
    "gpt-oss:20b": {"capabilities": ["completion", "tools", "thinking"], "family": "gptoss"},
    "llama3.2:3b": {"capabilities": ["completion", "tools"], "family": "llama"},
    "llava:7b": {"capabilities": ["completion", "vision"], "family": "llama"},
}


def create_app() -> FastAPI:
    app = FastAPI()
    app.state.requests = []

    @app.get("/api/tags")
    async def tags():
        return {"models": [{"name": m, "model": m, "size": 1, "details": {"family": v["family"]}} for m, v in MODELS.items()]}

    @app.get("/api/version")
    async def version():
        return {"version": "0.13.0"}

    @app.post("/api/show")
    async def show(request: Request):
        name = (await request.json()).get("model")
        if name not in MODELS:
            return JSONResponse({"error": f"model '{name}' not found"}, 404)
        return {"details": {"family": MODELS[name]["family"]}, "capabilities": MODELS[name]["capabilities"]}

    @app.post("/api/chat")
    async def chat(request: Request):
        body = await request.json()
        app.state.requests.append(body)
        model = MODELS.get(body.get("model"))
        if not model:
            return JSONResponse({"error": f"model '{body.get('model')}' not found"}, 404)
        think = body.get("think")
        if think is not None and "thinking" not in model["capabilities"]:
            return JSONResponse({"error": f"\"{body['model']}\" does not support thinking"}, 400)
        if model["family"] == "gptoss" and think is False:
            think = "medium"  # gpt-oss ignores false: it always thinks
        if model["family"] == "gptoss" and think is True:
            think = "medium"
        if think not in (None, False, True, "low", "medium", "high"):
            return JSONResponse({"error": f"invalid think value {think!r}"}, 400)
        thinks = think not in (None, False) or (think is None and "thinking" in model["capabilities"])
        text = (body.get("messages") or [{}])[-1].get("content", "")
        answer = "Mock Ollama Title" if "Write a short, specific title" in text else f"Ollama says: {text[:200]}"

        def lines():
            if thinks:
                yield json.dumps({"message": {"role": "assistant", "content": "", "thinking": f"Thinking ({think if think is not None else 'default'})."}, "done": False}) + "\n"
            for word in answer.split(" "):
                yield json.dumps({"message": {"role": "assistant", "content": word + " "}, "done": False}) + "\n"
            yield json.dumps({"done": True, "done_reason": "stop", "prompt_eval_count": 9, "eval_count": 5, "eval_duration": 1_000_000_000}) + "\n"

        return StreamingResponse(lines(), media_type="application/x-ndjson")

    return app
