"""User B must not be able to see or touch anything that belongs to user A."""

import io

import pytest

from .conftest import read_sse

PNG_1PX = bytes.fromhex(
    "89504e470d0a1a0a0000000d4948445200000001000000010806000000"
    "1f15c4890000000d49444154789c6360000002000100e221bc330000000049454e44ae426082"
)


@pytest.fixture()
def world(client, make_user, mock):
    """Alice (admin) owns one of everything; Bob is a regular user."""
    a = client
    prov = a.post("/api/providers", json={"name": "Alice's server", "kind": "llamacpp", "base_url": mock.url, "api_key": "sk-alice"}).json()
    conv = a.post("/api/conversations", json={"provider_id": prov["id"], "model": mock.app.state.models[0]}).json()
    with a.stream("POST", f"/api/conversations/{conv['id']}/messages", json={"content": "alice secret quokka"}) as r:
        read_sse(r)
    msgs = a.get(f"/api/conversations/{conv['id']}").json()["messages"]
    prompt = a.post("/api/prompts", json={"title": "Alice prompt", "content": "secret"}).json()
    col = a.post("/api/collections", json={"name": "Alice docs"}).json()
    doc = a.post(f"/api/collections/{col['id']}/documents", files={"file": ("a.md", io.BytesIO(b"quokka facts"), "text/markdown")}).json()
    text_file = a.post("/api/files", files={"file": ("a.txt", io.BytesIO(b"alice private"), "text/plain")}).json()
    image = a.post("/api/files", files={"file": ("a.png", io.BytesIO(PNG_1PX), "image/png")}).json()
    b = make_user("bob")
    return {"a": a, "b": b, "prov": prov, "conv": conv, "msgs": msgs, "prompt": prompt, "col": col, "doc": doc,
            "file": text_file, "image": image}


def test_lists_are_scoped(world):
    b = world["b"]
    assert b.get("/api/conversations").json() == []
    assert b.get("/api/prompts").json() == []
    assert b.get("/api/collections").json() == []
    assert b.get("/api/providers").json() == []
    assert b.get("/api/models").json()["providers"] == []
    assert b.get("/api/conversations/search", params={"q": "quokka"}).json() == []
    exported = b.get("/api/data/export").text
    assert "quokka" not in exported and "Alice" not in exported


def test_reads_return_404(world):
    b, w = world["b"], world
    for url in [
        f"/api/conversations/{w['conv']['id']}",
        f"/api/conversations/{w['conv']['id']}/stream",
        f"/api/collections/{w['col']['id']}",
        f"/api/files/{w['file']['id']}",
        f"/api/files/{w['image']['id']}/content",
    ]:
        assert b.get(url).status_code == 404, url


def test_writes_return_404(world):
    b, w = world["b"], world
    cid = w["conv"]["id"]
    checks = [
        b.patch(f"/api/conversations/{cid}", json={"title": "pwned"}),
        b.delete(f"/api/conversations/{cid}"),
        b.post(f"/api/conversations/{cid}/messages", json={"content": "inject"}),
        b.post(f"/api/conversations/{cid}/regenerate", json={}),
        b.post(f"/api/conversations/{cid}/messages/{w['msgs'][0]['id']}/edit", json={"content": "x"}),
        b.delete(f"/api/messages/{w['msgs'][1]['id']}"),
        b.put(f"/api/prompts/{w['prompt']['id']}", json={"title": "x", "content": "y"}),
        b.delete(f"/api/prompts/{w['prompt']['id']}"),
        b.patch(f"/api/collections/{w['col']['id']}", json={"name": "x"}),
        b.delete(f"/api/collections/{w['col']['id']}"),
        b.post(f"/api/collections/{w['col']['id']}/search", json={"query": "quokka"}),
        b.post(f"/api/collections/{w['col']['id']}/documents", files={"file": ("x.md", io.BytesIO(b"x"), "text/markdown")}),
        b.post(f"/api/documents/{w['doc']['id']}/reindex"),
        b.delete(f"/api/documents/{w['doc']['id']}"),
        b.delete(f"/api/files/{w['file']['id']}"),
        b.patch(f"/api/providers/{w['prov']['id']}", json={"name": "x"}),
        b.delete(f"/api/providers/{w['prov']['id']}"),
        b.post(f"/api/providers/{w['prov']['id']}/test"),
    ]
    assert [r.status_code for r in checks] == [404] * len(checks)
    # Nothing changed for Alice.
    a = w["a"]
    assert a.get(f"/api/conversations/{cid}").json()["title"] != "pwned"
    assert len(a.get(f"/api/conversations/{cid}").json()["messages"]) == 2
    assert a.get(f"/api/prompts").json()[0]["title"] == "Alice prompt"


