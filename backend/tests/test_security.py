from fastapi.testclient import TestClient

from chimera.main import create_app

from .conftest import make_config


def test_mutations_require_csrf_header(config):
    with TestClient(create_app(config), base_url="http://localhost") as c:
        resp = c.post("/api/conversations", json={})
        assert resp.status_code == 403 and resp.json()["code"] == "csrf"
        assert c.get("/api/conversations").status_code == 200  # reads are fine
        ok = c.post("/api/conversations", json={}, headers={"X-Chimera-Request": "1"})
        assert ok.status_code == 201


def test_cross_origin_requests_blocked(client):
    resp = client.post("/api/conversations", json={}, headers={"Origin": "http://evil.example"})
    assert resp.status_code == 403


def test_dns_rebinding_host_rejected(config):
    with TestClient(create_app(config), base_url="http://evil.example") as c:
        resp = c.get("/api/health")
        assert resp.status_code == 400 and resp.json()["code"] == "host_not_allowed"


def test_remote_clients_forbidden_without_password(config):
    with TestClient(create_app(config), base_url="http://localhost", client=("192.168.1.20", 5000)) as c:
        resp = c.get("/api/conversations")
        assert resp.status_code == 403 and resp.json()["code"] == "remote_forbidden"


def test_remote_allowed_when_explicitly_configured(tmp_path):
    cfg = make_config(tmp_path, allow_unauthenticated_remote=True)
    with TestClient(create_app(cfg), base_url="http://localhost", client=("192.168.1.20", 5000)) as c:
        assert c.get("/api/conversations").status_code == 200


def test_password_auth_flow(tmp_path):
    cfg = make_config(tmp_path, password="correct horse", allowed_hosts=["*"])
    headers = {"X-Chimera-Request": "1"}
    with TestClient(create_app(cfg), base_url="http://chimera.lan", client=("192.168.1.20", 5000), headers=headers) as c:
        assert c.get("/api/health").status_code == 200
        assert c.get("/api/auth/status").json() == {"auth_required": True, "authenticated": False}
        assert c.get("/api/conversations").status_code == 401
        assert c.post("/api/auth/login", json={"password": "nope"}).status_code == 401
        resp = c.post("/api/auth/login", json={"password": "correct horse"})
        assert resp.status_code == 200
        cookie = resp.headers["set-cookie"].lower()
        assert "httponly" in cookie and "samesite=strict" in cookie
        assert c.get("/api/conversations").status_code == 200
        c.post("/api/auth/logout")
        c.cookies.clear()
        assert c.get("/api/conversations").status_code == 401


def test_login_rate_limited(tmp_path):
    cfg = make_config(tmp_path, password="pw", allowed_hosts=["*"])
    with TestClient(create_app(cfg), base_url="http://localhost", headers={"X-Chimera-Request": "1"}) as c:
        codes = [c.post("/api/auth/login", json={"password": "bad"}).status_code for _ in range(6)]
        assert codes[:5] == [401] * 5 and codes[5] == 429


def test_tampered_session_rejected(tmp_path):
    cfg = make_config(tmp_path, password="pw", allowed_hosts=["*"])
    with TestClient(create_app(cfg), base_url="http://localhost", headers={"X-Chimera-Request": "1"}) as c:
        c.post("/api/auth/login", json={"password": "pw"})
        token = c.cookies.get("chimera_session")
        assert c.get("/api/conversations").status_code == 200
        c.cookies.clear()
        c.cookies.set("chimera_session", token[:-2] + ("00" if not token.endswith("00") else "11"))
        assert c.get("/api/conversations").status_code == 401


def test_api_key_never_returned(client, mock):
    created = client.post(
        "/api/providers", json={"name": "Remote", "kind": "openai", "base_url": mock.url, "api_key": "sk-secret-123"}
    ).json()
    assert "api_key" not in created and created["has_api_key"] is True
    listing = client.get("/api/providers").text
    assert "sk-secret-123" not in listing
    export = client.get("/api/data/export").text
    assert "sk-secret-123" not in export
    # omitted key keeps the stored one; empty string clears it
    assert client.patch(f"/api/providers/{created['id']}", json={"name": "R2"}).json()["has_api_key"] is True
    assert client.patch(f"/api/providers/{created['id']}", json={"api_key": ""}).json()["has_api_key"] is False


def test_security_headers_present(client):
    resp = client.get("/api/health")
    assert "default-src 'self'" in resp.headers["content-security-policy"]
    assert resp.headers["x-content-type-options"] == "nosniff"
    assert resp.headers["x-frame-options"] == "DENY"
    assert resp.headers["cache-control"] == "no-store"


def test_provider_url_validated(client):
    resp = client.post("/api/providers", json={"name": "x", "kind": "llamacpp", "base_url": "file:///etc/passwd"})
    assert resp.status_code == 422
    resp = client.post("/api/providers", json={"name": "x", "kind": "llamacpp", "base_url": "http://ok", "auth_header": "X\r\nInjected: 1"})
    assert resp.status_code == 422


def test_unknown_api_route_is_json_404(client):
    resp = client.get("/api/does-not-exist")
    assert resp.status_code == 404 and resp.json()["detail"] == "Not found"
