"""Headless browser loader: the egress filter proxy (SSRF rules for every
browser connection) and the loader integration (automatic fallback for
JavaScript pages, "Render with browser", limits, failures, cancellation),
with a fake browser service that talks through the real proxy."""

from __future__ import annotations

import asyncio
import base64
import time

import httpx
import pytest

from chimera.web.browser import BrowserClient
from chimera.web.egress import EgressProxy, EgressRules
from chimera.web.netguard import Allowlist
from tests.conftest import FAKE_PUBLIC_IP, FakeResolver, LiveServer
from tests.mock_browser import SECRET, create_browser_app
from tests.test_web_chat import detail, final, run_and_stop, send

SPA = "http://news.example.org/spa"


def map_connector(web_port: int):
    async def connect(host: str, port: int):
        if host == FAKE_PUBLIC_IP and port == 80:
            return await asyncio.open_connection("127.0.0.1", web_port)
        if host == "127.0.0.1" and port == web_port:  # an administrator-allow-listed internal host
            return await asyncio.open_connection("127.0.0.1", web_port)
        raise OSError("unreachable in tests")

    return connect


# --------------------------------------------------------------- the proxy
class ProxyEnv:
    def __init__(self, web_server):
        self.resolver = FakeResolver()
        self.proxy = EgressProxy(resolver=self.resolver, connector=map_connector(web_server.port))
        self.web = web_server


async def _with_proxy(web_server, fn, rules: EgressRules | None = None):
    env = ProxyEnv(web_server)
    await env.proxy.start("127.0.0.1", 0)
    try:
        async with env.proxy.session(rules or EgressRules()) as session:
            return await fn(env, session)
    finally:
        await env.proxy.stop()


def proxied(env, token: str | None):
    auth = f"render:{token}@" if token else ""
    return httpx.AsyncClient(proxy=f"http://{auth}127.0.0.1:{env.proxy.port}", trust_env=False, timeout=5)


async def raw(env, data: bytes) -> bytes:
    reader, writer = await asyncio.open_connection("127.0.0.1", env.proxy.port)
    writer.write(data)
    await writer.drain()
    out = await asyncio.wait_for(reader.read(65536), 5)
    writer.close()
    return out


def basic(token: str) -> str:
    return "Basic " + base64.b64encode(f"render:{token}".encode()).decode()


def test_proxy_forwards_allowed_pages_with_pinned_dns(web_server):
    async def run(env, session):
        async with proxied(env, session.token) as client:
            resp = await client.get("http://news.example.org/article")
        assert resp.status_code == 200 and "Aurora" in resp.text
        assert env.resolver.calls == ["news.example.org"]
        assert session.hosts == ["news.example.org"]

    asyncio.run(_with_proxy(web_server, run))


@pytest.mark.parametrize("token", [None, "wrong"])
def test_proxy_requires_the_session_credentials(web_server, token):
    async def run(env, session):
        async with proxied(env, token) as client:
            resp = await client.get("http://news.example.org/article")
        assert resp.status_code == 407 and "proxy-authenticate" in resp.headers

    asyncio.run(_with_proxy(web_server, run))


def test_credentials_stop_working_when_the_render_ends(web_server):
    async def run():
        env = ProxyEnv(web_server)
        await env.proxy.start("127.0.0.1", 0)
        try:
            async with env.proxy.session(EgressRules()) as session:
                token = session.token
            async with proxied(env, token) as client:
                assert (await client.get("http://news.example.org/article")).status_code == 407
        finally:
            await env.proxy.stop()

    asyncio.run(run())


@pytest.mark.parametrize(("url", "code"), [
    ("http://127.0.0.1/", "web.blocked_address"),
    ("http://10.0.0.5/", "web.blocked_address"),
    ("http://169.254.169.254/latest/meta-data/", "web.blocked_address"),
    ("http://0x7f000001/", "web.blocked_address"),
    ("http://localhost/", "web.blocked_address"),
    ("http://router.lan/", "web.blocked_address"),
    ("http://news.example.org:8080/", "web.blocked_port"),
    ("http://rebind.example.org/", "web.blocked_address"),
    ("http://blocked.example.net/", "web.domain_blocked"),
])
def test_proxy_blocks_what_the_fetcher_blocks(web_server, url, code):
    async def run(env, session):
        env.resolver.overrides["rebind.example.org"] = ["93.184.215.20", "192.168.1.10"]
        async with proxied(env, session.token) as client:
            resp = await client.get(url)
        assert resp.status_code == 403 and resp.headers["x-chimera-block"] == code
        assert session.blocked[-1]["code"] == code
        assert not web_server.app.state.hits

    web_server.reset()
    asyncio.run(_with_proxy(web_server, run, EgressRules(domain_denylist=["blocked.example.net"])))


