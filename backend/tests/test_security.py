import re

from fastapi.routing import APIRoute

from chimera.main import create_app
from chimera.security import PUBLIC_API

from .conftest import CsrfClient, login, make_config, new_client, register


def concrete(path: str) -> str:
    return re.sub(r"\{[^}]+\}", "0123456789abcdef0123456789abcdef", path).replace(":path", "")


def all_routes(routes):
    for r in routes:
        inner = getattr(r, "original_router", None)
        if inner is not None:
            yield from all_routes(inner.routes)
        else:
            yield r


def test_every_api_route_requires_a_session(app, anon):
    """Enumerates the registered routes so new endpoints are covered automatically."""
    checked = 0
    for route in all_routes(app.routes):
        if not isinstance(route, APIRoute) or not route.path.startswith("/api"):
            continue
        path = concrete(route.path)
        for method in route.methods - {"HEAD", "OPTIONS"}:
            resp = anon.request(method, path, json={})
            if route.path in PUBLIC_API:
                assert resp.status_code != 401 or route.path == "/api/auth/login", (method, path)
                continue
            assert resp.status_code == 401, (method, route.path, resp.status_code, resp.text)
            assert resp.json()["detail"]["code"] == "auth_required"
            checked += 1
    assert checked > 50
    # Streaming and inference specifically:
    for method, path in [("POST", "/api/conversations/x/messages"), ("GET", "/api/conversations/x/stream"),
                         ("GET", "/api/models"), ("POST", "/api/generations/x/cancel"), ("POST", "/api/files"),
                         ("POST", "/api/providers/test")]:
        assert anon.request(method, path, json={}).status_code == 401


def test_health_is_public_and_minimal(anon):
    resp = anon.get("/api/health")
    assert resp.status_code == 200 and resp.json() == {"status": "ok"}


def test_dns_rebinding_host_rejected(tmp_path):
    with CsrfClient(create_app(make_config(tmp_path)), base_url="http://evil.example") as c:
        resp = c.get("/api/health")
        assert resp.status_code == 400 and resp.json()["detail"]["code"] == "host_not_allowed"


def test_cross_origin_requests_blocked(client):
    resp = client.post("/api/conversations", json={}, headers={"Origin": "http://evil.example"})
    assert resp.status_code == 403 and resp.json()["detail"]["code"] == "origin_forbidden"


def test_configured_cors_origin_allowed(tmp_path):
    app = create_app(make_config(tmp_path, cors_origins=["http://tools.lan"]))
    with CsrfClient(app, base_url="http://localhost") as c:
        c.get("/api/auth/status")
        register(c, "alice")
        assert c.post("/api/conversations", json={}, headers={"Origin": "http://tools.lan"}).status_code == 201
        pre = c.options("/api/conversations", headers={"Origin": "http://tools.lan", "Access-Control-Request-Method": "POST"})
        assert pre.headers.get("access-control-allow-origin") == "http://tools.lan"
        pre = c.options("/api/conversations", headers={"Origin": "http://evil.example", "Access-Control-Request-Method": "POST"})
        assert "access-control-allow-origin" not in pre.headers


def test_no_cors_headers_by_default(client):
    resp = client.get("/api/conversations", headers={"Origin": "http://evil.example"})
    assert "access-control-allow-origin" not in resp.headers


def test_security_headers_present(anon):
    resp = anon.get("/api/health")
    assert "default-src 'self'" in resp.headers["content-security-policy"]
    assert "frame-ancestors 'none'" in resp.headers["content-security-policy"]
    assert resp.headers["x-content-type-options"] == "nosniff"
    assert resp.headers["x-frame-options"] == "DENY"
    assert resp.headers["referrer-policy"] == "no-referrer"
    assert resp.headers["cache-control"] == "no-store"


def test_forwarded_for_ignored_from_untrusted_peers(app, client):
    """A client can't dodge the per-IP limit by inventing X-Forwarded-For."""
    c = new_client(app)
    limit = app.state.chimera.config.login_max_failures * 4
    for i in range(limit):
        c.post(
            "/api/auth/login", json={"identifier": f"u{i}", "password": "wrong password!!"},
            headers={"X-Forwarded-For": f"10.0.0.{i}"},
        )
    resp = c.post("/api/auth/login", json={"identifier": "x", "password": "y"}, headers={"X-Forwarded-For": "10.9.9.9"})
    assert resp.status_code == 429


def test_forwarded_for_used_from_trusted_proxy(tmp_path):
    from chimera.security import ProxyResolver

    r = ProxyResolver(["127.0.0.1"])
    assert r.client("127.0.0.1", {b"x-forwarded-for": b"203.0.113.9, 127.0.0.1"}) == ("203.0.113.9", False)
    assert r.client("127.0.0.1", {b"x-forwarded-for": b"203.0.113.9", b"x-forwarded-proto": b"https"}) == ("203.0.113.9", True)
    assert r.client("198.51.100.7", {b"x-forwarded-for": b"203.0.113.9"}) == ("198.51.100.7", False)


def test_provider_url_validated(client):
    resp = client.post("/api/providers", json={"name": "x", "kind": "llamacpp", "base_url": "file:///etc/passwd"})
    assert resp.status_code == 422
    resp = client.post("/api/providers", json={"name": "x", "kind": "llamacpp", "base_url": "http://ok", "auth_header": "X\r\nInjected: 1"})
    assert resp.status_code == 422


def test_unknown_api_route_is_json_404(client):
    resp = client.get("/api/does-not-exist")
    assert resp.status_code == 404 and resp.json()["detail"]["code"] == "not_found"


def test_errors_have_stable_codes(client):
    resp = client.get("/api/conversations/nope")
    assert resp.json()["detail"] == {"code": "conversation_not_found", "message": "Conversation not found", "params": {}}
    resp = client.post("/api/prompts", json={"title": ""})
    assert resp.status_code == 422 and resp.json()["detail"]["code"] == "validation_error"


def test_password_never_logged(app, caplog):
    import logging

    caplog.set_level(logging.DEBUG)
    with CsrfClient(app, base_url="http://localhost") as c:
        c.get("/api/auth/status")
        register(c, "alice", password="unique-password-xyz-123")
        login(c, "alice", "unique-password-xyz-123")
        token = c.cookies.get("chimera_session")
    assert "unique-password-xyz-123" not in caplog.text
    assert token not in caplog.text
