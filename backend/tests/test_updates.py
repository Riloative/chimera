"""Opt-in update notices, against a mock GitHub Releases endpoint."""

from __future__ import annotations

import time

import pytest
from fastapi import FastAPI
from fastapi.responses import JSONResponse

from chimera import __version__
from chimera.services.updates import CHECK_INTERVAL, newest_stable

from .conftest import LiveServer


def releases_app() -> FastAPI:
    app = FastAPI()
    app.state.hits = []
    app.state.fail = False
    app.state.releases = [
        {"tag_name": "v9.1.0-rc.1", "prerelease": True, "draft": False, "html_url": "https://github.com/Riloative/chimera/releases/tag/v9.1.0-rc.1"},
        {"tag_name": "v9.0.1", "prerelease": False, "draft": False, "html_url": "https://github.com/Riloative/chimera/releases/tag/v9.0.1"},
        {"tag_name": "v9.0.0", "prerelease": False, "draft": False, "html_url": "https://evil.example/phish"},
        {"tag_name": "v9.9.9", "prerelease": False, "draft": True},
    ]

    @app.get("/releases")
    async def releases():
        return JSONResponse(app.state.releases)

    @app.middleware("http")
    async def record(request, call_next):
        app.state.hits.append({k.lower(): v for k, v in request.headers.items()})
        if app.state.fail:
            return JSONResponse({"message": "rate limited"}, status_code=403)
        return await call_next(request)

    return app


@pytest.fixture(scope="module")
def github():
    server = LiveServer(releases_app()).start()
    yield server
    server.stop()


@pytest.fixture()
def updates(client, github):
    github.app.state.hits.clear()
    github.app.state.fail = False
    state = client.app.state.chimera
    state.updates.url = f"{github.url}/releases"
    return state.updates


def test_newest_stable_ignores_drafts_prereleases_and_foreign_links():
    rels = [
        {"tag_name": "v2.0.0-rc.1", "prerelease": True},
        {"tag_name": "v1.4.0", "html_url": "https://evil.example/x"},
        {"tag_name": "v1.3.0"},
        {"tag_name": "v3.0.0", "draft": True},
        {"tag_name": "garbage"},
    ]
    assert newest_stable(rels, "1.2.0") == {"version": "1.4.0", "url": "https://github.com/Riloative/chimera/releases/tag/v1.4.0"}
    assert newest_stable(rels, "1.4.0") is None
    assert newest_stable(rels, "1.4.0-rc.2") == {"version": "1.4.0", "url": "https://github.com/Riloative/chimera/releases/tag/v1.4.0"}


def test_off_by_default_and_nothing_is_requested(client, updates, github):
    view = client.get("/api/admin/updates").json()
    assert view["enabled"] is False and view["asked"] is False and view["current"] == __version__
    client.portal.call(updates.maybe_check)
    assert github.app.state.hits == []
    assert client.get("/api/info").json()["update"] is None


def test_admin_only(client, updates, make_user):
    bob = make_user()
    assert bob.get("/api/admin/updates").status_code == 404
    assert bob.patch("/api/admin/updates", json={"enabled": True}).status_code == 404
    assert "update" not in bob.get("/api/info").json()


def test_enabling_checks_once_then_at_most_daily(client, updates, github):
    view = client.patch("/api/admin/updates", json={"enabled": True}).json()
    assert view["enabled"] is True and view["asked"] is True
    assert view["available"] == {"version": "9.0.1", "url": "https://github.com/Riloative/chimera/releases/tag/v9.0.1"}
    assert view["last_check"]["ok"] is True
    assert len(github.app.state.hits) == 1
    hit = github.app.state.hits[0]
    assert hit["user-agent"] == "Chimera-update-check" and "cookie" not in hit and "authorization" not in hit
    assert __version__ not in str(hit)  # nothing about this instance is sent
    client.portal.call(updates.maybe_check)
    assert len(github.app.state.hits) == 1  # not due again yet
    assert updates.due(now=time.time() + CHECK_INTERVAL + 1)
    assert client.get("/api/info").json()["update"]["version"] == "9.0.1"


def test_failures_are_silent_for_users_and_visible_to_admins(client, updates, github, make_user):
    github.app.state.fail = True
    view = client.patch("/api/admin/updates", json={"enabled": True}).json()
    assert view["last_check"]["ok"] is False and view["last_check"]["error"]
    assert view["available"] is None
    bob = make_user()
    info = bob.get("/api/info")
    assert info.status_code == 200 and "update" not in info.json()


def test_turning_it_off_stops_checks(client, updates, github):
    client.patch("/api/admin/updates", json={"enabled": True})
    client.patch("/api/admin/updates", json={"enabled": False})
    github.app.state.hits.clear()
    assert not updates.due(now=time.time() + 10 * CHECK_INTERVAL)
    client.portal.call(updates.maybe_check)
    assert github.app.state.hits == []
