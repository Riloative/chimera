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

## Response stops with "the request exceeds the available context size"

The conversation plus attachments is longer than the server's context (`-c`).
Start a new conversation, remove large attachments, or restart llama-server with
a larger `-c` (memory permitting). The connection diagnostics show the current
`n_ctx`.

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

## "Remote access is disabled. Set CHIMERA_PASSWORD…"

You're reaching Chimera from another device (or through a proxy that connects
from a non-loopback address) without a password. Set `CHIMERA_PASSWORD`. See
[SECURITY.md](SECURITY.md).

## "Host not allowed. Add it to CHIMERA_ALLOWED_HOSTS."

You're using a hostname other than `localhost`/`127.0.0.1` without a password.
Add it to `CHIMERA_ALLOWED_HOSTS`, or set a password (which allows any host).

## Streaming arrives all at once behind a reverse proxy

The proxy buffers responses. Caddy: `flush_interval -1`. nginx:
`proxy_buffering off;`. Chimera already sends `X-Accel-Buffering: no`.

## The web UI says "Chimera API is running, but the web interface has not been built"

Run `cd frontend && npm ci && npm run build`, or set `CHIMERA_STATIC_DIR` to the
built `dist` directory.

## Logs

- Docker: `docker compose logs -f chimera`
- Native: stdout of `scripts/start.sh`; set `CHIMERA_LOG_LEVEL=debug` for more.

Logs never contain message content or API keys.
