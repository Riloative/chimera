"""Upgrading a pre-accounts (schema v1) database."""

import sqlite3

from chimera.db import MIGRATIONS, _split_sql
from chimera.main import create_app

from .conftest import CsrfClient, make_config, new_client, register

PNG = b"\x89PNG\r\n\x1a\nfake"


def build_v1_database(config) -> str:
    """A fixture database exactly as version 0.1 created it, with data."""
    config.data_dir.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(config.db_path)
    conn.execute("CREATE TABLE schema_version (version INTEGER NOT NULL)")
    for stmt in _split_sql(MIGRATIONS[0]):
        conn.execute(stmt)
    conn.execute("INSERT INTO schema_version VALUES (1)")
    ts = "2026-01-01T00:00:00.000Z"
    conn.execute(
        "INSERT INTO providers(id, name, kind, base_url, api_key, created_at, updated_at) VALUES ('p1','Old','llamacpp','http://127.0.0.1:8080','sk-legacy',?,?)",
        (ts, ts),
    )
    conn.execute("INSERT INTO conversations(id, title, provider_id, model, created_at, updated_at) VALUES ('c1','Old chat','p1','m',?,?)", (ts, ts))
    conn.execute("INSERT INTO messages(id, conversation_id, position, role, content, created_at, updated_at) VALUES ('m1','c1',0,'user','legacy hello',?,?)", (ts, ts))
    conn.execute("INSERT INTO prompts(id, title, content, created_at, updated_at) VALUES ('pr1','Old prompt','x',?,?)", (ts, ts))
    conn.execute("INSERT INTO collections(id, name, created_at, updated_at) VALUES ('col1','Old docs',?,?)", (ts, ts))
    fid = "a" * 32
    conn.execute(
        "INSERT INTO files(id, filename, mime, size, kind, storage_name, created_at) VALUES (?,?,?,?,?,?,?)",
        (fid, "old.png", "image/png", len(PNG), "image", fid, ts),
    )
    conn.execute("INSERT INTO settings(key, value) VALUES ('theme', '\"dark\"')")
    conn.execute("INSERT INTO settings(key, value) VALUES ('_seeded', 'true')")
    conn.commit()
    conn.close()
    (config.uploads_dir).mkdir(parents=True, exist_ok=True)
    (config.uploads_dir / fid).write_bytes(PNG)
    return fid


def test_legacy_database_is_backed_up_migrated_and_adopted(tmp_path):
    config = make_config(tmp_path)
    fid = build_v1_database(config)
    app = create_app(config)
    with CsrfClient(app, base_url="http://localhost") as c:
        state = app.state.chimera
        # 1. automatic backup before migrating
        backup = state.db.last_backup
        assert backup and backup.is_file() and "pre-migration-v1-to-v2" in backup.name
        old = sqlite3.connect(backup)
        assert old.execute("SELECT MAX(version) FROM schema_version").fetchone()[0] == 1
        old.close()
        assert state.db.schema_version() == len(MIGRATIONS)
        # 2. legacy plaintext API key encrypted at startup
        assert state.db.one("SELECT api_key FROM providers WHERE id='p1'")["api_key"].startswith("enc:v1:")
        # 3. first admin adopts everything
        c.get("/api/auth/status")
        me = register(c, "alice").json()
        assert me["role"] == "admin"
        assert me["legacy_notice"] == {"conversations": 1, "prompts": 1, "files": 1, "collections": 1, "providers": 1, "settings": 1}
        assert c.get("/api/conversations").json()[0]["title"] == "Old chat"
        assert c.get("/api/conversations/c1").json()["messages"][0]["content"] == "legacy hello"
        assert c.get("/api/prompts").json()[0]["title"] == "Old prompt"
        assert c.get("/api/collections").json()[0]["name"] == "Old docs"
        assert c.get("/api/settings").json()["theme"] == "dark"
        assert [p["name"] for p in c.get("/api/providers").json()] == ["Old"]  # no extra seeded connection
        # the legacy image moved into the owner's directory and is still served
        assert (config.uploads_dir / me["id"] / fid).is_file()
        assert c.get(f"/api/files/{fid}/content").content == PNG
        # the notice can be dismissed and is shown only once
        assert c.post("/api/account/notice/dismiss").status_code == 204
        assert c.get("/api/auth/me").json()["legacy_notice"] is None
        # 4. later users start empty
        c.patch("/api/admin/settings", json={"registration_mode": "open"})
        bob = new_client(app)
        register(bob, "bob")
        assert bob.get("/api/conversations").json() == []
        assert bob.get("/api/prompts").json() == []
        assert bob.get("/api/conversations/c1").status_code == 404


def test_migration_is_idempotent_and_backup_only_once(tmp_path):
    config = make_config(tmp_path)
    build_v1_database(config)
    with CsrfClient(create_app(config), base_url="http://localhost"):
        pass
    app = create_app(config)
    with CsrfClient(app, base_url="http://localhost"):
        assert app.state.chimera.db.last_backup is None  # already current: no new backup
    assert len(list(config.backups_dir.iterdir())) == 1


def test_fresh_install_needs_no_backup(tmp_path):
    config = make_config(tmp_path)
    app = create_app(config)
    with CsrfClient(app, base_url="http://localhost") as c:
        assert app.state.chimera.db.last_backup is None
        c.get("/api/auth/status")
        me = register(c, "alice").json()
        assert me["legacy_notice"] is None
