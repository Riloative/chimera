from __future__ import annotations

import json
import socket
import sys
import threading
import time
from pathlib import Path
from typing import Iterator

import pytest
import uvicorn
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from chimera.config import Config  # noqa: E402
from chimera.main import create_app  # noqa: E402
from tests.mock_llama_server import create_mock_app  # noqa: E402


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class LiveServer:
    def __init__(self, app, lifespan: str = "off"):
        self.app = app
        self.port = free_port()
        self.url = f"http://127.0.0.1:{self.port}"
        config = uvicorn.Config(self.app, host="127.0.0.1", port=self.port, log_level="warning", lifespan=lifespan)
        self.server = uvicorn.Server(config)
        self.thread = threading.Thread(target=self.server.run, daemon=True)

    def start(self) -> "LiveServer":
        self.thread.start()
        deadline = time.time() + 10
        while not self.server.started:
            if time.time() > deadline:
                raise RuntimeError("mock server failed to start")
            time.sleep(0.02)
        return self

    def stop(self) -> None:
        self.server.should_exit = True
        self.thread.join(timeout=5)

class MockServer(LiveServer):
    def __init__(self, **kwargs):
        super().__init__(create_mock_app(**kwargs))

    def reset(self) -> None:
        self.app.state.requests.clear()
        self.app.state.cancelled = 0
        self.app.state.completed = 0
        self.app.state.loading = False
        self.app.state.vision = False
        self.app.state.api_key = None


@pytest.fixture(scope="session")
def mock_server() -> Iterator[MockServer]:
    server = MockServer()
    server.start()
    yield server
    server.stop()


@pytest.fixture()
def mock(mock_server: MockServer) -> MockServer:
    mock_server.reset()
    return mock_server


PASSWORD = "correct horse battery"


def make_config(tmp_path: Path, **overrides) -> Config:
    cfg = Config(
        data_dir=tmp_path / "data",
        static_dir=None,
        seed_provider_url="",
        allowed_hosts=["localhost", "127.0.0.1", "testserver"],
        # Cheap hashing keeps the suite fast; production uses RFC 9106 settings.
        argon2_time_cost=1,
        argon2_memory_kib=1024,
        argon2_parallelism=1,
    )
    for key, value in overrides.items():
        setattr(cfg, key, value)
    return cfg


class CsrfClient(TestClient):
    """TestClient that sends the double-submit CSRF header like the web UI."""

    send_csrf = True

    def build_request(self, method, url, **kwargs):  # type: ignore[override]
        token = self.cookies.get("chimera_csrf")
        if token and self.send_csrf:
            headers = dict(kwargs.pop("headers", None) or {})
            headers.setdefault("X-CSRF-Token", token)
            kwargs["headers"] = headers
        return super().build_request(method, url, **kwargs)


_PORTALS: dict[int, object] = {}


def new_client(app, **kwargs) -> CsrfClient:
    """A second browser on the same running app (shares its event loop so
    background generations started by this client keep running)."""
    c = CsrfClient(app, base_url="http://localhost", **kwargs)
    c.portal = _PORTALS.get(id(app))  # type: ignore[assignment]
    c.get("/api/auth/status")  # obtains the anonymous CSRF cookie
    return c


def register(c: TestClient, username: str, email: str | None = None, password: str = PASSWORD, headers=None, **extra):
    return c.post(
        "/api/auth/register",
        json={"email": email or f"{username}@example.com", "username": username, "password": password,
              "password_confirm": password, **extra},
        headers=headers,
    )


def login(c: TestClient, identifier: str, password: str = PASSWORD, headers=None, **extra):
    return c.post("/api/auth/login", json={"identifier": identifier, "password": password, **extra}, headers=headers)


@pytest.fixture()
def config(tmp_path: Path) -> Config:
    return make_config(tmp_path)


@pytest.fixture()
def app(config: Config):
    return create_app(config)


@pytest.fixture()
def anon(app) -> Iterator[CsrfClient]:
    """Runs the app lifespan; not logged in."""
    with CsrfClient(app, base_url="http://localhost") as c:
        _PORTALS[id(app)] = c.portal
        c.get("/api/auth/status")
        yield c
    _PORTALS.pop(id(app), None)


@pytest.fixture()
def client(anon: CsrfClient) -> CsrfClient:
    """Logged in as 'alice', the first account (administrator)."""
    resp = register(anon, "alice")
    assert resp.status_code == 201, resp.text
    return anon


@pytest.fixture()
def make_user(app, client):
    """Create another (non-admin) user and return a client logged in as them."""

    def _make(username: str = "bob") -> CsrfClient:
        resp = client.post(
            "/api/admin/users",
            json={"email": f"{username}@example.com", "username": username, "password": PASSWORD},
        )
        assert resp.status_code == 201, resp.text
        other = new_client(app)
        assert login(other, username).status_code == 200
        return other

    return _make


