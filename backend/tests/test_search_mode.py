"""Web search per conversation (off/on/auto), one-off overrides, Auto
decisions, the 1.2 migration and the conversation search index."""

from __future__ import annotations

import json
import sqlite3

import pytest

from chimera import db as dbmod
from chimera.services.conversations import ConversationStore
from tests.conftest import read_sse
from tests.test_web_chat import detail, final, send


def set_mode(client, cid, mode):
    resp = client.patch(f"/api/conversations/{cid}", json={"web_search": mode})
    assert resp.status_code == 200, resp.text
    return resp.json()


def regenerate(client, cid, **body):
    with client.stream("POST", f"/api/conversations/{cid}/regenerate", json=body) as resp:
        assert resp.status_code == 200, resp.read()
        return final(read_sse(resp))


def edit(client, cid, mid, content, **body):
    with client.stream("POST", f"/api/conversations/{cid}/messages/{mid}/edit", json={"content": content, **body}) as resp:
        assert resp.status_code == 200, resp.read()
        return final(read_sse(resp))


def kinds(msg):
    return [s["kind"] for s in msg["web"].get("steps", [])]


# ------------------------------------------------------------ stored mode
def test_new_conversations_start_from_the_users_default_and_keep_their_mode(client, provider, mock):
    model = mock.app.state.models[0]
    off = client.post("/api/conversations", json={"provider_id": provider["id"], "model": model}).json()
    assert off["web_search"] == "off"
    client.patch("/api/settings", json={"web": {"search_default": "auto"}})
    auto = client.post("/api/conversations", json={"provider_id": provider["id"], "model": model}).json()
    assert auto["web_search"] == "auto"
    chosen = client.post("/api/conversations", json={"provider_id": provider["id"], "model": model, "web_search": "on"}).json()
    assert chosen["web_search"] == "on"
    # Stored on the server: another device (a fresh load) sees the same mode.
    assert set_mode(client, off["id"], "on")["web_search"] == "on"
    assert client.get(f"/api/conversations/{off['id']}").json()["web_search"] == "on"
    assert client.patch(f"/api/conversations/{off['id']}", json={"web_search": "sometimes"}).status_code == 422
    assert client.patch(f"/api/conversations/{off['id']}", json={"web_search": None}).json()["web_search"] == "on"


def test_legacy_settings_are_read_as_the_new_default(client):
    client.patch("/api/settings", json={"web": {"search_by_default": True, "keep_search_on": True}})
    web = client.get("/api/settings").json()["web"]
    # Unknown in 1.2 and ignored on write; the stored default is unchanged ("off").
    assert web["search_default"] == "off" and "keep_search_on" not in web


# ------------------------------------------------------ follow the mode
def test_messages_follow_the_conversation_mode_unless_overridden(client, conversation, web):
    cid = conversation["id"]
    assert final(send(client, cid, "festival one"))["web"].get("search") in (None, False)  # off
    set_mode(client, cid, "on")
    assert final(send(client, cid, "festival two"))["web"]["search"] is True
    # One-off override: this message only, the conversation stays "on".
    assert final(send(client, cid, "festival three", web_search=False))["web"].get("search") in (None, False)
    assert detail(client, cid)["web_search"] == "on"
    set_mode(client, cid, "off")
    assert final(send(client, cid, "festival four", web_search=True))["web"]["search"] is True
    assert detail(client, cid)["web_search"] == "off"


def test_regenerate_and_edit_follow_the_conversation(client, conversation, web):
    """Regression (1.1): an edited message never searched, whatever the toggle said."""
    cid = conversation["id"]
    set_mode(client, cid, "on")
    first = final(send(client, cid, "vilnius festival"))
    assert first["web"]["search"] is True
    user_id = first["parent_id"]
    assert edit(client, cid, user_id, "kaunas festival")["web"]["search"] is True
    assert regenerate(client, cid)["web"]["search"] is True
    assert regenerate(client, cid, web_search=False)["web"].get("search") in (None, False)
    set_mode(client, cid, "off")
    assert regenerate(client, cid)["web"].get("search") in (None, False)
    assert regenerate(client, cid, web_search=True)["web"]["search"] is True


def test_a_mode_quietly_falls_back_when_search_is_not_available(client, conversation, web):
    cid = conversation["id"]
    set_mode(client, cid, "on")
    client.patch("/api/admin/web", json={"settings": {"engine": None}})
    msg = final(send(client, cid, "festival"))  # the mode doesn't refuse the message
    assert msg["status"] == "complete" and not msg["web"].get("search")
    # An explicit request still says why it can't search.
    resp = send(client, cid, "festival", expect=409, web_search=True)
    assert resp["detail"]["code"] == "web.search_unavailable"


# ----------------------------------------------------------------- Auto
def test_auto_searches_when_the_model_says_so(client, conversation, web, mock):
    cid = conversation["id"]
    set_mode(client, cid, "auto")
    msg = final(send(client, cid, "What is the latest news about the Vilnius festival?"))
    decide = next(s for s in msg["web"]["steps"] if s["kind"] == "decide")
    assert decide["status"] == "done" and decide["params"]["search"] is True and decide["params"]["reason"]
    assert msg["web"]["search"] is True and msg["web"]["auto"] is True and "search" in kinds(msg)
    assert msg["sources"] or detail(client, cid)["web_sources"]
    # One quick call decided and wrote the queries; no separate query call.
    prompts = [json.dumps(r["messages"][-1]) for r in mock.app.state.requests]
    assert sum("needs a web search" in p for p in prompts) == 1 and not any("Write web search queries" in p for p in prompts)


