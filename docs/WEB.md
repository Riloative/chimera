# Web reading and web search

Chimera can let your local models use the internet in two ways:

1. **Reading links.** Paste one or more `http(s)://` links into a message and
   Chimera downloads the pages, extracts the readable text and gives it to the
   model, clearly marked as source material.
2. **Web search.** Turn on **Search** in the composer for a message and
   Chimera searches the web, reads the best results and the model answers with
   numbered citations like `[1]` and a list of sources.

Both are **off until an administrator enables web access** (Settings → Web).
Web access is the only feature that sends data off your machine, so this page
is explicit about what leaves the server.

## What leaves the machine

| When | What is sent | To whom |
| --- | --- | --- |
| You paste a link (and it is read) | An HTTP request for that page, from **this server's** IP address, with Chimera's User-Agent | The website |
| You search the web | Your message, or 1–3 short queries the model wrote from it | The configured search engine |
| A search runs | HTTP requests for the top results (and their `robots.txt`) | The result websites |

Never sent: your cookies, your Chimera session, your account details, other
parts of the conversation, or API keys of other services. Websites see the
server's IP address, not yours. The first time someone turns on search, the
UI shows a short notice explaining this; it is not shown again once accepted
(Settings → Web → *Show again* re-enables it).

### Privacy of each search engine

| Engine | Privacy | Notes |
| --- | --- | --- |
| **SearXNG** (self-hosted, recommended) | Queries go to your own SearXNG, which forwards them to public engines without user identity. No account, no API key. | Optional Docker Compose profile included. |
| **Brave Search API** | Queries go to Brave under your API key. | Needs a key from <https://brave.com/search/api/>. |
| **Tavily API** | Queries go to Tavily under your API key. | Needs a key from <https://tavily.com>. Built for LLM retrieval. |
| DuckDuckGo | *Not offered.* | There is no official API; keyless libraries scrape DuckDuckGo's HTML, break regularly and conflict with its terms. Use SearXNG (which can include DuckDuckGo results) instead. |

## Setting up

### SearXNG with Docker Compose (recommended)

