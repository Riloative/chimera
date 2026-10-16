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
| `BROWSER_SETTLE_MAX_MS` | 15000 | longest wait for the page to settle after load (always within the render timeout) |
| `BROWSER_SETTLE_QUIET_MS` | 750 | the visible text must be unchanged this long |
| `BROWSER_SETTLE_MIN_MS` | 300 | shortest wait, so fast pages still return in about a second |
| `BROWSER_SETTLE_POLL_MS` | 250 | how often the text is sampled |
| `BROWSER_SETTLE_MS` | — | 0.4.0 name; still accepted as the maximum when `BROWSER_SETTLE_MAX_MS` is not set |

### Waiting for content

After `DOMContentLoaded` the service samples the page every 250 ms (visible
text length, a hash of it, and the title). The page has **settled** when the
text hasn't changed for 750 ms and it doesn't look like a loading screen: the
title doesn't contain "loading", and the text isn't empty, a "Loading… / Please
wait" placeholder, or a short "enable JavaScript" message. Static pages return
after about a second; single-page apps are captured once their data has
arrived; pages that never stop changing (tickers, ads) are captured when the
time runs out, as before. If the page is still a loading screen when the time
runs out, the render fails with `web.browser_loading` instead of handing a
loading screen to the model. HTTP error pages (including the egress proxy's
refusals) are reported immediately by their status.

## Development and tests

The backend's `tests/test_browser_live.py` drives this service with real
Chromium. It looks for `browser/.venv/bin/python` (or
`CHIMERA_TEST_BROWSER_PYTHON`):

```sh
python3 -m venv browser/.venv
browser/.venv/bin/pip install -r browser/requirements.txt
browser/.venv/bin/playwright install chromium   # skip if PLAYWRIGHT_BROWSERS_PATH already has Chromium 1194
```
