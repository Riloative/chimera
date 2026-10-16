"""Conversation branching: message tree, active path, edit/regenerate/delete, export/import, migration."""

from __future__ import annotations

import io
import json
import sqlite3
import threading
import time

from chimera import db as dbmod
from chimera.main import create_app

from .conftest import CsrfClient, active_messages, make_config, read_sse, register


def send(client, cid, content, **extra):
    with client.stream("POST", f"/api/conversations/{cid}/messages", json={"content": content, **extra}) as resp:
        assert resp.status_code == 200, resp.read()
        return read_sse(resp)


def stream(client, path, body):
    with client.stream("POST", path, json=body) as resp:
        assert resp.status_code == 200, resp.read()
        return read_sse(resp)


def detail(client, cid):
    return client.get(f"/api/conversations/{cid}").json()


def path_text(client, cid):
    return [m["content"] for m in active_messages(detail(client, cid))]


def activate(client, cid, mid):
    resp = client.post(f"/api/conversations/{cid}/active", json={"message_id": mid})
    assert resp.status_code == 200, resp.text
    return resp.json()["active_path"]


def test_edit_and_regenerate_create_branches_and_switching_restores_them(client, conversation):
    cid = conversation["id"]
    send(client, cid, "one")
    send(client, cid, "two")
    send(client, cid, "three")
    original = detail(client, cid)
    u1, a1, u2, a2 = original["active_path"][:4]

    # Edit message 2: a sibling of "two" with a fresh continuation.
    stream(client, f"/api/conversations/{cid}/messages/{u2}/edit", {"content": "deux"})
    assert path_text(client, cid) == ["one", "You said: one", "deux", "You said: deux"]
    edited_branch = detail(client, cid)["active_path"]
    assert edited_branch[2] != u2 and detail(client, cid)["messages"][0]["parent_id"] is None

    # Back to the original "two": its whole continuation is still there.
    path = activate(client, cid, u2)
    assert path == original["active_path"]
    assert path_text(client, cid)[-1] == "You said: three"

    # Regenerate the answer to "one": a sibling answer; switching keeps both.
    stream(client, f"/api/conversations/{cid}/regenerate", {"message_id": a1})
    d = detail(client, cid)
    new_a1 = d["active_path"][1]
    assert new_a1 != a1 and len(d["active_path"]) == 2
    siblings = [m["id"] for m in d["messages"] if m["parent_id"] == u1]
    assert siblings == [a1, new_a1]

    # Switching to the old answer resumes its most recently viewed branch (the original "two" … "three").
    path = activate(client, cid, a1)
    assert path == original["active_path"]
    # …and switching to the edited sibling resumes "deux".
    assert activate(client, cid, edited_branch[2]) == edited_branch


def test_active_path_persists_across_reload_and_clients(client, conversation, app):
    from .conftest import login, new_client

    cid = conversation["id"]
    send(client, cid, "a")
    first = detail(client, cid)["active_path"]
    stream(client, f"/api/conversations/{cid}/messages/{first[0]}/edit", {"content": "b"})
    activate(client, cid, first[0])
    other_device = new_client(app)
    assert login(other_device, "alice").status_code == 200
    assert detail(other_device, cid)["active_path"] == first


def test_only_the_active_path_is_sent_to_the_model(client, conversation, mock):
    cid = conversation["id"]
    send(client, cid, "alpha")
    path = detail(client, cid)["active_path"]
    stream(client, f"/api/conversations/{cid}/messages/{path[0]}/edit", {"content": "beta"})
    send(client, cid, "gamma")
    sent = [m["content"] for m in mock.app.state.requests[-1]["messages"] if m["role"] != "system"]
    assert sent == ["beta", "You said: beta", "gamma"]
    assert all("alpha" not in m for m in sent)


def test_delete_removes_the_subtree_and_repairs_the_active_path(client, conversation):
    cid = conversation["id"]
    send(client, cid, "one")
    send(client, cid, "two")
    u1, a1, u2, a2 = detail(client, cid)["active_path"]
    stream(client, f"/api/conversations/{cid}/regenerate", {"message_id": a1})  # sibling of a1, now active
    activate(client, cid, a1)
    resp = client.delete(f"/api/messages/{a1}")
    assert resp.json()["deleted"] == 3  # a1, u2, a2
    d = detail(client, cid)
    assert len(d["messages"]) == 2 and d["active_path"][0] == u1 and d["active_path"][1] != a1
    # Deleting the only root leaves an empty conversation.
    assert client.delete(f"/api/messages/{u1}").json() == {"deleted": 2, "active_path": []}
    assert detail(client, cid)["messages"] == []


