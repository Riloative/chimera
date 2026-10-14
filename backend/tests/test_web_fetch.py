"""Hardened fetcher and extraction against the local mock web."""

from __future__ import annotations

import asyncio

import pytest

from chimera.web.errors import WebError
from chimera.web.extract import extract_page
from chimera.web.fetcher import FetchPolicy, FetchResult
from tests.conftest import make_fetcher


def fetch(web_server, url, policy=None, extract=False, kind=None, fetcher_tuple=None):
    fetcher, transport, resolver = fetcher_tuple or make_fetcher(web_server)

    async def run():
        try:
            result = await fetcher.fetch(url, policy or FetchPolicy())
            return extract_page(result, 300_000, kind) if extract else result
        finally:
            await fetcher.client.aclose()

    return asyncio.run(run())


def fails(web_server, url, code, policy=None, extract=False):
    with pytest.raises(WebError) as exc:
        fetch(web_server, url, policy, extract)
    assert exc.value.code == code, exc.value.code
    return exc.value


# ------------------------------------------------------------ extraction
def test_article_main_content_and_metadata(web_server):
    page = fetch(web_server, "http://news.example.org/article", extract=True)
    assert "14 March 2026" in page.markdown and "Programme" in page.markdown
    assert "- Opening night: 14 March" in page.markdown
    for boilerplate in ("Sign in", "winter jackets", "trackPageView", "All rights reserved", "font-family"):
        assert boilerplate not in page.markdown
    assert page.title == "Aurora Borealis Festival comes to Vilnius"
    assert page.description == "The city announces a winter light festival."
    assert page.published == "2026-03-02"  # from <meta>, not the footer's copyright year
    assert page.site_name == "Example News"
    assert page.canonical == "https://news.example.org/aurora-festival"


def test_docs_page_keeps_code_blocks(web_server):
    page = fetch(web_server, "http://docs.example.org/docs", extract=True)
    assert "```" in page.markdown
    assert 'pip install "widget[cli]==2.4.1"' in page.markdown
    assert "max_size_mb = 512" in page.markdown
    assert "# Installing Widget" in page.markdown


def test_table_page_becomes_markdown_table(web_server):
    page = fetch(web_server, "http://example.org/table", extract=True)
    assert "| Planet | Moons |" in page.markdown.replace("  ", " ")
    assert "| Saturn | 146 |" in page.markdown.replace("  ", " ")


def test_forum_thread_keeps_all_posts(web_server):
    page = fetch(web_server, "http://example.org/forum", extract=True)
    assert "out of memory" in page.markdown
    assert "--ctx-size 8192" in page.markdown
    assert "fixed it" in page.markdown


@pytest.mark.parametrize("path", ["/lt", "/lt-meta", "/lt-8859-13"])
def test_lithuanian_legacy_charsets(web_server, path):
    page = fetch(web_server, f"http://example.org{path}", extract=True)
    assert "Šiandien Kauno senamiestyje prasideda žiemos šventė" in page.markdown
    assert "įėjimas" in page.markdown and "uždarytos" in page.markdown
    assert page.title == "Žiemos šventė Kaune"


def test_javascript_shell_is_reported(web_server):
    err = fails(web_server, "http://example.org/js-shell", "web.needs_javascript", extract=True)
    assert err.params.get("title") == "Dashboard App"


def test_plain_markdown_json_csv(web_server):
    assert "mock web server" in fetch(web_server, "http://example.org/plain.txt", extract=True).markdown
    assert "**Markdown**" in fetch(web_server, "http://example.org/readme.md", extract=True).markdown
    js = fetch(web_server, "http://example.org/data.json", extract=True)
    assert js.kind == "json" and '"version": "2.4.1"' in js.markdown
    table = fetch(web_server, "http://example.org/data.csv", extract=True)
    assert "| Vilnius | 600000 |" in table.markdown


def test_rss_and_atom_feeds_without_xxe(web_server):
    feed = fetch(web_server, "http://example.org/feed.xml", extract=True)
    assert feed.kind == "feed" and feed.title == "Example Releases"
    assert "Widget 2.4.1 released" in feed.markdown and "https://example.org/releases/2.4.1" in feed.markdown
    assert "root:" not in feed.markdown  # external entity was not resolved
    atom = fetch(web_server, "http://example.org/atom.xml", extract=True)
    assert "First post" in atom.markdown and "https://example.org/first" in atom.markdown


def test_pdf_link(web_server):
    page = fetch(web_server, "http://example.org/doc.pdf", extract=True)
    assert page.kind == "pdf" and "revenue grew 12 percent" in page.markdown


def test_youtube_page_gives_title_and_description_only(web_server):
    page = fetch(web_server, "https://www.youtube.com/watch?v=abc", extract=True, kind="video")
    assert page.kind == "video" and "transcript_unavailable" in page.notes
    assert "How llamas sleep" in page.markdown and "documentary" in page.markdown


