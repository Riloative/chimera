"""Search engine adapters against the mock engines (no real internet)."""

from __future__ import annotations

import asyncio

import pytest

from chimera.web.errors import WebError
from chimera.web.fetcher import FetchPolicy
from chimera.web.search import EngineConfig, SearchResult, build_engine, probe_engine
from chimera.web.service import WebService
from chimera.web.settings import WebAdminSettings
from tests.conftest import make_fetcher


def run_engine(web_server, config: EngineConfig, query="aurora festival", count=5, trusted=None, test=False):
    fetcher, transport, resolver = make_fetcher(web_server)
    policy = FetchPolicy(trusted_endpoint=config.kind == "searxng" if trusted is None else trusted)
    engine = build_engine(config, fetcher, policy)

    async def go():
        try:
            return await (probe_engine(engine, query) if test else engine.search(query, count))
        finally:
            await fetcher.client.aclose()

    return asyncio.run(go()), transport


def searx(web_server, **kw) -> EngineConfig:
    return EngineConfig(kind="searxng", base_url=web_server.url, **kw)


def test_searxng_normalizes_results(web_server):
    results, _ = run_engine(web_server, searx(web_server))
    assert [r.rank for r in results] == [1, 2, 3, 4, 5]
    first = results[0]
    assert first.title == "Aurora Borealis Festival comes to Vilnius"
    assert first.url == "http://news.example.org/article?utm_source=feed&id=1"
    assert first.snippet.startswith("The festival") and first.published.startswith("2026-03-02") and first.engine == "mock"
    assert web_server.app.state.searches[-1] == "aurora festival"


def test_searxng_on_a_private_address_is_allowed_only_as_admin_endpoint(web_server):
    # The mock runs on 127.0.0.1: fine as the administrator's SearXNG...
    run_engine(web_server, searx(web_server))
    # ...but the same address is blocked when treated as an untrusted URL.
    with pytest.raises(WebError) as exc:
        run_engine(web_server, searx(web_server), trusted=False)
    assert exc.value.code == "web.engine_config"


def test_searxng_json_disabled_diagnostic(web_server):
    web_server.app.state.json_disabled = True
    try:
        with pytest.raises(WebError) as exc:
            run_engine(web_server, searx(web_server))
        assert exc.value.code == "web.searxng_json_disabled"
    finally:
        web_server.app.state.json_disabled = False


def test_searxng_errors(web_server):
    with pytest.raises(WebError) as exc:
        run_engine(web_server, searx(web_server), query="explode")
    assert exc.value.code == "web.engine_error"
    assert run_engine(web_server, searx(web_server), query="noresults")[0] == []
    with pytest.raises(WebError) as exc:
        run_engine(web_server, EngineConfig(kind="searxng", base_url="http://127.0.0.1:1"))
    assert exc.value.code == "web.engine_unreachable"
    with pytest.raises(WebError) as exc:
        run_engine(web_server, EngineConfig(kind="searxng", base_url=web_server.url + "/nothing"))
    assert exc.value.code == "web.engine_config"


def test_brave_and_tavily(web_server):
    results, transport = run_engine(web_server, EngineConfig(kind="brave", api_key="brave-key"))
    assert results[0].title.startswith("Aurora") and results[0].engine == "brave"
    sent = transport.requests[-1]
    assert sent.headers["host"] == "api.search.brave.com" and sent.headers["x-subscription-token"] == "brave-key"
    with pytest.raises(WebError) as exc:
        run_engine(web_server, EngineConfig(kind="brave", api_key="wrong"))
    assert exc.value.code == "web.engine_auth"
    with pytest.raises(WebError) as exc:
        run_engine(web_server, EngineConfig(kind="brave", api_key="brave-key"), query="ratelimit")
    assert exc.value.code == "web.engine_rate_limited"
    with pytest.raises(WebError) as exc:
        run_engine(web_server, EngineConfig(kind="brave"))
    assert exc.value.code == "web.engine_config"
    tav, transport = run_engine(web_server, EngineConfig(kind="tavily", api_key="tavily-key"), count=2)
    assert len(tav) == 2 and tav[1].title == "Installing Widget"
    assert transport.requests[-1].method == "POST"


def test_engine_test_reports_latency_and_count(web_server):
    report, _ = run_engine(web_server, searx(web_server), test=True)
    assert report["ok"] and report["result_count"] == 5 and report["latency_ms"] >= 0 and report["error_code"] is None
    report, _ = run_engine(web_server, EngineConfig(kind="brave", api_key="bad"), test=True)
    assert not report["ok"] and report["error_code"] == "web.engine_auth"
    report, _ = run_engine(web_server, searx(web_server), query="noresults", test=True)
    assert report["ok"] and report["result_count"] == 0 and report["error_code"] == "web.no_results"


def test_merge_dedupes_strips_tracking_and_filters_domains():
    settings = WebAdminSettings(domain_denylist=["blocked.example.net"])
    batch1 = [
        SearchResult("A", "http://news.example.org/article?utm_source=feed&id=1", "", None, "x", 1),
        SearchResult("B", "http://blocked.example.net/x", "", None, "x", 2),
        SearchResult("C", "http://docs.example.org/docs", "", None, "x", 3),
    ]
    batch2 = [
        SearchResult("A2", "https://www.news.example.org/article/?id=1&fbclid=XYZ", "", None, "y", 1),
        SearchResult("D", "ftp://files.example.org/x", "", None, "y", 2),
        SearchResult("E", "https://example.org/e", "", None, "y", 3),
    ]
    merged = WebService.merge_results([batch1, batch2], settings, 10)
    assert [m.title for m in merged] == ["A", "C", "E"]
    assert merged[0].url == "http://news.example.org/article?id=1"
    assert [m.rank for m in merged] == [1, 2, 3]
    assert len(WebService.merge_results([batch1, batch2], settings, 2)) == 2
    only = WebService.merge_results([batch1], WebAdminSettings(domain_allowlist=["docs.example.org"]), 10)
    assert [m.title for m in only] == ["C"]
