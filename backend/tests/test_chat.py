import threading
import time

from chimera.main import create_app

from .conftest import CsrfClient, active_messages, login, read_sse, register

SLOW_WORDS = 150


def send(client, conv_id, content, **extra):
    with client.stream("POST", f"/api/conversations/{conv_id}/messages", json={"content": content, **extra}) as resp:
        assert resp.status_code == 200, resp.read()
        return read_sse(resp)


def names(events):
    return [e for e, _ in events]


def test_send_streams_and_persists(client, conversation):
    events = send(client, conversation["id"], "Hello there")
    assert names(events)[0] == "start"
    assert "done" in names(events) and names(events)[-1] == "end"
    text = "".join(d.get("content", "") for e, d in events if e == "delta")
    assert text == "You said: Hello there"
    done = next(d for e, d in events if e == "done")
    assert done["message"]["status"] == "complete"
    assert done["message"]["metrics"]["tokens_per_second"] == 42.3
    assert done["message"]["metrics"]["finish_reason"] == "stop"

    conv = client.get(f"/api/conversations/{conversation['id']}").json()
    assert [m["role"] for m in conv["messages"]] == ["user", "assistant"]
    assert conv["messages"][1]["content"] == "You said: Hello there"
    assert conv["messages"][1]["model"] == conversation["model"]
    assert conv["generation_id"] is None


def test_title_is_generated_by_model(client, conversation):
    events = send(client, conversation["id"], "How do I reverse a list in Python?")
    assert ("title", {"conversation_id": conversation["id"], "title": "Mock Conversation Title"}) in events
    assert client.get(f"/api/conversations/{conversation['id']}").json()["title"] == "Mock Conversation Title"


def test_start_event_carries_the_title_the_conversation_now_has(client, conversation):
    """The first message replaces "New conversation" with a fallback title at
    once; clients learn it from the start event, not only after a reload."""
    events = send(client, conversation["id"], "Testas-testas?")
    start = next(d for e, d in events if e == "start")
    assert start["conversation"] == {"id": conversation["id"], "title": "Testas-testas?", "title_source": "fallback"}
    # A later message reports the current (model) title, and a user's own title is never changed.
    start2 = next(d for e, d in send(client, conversation["id"], "more") if e == "start")
    assert start2["conversation"]["title"] == "Mock Conversation Title" and start2["conversation"]["title_source"] == "model"


def test_reasoning_models_still_get_a_title(client, conversation, mock):
    """Reasoning models (e.g. Qwen3) spent the whole title budget thinking, so
    no title was ever generated. Titles now ask the template not to think and
    leave room for an answer."""
    mock.app.state.think_titles = True
    events = send(client, conversation["id"], "How do I reverse a list in Python?")
    assert ("title", {"conversation_id": conversation["id"], "title": "Mock Conversation Title"}) in events
    title_req = next(r for r in mock.app.state.requests if "Write a short, specific title" in str(r["messages"][-1]["content"]))
    assert title_req["chat_template_kwargs"] == {"enable_thinking": False}


def test_title_comes_before_end_and_is_replayed_on_reattach(client, conversation):
    events = send(client, conversation["id"], "order please")
    order = names(events)
    assert order.index("done") < order.index("title") < order.index("end")


def test_fallback_title_when_disabled(client, conversation):
    client.patch("/api/settings", json={"auto_title": False})
    send(client, conversation["id"], "Explain the borrow checker in Rust please")
    conv = client.get(f"/api/conversations/{conversation['id']}").json()
    assert conv["title"] == "Explain the borrow checker in Rust please"


def test_user_renamed_title_is_kept(client, conversation):
    client.patch(f"/api/conversations/{conversation['id']}", json={"title": "Mine"})
    send(client, conversation["id"], "anything")
    assert client.get(f"/api/conversations/{conversation['id']}").json()["title"] == "Mine"


def test_history_and_system_prompt_are_sent(client, conversation, mock):
    client.patch("/api/settings", json={"default_system_prompt": "Be terse."})
    send(client, conversation["id"], "first")
    send(client, conversation["id"], "second")
    body = [r for r in mock.app.state.requests if "title" not in str(r["messages"][-1]["content"])][-1]
    roles = [m["role"] for m in body["messages"]]
    assert roles == ["system", "user", "assistant", "user"]
    assert body["messages"][0]["content"] == "Be terse."
    # conversation-specific prompt overrides the default
    client.patch(f"/api/conversations/{conversation['id']}", json={"system_prompt": "Be verbose."})
    send(client, conversation["id"], "third")
    body = [r for r in mock.app.state.requests if "title" not in str(r["messages"][-1]["content"])][-1]
    assert body["messages"][0]["content"] == "Be verbose."


