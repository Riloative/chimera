import asyncio
import json

import httpx
import pytest

from chimera.providers import ChatMessage, ChatRequest, ProviderConfig, ProviderError
from chimera.providers.ollama import OllamaProvider
from chimera.providers.openai_compat import LlamaCppProvider, OpenAICompatibleProvider


def run(coro):
    return asyncio.run(coro)


def llama(url: str, **kw) -> tuple[LlamaCppProvider, httpx.AsyncClient]:
    client = httpx.AsyncClient(trust_env=False)
    return LlamaCppProvider(ProviderConfig(id="p1", name="Local", kind="llamacpp", base_url=url, **kw), client), client


def test_check_reports_models_and_capabilities(mock):
    mock.app.state.vision = True

    async def go():
        provider, client = llama(mock.url)
        async with client:
            return await provider.check()

    report = run(go())
    assert report.ok and report.models_ok
    assert [m.id for m in report.models] == mock.app.state.models
    assert report.models[0].name == "qwen2.5-7b-instruct-q4_k_m"
    assert report.models[0].context_length == 32768
    assert report.capabilities["vision"] is True
    assert report.capabilities["n_ctx"] == 8192
    assert report.capabilities["slots"] == 1
    assert report.latency_ms is not None
    assert "top_k" in report.capabilities["params"]


def test_check_while_loading_is_not_ok(mock):
    mock.app.state.loading = True

    async def go():
        provider, client = llama(mock.url)
        async with client:
            return await provider.check()

    report = run(go())
    assert not report.ok
    assert report.error_code == "unavailable"
    assert "Loading model" in report.error


def test_connection_refused_is_friendly():
    async def go():
        provider, client = llama("http://127.0.0.1:9")  # discard port: nothing listens
        async with client:
            return await provider.check()

    report = run(go())
    assert not report.ok
    assert report.error_code == "connection"
    assert "Could not connect" in report.error


def test_api_key_is_sent_as_bearer(mock):
    mock.app.state.api_key = "s3cret"

    async def go(key):
        provider, client = llama(mock.url, api_key=key)
        async with client:
            return await provider.list_models()

    with pytest.raises(ProviderError) as err:
        run(go(None))
    assert err.value.code == "auth"
    assert run(go("s3cret"))


def test_stream_chat_content_reasoning_and_usage(mock):
    async def go():
        provider, client = llama(mock.url)
        async with client:
            req = ChatRequest(model=mock.app.state.models[0], messages=[ChatMessage("user", "hi [reasoning]")],
                              params={"temperature": 0.2, "top_k": 20, "bogus": 1})
            return [e async for e in provider.stream_chat(req)]

    events = run(go())
    text = "".join(e.text for e in events if e.type == "content")
    reasoning = "".join(e.text for e in events if e.type == "reasoning")
    assert text == "You said: hi [reasoning]"
    assert reasoning == "Let me think about this carefully."
    usage = next(e.usage for e in events if e.type == "usage")
    assert usage["tokens_per_second"] == 42.3
    assert events[-1].type == "done" and events[-1].finish_reason == "stop"
    sent = mock.app.state.requests[-1]
    assert sent["stream"] is True and sent["top_k"] == 20 and "bogus" not in sent


def test_generic_openai_drops_llama_only_params(mock):
    async def go():
        client = httpx.AsyncClient(trust_env=False)
        provider = OpenAICompatibleProvider(ProviderConfig(id="o", name="O", kind="openai", base_url=mock.url), client)
        async with client:
            req = ChatRequest(model=mock.app.state.models[0], messages=[ChatMessage("user", "x")],
                              params={"temperature": 0.5, "top_k": 3, "repeat_penalty": 1.1})
            return [e async for e in provider.stream_chat(req)]

    run(go())
    sent = mock.app.state.requests[-1]
    assert sent["temperature"] == 0.5 and "top_k" not in sent and "repeat_penalty" not in sent


