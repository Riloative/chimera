import io
from pathlib import Path

from fastapi.testclient import TestClient

from chimera.main import create_app

from .conftest import read_sse


def test_settings_defaults_and_validation(client):
    s = client.get("/api/settings").json()
    assert s["theme"] == "system" and s["enter_to_send"] is True and s["params"]["temperature"] is None
    assert client.patch("/api/settings", json={"theme": "neon"}).status_code == 422
    assert client.patch("/api/settings", json={"params": {"temperature": 99}}).status_code == 422
    assert client.patch("/api/settings", json={"unknown_key": 1}).status_code == 422
    s = client.patch("/api/settings", json={"theme": "dark", "params": {"top_p": 0.9}}).json()
    assert s["theme"] == "dark" and s["params"]["top_p"] == 0.9
    # nested patch merges rather than replaces
    s = client.patch("/api/settings", json={"params": {"temperature": 0.7}}).json()
    assert s["params"]["top_p"] == 0.9 and s["params"]["temperature"] == 0.7


def test_settings_persist_across_restart(config):
    for attempt in range(2):
        with TestClient(create_app(config), base_url="http://localhost", headers={"X-Chimera-Request": "1"}) as c:
            if attempt == 0:
                c.patch("/api/settings", json={"theme": "light", "font_size": "lg", "sidebar_collapsed": True})
            else:
                s = c.get("/api/settings").json()
                assert (s["theme"], s["font_size"], s["sidebar_collapsed"]) == ("light", "lg", True)


def test_default_provider_seeded_once(tmp_path):
    from .conftest import make_config

    cfg = make_config(tmp_path, seed_provider_url="http://127.0.0.1:8080")
    with TestClient(create_app(cfg), base_url="http://localhost", headers={"X-Chimera-Request": "1"}) as c:
        providers = c.get("/api/providers").json()
        assert len(providers) == 1 and providers[0]["kind"] == "llamacpp"
        assert providers[0]["status"] == "unknown"  # never claim "connected" before testing
        c.delete(f"/api/providers/{providers[0]['id']}")
    with TestClient(create_app(cfg), base_url="http://localhost", headers={"X-Chimera-Request": "1"}) as c:
        assert c.get("/api/providers").json() == []  # deleting it is respected


def test_model_discovery_and_cache(client, provider, mock):
    data = client.get("/api/models", params={"refresh": True}).json()
    entry = data["providers"][0]
    assert [m["id"] for m in entry["models"]] == mock.app.state.models
    assert entry["provider"]["status"] == "connected"
    # server goes away: cached list is returned and flagged stale
    client.patch(f"/api/providers/{provider['id']}", json={"manual_models": ["extra-model"]})
    mock_url = provider["base_url"]
    client.patch(f"/api/providers/{provider['id']}", json={"base_url": "http://127.0.0.1:9"})
    entry = client.get("/api/models", params={"refresh": True}).json()["providers"][0]
    assert entry["error"] and entry["provider"]["status"] == "error"
    assert [m["id"] for m in entry["models"]] == ["extra-model"]  # changed URL invalidates the cache
    client.patch(f"/api/providers/{provider['id']}", json={"base_url": mock_url})
    entry = client.get("/api/models", params={"refresh": True}).json()["providers"][0]
    assert {m["id"] for m in entry["models"]} == {*mock.app.state.models, "extra-model"}
    assert next(m for m in entry["models"] if m["id"] == "extra-model")["source"] == "manual"


def test_disabled_provider_has_no_models(client, provider):
    client.patch(f"/api/providers/{provider['id']}", json={"enabled": False})
    entry = client.get("/api/models", params={"refresh": True}).json()["providers"][0]
    assert entry["models"] == [] and entry["provider"]["status"] == "disabled"


def test_unsaved_connection_test(client, mock):
    ok = client.post("/api/providers/test", json={"kind": "llamacpp", "base_url": mock.url}).json()
    assert ok["ok"] and ok["models"]
    bad = client.post("/api/providers/test", json={"kind": "llamacpp", "base_url": "http://127.0.0.1:9"}).json()
    assert not bad["ok"] and bad["error"]


