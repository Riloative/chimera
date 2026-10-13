"""SSRF validator: hostile URL table, address classification, allow-list."""

from __future__ import annotations

import asyncio
import ipaddress

import pytest

from chimera.web.errors import WebError
from chimera.web.fetcher import FetchPolicy
from chimera.web.netguard import Allowlist, embedded_ipv4, is_public, parse_legacy_ipv4, parse_url
from tests.conftest import FakeResolver, make_fetcher

BLOCKED_URLS = [
    # loopback in every spelling
    ("http://127.0.0.1/", "web.blocked_address"),
    ("http://127.1/", "web.blocked_address"),
    ("http://2130706433/", "web.blocked_address"),  # decimal
    ("http://0x7f000001/", "web.blocked_address"),  # hex
    ("http://0x7f.0.0.1/", "web.blocked_address"),
    ("http://0177.0.0.1/", "web.blocked_address"),  # octal
    ("http://017700000001/", "web.blocked_address"),
    ("http://[::1]/", "web.blocked_address"),
    ("http://[0:0:0:0:0:0:0:1]/", "web.blocked_address"),
    ("http://[::ffff:127.0.0.1]/", "web.blocked_address"),  # IPv4-mapped
    ("http://[::ffff:7f00:1]/", "web.blocked_address"),
    ("http://[::127.0.0.1]/", "web.blocked_address"),  # IPv4-compatible
    ("http://[64:ff9b::7f00:1]/", "web.blocked_address"),  # NAT64 of loopback
    ("http://[2002:7f00:1::1]/", "web.blocked_address"),  # 6to4 of loopback
    ("http://localhost/", "web.blocked_address"),
    ("http://localhost./", "web.blocked_address"),  # trailing dot
    ("http://LOCALHOST:80/", "web.blocked_address"),
    ("http://foo.localhost/", "web.blocked_address"),
    ("http://printer.local/", "web.blocked_address"),
    ("http://router.lan/", "web.blocked_address"),
    ("http://intranet/", "web.blocked_address"),  # single-label name
    ("http://metadata.google.internal/", "web.blocked_address"),
    # unspecified / private / link-local / CGNAT / multicast / reserved
    ("http://0.0.0.0/", "web.blocked_address"),
    ("http://0/", "web.blocked_address"),
    ("http://[::]/", "web.blocked_address"),
    ("http://10.0.0.5/", "web.blocked_address"),
    ("http://172.16.3.4/", "web.blocked_address"),
    ("http://192.168.1.1/", "web.blocked_address"),
    ("http://169.254.169.254/latest/meta-data/", "web.blocked_address"),  # cloud metadata
    ("http://[fe80::1]/", "web.blocked_address"),
    ("http://[fc00::1]/", "web.blocked_address"),  # ULA
    ("http://[fd12:3456::1]/", "web.blocked_address"),
    ("http://[fec0::1]/", "web.blocked_address"),  # site-local
    ("http://100.64.0.1/", "web.blocked_address"),  # CGNAT
    ("http://224.0.0.1/", "web.blocked_address"),
    ("http://[ff02::1]/", "web.blocked_address"),
    ("http://255.255.255.255/", "web.blocked_address"),
    ("http://240.0.0.1/", "web.blocked_address"),
    ("http://198.18.0.1/", "web.blocked_address"),  # benchmarking range
    # schemes, credentials, ports, parser tricks
    ("file:///etc/passwd", "web.unsupported_scheme"),
    ("ftp://example.org/x", "web.unsupported_scheme"),
    ("gopher://example.org:70/_x", "web.unsupported_scheme"),
    ("data:text/html,hi", "web.unsupported_scheme"),
    ("javascript:alert(1)", "web.unsupported_scheme"),
    ("http://user:pass@example.org/", "web.credentials_in_url"),
    ("http://user@example.org/", "web.credentials_in_url"),
    ("http://example.org@127.0.0.1/", "web.credentials_in_url"),
    ("http://127.0.0.1#@example.org/", "web.blocked_address"),
    ("http://example.org:22/", "web.blocked_port"),
    ("http://example.org:6379/", "web.blocked_port"),
    ("http://example.org\\@127.0.0.1/", "web.invalid_url"),  # backslash differential
    ("http://exa mple.org/", "web.invalid_url"),
    ("http://example.org\t/", "web.invalid_url"),
    ("http://ex%41mple.org/", "web.invalid_url"),  # percent-encoded host
    ("http:///nohost", "web.invalid_url"),
    ("http://[::1/", "web.invalid_url"),
    ("http://example.org:99999/", "web.invalid_url"),
    ("http://1.2.3.4.5/", "web.invalid_url"),
]


def validate(url: str, resolver=None, allowlist=None):
    from chimera.web.fetcher import SafeFetcher, make_client

    fetcher = SafeFetcher(make_client(), resolver=resolver or FakeResolver())
    policy = FetchPolicy(allowlist=Allowlist(allowlist or []))

    async def run():
        try:
            return await fetcher.validate(url, policy)
        finally:
            await fetcher.client.aclose()

    return asyncio.run(run())


