# Changelog

All notable changes to Chimera are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and Chimera uses
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

Every release lists **upgrade notes**: database migrations (a backup is
always written first), new or changed environment variables, and breaking
changes.

## [Unreleased]

## [1.0.0-rc.1] - 2026-10-16

Release candidate for 1.0.0; the notes are under [1.0.0] below.

## [1.0.0] - Unreleased

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
