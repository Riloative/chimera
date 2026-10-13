"""Search engine abstraction (parallel to the model-provider layer).

Every engine returns normalized :class:`SearchResult` objects and raises
:class:`WebError` with stable ``web.*`` codes, so nothing above this module
depends on an engine's response format. All HTTP goes through the shared
:class:`~chimera.web.fetcher.SafeFetcher`.

Engines:

* ``searxng`` – self-hosted metasearch (recommended; JSON API).
* ``brave``   – Brave Search API (API key).
* ``tavily``  – Tavily Search API (API key; designed for LLM retrieval).

DuckDuckGo is intentionally not offered: it has no official search API, and
the keyless libraries scrape its HTML pages, which breaks frequently and is
against its terms of use.
"""

from __future__ import annotations

import abc
import json
import time
from dataclasses import asdict, dataclass

from .errors import WebError
from .fetcher import FetchPolicy, SafeFetcher

ENGINE_KINDS = ("searxng", "brave", "tavily")
BRAVE_URL = "https://api.search.brave.com/res/v1/web/search"
TAVILY_URL = "https://api.tavily.com/search"


@dataclass
class SearchResult:
    title: str
    url: str
    snippet: str
    published: str | None
    engine: str
    rank: int

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class EngineConfig:
    kind: str
    base_url: str = ""
    api_key: str | None = None  # decrypted; never leaves the server
    auth_header: str | None = None  # SearXNG behind an authenticating proxy


def _text(value: object, limit: int) -> str:
    if not isinstance(value, str):
        return ""
    return " ".join(value.split())[:limit]


class SearchEngine(abc.ABC):
    kind = ""
    label = ""
    needs_key = False

    def __init__(self, config: EngineConfig, fetcher: SafeFetcher, policy: FetchPolicy):
        self.config = config
        self.fetcher = fetcher
        # Engine endpoints answer JSON, never redirect, and stay small.
        self.policy = FetchPolicy(
            **{**policy.__dict__, "accept_types": ("application/json",), "max_redirects": 0,
               "max_bytes": 2 * 1024 * 1024, "accept": "application/json"}
        )

    @abc.abstractmethod
    async def search(self, query: str, count: int, language: str | None = None) -> list[SearchResult]: ...

    async def _get_json(self, url: str, *, params: dict | None = None, headers: dict | None = None,
                        json_body: object | None = None, method: str = "GET") -> object:
        try:
            result = await self.fetcher.fetch(url, self.policy, method=method, params=params, headers=headers, json_body=json_body)
        except WebError as exc:
            raise self._map_error(exc) from exc
        try:
            return json.loads(result.body.decode("utf-8", "replace"))
        except ValueError as exc:
            raise WebError("web.engine_bad_response", "The search engine returned an unexpected response.", engine=self.label) from exc

    def _map_error(self, exc: WebError) -> WebError:
        code = exc.code
        status = exc.params.get("http_status")
        if code == "web.access_denied":
            return WebError("web.engine_auth", "The search engine rejected the credentials.", engine=self.label, http_status=status)
        if code in ("web.site_rate_limited",):
            return WebError("web.engine_rate_limited", "The search engine is limiting requests; try again later.", engine=self.label)
        if code in ("web.unreachable", "web.dns_failed", "web.timeout", "web.tls_error"):
            return WebError("web.engine_unreachable", "The search engine could not be reached.", engine=self.label, reason=code)
        if code in ("web.unsupported_type", "web.decode_failed", "web.too_large"):
            return WebError("web.engine_bad_response", "The search engine returned an unexpected response.", engine=self.label)
        if code in ("web.blocked_address", "web.blocked_port", "web.invalid_url", "web.unsupported_scheme", "web.credentials_in_url"):
            return WebError("web.engine_config", "The search engine address is not valid.", engine=self.label, reason=code)
        return WebError("web.engine_error", "The search engine reported an error.", engine=self.label, http_status=status)