def test_auto_answers_without_search_when_not_needed(client, conversation, web):
    cid = conversation["id"]
    set_mode(client, cid, "auto")
    count = len(web.server.app.state.searches)
    msg = final(send(client, cid, "Write a haiku about autumn"))
    assert kinds(msg) == ["decide"] and msg["web"]["steps"][0]["params"]["search"] is False
    assert msg["status"] == "complete" and len(web.server.app.state.searches) == count


def test_auto_fails_safe(client, conversation, web):
    """An unusable decision, or a failed search, never blocks the answer."""
    cid = conversation["id"]
    set_mode(client, cid, "auto")
    msg = final(send(client, cid, "[auto-fail] Anything new?"))
    step = msg["web"]["steps"][0]
    assert step["kind"] == "decide" and step["status"] == "error" and step["code"] == "web.auto_undecided"
    assert msg["status"] == "complete" and msg["content"]
    msg = final(send(client, cid, "[auto-yes] noresults festival"))
    assert msg["status"] == "complete" and any(s["kind"] == "search" and s["status"] == "error" for s in msg["web"]["steps"])
    # "Search the web" afterwards is an ordinary override.
    assert regenerate(client, cid, web_search=True)["web"]["search"] is True


def test_auto_can_be_turned_off_by_the_administrator(client, conversation, web):
    cid = conversation["id"]
    set_mode(client, cid, "auto")
    client.patch("/api/admin/web", json={"settings": {"auto_search": False}})
    assert client.get("/api/web/status").json()["auto_available"] is False
    msg = final(send(client, cid, "latest news"))
    assert not msg["web"].get("steps")  # treated as off: no decision, no search


# --------------------------------------------------------- migration 5
def _v4_database(path):
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("CREATE TABLE schema_version (version INTEGER NOT NULL)")
    for script in dbmod.MIGRATIONS[:4]:
        if callable(script):
            script(conn)
        else:
            for statement in dbmod._split_sql(script):
                conn.execute(statement)
    conn.execute("INSERT INTO schema_version(version) VALUES (4)")
    ts = "2026-11-01T10:00:00.000Z"
    for uid, name, web in (("u1", "ona", {"search_by_default": True, "keep_search_on": True}), ("u2", "jonas", {"search_by_default": False}),
                           ("u3", "rita", None)):
        conn.execute("INSERT INTO users(id, email, email_norm, username, username_norm, password_hash, created_at, updated_at) "
                     "VALUES (?,?,?,?,?,?,?,?)", (uid, f"{name}@x", f"{name}@x", name, name, "x", ts, ts))
        if web is not None:
            conn.execute("INSERT INTO user_settings(user_id, key, value) VALUES (?, 'web', ?)", (uid, json.dumps(web)))
        conn.execute("INSERT INTO conversations(id, user_id, title, created_at, updated_at) VALUES (?,?,?,?,?)", (f"c-{uid}", uid, "T", ts, ts))
        conn.execute("INSERT INTO messages(id, conversation_id, position, role, content, created_at, updated_at) VALUES (?,?,?,?,?,?,?)",
                     (f"m-{uid}", f"c-{uid}", 0, "user", f"Kelionė į Šiaulius su {name}", ts, ts))
    conn.commit()
    conn.close()


def test_migration_keeps_each_users_search_preference(tmp_path):
    path = tmp_path / "chimera.sqlite"
    _v4_database(path)
    db = dbmod.Database(path, backups_dir=tmp_path / "backups")
    assert db.schema_version() == len(dbmod.MIGRATIONS)
    assert db.last_backup and f"pre-migration-v4-to-v{len(dbmod.MIGRATIONS)}" in db.last_backup.name
    modes = {r["id"]: r["web_search"] for r in db.all("SELECT id, web_search FROM conversations")}
    assert modes == {"c-u1": "on", "c-u2": "off", "c-u3": "off"}
    web = json.loads(db.one("SELECT value FROM user_settings WHERE user_id = 'u1' AND key = 'web'")["value"])
    assert web["search_default"] == "on"
    # Existing messages are searchable through the new index, case-insensitively.
    store = ConversationStore(db)
    assert store._fts() and [c["id"] for c in store.search("u1", "šIAULIUS")] == ["c-u1"]
    assert store.search("u2", "Šiaulius")[0]["id"] == "c-u2" and store.search("u3", "nothing here") == []


# ------------------------------------------------------- search index
def test_search_index_matches_like_the_scan_did(client, conversation, provider, mock, monkeypatch):
    cid = conversation["id"]
    state = client.app.state.chimera
    store = state.conversations
    assert store._fts()
    send(client, cid, "Plan: 50% off at Café Žalgiris_2, \"quoted\" words")

    def ids(q):
        return [c["id"] for c in client.get("/api/conversations/search", params={"q": q}).json()]

    for q in ("café žalgiris", "50% off", "is_2", '"quoted"', "Plan:", "LGIR"):
        assert ids(q) == [cid], q
    for q in ("50%x", "is-2", "nothing like it"):
        assert ids(q) == [], q
    # Edits and deletions keep the index current.
    msg_id = detail(client, cid)["messages"][0]["id"]
    edit(client, cid, msg_id, "Brand new words")
    assert ids("brand new") == [cid]
    client.delete(f"/api/conversations/{cid}")
    assert ids("brand new") == [] and ids("café") == []
    # A very common term goes through the scan instead, with the same result.
    other = client.post("/api/conversations", json={"provider_id": provider["id"], "model": mock.app.state.models[0]}).json()["id"]
    send(client, other, "common words here")
    monkeypatch.setattr(type(store), "FTS_MAX_HITS", 0)
    assert ids("common words") == [other]
