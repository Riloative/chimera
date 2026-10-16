"""Automatic context management against the mock llama-server
(``/props`` per-slot n_ctx, ``/tokenize``, ``/apply-template``, overflow errors)."""

from __future__ import annotations

import pytest

from tests.mock_llama_server import mock_tokens, render_template

from .conftest import active_messages, read_sse


def send(client, cid, content, **extra):
    with client.stream("POST", f"/api/conversations/{cid}/messages", json={"content": content, **extra}) as resp:
        assert resp.status_code == 200, resp.read()
        events = read_sse(resp)
    return next(d for e, d in events if e in ("done", "error"))["message"], events


def estimate(client, cid):
    resp = client.get(f"/api/conversations/{cid}/context")
    assert resp.status_code == 200, resp.text
    return resp.json()


def detect(client, provider, mock, n_ctx=8192, slots=1):
    mock.app.state.n_ctx, mock.app.state.slots = n_ctx, slots
    assert client.post(f"/api/providers/{provider['id']}/test").json()["ok"]


def chat_requests(mock):
    return [r for r in mock.app.state.requests
            if not any(k in str(r["messages"][-1]["content"]) for k in ("Write a short, specific title", "Summarize the earlier part"))]


def words(n: int, tag: str) -> str:
    return " ".join(f"{tag}{i}" for i in range(n))


@pytest.fixture(autouse=True)
def no_titles(client):
    from chimera.providers import openai_compat
    from chimera.services import context

    # Process-wide caches (tokens, template overhead, unsupported endpoints) start empty.
    context._cache.clear()
    context._overheads.clear()
    openai_compat._UNSUPPORTED.clear()
    client.patch("/api/settings", json={"auto_title": False})


# ---------------------------------------------------------------- discovery
def test_window_comes_from_props_per_slot_and_can_be_overridden(client, conversation, provider, mock):
    detect(client, provider, mock, n_ctx=8192, slots=4)
    est = estimate(client, conversation["id"])
    assert est["window"] == 2048 and est["window_source"] == "server" and est["window_slots"] == 4
    client.patch(f"/api/providers/{provider['id']}", json={"context_override": 3000})
    est = estimate(client, conversation["id"])
    assert est["window"] == 3000 and est["window_source"] == "manual"
    assert client.get("/api/providers").json()[0]["context_override"] == 3000


def test_unknown_window_uses_the_configured_fallback(client, mock):
    prov = client.post("/api/providers", json={"name": "Generic", "kind": "openai", "base_url": mock.url}).json()
    conv = client.post("/api/conversations", json={"provider_id": prov["id"], "model": mock.app.state.models[0]}).json()
    est = estimate(client, conv["id"])
    assert est["window"] == 4096 and est["window_source"] == "default"
    client.patch("/api/settings", json={"context": {"fallback_window": 12000}})
    assert estimate(client, conv["id"])["window"] == 12000


# ----------------------------------------------------------------- counting
def test_exact_counts_via_tokenize_are_cached(client, conversation, provider, mock):
    detect(client, provider, mock)
    send(client, conversation["id"], "hello there, how are you?")
    est = estimate(client, conversation["id"])
    assert est["exact"] is True
    calls = mock.app.state.tokenize_calls
    estimate(client, conversation["id"])
    assert mock.app.state.tokenize_calls == calls  # nothing re-tokenized


def test_estimates_when_the_server_cannot_tokenize(client, conversation, provider, mock):
    detect(client, provider, mock)
    mock.app.state.tokenize_supported = False
    send(client, conversation["id"], "hello there")
    est = estimate(client, conversation["id"])
    assert est["exact"] is False and est["used"] > 0


# ------------------------------------------------------------------- budget
def fill(client, cid, turns=8, size=40):
    for i in range(turns):
        send(client, cid, words(size, f"t{i}w"))


def tight(client, cid, provider, mock, share=1.0):
    """Shrink the reported window so the current history no longer fits
    (available ≈ 70 % of the window after the answer reserve and margin)."""
    used = estimate(client, cid)["used"]
    n = int(used * share)
    detect(client, provider, mock, n_ctx=n)
    return n