def test_params_merge_settings_conversation_request(client, conversation, mock):
    client.patch("/api/settings", json={"params": {"temperature": 0.1, "top_k": 10}})
    client.patch(f"/api/conversations/{conversation['id']}", json={"params": {"temperature": 0.5}})
    send(client, conversation["id"], "x", params={"seed": 42})
    body = mock.app.state.requests[0]
    assert body["temperature"] == 0.5 and body["top_k"] == 10 and body["seed"] == 42


def test_upstream_error_is_recorded_and_retry_works(client, conversation):
    events = send(client, conversation["id"], "boom [error]")
    err = next(d for e, d in events if e == "error")
    assert "the mock server exploded" in err["error"]
    assert err["message"]["status"] == "error"
    # Retry = regenerate the failed assistant message (user message edited first to remove the marker)
    msgs = client.get(f"/api/conversations/{conversation['id']}").json()["messages"]
    with client.stream("POST", f"/api/conversations/{conversation['id']}/messages/{msgs[0]['id']}/edit", json={"content": "fine now"}) as resp:
        events = read_sse(resp)
    assert next(d for e, d in events if e == "done")["message"]["content"] == "You said: fine now"
    detail = client.get(f"/api/conversations/{conversation['id']}").json()
    msgs = active_messages(detail)
    assert len(msgs) == 2 and msgs[1]["status"] == "complete"
    assert len(detail["messages"]) == 4  # the failed attempt stays as another branch


def test_midstream_error_keeps_partial_content(client, conversation):
    events = send(client, conversation["id"], "abc [midstream-error]")
    err = next(d for e, d in events if e == "error")
    assert "slot crashed" in err["error"]
    assert err["message"]["content"].startswith("You")


def test_truncated_stream_marked_error(client, conversation):
    events = send(client, conversation["id"], "x [truncate]")
    err = next(d for e, d in events if e == "error")
    assert "ended before" in err["error"]
    assert err["message"]["content"] == "You said: x [truncate]"


def test_malformed_events_do_not_duplicate_content(client, conversation):
    events = send(client, conversation["id"], "hello [malformed]")
    done = next(d for e, d in events if e == "done")
    assert done["message"]["content"] == "You said: hello [malformed]"


def test_reasoning_is_stored_separately(client, conversation):
    events = send(client, conversation["id"], "q [reasoning]")
    done = next(d for e, d in events if e == "done")
    assert done["message"]["reasoning"] == "Let me think about this carefully."
    assert done["message"]["content"] == "You said: q [reasoning]"


def test_regenerate_adds_a_sibling_answer(client, conversation, mock):
    send(client, conversation["id"], "one")
    msgs = client.get(f"/api/conversations/{conversation['id']}").json()["messages"]
    with client.stream("POST", f"/api/conversations/{conversation['id']}/regenerate", json={"message_id": msgs[1]["id"]}) as resp:
        events = read_sse(resp)
    assert "done" in names(events)
    detail = client.get(f"/api/conversations/{conversation['id']}").json()
    after = active_messages(detail)
    assert len(after) == 2 and after[1]["id"] != msgs[1]["id"]
    assert after[1]["parent_id"] == msgs[0]["id"]
    assert msgs[1]["id"] in {m["id"] for m in detail["messages"]}  # the previous answer is kept


def test_edit_creates_a_branch_and_keeps_the_original(client, conversation):
    send(client, conversation["id"], "one")
    send(client, conversation["id"], "two")
    msgs = client.get(f"/api/conversations/{conversation['id']}").json()["messages"]
    with client.stream("POST", f"/api/conversations/{conversation['id']}/messages/{msgs[0]['id']}/edit", json={"content": "uno"}) as resp:
        read_sse(resp)
    detail = client.get(f"/api/conversations/{conversation['id']}").json()
    assert [m["content"] for m in active_messages(detail)] == ["uno", "You said: uno"]
    assert len(detail["messages"]) == 6  # one, answer, two, answer + the edited branch