def test_failed_attempt_is_replaced_not_kept(client, conversation):
    cid = conversation["id"]
    send(client, cid, "boom [error]")
    u, a = detail(client, cid)["active_path"]
    stream(client, f"/api/conversations/{cid}/regenerate", {"message_id": a})
    d = detail(client, cid)
    assert len(d["messages"]) == 2 and a not in {m["id"] for m in d["messages"]}


def test_streaming_continues_on_its_branch_while_another_is_shown(client, conversation, mock):
    cid = conversation["id"]
    send(client, cid, "first")
    u1 = detail(client, cid)["active_path"][0]
    box = {}

    def slow_edit():
        with client.stream("POST", f"/api/conversations/{cid}/messages/{u1}/edit", json={"content": "go [slow]"}) as resp:
            box["events"] = read_sse(resp)

    thread = threading.Thread(target=slow_edit)
    thread.start()
    deadline = time.time() + 5
    while time.time() < deadline and not detail(client, cid)["generation_id"]:
        time.sleep(0.05)
    streaming_path = detail(client, cid)["active_path"]
    # Switch back to the original branch while the edit's answer is streaming.
    assert activate(client, cid, u1)[0] == u1
    thread.join(timeout=15)
    done = next(d for e, d in box["events"] if e == "done")["message"]
    assert done["id"] == streaming_path[-1] and done["content"].startswith("word0")
    d = detail(client, cid)
    assert d["active_path"][0] == u1  # finishing didn't move the view
    assert path_text(client, cid) == ["first", "You said: first"]
    assert activate(client, cid, streaming_path[0])[-1] == done["id"]


def test_stop_affects_only_its_node(client, conversation, mock):
    cid = conversation["id"]
    send(client, cid, "keep me")
    u1, a1 = detail(client, cid)["active_path"]
    box: dict = {}
    send_thread = threading.Thread(target=lambda: box.setdefault("events", send(client, cid, "more [slow]")))
    send_thread.start()
    deadline = time.time() + 5
    gen = None
    while time.time() < deadline and not gen:
        gen = detail(client, cid)["generation_id"]
        time.sleep(0.05)
    time.sleep(0.3)
    assert client.post(f"/api/generations/{gen}/cancel").json() == {"cancelled": True}
    send_thread.join(timeout=10)
    d = detail(client, cid)
    by_id = {m["id"]: m for m in d["messages"]}
    assert by_id[a1]["status"] == "complete" and by_id[a1]["content"] == "You said: keep me"
    assert by_id[d["active_path"][-1]]["status"] == "cancelled"


def test_search_finds_other_branches_and_opens_them(client, conversation):
    cid = conversation["id"]
    send(client, cid, "about zebras")
    u1 = detail(client, cid)["active_path"][0]
    stream(client, f"/api/conversations/{cid}/messages/{u1}/edit", {"content": "about lions"})
    hit = client.get("/api/conversations/search", params={"q": "zebras"}).json()[0]
    assert hit["id"] == cid and hit["match_message_id"] == u1
    path = activate(client, cid, hit["match_message_id"])
    assert path[0] == u1


def test_pin_and_isolation_of_branch_endpoints(client, conversation, make_user):
    cid = conversation["id"]
    send(client, cid, "pin me")
    u1, a1 = detail(client, cid)["active_path"]
    assert client.patch(f"/api/messages/{u1}", json={"pinned": True}).json()["pinned"] is True
    bob = make_user()
    assert bob.post(f"/api/conversations/{cid}/active", json={"message_id": a1}).status_code == 404
    assert bob.patch(f"/api/messages/{u1}", json={"pinned": False}).status_code == 404
    assert bob.delete(f"/api/messages/{u1}").status_code == 404
    assert client.post(f"/api/conversations/{cid}/active", json={"message_id": "nope"}).json()["detail"]["code"] == "branch.not_found"
    client.send_csrf = False
    try:
        assert client.post(f"/api/conversations/{cid}/active", json={"message_id": a1}).status_code == 403
    finally:
        client.send_csrf = True
    assert detail(client, cid)["messages"][0]["pinned"] is True