def test_connect_tunnels_are_validated_too(web_server):
    async def run(env, session):
        blocked = await raw(env, f"CONNECT 10.0.0.1:443 HTTP/1.1\r\nHost: 10.0.0.1:443\r\nProxy-Authorization: {basic(session.token)}\r\n\r\n".encode())
        assert blocked.startswith(b"HTTP/1.1 403") and b"web.blocked_address" in blocked
        v6 = await raw(env, f"GET http://[::1]/ HTTP/1.1\r\nHost: [::1]\r\nProxy-Authorization: {basic(session.token)}\r\n\r\n".encode())
        assert v6.startswith(b"HTTP/1.1 403") and b"web.blocked_address" in v6
        mapped = await raw(env, f"CONNECT [::ffff:127.0.0.1]:443 HTTP/1.1\r\nProxy-Authorization: {basic(session.token)}\r\n\r\n".encode())
        assert mapped.startswith(b"HTTP/1.1 403") and b"web.blocked_address" in mapped
        port = await raw(env, f"CONNECT news.example.org:22 HTTP/1.1\r\nProxy-Authorization: {basic(session.token)}\r\n\r\n".encode())
        assert port.startswith(b"HTTP/1.1 403") and b"web.blocked_port" in port
        # An allowed tunnel (to the plain-HTTP mock, standing in for TLS) works end to end.
        reader, writer = await asyncio.open_connection("127.0.0.1", env.proxy.port)
        writer.write(f"CONNECT news.example.org:80 HTTP/1.1\r\nProxy-Authorization: {basic(session.token)}\r\n\r\n".encode())
        assert (await reader.readuntil(b"\r\n\r\n")).startswith(b"HTTP/1.1 200")
        writer.write(b"GET /article HTTP/1.1\r\nHost: news.example.org\r\nConnection: close\r\n\r\n")
        body = await asyncio.wait_for(reader.read(), 5)
        assert b"Aurora" in body
        writer.close()

    asyncio.run(_with_proxy(web_server, run, EgressRules(allowed_ports=(80, 443))))


def test_a_kept_alive_connection_cannot_reach_another_host(web_server):
    """After one proxied request the connection is closed, so a second
    request on it (for an unvalidated host) never goes anywhere."""

    async def run(env, session):
        auth = basic(session.token)
        data = (f"GET http://news.example.org/article HTTP/1.1\r\nHost: news.example.org\r\nProxy-Authorization: {auth}\r\n\r\n"
                f"GET http://10.0.0.1/ HTTP/1.1\r\nHost: 10.0.0.1\r\nProxy-Authorization: {auth}\r\n\r\n").encode()
        reader, writer = await asyncio.open_connection("127.0.0.1", env.proxy.port)
        writer.write(data)
        await writer.drain()
        out = await asyncio.wait_for(reader.read(), 5)  # until the proxy closes
        writer.close()
        assert out.count(b"HTTP/1.1 ") == 1 and b"Aurora" in out
        assert web_server.app.state.hits[("news.example.org", "/article")] == 1

    web_server.reset()
    asyncio.run(_with_proxy(web_server, run))


def test_proxy_limits_connections_and_closes_tunnels_at_the_end(web_server):
    async def run():
        env = ProxyEnv(web_server)
        await env.proxy.start("127.0.0.1", 0)
        try:
            async with env.proxy.session(EgressRules(max_connections=2)) as session:
                async with proxied(env, session.token) as client:
                    assert (await client.get("http://news.example.org/article")).status_code == 200
                    assert (await client.get("http://news.example.org/article")).status_code == 200
                    assert (await client.get("http://news.example.org/article")).status_code == 429
            async with env.proxy.session(EgressRules()) as session:
                reader, writer = await asyncio.open_connection("127.0.0.1", env.proxy.port)
                writer.write(f"CONNECT news.example.org:80 HTTP/1.1\r\nProxy-Authorization: {basic(session.token)}\r\n\r\n".encode())
                assert (await reader.readuntil(b"\r\n\r\n")).startswith(b"HTTP/1.1 200")  # tunnel open, idle
            # The render ended: its open tunnel is cut at once (EOF long before the idle timeout).
            assert await asyncio.wait_for(reader.read(), 2) == b""
            writer.close()
            assert not env.proxy.sessions
        finally:
            await env.proxy.stop()

    asyncio.run(run())