@pytest.mark.parametrize(("url", "code"), BLOCKED_URLS)
def test_hostile_urls_are_rejected(url, code):
    with pytest.raises(WebError) as exc:
        validate(url)
    assert exc.value.code == code, (url, exc.value.code)


@pytest.mark.parametrize("url", ["http://news.example.org/a", "https://docs.example.org:443/x?y=1", "http://93.184.215.14/", "http://[2606:4700::1111]/"])
def test_public_urls_pass(url):
    target, addresses = validate(url)
    assert addresses and all(is_public(a) for a in addresses)
    assert target.path.startswith("/")


def test_name_resolving_to_private_address_is_blocked():
    resolver = FakeResolver()
    resolver.overrides["evil.example.org"] = ["10.1.2.3"]
    with pytest.raises(WebError) as exc:
        validate("http://evil.example.org/", resolver)
    assert exc.value.code == "web.blocked_address"


def test_any_private_answer_blocks_the_name():
    resolver = FakeResolver()
    resolver.overrides["mixed.example.org"] = ["93.184.215.14", "127.0.0.1"]
    with pytest.raises(WebError):
        validate("http://mixed.example.org/", resolver)
    resolver.overrides["mapped.example.org"] = ["::ffff:192.168.0.1"]
    with pytest.raises(WebError):
        validate("http://mapped.example.org/", resolver)


def test_unresolvable_name():
    with pytest.raises(WebError) as exc:
        validate("http://does-not-exist.example.com/")
    assert exc.value.code == "web.dns_failed"


def test_allowlist_opens_specific_hosts_and_networks_only():
    resolver = FakeResolver()
    resolver.overrides["wiki.corp.example"] = ["10.0.0.7"]
    target, addresses = validate("http://wiki.corp.example:8080/page", resolver, ["wiki.corp.example"])
    assert str(addresses[0]) == "10.0.0.7" and target.port == 8080  # allow-listed hosts may use any port
    validate("http://10.0.0.8/", resolver, ["10.0.0.0/24"])
    with pytest.raises(WebError):
        validate("http://10.0.1.8/", resolver, ["10.0.0.0/24"])
    with pytest.raises(WebError):
        validate("http://127.0.0.1/", resolver, ["10.0.0.0/24"])


def test_legacy_ipv4_forms():
    assert parse_legacy_ipv4("2130706433") == ipaddress.IPv4Address("127.0.0.1")
    assert parse_legacy_ipv4("127.1") == ipaddress.IPv4Address("127.0.0.1")
    assert parse_legacy_ipv4("0x7f.0x0.0.1") == ipaddress.IPv4Address("127.0.0.1")
    assert parse_legacy_ipv4("10.257") == ipaddress.IPv4Address("10.0.1.1")
    assert parse_legacy_ipv4("example") is None
    assert parse_legacy_ipv4("1.2.3.4.5") is None
    assert parse_legacy_ipv4("256.1.1.1") is None


def test_embedded_ipv4_detection():
    for literal in ("::ffff:10.0.0.1", "2002:0a00:0001::", "64:ff9b::a00:1", "::10.0.0.1"):
        inner = embedded_ipv4(ipaddress.ip_address(literal))
        assert inner == ipaddress.IPv4Address("10.0.0.1"), literal
        assert not is_public(ipaddress.ip_address(literal))
    assert is_public(ipaddress.ip_address("64:ff9b::5db8:d70e"))  # NAT64 of a public address


def test_parsed_target_is_rebuilt_from_components():
    target = parse_url("HTTP://News.Example.ORG./path/a?q=1#frag")
    assert target.url == "http://news.example.org/path/a?q=1"
    assert target.url_for(ipaddress.ip_address("93.184.215.14")) == "http://93.184.215.14:80/path/a?q=1"
    assert parse_url("https://xn--bcher-kva.example/").host == "xn--bcher-kva.example"
    assert parse_url("https://bücher.example/").host == "xn--bcher-kva.example"


def test_dns_rebinding_cannot_switch_the_connection(web_server):
    """The resolver answers public first and private afterwards; the request
    must go to the address that was validated (one resolution per hop)."""
    fetcher, transport, resolver = make_fetcher(web_server)
    resolver.overrides["rebind.example.org"] = lambda n: ["93.184.215.14"] if n == 1 else ["127.0.0.1"]

    async def run():
        try:
            return await fetcher.fetch("http://rebind.example.org/article", FetchPolicy())
        finally:
            await fetcher.client.aclose()

    result = asyncio.run(run())
    assert result.status == 200
    assert resolver.calls.count("rebind.example.org") == 1
    sent = transport.requests[0]
    assert sent.headers["host"] == "rebind.example.org"
