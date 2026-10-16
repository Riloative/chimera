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

(Filled in below.)

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
