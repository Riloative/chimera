# Troubleshooting

Start with **Models → (connection) → Test**. The diagnostics panel shows which
endpoint failed and why.

## "Could not connect to llama-server. Is the server running and is the URL correct?"

- Is it running? `curl http://127.0.0.1:8080/health`
- Wrong port or host in the connection URL.
- **Chimera in Docker:** `127.0.0.1` is the container itself. Use
  `http://host.docker.internal:8080` (the compose file maps it to the host), and
  start llama-server with `--host 0.0.0.0` *or* make sure Docker's host gateway
  can reach `127.0.0.1` on your OS (Docker Desktop: yes; Linux: llama-server must
  listen on an address the bridge can reach, e.g. `--host 0.0.0.0` with a
  firewall, or run both in Compose with `--profile llama`).
- A firewall blocking the port.

## "Server is up but not ready (loading model)"

llama-server answers `/health` with 503 while loading weights. Wait and test
again; large models can take a minute.

## "returned HTTP 401: Invalid API Key"

The server was started with `--api-key`. Enter the same key in the connection
(the field is write-only; leave it empty to keep the stored key).

## The model list is empty

- `/v1/models` returned nothing; check `curl http://127.0.0.1:8080/v1/models`.
- Servers without discovery: add model IDs under *Advanced → Manual models*.
- Disabled connections list no models.

Chimera never invents models: only what the server reported (or you added
manually) is shown. If a server is unreachable, the last known list is shown
with a warning.

## "Your message is too long for this model's context window"

