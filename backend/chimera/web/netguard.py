"""Address and URL validation for outbound web requests (SSRF defence).

Users paste URLs, search results contain URLs and pages redirect to URLs, so
every outbound request made on behalf of the web feature is validated here
before a connection is opened:

* only ``http``/``https``; no credentials in the URL; only allowed ports;
* the host must be a public DNS name or a public IP address. Hostnames are
  resolved by the caller (see ``fetcher.py``) and **every** resolved address
  must be public; the connection is then made to one of those exact addresses
  so a second DNS answer cannot redirect it (DNS rebinding);
* a small administrator allow-list may open specific internal hosts/CIDRs.

The rules are deliberately stricter than the provider-URL policy in
``netpolicy.py``: there an administrator chooses inference servers, here
anyone (including web content) can supply the URL.
"""

from __future__ import annotations

import ipaddress
import re
from dataclasses import dataclass
from urllib.parse import urlsplit

from .errors import WebError

IPAddress = ipaddress.IPv4Address | ipaddress.IPv6Address

# Names that never refer to something on the public internet.
BLOCKED_SUFFIXES = (".localhost", ".local", ".internal", ".intranet", ".lan", ".home.arpa", ".localdomain", ".corp")
BLOCKED_NAMES = {"localhost", "localhost.localdomain", "ip6-localhost", "ip6-loopback", "metadata", "metadata.google.internal"}
_HOST_RE = re.compile(r"^(?=.{1,253}$)([a-z0-9_](?:[a-z0-9_-]{0,61}[a-z0-9_])?)(\.[a-z0-9_](?:[a-z0-9_-]{0,61}[a-z0-9_])?)*$")
_NAT64 = ipaddress.ip_network("64:ff9b::/96")
_NAT64_LOCAL = ipaddress.ip_network("64:ff9b:1::/48")


@dataclass(frozen=True)
class Target:
    """A validated URL, split into the parts the fetcher needs."""

    scheme: str
    host: str  # lower-case ASCII host name or canonical IP literal (no brackets)
    port: int
    path: str  # path + query, never empty
    ip: IPAddress | None  # set when the URL host is an IP literal

    @property
    def default_port(self) -> bool:
        return self.port == (443 if self.scheme == "https" else 80)

    @property
    def host_header(self) -> str:
        host = f"[{self.host}]" if ":" in self.host else self.host
        return host if self.default_port else f"{host}:{self.port}"

    @property
    def url(self) -> str:
        return f"{self.scheme}://{self.host_header}{self.path}"

    def url_for(self, address: IPAddress) -> str:
        host = f"[{address}]" if address.version == 6 else str(address)
        return f"{self.scheme}://{host}:{self.port}{self.path}"


def parse_legacy_ipv4(host: str) -> ipaddress.IPv4Address | None:
    """Parse inet_aton-style forms browsers and libc accept: ``2130706433``,
    ``0x7f.1``, ``0177.0.0.1``, ``127.1``. Returns ``None`` if not numeric."""
    parts = host.split(".")
    if not 1 <= len(parts) <= 4 or not all(parts):
        return None
    values = []
    for part in parts:
        try:
            if part.lower().startswith("0x"):
                values.append(int(part[2:] or "0", 16))
            elif len(part) > 1 and part.startswith("0"):
                values.append(int(part, 8))
            else:
                values.append(int(part, 10))
        except ValueError:
            return None
    *head, last = values
    if any(v > 255 for v in head) or last >= 256 ** (5 - len(values)):
        return None
    number = 0
    for v in head:
        number = number * 256 + v
    number = number * 256 ** (5 - len(values)) + last
    return ipaddress.IPv4Address(number)


def embedded_ipv4(address: IPAddress) -> ipaddress.IPv4Address | None:
    """IPv4 address carried inside an IPv6 one (mapped, compatible, 6to4, NAT64, Teredo)."""
    if address.version != 6:
        return None
    if address.ipv4_mapped:
        return address.ipv4_mapped
    if address.sixtofour:
        return address.sixtofour
    if address.teredo:
        return address.teredo[1]
    if address in _NAT64 or address in _NAT64_LOCAL:
        return ipaddress.IPv4Address(int(address) & 0xFFFFFFFF)
    packed = address.packed
    if packed[:12] == bytes(12) and int(address) > 1:  # deprecated IPv4-compatible ::a.b.c.d
        return ipaddress.IPv4Address(packed[12:])
    return None


def is_public(address: IPAddress) -> bool:
    """True only for globally routable unicast addresses."""
    inner = embedded_ipv4(address)
    if inner is not None:
        return is_public(inner)
    if address.is_multicast or address.is_unspecified or address.is_reserved or address.is_loopback:
        return False
    if address.is_link_local or address.is_private:
        return False
    if address.version == 6 and address.is_site_local:
        return False
    return address.is_global