def test_cancel_stops_generation_and_keeps_partial(client, conversation, mock):
    collected = {}

    def consume():
        with client.stream("POST", f"/api/conversations/{conversation['id']}/messages", json={"content": "go [slow]"}) as resp:
            collected["events"] = read_sse(resp)

    t = threading.Thread(target=consume)
    t.start()
    gen_id = None
    deadline = time.time() + 5
    while time.time() < deadline and not gen_id:
        gen_id = client.get(f"/api/conversations/{conversation['id']}").json()["generation_id"]
        time.sleep(0.05)
    assert gen_id
    # duplicate submission while busy is rejected
    busy = client.post(f"/api/conversations/{conversation['id']}/messages", json={"content": "again"})
    assert busy.status_code == 409
    time.sleep(0.4)
    assert client.post(f"/api/generations/{gen_id}/cancel").json() == {"cancelled": True}
    t.join(timeout=5)
    done = next(d for e, d in collected["events"] if e == "done")
    assert done["message"]["status"] == "cancelled"
    assert done["message"]["content"].startswith("word0")
    assert len(done["message"]["content"]) < len(" ".join(f"word{i}" for i in range(SLOW_WORDS)))
    deadline = time.time() + 3
    while mock.app.state.cancelled == 0 and time.time() < deadline:
        time.sleep(0.05)
    assert mock.app.state.cancelled == 1, "upstream connection should be closed on stop"
    # conversation is usable again
    assert "done" in names(send(client, conversation["id"], "after stop"))


def test_reattach_stream_after_disconnect(live_app, mock):
    """A real HTTP disconnect (page reload) must not stop or lose the answer."""
    import httpx

    with httpx.Client(base_url=live_app.url, trust_env=False, timeout=30) as c:
        c.get("/api/auth/status")
        c.headers["X-CSRF-Token"] = c.cookies["chimera_csrf"]
        assert register(c, "live").status_code == 201
        c.headers["X-CSRF-Token"] = c.cookies["chimera_csrf"]  # rotated with the session
        prov = c.post("/api/providers", json={"name": "L", "kind": "llamacpp", "base_url": mock.url}).json()
        conv = c.post("/api/conversations", json={"provider_id": prov["id"], "model": mock.app.state.models[0]}).json()
        with c.stream("POST", f"/api/conversations/{conv['id']}/messages", json={"content": "go [slow]"}) as resp:
            for line in resp.iter_lines():
                if line.startswith("event: delta"):
                    break  # the browser goes away mid-stream
        with c.stream("GET", f"/api/conversations/{conv['id']}/stream") as resp:
            assert resp.status_code == 200
            events = read_sse(resp)
        assert names(events)[0] == "snapshot"
        full = events[0][1]["message"]["content"] + "".join(d.get("content", "") for e, d in events if e == "delta")
        assert full == " ".join(f"word{i}" for i in range(SLOW_WORDS))
        assert next(d for e, d in events if e == "done")["message"]["status"] == "complete"
        assert c.get(f"/api/conversations/{conv['id']}/stream").status_code == 204


def test_no_model_selected_is_rejected(client, provider):
    conv = client.post("/api/conversations", json={}).json()
    resp = client.post(f"/api/conversations/{conv['id']}/messages", json={"content": "hi"})
    assert resp.status_code == 400
    assert resp.json()["detail"]["code"] == "no_model"


def test_empty_message_rejected(client, conversation):
    resp = client.post(f"/api/conversations/{conversation['id']}/messages", json={"content": "   "})
    assert resp.status_code == 400


def test_disconnected_provider_error_and_status(client, conversation):
    pid = conversation["provider_id"]
    client.patch(f"/api/providers/{pid}", json={"base_url": "http://127.0.0.1:9"})
    events = send(client, conversation["id"], "hi")
    err = next(d for e, d in events if e == "error")
    assert "Could not connect" in err["error"]
    prov = next(p for p in client.get("/api/providers").json() if p["id"] == pid)
    assert prov["status"] == "error"


