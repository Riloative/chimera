"""Thinking (reasoning effort) per conversation: the neutral levels, the
capability descriptor, the mapping for every provider kind and hosted service,
clamping, quick requests, the retry without rejected fields, migration 6 and
the API. Hosted services are imitated by the mocks (rules taken from each
provider's documentation); nothing here talks to a real API."""

from __future__ import annotations

import json
import sqlite3

import pytest

from chimera import db as dbmod
from chimera.providers import reasoning as rz
from chimera.providers.anthropic import AnthropicProvider
from chimera.providers.openai_compat import ThinkSplitter
from tests.conftest import LiveServer
from tests.mock_anthropic import create_app as anthropic_app
from tests.mock_ollama import create_app as ollama_app
from tests.test_web_chat import detail, final, send


@pytest.fixture(scope="module")
def claude():
    server = LiveServer(anthropic_app(api_key="sk-ant-test")).start()
    yield server
    server.stop()


@pytest.fixture(scope="module")
def ollama():
    server = LiveServer(ollama_app()).start()
    yield server
    server.stop()


def connect(client, **data) -> dict:
    resp = client.post("/api/providers", json={"name": data.pop("name", "Hosted"), **data})
    assert resp.status_code == 201, resp.text
    return resp.json()


def models(client, provider_id) -> dict[str, dict]:
    entry = next(e for e in client.get("/api/models", params={"refresh": True}).json()["providers"] if e["provider"]["id"] == provider_id)
    return {m["id"]: m for m in entry["models"]}


def chat(client, provider_id, model, thinking=None) -> str:
    body = {"provider_id": provider_id, "model": model, **({"thinking": thinking} if thinking else {})}
    resp = client.post("/api/conversations", json=body)
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


def answer_requests(requests):
    return [b for b in requests if "Write a short, specific title" not in json.dumps(b.get("messages", [])[-1:])]


def title_request(requests):
    return next(b for b in requests if "Write a short, specific title" in json.dumps(b.get("messages", [])[-1:]))


# ------------------------------------------------------------------ levels
def test_clamping_picks_the_nearest_supported_level():
    four = rz.levels("openai", "table", ("off", "low", "medium", "high"))
    assert [rz.clamp(lv, four) for lv in ("default", "off", "low", "max", "on")] == ["default", "off", "low", "high", "medium"]
    no_off = rz.levels("openai", "table", ("low", "medium", "high"))
    assert rz.clamp("off", no_off) == "low" and rz.cheapest(no_off) == "low"
    two = rz.levels("deepseek", "table", ("off", "low", "high", "max"))
    assert rz.clamp("medium", two) == "high"  # a tie goes up: the user asked for more than "low"
    switch = rz.toggle("llama_toggle", "detected")
    assert [rz.clamp(lv, switch) for lv in ("off", "low", "max", "on")] == ["off", "on", "on", "on"]
    assert rz.cheapest(switch) == "off"
    for caps in (rz.NONE, rz.always("deepseek", "table")):
        assert rz.clamp("high", caps) == "default" and rz.cheapest(caps) == "default"


def test_services_are_recognised_by_host_or_chosen():
    assert rz.service_of("https://api.openai.com") == "openai"
    assert rz.service_of("https://eu.api.mistral.ai/v1") == "mistral"
    assert rz.service_of("https://generativelanguage.googleapis.com") == "gemini"
    assert rz.service_of("https://openrouter.ai/api") == "openrouter"
    assert rz.service_of("http://127.0.0.1:1234") == "other"
    assert rz.service_of("http://127.0.0.1:1234", "xai") == "xai"


