"""Chimera headless browser: renders JavaScript-heavy pages for Chimera's web
reader. Runs in its own container on an internal-only network.

Security model (see docs/WEB.md, "Headless browser"):

* Only Chimera can use it: every request needs ``Authorization: Bearer
  <BROWSER_SECRET>``; there are no published ports.
* All browser traffic goes through Chimera's egress filter proxy
  (``BROWSER_PROXY_SERVER``), which applies the same rules as Chimera's own
  fetcher (public addresses only, DNS pinning, allowed ports, domain lists)
  and accepts only the single-use credentials Chimera issues per render.
  Chromium is told to send loopback traffic through the proxy too and to
  resolve no host names itself.
* Every render gets a fresh incognito context (no cookies, storage or cache
  shared with anything else), which is closed when the render ends, fails, or
  the caller disconnects.
* Blocked inside the page: WebRTC, service workers, downloads, popups,
  WebSockets, non-http(s) navigations, and images/media/fonts (not needed for
  text). Requests per page and returned HTML size are capped.

The HTTP server is a deliberately small asyncio implementation so the image
needs nothing beyond Playwright.
"""

from __future__ import annotations

import asyncio
import contextlib
import hmac
import json
import logging
import os
import signal
import time
from typing import Any
from urllib.parse import urlsplit

from playwright.async_api import Browser, Error as PlaywrightError, TimeoutError as PlaywrightTimeout, async_playwright

VERSION = "1.0"
log = logging.getLogger("chimera.browser")

BLOCKED_RESOURCES = {"image", "media", "font"}
MAX_BODY = 64 * 1024
# Runs before any page script: remove APIs that could open side channels.
INIT_SCRIPT = """
(() => {
  for (const name of ["RTCPeerConnection", "webkitRTCPeerConnection", "RTCDataChannel", "RTCSessionDescription", "RTCIceCandidate"]) {
    try { Object.defineProperty(window, name, { value: undefined, configurable: false, writable: false }); } catch (e) {}
  }
  try { Object.defineProperty(window, "open", { value: () => null, configurable: false, writable: false }); } catch (e) {}
  // Playwright's service_workers="block" does not stop navigator.serviceWorker.register()
  // in secure contexts (https, loopback); remove the API itself.
  try { Object.defineProperty(Navigator.prototype, "serviceWorker", { get: () => undefined, configurable: false }); } catch (e) {}
  try {
    if (navigator.mediaDevices) Object.defineProperty(navigator, "mediaDevices", { value: undefined, configurable: false });
  } catch (e) {}
})();
"""


def env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except ValueError:
        return default


class Settings:
    def __init__(self, env: dict[str, str] | None = None):
        env = dict(os.environ if env is None else env)
        self.secret = env.get("BROWSER_SECRET", "")
        self.proxy_server = env.get("BROWSER_PROXY_SERVER", "").strip()
        self.host = env.get("BROWSER_HOST", "0.0.0.0")
        self.port = int(env.get("BROWSER_PORT", "3000"))
        self.max_pages = max(1, int(env.get("BROWSER_MAX_PAGES", "2")))
        self.max_queue = max(0, int(env.get("BROWSER_MAX_QUEUE", "8")))
        self.max_requests = max(10, int(env.get("BROWSER_MAX_REQUESTS", "250")))
        self.max_html_bytes = max(64, int(env.get("BROWSER_MAX_HTML_KB", "5120"))) * 1024
        self.max_timeout = max(5.0, float(env.get("BROWSER_MAX_TIMEOUT_SECONDS", "60")))
        self.settle_ms = max(0, int(env.get("BROWSER_SETTLE_MS", "1500")))
        self.executable = env.get("BROWSER_EXECUTABLE") or None

    def check(self) -> None:
        if len(self.secret) < 16:
            raise SystemExit("BROWSER_SECRET must be set (at least 16 characters); refusing to start.")
        if not self.proxy_server.startswith("http://"):
            raise SystemExit("BROWSER_PROXY_SERVER must be Chimera's egress proxy (http://host:port); refusing to start.")