def test_cannot_borrow_others_resources(world):
    """References to someone else's IDs inside your own requests are ignored or rejected."""
    b, w = world["b"], world
    # Using Alice's connection in Bob's conversation
    conv = b.post("/api/conversations", json={"provider_id": w["prov"]["id"], "model": "m"}).json()
    assert conv["provider_id"] is None
    assert b.patch(f"/api/conversations/{conv['id']}", json={"provider_id": w["prov"]["id"]}).status_code == 404
    # Attaching Alice's knowledge collection
    assert b.patch(f"/api/conversations/{conv['id']}", json={"collection_ids": [w["col"]["id"]]}).json()["collection_ids"] == []
    # Attaching Alice's file to a message
    resp = b.post(f"/api/conversations/{conv['id']}/messages", json={"content": "hi", "attachments": [w["file"]["id"]], "provider_id": w["prov"]["id"], "model": "m"})
    assert resp.status_code in (400, 404)


def test_cannot_cancel_others_generation(world, mock):
    import threading
    import time

    a, b, w = world["a"], world["b"], world
    done = {}

    def run():
        with a.stream("POST", f"/api/conversations/{w['conv']['id']}/messages", json={"content": "go [slow]"}) as r:
            done["events"] = read_sse(r)

    t = threading.Thread(target=run)
    t.start()
    gid = None
    for _ in range(100):
        gid = a.get(f"/api/conversations/{w['conv']['id']}").json()["generation_id"]
        if gid:
            break
        time.sleep(0.02)
    assert gid
    assert b.post(f"/api/generations/{gid}/cancel").status_code == 404
    assert a.post(f"/api/generations/{gid}/cancel").json() == {"cancelled": True}
    t.join(timeout=5)
    assert next(d for e, d in done["events"] if e == "done")["message"]["status"] == "cancelled"


def test_shared_connections_visible_but_read_only(world, mock):
    a, b = world["a"], world["b"]
    shared = a.post("/api/providers", json={"name": "House server", "kind": "llamacpp", "base_url": mock.url, "shared": True, "api_key": "sk-house"}).json()
    listed = b.get("/api/providers").json()
    assert [p["name"] for p in listed] == ["House server"]
    assert listed[0]["editable"] is False and listed[0]["has_api_key"] is True and "api_key" not in listed[0]
    assert b.patch(f"/api/providers/{shared['id']}", json={"name": "mine"}).status_code == 403
    assert b.delete(f"/api/providers/{shared['id']}").status_code == 403
    # Bob can chat through it.
    conv = b.post("/api/conversations", json={"provider_id": shared["id"], "model": mock.app.state.models[0]}).json()
    with b.stream("POST", f"/api/conversations/{conv['id']}/messages", json={"content": "hello"}) as r:
        events = read_sse(r)
    assert next(d for e, d in events if e == "done")["message"]["content"] == "You said: hello"
    # The stored key is used for Bob's request but never revealed to him.
    assert "sk-house" not in b.get("/api/models").text
    # Regular users can't create shared connections.
    assert b.post("/api/providers", json={"name": "x", "kind": "llamacpp", "base_url": mock.url, "shared": True}).status_code == 403


def test_api_keys_encrypted_at_rest(app, world):
    stored = app.state.chimera.db.one("SELECT api_key FROM providers WHERE id = ?", (world["prov"]["id"],))["api_key"]
    assert stored.startswith("enc:v1:") and "sk-alice" not in stored
    assert "sk-alice" not in world["a"].get("/api/providers").text


