# Changelog

All notable changes to Chimera are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and Chimera uses
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

Every release lists **upgrade notes**: database migrations (a backup is
always written first), new or changed environment variables, and breaking
changes.

## [Unreleased]

## [1.3.0] - Unreleased

A **Thinking** control next to the model picker, a native Anthropic
connection, and a fix for the context indicator showing another
conversation's numbers.

### Added
- **Thinking per conversation**, stored on the server like web search. The
  control next to the model picker adapts to the model: a menu of levels
  (Default, Off, Low, Medium, High, Max — only those the model supports, each
  with a one-line explanation), a single on/off button, a quiet "Always
  thinks", or nothing when the model has no thinking control. A level the
  model can't do is shown as the nearest one actually used, with a note in the
  menu; switching models keeps the conversation's level. **Settings → Chat →
  Thinking in new conversations** sets the default (Default: the model
  decides, as before).
- Thinking works with **llama-server** (the chat template's `enable_thinking`
  switch or gpt-oss levels, detected from `/props`), **Ollama** (`think`,
  detected from `/api/show`), and the hosted services **OpenAI, Mistral,
  Google Gemini, DeepSeek, xAI and OpenRouter** through their
  OpenAI-compatible endpoints (recognised from the URL, or chosen under
  *Service* on the connection). The mapping and detection are described in
  docs/ARCHITECTURE.md ("Thinking").
- **Anthropic connections** (native Messages API, no SDK): effort levels with
  adaptive thinking (a thinking budget on Claude 4.5 and earlier) and
  thinking summaries shown under *Thought process*. Anthropic's
  OpenAI-compatible endpoint can't set or show thinking, so the connection
  form suggests this type.
- **Models page**: each model shows what its thinking control can do, and the
  connection's editor can override it ("Detect", "Not supported", "On or
  off", "Levels").
- API (additive): `thinking` on `GET/PATCH /api/conversations/{id}` and
  `POST /api/conversations` (default: the user's `thinking_default`); each
  model in `GET /api/models` carries `thinking` (`shape`, `levels`,
  `default`, `source`); connections accept `kind: "anthropic"`, `service`
  and `model_settings`; answers record `metrics.thinking` (requested,
  applied, rejected). Export and import keep each conversation's level.

### Fixed
- **The context indicator showed the same numbers in different
  conversations.** The top bar stays mounted when you switch, and the
  indicator kept the previous conversation's estimate until the new one
  arrived; while the conversation you switched to was answering it didn't
  fetch at all, so a short chat could show a long chat's 5.2K for the whole
  answer. Estimates are now kept per conversation, fetched while an answer
  streams, and a late reply can't land in another conversation. Your draft
  only counts in its own conversation.
- **Titles, summaries and search queries on hosted reasoning models** came
  back empty or failed: only llama-server was asked not to think for these
  short requests. They now use each model's cheapest setting (off where
  possible) with room for the answer.
- **OpenAI reasoning models rejected Chimera's requests** because of
  `max_tokens` and `temperature`; Chimera now sends `max_completion_tokens`
  and leaves out the parameters these models reject (titles failed silently,
  and a conversation with a token limit failed every message).
- **A half-written message followed you into another conversation.** Each
  conversation now keeps its own draft.
- **Thinking left inline** (`<think>…</think>` at the start of an answer, from
  servers that don't separate it) is shown under *Thought process* instead of
  inside the answer.
- **Mistral's thinking** (sent as content chunks) was dropped; it is shown
  under *Thought process*.
- **Ollama vision** is detected from `/api/show`, so image input works
  without setting it to "on" manually.

### Not verified against live services
The hosted services' rules (which levels each model accepts, which
parameters it rejects, how thinking comes back) come from their current
documentation and are tested against mocks only: OpenAI, Anthropic, Mistral,
Google Gemini, DeepSeek, xAI and OpenRouter. Unknown models and servers get
no thinking control and are sent nothing new; a provider that rejects the
setting anyway gets one retry without it.

### Upgrade notes
- **Database migration 6** (a backup is written first): adds
  `conversations.thinking` (existing conversations: `default`, i.e. no
  change), `providers.service` and `providers.model_settings`. Additive: an
  older Chimera ignores them.