def test_malformed_events_are_skipped(mock):
    async def go():
        provider, client = llama(mock.url)
        async with client:
            req = ChatRequest(model=mock.app.state.models[0], messages=[ChatMessage("user", "abc [malformed]")])
            return [e async for e in provider.stream_chat(req)]

    events = run(go())
    assert "".join(e.text for e in events if e.type == "content") == "You said: abc [malformed]"


def test_truncated_stream_raises_incomplete(mock):
    async def go():
        provider, client = llama(mock.url)
        async with client:
            req = ChatRequest(model=mock.app.state.models[0], messages=[ChatMessage("user", "x [truncate]")])
            return [e async for e in provider.stream_chat(req)]

    with pytest.raises(ProviderError) as err:
        run(go())
    assert err.value.code == "incomplete"


def test_http_error_body_is_surfaced(mock):
    async def go():
        provider, client = llama(mock.url)
        async with client:
            req = ChatRequest(model=mock.app.state.models[0], messages=[ChatMessage("user", "[error]")])
            return [e async for e in provider.stream_chat(req)]

    with pytest.raises(ProviderError) as err:
        run(go())
    assert "the mock server exploded" in err.value.message and err.value.status == 500


def test_images_are_encoded_as_content_parts():
    from chimera.providers.openai_compat import encode_messages

    out = encode_messages([ChatMessage("user", "look", images=[("image/png", "AAAA")])], allow_images=True)
    assert out[0]["content"][1]["image_url"]["url"] == "data:image/png;base64,AAAA"
    assert encode_messages([ChatMessage("user", "look", images=[("image/png", "AAAA")])], False)[0]["content"] == "look"


def test_ollama_adapter_parses_ndjson():
    lines = [
        {"message": {"role": "assistant", "content": "", "thinking": "hmm"}, "done": False},
        {"message": {"role": "assistant", "content": "Hel"}, "done": False},
        {"message": {"role": "assistant", "content": "lo"}, "done": False},
        {"done": True, "done_reason": "stop", "prompt_eval_count": 5, "eval_count": 2, "eval_duration": 1_000_000_000},
    ]
    body = ("\n".join(json.dumps(x) for x in lines) + "\n").encode()
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/tags":
            return httpx.Response(200, json={"models": [{"name": "llama3.2:3b", "size": 1, "details": {"family": "llama"}}]})
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, content=body, headers={"content-type": "application/x-ndjson"})

    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            p = OllamaProvider(ProviderConfig(id="o", name="Ollama", kind="ollama", base_url="http://ollama"), client)
            models = await p.list_models()
            events = [e async for e in p.stream_chat(ChatRequest(model="llama3.2:3b", messages=[ChatMessage("user", "hi")], params={"max_tokens": 9}))]
            return models, events

    models, events = run(go())
    assert models[0].id == "llama3.2:3b"
    assert "".join(e.text for e in events if e.type == "content") == "Hello"
    assert "".join(e.text for e in events if e.type == "reasoning") == "hmm"
    assert next(e for e in events if e.type == "usage").usage["tokens_per_second"] == 2.0
    assert seen["body"]["options"] == {"num_predict": 9}


def test_quick_requests_turn_thinking_off_only_where_supported():
    client = httpx.AsyncClient()
    req = ChatRequest(model="m", messages=[ChatMessage(role="user", content="hi")], params={"max_tokens": 9}, quick=True)
    llama = LlamaCppProvider(ProviderConfig(id="l", name="L", kind="llamacpp", base_url="http://x"), client)
    generic = OpenAICompatibleProvider(ProviderConfig(id="o", name="O", kind="openai", base_url="http://x"), client)
    assert llama.build_body(req)["chat_template_kwargs"] == {"enable_thinking": False}
    assert "chat_template_kwargs" not in generic.build_body(req)
    normal = ChatRequest(model="m", messages=req.messages)
    assert "chat_template_kwargs" not in llama.build_body(normal)
    asyncio.run(client.aclose())
