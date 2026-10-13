"""Access control and hardening.

Threat model (see docs/SECURITY.md):

* **Same machine, no password** — only loopback clients are served. The Host
  header must be a loopback name (blocks DNS-rebinding attacks from websites
  you visit), and every state-changing API call must carry the
  ``X-Chimera-Request`` header, which browsers refuse to send cross-origin
  without a CORS preflight that this server never approves (blocks CSRF).
* **LAN / reverse proxy** — set ``CHIMERA_PASSWORD``. All API calls then need
  a signed, HttpOnly, SameSite=Strict session cookie.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import ipaddress
import json
import time
from http.cookies import SimpleCookie
from typing import Any

from .config import Config

SESSION_COOKIE = "chimera_session"
CSRF_HEADER = b"x-chimera-request"
PUBLIC_API = {"/api/health", "/api/auth/status", "/api/auth/login", "/api/auth/logout"}
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}

CSP = (
    "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data: blob:; font-src 'self' data:; connect-src 'self'; "
    "object-src 'none'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
)
SECURITY_HEADERS = [
    (b"x-content-type-options", b"nosniff"),
    (b"referrer-policy", b"no-referrer"),
    (b"x-frame-options", b"DENY"),
    (b"cross-origin-opener-policy", b"same-origin"),
    (b"permissions-policy", b"camera=(), microphone=(), geolocation=()"),
    (b"content-security-policy", CSP.encode()),
]


class SessionSigner:
    def __init__(self, secret: str, days: int):
        self.key = hashlib.sha256(("chimera-session:" + secret).encode()).digest()
        self.max_age = days * 86400

    def issue(self) -> str:
        payload = base64.urlsafe_b64encode(json.dumps({"exp": int(time.time()) + self.max_age}).encode()).decode()
        sig = hmac.new(self.key, payload.encode(), hashlib.sha256).hexdigest()
        return f"{payload}.{sig}"

    def verify(self, token: str | None) -> bool:
        if not token or "." not in token:
            return False
        payload, sig = token.rsplit(".", 1)
        expected = hmac.new(self.key, payload.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(sig, expected):
            return False
        try:
            data = json.loads(base64.urlsafe_b64decode(payload.encode()))
            return int(data["exp"]) > time.time()
        except (ValueError, KeyError, TypeError):
            return False


def is_loopback(host: str | None) -> bool:
    if not host:
        return False
    try:
        return ipaddress.ip_address(host.split("%")[0]).is_loopback
    except ValueError:
        return host == "localhost" or host == "testclient"


def host_without_port(host: str) -> str:
    host = host.strip().lower()
    if host.startswith("["):
        return host[: host.index("]") + 1] if "]" in host else host
    return host.rsplit(":", 1)[0] if host.count(":") == 1 else host


class LoginLimiter:
    """Tiny in-memory brute-force brake: 5 failures → 30 s lockout per client."""

    def __init__(self, attempts: int = 5, window: float = 30.0):
        self.attempts = attempts
        self.window = window
        self.failures: dict[str, list[float]] = {}

    def blocked(self, client: str) -> float:
        now = time.monotonic()
        recent = [t for t in self.failures.get(client, []) if now - t < self.window]
        self.failures[client] = recent
        if len(recent) >= self.attempts:
            return self.window - (now - recent[0])
        return 0.0

    def fail(self, client: str) -> None:
        self.failures.setdefault(client, []).append(time.monotonic())

    def reset(self, client: str) -> None:
        self.failures.pop(client, None)


def _cookie(headers: dict[bytes, bytes], name: str) -> str | None:
    raw = headers.get(b"cookie")
    if not raw:
        return None
    jar = SimpleCookie()
    try:
        jar.load(raw.decode("latin-1"))
    except Exception:  # noqa: BLE001
        return None
    morsel = jar.get(name)
    return morsel.value if morsel else None


class SecurityMiddleware:
    def __init__(self, app, config: Config, signer: SessionSigner):
        self.app = app
        self.config = config
        self.signer = signer
        self.allowed_hosts = {h.lower() for h in config.allowed_hosts}

    def is_authenticated(self, scope) -> bool:
        headers = dict(scope.get("headers") or [])
        return self.signer.verify(_cookie(headers, SESSION_COOKIE))

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        headers = dict(scope.get("headers") or [])
        path: str = scope.get("path", "")
        method: str = scope.get("method", "GET")
        client = (scope.get("client") or ("", 0))[0]

        if "*" not in self.allowed_hosts:
            host = host_without_port(headers.get(b"host", b"").decode("latin-1"))
            if host not in self.allowed_hosts:
                return await _reject(send, 400, "Host not allowed. Add it to CHIMERA_ALLOWED_HOSTS.", "host_not_allowed")

        is_api = path.startswith("/api/") or path == "/api"
        if is_api and method not in SAFE_METHODS:
            if headers.get(CSRF_HEADER) != b"1":
                return await _reject(send, 403, "Missing X-Chimera-Request header.", "csrf")
            origin = headers.get(b"origin")
            if origin and origin != b"null":
                origin_host = origin.decode("latin-1").split("://", 1)[-1]
                if origin_host.lower() != headers.get(b"host", b"").decode("latin-1").lower():
                    return await _reject(send, 403, "Cross-origin request blocked.", "csrf")

        if self.config.auth_enabled:
            if is_api and path not in PUBLIC_API and not self.is_authenticated(scope):
                return await _reject(send, 401, "Authentication required.", "unauthenticated")
        elif not self.config.allow_unauthenticated_remote and not is_loopback(client):
            return await _reject(
                send,
                403,
                "Remote access is disabled. Set CHIMERA_PASSWORD to allow access from other devices.",
                "remote_forbidden",
            )

        async def send_with_headers(message: dict[str, Any]):
            if message["type"] == "http.response.start":
                existing = {k.lower() for k, _ in message.get("headers", [])}
                extra = [(k, v) for k, v in SECURITY_HEADERS if k not in existing]
                if is_api:
                    extra.append((b"cache-control", b"no-store"))
                message["headers"] = list(message.get("headers", [])) + extra
            await send(message)

        return await self.app(scope, receive, send_with_headers)


async def _reject(send, status: int, message: str, code: str) -> None:
    body = json.dumps({"detail": message, "code": code}).encode()
    await send(
        {
            "type": "http.response.start",
            "status": status,
            "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode()), *SECURITY_HEADERS],
        }
    )
    await send({"type": "http.response.body", "body": body})
