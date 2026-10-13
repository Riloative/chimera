"""Server-side request forgery (SSRF) guard for user-supplied provider URLs.

A provider URL makes the *server* issue HTTP requests, so a user could point it
at internal services. Admins choose what non-admin users may configure:

* ``any``      – no restriction
* ``private``  – loopback, private (RFC 1918 / ULA), link-local and CGNAT
                 addresses only (default: local inference servers)
* ``custom``   – only hosts/CIDRs in an explicit allow-list

Admins themselves are never restricted. Hostnames are resolved at validation
time and every resolved address must pass; see docs/SECURITY.md for the
DNS-rebinding caveat.
"""

from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlsplit

URL_POLICIES = ("any", "private", "custom")


def _addresses(host: str) -> list[ipaddress._BaseAddress]:
    try:
        return [ipaddress.ip_address(host.strip("[]"))]
    except ValueError:
        pass
    try:
        infos = socket.getaddrinfo(host, None)
    except OSError:
        return []
    out = []
    for info in infos:
        try:
            out.append(ipaddress.ip_address(info[4][0].split("%")[0]))
        except ValueError:
            continue
    return out


def _is_private(addr: ipaddress._BaseAddress) -> bool:
    if isinstance(addr, ipaddress.IPv6Address) and addr.ipv4_mapped:
        addr = addr.ipv4_mapped
    if addr.is_loopback or addr.is_private or addr.is_link_local:
        return True
    return isinstance(addr, ipaddress.IPv4Address) and addr in ipaddress.ip_network("100.64.0.0/10")


def url_allowed(url: str, policy: str, allowlist: list[str]) -> bool:
    if policy == "any":
        return True
    host = urlsplit(url).hostname
    if not host:
        return False
    if policy == "custom":
        host_l = host.lower()
        networks = []
        for entry in allowlist:
            entry = entry.strip().lower()
            if not entry:
                continue
            if entry == host_l:
                return True
            try:
                networks.append(ipaddress.ip_network(entry, strict=False))
            except ValueError:
                continue
        addrs = _addresses(host)
        return bool(addrs) and all(any(a in n for n in networks) for a in addrs)
    addrs = _addresses(host)
    return bool(addrs) and all(_is_private(a) for a in addrs)