@pytest.mark.parametrize(("service", "model", "shape", "levels"), [
    ("openai", "gpt-5.2", "levels", ["off", "low", "medium", "high", "max"]),
    ("openai", "gpt-5-mini", "levels", ["off", "low", "medium", "high"]),
    ("openai", "o3-mini", "levels", ["low", "medium", "high"]),
    ("openai", "gpt-4o", "none", []),
    ("mistral", "mistral-small-latest", "toggle", ["off", "on"]),
    ("mistral", "mistral-large-latest", "levels", ["low", "medium", "high"]),
    ("mistral", "magistral-medium-latest", "always", []),
    ("gemini", "models/gemini-2.5-flash", "levels", ["off", "low", "medium", "high"]),
    ("gemini", "gemini-2.5-pro", "levels", ["low", "medium", "high"]),
    ("gemini", "gemini-2.0-flash", "none", []),
    ("deepseek", "deepseek-chat", "levels", ["off", "low", "high", "max"]),
    ("deepseek", "deepseek-reasoner", "always", []),
    ("xai", "grok-4.7", "levels", ["low", "medium", "high", "max"]),
    ("xai", "grok-4.3", "levels", ["off", "low", "medium"]),
    ("xai", "grok-4-0709", "always", []),
    ("xai", "grok-4.7-non-reasoning", "none", []),
    ("other", "gpt-5.2", "none", []),  # an unknown server is sent nothing new
])
def test_table(service, model, shape, levels):
    caps = rz.table_caps(service, model)
    assert (caps.shape, list(caps.levels)) == (shape, levels)


@pytest.mark.parametrize(("model", "off", "levels"), [
    ("claude-opus-5-5", None, ["low", "medium", "high", "max"]),
    ("claude-fable-5-2", None, ["low", "medium", "high", "max"]),
    ("claude-sonnet-5-5", "between_tools", ["off", "low", "medium", "high", "max"]),
    ("claude-opus-4-8", "disabled", ["off", "low", "medium", "high", "max"]),
    ("claude-sonnet-4-6", "disabled", ["off", "low", "medium", "high", "max"]),
])
def test_anthropic_families(model, off, levels):
    caps = rz.anthropic_caps(model)
    assert (caps.flavor, caps.off, list(caps.levels)) == ("anthropic", off, levels)
    assert rz.anthropic_caps("claude-haiku-4-5").flavor == "anthropic_budget"
    assert rz.anthropic_caps("claude-3-haiku-20240307").shape == "none"


# --------------------------------------------------------------- mapping
def test_the_mapping_table():
    """docs/ARCHITECTURE.md ("Thinking") shows this table; keep them in step."""
    gpt = rz.table_caps("openai", "gpt-5.2")
    assert rz.openai_fields(gpt, "off") == {"reasoning_effort": "none"}
    assert rz.openai_fields(gpt, "max") == {"reasoning_effort": "xhigh"}
    assert rz.openai_fields(gpt, "default") == {}
    assert rz.openai_fields(rz.table_caps("openai", "gpt-5"), "off") == {"reasoning_effort": "minimal"}
    assert rz.openai_fields(rz.table_caps("mistral", "mistral-small-latest"), "on") == {"reasoning_effort": "high"}
    assert rz.openai_fields(rz.table_caps("mistral", "mistral-small-latest"), "off") == {"reasoning_effort": "none"}
    assert rz.openai_fields(rz.table_caps("gemini", "gemini-2.5-flash"), "off") == {"reasoning_effort": "none"}
    ds = rz.table_caps("deepseek", "deepseek-chat")
    assert rz.openai_fields(ds, "off") == {"thinking": {"type": "disabled"}}
    assert rz.openai_fields(ds, "max") == {"thinking": {"type": "enabled"}, "reasoning_effort": "max"}
    assert rz.openai_fields(rz.table_caps("xai", "grok-4.7"), "max") == {"reasoning_effort": "xhigh"}
    llama = rz.toggle("llama_toggle", "detected")
    assert rz.openai_fields(llama, "off") == {"chat_template_kwargs": {"enable_thinking": False}}
    assert rz.openai_fields(llama, "on") == {"chat_template_kwargs": {"enable_thinking": True}}
    oss = rz.llama_template_caps("{% if reasoning_effort %}...{% endif %}")
    assert rz.openai_fields(oss, "high") == {"chat_template_kwargs": {"reasoning_effort": "high"}}
    router = rz.openrouter_caps({"supported_parameters": ["reasoning"], "reasoning": {"supported_efforts": ["low", "medium", "high", "xhigh"]}})
    assert rz.openai_fields(router, "off") == {"reasoning": {"enabled": False}}
    assert rz.openai_fields(router, "max") == {"reasoning": {"effort": "xhigh"}}
    assert rz.ollama_fields(rz.toggle("ollama", "detected"), "off") == {"think": False}
    assert rz.ollama_fields(rz.levels("ollama", "detected", ("low", "medium", "high")), "high") == {"think": "high"}
    f = AnthropicProvider.thinking_fields
    opus = rz.anthropic_caps("claude-opus-5-5")
    assert f(opus, "high", "claude-opus-5-5") == {"thinking": {"type": "adaptive", "display": "summarized"}, "output_config": {"effort": "high"}}
    assert f(opus, "default", "claude-opus-5-5") == {"thinking": {"type": "adaptive", "display": "summarized"}}
    assert f(rz.anthropic_caps("claude-sonnet-5-5"), "off", "claude-sonnet-5-5") == {"thinking": {"type": "between_tools"}}
    assert f(rz.anthropic_caps("claude-sonnet-4-6"), "off", "claude-sonnet-4-6") == {"thinking": {"type": "disabled"}}
    assert f(rz.anthropic_caps("claude-sonnet-4-6"), "default", "claude-sonnet-4-6") == {}
    assert f(rz.anthropic_caps("claude-haiku-4-5"), "medium", "claude-haiku-4-5") == {"thinking": {"type": "enabled", "budget_tokens": 8192}}