class RenderError(Exception):
    def __init__(self, code: str, message: str, **params: Any):
        super().__init__(message)
        self.code, self.message, self.params = code, message, params


class Renderer:
    def __init__(self, settings: Settings):
        self.settings = settings
        self._pw = None
        self._browser: Browser | None = None
        self._launch_lock = asyncio.Lock()
        self._slots = asyncio.Semaphore(settings.max_pages)
        self.waiting = 0
        self.active = 0
        self.rendered = 0

    def launch_args(self) -> list[str]:
        proxy_host = urlsplit(self.settings.proxy_server).hostname or ""
        return [
            # Send loopback traffic through the proxy as well (Chromium bypasses
            # proxies for localhost by default), where it is refused.
            "--proxy-bypass-list=<-loopback>",
            # Resolve nothing locally: the proxy resolves (and validates) names.
            f"--host-resolver-rules=MAP * ~NOTFOUND , EXCLUDE {proxy_host}",
            "--force-webrtc-ip-handling-policy=disable_non_proxied_udp",
            "--webrtc-ip-handling-policy=disable_non_proxied_udp",
            "--disable-features=WebRtcHideLocalIpsWithMdns,InterestFeedContentSuggestions,MediaRouter,DialMediaRouteProvider",
            "--disable-background-networking",
            "--disable-component-update",
            "--disable-domain-reliability",
            "--disable-sync",
            "--no-first-run",
            "--no-pings",
            "--mute-audio",
            "--disable-dev-shm-usage",
        ]

    async def browser(self) -> Browser:
        async with self._launch_lock:
            if self._browser is None or not self._browser.is_connected():
                if self._pw is None:
                    self._pw = await async_playwright().start()
                self._browser = await self._pw.chromium.launch(
                    headless=True,
                    executable_path=self.settings.executable,
                    # Every context overrides it with its own credentials; this
                    # makes any traffic outside a context hit the proxy too.
                    proxy={"server": self.settings.proxy_server},
                    args=self.launch_args(),
                )
                log.info("chromium %s started", self._browser.version)
            return self._browser

    async def close(self) -> None:
        with contextlib.suppress(Exception):
            if self._browser:
                await self._browser.close()
        with contextlib.suppress(Exception):
            if self._pw:
                await self._pw.stop()

    async def render(self, job: dict[str, Any]) -> dict[str, Any]:
        url = str(job.get("url") or "")
        if urlsplit(url).scheme.lower() not in ("http", "https"):
            raise RenderError("web.browser_blocked", "Only http and https pages can be rendered.", reason="scheme")
        proxy = job.get("proxy") or {}
        if not proxy.get("username") or not proxy.get("password"):
            raise RenderError("web.browser_failed", "Missing proxy credentials.")
        timeout = min(self.settings.max_timeout, max(2.0, float(job.get("timeout") or 20)))
        if self.waiting >= self.settings.max_queue and self._slots.locked():
            raise RenderError("web.browser_limit", "The browser is busy; try again shortly.", reason="busy")
        self.waiting += 1
        try:
            await asyncio.wait_for(self._slots.acquire(), timeout)
        except TimeoutError as exc:
            raise RenderError("web.browser_limit", "The browser is busy; try again shortly.", reason="busy") from exc
        finally:
            self.waiting -= 1
        self.active += 1
        try:
            async with asyncio.timeout(timeout + 3):
                return await self._render(url, proxy, timeout, job)
        except TimeoutError as exc:
            raise RenderError("web.browser_timeout", "The page took too long to render.") from exc
        finally:
            self.active -= 1
            self._slots.release()

    async def _render(self, url: str, proxy: dict, timeout: float, job: dict[str, Any]) -> dict[str, Any]:
        browser = await self.browser()
        context = await browser.new_context(
            proxy={"server": self.settings.proxy_server, "username": str(proxy["username"]), "password": str(proxy["password"])},
            service_workers="block",
            accept_downloads=False,
            user_agent=str(job.get("user_agent") or "") or None,
            locale=str(job.get("locale") or "") or None,
            extra_http_headers={"Accept-Language": str(job["accept_language"])} if job.get("accept_language") else None,
            viewport={"width": 1280, "height": 900},
            java_script_enabled=True,
            ignore_https_errors=False,
            permissions=[],
        )
        state = {"requests": 0, "blocked": []}
        try:
            context.set_default_timeout(timeout * 1000)
            await context.add_init_script(INIT_SCRIPT)
            await context.route("**/*", lambda route: self._route(route, state))
            if hasattr(context, "route_web_socket"):
                await context.route_web_socket("**/*", lambda ws: ws.close())
            page = await context.new_page()
            # Any other page (popup, target=_blank) is closed immediately.
            context.on("page", lambda p: None if p is page else asyncio.ensure_future(p.close()))
            started = time.monotonic()
            try:
                response = await page.goto(url, wait_until="domcontentloaded", timeout=timeout * 1000)
            except PlaywrightTimeout as exc:
                raise RenderError("web.browser_timeout", "The page took too long to render.") from exc
            except PlaywrightError as exc:
                raise _navigation_error(str(exc)) from exc
            remaining = timeout - (time.monotonic() - started)
            settle = min(self.settings.settle_ms / 1000, max(0.0, remaining - 0.5))
            if settle > 0:
                with contextlib.suppress(PlaywrightError):
                    await page.wait_for_load_state("networkidle", timeout=settle * 1000)
            html = await page.content()
            final_url = page.url
            title = await page.title()
            data = html.encode("utf-8", "replace")
            truncated = len(data) > self.settings.max_html_bytes
            if truncated:
                html = data[: self.settings.max_html_bytes].decode("utf-8", "ignore")
            self.rendered += 1
            return {
                "ok": True,
                "final_url": final_url,
                "status": response.status if response else 200,
                "title": title,
                "html": html,
                "truncated": truncated,
                "requests": state["requests"],
                "blocked": state["blocked"][:20],
            }
        finally:
            with contextlib.suppress(Exception):
                await context.close()

    async def _route(self, route, state: dict) -> None:
        request = route.request
        scheme = urlsplit(request.url).scheme.lower()
        if scheme not in ("http", "https"):
            state["blocked"].append("scheme")
            await route.abort("blockedbyclient")
            return
        if request.resource_type in BLOCKED_RESOURCES:
            await route.abort("blockedbyclient")
            return
        state["requests"] += 1
        if state["requests"] > self.settings.max_requests:
            if "requests" not in state["blocked"]:
                state["blocked"].append("requests")
            await route.abort("blockedbyclient")
            return
        await route.continue_()