def test_export_import_tree_and_legacy_linear_format(client, conversation):
    cid = conversation["id"]
    send(client, cid, "root")
    send(client, cid, "child")
    u1, a1, u2, a2 = detail(client, cid)["active_path"]
    stream(client, f"/api/conversations/{cid}/messages/{u2}/edit", {"content": "other child"})
    activate(client, cid, u2)
    export = json.loads(client.get("/api/data/export").content)
    assert export["version"] == 3
    [conv] = export["conversations"]
    assert conv["active_leaf_id"] == a2 and len(conv["messages"]) == 6
    parents = [m["parent_id"] for m in conv["messages"]]
    assert parents.count(None) == 1 and parents.count(a1) == 2  # "child" and "other child" are siblings

    # Re-import as another user's data path: same user, existing IDs are skipped, so change the id.
    conv["id"] = "imported-tree"
    resp = client.post("/api/data/import", files={"file": ("e.json", io.BytesIO(json.dumps(export).encode()), "application/json")})
    assert resp.json()["messages"] == 6
    d = detail(client, "imported-tree")
    assert [m["content"] for m in active_messages(d)] == ["root", "You said: root", "child", "You said: child"]
    roots = [m for m in d["messages"] if m["parent_id"] is None]
    assert len(roots) == 1 and len([m for m in d["messages"] if m["parent_id"] == active_messages(d)[1]["id"]]) == 2

    legacy = {"format": "chimera-export", "version": 2, "conversations": [{
        "id": "legacy-1", "title": "Old", "messages": [
            {"role": "user", "content": "q1", "position": 0}, {"role": "assistant", "content": "a1", "position": 1},
            {"role": "user", "content": "q2", "position": 2}, {"role": "assistant", "content": "a2", "position": 3}]}]}
    resp = client.post("/api/data/import", files={"file": ("l.json", io.BytesIO(json.dumps(legacy).encode()), "application/json")})
    assert resp.json()["messages"] == 4
    d = detail(client, "legacy-1")
    assert [m["content"] for m in active_messages(d)] == ["q1", "a1", "q2", "a2"]
    assert [m["position"] for m in active_messages(d)] == [0, 1, 2, 3]


def test_migration_turns_linear_history_into_chains(tmp_path, monkeypatch):
    cfg = make_config(tmp_path)
    cfg.data_dir.mkdir(parents=True)
    # Build a v3 database (the previous schema) with linear conversations.
    monkeypatch.setattr(dbmod, "MIGRATIONS", dbmod.MIGRATIONS[:3])
    old = dbmod.Database(cfg.db_path)
    ts = "2026-01-01T00:00:00.000Z"
    old.execute("INSERT INTO users(id, email, email_norm, username, username_norm, password_hash, role, created_at, updated_at) "
                "VALUES ('u','a@x.org','a@x.org','a','a','x','admin',?,?)", (ts, ts))
    for cid, n in (("c1", 5), ("c2", 0), ("c3", 2)):
        old.execute("INSERT INTO conversations(id, user_id, title, created_at, updated_at) VALUES (?,?,?,?,?)", (cid, "u", cid, ts, ts))
        for i in range(n):
            # gaps in positions (an older single-message delete) must not matter
            old.execute("INSERT INTO messages(id, conversation_id, position, role, content, created_at, updated_at) VALUES (?,?,?,?,?,?,?)",
                        (f"{cid}-m{i}", cid, i * 2, "user" if i % 2 == 0 else "assistant", f"text {i}", f"2026-01-01T00:00:0{i}.000Z", ts))
    old.close()
    monkeypatch.undo()  # back to the full migration list

    with CsrfClient(create_app(cfg), base_url="http://localhost") as c:
        state = c.app.state.chimera
        assert state.db.schema_version() == len(dbmod.MIGRATIONS)
        assert state.db.last_backup and "pre-migration-v3-to-v4" in state.db.last_backup.name
        rows = state.db.all("SELECT id, parent_id, position, active_child_id, content, created_at FROM messages WHERE conversation_id = 'c1' ORDER BY position")
        assert [r["id"] for r in rows] == [f"c1-m{i}" for i in range(5)]
        assert [r["parent_id"] for r in rows] == [None] + [f"c1-m{i}" for i in range(4)]
        assert [r["position"] for r in rows] == [0, 1, 2, 3, 4]
        assert [r["active_child_id"] for r in rows] == [f"c1-m{i}" for i in range(1, 5)] + [None]
        assert rows[3]["content"] == "text 3" and rows[3]["created_at"] == "2026-01-01T00:00:03.000Z"
        leaves = {r["id"]: r["active_leaf_id"] for r in state.db.all("SELECT id, active_leaf_id FROM conversations")}
        assert leaves == {"c1": "c1-m4", "c2": None, "c3": "c3-m1"}
        backup = sqlite3.connect(str(state.db.last_backup))
        assert backup.execute("SELECT MAX(version) FROM schema_version").fetchone()[0] == 3
        backup.close()
        assert register(c, "a2").status_code in (201, 403)  # the app serves requests on the migrated database
