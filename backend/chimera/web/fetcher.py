"""The single outbound HTTP component for the web feature.

Every page fetch and every search-engine call goes through
:meth:`SafeFetcher.fetch`. It

* validates the URL and resolves the host itself (``netguard``), then connects
  to the validated IP address (the host name is kept for the ``Host`` header
  and for TLS certificate verification via SNI), so a hostile DNS server
  cannot answer differently between check and use;
* follows redirects manually, re-validating every hop;
* never reuses connections (a pooled TLS connection to one IP could otherwise
  serve a request for a different host name) and never stores or sends
  cookies, so nothing leaks between users;
* streams the body and stops at a byte limit, decompressing gzip/deflate
  itself with an output cap (decompression-bomb protection);
* enforces connect/read timeouts and an overall deadline;
* logs only the domain and outcome unless verbose logging is enabled.
"""

from __future__ import annotations

import asyncio
import ipaddress
import logging
import socket
import time
import zlib
from dataclasses import dataclass, field
from email.message import Message
from http.cookiejar import CookieJar, DefaultCookiePolicy
from typing import Awaitable, Callable
from urllib.parse import urljoin

import httpx

from .errors import WebError
from .netguard import Allowlist, IPAddress, Target, check_addresses, check_host_name, parse_url

log = logging.getLogger("chimera.web")

Resolver = Callable[[str, int], Awaitable[list[IPAddress]]]

REDIRECT_STATUSES = {301, 302, 303, 307, 308}
PAGE_TYPES = (
    "text/html", "application/xhtml+xml", "text/plain", "text/markdown", "text/x-markdown", "text/csv",
    "application/json", "application/ld+json", "text/xml", "application/xml", "application/rss+xml",
    "application/atom+xml", "application/pdf", "text/x-rst",
)
JSON_TYPES = ("application/json",)


async def system_resolver(host: str, port: int) -> list[IPAddress]:
    loop = asyncio.get_running_loop()
    try:
        infos = await loop.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except (OSError, UnicodeError):
        return []
    out: list[IPAddress] = []
    for info in infos:
        try:
            out.append(ipaddress.ip_address(str(info[4][0]).split("%")[0]))
        except ValueError:
            continue
    return out


@dataclass
class FetchPolicy:
    allowed_ports: tuple[int, ...] = (80, 443)
    allowlist: Allowlist = field(default_factory=Allowlist)
    # Skip the private-address block (only for administrator-configured endpoints
    # such as a self-hosted SearXNG). Never set for user-supplied URLs.
    trusted_endpoint: bool = False
    max_bytes: int = 10 * 1024 * 1024
    connect_timeout: float = 8.0
    read_timeout: float = 15.0
    total_timeout: float = 25.0
    max_redirects: int = 5
    accept_types: tuple[str, ...] = PAGE_TYPES
    user_agent: str = "Chimera"
    accept: str = "text/html,application/xhtml+xml,application/xml;q=0.9,text/plain;q=0.8,application/pdf;q=0.8,*/*;q=0.5"
    accept_language: str | None = None


@dataclass
class FetchResult:
    url: str  # what was requested (validated form)
    final_url: str
    status: int
    content_type: str
    charset: str | None
    body: bytes
    elapsed_ms: float
    redirects: int = 0
    headers: dict[str, str] = field(default_factory=dict)


def parse_content_type(value: str | None) -> tuple[str, str | None]:
    if not value:
        return "", None
    msg = Message()
    msg["content-type"] = value
    return msg.get_content_type().lower(), msg.get_param("charset")  # type: ignore[return-value]


def sniff_type(body: bytes) -> str:
    head = body[:1024].lstrip().lower()
    if body.startswith(b"%PDF-"):
        return "application/pdf"
    if head.startswith((b"<!doctype html", b"<html", b"<head", b"<body")):
        return "text/html"
    if head.startswith(b"<?xml") or head.startswith((b"<rss", b"<feed")):
        return "application/xml"
    if head[:1] in (b"{", b"["):
        return "application/json"
    return "text/plain"


def type_allowed(ctype: str, accepted: tuple[str, ...]) -> bool:
    if ctype in accepted:
        return True
    if ctype.endswith("+json") and "application/json" in accepted:
        return True
    return ctype.endswith("+xml") and "application/xml" in accepted