def test_prompts_crud(client):
    p = client.post("/api/prompts", json={"title": "Reviewer", "category": "Code", "content": "Review this code."}).json()
    assert client.get("/api/prompts").json()[0]["title"] == "Reviewer"
    upd = client.put(f"/api/prompts/{p['id']}", json={"title": "Strict reviewer", "category": "Code", "content": "Be strict."}).json()
    assert upd["title"] == "Strict reviewer"
    assert client.delete(f"/api/prompts/{p['id']}").status_code == 204
    assert client.get("/api/prompts").json() == []


def test_export_import_roundtrip(client, conversation):
    with client.stream("POST", f"/api/conversations/{conversation['id']}/messages", json={"content": "keep this"}) as r:
        read_sse(r)
    client.post("/api/prompts", json={"title": "P", "content": "c"})
    exported = client.get("/api/data/export")
    assert exported.headers["content-disposition"].startswith("attachment")
    payload = exported.content
    client.post("/api/data/clear", json={"scope": "conversations", "confirm": "DELETE"})
    assert client.get("/api/conversations").json() == []
    result = client.post("/api/data/import", files={"file": ("e.json", io.BytesIO(payload), "application/json")}).json()
    assert result["conversations"] == 1 and result["messages"] == 2 and result["skipped"] == 1  # prompt existed
    msgs = client.get(f"/api/conversations/{conversation['id']}").json()["messages"]
    assert msgs[0]["content"] == "keep this"
    bad = client.post("/api/data/import", files={"file": ("e.json", io.BytesIO(b'{"x":1}'), "application/json")})
    assert bad.status_code == 422


def test_backup_and_restore(client, conversation, tmp_path: Path):
    backup = client.get("/api/data/backup")
    assert backup.status_code == 200 and backup.content.startswith(b"SQLite format 3")
    client.delete(f"/api/conversations/{conversation['id']}")
    assert client.get("/api/conversations").json() == []
    resp = client.post("/api/data/restore", files={"file": ("b.sqlite", io.BytesIO(backup.content), "application/octet-stream")})
    assert resp.status_code == 200
    assert client.get("/api/conversations").json()[0]["id"] == conversation["id"]
    junk = client.post("/api/data/restore", files={"file": ("b.sqlite", io.BytesIO(b"junk"), "application/octet-stream")})
    assert junk.status_code == 422


def test_clear_requires_confirmation(client, conversation):
    assert client.post("/api/data/clear", json={"scope": "all"}).status_code == 422
    assert client.post("/api/data/clear", json={"scope": "all", "confirm": "DELETE"}).status_code == 200
    assert client.get("/api/providers").json() == [] and client.get("/api/conversations").json() == []


def test_unsaved_test_can_reuse_stored_key(client, mock):
    mock.app.state.api_key = "k1"
    p = client.post("/api/providers", json={"name": "K", "kind": "llamacpp", "base_url": mock.url, "api_key": "k1"}).json()
    without = client.post("/api/providers/test", json={"kind": "llamacpp", "base_url": mock.url}).json()
    assert not without["ok"]
    reused = client.post("/api/providers/test", json={"kind": "llamacpp", "base_url": mock.url, "provider_id": p["id"]}).json()
    assert reused["ok"]


def test_dotenv_parsing_and_precedence(tmp_path, monkeypatch):
    from chimera.config import load_config, read_dotenv

    env_file = tmp_path / ".env"
    env_file.write_text('# comment\nCHIMERA_PORT=9999\nexport CHIMERA_PASSWORD="s3cret pw"\nCHIMERA_LOG_LEVEL=debug # trailing\n')
    assert read_dotenv(env_file) == {"CHIMERA_PORT": "9999", "CHIMERA_PASSWORD": "s3cret pw", "CHIMERA_LOG_LEVEL": "debug"}
    monkeypatch.setenv("CHIMERA_ENV_FILE", str(env_file))
    monkeypatch.setenv("CHIMERA_PORT", "8123")
    cfg = load_config()
    assert cfg.port == 8123 and cfg.password == "s3cret pw" and cfg.allowed_hosts == ["*"]
