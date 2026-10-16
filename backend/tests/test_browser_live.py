"""The real browser service (``browser/chimera_browser.py``) with real
Chromium, talking through Chimera's real egress proxy to the mock web.

Needs a Python with Playwright and its Chromium (the Docker image has both).
Set ``CHIMERA_TEST_BROWSER_PYTHON`` to that interpreter, or create
``browser/.venv`` (see browser/README.md); otherwise these tests are skipped.
"""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import httpx
import pytest

from chimera.web.browser import BrowserClient
from chimera.web.egress import EgressProxy, EgressRules
from chimera.web.errors import WebError
from chimera.web.extract import extract_page
from tests.conftest import FakeResolver, free_port
from tests.test_web_browser import map_connector

ROOT = Path(__file__).resolve().parents[2]
SECRET = "live-test-secret-0123456789abcdef"


def browser_python() -> str | None:
    candidate = os.environ.get("CHIMERA_TEST_BROWSER_PYTHON") or str(ROOT / "browser" / ".venv" / "bin" / "python")
    if not Path(candidate).exists():
        return None
    ok = subprocess.run([candidate, "-c", "import playwright"], capture_output=True).returncode == 0
    return candidate if ok else None


PYTHON = browser_python()
pytestmark = pytest.mark.skipif(PYTHON is None, reason="no Python with Playwright (set CHIMERA_TEST_BROWSER_PYTHON)")


class Live:
    """Egress proxy on a background event loop + the browser service process."""

    def __init__(self, web_server):
        self.web = web_server
        self.loop = asyncio.new_event_loop()
        self.thread = threading.Thread(target=self.loop.run_forever, daemon=True)
        self.thread.start()
        self.resolver = FakeResolver()
        self.proxy = EgressProxy(resolver=self.resolver, connector=map_connector(web_server.port))
        self.call(self.proxy.start("127.0.0.1", 0))
        self.port = free_port()
        env = {
            **os.environ,
            "BROWSER_SECRET": SECRET,
            "BROWSER_PROXY_SERVER": f"http://127.0.0.1:{self.proxy.port}",
            "BROWSER_HOST": "127.0.0.1",
            "BROWSER_PORT": str(self.port),
            "BROWSER_SETTLE_MS": "3000",
            "BROWSER_LOG_LEVEL": "WARNING",
        }
        self.process = subprocess.Popen([PYTHON, str(ROOT / "browser" / "chimera_browser.py")], env=env)
        self.url = f"http://127.0.0.1:{self.port}"
        deadline = time.time() + 60
        while time.time() < deadline:
            try:
                if httpx.get(f"{self.url}/health", timeout=1).status_code == 200:
                    break
            except httpx.HTTPError:
                time.sleep(0.2)
        else:
            self.close()
            raise RuntimeError("browser service did not start")
        self.client = BrowserClient(self.url, SECRET, self.proxy)

    def call(self, coro, timeout: float = 90):
        return asyncio.run_coroutine_threadsafe(coro, self.loop).result(timeout)

    def render(self, url: str, rules: EgressRules | None = None, timeout: float = 20):
        return self.call(self.client.render(url, rules or EgressRules(), timeout=timeout, user_agent="ChimeraTest"))

    def status(self) -> dict:
        return httpx.get(f"{self.url}/status", headers={"Authorization": f"Bearer {SECRET}"}, timeout=5).json()

    def close(self) -> None:
        self.process.terminate()
        try:
            self.process.wait(10)
        except subprocess.TimeoutExpired:
            self.process.kill()
        self.call(self.proxy.stop())
        self.loop.call_soon_threadsafe(self.loop.stop)


@pytest.fixture(scope="module")
def live(web_server):
    env = Live(web_server)
    yield env
    env.close()


def renderers() -> int:
    count = 0
    for pid in os.listdir("/proc"):
        if pid.isdigit():
            try:
                if b"--type=renderer" in Path(f"/proc/{pid}/cmdline").read_bytes():
                    count += 1
            except OSError:
                pass
    return count


def text_of(result) -> str:
    return extract_page(result.fetch, 100_000).markdown.replace("\\", "")