`context.too_large`: the system prompt plus your latest message (with its
attachments and web pages) alone don't fit, so nothing was sent. Older history
is already left out automatically; this is about the message itself. Shorten
it, remove attachments or links, choose a model with a larger context, or
restart llama-server with a larger `-c` (per slot: `-c ÷ --parallel`; see
[LLAMA_SERVER.md](LLAMA_SERVER.md#context-size-slots-and-how-chimera-uses-them)).

## "The model server rejected the request as too long … even after sending less history"

`context_exceeded`: llama-server refused the request, Chimera retried once
with less history against the size the server reported, and it refused again.
Usually the server's real per-slot context is smaller than it reports to
Chimera (another proxy in between, or `--parallel` changed without a restart).
Set **Context size override** on the connection (Models → Edit → Advanced) to
the real size. With the strategy **Off** there is no retry: the server's error
is shown as is.

## Older messages are "not included in the model's context"

That is the dashed marker in the chat: the conversation is longer than the
window, so the oldest exchanges are left out (or summarized, with *Summarize
older messages*). The indicator in the top bar shows what the next request
contains. To keep a specific message, use **Pin to context** on it. To change
the behaviour: **Change strategy** on the marker (this conversation) or
Settings → Chat → Context window (your default).

## "Summarizing failed, so older messages were left out instead"

The summary request failed or the model ignored the length limit. The answer
was produced with the sliding window instead. Pick a smaller, faster model for
summaries in Settings → Chat → Context window, or check that the connection
works.

## The context indicator shows "default" instead of the server's size

The server doesn't report its context size (not llama-server, or an old build).
Set it per connection (Models → Edit → Advanced → *Context size override*) or
change *Context size when the server doesn't report it* in Settings → Chat.

## Where did my old answer go after editing / regenerating?

It is still there: editing or regenerating creates a new **version**. Use the
`‹ 1 / 2 ›` arrows under the message to switch. Deleting a message deletes it
and every version that branches from it (the confirmation says how many).

## "did not respond within the configured timeout"

The timeout is the max wait between chunks, including time to the first token.
Long prompts on CPU can take minutes to process. Raise the timeout in the
connection's advanced settings.

## Responses are cut off ("hit max tokens")

A *Max output tokens* limit is set (in Settings → Chat or the conversation
settings), or the server's `-n` limit applies.

## The title stays as my first message

Title generation asks the model for a short title after the first reply. Models
that only emit reasoning, or very small models, may return nothing usable; the
first message is then kept. You can disable it under Settings → Chat, and you can
always rename.

## Image attachments are refused

The model isn't known to support vision. Start llama-server with the model's
`--mmproj`, test the connection (it reads `/props`), or force *Image input →
Supported* in the connection's advanced settings.

## PDF: "No extractable text found"

The PDF contains scanned images. Chimera doesn't do OCR; convert it with an OCR
tool first (e.g. `ocrmypdf`).

## Knowledge base: "Embeddings unavailable … Keyword search only"

The embedding connection/model couldn't be used (server not started with
`--embeddings`, wrong model ID). Documents still work with keyword search. Fix
the setting, then click *Re-index* on each document.

## "Incorrect login or password" / "Too many attempts"

Sign-in errors are deliberately generic. After 5 failures in 15 minutes an
account is locked briefly (60 s, doubling with repeated failures, at most 1 h).
An administrator can reset the password, or on the server:
`python -m chimera.cli reset-password <login>` (this also clears the lock).

## "Registration is closed"

The default mode closes self-registration after the first account. Ask an
administrator to add you (Settings → Users), or as admin switch the mode, or run
`python -m chimera.cli registration open`.

## "Your security token expired"

The CSRF token is tied to your session. Reload the page (e.g. after signing in
in another tab or after the session expired).

## Every request returns 401 behind a proxy / after enabling HTTPS

If `CHIMERA_COOKIE_SECURE=true` but the site is served over plain HTTP, browsers
drop the session cookie. Use `auto` (default), or serve over HTTPS and list the
proxy in `CHIMERA_TRUSTED_PROXIES` so `X-Forwarded-Proto` is honoured.

## "Your administrator does not allow connections to this address"

Non-admin users may only add connections allowed by Settings → Users →
Connection addresses (default: local and private networks).

## "Host not allowed. Add it to CHIMERA_ALLOWED_HOSTS."

You're using a hostname or IP other than `localhost`/`127.0.0.1`. Add it to
`CHIMERA_ALLOWED_HOSTS` (comma-separated), or `*` to disable the check.

## Streaming arrives all at once behind a reverse proxy

The proxy buffers responses. Caddy: `flush_interval -1`. nginx:
`proxy_buffering off;`. Chimera already sends `X-Accel-Buffering: no`.

## The web UI says "Chimera API is running, but the web interface has not been built"

Run `cd frontend && npm ci && npm run build`, or set `CHIMERA_STATIC_DIR` to the
built `dist` directory.

## Installing and updating

**`update.sh` says it rolled back.** The new version didn't pass its health
check within the timeout (or kept crashing), so the previous `.env`, Compose
file, data and images were put back and the old version started again. Look
at the end of `/opt/chimera/update.log` (it includes the failed container's
last log lines) and at `docker compose logs chimera`. Common causes: not
enough memory (`CHIMERA_MEM_LIMIT`), a configuration error the new version
reports at startup, or a too-short `--timeout` on a slow machine. Fix the
cause and run `update.sh` again; report a bug if the release itself fails.

**"…has schema version N, but this version of Chimera only supports up to M"**
(the container exits with code 3). An older Chimera was started on a database
that a newer one already migrated: the downgrade guard refuses, so nothing is
damaged. Set `CHIMERA_VERSION` in `.env` back to the newer version and
`docker compose up -d`, or restore a backup from before that upgrade
([DEPLOYMENT.md → Rolling back](DEPLOYMENT.md#rolling-back)).

**Image pull fails** (`denied`, `manifest unknown`, `toomanyrequests`, TLS
errors). Check `CHIMERA_VERSION` is a published release (`ghcr.io/riloative/chimera`
tags), the server can reach `ghcr.io` (and Docker Hub for SearXNG), and the
system clock is correct (TLS). Behind an HTTP proxy, configure it for the
Docker daemon (`systemctl edit docker`, `HTTPS_PROXY=…`). Air-gapped: load the
images with `docker load` ([DEPLOYMENT.md → Offline](DEPLOYMENT.md#offline-operation)).
A failed pull during `update.sh` changes nothing.

**The health check fails / the container restarts in a loop.**
`docker compose ps` shows `unhealthy`; `curl -s http://127.0.0.1:8700/api/ready`
shows which check fails (`migrations`, `writable`, `data_dir`). `writable:
false`: the disk is full or the volume is read-only (`df -h`). Startup errors
are in `docker compose logs chimera`; configuration problems are reported as
`Configuration error: …` with the variable to fix (`chimera --check-config`
checks without starting).

**"Another Chimera server is already using /data."** Two containers (or a
container and a native server) share one data directory. Stop the other one.

**"Chimera is running and using this data directory. Stop it first."**
`chimera restore` refuses while the server runs: `docker compose stop chimera`,
then run the restore with `docker compose run --rm --no-deps chimera chimera restore …`.

**The install script stops at "Checksum mismatch".** A downloaded file differs
from the release's `SHA256SUMS` (a broken download or proxy, or tampering).
Nothing was installed; try again, or download the files manually and compare.

## Headless browser

**"…still showing a loading screen when the time ran out"** (`web.browser_loading`):
the page rendered, but after the render timeout it still looked like a loading
screen ("Loading…", a spinner, an "enable JavaScript" placeholder), so it was
not given to the model. Raise *Render timeout* (Settings → Web → Headless
browser) and `BROWSER_SETTLE_MAX_MS` (`.env`, then `docker compose --profile
browser up -d`). Pages behind a cookie wall or login can't be read.

See [WEB.md → Troubleshooting](WEB.md#troubleshooting). Quick checks: Settings →
Web → *Headless browser* shows *not set up*, *not reachable* or *rejected
Chimera's secret*; `docker compose --profile browser ps` and
`docker compose logs browser`. The service refuses to start without
`CHIMERA_BROWSER_SECRET` (≥ 16 characters).

## Web reading and search

"No Search button", "SearXNG refused JSON output", "could not reach its search
engines", "This address points to a private or local network", "This page needs
JavaScript", timeouts, quotas and context-limit messages are explained in the
troubleshooting table of [WEB.md](WEB.md#troubleshooting).

## Logs

- Docker: `docker compose logs -f chimera`
- Native: stdout of `scripts/start.sh`; set `CHIMERA_LOG_LEVEL=debug` for more.

Logs never contain message content, passwords, session tokens, API keys or fetched
page content; web requests are logged by domain only (full URLs only with
`CHIMERA_WEB_VERBOSE_LOG=true`).
