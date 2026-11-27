"""Web reading & search through the chat API, end to end (mock model, mock web)."""

from __future__ import annotations

import json
import threading
import time

import pytest

from chimera.web import pipeline
from tests.conftest import read_sse

ARTICLE = "http://news.example.org/article"


def send(client, cid, content, expect=200, **extra):
    with client.stream("POST", f"/api/conversations/{cid}/messages", json={"content": content, **extra}) as resp:
        if expect != 200:
            resp.read()
            assert resp.status_code == expect, resp.text
            return resp.json()
        assert resp.status_code == 200, resp.read()
        return read_sse(resp)


def final(events):
    return next(d for e, d in events if e in ("done", "error"))["message"]


def detail(client, cid):
    return client.get(f"/api/conversations/{cid}").json()


def chat_requests(mock):
    """Requests that produced an answer (not query generation or titles)."""
    out = []
    for body in mock.app.state.requests:
        text = json.dumps(body["messages"][-1])
        if "Write web search queries" not in text and "Write a short, specific title" not in text and "needs a web search" not in text:
            out.append(body)
    return out


def last_prompt(mock) -> str:
    return "\n".join(m["content"] if isinstance(m["content"], str) else json.dumps(m["content"]) for m in chat_requests(mock)[-1]["messages"])


# ---------------------------------------------------------------- pasted links
def test_pasted_url_is_read_and_answer_uses_it(client, conversation, web, mock):
    events = send(client, conversation["id"], f"Summarize {ARTICLE} please")
    msg = final(events)
    assert msg["status"] == "complete"
    assert msg["content"].startswith("According to [1], Aurora Borealis Festival comes to Vilnius")
    prompt = last_prompt(mock)
    assert "14 March 2026" in prompt and "<web_sources>" in prompt and 'token="' in prompt
    assert "untrusted text copied from the internet" in prompt  # security notice in system prompt

    web_events = [d for e, d in events if e == "web"]
    assert web_events and web_events[-1]["web"]["steps"][0]["kind"] == "read_links"
    step = web_events[-1]["web"]["steps"][0]
    assert step["status"] == "done" and step["params"]["done"] == 1 and step["params"]["domains"] == ["news.example.org"]

    conv = detail(client, conversation["id"])
    user, asst = conv["messages"]
    assert user["content"] == f"Summarize {ARTICLE} please"  # unchanged in the database
    [source] = conv["web_sources"]
    assert source["message_id"] == user["id"] and source["origin"] == "pasted" and source["status"] == "ok"
    assert source["title"] == "Aurora Borealis Festival comes to Vilnius" and source["domain"] == "news.example.org"
    assert "content" not in source  # metadata only in conversation payloads
    assert asst["web"]["refs"] == [{"n": 1, "source_id": source["id"], "included": True, "shown": True}]
    assert asst["web"]["citations"] == {"used": [1], "invalid": []}
    full = client.get(f"/api/web/sources/{source['id']}").json()
    assert "40,000 visitors" in full["content"]


def test_failed_url_is_reported_and_model_is_told(client, conversation, web, mock):
    msg = final(send(client, conversation["id"], "What does http://news.example.org/status/404 say?"))
    assert msg["content"] == "Source [1] could not be read."
    assert "COULD NOT BE READ" in last_prompt(mock)
    [source] = detail(client, conversation["id"])["web_sources"]
    assert source["status"] == "error" and source["error_code"] == "web.not_found"


@pytest.mark.parametrize("url", ["http://127.0.0.1:{port}/article", "http://localhost:{port}/article", "http://169.254.169.254/latest/"])
def test_internal_urls_are_never_fetched(client, conversation, web, url):
    url = url.format(port=web.server.port)
    final(send(client, conversation["id"], f"Read {url}"))
    [source] = detail(client, conversation["id"])["web_sources"]
    assert source["status"] == "error" and source["error_code"] in ("web.blocked_address", "web.blocked_port")
    assert not web.hits