def test_gzip_and_cookies_not_kept(web_server):
    fetcher_tuple = make_fetcher(web_server)
    page = fetch(web_server, "http://news.example.org/gzipped", extract=True, fetcher_tuple=make_fetcher(web_server))
    assert "14 March 2026" in page.markdown
    fetcher, transport, _ = fetcher_tuple

    async def twice():
        try:
            await fetcher.fetch("http://news.example.org/article", FetchPolicy())  # sets a cookie
            await fetcher.fetch("http://news.example.org/article", FetchPolicy())
        finally:
            await fetcher.client.aclose()

    asyncio.run(twice())
    assert all("cookie" not in r.headers for r in transport.requests)
    assert not list(fetcher.client.cookies.jar)


def test_honest_user_agent_and_no_session_material(web_server):
    policy = FetchPolicy(user_agent="Chimera-Test/1.0")
    fetch(web_server, "http://news.example.org/article", policy)
    headers = web_server.app.state.last_headers
    assert headers["user-agent"] == "Chimera-Test/1.0"
    assert "cookie" not in headers and "authorization" not in headers and "x-csrf-token" not in headers


# -------------------------------------------------------------- failures
@pytest.mark.parametrize(("path", "code"), [
    ("/status/404", "web.not_found"),
    ("/status/410", "web.not_found"),
    ("/status/403", "web.access_denied"),
    ("/status/401", "web.access_denied"),
    ("/status/429", "web.site_rate_limited"),
    ("/status/503", "web.site_error"),
    ("/status/418", "web.http_error"),
    ("/image.png", "web.unsupported_type"),
    ("/binary", "web.unsupported_type"),
])
def test_http_failures_map_to_codes(web_server, path, code):
    fails(web_server, f"http://example.org{path}", code)


def test_size_limits(web_server):
    fails(web_server, "http://example.org/huge", "web.too_large", FetchPolicy(max_bytes=1024 * 1024))
    fails(web_server, "http://example.org/huge-declared", "web.too_large", FetchPolicy(max_bytes=1024 * 1024))


def test_decompression_bomb_is_stopped(web_server):
    # ~40 KB on the wire, 40 MiB decompressed: must stop at the 1 MiB cap.
    fails(web_server, "http://example.org/bomb", "web.too_large", FetchPolicy(max_bytes=1024 * 1024))


def test_timeouts(web_server):
    fails(web_server, "http://example.org/slow", "web.timeout", FetchPolicy(read_timeout=0.5, total_timeout=1))
    fails(web_server, "http://example.org/slow-body", "web.timeout", FetchPolicy(read_timeout=5, total_timeout=1))


def test_redirects_are_followed_and_capped(web_server):
    result = fetch(web_server, "http://example.org/redirect/3")
    assert result.redirects == 3 and result.final_url.endswith("/article")
    fails(web_server, "http://example.org/redirect/7", "web.too_many_redirects")


@pytest.mark.parametrize("target", [
    "http://127.0.0.1/admin", "http://169.254.169.254/latest/meta-data/", "http://localhost:8700/api/health",
    "http://[::1]/", "file:///etc/passwd", "http://10.0.0.1/",
])
def test_redirect_to_internal_targets_is_blocked(web_server, target):
    from urllib.parse import quote

    with pytest.raises(WebError) as exc:
        fetch(web_server, f"http://example.org/redirect-to?url={quote(target, safe='')}")
    expected = {"web.unsupported_scheme"} if target.startswith("file:") else {"web.blocked_address", "web.blocked_port"}
    assert exc.value.code in expected


def test_unreachable_host(web_server):
    fails(web_server, "http://nothing-here.example.com/", "web.dns_failed")


def test_concurrency_is_bounded(web_server):
    fetcher, transport, _ = make_fetcher(web_server)
    fetcher._sem = asyncio.Semaphore(2)
    active, peak = 0, 0
    original = fetcher._fetch

    async def tracked(*args, **kwargs):
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        try:
            await asyncio.sleep(0.05)
            return await original(*args, **kwargs)
        finally:
            active -= 1

    fetcher._fetch = tracked  # type: ignore[method-assign]

    async def run():
        try:
            await asyncio.gather(*(fetcher.fetch("http://news.example.org/article", FetchPolicy()) for _ in range(6)))
        finally:
            await fetcher.client.aclose()

    asyncio.run(run())
    assert peak == 2


def test_binary_body_without_type_is_rejected():
    result = FetchResult(url="u", final_url="u", status=200, content_type="text/plain", charset=None, body=b"\x00\x01\x02" * 100, elapsed_ms=1)
    with pytest.raises(WebError) as exc:
        extract_page(result, 1000)
    assert exc.value.code == "web.binary_content"