def test_old_exchanges_are_dropped_whole_and_pinned_ones_kept(client, conversation, provider, mock):
    cid = conversation["id"]
    detect(client, provider, mock)
    client.patch(f"/api/conversations/{cid}", json={"system_prompt": "Be brief."})
    fill(client, cid, turns=6)
    path = detail_path(client, cid)
    client.patch(f"/api/messages/{path[0]['id']}", json={"pinned": True})  # pin the first question
    detect(client, provider, mock, n_ctx=900)
    msg, _ = send(client, cid, "final question")
    sent = chat_requests(mock)[-1]["messages"]
    roles = [m["role"] for m in sent]
    assert roles[0] == "system" and sent[0]["content"].startswith("Be brief.")
    assert sent[-1]["content"] == "final question"
    assert sent[1]["content"] == path[0]["content"] and sent[2]["content"] == path[1]["content"]  # pinned exchange kept
    # never half an exchange: every assistant message follows its user message
    for i, role in enumerate(roles[1:], start=1):
        if role == "assistant":
            assert roles[i - 1] == "user"
    ctx = msg["context"]
    assert ctx["dropped"] > 0 and ctx["dropped"] % 2 == 0 and ctx["breakdown"]["pinned"] > 0
    assert ctx["exact"] is True and ctx["used"] <= 900 - ctx["breakdown"]["reserved"]
    assert ctx["cut_before"] in {m["id"] for m in detail_path(client, cid)}
    assert mock.app.state.prompt_sizes[-1] < 900


def detail_path(client, cid):
    return active_messages(client.get(f"/api/conversations/{cid}").json())


def test_too_large_message_is_not_sent(client, conversation, provider, mock):
    detect(client, provider, mock, n_ctx=600)
    before = len(chat_requests(mock))
    msg, _ = send(client, conversation["id"], words(800, "x"))
    assert msg["status"] == "error" and msg["error_code"] == "context.too_large"
    assert msg["error_params"]["needed"] > msg["error_params"]["available"]
    assert len(chat_requests(mock)) == before  # nothing reached the model


def test_hysteresis_keeps_the_prompt_prefix_stable(client, conversation, provider, mock):
    cid = conversation["id"]
    detect(client, provider, mock)
    fill(client, cid, turns=10, size=30)
    tight(client, cid, provider, mock)
    prefixes = []
    for i in range(3):
        msg, _ = send(client, cid, f"follow-up {i}")
        sent = chat_requests(mock)[-1]["messages"]
        prefixes.append((msg["context"]["cut_before"], [m["content"] for m in sent[:4]]))
    # After the cut, the next turns keep the same first messages (llama-server can reuse its cache).
    assert prefixes[0] == prefixes[1] == prefixes[2]
    # …until the window overflows again, then the cut moves forward by a larger block.
    for i in range(12):
        msg, _ = send(client, cid, words(30, f"more{i}"))
    assert msg["context"]["cut_before"] != prefixes[0][0]


def test_summary_is_created_reused_and_invalidated(client, conversation, provider, mock):
    cid = conversation["id"]
    detect(client, provider, mock)
    fill(client, cid, turns=8, size=30)
    client.patch(f"/api/conversations/{cid}", json={"context_strategy": "summarize"})
    tight(client, cid, provider, mock)
    msg, _ = send(client, cid, "and now?")
    assert mock.app.state.summaries == 1
    system = chat_requests(mock)[-1]["messages"][0]["content"]
    assert "SUMMARY#1" in system and msg["context"]["summary"].startswith("SUMMARY#1")
    msg, _ = send(client, cid, "and then?")
    assert mock.app.state.summaries == 1  # same cut → cached summary reused
    assert "SUMMARY#1" in chat_requests(mock)[-1]["messages"][0]["content"]
    # Another branch (edit an early message): its dropped part differs → new summary.
    first = detail_path(client, cid)[2]
    with client.stream("POST", f"/api/conversations/{cid}/messages/{first['id']}/edit", json={"content": words(30, "edited")}) as r:
        read_sse(r)
    for i in range(7):
        send(client, cid, words(30, f"b{i}"))
    assert mock.app.state.summaries >= 2