class _Decoder:
    """Incremental gzip/deflate decoding with a hard output limit."""

    def __init__(self, encoding: str):
        enc = encoding.strip().lower()
        self.kind = enc
        self._d: "zlib._Decompress | None" = None
        if enc in ("", "identity"):
            self.kind = ""
        elif enc in ("gzip", "x-gzip"):
            self._d = zlib.decompressobj(16 + zlib.MAX_WBITS)
        elif enc == "deflate":
            self._d = None  # decided on the first byte (zlib-wrapped or raw)
        else:
            raise WebError("web.unsupported_encoding", "The site used an unsupported compression format.")

    def feed(self, chunk: bytes, room: int) -> bytes:
        if not self.kind:
            return chunk
        if self._d is None:
            wrapped = bool(chunk) and (chunk[0] & 0x0F) == 8
            self._d = zlib.decompressobj(zlib.MAX_WBITS if wrapped else -zlib.MAX_WBITS)
        out = bytearray()
        data = chunk
        try:
            while data:
                piece = self._d.decompress(data, max(1, room + 1 - len(out)))
                out += piece
                if len(out) > room:
                    return bytes(out)  # caller reports "too large"
                data = self._d.unconsumed_tail
                if not piece and data == chunk:
                    break
        except zlib.error as exc:
            raise WebError("web.decode_failed", "The page's compressed data is corrupt.") from exc
        return bytes(out)


def _no_cookies() -> httpx.Cookies:
    return httpx.Cookies(CookieJar(policy=DefaultCookiePolicy(allowed_domains=[])))


def make_client(proxy: str | None = None, transport: httpx.AsyncBaseTransport | None = None) -> httpx.AsyncClient:
    """The HTTP client used for web traffic (separate from the provider client)."""
    client = httpx.AsyncClient(
        trust_env=False,  # never pick up proxies/credentials from the environment
        follow_redirects=False,
        cookies=_no_cookies(),
        limits=httpx.Limits(max_connections=64, max_keepalive_connections=0),
        proxy=proxy if transport is None else None,
        transport=transport,
        http2=False,
    )
    # httpx copies the jar passed in; install the refuse-everything policy on
    # the jar it actually uses, so no cookie is ever stored or sent.
    client.cookies.jar.set_policy(DefaultCookiePolicy(allowed_domains=[]))
    return client