def test_interrupted_generation_recovered_on_restart(config, mock):
    app = create_app(config)
    with CsrfClient(app, base_url="http://localhost") as c:
        c.get("/api/auth/status")
        register(c, "alice")
        prov = c.post("/api/providers", json={"name": "L", "kind": "llamacpp", "base_url": mock.url}).json()
        conv = c.post("/api/conversations", json={"provider_id": prov["id"], "model": mock.app.state.models[0]}).json()
        state = app.state.chimera
        # Simulate a crash mid-generation: a row left in 'streaming'.
        state.db.execute(
            "INSERT INTO messages(id, conversation_id, position, role, content, status, created_at, updated_at) "
            "VALUES ('m1', ?, 0, 'assistant', 'partial', 'streaming', 'x', 'x')",
            (conv["id"],),
        )
    app2 = create_app(config)
    with CsrfClient(app2, base_url="http://localhost") as c:
        c.get("/api/auth/status")
        login(c, "alice")
        msgs = c.get(f"/api/conversations/{conv['id']}").json()["messages"]
        assert msgs[0]["status"] == "interrupted" and msgs[0]["content"] == "partial"


def test_conversations_persist_across_restart(config, mock):
    for attempt in range(2):
        app = create_app(config)
        with CsrfClient(app, base_url="http://localhost") as c:
            c.get("/api/auth/status")
            (register if attempt == 0 else login)(c, "alice")
            if attempt == 0:
                prov = c.post("/api/providers", json={"name": "L", "kind": "llamacpp", "base_url": mock.url}).json()
                conv = c.post("/api/conversations", json={"provider_id": prov["id"], "model": mock.app.state.models[0]}).json()
                send(c, conv["id"], "remember me")
            else:
                convs = c.get("/api/conversations").json()
                assert len(convs) == 1
                msgs = c.get(f"/api/conversations/{convs[0]['id']}").json()["messages"]
                assert msgs[0]["content"] == "remember me"
                assert msgs[1]["content"] == "You said: remember me"
                assert msgs[0]["created_at"] <= msgs[1]["created_at"]


def test_conversation_crud_and_search(client, conversation):
    send(client, conversation["id"], "Tell me about quaternions")
    cid = conversation["id"]
    assert client.patch(f"/api/conversations/{cid}", json={"pinned": True}).json()["pinned"] is True
    assert client.patch(f"/api/conversations/{cid}", json={"title": "Rotations"}).json()["title"] == "Rotations"
    results = client.get("/api/conversations/search", params={"q": "quaternion"}).json()
    assert results[0]["id"] == cid and "quaternions" in results[0]["snippet"]
    assert client.get("/api/conversations/search", params={"q": "Rotations"}).json()[0]["id"] == cid
    assert client.get("/api/conversations/search", params={"q": "100%_"}).json() == []
    msgs = client.get(f"/api/conversations/{cid}").json()["messages"]
    resp = client.delete(f"/api/messages/{msgs[1]['id']}")
    assert resp.status_code == 200 and resp.json()["deleted"] == 1
    assert len(client.get(f"/api/conversations/{cid}").json()["messages"]) == 1
    assert client.delete(f"/api/conversations/{cid}").status_code == 204
    assert client.get(f"/api/conversations/{cid}").status_code == 404


def test_conversation_list_pages_beyond_the_first_500(client, provider, mock):
    """Regression: the sidebar used to show only the newest 500 conversations, with no way to reach older ones."""
    model = mock.app.state.models[0]
    ids = [client.post("/api/conversations", json={"provider_id": provider["id"], "model": model}).json()["id"] for _ in range(7)]
    first = client.get("/api/conversations", params={"limit": 3}).json()
    second = client.get("/api/conversations", params={"limit": 3, "offset": 3}).json()
    third = client.get("/api/conversations", params={"limit": 3, "offset": 6}).json()
    seen = [c["id"] for c in first + second + third]
    assert len(first) == len(second) == 3 and len(third) == 1
    assert sorted(seen) == sorted(ids) and len(set(seen)) == 7  # every conversation exactly once
    assert [c["id"] for c in client.get("/api/conversations").json()] == seen  # the default is unchanged
    assert client.get("/api/conversations", params={"limit": 501}).status_code == 422
    assert client.get("/api/conversations", params={"offset": -1}).status_code == 422


def test_non_streaming_mode(client, conversation, mock):
    client.patch("/api/settings", json={"stream": False})
    events = send(client, conversation["id"], "hello")
    deltas = [d for e, d in events if e == "delta"]
    assert len(deltas) == 1 and deltas[0]["content"] == "You said: hello"