def test_provider_url_policy_for_regular_users(world):
    a, b = world["a"], world["b"]
    ok = b.post("/api/providers", json={"name": "local", "kind": "llamacpp", "base_url": "http://192.168.1.50:8080"})
    assert ok.status_code == 201
    public = b.post("/api/providers", json={"name": "x", "kind": "openai", "base_url": "http://8.8.8.8"})
    assert public.status_code == 403 and public.json()["detail"]["code"] == "provider_url_forbidden"
    assert b.post("/api/providers/test", json={"kind": "openai", "base_url": "http://8.8.8.8"}).status_code == 403
    a.patch("/api/admin/settings", json={"provider_url_policy": "custom", "provider_url_allowlist": ["10.0.0.0/8"]})
    assert b.post("/api/providers", json={"name": "x", "kind": "llamacpp", "base_url": "http://10.1.2.3"}).status_code == 201
    assert b.post("/api/providers", json={"name": "x", "kind": "llamacpp", "base_url": "http://192.168.1.50"}).status_code == 403
    # Admins are not restricted.
    assert a.post("/api/providers", json={"name": "cloud", "kind": "openai", "base_url": "http://8.8.8.8"}).status_code == 201


def test_account_deletion_removes_everything(app, client, make_user, mock):
    b = make_user("bob")
    prov = b.post("/api/providers", json={"name": "bob", "kind": "llamacpp", "base_url": mock.url}).json()
    conv = b.post("/api/conversations", json={"provider_id": prov["id"], "model": mock.app.state.models[0]}).json()
    with b.stream("POST", f"/api/conversations/{conv['id']}/messages", json={"content": "bye"}) as r:
        read_sse(r)
    b.post("/api/prompts", json={"title": "p", "content": "c"})
    col = b.post("/api/collections", json={"name": "c"}).json()
    b.post(f"/api/collections/{col['id']}/documents", files={"file": ("d.md", io.BytesIO(b"chunk text"), "text/markdown")})
    img = b.post("/api/files", files={"file": ("p.png", io.BytesIO(PNG_1PX), "image/png")}).json()
    me = b.get("/api/auth/me").json()
    uploads = app.state.chimera.config.uploads_dir / me["id"]
    assert (uploads / img["id"]).is_file()

    assert b.request("DELETE", "/api/account", json={"password": "wrong password!"}).status_code == 403
    assert b.request("DELETE", "/api/account", json={"password": "correct horse battery"}).status_code == 204
    db = app.state.chimera.db
    assert db.one("SELECT 1 AS x FROM users WHERE id = ?", (me["id"],)) is None
    for table in ("conversations", "prompts", "files", "collections", "providers", "user_settings", "sessions"):
        assert db.one(f"SELECT COUNT(*) AS n FROM {table} WHERE user_id = ?", (me["id"],))["n"] == 0, table
    assert db.one("SELECT COUNT(*) AS n FROM messages WHERE conversation_id = ?", (conv["id"],))["n"] == 0
    assert db.one("SELECT COUNT(*) AS n FROM chunks WHERE collection_id = ?", (col["id"],))["n"] == 0
    assert db.one("SELECT COUNT(*) AS n FROM documents WHERE collection_id = ?", (col["id"],))["n"] == 0
    assert not uploads.exists()
    assert b.get("/api/conversations").status_code == 401


def test_admin_delete_user_removes_data(app, client, make_user):
    b = make_user("bob")
    b.post("/api/prompts", json={"title": "p", "content": "c"})
    bob_id = b.get("/api/auth/me").json()["id"]
    assert client.delete(f"/api/admin/users/{bob_id}").status_code == 204
    assert app.state.chimera.db.one("SELECT COUNT(*) AS n FROM prompts WHERE user_id = ?", (bob_id,))["n"] == 0
    assert client.delete(f"/api/admin/users/{client.get('/api/auth/me').json()['id']}").status_code == 409


def test_import_never_overwrites_other_users(world):
    a, b = world["a"], world["b"]
    payload = a.get("/api/data/export").content
    result = b.post("/api/data/import", files={"file": ("e.json", io.BytesIO(payload), "application/json")}).json()
    assert result["conversations"] == 1
    bob_convs = b.get("/api/conversations").json()
    assert bob_convs[0]["id"] != world["conv"]["id"]  # fresh ID, Alice's row untouched
    assert a.get(f"/api/conversations/{world['conv']['id']}").status_code == 200


def test_settings_are_per_user(world):
    a, b = world["a"], world["b"]
    a.patch("/api/settings", json={"theme": "dark", "default_system_prompt": "Alice rules"})
    s = b.get("/api/settings").json()
    assert s["theme"] == "system" and s["default_system_prompt"] == ""