def test_link_selection_and_auto_read_setting(client, conversation, web):
    text = f"Compare {ARTICLE} and http://docs.example.org/docs"
    final(send(client, conversation["id"], text, urls=["http://docs.example.org/docs", "http://not-in-message.example.org/"]))
    sources = detail(client, conversation["id"])["web_sources"]
    assert [s["url"] for s in sources] == ["http://docs.example.org/docs"]
    final(send(client, conversation["id"], f"Again {ARTICLE}", urls=[]))
    assert len(detail(client, conversation["id"])["web_sources"]) == 1
    client.patch("/api/settings", json={"web": {"auto_read_links": False}})
    final(send(client, conversation["id"], f"Once more {ARTICLE}"))
    assert len(detail(client, conversation["id"])["web_sources"]) == 1
    assert ("news.example.org", "/article") not in web.hits


def test_follow_up_reuses_page_without_refetching(client, conversation, web, mock):
    final(send(client, conversation["id"], f"Read {ARTICLE}"))
    msg = final(send(client, conversation["id"], "When is the drone show?"))
    assert web.hits[("news.example.org", "/article")] == 1
    assert "Drone show: 16 March" in last_prompt(mock)
    assert msg["web"]["refs"][0]["n"] == 1


def shown_refs(msg):
    return [r for r in msg["web"]["refs"] if r.get("shown", True)]


def test_uncited_earlier_page_is_sent_but_not_listed(client, conversation, web, mock):
    final(send(client, conversation["id"], f"Read {ARTICLE}"))
    msg = final(send(client, conversation["id"], "Test"))
    assert "Aurora Borealis Festival" in last_prompt(mock)  # still sent to the model for follow-ups
    [ref] = msg["web"]["refs"]
    assert ref["earlier"] is True and ref["shown"] is False
    assert shown_refs(msg) == []  # no Sources block under "Test"
    assert msg["web"]["citations"] == {"used": [], "invalid": []}


def test_cited_earlier_page_is_listed_with_a_stable_number(client, conversation, web, mock):
    final(send(client, conversation["id"], f"Read {ARTICLE}"))
    msg = final(send(client, conversation["id"], "And the dates? [cite-earlier]"))
    assert msg["content"].startswith("According to [1]")
    [ref] = shown_refs(msg)
    assert ref["n"] == 1 and ref["earlier"] is True and ref["included"] is True
    assert msg["web"]["citations"]["used"] == [1]
    source = next(s for s in detail(client, conversation["id"])["web_sources"] if s["id"] == ref["source_id"])
    assert source["url"] == ARTICLE


def test_this_turns_sources_are_always_listed_including_failures(client, conversation, web, mock):
    final(send(client, conversation["id"], f"Read {ARTICLE}"))
    msg = final(send(client, conversation["id"], "Now http://docs.example.org/docs and http://news.example.org/status/404"))
    shown = shown_refs(msg)
    by_id = {s["id"]: s for s in detail(client, conversation["id"])["web_sources"]}
    urls = {by_id[r["source_id"]]["url"]: r for r in shown}
    assert set(urls) == {"http://docs.example.org/docs", "http://news.example.org/status/404"}
    assert urls["http://news.example.org/status/404"]["included"] is False
    assert by_id[urls["http://news.example.org/status/404"]["source_id"]]["error_code"] == "web.not_found"
    # The earlier article was sent (and cited by the mock) but belongs to this answer only if cited.
    earlier = [r for r in msg["web"]["refs"] if r.get("earlier")]
    assert all(r["shown"] == (r["n"] in msg["web"]["citations"]["used"]) for r in earlier)


def test_refetch_updates_source(client, conversation, web):
    final(send(client, conversation["id"], f"Read {ARTICLE}"))
    [source] = detail(client, conversation["id"])["web_sources"]
    resp = client.post(f"/api/web/sources/{source['id']}/refetch")
    assert resp.status_code == 200 and resp.json()["ok"] is True
    assert web.hits[("news.example.org", "/article")] == 2  # bypasses the cache