# ------------------------------------------------------------- the loader
class BrowserEnv:
    def __init__(self, server, client: BrowserClient):
        self.server, self.client = server, client

    @property
    def state(self):
        return self.server.app.state


@pytest.fixture(scope="session")
def browser_server():
    server = LiveServer(create_browser_app()).start()
    yield server
    server.stop()


@pytest.fixture()
def browser(client, web, browser_server):
    state = browser_server.app.state
    state.renders.clear()
    state.delay, state.cancelled, state.fail = 0.0, 0, None
    proxy = EgressProxy(resolver=web.resolver, connector=map_connector(web.server.port))
    bc = BrowserClient(browser_server.url, SECRET, proxy)
    client.portal.call(proxy.start, "127.0.0.1", 0)
    state.proxy_port = proxy.port
    web.state.web.browser = bc
    yield BrowserEnv(browser_server, bc)
    web.state.web.browser = None
    client.portal.call(proxy.stop)
    client.portal.call(bc.aclose)


def only_source(client, cid):
    [source] = detail(client, cid)["web_sources"]
    return source


def test_javascript_page_is_rendered_automatically(client, conversation, web, browser):
    msg = final(send(client, conversation["id"], f"What does {SPA} say?"))
    assert msg["status"] == "complete"
    source = only_source(client, conversation["id"])
    assert source["status"] == "ok" and source["loader"] == "browser"
    assert "42 new members" in client.get(f"/api/web/sources/{source['id']}").json()["content"]
    assert browser.state.renders == [SPA]
    assert not browser.client.proxy.sessions  # the render's credentials are gone


def test_static_pages_never_start_the_browser(client, conversation, web, browser):
    final(send(client, conversation["id"], "Read http://news.example.org/article"))
    assert only_source(client, conversation["id"])["loader"] == "static"
    assert browser.state.renders == []


def test_automatic_rendering_can_be_turned_off(client, conversation, web, browser):
    client.patch("/api/admin/web", json={"settings": {"browser_auto": False}})
    final(send(client, conversation["id"], f"Read {SPA}"))
    source = only_source(client, conversation["id"])
    assert source["error_code"] == "web.needs_javascript" and browser.state.renders == []
    # …but it can still be rendered on request.
    resp = client.post(f"/api/web/sources/{source['id']}/refetch", json={"loader": "browser"}).json()
    assert resp["ok"] is True and resp["source"]["loader"] == "browser" and resp["source"]["status"] == "ok"


def test_render_with_browser_on_request(client, conversation, web, browser):
    final(send(client, conversation["id"], "Read http://news.example.org/article"))
    source = only_source(client, conversation["id"])
    resp = client.post(f"/api/web/sources/{source['id']}/refetch", json={"loader": "browser"}).json()
    assert resp["ok"] and resp["source"]["loader"] == "browser"
    assert browser.state.renders == ["http://news.example.org/article"]
    assert client.get("/api/web/status").json()["browser_available"] is True


def test_unavailable_browser_degrades_gracefully(client, conversation, web, browser, browser_server):
    browser.client.url = "http://127.0.0.1:9"  # nothing listens there
    final(send(client, conversation["id"], f"Read {SPA}"))
    source = only_source(client, conversation["id"])
    assert source["error_code"] == "web.needs_javascript"
    assert source["error_params"]["browser"] == "web.browser_unavailable"
    assert client.get("/api/web/status").json()["browser_available"] is False
    resp = client.post(f"/api/web/sources/{source['id']}/refetch", json={"loader": "browser"})
    assert resp.status_code == 503 and resp.json()["detail"]["code"] == "web.browser_unavailable"
    view = client.get("/api/admin/web").json()
    assert view["browser"]["configured"] is True and view["browser"]["reachable"] is False