def test_inline_think_tags_become_reasoning_whatever_the_chunking():
    text = "  <think>step one\nstep two</think>\n\nThe answer is <b>4</b>."
    for size in (1, 2, 3, 5, 7, 100):
        s = ThinkSplitter()
        parts = [p for i in range(0, len(text), size) for p in s.feed(text[i:i + size])] + s.flush()
        assert "".join(t for k, t in parts if k == "reasoning") == "step one\nstep two"
        assert "".join(t for k, t in parts if k == "content") == "The answer is <b>4</b>."
    s = ThinkSplitter()
    assert s.feed("<thin") == [] and s.feed("king about it") == [("content", "<thinking about it")]
    s = ThinkSplitter()
    assert s.feed("<think>never closed") == [("reasoning", "never closed")] and s.flush() == []


# ----------------------------------------------------- llama-server (live mock)
def test_llama_server_template_is_detected_and_levels_are_sent(client, mock):
    mock.app.state.chat_template = "{%- if enable_thinking is defined and not enable_thinking %}<think></think>{%- endif %}"
    provider = connect(client, name="Local", kind="llamacpp", base_url=mock.url)
    model = mock.app.state.models[0]
    assert models(client, provider["id"])[model]["thinking"] == {"shape": "toggle", "levels": ["off", "on"], "default": "on", "source": "detected"}
    cid = chat(client, provider["id"], model, "off")
    send(client, cid, "hello")
    assert answer_requests(mock.app.state.requests)[-1]["chat_template_kwargs"] == {"enable_thinking": False}
    client.patch(f"/api/conversations/{cid}", json={"thinking": "high"})  # clamped to "on"
    msg = final(send(client, cid, "again"))
    assert answer_requests(mock.app.state.requests)[-1]["chat_template_kwargs"] == {"enable_thinking": True}
    assert msg["reasoning"].startswith("Thinking (on)")
    assert msg["metrics"]["thinking"] == {"requested": "high", "applied": "on", "source": "detected"}
    client.patch(f"/api/conversations/{cid}", json={"thinking": "default"})
    send(client, cid, "and again")
    assert "chat_template_kwargs" not in answer_requests(mock.app.state.requests)[-1]


def test_a_template_without_thinking_gets_no_control_and_quick_requests_keep_working(client, provider, mock):
    model = mock.app.state.models[0]
    assert models(client, provider["id"])[model]["thinking"]["shape"] == "none"
    cid = chat(client, provider["id"], model, "high")
    send(client, cid, "hello")
    assert "chat_template_kwargs" not in answer_requests(mock.app.state.requests)[-1]
    # Titles still ask Qwen3-style templates to skip thinking (harmless elsewhere).
    assert title_request(mock.app.state.requests)["chat_template_kwargs"] == {"enable_thinking": False}


