# Security and privacy

Chimera treats "local-first" as a requirement: nothing leaves your
infrastructure unless you configure a connection that sends it somewhere.

## What Chimera never does

- No accounts, telemetry, analytics, crash reporting or update checks.
- No requests to CDNs: fonts (Inter, JetBrains Mono), KaTeX fonts, icons and all
  scripts are bundled at build time. The Content-Security-Policy forbids loading
  anything from another origin (`default-src 'self'`).
- No remote images in model output: Markdown images pointing at other hosts are
  replaced with a placeholder, so a response can't make your browser contact a
  tracker.
- No logging of message content, prompts or API keys.
- No hardcoded credentials. API keys you enter are stored in the local database,
  never sent back to the browser (the UI only learns *whether* a key exists), and
  excluded from JSON exports. (Database backups contain them; protect backups.)

## Deployment modes

Chimera distinguishes three situations. Don't assume something is safe because
it's on `localhost`: any website you visit can try to talk to local services.

### 1. Same machine (default)

`CHIMERA_HOST=127.0.0.1`, no password.

- Only loopback clients are served (others get `403`).
- **DNS rebinding:** the `Host` header must be a loopback name
  (`localhost`, `127.0.0.1`, `[::1]`), so a malicious domain re-pointed at
  127.0.0.1 is rejected.
- **CSRF:** every state-changing API request must carry
  `X-Chimera-Request: 1`. Browsers won't add custom headers cross-origin without
  a CORS preflight, and Chimera never answers preflights (no CORS). Requests with
  a foreign `Origin` are rejected as well.

### 2. Trusted local network

```bash
CHIMERA_HOST=0.0.0.0
CHIMERA_PASSWORD=a-long-random-passphrase
CHIMERA_ALLOWED_HOSTS=chimera.lan,192.168.1.20   # recommended
```

- Every API call requires a session (HMAC-signed, `HttpOnly`,
  `SameSite=Strict` cookie, 30 days by default).
- Login is rate-limited (5 failures → 30 s lockout per client).
- Traffic is plain HTTP on the LAN. Use HTTPS (below) if the network isn't fully trusted.

### 3. Reverse proxy / remote access

Terminate TLS in a reverse proxy (Caddy, nginx, Traefik) and keep Chimera on
loopback behind it:

```bash
CHIMERA_HOST=127.0.0.1
CHIMERA_PASSWORD=...
CHIMERA_SECURE_COOKIES=true
CHIMERA_ALLOWED_HOSTS=chat.example.com
```

Example Caddyfile:

```
chat.example.com {
    reverse_proxy 127.0.0.1:8700 {
        flush_interval -1     # don't buffer streamed responses
    }
}
```

nginx: set `proxy_buffering off;` and `proxy_read_timeout 600s;` for `/api/`.

Without a password, Chimera refuses non-loopback clients. The escape hatch
`CHIMERA_ALLOW_UNAUTHENTICATED_REMOTE=true` exists for Docker, where requests
arrive from the bridge network; the shipped `docker-compose.yml` publishes the
port on `127.0.0.1` only, which is what keeps that setup private. Don't combine
this flag with a public port mapping.

### Don't expose the inference server

`llama-server` has no user management, and anyone who can reach it can use your
GPU. Keep it on `127.0.0.1` (or an internal Docker network) and let Chimera be
the only client. If it must listen on the network, start it with `--api-key`.

## Content safety

- Model output is rendered with `react-markdown` **without** raw HTML support:
  `<script>`, `<img onerror>`, `<iframe>` etc. appear as text.
- Link URLs are sanitized (`javascript:`/`vbscript:` removed) and open in a new
  tab with `rel="noopener noreferrer nofollow"`.
- KaTeX runs with `trust: false` (no `\href`, `\url` side effects).

## Uploads

- Allow-listed extensions; content is sniffed (PDF magic bytes, PNG/JPEG/GIF/WebP
  signatures, NUL-byte check for text). A `.png` that isn't an image is rejected.
- Size limits (configurable, default 10 MB per attachment).
- Nothing uploaded is executed. Text is extracted in-process (pypdf for PDF).
- Filenames are sanitized; images are stored under random IDs inside the data
  directory, never at a user-controlled path, and served with their sniffed type
  and `X-Content-Type-Options: nosniff`.

## Other hardening

- Strict security headers on every response (CSP, `X-Frame-Options: DENY`,
  `Referrer-Policy: no-referrer`, `nosniff`, `Permissions-Policy`).
- Input validation with Pydantic on every endpoint; provider URLs must be
  `http(s)`; auth header names are restricted to token characters.
- The container runs as an unprivileged user (`uid 10001`).
- Outbound provider requests ignore `HTTP(S)_PROXY` variables by default so that
  local traffic is never routed through a proxy unexpectedly.

## Reporting

If you find a vulnerability, please open a private security advisory on the
repository rather than a public issue.