class SearxngEngine(SearchEngine):
    kind = "searxng"
    label = "SearXNG"

    def _map_error(self, exc: WebError) -> WebError:
        # SearXNG answers 403 to format=json unless "json" is enabled in
        # settings.yml (search.formats) — the most common setup problem.
        if exc.code == "web.access_denied" and exc.params.get("http_status") == 403 and not self.config.api_key:
            return WebError("web.searxng_json_disabled", "SearXNG refused JSON output; enable the json format in its settings.yml.")
        if exc.code == "web.not_found":
            return WebError("web.engine_config", "No SearXNG search endpoint at this address.", engine=self.label, reason=exc.code)
        return super()._map_error(exc)

    async def search(self, query: str, count: int, language: str | None = None) -> list[SearchResult]:
        base = self.config.base_url.rstrip("/")
        if not base:
            raise WebError("web.engine_config", "The SearXNG address is not configured.", engine=self.label)
        headers = {}
        if self.config.auth_header and self.config.api_key:
            headers[self.config.auth_header] = self.config.api_key
        params = {"q": query, "format": "json", "pageno": "1", "safesearch": "1"}
        if language:
            params["language"] = language
        data = await self._get_json(f"{base}/search", params=params, headers=headers)
        if not isinstance(data, dict) or not isinstance(data.get("results"), list):
            raise WebError("web.engine_bad_response", "The search engine returned an unexpected response.", engine=self.label)
        out = []
        for item in data["results"]:
            if not isinstance(item, dict):
                continue
            url = _text(item.get("url"), 2000)
            if not url:
                continue
            out.append(SearchResult(
                title=_text(item.get("title"), 300) or url, url=url, snippet=_text(item.get("content"), 600),
                published=_text(item.get("publishedDate"), 40) or None,
                engine=_text(item.get("engine"), 40) or self.kind, rank=len(out) + 1,
            ))
            if len(out) >= count:
                break
        return out


class BraveEngine(SearchEngine):
    kind = "brave"
    label = "Brave Search"
    needs_key = True

    async def search(self, query: str, count: int, language: str | None = None) -> list[SearchResult]:
        if not self.config.api_key:
            raise WebError("web.engine_config", "The Brave Search API key is not set.", engine=self.label)
        params = {"q": query, "count": str(min(20, max(1, count)))}
        if language:
            params["search_lang"] = language
        data = await self._get_json(self.config.base_url or BRAVE_URL, params=params,
                                    headers={"X-Subscription-Token": self.config.api_key})
        items = (data.get("web") or {}).get("results") if isinstance(data, dict) else None
        if not isinstance(items, list):
            if isinstance(data, dict) and "web" not in data:
                return []
            raise WebError("web.engine_bad_response", "The search engine returned an unexpected response.", engine=self.label)
        out = []
        for item in items:
            if not isinstance(item, dict) or not _text(item.get("url"), 2000):
                continue
            out.append(SearchResult(
                title=_text(item.get("title"), 300), url=_text(item.get("url"), 2000),
                snippet=_text(item.get("description"), 600), published=_text(item.get("page_age") or item.get("age"), 40) or None,
                engine=self.kind, rank=len(out) + 1,
            ))
            if len(out) >= count:
                break
        return out


class TavilyEngine(SearchEngine):
    kind = "tavily"
    label = "Tavily"
    needs_key = True

    async def search(self, query: str, count: int, language: str | None = None) -> list[SearchResult]:
        if not self.config.api_key:
            raise WebError("web.engine_config", "The Tavily API key is not set.", engine=self.label)
        body = {"query": query, "max_results": min(20, max(1, count)), "search_depth": "basic"}
        data = await self._get_json(self.config.base_url or TAVILY_URL, method="POST", json_body=body,
                                    headers={"Authorization": f"Bearer {self.config.api_key}"})
        items = data.get("results") if isinstance(data, dict) else None
        if not isinstance(items, list):
            raise WebError("web.engine_bad_response", "The search engine returned an unexpected response.", engine=self.label)
        out = []
        for item in items:
            if not isinstance(item, dict) or not _text(item.get("url"), 2000):
                continue
            out.append(SearchResult(
                title=_text(item.get("title"), 300), url=_text(item.get("url"), 2000),
                snippet=_text(item.get("content"), 600), published=_text(item.get("published_date"), 40) or None,
                engine=self.kind, rank=len(out) + 1,
            ))
            if len(out) >= count:
                break
        return out


ENGINES: dict[str, type[SearchEngine]] = {"searxng": SearxngEngine, "brave": BraveEngine, "tavily": TavilyEngine}


def build_engine(config: EngineConfig, fetcher: SafeFetcher, policy: FetchPolicy) -> SearchEngine:
    try:
        cls = ENGINES[config.kind]
    except KeyError as exc:
        raise WebError("web.engine_config", "Unknown search engine.") from exc
    return cls(config, fetcher, policy)


async def probe_engine(engine: SearchEngine, query: str) -> dict:
    """Run a real query and report status, latency and result count."""
    started = time.perf_counter()
    try:
        results = await engine.search(query, 5)
    except WebError as exc:
        return {"ok": False, "latency_ms": round((time.perf_counter() - started) * 1000, 1), "result_count": 0,
                "error_code": exc.code, "error_params": exc.params}
    return {
        "ok": True,
        "latency_ms": round((time.perf_counter() - started) * 1000, 1),
        "result_count": len(results),
        "error_code": None if results else "web.no_results",
        "error_params": {},
        "sample": [{"title": r.title, "url": r.url} for r in results[:3]],
    }