# ------------------------------------------------- hosted services (mocked)
def test_openai_reasoning_models_get_their_parameters_right(client, mock):
    mock.app.state.models = ["gpt-5.2", "o3-mini", "gpt-4o"]
    mock.app.state.rules = {
        "gpt-5.2": {"reject": ["max_tokens", "temperature", "top_p"], "efforts": ["none", "low", "medium", "high", "xhigh"]},
        "o3-mini": {"reject": ["max_tokens", "temperature", "stop"], "efforts": ["low", "medium", "high"]},
    }
    provider = connect(client, kind="openai", base_url=mock.url, service="openai")
    found = models(client, provider["id"])
    assert found["gpt-5.2"]["thinking"]["levels"] == ["off", "low", "medium", "high", "max"]
    assert found["gpt-4o"]["thinking"]["shape"] == "none"
    cid = chat(client, provider["id"], "gpt-5.2", "max")
    client.patch(f"/api/conversations/{cid}", json={"params": {"temperature": 0.4, "max_tokens": 900}})
    msg = final(send(client, cid, "hello"))
    assert msg["status"] == "complete", msg["error"]
    body = answer_requests(mock.app.state.requests)[-1]
    assert body["reasoning_effort"] == "xhigh" and body["max_completion_tokens"] == 900
    assert "max_tokens" not in body and "temperature" not in body
    # The title is a quick request: the cheapest setting, and room to answer.
    title = title_request(mock.app.state.requests)
    assert title["reasoning_effort"] == "none" and "temperature" not in title
    assert detail(client, cid)["title"] == "Mock Conversation Title"
    # o3-mini can't switch thinking off: "off" becomes its lowest level, quietly.
    cid = chat(client, provider["id"], "o3-mini", "off")
    client.patch(f"/api/conversations/{cid}", json={"params": {"stop": ["END"]}})
    msg = final(send(client, cid, "hi"))
    assert msg["status"] == "complete", msg["error"]
    assert answer_requests(mock.app.state.requests)[-1]["reasoning_effort"] == "low" and msg["metrics"]["thinking"]["applied"] == "low"
    assert "stop" not in answer_requests(mock.app.state.requests)[-1]
    # A model without thinking is sent nothing new.
    mock.app.state.requests.clear()
    send(client, chat(client, provider["id"], "gpt-4o", "high"), "hi")
    assert all("reasoning_effort" not in b for b in mock.app.state.requests)


def test_a_rejected_thinking_field_is_retried_once_without_it(client, mock):
    mock.app.state.models = ["gpt-5.2-preview"]
    mock.app.state.rules = {"gpt-5.2-preview": {"no_reasoning": True}}
    provider = connect(client, kind="openai", base_url=mock.url, service="openai")
    cid = chat(client, provider["id"], "gpt-5.2-preview", "high")
    msg = final(send(client, cid, "hello"))
    assert msg["status"] == "complete" and msg["content"].startswith("You said")
    sent = answer_requests(mock.app.state.requests)
    assert [("reasoning_effort" in b) for b in sent] == [True, False]  # one retry, without the field
    assert msg["metrics"]["thinking"]["rejected"] is True
    # Remembered for a while: the control says so and nothing is sent again.
    assert models(client, provider["id"])["gpt-5.2-preview"]["thinking"] == {"shape": "none", "levels": [], "default": None, "source": "rejected"}
    mock.app.state.requests.clear()
    send(client, cid, "again")
    assert len(answer_requests(mock.app.state.requests)) == 1 and "reasoning_effort" not in mock.app.state.requests[0]


def test_an_unsupported_value_is_retried_and_other_errors_are_not(client, mock):
    mock.app.state.models = ["gpt-5.2"]
    mock.app.state.rules = {"gpt-5.2": {"efforts": ["low", "medium", "high"]}}  # this deployment has no "none"
    provider = connect(client, kind="openai", base_url=mock.url, service="openai")
    msg = final(send(client, chat(client, provider["id"], "gpt-5.2", "off"), "hello"))
    assert msg["status"] == "complete" and msg["metrics"]["thinking"]["rejected"] is True
    # A failure that has nothing to do with thinking is not retried.
    mock.app.state.requests.clear()
    msg = final(send(client, chat(client, provider["id"], "gpt-5.2", "high"), "[error] boom"))
    assert msg["status"] == "error" and len(answer_requests(mock.app.state.requests)) == 1


