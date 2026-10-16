"""Egress filter proxy for the headless browser.

The browser container sits on an internal-only network; its only way out is
this HTTP proxy, which runs inside Chimera and applies the same rules as
Chimera's own fetcher (``netguard``) to every connection the browser makes,
including redirects, sub-resources and anything page scripts request:

* each CONNECT (https) or absolute-form request (http) is validated: scheme,
  port, host name, administrator domain lists; the name is resolved here and
  **every** address must be public, then the connection goes to that exact
  address (DNS pinning, so the browser cannot be steered by DNS rebinding);
* a redirect is a new request from the browser, so it is validated again;
* only credentials issued by :meth:`EgressProxy.session` for one render are
  accepted, and they stop working when that render ends (no orphaned
  connections: open tunnels of an ended session are closed);
* per-session limits on connections and bytes.
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import hmac
import logging
import secrets
from dataclasses import dataclass, field
from typing import AsyncIterator, Awaitable, Callable

from .errors import WebError
from .fetcher import Resolver, system_resolver
from .netguard import Allowlist, check_addresses, check_host_name, parse_url
from .urls import domain_matches

log = logging.getLogger("chimera.web")

Connector = Callable[[str, int], Awaitable[tuple[asyncio.StreamReader, asyncio.StreamWriter]]]

MAX_HEAD = 32 * 1024
IDLE_TIMEOUT = 30.0
CONNECT_TIMEOUT = 10.0


async def direct_connect(host: str, port: int) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
    return await asyncio.open_connection(host, port)


@dataclass
class EgressRules:
    allowed_ports: tuple[int, ...] = (80, 443)
    allowlist: Allowlist = field(default_factory=Allowlist)
    domain_allowlist: list[str] = field(default_factory=list)
    domain_denylist: list[str] = field(default_factory=list)
    max_connections: int = 300
    max_open: int = 32
    max_bytes: int = 64 * 1024 * 1024


@dataclass
class EgressSession:
    token: str
    rules: EgressRules
    connections: int = 0
    open: int = 0
    bytes: int = 0
    blocked: list[dict] = field(default_factory=list)
    hosts: list[str] = field(default_factory=list)
    writers: set[asyncio.StreamWriter] = field(default_factory=set)
    closed: bool = False

    def block(self, host: str, code: str) -> None:
        if len(self.blocked) < 50:
            self.blocked.append({"host": host, "code": code})


class EgressProxy:
    def __init__(self, resolver: Resolver | None = None, connector: Connector | None = None):
        self.resolver = resolver or system_resolver
        self.connector = connector or direct_connect
        self.sessions: dict[str, EgressSession] = {}
        self._server: asyncio.base_events.Server | None = None

    # ------------------------------------------------------------ lifecycle
    async def start(self, host: str, port: int) -> None:
        self._server = await asyncio.start_server(self._handle, host, port, limit=MAX_HEAD)
        log.info("browser egress proxy listening on %s:%d", host, self.port)

    @property
    def port(self) -> int:
        assert self._server is not None
        return self._server.sockets[0].getsockname()[1]

    @property
    def running(self) -> bool:
        return self._server is not None

    async def stop(self) -> None:
        for session in list(self.sessions.values()):
            self._end(session)
        if self._server is not None:
            self._server.close()
            with contextlib.suppress(Exception):
                await self._server.wait_closed()
            self._server = None

    @contextlib.asynccontextmanager
    async def session(self, rules: EgressRules) -> AsyncIterator[EgressSession]:
        """Single-use proxy credentials for one render."""
        session = EgressSession(token=secrets.token_urlsafe(24), rules=rules)
        self.sessions[session.token] = session
        try:
            yield session
        finally:
            self._end(session)

    def _end(self, session: EgressSession) -> None:
        session.closed = True
        self.sessions.pop(session.token, None)
        for writer in list(session.writers):
            with contextlib.suppress(Exception):
                writer.close()

    # ------------------------------------------------------------- handling
    def _authenticate(self, headers: dict[str, str]) -> EgressSession | None:
        value = headers.get("proxy-authorization", "")
        if not value.lower().startswith("basic "):
            return None
        try:
            user, _, password = base64.b64decode(value[6:].strip(), validate=True).decode().partition(":")
        except (ValueError, UnicodeDecodeError):
            return None
        session = self.sessions.get(password)
        if session is None or user != "render" or not hmac.compare_digest(session.token, password):
            return None
        return session

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        session: EgressSession | None = None
        upstream: asyncio.StreamWriter | None = None
        counted = False
        try:
            try:
                async with asyncio.timeout(IDLE_TIMEOUT):
                    head = await reader.readuntil(b"\r\n\r\n")
            except (asyncio.IncompleteReadError, asyncio.LimitOverrunError, TimeoutError, ConnectionError):
                return
            try:
                method, target, version, headers, raw_headers = parse_head(head)
            except ValueError:
                await reply(writer, 400, "Bad Request")
                return
            session = self._authenticate(headers)
            if session is None:
                await reply(writer, 407, "Proxy Authentication Required", {"Proxy-Authenticate": 'Basic realm="chimera"'})
                return
            if session.connections >= session.rules.max_connections or session.open >= session.rules.max_open:
                session.block("", "web.browser_limit")
                await reply(writer, 429, "Too Many Requests")
                return
            session.connections += 1
            session.open += 1
            counted = True
            session.writers.add(writer)
            if method == "CONNECT":
                url = f"https://{target}/"
            elif target.lower().startswith("http://"):
                url = target
            else:  # origin-form, https-in-plain-proxy or other schemes
                session.block(target[:100], "web.unsupported_scheme")
                await reply(writer, 400, "Bad Request")
                return
            try:
                host, address, port, path = await self._validate(url, session)
            except WebError as exc:
                session.block(_host(url), exc.code)
                log.info("browser egress blocked host=%s code=%s", _host(url), exc.code)
                await reply(writer, 403, "Forbidden", {"X-Chimera-Block": exc.code})
                return
            try:
                async with asyncio.timeout(CONNECT_TIMEOUT):
                    up_reader, upstream = await self.connector(address, port)
            except (OSError, TimeoutError):
                await reply(writer, 502, "Bad Gateway")
                return
            session.writers.add(upstream)
            if host not in session.hosts and len(session.hosts) < 100:
                session.hosts.append(host)
            if method == "CONNECT":
                writer.write(b"HTTP/1.1 200 Connection Established\r\n\r\n")
                await writer.drain()
            else:
                upstream.write(rewrite_request(method, path, version, raw_headers))
                await upstream.drain()
            await self._pipe(session, reader, writer, up_reader, upstream)
        except (ConnectionError, asyncio.CancelledError):
            pass
        finally:
            if session is not None:
                if counted:
                    session.open -= 1
                session.writers.discard(writer)
                if upstream is not None:
                    session.writers.discard(upstream)
            for w in (upstream, writer):
                if w is not None:
                    with contextlib.suppress(Exception):
                        w.close()

    async def _validate(self, url: str, session: EgressSession) -> tuple[str, str, int, str]:
        rules = session.rules
        if session.closed:
            raise WebError("web.browser_blocked", "The render has ended.")
        target = parse_url(url, allowed_ports=rules.allowed_ports, allowlist=rules.allowlist)
        host = target.host
        if rules.domain_denylist and domain_matches(host, rules.domain_denylist):
            raise WebError("web.domain_blocked", "Your administrator has blocked this site.", host=host)
        if rules.domain_allowlist and not domain_matches(host, rules.domain_allowlist):
            raise WebError("web.domain_not_allowed", "Your administrator only allows reading selected sites.", host=host)
        check_host_name(target, rules.allowlist)
        addresses = [target.ip] if target.ip else await self.resolver(target.host, target.port)
        addresses = check_addresses(target, addresses, rules.allowlist)
        return host, str(addresses[0]), target.port, target.path

    async def _pipe(self, session: EgressSession, client_r, client_w, up_r, up_w) -> None:
        async def copy(src: asyncio.StreamReader, dst: asyncio.StreamWriter) -> None:
            try:
                while True:
                    async with asyncio.timeout(IDLE_TIMEOUT):
                        chunk = await src.read(65536)
                    if not chunk:
                        break
                    session.bytes += len(chunk)
                    if session.bytes > session.rules.max_bytes or session.closed:
                        if session.bytes > session.rules.max_bytes:
                            session.block("", "web.browser_limit")
                        break
                    dst.write(chunk)
                    await dst.drain()
            except (ConnectionError, TimeoutError, asyncio.IncompleteReadError):
                pass
            finally:
                with contextlib.suppress(Exception):
                    dst.close()

        await asyncio.gather(copy(client_r, up_w), copy(up_r, client_w))


# ------------------------------------------------------------------- helpers
def parse_head(head: bytes) -> tuple[str, str, str, dict[str, str], list[tuple[str, str]]]:
    text = head.decode("latin-1")
    lines = text.split("\r\n")
    parts = lines[0].split(" ")
    if len(parts) != 3 or not parts[2].startswith("HTTP/1."):
        raise ValueError("bad request line")
    method, target, version = parts
    raw: list[tuple[str, str]] = []
    headers: dict[str, str] = {}
    for line in lines[1:]:
        if not line:
            continue
        if ":" not in line or line[0] in " \t":
            raise ValueError("bad header")
        key, value = line.split(":", 1)
        raw.append((key.strip(), value.strip()))
        headers[key.strip().lower()] = value.strip()
    return method.upper(), target, version, headers, raw


HOP_HEADERS = {"proxy-authorization", "proxy-connection", "connection", "keep-alive", "te", "trailer", "upgrade"}


def rewrite_request(method: str, path: str, version: str, headers: list[tuple[str, str]]) -> bytes:
    """Absolute-form proxy request → origin-form request for one upstream
    connection (closed after the response, so a kept-alive client connection
    can't carry a request for a different, unvalidated host)."""
    lines = [f"{method} {path} {version}"]
    lines += [f"{k}: {v}" for k, v in headers if k.lower() not in HOP_HEADERS]
    lines.append("Connection: close")
    return ("\r\n".join(lines) + "\r\n\r\n").encode("latin-1")


async def reply(writer: asyncio.StreamWriter, status: int, reason: str, headers: dict[str, str] | None = None) -> None:
    extra = "".join(f"{k}: {v}\r\n" for k, v in (headers or {}).items())
    with contextlib.suppress(Exception):
        writer.write(f"HTTP/1.1 {status} {reason}\r\n{extra}Content-Length: 0\r\nConnection: close\r\n\r\n".encode())
        await writer.drain()


def _host(url: str) -> str:
    from .urls import host_of

    try:
        return host_of(url) or "?"
    except Exception:  # noqa: BLE001
        return "?"
