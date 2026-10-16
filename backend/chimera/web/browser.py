"""Client for the optional headless browser service (``browser/`` in the
repository, ``docker compose --profile browser``).

Chimera asks the service to render a page and gets back the rendered HTML,
which goes through the same extraction as a static fetch. All of the
browser's network traffic is forced through :mod:`.egress`; each render gets
its own single-use proxy credentials, revoked when the render ends.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any

import httpx

from .egress import EgressProxy, EgressRules
from .errors import WebError
from .fetcher import FetchResult, status_error

log = logging.getLogger("chimera.web")

STATUS_TTL = 30.0
DOWN_TTL = 30.0


@dataclass
class RenderResult:
    fetch: FetchResult
    requests: int = 0
    blocked: list[dict] = field(default_factory=list)


class BrowserClient:
    def __init__(self, url: str, secret: str, proxy: EgressProxy, client: httpx.AsyncClient | None = None):
        self.url = url.rstrip("/")
        self.secret = secret
        self.proxy = proxy
        # The browser service is on an internal network: never use env proxies.
        self.client = client or httpx.AsyncClient(trust_env=False, follow_redirects=False)
        self._status: tuple[float, dict[str, Any]] | None = None
        self._down_until = 0.0

    @property
    def headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.secret}"}

    async def status(self, *, fresh: bool = False) -> dict[str, Any]:
        now = time.monotonic()
        if not fresh and self._status and self._status[0] > now:
            return self._status[1]
        try:
            resp = await self.client.get(f"{self.url}/status", headers=self.headers, timeout=5)
            if resp.status_code == 401:
                info = {"reachable": False, "error": "unauthorized"}
            else:
                resp.raise_for_status()
                info = {"reachable": True, **{k: v for k, v in resp.json().items() if k != "ok"}}
        except (httpx.HTTPError, ValueError):
            info = {"reachable": False, "error": "unreachable"}
        info["proxy"] = self.proxy.running
        self._status = (now + STATUS_TTL, info)
        if not info["reachable"]:
            self._down_until = now + DOWN_TTL
        return info

    def known_down(self) -> bool:
        return time.monotonic() < self._down_until

    async def render(self, url: str, rules: EgressRules, *, timeout: float, user_agent: str,
                     accept_language: str | None = None) -> RenderResult:
        if not self.proxy.running:
            raise WebError("web.browser_unavailable", "The headless browser is not available.")
        started = time.perf_counter()
        async with self.proxy.session(rules) as session:
            job = {
                "url": url,
                "proxy": {"username": "render", "password": session.token},
                "timeout": timeout,
                "user_agent": user_agent,
                "accept_language": accept_language,
            }
            try:
                resp = await self.client.post(f"{self.url}/render", json=job, headers=self.headers,
                                              timeout=httpx.Timeout(timeout + 15, connect=5))
            except httpx.TimeoutException as exc:
                raise WebError("web.browser_timeout", "The page took too long to render.") from exc
            except httpx.HTTPError as exc:
                self._down_until = time.monotonic() + DOWN_TTL
                self._status = None
                log.info("browser service unreachable: %s", type(exc).__name__)
                raise WebError("web.browser_unavailable", "The headless browser is not available.") from exc
            if resp.status_code != 200:
                log.warning("browser service answered HTTP %d", resp.status_code)
                raise WebError("web.browser_unavailable", "The headless browser is not available.")
            data = resp.json()
            blocked = list(session.blocked)
        if not data.get("ok"):
            code = str(data.get("code") or "web.browser_failed")
            params = dict(data.get("params") or {})
            if code == "web.browser_blocked" and blocked:
                params.setdefault("host", blocked[0]["host"])
                params.setdefault("reason", blocked[0]["code"])
            if not code.startswith("web."):
                code = "web.browser_failed"
            raise WebError(code, str(data.get("message") or "The page could not be rendered."), **params)
        status = int(data.get("status") or 200)
        final_url = str(data.get("final_url") or url)
        if status >= 400:
            host = _host(final_url)
            hit = next((b for b in blocked if b["host"] == host), None)
            if hit:  # the proxy refused the page itself (e.g. a redirect to a private address)
                raise WebError("web.browser_blocked", "The page or one of its redirects is not allowed.", host=host, reason=hit["code"])
            raise status_error(status)
        html = str(data.get("html") or "")
        result = FetchResult(
            url=url, final_url=final_url, status=status, content_type="text/html", charset="utf-8",
            body=html.encode("utf-8"), elapsed_ms=round((time.perf_counter() - started) * 1000, 1),
        )
        return RenderResult(fetch=result, requests=int(data.get("requests") or 0), blocked=blocked)

    async def aclose(self) -> None:
        await self.client.aclose()


def _host(url: str) -> str:
    try:
        return httpx.URL(url).host
    except Exception:  # noqa: BLE001
        return ""