def test_mistral_thinking_chunks_deepseek_xai_gemini_and_openrouter(client, mock):
    mock.app.state.models = ["mistral-small-latest"]
    mock.app.state.rules = {"mistral-small-latest": {"style": "mistral"}}
    mistral = connect(client, kind="openai", base_url=mock.url, service="mistral")
    msg = final(send(client, chat(client, mistral["id"], "mistral-small-latest", "on"), "hello"))
    assert answer_requests(mock.app.state.requests)[-1]["reasoning_effort"] == "high"
    assert msg["reasoning"].startswith("Thinking (high)") and msg["content"] == "You said: hello"

    mock.app.state.models = ["deepseek-chat"]
    deepseek = connect(client, kind="openai", base_url=mock.url, service="deepseek")
    send(client, chat(client, deepseek["id"], "deepseek-chat", "off"), "hello")
    assert answer_requests(mock.app.state.requests)[-1]["thinking"] == {"type": "disabled"}
    send(client, chat(client, deepseek["id"], "deepseek-chat", "medium"), "hello")
    last = answer_requests(mock.app.state.requests)[-1]
    assert last["thinking"] == {"type": "enabled"} and last["reasoning_effort"] == "high"

    mock.app.state.models = ["grok-4.7"]
    mock.app.state.rules = {"grok-4.7": {"reject": ["presence_penalty", "stop"]}}
    xai = connect(client, kind="openai", base_url=mock.url, service="xai")
    cid = chat(client, xai["id"], "grok-4.7", "max")
    client.patch(f"/api/conversations/{cid}", json={"params": {"presence_penalty": 0.5}})
    assert final(send(client, cid, "hello"))["status"] == "complete"
    assert answer_requests(mock.app.state.requests)[-1]["reasoning_effort"] == "xhigh"

    mock.app.state.models = ["gemini-2.5-pro"]
    gemini = connect(client, kind="openai", base_url=mock.url, service="gemini")
    send(client, chat(client, gemini["id"], "gemini-2.5-pro", "off"), "hello")
    assert answer_requests(mock.app.state.requests)[-1]["reasoning_effort"] == "low"  # 2.5 Pro can't switch it off

    mock.app.state.models = ["deepseek/deepseek-r1", "openai/gpt-4o-mini"]
    mock.app.state.model_extra = {"deepseek/deepseek-r1": {"supported_parameters": ["reasoning"],
                                                           "reasoning": {"supported_efforts": ["low", "medium", "high"], "mandatory": True}}}
    router = connect(client, kind="openai", base_url=mock.url, service="openrouter")
    found = models(client, router["id"])
    assert found["deepseek/deepseek-r1"]["thinking"] == {"shape": "levels", "levels": ["low", "medium", "high"], "default": None, "source": "detected"}
    assert found["openai/gpt-4o-mini"]["thinking"]["shape"] == "none"
    send(client, chat(client, router["id"], "deepseek/deepseek-r1", "off"), "hello")
    assert answer_requests(mock.app.state.requests)[-1]["reasoning"] == {"effort": "low"}


def test_inline_think_tags_from_a_server_are_split_off(client, mock):
    mock.app.state.models = ["r1-distill"]
    mock.app.state.rules = {"r1-distill": {"style": "inline"}}
    provider = connect(client, kind="openai", base_url=mock.url, api_path="/v1")
    client.patch(f"/api/providers/{provider['id']}", json={"model_settings": {"r1-distill": {"thinking": "toggle"}}})
    msg = final(send(client, chat(client, provider["id"], "r1-distill", "on"), "hello"))
    assert msg["reasoning"] == "Thinking (on) about it." and msg["content"] == "You said: hello"
    # The user's override on an unknown server: the generic chat-template switch.
    assert answer_requests(mock.app.state.requests)[-1]["chat_template_kwargs"] == {"enable_thinking": True}