def test_github_rewriter_and_fallback(client, conversation, web):
    final(send(client, conversation["id"], "Explain https://github.com/owner/repo and https://github.com/owner/missing"))
    repo, missing = detail(client, conversation["id"])["web_sources"]
    assert repo["status"] == "ok" and repo["final_url"] == "https://github.com/owner/repo"
    assert "caching toolkit" in client.get(f"/api/web/sources/{repo['id']}").json()["content"]
    assert missing["status"] == "ok"  # README missing: the repository page itself was read
    assert web.hits[("raw.githubusercontent.com", "/owner/repo/HEAD/README.md")] == 1


def test_edit_reads_the_new_links(client, conversation, web):
    final(send(client, conversation["id"], f"Read {ARTICLE}"))
    user = detail(client, conversation["id"])["messages"][0]
    with client.stream("POST", f"/api/conversations/{conversation['id']}/messages/{user['id']}/edit",
                       json={"content": "Read http://docs.example.org/docs instead"}) as resp:
        read_sse(resp)
    conv = detail(client, conversation["id"])
    edited = conv["active_path"][0]
    sources = {s["message_id"]: s["url"] for s in conv["web_sources"]}
    # The edit is a new branch: it reads its own links; the original keeps its page.
    assert sources == {edited: "http://docs.example.org/docs", user["id"]: ARTICLE}


# --------------------------------------------------------------------- search
def test_search_flow_steps_sources_citations_and_reload(client, conversation, web, mock):
    events = send(client, conversation["id"], "What is happening in Vilnius in March?", web_search=True)
    msg = final(events)
    assert msg["status"] == "complete", msg
    kinds = [s["kind"] for s in msg["web"]["steps"]]
    assert kinds == ["queries", "search", "read_results"]
    assert all(s["status"] == "done" for s in msg["web"]["steps"])
    queries = msg["web"]["queries"]
    assert queries == ["What is happening in Vilnius in", "What is happening in Vilnius in latest"]
    assert web.server.app.state.searches[-2:] == queries or set(web.server.app.state.searches[-2:]) == set(queries)
    read = msg["web"]["steps"][2]["params"]
    assert read["total"] == 4 and read["done"] == 4 and read["failed"] == 1  # the 404 falls back to its snippet

    conv = detail(client, conversation["id"])
    sources = conv["web_sources"]
    assert [s["origin"] for s in sources] == ["search"] * 4
    assert [s["rank"] for s in sources] == [1, 2, 3, 4]
    assert sources[0]["url"] == "http://news.example.org/article?id=1"  # tracking parameter removed
    gone = sources[2]
    assert gone["status"] == "snippet" and gone["error_code"] == "web.not_found" and gone["snippet"].startswith("Snippet text")
    assert [r["n"] for r in msg["web"]["refs"]] == [1, 2, 3, 4]
    assert "[1]" in msg["content"] and msg["web"]["citations"]["invalid"] == []
    # Reload shows exactly what was shown live.
    assert conv["messages"][1]["web"] == msg["web"]
    live = [d for e, d in events if e == "web"][-1]
    assert {s["id"] for s in live["sources"]} == {s["id"] for s in sources}
    prompt = last_prompt(mock)
    assert "only the search engine's short summary" in prompt and "Today is" in prompt


def test_query_generation_fallbacks(client, conversation, web, monkeypatch):
    msg = final(send(client, conversation["id"], "[badjson] vilnius festival", web_search=True))
    step = msg["web"]["steps"][0]
    assert step["params"]["fallback"] is True and msg["web"]["queries"] == ["[badjson] vilnius festival"]
    monkeypatch.setattr(pipeline, "QUERY_TIMEOUT", 0.3)
    msg = final(send(client, conversation["id"], "[slowquery] vilnius festival", web_search=True))
    assert msg["status"] == "complete" and msg["web"]["steps"][0]["params"]["fallback"] is True
    client.patch("/api/admin/web", json={"settings": {"query_generation": False}})
    msg = final(send(client, conversation["id"], "drone show dates", web_search=True))
    assert msg["web"]["queries"] == ["drone show dates"]