- No new environment variables. No breaking API changes.
- Existing OpenAI-compatible connections to `api.anthropic.com` keep working
  without a thinking control; edit the connection and choose the *Anthropic*
  type to get it.

## [1.2.0] - 2026-11-27

Web search per conversation with an Auto mode, a calmer look (the "Ink"
accent and a simpler stop button), and a round of accessibility, error-state
and speed fixes.

### Added
- **Web search per conversation**, stored on the server (so it survives
  reloads and other devices): the composer's **Search** button switches it on
  or off for the whole conversation. The menu beside it offers **Auto** and a
  **one-off choice for the next message** ("Search the web for the next
  message" / "Don't search for the next message"), drawn with a dashed outline
  and explained under the composer. Regenerate and edit follow the
  conversation unless you choose otherwise.
- **Auto mode**: one short model call decides whether a message needs a
  search and writes the queries in the same reply. The answer shows the
  decision ("No web search needed: …") with **Search the web** one click
  away. It never blocks an answer: an unusable decision or a failed search
  answers without the web and says so. Administrators can turn Auto off and
  edit its instructions (Settings → Web).
- `GET/PATCH /api/conversations/{id}` carry `web_search` (`off`, `on`,
  `auto`); `POST /api/conversations` accepts it (default: the user's setting).
  The message, regenerate and edit endpoints' `web_search` is now a one-off
  override (unset = the conversation's mode).

### Changed
- **Accent "Ink"** (warm graphite) replaces terracotta. Colour is now left to
  meaning (errors, warnings, success); links are always underlined; code
  keywords are bold. Every accent token passes WCAG contrast on every surface
  in both themes (terracotta text on its tint was 4.33:1, and in dark mode it
  was nearly the danger red).
- **Stop button**: the same size, place and colour as Send with a small stop
  mark, so nothing moves when they swap; no spinner (the answer area already
  shows progress).
- **Settings → Web**: one choice, "Web search in new conversations" (Off, On,
  Auto), replaces "Turn on web search for new messages" and "Keep web search
  on for the whole conversation".
- **Faster first chat load**: KaTeX and highlight.js load only for answers with
  maths or code. The Markdown code every chat loads drops from 610 kB to
  172 kB; Lighthouse (mobile, a chat with maths and code) 56 → 58, desktop
  88 → 92.
- **Conversation search** (Ctrl+K) uses a full-text index: a rare word in
  10,000 conversations takes 7 ms instead of 405 ms. Matching is unchanged
  ("contains", any case).
- One type scale (12, 13, 14 and 15 px plus headings) replaces 13 hand-picked
  text sizes.

### Fixed
- **Focus ring** below 3:1 contrast (55–60% transparent): now solid.
- **Editing a message never searched**, even with search on.
- **Failed loads** (prompts, collections, sessions, users, settings, the
  conversation list) showed a toast and then a spinner forever, and the
  sidebar said "No conversations yet": they now say what went wrong and offer
  **Try again**.
- **Model picker**: arrow keys, Home/End and Enter work; Escape returns focus
  to the picker instead of the page; correct listbox roles.
- **Menus** hid the rest of the page from screen readers while its controls
  stayed focusable (now non-modal).
- The administrators' **update question** pushed the page down after it
  loaded; it is now part of the first frame. Its close button is 28 px (was
  16).
- **Touch screens**: conversation options (⋯) in the sidebar were invisible
  except for the open chat.
- **Find in page** (Ctrl/Cmd+F) couldn't see earlier messages of a long
  conversation that weren't shown yet; they are now shown first.
- Faint text on highlighted rows (model picker, search) was below 4.5:1 in
  dark mode.
- The Settings tab row on tablets gives no sign that it scrolls: it now fades
  at the edge.

### Upgrade notes
- **Database migration 5** (a backup is written first, as always): adds each
  conversation's search mode, set from its owner's "search by default" (what a
  reload showed before), and a full-text index of message text (FTS5,
  trigram). The index takes about 1.7 times the size of the message text
  (measured: 29 MB for 17 MB of text) and is built once during the upgrade
  (about 0.7 s per 20,000 messages). On
  SQLite without the trigram tokenizer (before 3.34; the published images have
  it) search keeps scanning as before.
- "Search by default" becomes `search_default` (`off`/`on`); "keep search on"
  is retired: a conversation's mode now always stays as set.
- No new environment variables. API clients that sent `web_search: false` to
  mean "no search" still get no search; clients that sent nothing now get the
  conversation's mode instead of no search.

## [1.1.0] - 2026-11-18

Polish, bug fixes and speed: titles appear live, one calm waiting
indicator, long conversations stay fast, pages load only what they need, and
the interface meets WCAG 2.2 AA in both languages.

### Added
- **Older conversations in the sidebar**: "Show older conversations" loads them
  500 at a time. Before, only the newest 500 were listed (the rest were only
  reachable through search). `GET /api/conversations` takes optional `limit`
  (1–500, default 500) and `offset` (default 0); without them it answers as
  before.
- **Long conversations open with the latest 60 messages**; earlier ones load
  as you scroll up (or with "Show N earlier messages"), keeping your place.

### Changed
- **One waiting indicator everywhere** (waiting for the first token, live
  reasoning, web steps, the send/stop button, loading states): a CSS-only
  orbit that animates only `transform`/`opacity` on the compositor, so it
  stays smooth while answers stream. With reduced motion it becomes a slow
  opacity pulse. The status text next to it is unchanged and the indicator is
  hidden from screen readers. The waiting row has the height of a line of
  text, so the answer doesn't jump when it starts.
- **Faster streaming in long conversations**: a token re-renders only the
  message it belongs to (before: every message, with tree scans per message).
  In a 600-message conversation an answer used 6 s of script with the page
  frozen for up to a second at a time; now 0.7 s and no long tasks.
- **Smaller pages**: pages load on demand, and the Markdown renderer (KaTeX,
  highlight.js) comes with the chat page only. The sign-in page loads 616 kB
  of JavaScript instead of 1,354 kB.
- **Contrast**: secondary text, the warning colour and syntax highlighting
  meet 4.5:1 in light and dark themes; inline links are underlined.
- Settings use the tab row up to 1024 px wide, so tablets get a full-width
  form instead of three narrow columns.
- On phones the details under an answer (time, tokens) end in an ellipsis
  instead of being cut mid-word; the speed is left out there.

### Fixed
- **Conversation titles not updating live** (the sidebar kept showing "New
  conversation"): the start of an answer now carries the title the
  conversation already has (from the first message), a slower conversation
  list loaded at startup no longer overwrites newer titles, and title, summary
  and query requests ask llama-server reasoning models (e.g. Qwen3) not to
  think, so the short budget isn't spent on reasoning and a title is produced.
  A title you set yourself is never replaced.
- Renaming a chat while its title was being generated: the generated title
  replaced what you were typing.
- Search (Ctrl+K): an older, slower response could replace the results of
  what you typed last; clearing the box mid-search left the spinner on; hits
  showed the title from when the search ran.
- Web settings (administrators): typing in a list, the ports or a limit was
  wiped when another web setting finished saving.
- A live update for a conversation that isn't listed no longer adds an empty
  row to the sidebar.
- Accessibility: the account menu, the web-steps toggle and the Lithuanian
  web-search button are named by their visible text (WCAG 2.5.3); the
  conversation list is a navigation landmark; the chat view is the page's
  `main` region.

### Upgrade notes
- No database migration, no new or changed environment variables, no
  breaking API change (`GET /api/conversations` only gained optional
  parameters).

## [1.0.0-rc.1] - 2026-10-16

Release candidate for 1.0.0; the notes are under [1.0.0] below.

## [1.0.0] - 2026-10-16

The first stable release: published images, a documented install and update
path with backups and automatic rollback, and the release engineering behind
it. Everything from 0.4.1 is included.

### Added
- **Published images** `ghcr.io/riloative/chimera` and
  `ghcr.io/riloative/chimera-browser` for linux/amd64 and linux/arm64, built
  only by CI from version tags, scanned (Trivy), signed (Sigstore cosign,
  keyless) and published with SBOM and provenance attestations. Tags: `1.0.0`,
  `1.0`, `1`, `latest` (pre-releases: their exact version only).
- **Install script** `install-docker.sh` (release asset): checks
  prerequisites, verifies the release files against `SHA256SUMS`, writes a
  `.env` with fresh secrets (mode 600), starts the chosen profiles and waits
  for the health check. Idempotent; never overwrites `.env` or data.
- **`update.sh`**: updates to the newest release in the same major line (or a
  given version), pulls first, backs up `.env`, Compose files and the data
  volume, waits for the health check and **rolls back automatically** if the
  new version doesn't become healthy (crash loops are detected in seconds).
  `--dry-run`, `--channel patch|minor`, `--allow-major`, logs to `update.log`.
- **Opt-in automatic updates**: a systemd service and timer (patch releases by
  default, never a new major version); a cron alternative is documented.
- **Opt-in update notices** for administrators (Settings → About; asked once
  after setup): at most one request a day to the GitHub release list, with no
  user or instance data; never installs anything.
- **`chimera backup` / `chimera restore`** (also `chimera version`): consistent
  archives of the database, uploads and keys; restore refuses while the server
  runs, validates the archive and keeps the replaced data aside.
- **Downgrade guard**: an older Chimera refuses to start on a database a newer
  one migrated (exit code 3, with instructions), and the in-app restore
  refuses backups from newer versions.
- **`GET /api/ready`** readiness (migrations complete, database writable),
  used by the Compose health checks; `GET /api/health` now includes the
  version. Version and build commit in Settings → About.
- **Startup configuration validation**: fatal errors (e.g. browser URL without
  a secret, invalid trusted-proxy CIDR) stop the server with an actionable
  message; risky settings (e.g. `*` hosts with open registration) are warned
  about. `chimera --check-config` checks without starting.
- **JSON logs** (`CHIMERA_LOG_FORMAT=json`) with fixed fields; query strings
  are removed from access-log lines in both formats.
- **Graceful shutdown**: running answers get time to finish
  (`CHIMERA_SHUTDOWN_GRACE_SECONDS`), then are saved as *interrupted*.
- `Strict-Transport-Security` on responses served over HTTPS (directly or via
  a trusted proxy).
- CI on every pull request (backend, frontend typecheck/lint/unit/i18n, e2e,
  real-Chromium browser tests, both Docker builds, actionlint, shellcheck,
  Compose validation, version consistency); Dependabot; ESLint.
- Documentation: reverse proxies (Caddy, nginx, Cloudflare Tunnel),
  llama-server on the same host, updates and rollback, a production
  checklist, image verification; `CONTRIBUTING.md`, `CODE_OF_CONDUCT.md`,
  `SECURITY.md`, `RELEASING.md`, issue and PR templates.
- MIT license; `THIRD_PARTY_NOTICES.md` for the bundled fonts and libraries.

### Changed
- **`docker-compose.yml` now uses the published images** instead of
  building; `docker-compose.dev.yml` builds from a checkout.
- The Chimera container is hardened: read-only root filesystem, no Linux
  capabilities, `no-new-privileges`, memory and process limits, health check
  on readiness, 30 s stop grace period.
- Third-party images are pinned to exact versions (SearXNG
  `2026.10.15-b00212a83`, llama.cpp `server-b11669`).
- One version source: `backend/pyproject.toml` (checked against
  `frontend/package.json`, the Compose file, the changelog and release tags).
- Administrative commands are available as `chimera <command>` (e.g.
  `docker compose exec chimera chimera reset-password admin`); the old
  `python -m chimera.cli …` keeps working.
- One server per data directory (a lock file); a second one refuses to start.

### Upgrade notes
- **No database migration** from 0.4.x (schema 4). From 0.3 or older, the
  usual migrations run after an automatic backup.
- **Locally built installs** (0.x `git clone` + `docker compose up --build`):
  follow *Moving a locally built 0.x install to the published images* in
  `docs/DEPLOYMENT.md`. Remove `build:`/`image:` lines for `chimera` and
  `browser` from your `docker-compose.override.yml`; network pinning and
  security options keep working.
- **New variables** (all optional): `CHIMERA_VERSION`, `COMPOSE_PROFILES`
  (Compose), `CHIMERA_PUBLISH` (replaces editing the `ports:` mapping),
  `CHIMERA_MEM_LIMIT`, `BROWSER_MEM_LIMIT`, `LLAMA_PARALLEL`,
  `CHIMERA_LOG_FORMAT`, `CHIMERA_SHUTDOWN_GRACE_SECONDS`,
  `CHIMERA_UPDATE_CHECK_URL`.
- **Behind a reverse proxy in Docker**, `CHIMERA_TRUSTED_PROXIES` must be the
  Compose network's gateway (published-port traffic arrives from there), not
  127.0.0.1; see `docs/REVERSE_PROXY.md`. Invalid entries now stop the server.
- **Stricter startup**: configurations that 0.4 only warned about (browser URL
  without a valid secret) now refuse to start, with a message naming the
  variable.
- If you mount the data directory from the host, it must be writable by
  uid 10001 (unchanged), and backups are written with owner-only permissions.

## [0.4.1] - 2026-10-16

### Fixed
- **Sources under an answer** show only that turn's pages and search results
  (including failed ones) plus pages from earlier turns that the answer
  actually cites. Earlier pages are still sent to the model so follow-up
  questions keep working; they no longer appear under every later answer.
- **Failed and blocked links** show their real reason in the Sources list
  (the same text as the red chip under your message), e.g. a private address
  or a 404, instead of "Left out because of the model's context limit". That
  text now appears only for pages that were read but didn't fit.
- Search results of the current turn that were read but didn't fit the context
  are listed as left out instead of disappearing from the Sources list.
- **Headless browser waits for content**, not a fixed time: after the page
  loads it waits until the visible text stops changing and the page no longer
  looks like a loading screen (bounded by the render timeout). Single-page apps
  that fetch their data after a "Loading…" screen are now captured with their
  real content.
- A page that is still a loading screen when the time runs out is reported as
  not readable (`web.browser_loading`) instead of being given to the model.
- Service workers are blocked in secure contexts too (https and loopback
  pages), not only on plain-http pages.

### Upgrade notes
- No database migration.
- New optional browser settings: `BROWSER_SETTLE_MAX_MS` (15000),
  `BROWSER_SETTLE_QUIET_MS` (750), `BROWSER_SETTLE_MIN_MS` (300),
  `BROWSER_SETTLE_POLL_MS` (250). `BROWSER_SETTLE_MS` from 0.4.0 is still
  accepted and now means the *maximum* settle wait; remove it from `.env` to
  get the new default of 15 s.
- With the browser profile, rebuild the browser image:
  `docker compose --profile browser up -d --build`.
- New error code `web.browser_loading` (localized in English and Lithuanian).

## [0.4.0] - 2026-10-16

### Added
- Conversation branching: edits and regenerations keep earlier versions, with
  an inline version navigator; the active branch is saved per conversation.
- Automatic context management: window discovery from llama-server, exact
  token counts, priority-based trimming with prompt-cache-friendly hysteresis,
  optional summaries, pinning, a usage indicator and a trimmed-history marker.
- Optional headless browser (`browser` Compose profile) for JavaScript pages,
  isolated behind Chimera's egress filter proxy.

### Upgrade notes
- Migration 4 converts linear histories into message trees (backup first).
  0.3 cannot use a 0.4 database; roll back with the pre-migration copy.
- Export format version 3 (trees); older exports still import.
- New variables: `CHIMERA_BROWSER_URL`, `CHIMERA_BROWSER_SECRET`,
  `CHIMERA_BROWSER_PROXY_BIND`, `BROWSER_MAX_PAGES`.

## [0.3.0]

### Added
- Web reading (pasted links) and web search (SearXNG, Brave, Tavily) with
  citations, an SSRF-hardened fetcher and per-user quotas.

### Upgrade notes
- Migration 3 adds `web_sources` and `messages.web`.

## [0.2.0]

### Added
- Multi-user accounts, per-user data isolation, English and Lithuanian.

## [0.1.0]

### Added
- First version: local chat with llama-server and OpenAI-compatible servers.
