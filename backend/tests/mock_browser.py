"""A stand-in for the headless browser service (``browser/chimera_browser.py``)
with the same HTTP contract. It fetches pages *through Chimera's egress
proxy* with the single-use credentials it is given (as Chromium would) and
"runs" the mock SPA's inline script. The real service is exercised against
real Chromium in ``test_browser_live.py``."""

from __future__ import annotations

import asyncio
import hmac
import json
import re
from urllib.parse import urljoin

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

SECRET = "test-browser-secret-0123456789"
SCRIPT_RE = re.compile(r'document\.getElementById\("root"\)\.innerHTML = ("(?:[^"\\]|\\.)*");')


def create_browser_app() -> FastAPI:
    app = FastAPI()
    app.state.proxy_port = None
    app.state.renders = []
    app.state.delay = 0.0
    app.state.cancelled = 0
    app.state.active = 0
    app.state.fail = None  # e.g. {"code": "web.browser_timeout"}

    def authorized(request: Request) -> bool:
        return hmac.compare_digest(request.headers.get("authorization", ""), f"Bearer {SECRET}")

    @app.get("/health")
    async def health():
        return {"ok": True}

    @app.get("/status")
    async def status(request: Request):
        if not authorized(request):
            return JSONResponse({"ok": False, "code": "unauthorized"}, status_code=401)
        return {"ok": True, "version": "fake", "active": app.state.active, "waiting": 0, "rendered": len(app.state.renders), "max_pages": 2}

    @app.post("/render")
    async def render(request: Request):
        if not authorized(request):
            return JSONResponse({"ok": False, "code": "unauthorized"}, status_code=401)
        job = await request.json()
        app.state.renders.append(job["url"])
        app.state.active += 1
        try:
            waited = 0.0
            while waited < app.state.delay:
                if await request.is_disconnected():
                    app.state.cancelled += 1
                    return JSONResponse({"ok": False, "code": "cancelled"})
                await asyncio.sleep(0.05)
                waited += 0.05
            if app.state.fail:
                return {"ok": False, **app.state.fail}
            proxy = f"http://{job['proxy']['username']}:{job['proxy']['password']}@127.0.0.1:{app.state.proxy_port}"
            url = job["url"]
            async with httpx.AsyncClient(proxy=proxy, trust_env=False, follow_redirects=False, timeout=10) as client:
                for _ in range(6):
                    try:
                        resp = await client.get(url)
                    except httpx.ProxyError:
                        return {"ok": False, "code": "web.browser_blocked", "message": "blocked", "params": {"reason": "blocked"}}
                    if resp.status_code in (301, 302, 303, 307, 308) and resp.headers.get("location"):
                        url = urljoin(url, resp.headers["location"])
                        continue
                    break
            html = resp.text
            match = SCRIPT_RE.search(html)
            if match:
                html = html.replace('<div id="root"></div>', f'<div id="root">{json.loads(match.group(1))}</div>')
            return {"ok": True, "final_url": url, "status": resp.status_code, "title": "", "html": html,
                    "truncated": False, "requests": 1, "blocked": []}
        finally:
            app.state.active -= 1

    return app