@pytest.mark.parametrize(("query", "code"), [
    ("explode now", "web.engine_error"), ("noresults here", "web.no_results"), ("allfail please", "web.all_fetches_failed"),
])
def test_search_failures_stop_before_the_model_and_can_be_answered_without_search(client, conversation, web, mock, query, code):
    client.patch("/api/admin/web", json={"settings": {"query_generation": False}})
    before = len(chat_requests(mock))
    msg = final(send(client, conversation["id"], query, web_search=True))
    assert msg["status"] == "error" and msg["error_code"] == code
    assert len(chat_requests(mock)) == before  # the model was not asked to answer without sources
    assert msg["web"]["steps"][-1]["status"] == "error"
    with client.stream("POST", f"/api/conversations/{conversation['id']}/regenerate", json={"web_search": False}) as resp:
        again = final(read_sse(resp))
    assert again["status"] == "complete" and again["content"] == f"You said: {query}"


def test_searxng_json_disabled_is_explained(client, conversation, web):
    web.server.app.state.json_disabled = True
    msg = final(send(client, conversation["id"], "anything", web_search=True))
    assert msg["error_code"] == "web.searxng_json_disabled"


def test_regenerate_follows_the_conversation_and_flags_invalid_citations(client, conversation, web):
    client.patch(f"/api/conversations/{conversation['id']}", json={"web_search": "on"})
    msg = final(send(client, conversation["id"], "festival [badcite]"))
    assert msg["web"]["citations"]["invalid"] == [42]
    count = len(web.server.app.state.searches)
    with client.stream("POST", f"/api/conversations/{conversation['id']}/regenerate", json={}) as resp:
        again = final(read_sse(resp))
    assert again["web"]["search"] is True and len(web.server.app.state.searches) > count


def test_context_budget_drops_lowest_ranked_sources(client, conversation, web, provider):
    client.app.state.chimera.db.execute("UPDATE providers SET capabilities = ? WHERE id = ?", (json.dumps({"n_ctx": 1300}), provider["id"]))
    client.patch("/api/admin/web", json={"settings": {"per_source_chars": 1000}})
    msg = final(send(client, conversation["id"], "vilnius festival", web_search=True))
    budget = [s for s in msg["web"]["steps"] if s["kind"] == "budget"]
    assert budget and budget[0]["params"]["used"] < budget[0]["params"]["total"] == 4
    assert msg["web"]["context_tokens"] == 1300
    included = [r for r in msg["web"]["refs"] if r["included"]]
    assert len(included) == budget[0]["params"]["used"]
    # Results that were read but didn't fit are still listed under the answer
    # (as left out), numbered after everything the model saw.
    assert len(msg["web"]["refs"]) == 4 and all(r["shown"] for r in msg["web"]["refs"])
    left_out = [r for r in msg["web"]["refs"] if not r["included"]]
    assert min(r["n"] for r in left_out) > max(r["n"] for r in included)
    assert all(f"[{r['n']}]" not in msg["content"] for r in left_out)


# ------------------------------------------------------------- cancellation
def run_and_stop(client, cid, body, ready):
    box = {}

    def consume():
        with client.stream("POST", f"/api/conversations/{cid}/messages", json=body) as resp:
            box["events"] = read_sse(resp)

    thread = threading.Thread(target=consume)
    thread.start()
    deadline = time.time() + 10
    gen_id = None
    while time.time() < deadline:
        conv = detail(client, cid)
        gen_id = conv["generation_id"]
        if gen_id and conv["messages"] and ready(conv["messages"][-1]):
            break
        time.sleep(0.05)
    assert gen_id
    assert client.post(f"/api/generations/{gen_id}/cancel").json() == {"cancelled": True}
    thread.join(timeout=10)
    return final(box["events"])


def _running(kind):
    return lambda m: any(s["kind"] == kind and s["status"] == "running" for s in (m.get("web") or {}).get("steps", []))