# ---------------------------------------------------------- Anthropic (mock)
def test_anthropic_native_adapter(client, claude):
    claude.app.state.requests.clear()
    provider = connect(client, kind="anthropic", base_url=claude.url, api_key="sk-ant-test")
    assert "sk-ant-test" not in json.dumps(provider)
    found = models(client, provider["id"])
    assert found["claude-opus-5-5"]["thinking"] == {"shape": "levels", "levels": ["low", "medium", "high", "max"], "default": "medium",
                                                     "source": "detected"}
    assert found["claude-sonnet-5-5"]["thinking"]["levels"][0] == "off"
    assert found["claude-3-haiku-20240307"]["thinking"]["shape"] == "none"
    assert found["claude-opus-5-5"]["context_length"] == 200_000

    # Opus 5.5 can't turn thinking off: "off" is clamped to low effort; the summary shows as reasoning.
    cid = chat(client, provider["id"], "claude-opus-5-5", "off")
    client.patch(f"/api/conversations/{cid}", json={"params": {"temperature": 0.2}})
    msg = final(send(client, cid, "hello"))
    assert msg["status"] == "complete", msg["error"]
    body = answer_requests(claude.app.state.requests)[-1]
    assert body["thinking"] == {"type": "adaptive", "display": "summarized"} and body["output_config"] == {"effort": "low"}
    assert "temperature" not in body and body["max_tokens"] == 32_000
    assert msg["reasoning"].strip() == "Thinking at low." and msg["content"].strip() == "Claude says: hello"
    assert detail(client, cid)["title"] == "Mock Claude Title"
    assert title_request(claude.app.state.requests)["output_config"] == {"effort": "low"}

    for model, level, expect in (
        ("claude-sonnet-5-5", "off", {"type": "between_tools"}),
        ("claude-sonnet-4-6", "off", {"type": "disabled"}),
        ("claude-sonnet-4-6", "max", {"type": "adaptive", "display": "summarized"}),
        ("claude-haiku-4-5", "high", {"type": "enabled", "budget_tokens": 16384}),
    ):
        msg = final(send(client, chat(client, provider["id"], model, level), "hello"))
        assert msg["status"] == "complete", (model, msg["error"])
        assert answer_requests(claude.app.state.requests)[-1]["thinking"] == expect, model
    assert answer_requests(claude.app.state.requests)[-1]["max_tokens"] > 16384
    # A model that doesn't think: nothing is sent, and sampling still works when thinking is off.
    cid = chat(client, provider["id"], "claude-3-haiku-20240307", "high")
    client.patch(f"/api/conversations/{cid}", json={"params": {"temperature": 0.2, "stop": ["END"]}})
    assert final(send(client, cid, "hi"))["status"] == "complete"
    body = answer_requests(claude.app.state.requests)[-1]
    assert "thinking" not in body and body["temperature"] == 0.2 and body["stop_sequences"] == ["END"] and body["max_tokens"] == 4096


def test_anthropic_retries_without_thinking_when_rejected(client, claude):
    provider = connect(client, kind="anthropic", base_url=claude.url, api_key="sk-ant-test")
    claude.app.state.reject_thinking = {"claude-sonnet-4-6"}
    try:
        msg = final(send(client, chat(client, provider["id"], "claude-sonnet-4-6", "high"), "hello"))
    finally:
        claude.app.state.reject_thinking = set()
    assert msg["status"] == "complete" and msg["metrics"]["thinking"]["rejected"] is True
    assert "thinking" not in answer_requests(claude.app.state.requests)[-1]


def test_anthropic_errors_are_readable(client, claude):
    provider = connect(client, kind="anthropic", base_url=claude.url, api_key="wrong")
    result = client.post(f"/api/providers/{provider['id']}/test").json()
    assert result["ok"] is False and result["error_code"] == "auth"


# ------------------------------------------------------------- Ollama (mock)
def test_ollama_think(client, ollama):
    provider = connect(client, kind="ollama", base_url=ollama.url)
    found = models(client, provider["id"])
    assert found["qwen3:8b"]["thinking"]["shape"] == "toggle"
    assert found["gpt-oss:20b"]["thinking"]["levels"] == ["low", "medium", "high"]
    assert found["llama3.2:3b"]["thinking"]["shape"] == "none"
    assert found["llava:7b"]["vision"] is True and found["llama3.2:3b"]["vision"] is False
    reqs = ollama.app.state.requests
    msg = final(send(client, chat(client, provider["id"], "qwen3:8b", "off"), "hello"))
    assert answer_requests(reqs)[-1]["think"] is False and not msg["reasoning"]
    assert title_request(reqs)["think"] is False
    msg = final(send(client, chat(client, provider["id"], "gpt-oss:20b", "off"), "hello"))
    assert answer_requests(reqs)[-1]["think"] == "low" and msg["reasoning"] == "Thinking (low)."
    send(client, chat(client, provider["id"], "llama3.2:3b", "high"), "hello")
    assert "think" not in answer_requests(reqs)[-1]
    # A wrong override: Ollama answers 400, Chimera retries once without "think".
    client.patch(f"/api/providers/{provider['id']}", json={"model_settings": {"llama3.2:3b": {"thinking": "toggle"}}})
    msg = final(send(client, chat(client, provider["id"], "llama3.2:3b", "on"), "hello"))
    assert msg["status"] == "complete" and msg["metrics"]["thinking"]["rejected"] is True
    assert [("think" in b) for b in answer_requests(reqs)[-2:]] == [True, False]