def test_javascript_is_rendered(live):
    result = live.render("http://news.example.org/spa")
    assert "42 new members" in text_of(result)
    assert result.fetch.final_url == "http://news.example.org/spa"
    assert not live.proxy.sessions


def test_page_cannot_reach_internal_addresses_or_use_dangerous_apis(live):
    live.web.reset()
    service = f"http://127.0.0.1:{live.port}/status"
    result = live.render(f"http://news.example.org/spa-probe?local={service}")
    text = text_of(result)
    assert "rtc:undefined" in text and "popup:null" in text and "sw:registered" not in text and "ws:open" not in text
    blocked = {b["host"] for b in result.blocked}
    # Loopback traffic went to the proxy (not around it) and was refused there, like private ranges.
    assert {"10.11.12.13", "127.0.0.1"} <= blocked
    hits = live.web.app.state.hits
    assert not any(path == "/image.png" for _, path in hits)  # images are not loaded
    assert not any(path == "/article" for _, path in hits)  # the popup never opened
    assert not any(path == "/sw.js" for _, path in hits)  # no service worker


def test_redirect_to_a_private_address_is_blocked(live):
    with pytest.raises(WebError) as err:
        live.render("http://news.example.org/redirect-to?url=http://192.168.0.1/admin")
    assert err.value.code == "web.browser_blocked" and err.value.params["reason"] == "web.blocked_address"


def test_downloads_are_refused(live):
    with pytest.raises(WebError) as err:
        live.render("http://news.example.org/download")
    assert err.value.code == "web.browser_blocked" and err.value.params.get("reason") == "download"


def test_each_render_starts_with_a_clean_profile(live):
    first = text_of(live.render("http://news.example.org/state/set"))
    assert "storage=[leftover]" in first
    second = text_of(live.render("http://news.example.org/state/show"))
    assert "cookie=[]" in second and "storage=[]" in second


def test_slow_pages_time_out(live):
    started = time.monotonic()
    with pytest.raises(WebError) as err:
        live.render("http://news.example.org/hang", timeout=4)
    assert err.value.code == "web.browser_timeout" and time.monotonic() - started < 15
    assert live.status()["active"] == 0


def test_cancelling_closes_the_page_and_its_network_access(live):
    baseline = renderers()
    future = asyncio.run_coroutine_threadsafe(
        live.client.render("http://news.example.org/hang", EgressRules(), timeout=30, user_agent="ChimeraTest"), live.loop)
    deadline = time.time() + 15
    while live.status()["active"] == 0 and time.time() < deadline:
        time.sleep(0.1)
    assert live.status()["active"] == 1 and len(live.proxy.sessions) == 1
    time.sleep(0.5)
    future.cancel()
    deadline = time.time() + 10
    while (live.status()["active"] or renderers() > baseline) and time.time() < deadline:
        time.sleep(0.1)
    assert live.status()["active"] == 0
    assert renderers() <= baseline  # no orphaned renderer process
    assert not live.proxy.sessions


def test_requests_need_the_shared_secret(live):
    assert httpx.post(f"{live.url}/render", json={"url": "http://news.example.org/spa"}, timeout=5).status_code == 401
    assert httpx.get(f"{live.url}/status", headers={"Authorization": "Bearer wrong"}, timeout=5).status_code == 401
    bad = BrowserClient(live.url, "wrong-secret-0123456789", live.proxy)
    with pytest.raises(WebError) as err:
        live.call(bad.render("http://news.example.org/spa", EgressRules(), timeout=10, user_agent="x"))
    assert err.value.code == "web.browser_unavailable"


def test_service_refuses_to_start_without_a_secret():
    env = {**os.environ, "BROWSER_SECRET": "", "BROWSER_PROXY_SERVER": "http://127.0.0.1:1"}
    proc = subprocess.run([PYTHON, str(ROOT / "browser" / "chimera_browser.py")], env=env, capture_output=True, timeout=30)
    assert proc.returncode != 0 and b"BROWSER_SECRET" in proc.stderr


if __name__ == "__main__":  # pragma: no cover
    sys.exit(pytest.main([__file__, "-v"]))