def _navigation_error(text: str) -> RenderError:
    low = text.lower()
    if "download is starting" in low:
        return RenderError("web.browser_blocked", "The address starts a download, which is not allowed.", reason="download")
    if "err_tunnel_connection_failed" in low or "err_proxy" in low or "err_blocked_by_client" in low:
        return RenderError("web.browser_blocked", "The page or one of its redirects is not allowed.", reason="blocked")
    if "err_name_not_resolved" in low:
        return RenderError("web.dns_failed", "The site's address could not be found.")
    if "err_cert" in low or "ssl" in low:
        return RenderError("web.tls_error", "The site's security certificate could not be verified.")
    if "err_timed_out" in low or "timeout" in low:
        return RenderError("web.browser_timeout", "The page took too long to render.")
    if "err_connection" in low or "err_empty_response" in low or "err_address_unreachable" in low:
        return RenderError("web.unreachable", "The site could not be reached.")
    return RenderError("web.browser_failed", "The page could not be rendered.")


# ------------------------------------------------------------------ server
class Server:
    def __init__(self, settings: Settings, renderer: Renderer | None = None):
        self.settings = settings
        self.renderer = renderer or Renderer(settings)
        self._server: asyncio.base_events.Server | None = None

    async def start(self) -> None:
        self._server = await asyncio.start_server(self.handle, self.settings.host, self.settings.port, limit=MAX_BODY)
        log.info("listening on %s:%d", self.settings.host, self.settings.port)

    @property
    def port(self) -> int:
        assert self._server
        return self._server.sockets[0].getsockname()[1]

    async def stop(self) -> None:
        if self._server:
            self._server.close()
            await self._server.wait_closed()
        await self.renderer.close()

    def authorized(self, headers: dict[str, str]) -> bool:
        value = headers.get("authorization", "")
        return value.startswith("Bearer ") and hmac.compare_digest(value[7:].encode(), self.settings.secret.encode())

    async def handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            try:
                async with asyncio.timeout(10):
                    method, path, headers, body = await read_request(reader)
            except (ValueError, TimeoutError, asyncio.IncompleteReadError, asyncio.LimitOverrunError):
                await respond(writer, 400, {"ok": False, "code": "bad_request"})
                return
            if method == "GET" and path == "/health":
                await respond(writer, 200, {"ok": True})
                return
            if not self.authorized(headers):
                await respond(writer, 401, {"ok": False, "code": "unauthorized"})
                return
            if method == "GET" and path == "/status":
                r = self.renderer
                await respond(writer, 200, {"ok": True, "version": VERSION, "active": r.active, "waiting": r.waiting,
                                            "rendered": r.rendered, "max_pages": self.settings.max_pages})
                return
            if method != "POST" or path != "/render":
                await respond(writer, 404, {"ok": False, "code": "not_found"})
                return
            try:
                job = json.loads(body or b"{}")
                if not isinstance(job, dict):
                    raise ValueError
            except ValueError:
                await respond(writer, 400, {"ok": False, "code": "bad_request"})
                return
            await self.render_until_disconnect(job, reader, writer)
        except (ConnectionError, asyncio.CancelledError):
            pass
        finally:
            with contextlib.suppress(Exception):
                writer.close()

    async def render_until_disconnect(self, job: dict, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        """Render, but stop (and close the page) as soon as the caller goes away."""
        task = asyncio.create_task(self.renderer.render(job))
        gone = asyncio.create_task(reader.read(1))  # EOF (b"") = Chimera cancelled
        done, _ = await asyncio.wait({task, gone}, return_when=asyncio.FIRST_COMPLETED)
        if task not in done:
            task.cancel()
            with contextlib.suppress(BaseException):
                await task
            log.info("render cancelled by caller")
            return
        gone.cancel()
        try:
            result = task.result()
            await respond(writer, 200, result)
        except RenderError as exc:
            await respond(writer, 200, {"ok": False, "code": exc.code, "message": exc.message, "params": exc.params})
        except Exception:  # noqa: BLE001
            log.exception("render failed")
            await respond(writer, 200, {"ok": False, "code": "web.browser_failed", "message": "The page could not be rendered."})


async def read_request(reader: asyncio.StreamReader) -> tuple[str, str, dict[str, str], bytes]:
    head = await reader.readuntil(b"\r\n\r\n")
    lines = head.decode("latin-1").split("\r\n")
    method, path, _ = lines[0].split(" ", 2)
    headers: dict[str, str] = {}
    for line in lines[1:]:
        if ":" in line:
            key, value = line.split(":", 1)
            headers[key.strip().lower()] = value.strip()
    length = int(headers.get("content-length") or 0)
    if length < 0 or length > MAX_BODY:
        raise ValueError("body too large")
    body = await reader.readexactly(length) if length else b""
    return method.upper(), path.split("?", 1)[0], headers, body


async def respond(writer: asyncio.StreamWriter, status: int, payload: dict) -> None:
    data = json.dumps(payload).encode()
    reason = {200: "OK", 400: "Bad Request", 401: "Unauthorized", 404: "Not Found"}.get(status, "OK")
    writer.write(
        f"HTTP/1.1 {status} {reason}\r\nContent-Type: application/json\r\nContent-Length: {len(data)}\r\n"
        f"Cache-Control: no-store\r\nConnection: close\r\n\r\n".encode() + data
    )
    with contextlib.suppress(ConnectionError):
        await writer.drain()


async def main() -> None:
    logging.basicConfig(level=os.environ.get("BROWSER_LOG_LEVEL", "INFO"), format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    settings = Settings()
    settings.check()
    server = Server(settings)
    await server.renderer.browser()  # fail fast if Chromium can't start
    await server.start()
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop.set)
    await stop.wait()
    await server.stop()


if __name__ == "__main__":
    asyncio.run(main())
