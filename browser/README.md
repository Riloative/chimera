# Chimera headless browser

Optional service that renders JavaScript-heavy pages for Chimera's web reader.
Enable it with the Compose profile (see `docs/WEB.md`, "Headless browser"):

```sh
# .env
CHIMERA_BROWSER_URL=http://browser:3000
CHIMERA_BROWSER_SECRET=$(openssl rand -hex 32)

docker compose --profile browser up -d --build
```

The image is the official Playwright image pinned to the same version as
`requirements.txt` (`mcr.microsoft.com/playwright/python:v1.56.0-noble`), so
Chromium is part of the image and nothing is downloaded at runtime.

## Contract

| Request | Auth | Answer |
|---|---|---|
| `GET /health` | none | `{"ok": true}` (liveness only) |
| `GET /status` | `Authorization: Bearer <BROWSER_SECRET>` | active/waiting renders, version |
| `POST /render` | same | `{url, proxy: {username, password}, timeout, user_agent, accept_language}` → `{ok, final_url, status, html, title, truncated, requests, blocked}` or `{ok: false, code, message, params}` |

The proxy server is fixed by `BROWSER_PROXY_SERVER` (Chimera's egress filter
proxy); only its per-render credentials come with the request. Closing the
connection cancels the render and closes its browser context.

## Settings (environment)

| Variable | Default | |
|---|---|---|
| `BROWSER_SECRET` | — | required, ≥ 16 characters |
| `BROWSER_PROXY_SERVER` | — | required, `http://chimera:8701` in Compose |
| `BROWSER_MAX_PAGES` | 2 | renders at once |
| `BROWSER_MAX_QUEUE` | 8 | waiting renders before `web.browser_limit` |
| `BROWSER_MAX_REQUESTS` | 250 | network requests per page |
| `BROWSER_MAX_HTML_KB` | 5120 | rendered HTML returned |
| `BROWSER_MAX_TIMEOUT_SECONDS` | 60 | upper bound for a render |
| `BROWSER_SETTLE_MS` | 1500 | wait for network idle after load |

## Development and tests

The backend's `tests/test_browser_live.py` drives this service with real
Chromium. It looks for `browser/.venv/bin/python` (or
`CHIMERA_TEST_BROWSER_PYTHON`):

```sh
python3 -m venv browser/.venv
browser/.venv/bin/pip install -r browser/requirements.txt
browser/.venv/bin/playwright install chromium   # skip if PLAYWRIGHT_BROWSERS_PATH already has Chromium 1194
```