class Allowlist:
    """Administrator exceptions: exact host names and IP networks."""

    def __init__(self, entries: list[str] | None = None):
        self.hosts: set[str] = set()
        self.networks: list[ipaddress.IPv4Network | ipaddress.IPv6Network] = []
        for raw in entries or []:
            entry = raw.strip().lower().rstrip(".")
            if not entry:
                continue
            try:
                self.networks.append(ipaddress.ip_network(entry.strip("[]"), strict=False))
            except ValueError:
                self.hosts.add(entry)

    def __bool__(self) -> bool:
        return bool(self.hosts or self.networks)

    def host_allowed(self, host: str) -> bool:
        return host in self.hosts

    def address_allowed(self, address: IPAddress) -> bool:
        candidates = [address]
        inner = embedded_ipv4(address)
        if inner is not None:
            candidates.append(inner)
        return any(a in n for a in candidates for n in self.networks if a.version == n.version)


def parse_url(url: str, *, allowed_ports: list[int] | tuple[int, ...] | None = (80, 443), allowlist: Allowlist | None = None) -> Target:
    """Validate the syntax of ``url`` and return a :class:`Target`.

    The returned target is rebuilt from parsed components, so the URL the
    fetcher requests is exactly what was validated (no parser differentials).
    """
    if not isinstance(url, str) or not url or len(url) > 4096:
        raise WebError("web.invalid_url", "The address is not a valid web URL.")
    if any(ord(ch) < 0x21 or ord(ch) == 0x7F for ch in url) or "\\" in url:
        raise WebError("web.invalid_url", "The address is not a valid web URL.")
    try:
        parts = urlsplit(url)
        port = parts.port
    except ValueError as exc:
        raise WebError("web.invalid_url", "The address is not a valid web URL.") from exc
    scheme = parts.scheme.lower()
    if scheme not in ("http", "https"):
        raise WebError("web.unsupported_scheme", "Only http and https addresses can be read.", scheme=scheme or None)
    if "@" in parts.netloc or parts.username is not None or parts.password is not None:
        raise WebError("web.credentials_in_url", "Addresses with a user name or password are not allowed.")
    raw_host = parts.hostname or ""
    if not raw_host:
        raise WebError("web.invalid_url", "The address is not a valid web URL.")
    host = raw_host.rstrip(".").lower()
    ip: IPAddress | None = None
    try:
        ip = ipaddress.ip_address(host.split("%")[0])
        if "%" in host:  # zone IDs only make sense for link-local addresses
            raise WebError("web.blocked_address", "This address points to a private or local network.")
    except ValueError:
        ip = parse_legacy_ipv4(host)
    if ip is not None:
        host = str(ip)
    else:
        try:
            host = host.encode("idna").decode("ascii")
        except UnicodeError as exc:
            raise WebError("web.invalid_url", "The address is not a valid web URL.") from exc
        if not _HOST_RE.match(host) or host.replace(".", "").isdigit():
            raise WebError("web.invalid_url", "The address is not a valid web URL.")
    port = port or (443 if scheme == "https" else 80)
    allowlisted = bool(allowlist) and (
        allowlist.host_allowed(host) or (ip is not None and allowlist.address_allowed(ip))  # type: ignore[union-attr]
    )
    if allowed_ports is not None and port not in allowed_ports and not allowlisted:
        raise WebError("web.blocked_port", "Connections to this port are not allowed.", port=port)
    path = parts.path or "/"
    if parts.query:
        path += "?" + parts.query
    return Target(scheme=scheme, host=host, port=port, path=path, ip=ip)


def check_host_name(target: Target, allowlist: Allowlist | None) -> None:
    """Reject names that can only mean something local (before resolving)."""
    if target.ip is not None or (allowlist and allowlist.host_allowed(target.host)):
        return
    host = target.host
    if host in BLOCKED_NAMES or host.endswith(BLOCKED_SUFFIXES) or "." not in host:
        raise WebError("web.blocked_address", "This address points to a private or local network.", host=host)


def check_addresses(target: Target, addresses: list[IPAddress], allowlist: Allowlist | None) -> list[IPAddress]:
    """All resolved addresses must be acceptable; returns them (deduplicated)."""
    if not addresses:
        raise WebError("web.dns_failed", "The site's address could not be found.", host=target.host)
    host_allowed = bool(allowlist) and allowlist.host_allowed(target.host)  # type: ignore[union-attr]
    unique: list[IPAddress] = []
    for address in addresses:
        if address in unique:
            continue
        if not (host_allowed or is_public(address) or (allowlist and allowlist.address_allowed(address))):
            raise WebError("web.blocked_address", "This address points to a private or local network.", host=target.host)
        unique.append(address)
    return unique