def test_summary_failure_falls_back_to_sliding_window(client, conversation, provider, mock):
    cid = conversation["id"]
    detect(client, provider, mock)
    fill(client, cid, turns=8, size=30)
    client.patch(f"/api/conversations/{cid}", json={"context_strategy": "summarize"})
    tight(client, cid, provider, mock)
    mock.app.state.summary_fail = True
    msg, _ = send(client, cid, "still works?")
    assert msg["status"] == "complete"
    assert msg["context"]["summary_failed"] is True and msg["context"]["summary"] is None and msg["context"]["dropped"] > 0


def test_server_overflow_triggers_exactly_one_tighter_retry(client, conversation, provider, mock):
    cid = conversation["id"]
    detect(client, provider, mock)
    fill(client, cid, turns=8, size=30)
    mock.app.state.enforce_ctx = 500  # the server is smaller than it reported
    before = len(chat_requests(mock))
    msg, _ = send(client, cid, "retry me")
    assert msg["status"] == "complete" and msg["context"]["retried"] is True
    assert len(chat_requests(mock)) - before == 2
    # Too small even for the latest message: the retry, re-measured against the
    # size the server reported in its error, is caught before sending.
    mock.app.state.enforce_ctx = 40
    before = len(chat_requests(mock))
    msg, _ = send(client, cid, words(30, "big"))
    assert msg["status"] == "error" and msg["error_code"] == "context.too_large"
    assert len(chat_requests(mock)) - before == 1
    # When the server keeps rejecting even the tighter request: exactly one retry, then its error.
    mock.app.state.enforce_ctx = None
    detect(client, provider, mock)
    mock.app.state.enforce_ctx = 300
    mock.app.state.n_ctx_error = 8192  # the error claims the big size, so the retry is sent and rejected again
    before = len(chat_requests(mock))
    msg, _ = send(client, cid, "again " + words(200, "huge"))
    assert msg["status"] == "error" and msg["error_code"] == "context_exceeded"
    assert len(chat_requests(mock)) - before == 2


def test_off_sends_everything_and_shows_the_server_error(client, conversation, provider, mock):
    cid = conversation["id"]
    detect(client, provider, mock)
    fill(client, cid, turns=10, size=40)
    client.patch(f"/api/conversations/{cid}", json={"context_strategy": "off"})
    detect(client, provider, mock, n_ctx=600)
    before = len(chat_requests(mock))
    msg, _ = send(client, cid, "everything please")
    assert msg["status"] == "error" and msg["error_code"] == "context_exceeded"
    assert msg["error_params"]["n_ctx"] == 600
    assert len(chat_requests(mock)) - before == 1  # no retry with "off"
    assert len(chat_requests(mock)[-1]["messages"]) == 21  # the full branch was sent


def test_branch_and_regenerate_use_the_same_context_rules(client, conversation, provider, mock):
    cid = conversation["id"]
    detect(client, provider, mock)
    fill(client, cid, turns=6, size=30)
    n = tight(client, cid, provider, mock)
    path = detail_path(client, cid)
    with client.stream("POST", f"/api/conversations/{cid}/regenerate", json={"message_id": path[-1]["id"]}) as r:
        msg = next(d for e, d in read_sse(r) if e == "done")["message"]
    assert msg["context"]["dropped"] > 0 and mock.app.state.prompt_sizes[-1] < n


def test_unified_budget_with_web_material(client, conversation, provider, mock, web):
    cid = conversation["id"]
    detect(client, provider, mock)
    fill(client, cid, turns=6, size=30)
    detect(client, provider, mock, n_ctx=1500)
    msg, _ = send(client, cid, "Summarize http://news.example.org/article")
    sent = chat_requests(mock)[-1]["messages"]
    assert "14 March 2026" in sent[-1]["content"]  # the page (current message) wins
    assert msg["context"]["dropped"] > 0  # …history was trimmed instead
    kinds = [s["kind"] for s in msg["web"]["steps"]]
    assert "history_trimmed" in kinds


def test_mock_tokenizer_matches_template_rendering():
    assert mock_tokens(render_template([{"role": "user", "content": "a b"}])) > mock_tokens("a b")
