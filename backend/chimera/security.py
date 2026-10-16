"""Access control and hardening (pure ASGI middleware).

Every ``/api/*`` request passes through here *before* routing, so a route is
protected even if its author forgets: only the paths in ``PUBLIC_API`` are
reachable without a valid session. Then, for state-changing requests:

* **CSRF** – double-submit token: the ``X-CSRF-Token`` header must equal the
  ``chimera_csrf`` cookie and, once logged in, the token bound to the session
  (stored hashed). Cross-site pages can neither read the cookie nor set the
  header without a CORS preflight, which is only granted to configured origins.
* **Origin** – a foreign ``Origin`` header is rejected outright.
* **DNS rebinding** – the ``Host`` header must be in ``CHIMERA_ALLOWED_HOSTS``.

Client IPs (used for rate limiting) honour ``X-Forwarded-For`` only when the
direct peer is a configured trusted proxy, so they cannot be spoofed.
"""

from __future__ import annotations

import ipaddress
import json
from http.cookies import SimpleCookie
from typing import Any

from .config import Config

SESSION_COOKIE = "chimera_session"
CSRF_COOKIE = "chimera_csrf"
CSRF_HEADER = b"x-csrf-token"
PUBLIC_API = {"/api/health", "/api/ready", "/api/auth/status", "/api/auth/login", "/api/auth/register", "/api/auth/logout"}
# Reachable while an account must replace a temporary password.
PASSWORD_CHANGE_PATHS = {"/api/auth/me", "/api/account/password"}
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
    (b"cross-origin-resource-policy", b"same-origin"),
    (b"permissions-policy", b"camera=(), microphone=(), geolocation=()"),
    (b"content-security-policy", CSP.encode()),
]


def host_without_port(host: str) -> str:
    host = host.strip().lower()
    if host.startswith("["):
        return host[: host.index("]") + 1] if "]" in host else host
    return host.rsplit(":", 1)[0] if host.count(":") == 1 else host


def parse_cookies(headers: dict[bytes, bytes]) -> dict[str, str]:
    raw = headers.get(b"cookie")
    if not raw:
        return {}
    jar = SimpleCookie()
    try:
        jar.load(raw.decode("latin-1"))
    except Exception:  # noqa: BLE001
        return {}
    return {k: m.value for k, m in jar.items()}


class ProxyResolver:
    def __init__(self, trusted: list[str]):
        self.networks = []
        for entry in trusted:
            try:
                self.networks.append(ipaddress.ip_network(entry.strip(), strict=False))
            except ValueError:
                continue

    def trusted(self, ip: str) -> bool:
        try:
            addr = ipaddress.ip_address(ip)
        except ValueError:
            return False
        return any(addr in n for n in self.networks)

    def client(self, peer: str, headers: dict[bytes, bytes]) -> tuple[str, bool]:
        """(client_ip, forwarded_https)."""
        if not self.networks or not self.trusted(peer):
            return peer, False
        forwarded = headers.get(b"x-forwarded-for", b"").decode("latin-1")
        hops = [h.strip() for h in forwarded.split(",") if h.strip()]
        client = peer
        # Walk from the nearest hop outwards, skipping our own proxies.
        for hop in reversed(hops):
            client = hop
            if not self.trusted(hop):
                break
        proto = headers.get(b"x-forwarded-proto", b"").decode("latin-1").split(",")[0].strip().lower()
        return client, proto == "https"


def request_is_https(scope) -> bool:
    return scope.get("scheme") == "https" or bool(scope.get("state", {}).get("forwarded_https"))


def cookie_secure(config: Config, scope) -> bool:
    if config.cookie_secure == "true":
        return True
    if config.cookie_secure == "false":
        return False
    return request_is_https(scope)


class SecurityMiddleware:
    def __init__(self, app, config: Config):
        self.app = app
        self.config = config
        self.allowed_hosts = {h.lower() for h in config.allowed_hosts}
        self.allowed_origins = {o.rstrip("/").lower() for o in config.cors_origins}
        self.proxies = ProxyResolver(config.trusted_proxies)

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        headers = dict(scope.get("headers") or [])
        path: str = scope.get("path", "")
        method: str = scope.get("method", "GET")
        peer = (scope.get("client") or ("", 0))[0]
        state = scope.setdefault("state", {})
        state["client_ip"], state["forwarded_https"] = self.proxies.client(peer, headers)

        if "*" not in self.allowed_hosts:
            host = host_without_port(headers.get(b"host", b"").decode("latin-1"))
            if host not in self.allowed_hosts:
                return await _reject(send, 400, "host_not_allowed", "Host not allowed. Add it to CHIMERA_ALLOWED_HOSTS.")

        is_api = path == "/api" or path.startswith("/api/")
        if is_api:
            if method == "OPTIONS":
                return await self.app(scope, receive, self._wrap(send, True))
            if method not in SAFE_METHODS:
                origin = headers.get(b"origin", b"").decode("latin-1").rstrip("/").lower()
                if origin and origin != "null":
                    own = origin.split("://", 1)[-1] == headers.get(b"host", b"").decode("latin-1").lower()
                    if not own and origin not in self.allowed_origins:
                        return await _reject(send, 403, "origin_forbidden", "Cross-origin request blocked.")

            cookies = parse_cookies(headers)
            app_state = scope["app"].state.chimera
            info = app_state.auth.resolve(cookies.get(SESSION_COOKIE))
            if info:
                state["user"], state["session"] = info.user, info.session
            if path not in PUBLIC_API:
                if not info:
                    return await _reject(send, 401, "auth_required", "Authentication required.")
                if info.user["must_change_password"] and path not in PASSWORD_CHANGE_PATHS:
                    return await _reject(send, 403, "password_change_required", "Choose a new password to continue.")
            if method not in SAFE_METHODS:
                token = headers.get(CSRF_HEADER, b"").decode("latin-1") or None
                if not app_state.auth.csrf_valid(info.session if info else None, token, cookies.get(CSRF_COOKIE)):
                    return await _reject(send, 403, "csrf_failed", "Security token missing or expired. Reload the page.")

        return await self.app(scope, receive, self._wrap(send, is_api))

    @staticmethod
    def _wrap(send, is_api: bool):
        async def send_with_headers(message: dict[str, Any]):
            if message["type"] == "http.response.start":
                existing = {k.lower() for k, _ in message.get("headers", [])}
                extra = [(k, v) for k, v in SECURITY_HEADERS if k not in existing]
                if is_api:
                    extra.append((b"cache-control", b"no-store"))
                message["headers"] = list(message.get("headers", [])) + extra
            await send(message)

        return send_with_headers


async def _reject(send, status: int, code: str, message: str) -> None:
    body = json.dumps({"detail": {"code": code, "message": message, "params": {}}}).encode()
    await send(
        {
            "type": "http.response.start",
            "status": status,
            "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode()),
                        (b"cache-control", b"no-store"), *SECURITY_HEADERS],
        }
    )
    await send({"type": "http.response.body", "body": body})
