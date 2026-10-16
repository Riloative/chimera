# Security and privacy

Chimera treats "local-first" as a requirement: nothing leaves your
infrastructure unless you configure a connection that sends it somewhere, or an
administrator enables web access (then only when a user pastes a link or turns
on search; see [WEB.md](WEB.md)). Every
part of the app and API (except the health check, sign-in and registration)
requires an account.

## What Chimera never does

- No telemetry, analytics or crash reporting. No update checks unless an
  administrator turns them on (then one request a day to GitHub's release
  list, with nothing about the instance; see [Updates](#updates-and-the-supply-chain)).
- No requests to CDNs: fonts, KaTeX fonts, icons, scripts and both language
  catalogs are bundled. The Content-Security-Policy forbids loading anything
  from another origin (`default-src 'self'`).
- No remote images in model output (replaced with a placeholder with an
  explicit *Open image* link, so a response can't make your browser contact a
  tracker or carry data out in an image URL).
- No logging of message content, passwords, session tokens or API keys, in
  text or JSON log format (`CHIMERA_LOG_FORMAT=json`); access-log lines drop
  query strings, and web pages are logged by domain only.
- No email/SMTP or other cloud dependency for accounts.

## Accounts

| Topic | Implementation |
| --- | --- |
| Passwords | **Argon2id** (`argon2-cffi`), RFC 9106 parameters by default (t=3, m=64 MiB, p=4). The PHC string records the parameters; hashes made with weaker settings are upgraded transparently at the next successful sign-in. |
| Password policy | 10–128 characters, no composition rules; rejects a small bundled list of very common passwords, trivial repetitions, and passwords containing the login name or email name. |
| Login | One field accepts email or login name (case-insensitive). Failures always return the same generic message; unknown accounts still run a full Argon2 verification so timing doesn't reveal whether an account exists. A disabled account is only reported after a correct password. |
| Brute force | Failure counters in SQLite (they survive restarts) keyed by account and by client IP: after 5 failures in 15 minutes the account is locked for 60 s, doubling with each further failure up to 1 h. The per-IP limit is 4× higher (households behind one NAT). Registration is limited per IP per hour. All tunable via environment. |
| Registration | `CHIMERA_REGISTRATION_MODE`: `first_user_only` (default), `closed` or `open`; admins can change it in the UI. The first account is always the administrator. Optional `CHIMERA_SETUP_TOKEN` protects the very first registration. |
| Enumeration | Login and lockout responses are generic. Registration must say when an email or login name is already taken; that is an inherent trade-off of self-registration, mitigated by rate limiting and by `first_user_only`/`closed` being the defaults. |
| Recovery | No email is sent. An administrator can reset a password (temporary password, forced change at next sign-in, all sessions revoked), or use `python -m chimera.cli reset-password <login>` on the server. |

## Sessions and CSRF

- Server-side sessions: a 256-bit random token in an `HttpOnly; SameSite=Lax`
  cookie. The database stores only its SHA-256 hash, so a leaked database copy
  contains no usable sessions.
- Idle timeout (default 12 h) and absolute lifetime (default 7 days);
  "keep me signed in" sessions last 30 days. All configurable.
- Signing in rotates the session; signing out deletes it; changing the password
  revokes every other session; an admin reset or disabling an account revokes
  all of them. Settings → Account lists active sessions with per-session revoke.
- `Secure` flag: `CHIMERA_COOKIE_SECURE=auto` sets it whenever the request
  arrived over HTTPS (directly, or via a trusted proxy's `X-Forwarded-Proto`).
- **CSRF**: double-submit token. A readable `chimera_csrf` cookie must be
  echoed in an `X-CSRF-Token` header on every state-changing request, and once
  signed in it must match the token bound to the session (stored hashed). This
  also covers streaming `fetch` calls and Stop (cancellation is a normal POST).
  Requests with a foreign `Origin` are rejected outright.
- Tokens are never stored in `localStorage`.

## Data isolation

Every user-owned record carries an owner: conversations (and their messages),
prompts, attachments, knowledge collections (and their documents and chunks),
settings, and connections. Every endpoint is scoped by the signed-in user and
answers **404** for other people's records. Attachments live in per-user
directories under random IDs, and the server re-validates every path. See
[ENDPOINT_AUDIT.md](ENDPOINT_AUDIT.md) for the route-by-route audit.

### Connections (inference servers)

- **Personal** connections belong to one user.
- **Shared** connections are created by an administrator; every user can chat
  through them, only administrators can change them. The default
  `llama-server` connection created at first setup is shared.
- API keys are **write-only** in the UI ("set / not set") and **encrypted at
  rest** (Fernet: AES-128-CBC + HMAC-SHA256) with `CHIMERA_ENCRYPTION_KEY` or a
  generated `.encryption_key` file (mode 0600) in the data directory. Keys from
  older versions are encrypted at startup. Keep the key with your backups.
- **SSRF**: a connection URL makes the *server* send requests. Administrators
  choose what other users may enter (Settings → Users → Connection addresses):
  local and private networks only (default), an explicit allow-list, or any
  address. Administrators themselves are not restricted. Hostnames are resolved
  when a connection is saved or tested; a hostile DNS server could still change
  the answer later (DNS rebinding), so use the allow-list mode with IP ranges if
  you don't trust your users.

## Web reading and search

Off by default; see [WEB.md](WEB.md#security-model) for the full model.
In short:

- One outbound path (`web/fetcher.py`) for pages, `robots.txt` and search
  engines: `http(s)` only, no credentials in URLs, ports 80/443, the host is
  resolved by Chimera and **every** address must be public; the connection is
  pinned to the checked address (DNS rebinding) and each redirect hop is checked
  again. Loopback, private, CGNAT, link-local/metadata, ULA, multicast,
  embedded-IPv4 IPv6 forms, numeric IPv4 spellings, `localhost`, `*.local` and
  single-label names are blocked. An administrator allow-list can open specific
  internal hosts; the configured SearXNG address is exempt for search only.
- Streamed bodies with byte and decompressed-size caps, timeouts and a deadline,
  allowed content types, global/per-user concurrency limits, per-user hourly
  quotas, no cookies, no connection reuse, domain-only logging.
- No arbitrary-URL proxy: the browser only receives extracted text of sources
  it owns. Sources, cache entries and step logs are per user.
- Page text is delimited with a per-request random token, stripped of
  chat-template control tokens, and declared untrusted in the system prompt.
  This limits — but cannot eliminate — prompt injection with local models.
- Search-engine API keys are write-only and encrypted like connection keys.

### Headless browser (optional)

Running a real browser on pages chosen by users (or by search results) is the
riskiest web feature, so it is isolated in layers
([details](WEB.md#how-it-is-isolated)):

- separate container on an `internal: true` network (no route to the
  internet, host or LAN), no published port, shared-secret API;
- its only egress is Chimera's filter proxy, which applies the fetcher's rules
  (public addresses only, pinned DNS, ports, domain lists) to **every**
  connection the page makes, including redirects, scripts and `fetch()`;
  per-render credentials are revoked and tunnels closed when the render ends;
- loopback forced through the proxy, no local DNS, WebRTC disabled, service
  workers, downloads, popups and WebSockets blocked, a fresh profile per
  render, request/size/time limits and per-user quotas;
- non-root, read-only, capability-less container with memory/process limits.
  Chromium's internal sandbox is **off** (Playwright's default in containers),
  so a Chromium exploit would land in that container: keep the pinned image
  current, and leave the profile off if you don't need it.

The proxy listens on port 8701 inside Chimera's container (Compose) or
`127.0.0.1:8701` (native). Don't publish or forward it; without valid
per-render credentials it answers `407` to everything.

On `browser-net` the browser container can open TCP connections to exactly
one other container: Chimera, on 8700 (the normal API: every route needs a
session) and 8701 (the proxy: per-render credentials). Page scripts can't even
do that — their requests go through the proxy, which refuses single-label
names like `chimera`. Verified with the Compose stack: no route to the internet
or the Docker host from the browser container.

## Deployment modes

Don't assume something is safe because it's on `localhost`: any website you
visit can try to talk to local services.

### 1. Same machine (default)

`CHIMERA_HOST=127.0.0.1`. Accounts are still required. The `Host` header must be
a loopback name, which blocks DNS-rebinding attacks from websites; CSRF tokens
and the `Origin` check block cross-site requests.

### 2. Trusted local network

```bash
CHIMERA_HOST=0.0.0.0
CHIMERA_ALLOWED_HOSTS=chimera.lan,192.168.1.20
```

Passwords and session cookies cross the network **unencrypted** over plain
HTTP; anyone who can observe the traffic (shared Wi-Fi, a compromised device)
can capture them. That is acceptable only on a network you fully trust. Prefer
HTTPS even on a LAN (Caddy can issue an internal certificate with `tls internal`).

### 3. Reverse proxy / remote access

Terminate TLS in a reverse proxy and keep Chimera on loopback behind it:

```bash
CHIMERA_ALLOWED_HOSTS=chat.example.com
# Native: the proxy's address (127.0.0.1). Docker: the Compose network's
# gateway, because published-port traffic enters the container from there.
CHIMERA_TRUSTED_PROXIES=172.30.0.1
```

Complete Caddy, nginx and Cloudflare Tunnel examples, and how to find and pin
the gateway address: [REVERSE_PROXY.md](REVERSE_PROXY.md). Only list proxies
you control in `CHIMERA_TRUSTED_PROXIES`; otherwise clients could forge
`X-Forwarded-For` to dodge per-IP rate limits (Chimera ignores the header from
untrusted peers). With `CHIMERA_COOKIE_SECURE=auto` (default) the session
cookie becomes `Secure` when the proxy reports HTTPS, and responses then carry
`Strict-Transport-Security`.

Exposing Chimera to the internet: keep registration `closed` or
`first_user_only`, use strong passwords, and consider an extra layer (VPN such
as WireGuard/Tailscale, or proxy-level authentication). There is no 2FA yet.

### Docker

The bundled `docker-compose.yml` publishes the port on `127.0.0.1` only
(`CHIMERA_PUBLISH`). Requests reach the container from the Docker network's
gateway, so without a trusted reverse proxy all clients share one IP for
rate-limiting purposes (per-account limits still apply); with
`CHIMERA_TRUSTED_PROXIES` set to that gateway, the real client IPs are used.
The containers run unprivileged, read-only, without capabilities and with
`no-new-privileges`; no container gets the Docker socket.

Never publish the `browser` service's port or route a tunnel to it (e.g. a
Cloudflare Tunnel should point at Chimera only).

### Don't expose the inference server

`llama-server` has no user management. Keep it on `127.0.0.1` or an internal
Docker network and let Chimera be its only client; if it must listen on a
network, start it with `--api-key` and store that key in the connection.

## Content and upload safety

- Model output is rendered without raw HTML; links are sanitized and open with
  `rel="noopener noreferrer nofollow"`; KaTeX runs with `trust: false`.
- Uploads: allow-listed extensions, content sniffing (PDF and image magic
  bytes, NUL-byte check for text), size limits, nothing is executed, images are
  served with their sniffed type and `nosniff`.

## Headers

`Content-Security-Policy` (no inline scripts, `frame-ancestors 'none'`),
`X-Frame-Options: DENY`, `X-Content-Type-Options: nosniff`,
`Referrer-Policy: no-referrer`, `Cross-Origin-Opener-Policy`,
`Cross-Origin-Resource-Policy`, `Permissions-Policy`, and `no-store` caching for
API responses. Over HTTPS (directly or through a trusted proxy) also
`Strict-Transport-Security: max-age=31536000`. CORS is off;
`CHIMERA_CORS_ORIGINS` enables it for explicit origins only.

Session cookies are `HttpOnly`, `SameSite=Lax` and `Secure` over HTTPS; every
state-changing request needs the CSRF token (double submit, tied to the
session) and a same-origin `Origin`; login, registration and web requests are
rate limited. The registration default (`first_user_only`) and the setup token
written by the install script are suitable for internet-facing servers.

## Production checklist

Before exposing Chimera beyond your own machine:

- [ ] **HTTPS** through a reverse proxy or tunnel ([REVERSE_PROXY.md](REVERSE_PROXY.md)); Chimera itself stays on `127.0.0.1:8700`.
- [ ] **`CHIMERA_ALLOWED_HOSTS`** lists exactly your host names (never `*` on the internet).
- [ ] **`CHIMERA_TRUSTED_PROXIES`** is the proxy's address as seen by the container (Docker: the network gateway), nothing broader.
- [ ] **Registration** `first_user_only` or `closed`; the first admin account created with the **setup token**; strong passwords.
- [ ] **Backups**: `chimera backup` on a schedule and copied off the machine; a restore tried once.
- [ ] **Updates**: a plan for applying patch releases (`update.sh`, or the opt-in timer); security advisories watched (GitHub → Watch → Custom → Security alerts).
- [ ] **Firewall**: only 80/443 (or none, with a tunnel) open to the internet; SSH restricted.
- [ ] **Never publish** llama-server, SearXNG, the browser service, or the browser egress proxy (8701). If llama-server must listen on a network, use `--api-key`.
- [ ] `docker compose exec chimera chimera --check-config` (or the startup log) shows no configuration warnings.
- [ ] Image signatures verified (below) if you pull from a mirror or want proof of origin.

## Updates and the supply chain

- **Releases are built by CI only.** A tag `vX.Y.Z` triggers
  `.github/workflows/release.yml`, which runs the full test suite, builds the
  images for linux/amd64 and linux/arm64, scans them with Trivy (a fixable
  critical vulnerability fails the release), pushes them with SBOM and
  provenance attestations, and signs them with Sigstore cosign (keyless: the
  signature is bound to the workflow's GitHub identity, no long-lived key
  exists). Only the built-in `GITHUB_TOKEN` is used; third-party actions are
  pinned to commit SHAs; jobs have least-privilege permissions.
- **Release assets** (Compose file, `.env.example`, install and update
  scripts, systemd units) are listed in `SHA256SUMS`; `install-docker.sh` and
  `update.sh` refuse files that don't match.
- **Updating never happens from inside Chimera.** The in-app check only
  informs administrators; `update.sh` runs on the host, takes a backup, waits
  for the health check and rolls back on failure. No container mounts the
  Docker socket. Automatic updates are opt-in, patch-level by default, and
  never cross a major version.

### Verifying images

With [cosign](https://docs.sigstore.dev/cosign/system_config/installation/):

```bash
cosign verify ghcr.io/riloative/chimera:1.0.0 \
  --certificate-identity-regexp '^https://github\.com/Riloative/chimera/\.github/workflows/release\.yml@refs/tags/v' \
  --certificate-oidc-issuer https://token.actions.githubusercontent.com
```

The same for `ghcr.io/riloative/chimera-browser`. A successful verification
proves the image was built and signed by this repository's release workflow
from a version tag. The SBOM and build provenance are attached to the image:

```bash
docker buildx imagetools inspect ghcr.io/riloative/chimera:1.0.0 --format '{{ json .SBOM }}'
docker buildx imagetools inspect ghcr.io/riloative/chimera:1.0.0 --format '{{ json .Provenance }}'
```

## Not implemented (by design for this iteration)

Email verification, password reset by email (SMTP), two-factor authentication,
OAuth/SSO/LDAP, per-user quotas (beyond the hourly web limits), and audit logs
beyond the server log. The
account layer keeps email as a separate field and recovery behind one service
(`services/auth.py`), so an SMTP sender, TOTP or an external identity provider
can be added without changing the data model.

## Reporting

Please report vulnerabilities privately; see [SECURITY.md](../SECURITY.md)
in the repository root (process and supported versions).