class SafeFetcher:
    def __init__(
        self,
        client: httpx.AsyncClient,
        resolver: Resolver | None = None,
        *,
        via_proxy: bool = False,
        verbose_log: bool = False,
        max_concurrency: int = 16,
    ):
        self.client = client
        self.resolver = resolver or system_resolver
        self.via_proxy = via_proxy
        self.verbose_log = verbose_log
        self._sem = asyncio.Semaphore(max_concurrency)

    # -- validation --------------------------------------------------------
    async def validate(self, url: str, policy: FetchPolicy) -> tuple[Target, list[IPAddress]]:
        # An administrator-configured endpoint may use any port.
        target = parse_url(url, allowed_ports=None if policy.trusted_endpoint else policy.allowed_ports,
                           allowlist=None if policy.trusted_endpoint else policy.allowlist)
        if policy.trusted_endpoint:
            addresses = [target.ip] if target.ip else await self.resolver(target.host, target.port)
            if not addresses:
                raise WebError("web.dns_failed", "The site's address could not be found.", host=target.host)
            return target, list(dict.fromkeys(addresses))
        check_host_name(target, policy.allowlist)
        addresses = [target.ip] if target.ip else await self.resolver(target.host, target.port)
        return target, check_addresses(target, addresses, policy.allowlist)

    def _describe(self, target: Target) -> str:
        return target.url if self.verbose_log else target.host

    # -- fetching ----------------------------------------------------------
    async def fetch(
        self,
        url: str,
        policy: FetchPolicy,
        *,
        method: str = "GET",
        headers: dict[str, str] | None = None,
        json_body: object | None = None,
        params: dict[str, str] | None = None,
    ) -> FetchResult:
        started = time.perf_counter()
        if params:
            url = str(httpx.URL(url, params=params))
        try:
            async with self._sem:
                async with asyncio.timeout(policy.total_timeout):
                    result = await self._fetch(url, policy, method, headers or {}, json_body, started)
        except TimeoutError as exc:
            log.info("web fetch timeout host=%s", _host_of(url, self.verbose_log))
            raise WebError("web.timeout", "The site took too long to respond.") from exc
        log.info("web fetch ok host=%s status=%s bytes=%d ms=%.0f", _host_of(result.final_url, self.verbose_log),
                 result.status, len(result.body), result.elapsed_ms)
        return result

    async def _fetch(self, url, policy: FetchPolicy, method, headers, json_body, started) -> FetchResult:
        current = url
        redirects = 0
        while True:
            target, addresses = await self.validate(current, policy)
            response = await self._send(target, addresses, policy, method, headers, json_body)
            try:
                if response.status_code in REDIRECT_STATUSES and response.headers.get("location"):
                    if redirects >= policy.max_redirects:
                        raise WebError("web.too_many_redirects", "The page redirected too many times.")
                    redirects += 1
                    current = urljoin(target.url, response.headers["location"].strip())
                    if response.status_code == 303 or method != "GET":
                        method, json_body = "GET", None
                    continue
                return await self._read(response, target, url, policy, redirects, started)
            finally:
                await response.aclose()

    async def _send(self, target: Target, addresses: list[IPAddress], policy: FetchPolicy, method, headers, json_body) -> httpx.Response:
        base_headers = {
            "User-Agent": policy.user_agent,
            "Accept": policy.accept,
            "Accept-Encoding": "gzip, deflate",
            **({"Accept-Language": policy.accept_language} if policy.accept_language else {}),
            **headers,
        }
        timeout = httpx.Timeout(connect=policy.connect_timeout, read=policy.read_timeout, write=policy.read_timeout, pool=policy.connect_timeout)
        last_error: Exception | None = None
        candidates = [None] if self.via_proxy else addresses[:3]
        for address in candidates:
            if address is None:
                # Through an explicit proxy the proxy resolves the name; the
                # name was still validated above (see docs/WEB.md).
                url, extensions = target.url, {}
            else:
                url = target.url_for(address)
                base_headers["Host"] = target.host_header
                extensions = {"sni_hostname": target.host} if target.scheme == "https" and target.ip is None else {}
            request = self.client.build_request(
                method, url, headers=base_headers, json=json_body, timeout=timeout, extensions=extensions
            )
            try:
                response = await self.client.send(request, stream=True)
                self.client.cookies.clear()  # belt and braces: nothing persists between requests
                return response
            except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
                last_error = exc
                continue
            except httpx.TimeoutException as exc:
                raise WebError("web.timeout", "The site took too long to respond.") from exc
            except httpx.HTTPError as exc:
                raise _transport_error(exc, target) from exc
        log.info("web connect failed host=%s", self._describe(target))
        raise _transport_error(last_error, target)

    async def _read(self, response: httpx.Response, target: Target, url: str, policy: FetchPolicy, redirects: int, started: float) -> FetchResult:
        status = response.status_code
        if status >= 400:
            log.info("web fetch http host=%s status=%d", self._describe(target), status)
            raise status_error(status)
        ctype, charset = parse_content_type(response.headers.get("content-type"))
        if ctype and ctype not in ("application/octet-stream", "binary/octet-stream") and not type_allowed(ctype, policy.accept_types):
            raise WebError("web.unsupported_type", "This kind of content cannot be read.", type=ctype)
        length = response.headers.get("content-length")
        if length and length.isdigit() and int(length) > policy.max_bytes:
            raise WebError("web.too_large", "The page is too large to read.", limit_kb=policy.max_bytes // 1024)
        decoder = _Decoder(response.headers.get("content-encoding", ""))
        body = bytearray()
        raw_total = 0
        try:
            async for chunk in response.aiter_raw():
                raw_total += len(chunk)
                if raw_total > policy.max_bytes:
                    raise WebError("web.too_large", "The page is too large to read.", limit_kb=policy.max_bytes // 1024)
                body += decoder.feed(chunk, policy.max_bytes - len(body))
                if len(body) > policy.max_bytes:
                    raise WebError("web.too_large", "The page is too large to read.", limit_kb=policy.max_bytes // 1024)
        except httpx.TimeoutException as exc:
            raise WebError("web.timeout", "The site took too long to respond.") from exc
        except httpx.HTTPError as exc:
            raise _transport_error(exc, target) from exc
        data = bytes(body)
        if not ctype or ctype in ("application/octet-stream", "binary/octet-stream"):
            sniffed = sniff_type(data)
            if ctype and sniffed != "application/pdf":
                raise WebError("web.unsupported_type", "This kind of content cannot be read.", type=ctype)
            ctype = sniffed
            if not type_allowed(ctype, policy.accept_types):
                raise WebError("web.unsupported_type", "This kind of content cannot be read.", type=ctype)
        return FetchResult(
            url=url,
            final_url=target.url,
            status=status,
            content_type=ctype,
            charset=charset,
            body=data,
            elapsed_ms=round((time.perf_counter() - started) * 1000, 1),
            redirects=redirects,
            headers={k: v for k, v in response.headers.items() if k.lower() in ("content-language", "last-modified", "date")},
        )


def status_error(status: int) -> WebError:
    if status in (401, 403, 451):
        return WebError("web.access_denied", "The site refused access.", http_status=status)
    if status in (404, 410):
        return WebError("web.not_found", "The page was not found.", http_status=status)
    if status == 429:
        return WebError("web.site_rate_limited", "The site is limiting requests; try again later.", http_status=status)
    if status >= 500:
        return WebError("web.site_error", "The site reported an error.", http_status=status)
    return WebError("web.http_error", f"The site answered with HTTP {status}.", http_status=status)


def _transport_error(exc: Exception | None, target: Target) -> WebError:
    if isinstance(exc, (httpx.ConnectTimeout, httpx.ReadTimeout, httpx.WriteTimeout, httpx.PoolTimeout)):
        return WebError("web.timeout", "The site took too long to respond.")
    text = str(exc or "").lower()
    if "certificate" in text or "ssl" in text or "tls" in text:
        return WebError("web.tls_error", "The site's security certificate could not be verified.")
    return WebError("web.unreachable", "The site could not be reached.", host=target.host)


def _host_of(url: str, verbose: bool) -> str:
    if verbose:
        return url
    try:
        return httpx.URL(url).host
    except Exception:  # noqa: BLE001
        return "?"