1. Generate a secret and put it in `.env` next to `docker-compose.yml`:

   ```bash
   echo "SEARXNG_SECRET=$(openssl rand -hex 32)" >> .env
   ```

   Without it SearXNG refuses to start (on purpose: the bundled config contains
   SearXNG's "unset" marker, so the instance never runs with a public default).

2. Start it alongside Chimera:

   ```bash
   docker compose --profile searxng up -d
   ```

   The service is not published to the host; only Chimera reaches it on the
   internal Compose network. Its configuration is
   [`deploy/searxng/settings.yml`](../deploy/searxng/settings.yml): JSON output
   enabled, metrics, image proxy and the public-instance bot limiter off.

3. In Chimera: **Settings → Web**, switch on **Allow web access**, choose
   **SearXNG**, enter `http://searxng:8080`, press **Test search**, then **Save engine**.
   (Or set `CHIMERA_WEB_ENABLED=true` and `CHIMERA_WEB_SEARXNG_URL=http://searxng:8080`
   in `.env` before the first start.)

### Your own SearXNG instance

SearXNG's JSON API is **disabled by default**. Enable it in its `settings.yml`:

```yaml
search:
  formats:
    - html
    - json
```

and restart SearXNG. If you forget, Chimera's **Test search** says *"SearXNG
refused JSON output"*. If SearXNG sits behind a proxy that needs a header,
fill in *Authentication header* and *Header value* (the value is stored
encrypted and never shown again).

### An API engine (Brave or Tavily)

Settings → Web → Engine **Brave Search API** (or **Tavily API**) → paste the
key → **Test search** → **Save engine**. Keys are write-only in the UI
("A key is saved"), encrypted at rest with the same key as connection
credentials (`CHIMERA_ENCRYPTION_KEY` / `data/.encryption_key`), never
returned by the API, never logged and never exported.

### Testing

**Test search** runs a real query and reports status, latency and the number
of results. Chimera never shows an engine as working unless a test succeeded;
the last result is remembered (*"Last tested …"*).

## How link reading works

- **Detection.** When you send a message, links are detected the same way the
  composer shows them (shared test vectors keep both in sync). Trailing
  punctuation, parentheses and Markdown link syntax are handled; links inside
  code blocks or inline code are **not** read. The composer shows each link as
  a chip; click it to skip (or, with *Read links in my messages* off, to
  choose) a link before sending. At most 5 links per message by default.
- **Fetching** goes through the hardened fetcher (below). Redirects are
  followed (up to 5) and every hop is checked again.
- **Extraction.**
  - HTML → [trafilatura](https://trafilatura.readthedocs.io) main-content
    extraction to Markdown (headings, lists, tables and code blocks are kept;
    navigation, ads, footers, scripts and styles are dropped). It runs locally,
    scores at the top of public extraction benchmarks and needs no browser.
  - Title, description, canonical URL and publication date come only from
    explicit `<meta>`/JSON-LD data (guessing picked up copyright years).
  - Plain text, Markdown, JSON (pretty-printed), CSV (as a table), RSS/Atom
    (item list; XML entities are never resolved) and PDFs (the same extractor
    as attachments).
  - Character sets: from the HTTP header, the page's `<meta charset>`, or
    detection — Windows-1257 and ISO-8859-13 Lithuanian pages decode correctly.
  - **Rewriters** (`backend/chimera/web/urls.py`, a small list): GitHub file
    pages are read from `raw.githubusercontent.com`, repository pages via their
    README (falling back to the page); YouTube pages give only the title and
    description, clearly labelled *"transcripts aren't available"*.
- **Failures are reported, never hidden.** A page that can't be read (blocked
  address, 403/404/5xx, timeout, too large, unsupported type, needs
  JavaScript, empty) appears as a red chip under your message with the reason,
  and the model is told explicitly that the page *could not be read* and must
  not guess its content.
- **Follow-ups** reuse the stored text: ask more questions about the same page
  without downloading it again. **Read again** (in the page dialog) re-downloads
  it and keeps the old text if the new attempt fails.

### Limits

- **JavaScript-only pages** (single-page apps) usually have no text in their
  HTML. Chimera detects this and says *"This page needs JavaScript"* — or,
  with the optional [headless browser](#headless-browser-optional), renders the
  page instead.
- **Paywalls and logins**: Chimera never sends your cookies, so it reads what
  an anonymous visitor sees.
- **robots.txt** is respected for automatically fetched *search results*
  (setting, on by default). Links **you** paste are treated like a browser
  visit you asked for and are read regardless.
- **Size**: 10 MB per page by default (Settings → Web → Limits); compressed
  responses are capped by their *decompressed* size.

## How web search works

1. **Query writing.** The selected chat model (or a lighter *task model* you
   pick in Settings → Web) turns the conversation and your latest message into
   1–3 search queries, returned as JSON. The prompt includes today's date so
   "latest" or "this week" work. If the reply is unusable or takes longer than
   30 s, your message is searched as written — the pipeline never fails on a
   small model's bad JSON. Administrators can switch this step off and edit its
   prompt.
2. **Search** through the configured engine. Results of all queries are
   merged by rank, duplicates removed (by canonical URL, ignoring `www.`,
   trailing slashes and tracking parameters such as `utm_*` and `fbclid`, which
   are also stripped from the links), and domain allow/deny lists applied.
3. **Reading** the top results in parallel (4 at a time per user). A page that
   can't be read falls back to the search engine's snippet, marked *Summary
   only*.
4. **Fitting into the context** (below).
5. **Answering** with the sources and grounding instructions.
6. Everything — steps, queries, sources, citation map — is stored with the
   answer, so reloading shows exactly what was shown live.

The answer shows its progress live (*Writing search queries… → Searching for:
… → Reading 4 sources* with the domains) and collapses into a one-line summary
afterwards. Citations `[n]` are links to the source (keyboard-focusable, with
title and URL on hover/focus); numbers that don't match a source stay plain
text, and the server records them as invalid. If the search fails (engine
unreachable, rate limited, no results, nothing readable) the model is **not**
asked to answer without sources; instead the message offers **Retry search**
and **Answer without search**. **Stop** cancels whichever stage is running.

### Context budget and relevance

Local models often run with 4k–32k tokens of context, so web text is budgeted:

- Web text is part of the **one context budget** described in
  [ARCHITECTURE.md](ARCHITECTURE.md#context-management): it belongs to the
  latest message, which comes right after the system prompt in priority, so
  older conversation history is trimmed first (the steps say *"3 earlier
  messages were left out to make room for the pages"*). The window size comes
  from llama-server (`/props`, per slot), a per-connection override, or the
  fallback in Settings → Chat → Context window.
- Web text is further capped by *Characters of web text per answer* (default
  24,000) and *Characters per source* (default 8,000). If even the pages alone
  don't fit next to the system prompt and the answer reserve, fewer sources are
  used (retried with a smaller share) before the message is refused.
- Long pages are split into passages and scored against the queries and your
  question with **BM25** (no embedding model needed). Pasted links use *full*
  mode (the whole page if it fits, otherwise its opening plus the most relevant
  passages, in order); search results use *focused* mode (the best passages).
- Pasted links have priority, then results by rank. When not everything fits,
  the lowest-ranked sources are dropped and the steps say so: *"Used 3 of 5
  sources because of the model's context limit"*. A pasted link that doesn't
  fit is still mentioned to the model as not included.
- Pages read in earlier turns are re-sent for follow-ups when there is room;
  otherwise they are replaced by a one-line note.

**Recommended `--ctx-size` for llama-server** (see [LLAMA_SERVER.md](LLAMA_SERVER.md)):
at least **8192** for reading a link, **16384** or more for web search with
5 results. With 4096, expect only one or two sources to fit. Rough guide: one
token ≈ 3.5 characters of web text.

### Grounding instructions

The default instructions (editable by administrators, *Reset to default*
restores them) tell the model to use and cite the sources, only cite numbers
that exist, say when the sources don't contain the answer, never invent URLs
or quotes, mention unreadable sources as unreadable, and answer in the user's
language. The current date, time and the user's time zone (from the browser)
are filled in. A fixed security notice (below) is always appended and cannot
be removed by editing.

## Security model

### The fetcher (SSRF protection)

Users — and web pages, through redirects — supply URLs that **this server**
then requests. Without protection that would let anyone probe your LAN, the
Docker host or cloud metadata services. All web traffic (pages, `robots.txt`,
search engines) therefore goes through one component,
`backend/chimera/web/fetcher.py`:

- Only `http`/`https`; no `user:password@` in URLs; only ports 80 and 443
  unless an administrator allows more.
- The URL is parsed once and rebuilt from its parts; backslashes, control
  characters, whitespace and percent-encoded host names are rejected, so
  different URL parsers can't disagree about the host.
- Chimera resolves the host name itself, rejects it if **any** address is not
  public, and then connects to exactly that address (keeping the name for the
  `Host` header and TLS certificate checks). A second, different DNS answer
  (DNS rebinding) is never used. Every redirect hop is checked the same way.
- Blocked: loopback, private (RFC 1918), CGNAT, link-local (incl.
  `169.254.169.254`), ULA/site-local IPv6, multicast, unspecified, reserved and
  benchmarking ranges, IPv6 forms that embed a blocked IPv4 address (mapped,
  compatible, 6to4, NAT64, Teredo), decimal/octal/hex IPv4 spellings,
  `localhost`, `*.local`, `*.lan`, `*.internal`, `*.home.arpa` and single-label
  names.
- **Administrator allow-list** (Settings → Web → *Internal hosts that may be
  read*), empty by default, for e.g. an intranet wiki. The configured SearXNG
  address is exempt **only for search requests**; pasted links to it are still
  blocked.
- Limits: connect/read timeouts and an overall deadline, response size (the
  body is streamed and aborted at the limit), decompressed size (gzip/deflate
  are decoded by Chimera with an output cap — a 40 KB "zip bomb" that inflates
  to 40 MB is stopped at the limit), allowed content types, 16 concurrent
  requests per server and 4 per user, and per-user hourly quotas for page reads
  and searches (stored in the database like the login throttle).
- No cookies are stored or sent, connections are not reused between requests,
  and nothing of the user's session reaches a website.
- There is **no endpoint that fetches an arbitrary URL for the browser** or
  returns raw fetched bytes. The browser only receives extracted text and
  metadata of sources it owns.
- Logs contain the domain and outcome only (`web fetch ok host=example.org
  status=200`). Full URLs (which can contain secrets in query strings) are
  logged only with `CHIMERA_WEB_VERBOSE_LOG=true`. Page content and API keys
  are never logged.

`CHIMERA_WEB_PROXY` sends web traffic through an HTTP proxy. Names are still
resolved and checked locally (so the server must be able to resolve DNS), but
the proxy makes the final connection, so DNS pinning no longer applies —
restrict internal destinations on the proxy as well.

### Prompt injection

A web page can contain text written to manipulate the model ("ignore your
instructions and …"). Small local models are particularly easy to steer.
Chimera does what an application can do:

- Each source is wrapped in `<source id="n" token="…">` … `</source token="…">`
  with a **random token per request**; the token is removed from page text,
  and any `<source`-like tag inside the page is defanged, so a page cannot
  close its block and start writing "instructions".
- Chat-template control tokens (`<|im_start|>`, `[INST]`, `<|eot_id|>`,
  `<start_of_turn>`, `<think>` …) are stripped from page text, so a page can't
  open a fake system or user turn.
- The system prompt always states that source text is untrusted reference
  material, never instructions, and must not cause data to be sent anywhere.
- Web content can't trigger actions: Chimera executes no tools in the default
  pipeline, and nothing a page says changes settings or reaches other users.
- Model output is rendered without raw HTML; links open in a new tab with
  `noopener noreferrer`; **remote images are never loaded automatically**
  (they would reveal your IP and could carry conversation data in their URL) —
  they show as a placeholder with an explicit *Open image* link.

**What is not guaranteed:** the model may still follow instructions it reads,
summarize a page misleadingly, or produce a link containing conversation text.
You click links at your own discretion. Treat answers based on untrusted pages
with the same care as the pages themselves.

### Data and isolation

- Pages read and search results are stored per user, attached to the message
  (`web_sources` table; migration 3, preceded by the automatic backup) and
  covered by the usual ownership checks: other users get 404. They are included
  in your data export (with the extracted text) and deleted with the
  conversation, by *Delete all my data*, and with the account.
- The read cache (10 minutes) is **per user**: two users reading the same page
  each download it. This avoids any chance of one user's request revealing
  another's.
- Administrator-only: the web switch, engines and keys, allow/deny lists,
  robots policy, limits and prompts. Turning web access off is enforced by the
  server (`web.disabled`), not just hidden in the UI.

## Headless browser (optional)

Some pages only produce their text with JavaScript. Chimera can render them in
a headless Chromium that runs as a **separate, isolated service**. It is off
unless you start it.

### Enabling it

```sh
# .env (next to docker-compose.yml)
CHIMERA_BROWSER_URL=http://browser:3000
CHIMERA_BROWSER_SECRET=<openssl rand -hex 32>

docker compose --profile browser up -d --build
```

Then, as an administrator, check **Settings → Web → Headless browser**: it
shows whether the service is reachable and has the switches:

| Setting | Default | |
| --- | --- | --- |
| Use the headless browser | on | Off disables it completely (including "Render with browser"). |
| Render pages that need JavaScript automatically | on | When static reading finds an empty JavaScript app, the page is rendered instead. |
| Render timeout | 25 s | Per page (5–60). |
| Renders per user per hour | 60 | Separate from the page-read quota; over it: `web.browser_limit`. |

Users see **Render with browser** in a source's details (next to *Read
again*) whenever the browser is available, and *Rendered with the headless
browser* on sources it read. Sources record which loader read them
(`loader`: `static` or `browser`).

Without Docker: run `browser/chimera_browser.py` with Playwright 1.56 and set
`BROWSER_SECRET` and `BROWSER_PROXY_SERVER=http://127.0.0.1:8701` for it, and
`CHIMERA_BROWSER_URL=http://127.0.0.1:3000`, `CHIMERA_BROWSER_SECRET` for
Chimera. Without network isolation the egress proxy and Chromium's flags are
the only guard — prefer the container.

### How it is isolated

```
 browser container ──(internal network, no gateway)──▶ Chimera :8701 egress filter proxy ──▶ internet
       ▲                                                        (netguard rules, pinned DNS)
       └── POST /render (Bearer secret) ── Chimera
```

- **Network.** The browser container is only on `browser-net`, a Compose
  network marked `internal: true` (no route to the internet, the host or your
  LAN). Its port is not published and not reachable through a Cloudflare
  Tunnel or any reverse proxy pointed at Chimera.
- **Egress filter proxy.** Chromium's only way out is an HTTP proxy that runs
  inside Chimera (`backend/chimera/web/egress.py`). Every connection — the
  page, each redirect, every script, stylesheet, `fetch()` or WebSocket the
  page tries — is a separate CONNECT or proxied request, validated with the
  same rules as the static fetcher: http/https only, allowed ports, the
  administrator's domain allow/deny lists, the name resolved by Chimera and
  every address required to be public, then a connection to that exact address
  (DNS pinning). Plain-HTTP connections are closed after one response, so a
  kept-alive connection can't carry a request for another host.
- **Credentials per render.** The proxy only accepts credentials Chimera
  issues for one render; they're revoked, and any open tunnel is closed, when
  the render finishes, fails or is cancelled. Other containers on the network
  can't use the proxy.
- **Chromium flags.** Loopback traffic is forced through the proxy (Chromium
  normally bypasses proxies for `localhost`), host-name resolution inside the
  browser is disabled except for the proxy, and WebRTC is limited to proxied
  traffic and removed from pages.
- **Per page:** a fresh incognito context (no cookies, storage or cache shared
  between renders or users), service workers blocked, downloads refused
  (`web.browser_blocked`), popups closed, WebSockets closed, non-http(s)
  requests aborted, images/media/fonts not loaded, at most 250 requests per
  page and 5 MB of HTML returned.
- **Limits.** 2 pages at once and 8 waiting (`BROWSER_MAX_PAGES`,
  `BROWSER_MAX_QUEUE`), per-render timeout, per-user hourly quota. Pressing
  **Stop** cancels the render: Chimera drops the request, the service closes
  the page, and the proxy credentials stop working (tested: no renderer
  process is left behind).
- **Container.** Non-root (`pwuser`), read-only root filesystem with small
  tmpfs mounts, all capabilities dropped, `no-new-privileges`, memory and
  process limits, `init` to reap processes. Chromium's own sandbox is off
  (Playwright's default inside containers), so the container is the boundary;
  keep the image updated by bumping the pinned Playwright version.
- **Nothing downloaded at runtime.** Chromium comes with the pinned
  `mcr.microsoft.com/playwright/python:v1.56.0-noble` image.

### What it can't do

Text only (no screenshots); no logins, cookies or interaction (clicking
"load more", scrolling); it doesn't use `CHIMERA_WEB_PROXY` (connections go
directly from Chimera); sites that block headless browsers still refuse it.
Pages rendered this way take seconds and ~100–300 MB of memory each.

### Errors

| Code | Meaning |
| --- | --- |
| `web.browser_unavailable` | Not configured, switched off, or the service can't be reached (static reading continues). |
| `web.browser_timeout` | The page didn't finish within the render timeout. |
| `web.browser_blocked` | The page or a redirect pointed to a forbidden address, or it started a download. |
| `web.browser_limit` | The service is busy, or the user's hourly render quota is used up. |

When automatic rendering fails, the source keeps *"This page needs
JavaScript"* and adds why the browser didn't help.

## Optional: "let the model decide when to search"

**Status: planned — not available in the UI.** The interface exists
(`backend/chimera/web/tools.py`): OpenAI-style `web_search(query)` and
`fetch_url(url)` tool definitions and an executor that validates arguments,
caps calls (4) and routes everything through the same fetcher, quotas and
domain rules (tested). It is not wired into chat because native tool calling
with local models depends on the chat template (llama-server needs `--jinja`
and a template with tool support, which `/props` does not reliably report),
and small models often emit malformed calls. Rather than ship a toggle that
works for some models and silently fails for others, the default pipeline —
which works with every model — is the supported path.

## Implemented and planned

Implemented: link reading (HTML, text, Markdown, JSON, CSV, RSS/Atom, PDF,
GitHub, YouTube metadata), SearXNG/Brave/Tavily, query writing with fallback,
BM25 passage selection and context budgeting, live steps, citations and
sources, per-user quotas and caching, admin controls, the optional headless
browser for JavaScript pages, English and Lithuanian.

Planned: model-driven tool
search, embedding-based reranking (using a knowledge-base embedding model),
video transcripts, image search, a "deep research" multi-step mode, more
engines (Google PSE, Kagi, Serper, Mojeek).

## Troubleshooting

| Symptom | Cause and fix |
| --- | --- |
| No **Search** button in the composer | Web access is off (Settings → Web → *Allow web access*). |
| Search button greyed out | No engine configured, or an API engine without a key. |
| *SearXNG refused JSON output* | Add `json` to `search.formats` in SearXNG's `settings.yml`, restart it. |
| *SearXNG answered, but could not reach its search engines (…)* | SearXNG has no internet access (firewall, DNS, proxy). Check its logs; outgoing proxies go under `outgoing.proxies` in its settings. |
| *The search engine (…) could not be reached* | Wrong address or SearXNG not running. With Compose use `http://searxng:8080`; from a host install use the published address. |
| *rejected the API key* | Wrong or expired Brave/Tavily key: enter a new one. |
| *The search returned no results* | Rephrase; check the domain allow-list isn't too narrow. |
| *This address points to a private or local network* | Intended. To read an internal site, add its host or network to *Internal hosts that may be read*. |
| *Connections to port … are not allowed* | Add the port under *Allowed ports*, or allow-list the host. |
| *This page needs JavaScript* | The site renders in the browser. Enable the [headless browser](#headless-browser-optional), or paste the text or a PDF instead. |
| *The headless browser is not available* | `CHIMERA_BROWSER_URL`/`CHIMERA_BROWSER_SECRET` not set, the `browser` profile not running, or the secrets differ (Settings → Web → Headless browser shows which). `docker compose logs browser`. |
| *The page, or a page it redirected to, is not allowed* | The page (or a redirect) points to a private address or starts a download. Intended; allow-list an internal host if you really need it. |
| *took too long to render* | Raise *Render timeout*; heavy pages may need 30–45 s. |
| *No readable text was found* | The page is mostly images or forms. |
| *The site took too long to respond* | Raise *Page timeout* in Settings → Web → Limits. |
| *You've reached the hourly limit* | Per-user quota; an administrator can raise it under Limits. |
| *Used 2 of 5 sources because of the model's context limit* | Start llama-server with a larger `--ctx-size`, request fewer results, or lower *Characters per source*. |
| The model ignores sources or cites wrong numbers | Small models struggle; try a larger instruct model, fewer results, or adjust the grounding instructions. Invalid citation numbers are shown as plain text. |