@pytest.mark.parametrize(("content", "search", "stage"), [
    ("Read http://news.example.org/slow", False, "read_links"),
    ("[slowquery] festival", True, "queries"),
    ("[slowsearch] festival", True, "search"),
])
def test_stop_during_each_web_stage(client, conversation, web, content, search, stage):
    if stage == "search":
        client.patch("/api/admin/web", json={"settings": {"query_generation": False}})
    msg = run_and_stop(client, conversation["id"], {"content": content, "web_search": search}, _running(stage))
    assert msg["status"] == "cancelled"
    assert all(s["status"] != "running" for s in msg["web"]["steps"])
    assert any(s["kind"] == stage and s["status"] == "cancelled" for s in msg["web"]["steps"])
    assert detail(client, conversation["id"])["generation_id"] is None
    assert final(send(client, conversation["id"], "after stop"))["status"] == "complete"


def test_stop_during_generation_after_search(client, conversation, web, mock):
    msg = run_and_stop(client, conversation["id"], {"content": "festival [slow]", "web_search": True},
                       lambda m: m["content"].startswith("word"))
    assert msg["status"] == "cancelled" and all(s["status"] == "done" for s in msg["web"]["steps"])
    assert len(detail(client, conversation["id"])["web_sources"]) == 4  # sources kept with the partial answer


# -------------------------------------------------------- admin and limits
def test_admin_switch_is_enforced_server_side(client, conversation, web):
    final(send(client, conversation["id"], f"Read {ARTICLE}"))
    [source] = detail(client, conversation["id"])["web_sources"]
    client.patch("/api/admin/web", json={"settings": {"enabled": False}})
    assert client.get("/api/web/status").json()["enabled"] is False
    assert send(client, conversation["id"], "x", expect=403, web_search=True)["detail"]["code"] == "web.disabled"
    assert send(client, conversation["id"], f"y {ARTICLE}", expect=403, urls=[ARTICLE])["detail"]["code"] == "web.disabled"
    msg = final(send(client, conversation["id"], f"Plain {ARTICLE}"))  # links are simply not read
    assert msg["status"] == "complete" and web.hits[("news.example.org", "/article")] == 1
    assert client.post(f"/api/web/sources/{source['id']}/refetch").json()["detail"]["code"] == "web.disabled"


def test_search_requires_a_configured_engine(client, conversation, web):
    client.patch("/api/admin/web", json={"settings": {"engine": None}})
    status = client.get("/api/web/status").json()
    assert status["enabled"] is True and status["search_available"] is False
    assert send(client, conversation["id"], "x", expect=409, web_search=True)["detail"]["code"] == "web.search_unavailable"


def test_hourly_quotas(client, conversation, web):
    client.patch("/api/admin/web", json={"settings": {"fetches_per_hour": 1, "searches_per_hour": 1, "query_generation": False}})
    final(send(client, conversation["id"], f"Read {ARTICLE} and http://docs.example.org/docs"))
    statuses = sorted(s["error_code"] or "ok" for s in detail(client, conversation["id"])["web_sources"])
    assert statuses == ["ok", "web.rate_limited"]
    final(send(client, conversation["id"], "festival", web_search=True))  # the one search
    msg = final(send(client, conversation["id"], "festival", web_search=True))
    assert msg["error_code"] == "web.rate_limited"


def test_robots_applies_to_search_results_not_pasted_links(client, conversation, web):
    state = web.state
    user = client.get("/api/auth/me").json()
    settings = state.web.admin()
    data = client.portal.call(lambda: state.web.read(user, "http://news.example.org/private/page", settings, robots=True))
    assert data.status == "error" and data.error_code == "web.robots_disallowed"
    final(send(client, conversation["id"], "Read http://news.example.org/private/page"))
    [source] = detail(client, conversation["id"])["web_sources"]
    assert source["status"] == "ok"


def test_status_defaults_without_configuration(client):
    status = client.get("/api/web/status").json()
    assert status["enabled"] is False and status["search_available"] is False and status["engine"] is None