# --------------------------------------------------------------------- API
def test_conversations_store_their_level_and_start_from_the_default(client, provider, mock):
    model = mock.app.state.models[0]
    cid = chat(client, provider["id"], model)
    assert detail(client, cid)["thinking"] == "default"
    client.patch("/api/settings", json={"thinking_default": "high"})
    assert detail(client, chat(client, provider["id"], model))["thinking"] == "high"
    assert client.patch(f"/api/conversations/{cid}", json={"thinking": "xhigh"}).status_code == 422  # a provider value, not a level
    assert client.patch(f"/api/conversations/{cid}", json={"thinking": "low"}).json()["thinking"] == "low"
    assert client.patch(f"/api/conversations/{cid}", json={"thinking": None}).json()["thinking"] == "low"
    # Export and import keep it.
    exported = client.get("/api/data/export").json()
    conv = next(c for c in exported["conversations"] if c["id"] == cid)
    assert conv["thinking"] == "low"
    conv["id"] = "imported-thinking"
    files = {"file": ("e.json", json.dumps({"format": "chimera-export", "conversations": [conv]}), "application/json")}
    assert client.post("/api/data/import", files=files).status_code == 200
    assert detail(client, "imported-thinking")["thinking"] == "low"


def test_model_settings_are_validated_and_only_editors_change_them(client, provider, make_user, mock):
    model = mock.app.state.models[0]
    resp = client.patch(f"/api/providers/{provider['id']}", json={"model_settings": {model: {"thinking": "levels"}, "other": {"thinking": "detect"}}})
    assert resp.status_code == 200 and resp.json()["model_settings"] == {model: {"thinking": "levels"}}
    assert models(client, provider["id"])[model]["thinking"]["source"] == "override"
    assert client.patch(f"/api/providers/{provider['id']}", json={"model_settings": {model: {"thinking": "maybe"}}}).status_code == 422
    assert client.patch(f"/api/providers/{provider['id']}", json={"service": "skynet"}).status_code == 422
    bob = make_user("bob")
    assert bob.patch(f"/api/providers/{provider['id']}", json={"model_settings": {}}).status_code == 404


# ------------------------------------------------------------- migration 6
def test_migration_6(tmp_path):
    path = tmp_path / "chimera.sqlite"
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("CREATE TABLE schema_version (version INTEGER NOT NULL)")
    for script in dbmod.MIGRATIONS[:5]:
        if callable(script):
            script(conn)
        else:
            for statement in dbmod._split_sql(script):
                conn.execute(statement)
    conn.execute("INSERT INTO schema_version(version) VALUES (5)")
    ts = "2026-11-01T10:00:00.000Z"
    conn.execute("INSERT INTO users(id, email, email_norm, username, username_norm, password_hash, created_at, updated_at) "
                 "VALUES ('u1','a@x','a@x','ona','ona','x',?,?)", (ts, ts))
    conn.execute("INSERT INTO providers(id, user_id, name, kind, base_url, created_at, updated_at) VALUES ('p1','u1','P','openai','https://api.openai.com',?,?)",
                 (ts, ts))
    conn.execute("INSERT INTO conversations(id, user_id, title, web_search, created_at, updated_at) VALUES ('c1','u1','T','on',?,?)", (ts, ts))
    conn.commit()
    conn.close()
    db = dbmod.Database(path, backups_dir=tmp_path / "backups")
    assert db.schema_version() == len(dbmod.MIGRATIONS) == 6
    assert db.last_backup and "pre-migration-v5-to-v6" in db.last_backup.name
    assert db.one("SELECT thinking, web_search FROM conversations WHERE id = 'c1'") == {"thinking": "default", "web_search": "on"}
    assert db.one("SELECT service, model_settings FROM providers WHERE id = 'p1'") == {"service": None, "model_settings": "{}"}
