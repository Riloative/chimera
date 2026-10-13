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