def test_admin_settings_and_write_only_keys(client, make_user, web):
    bob = make_user()
    assert bob.get("/api/admin/web").status_code == 404
    assert bob.patch("/api/admin/web", json={"settings": {"enabled": False}}).status_code == 404
    assert bob.post("/api/admin/web/test", json={"kind": "searxng"}).status_code == 404
    view = client.patch("/api/admin/web", json={"engine_keys": {"brave": "brave-key"}}).json()
    assert next(e for e in view["engines"] if e["kind"] == "brave")["has_key"] is True
    assert "brave-key" not in json.dumps(view)
    stored = client.app.state.chimera.db.one("SELECT value FROM app_settings WHERE key = 'web_key:brave'")["value"]
    assert stored.startswith('"enc:v1:') and "brave-key" not in stored
    bad = client.patch("/api/admin/web", json={"settings": {"max_results": 99}})
    assert bad.status_code == 422 and bad.json()["detail"]["code"] == "validation_error"
    removed = client.patch("/api/admin/web", json={"engine_keys": {"brave": None}}).json()
    assert next(e for e in removed["engines"] if e["kind"] == "brave")["has_key"] is False


def test_engine_test_endpoint(client, web):
    report = client.post("/api/admin/web/test", json={"kind": "searxng"}).json()
    assert report["ok"] is True and report["result_count"] == 5 and report["latency_ms"] is not None
    view = client.get("/api/admin/web").json()
    assert next(e for e in view["engines"] if e["kind"] == "searxng")["status"]["ok"] is True
    bad = client.post("/api/admin/web/test", json={"kind": "brave", "api_key": "nope"}).json()
    assert bad["ok"] is False and bad["error_code"] == "web.engine_auth"
    good = client.post("/api/admin/web/test", json={"kind": "tavily", "api_key": "tavily-key"}).json()
    assert good["ok"] is True
    unreachable = client.post("/api/admin/web/test", json={"kind": "searxng", "searxng_url": "http://127.0.0.1:1"}).json()
    assert unreachable["ok"] is False and unreachable["error_code"] == "web.engine_unreachable"


# ---------------------------------------------------------------- isolation
@pytest.fixture()
def shared(client, mock):
    resp = client.post("/api/providers", json={"name": "Shared", "kind": "llamacpp", "base_url": mock.url, "shared": True})
    assert resp.status_code == 201, resp.text
    return resp.json()


def test_sources_and_cache_are_per_user(client, conversation, web, make_user, mock, shared):
    final(send(client, conversation["id"], f"Read {ARTICLE}"))
    [source] = detail(client, conversation["id"])["web_sources"]
    bob = make_user()
    assert bob.get(f"/api/web/sources/{source['id']}").status_code == 404
    assert bob.post(f"/api/web/sources/{source['id']}/refetch").status_code == 404
    assert bob.get(f"/api/conversations/{conversation['id']}").status_code == 404
    # Bob reading the same page fetches it himself (no shared cache)...
    conv = bob.post("/api/conversations", json={"provider_id": shared["id"], "model": mock.app.state.models[0]}).json()
    with bob.stream("POST", f"/api/conversations/{conv['id']}/messages", json={"content": f"Read {ARTICLE}"}) as resp:
        read_sse(resp)
    assert web.hits[("news.example.org", "/article")] == 2
    # ...while Alice's second read within the cache lifetime does not.
    final(send(client, conversation["id"], f"Again {ARTICLE}"))
    assert web.hits[("news.example.org", "/article")] == 2
    assert [s["id"] for s in bob.get(f"/api/conversations/{conv['id']}").json()["web_sources"]] != [source["id"]]


def test_csrf_required_for_refetch(client, conversation, web):
    final(send(client, conversation["id"], f"Read {ARTICLE}"))
    [source] = detail(client, conversation["id"])["web_sources"]
    client.send_csrf = False
    try:
        assert client.post(f"/api/web/sources/{source['id']}/refetch").status_code == 403
    finally:
        client.send_csrf = True