def test_without_a_browser_service_nothing_changes(client, conversation, web):
    final(send(client, conversation["id"], f"Read {SPA}"))
    assert only_source(client, conversation["id"])["error_code"] == "web.needs_javascript"
    assert client.get("/api/web/status").json()["browser_available"] is False
    assert client.get("/api/admin/web").json()["browser"] == {"configured": False, "reachable": False}


def test_internal_targets_are_refused_before_and_inside_the_browser(client, conversation, web, browser):
    # The address itself: refused before the browser is asked.
    final(send(client, conversation["id"], "Read http://news.example.org/article"))
    source = only_source(client, conversation["id"])
    with client.app.state.chimera.db.transaction() as conn:
        conn.execute("UPDATE web_sources SET url = 'http://127.0.0.1/x' WHERE id = ?", (source["id"],))
    resp = client.post(f"/api/web/sources/{source['id']}/refetch", json={"loader": "browser"}).json()
    assert resp["ok"] is False and resp["error_code"] == "web.blocked_address"
    assert browser.state.renders == []
    # A redirect to an internal address: the proxy refuses it.
    target = "http://news.example.org/redirect-to?url=http://10.1.2.3/admin"
    with client.app.state.chimera.db.transaction() as conn:
        conn.execute("UPDATE web_sources SET url = ? WHERE id = ?", (target, source["id"]))
    resp = client.post(f"/api/web/sources/{source['id']}/refetch", json={"loader": "browser"}).json()
    assert resp["ok"] is False and resp["error_code"] == "web.browser_blocked"
    assert resp["error_params"]["reason"] == "web.blocked_address"


def test_hourly_render_limit(client, conversation, web, browser):
    client.patch("/api/admin/web", json={"settings": {"browser_renders_per_hour": 1}})
    final(send(client, conversation["id"], f"Read {SPA}"))
    assert only_source(client, conversation["id"])["loader"] == "browser"
    source = only_source(client, conversation["id"])
    resp = client.post(f"/api/web/sources/{source['id']}/refetch", json={"loader": "browser"}).json()
    assert resp["ok"] is False and resp["error_code"] == "web.browser_limit" and resp["error_params"]["reason"] == "quota"


@pytest.mark.parametrize("code", ["web.browser_timeout", "web.browser_limit", "web.browser_blocked"])
def test_service_errors_are_reported_with_their_codes(client, conversation, web, browser, code):
    browser.state.fail = {"code": code, "message": "x", "params": {}}
    final(send(client, conversation["id"], "Read http://news.example.org/article"))
    source = only_source(client, conversation["id"])
    resp = client.post(f"/api/web/sources/{source['id']}/refetch", json={"loader": "browser"}).json()
    assert resp["ok"] is False and resp["error_code"] == code


def test_stop_cancels_the_render_and_revokes_its_proxy_access(client, conversation, web, browser):
    browser.state.delay = 20
    msg = run_and_stop(client, conversation["id"], {"content": f"Read {SPA}"},
                       lambda m: bool(browser.state.renders))
    assert msg["status"] == "cancelled"
    deadline = time.time() + 5
    while browser.state.cancelled == 0 and time.time() < deadline:
        time.sleep(0.05)
    assert browser.state.cancelled == 1  # the service saw the disconnect and stopped
    assert not browser.client.proxy.sessions


def test_other_users_cannot_render_my_sources(client, conversation, web, browser, make_user):
    final(send(client, conversation["id"], "Read http://news.example.org/article"))
    source = only_source(client, conversation["id"])
    bob = make_user("bob")
    resp = bob.post(f"/api/web/sources/{source['id']}/refetch", json={"loader": "browser"})
    assert resp.status_code == 404 and browser.state.renders == []


def test_admin_settings_validate(client, web):
    assert client.patch("/api/admin/web", json={"settings": {"browser_timeout_seconds": 1}}).status_code == 422
    ok = client.patch("/api/admin/web", json={"settings": {"browser_enabled": False, "browser_timeout_seconds": 30}})
    assert ok.status_code == 200 and ok.json()["settings"]["browser_enabled"] is False


def test_allowlisted_internal_hosts_stay_reachable_through_the_proxy(web_server):
    async def run(env, session):
        async with proxied(env, session.token) as client:
            resp = await client.get("http://news.example.org/article")
        assert resp.status_code == 200

    rules = EgressRules(allowlist=Allowlist(["10.0.0.0/8"]))
    asyncio.run(_with_proxy(web_server, run, rules))
