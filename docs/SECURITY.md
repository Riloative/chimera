# Security and privacy

Chimera treats "local-first" as a requirement: nothing leaves your
infrastructure unless you configure a connection that sends it somewhere, or an
administrator enables web access (then only when a user pastes a link or turns
on search; see [WEB.md](WEB.md)). Every
part of the app and API (except the health check, sign-in and registration)
requires an account.

## What Chimera never does

- No telemetry, analytics, crash reporting or update checks.
- No requests to CDNs: fonts, KaTeX fonts, icons, scripts and both language
  catalogs are bundled. The Content-Security-Policy forbids loading anything
  from another origin (`default-src 'self'`).
- No remote images in model output (replaced with a placeholder with an
  explicit *Open image* link, so a response can't make your browser contact a
  tracker or carry data out in an image URL).
- No logging of message content, passwords, session tokens or API keys.
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
CHIMERA_HOST=127.0.0.1
CHIMERA_TRUSTED_PROXIES=127.0.0.1
CHIMERA_ALLOWED_HOSTS=chat.example.com
CHIMERA_COOKIE_SECURE=true
```

```
# Caddyfile
chat.example.com {
    reverse_proxy 127.0.0.1:8700 {
        flush_interval -1     # don't buffer streamed responses
    }
}
```

nginx: `proxy_buffering off;`, `proxy_read_timeout 600s;` and set
`X-Forwarded-For`/`X-Forwarded-Proto`. Only list proxies you control in
`CHIMERA_TRUSTED_PROXIES`; otherwise clients could forge `X-Forwarded-For` to
dodge per-IP rate limits (Chimera ignores the header from untrusted peers).

Exposing Chimera to the internet: keep registration `closed` or
`first_user_only`, use strong passwords, and consider an extra layer (VPN such
as WireGuard/Tailscale, or proxy-level authentication). There is no 2FA yet.

### Docker

The bundled `docker-compose.yml` publishes the port on `127.0.0.1` only.
Requests reach the container from the Docker bridge network, so all clients
share one IP for rate-limiting purposes; per-account limits still apply.

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
API responses. CORS is off; `CHIMERA_CORS_ORIGINS` enables it for explicit
origins only.

## Not implemented (by design for this iteration)

Email verification, password reset by email (SMTP), two-factor authentication,
OAuth/SSO/LDAP, per-user quotas (beyond the hourly web limits), and audit logs
beyond the server log. The
account layer keeps email as a separate field and recovery behind one service
(`services/auth.py`), so an SMTP sender, TOTP or an external identity provider
can be added without changing the data model.

## Reporting

If you find a vulnerability, please open a private security advisory on the
repository rather than a public issue.