def read_sse(response) -> list[tuple[str, dict]]:
    events: list[tuple[str, dict]] = []
    event = "message"
    data: list[str] = []
    for line in response.iter_lines():
        if line == "":
            if data:
                events.append((event, json.loads("\n".join(data))))
            event, data = "message", []
        elif line.startswith("event:"):
            event = line[6:].strip()
        elif line.startswith("data:"):
            data.append(line[5:].lstrip())
    if data:
        events.append((event, json.loads("\n".join(data))))
    return events


@pytest.fixture()
def provider(client: TestClient, mock: MockServer) -> dict:
    resp = client.post("/api/providers", json={"name": "Local", "kind": "llamacpp", "base_url": mock.url})
    assert resp.status_code == 201, resp.text
    return resp.json()


@pytest.fixture()
def conversation(client: TestClient, provider: dict, mock: MockServer) -> dict:
    model = mock.app.state.models[0]
    resp = client.post("/api/conversations", json={"provider_id": provider["id"], "model": model})
    assert resp.status_code == 201
    return resp.json()


@pytest.fixture()
def live_app(config: Config) -> Iterator[LiveServer]:
    config.registration_mode = "open"
    server = LiveServer(create_app(config), lifespan="on")
    server.start()
    yield server
    server.stop()


# ------------------------------------------------------------------ mock web
import ipaddress  # noqa: E402

import httpx  # noqa: E402

from chimera.web.fetcher import SafeFetcher, make_client  # noqa: E402
from tests.mock_web import create_web_app  # noqa: E402

# Test host names resolve to this public address; MapTransport then delivers
# the (pinned) connection to the local mock web server instead.
FAKE_PUBLIC_IP = "93.184.215.14"
FAKE_HOSTS = {
    "news.example.org", "www.news.example.org", "docs.example.org", "blocked.example.net", "example.org",
    "raw.githubusercontent.com", "github.com", "www.youtube.com", "youtu.be", "api.search.brave.com", "api.tavily.com",
}


class FakeResolver:
    """Deterministic DNS: FAKE_HOSTS -> public IP; ``overrides`` for special
    cases (e.g. a name that resolves to a private address); counts calls."""

    def __init__(self):
        self.calls: list[str] = []
        self.overrides: dict[str, list] = {}

    async def __call__(self, host: str, port: int):
        self.calls.append(host)
        if host in self.overrides:
            answer = self.overrides[host]
            value = answer(len([h for h in self.calls if h == host])) if callable(answer) else answer
            return [ipaddress.ip_address(a) for a in value]
        if host in FAKE_HOSTS:
            return [ipaddress.ip_address(FAKE_PUBLIC_IP)]
        return []


class MapTransport(httpx.AsyncBaseTransport):
    """Delivers requests for mapped IPs to local ports; records what was asked."""

    def __init__(self, mapping: dict[str, int]):
        self.inner = httpx.AsyncHTTPTransport()
        self.mapping = mapping
        self.requests: list[httpx.Request] = []

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        host = request.url.host
        if host in self.mapping:
            request.url = request.url.copy_with(scheme="http", host="127.0.0.1", port=self.mapping[host])
            request.extensions.pop("sni_hostname", None)
        return await self.inner.handle_async_request(request)

    async def aclose(self) -> None:
        await self.inner.aclose()


class WebServer(LiveServer):
    def __init__(self):
        super().__init__(create_web_app())

    def reset(self) -> None:
        self.app.state.hits.clear()
        self.app.state.json_disabled = False
        self.app.state.searches.clear()


@pytest.fixture(scope="session")
def web_server() -> Iterator[WebServer]:
    server = WebServer()
    server.start()
    yield server
    server.stop()


def make_fetcher(web_server: "WebServer") -> tuple[SafeFetcher, MapTransport, FakeResolver]:
    transport = MapTransport({FAKE_PUBLIC_IP: web_server.port})
    resolver = FakeResolver()
    return SafeFetcher(make_client(transport=transport), resolver=resolver), transport, resolver


class WebEnv:
    def __init__(self, server, transport, resolver, state):
        self.server, self.transport, self.resolver, self.state = server, transport, resolver, state

    @property
    def hits(self):
        return self.server.app.state.hits


@pytest.fixture()
def web(client: CsrfClient, web_server: WebServer) -> Iterator[WebEnv]:
    """Web access enabled (as the admin 'alice') with SearXNG = the mock web
    server; page fetches go through the real SafeFetcher to the mock."""
    web_server.reset()
    state = client.app.state.chimera
    fetcher, transport, resolver = make_fetcher(web_server)
    state.web.fetcher = fetcher
    resp = client.patch("/api/admin/web", json={"settings": {"enabled": True, "engine": "searxng", "searxng_url": web_server.url}})
    assert resp.status_code == 200, resp.text
    yield WebEnv(web_server, transport, resolver, state)
    client.portal.call(fetcher.client.aclose)


def active_messages(detail: dict) -> list[dict]:
    """The messages on a conversation's active branch, in order."""
    by_id = {m["id"]: m for m in detail["messages"]}
    return [by_id[i] for i in detail["active_path"]]