def test_export_includes_web_content_and_account_deletion_removes_it(client, web, make_user, mock, shared):
    bob = make_user()
    conv = bob.post("/api/conversations", json={"provider_id": shared["id"], "model": mock.app.state.models[0]}).json()
    with bob.stream("POST", f"/api/conversations/{conv['id']}/messages", json={"content": f"Read {ARTICLE}"}) as resp:
        read_sse(resp)
    export = json.loads(bob.get("/api/data/export").content)
    [source] = export["conversations"][0]["web_sources"]
    assert "40,000 visitors" in source["content"]
    db = client.app.state.chimera.db
    bob_id = bob.get("/api/auth/me").json()["id"]
    assert db.one("SELECT COUNT(*) AS n FROM web_sources WHERE user_id = ?", (bob_id,))["n"] == 1
    from tests.conftest import PASSWORD

    assert bob.request("DELETE", "/api/account", json={"password": PASSWORD}).status_code == 204
    assert db.one("SELECT COUNT(*) AS n FROM web_sources WHERE user_id = ?", (bob_id,))["n"] == 0
    assert bob_id not in client.app.state.chimera.web._cache


def test_export_keeps_source_visibility_and_import_still_works(client, conversation, web):
    final(send(client, conversation["id"], f"Read {ARTICLE}"))
    final(send(client, conversation["id"], "Test"))
    export = json.loads(client.get("/api/data/export").content)
    [conv] = [c for c in export["conversations"] if c["id"] == conversation["id"]]
    refs = [m["web"]["refs"] for m in conv["messages"] if m["role"] == "assistant"]
    assert refs[0][0]["shown"] is True and refs[1][0] == {**refs[1][0], "earlier": True, "shown": False}
    assert client.delete(f"/api/conversations/{conversation['id']}").status_code in (200, 204)
    resp = client.post("/api/data/import", files={"file": ("export.json", json.dumps(export), "application/json")})
    assert resp.status_code == 200 and resp.json()["conversations"] == 1
    restored = [c for c in client.get("/api/conversations").json() if c["title"] == conv["title"]]
    assert len(detail(client, restored[0]["id"])["messages"]) == 4


# ------------------------------------------------- agentic tool interface
def test_tool_executor_validates_and_uses_the_same_guards(client, web):
    from chimera.web.tools import TOOL_DEFINITIONS, ToolExecutor

    assert {t["function"]["name"] for t in TOOL_DEFINITIONS} == {"web_search", "fetch_url"}
    state = web.state
    user = client.get("/api/auth/me").json()
    executor = ToolExecutor(state.web, user, state.web.admin(), max_calls=6)
    call = lambda name, args: client.portal.call(lambda: executor.execute(name, args))  # noqa: E731
    ok = call("fetch_url", '{"url": "http://news.example.org/article"}')
    assert ok.ok and "14 March 2026" in ok.content
    blocked = call("fetch_url", {"url": "http://169.254.169.254/latest/meta-data"})
    assert not blocked.ok and blocked.error_code == "web.blocked_address"
    assert call("fetch_url", '{"url": "x", "headers": {"Cookie": "a"}}').error_code == "web.tool_invalid"
    assert call("delete_everything", "{}").error_code == "web.tool_unknown"
    found = call("web_search", '{"query": "festival"}')
    assert found.ok and "news.example.org" in found.content
    assert call("web_search", "not json").error_code == "web.tool_invalid"
    assert call("web_search", '{"query": "again"}').error_code == "web.tool_limit"


def test_unexpected_extraction_failure_is_contained(client, conversation, web, monkeypatch):
    from chimera.web import service

    def boom(*args, **kwargs):
        raise RuntimeError("parser crashed")

    monkeypatch.setattr(service, "extract_page", boom)
    msg = final(send(client, conversation["id"], f"Read {ARTICLE}"))
    assert msg["status"] == "complete" and msg["content"] == "Source [1] could not be read."
    [source] = detail(client, conversation["id"])["web_sources"]
    assert source["error_code"] == "web.read_failed"
