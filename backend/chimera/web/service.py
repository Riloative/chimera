"""Web feature service: settings, quotas, caching, reading pages, searching,
and persistence of sources. Everything here is scoped to a user."""

from __future__ import annotations

import asyncio
import logging
import time
import urllib.robotparser
from dataclasses import asdict, dataclass, field
from typing import Any
from urllib.parse import urlsplit

from ..config import Config
from ..crypto import SecretBox
from ..db import Database, dumps, loads, new_id, now_iso
from .errors import WebError
from .extract import extract_page
from .fetcher import FetchPolicy, SafeFetcher
from .netguard import Allowlist
from .prompting import DEFAULT_GROUNDING_PROMPT, DEFAULT_QUERY_PROMPT
from .search import ENGINE_KINDS, EngineConfig, SearchEngine, SearchResult, build_engine, probe_engine
from .settings import WebAdminSettings
from .urls import canonical_url, display_domain, domain_matches, host_of, rewrite, strip_tracking

log = logging.getLogger("chimera.web")

CACHE_TTL = 600.0
CACHE_PER_USER = 64
ROBOTS_TTL = 3600.0
PER_USER_CONCURRENCY = 4
MAX_STORED_CHARS = 300_000
ROBOTS_AGENT = "Chimera"


@dataclass
class SourceData:
    """One fetched page (or the failure to fetch it)."""

    url: str
    status: str  # ok | snippet | error
    final_url: str | None = None
    title: str | None = None
    site_name: str | None = None
    description: str | None = None
    published: str | None = None
    kind: str | None = None
    content_type: str | None = None
    content: str = ""
    truncated: bool = False
    notes: list[str] = field(default_factory=list)
    error_code: str | None = None
    error_params: dict[str, Any] = field(default_factory=dict)
    fetched_at: str | None = None
    # search metadata
    query: str | None = None
    snippet: str | None = None
    rank: int | None = None
    engine: str | None = None

    @property
    def ok(self) -> bool:
        return self.status in ("ok", "snippet")


def source_meta(row: dict) -> dict[str, Any]:
    """What the browser may see about a stored source (no page content)."""
    return {
        "id": row["id"],
        "message_id": row["message_id"],
        "origin": row["origin"],
        "position": row["position"],
        "url": row["url"],
        "final_url": row["final_url"],
        "domain": display_domain(row["final_url"] or row["url"]),
        "title": row["title"],
        "site_name": row["site_name"],
        "description": row["description"],
        "published": row["published"],
        "kind": row["kind"],
        "status": row["status"],
        "error_code": row["error_code"],
        "error_params": loads(row["error_params"], {}),
        "chars": len(row["content"] or "") if "content" in row else row.get("chars", 0),
        "truncated": bool(row["truncated"]),
        "notes": loads(row["notes"], []),
        "query": row["query"],
        "snippet": row["snippet"],
        "rank": row["rank"],
        "fetched_at": row["fetched_at"],
    }


META_COLUMNS = (
    "id, message_id, origin, position, url, final_url, title, site_name, description, published, kind, status, "
    "error_code, error_params, length(content) AS chars, truncated, notes, query, snippet, rank, fetched_at"
)


class WebService:
    def __init__(self, db: Database, config: Config, box: SecretBox, fetcher: SafeFetcher):
        self.db = db
        self.config = config
        self.box = box
        self.fetcher = fetcher
        self._cache: dict[str, dict[str, tuple[float, SourceData]]] = {}
        self._robots: dict[str, dict[str, tuple[float, urllib.robotparser.RobotFileParser | None]]] = {}
        self._user_sems: dict[str, asyncio.Semaphore] = {}

    # ------------------------------------------------------------ settings
    def _defaults(self) -> dict[str, Any]:
        cfg = self.config
        out: dict[str, Any] = {"enabled": cfg.web_enabled}
        if cfg.web_searxng_url:
            out.update(engine="searxng", searxng_url=cfg.web_searxng_url)
        if cfg.web_fetch_allowlist:
            out["fetch_allowlist"] = cfg.web_fetch_allowlist
        if cfg.web_user_agent:
            out["user_agent"] = cfg.web_user_agent
        return out

    def _app(self, key: str, default: Any = None) -> Any:
        row = self.db.one("SELECT value FROM app_settings WHERE key = ?", (key,))
        return loads(row["value"], default) if row else default

    def _set_app(self, key: str, value: Any) -> None:
        self.db.execute(
            "INSERT INTO app_settings(key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, dumps(value)),
        )

    def admin(self) -> WebAdminSettings:
        stored = self._app("web", {}) or {}
        merged = {**self._defaults(), **{k: v for k, v in stored.items() if k in WebAdminSettings.model_fields}}
        try:
            return WebAdminSettings.model_validate(merged)
        except Exception:  # noqa: BLE001 - never lock the admin out because of a bad value
            log.warning("invalid stored web settings; using defaults")
            return WebAdminSettings.model_validate(self._defaults())

    def update_admin(self, patch: dict[str, Any]) -> WebAdminSettings:
        current = self.admin().model_dump()
        current.update(patch)
        validated = WebAdminSettings.model_validate(current)
        self._set_app("web", validated.model_dump())
        return validated

    def engine_key(self, kind: str) -> str | None:
        return self.box.decrypt(self._app(f"web_key:{kind}"))

    def set_engine_key(self, kind: str, value: str | None) -> None:
        if kind not in ENGINE_KINDS:
            raise WebError("web.engine_config", "Unknown search engine.")
        if value:
            self._set_app(f"web_key:{kind}", self.box.encrypt(value.strip()))
        else:
            self.db.execute("DELETE FROM app_settings WHERE key = ?", (f"web_key:{kind}",))

    def engine_status(self, kind: str) -> dict[str, Any] | None:
        return self._app(f"web_status:{kind}")

    def engine_config(self, kind: str, settings: WebAdminSettings | None = None) -> EngineConfig:
        settings = settings or self.admin()
        if kind == "searxng":
            return EngineConfig(kind=kind, base_url=settings.searxng_url, api_key=self.engine_key(kind),
                                auth_header=settings.searxng_auth_header or None)
        return EngineConfig(kind=kind, api_key=self.engine_key(kind))

    def engine_configured(self, kind: str | None, settings: WebAdminSettings) -> bool:
        if kind == "searxng":
            return bool(settings.searxng_url)
        if kind in ("brave", "tavily"):
            return bool(self._app(f"web_key:{kind}"))
        return False

    def search_available(self, settings: WebAdminSettings | None = None) -> bool:
        settings = settings or self.admin()
        return settings.enabled and self.engine_configured(settings.engine, settings)

    def admin_view(self) -> dict[str, Any]:
        settings = self.admin()
        engines = []
        for kind in ENGINE_KINDS:
            engines.append({
                "kind": kind,
                "configured": self.engine_configured(kind, settings),
                "has_key": bool(self._app(f"web_key:{kind}")),
                "status": self.engine_status(kind),
            })
        return {
            "settings": settings.model_dump(),
            "engines": engines,
            "defaults": {"query_prompt": DEFAULT_QUERY_PROMPT, "grounding_prompt": DEFAULT_GROUNDING_PROMPT},
        }

    def status_for(self, user: dict) -> dict[str, Any]:
        settings = self.admin()
        return {
            "enabled": settings.enabled,
            "search_available": self.search_available(settings),
            "engine": settings.engine if self.search_available(settings) else None,
            "max_urls_per_message": settings.max_urls_per_message,
            "max_results": settings.max_results,
            "is_admin": user["role"] == "admin",
        }

    # -------------------------------------------------------------- policy
    def policy(self, settings: WebAdminSettings, *, trusted: bool = False) -> FetchPolicy:
        return FetchPolicy(
            allowed_ports=tuple(settings.allowed_ports),
            allowlist=Allowlist(settings.fetch_allowlist),
            trusted_endpoint=trusted,
            max_bytes=settings.max_page_kb * 1024,
            connect_timeout=min(10.0, settings.timeout_seconds),
            read_timeout=settings.timeout_seconds,
            total_timeout=settings.timeout_seconds + 5,
            user_agent=settings.user_agent,
        )

    def engine(self, settings: WebAdminSettings, kind: str | None = None, config: EngineConfig | None = None) -> SearchEngine:
        kind = kind or settings.engine
        if not kind:
            raise WebError("web.search_unavailable", "Web search is not configured.")
        # Only the administrator-configured SearXNG address may be internal.
        return build_engine(config or self.engine_config(kind, settings), self.fetcher, self.policy(settings, trusted=kind == "searxng"))

    # -------------------------------------------------------------- quotas
    def consume(self, user_id: str, kind: str, limit: int) -> None:
        """Count one fetch/search against the user's hourly quota (SQLite, so
        it survives restarts; same table as the login throttle)."""
        key, now = f"web:{kind}:{user_id}", time.time()
        row = self.db.one("SELECT * FROM auth_throttle WHERE key = ?", (key,))
        if row and now - row["window_start"] < 3600:
            if row["failures"] >= limit:
                raise WebError("web.rate_limited", "You have reached the hourly limit for web requests.",
                               retry_after=int(3600 - (now - row["window_start"])) + 1, kind=kind)
            self.db.execute("UPDATE auth_throttle SET failures = failures + 1 WHERE key = ?", (key,))
        else:
            self.db.execute(
                "INSERT OR REPLACE INTO auth_throttle(key, failures, window_start, locked_until) VALUES (?,1,?,0)", (key, now)
            )

    def _sem(self, user_id: str) -> asyncio.Semaphore:
        sem = self._user_sems.get(user_id)
        if sem is None:
            sem = self._user_sems[user_id] = asyncio.Semaphore(PER_USER_CONCURRENCY)
        return sem

    # --------------------------------------------------------------- cache
    def _cache_get(self, user_id: str, url: str) -> SourceData | None:
        entry = self._cache.get(user_id, {}).get(url)
        if entry and entry[0] > time.monotonic():
            return SourceData(**asdict(entry[1]))
        return None

    def _cache_put(self, user_id: str, url: str, data: SourceData) -> None:
        bucket = self._cache.setdefault(user_id, {})
        bucket[url] = (time.monotonic() + CACHE_TTL, SourceData(**asdict(data)))
        if len(bucket) > CACHE_PER_USER:
            for key, _ in sorted(bucket.items(), key=lambda kv: kv[1][0])[: len(bucket) - CACHE_PER_USER]:
                bucket.pop(key, None)

    def forget_user(self, user_id: str) -> None:
        self._cache.pop(user_id, None)
        self._robots.pop(user_id, None)
        self._user_sems.pop(user_id, None)

    # ----------------------------------------------------------- domains
    @staticmethod
    def check_domain(url: str, settings: WebAdminSettings) -> None:
        host = host_of(url)
        if settings.domain_denylist and domain_matches(host, settings.domain_denylist):
            raise WebError("web.domain_blocked", "Your administrator has blocked this site.", host=display_domain(url))
        if settings.domain_allowlist and not domain_matches(host, settings.domain_allowlist):
            raise WebError("web.domain_not_allowed", "Your administrator only allows reading selected sites.", host=display_domain(url))

    # --------------------------------------------------------------- robots
    async def _robots_allowed(self, user_id: str, url: str, settings: WebAdminSettings) -> bool:
        parts = urlsplit(url)
        origin = f"{parts.scheme}://{parts.netloc}".lower()
        bucket = self._robots.setdefault(user_id, {})
        entry = bucket.get(origin)
        if not entry or entry[0] < time.monotonic():
            parser: urllib.robotparser.RobotFileParser | None = None
            policy = self.policy(settings)
            policy.max_bytes = 512 * 1024
            policy.accept_types = ("text/plain",)
            policy.total_timeout = min(policy.total_timeout, 10)
            try:
                result = await self.fetcher.fetch(origin + "/robots.txt", policy)
                parser = urllib.robotparser.RobotFileParser()
                parser.parse(result.body.decode("utf-8", "replace").splitlines())
            except WebError as exc:
                if exc.code == "web.site_error":  # RFC 9309: server error = assume disallowed
                    parser = urllib.robotparser.RobotFileParser()
                    parser.disallow_all = True
            bucket[origin] = (time.monotonic() + ROBOTS_TTL, parser)
            entry = bucket[origin]
        parser = entry[1]
        return True if parser is None else parser.can_fetch(ROBOTS_AGENT, url)

    # --------------------------------------------------------------- reading
    async def read(self, user: dict, url: str, settings: WebAdminSettings, *, robots: bool = False, use_cache: bool = True) -> SourceData:
        """Fetch and extract one URL for ``user``. Never raises for page-level
        failures: they come back as ``status="error"`` with a code."""
        uid = user["id"]
        if use_cache:
            cached = self._cache_get(uid, url)
            if cached:
                return cached
        data = SourceData(url=url, status="error", fetched_at=now_iso())
        try:
            self.check_domain(url, settings)
            if robots and not await self._robots_allowed(uid, url, settings):
                raise WebError("web.robots_disallowed", "The site asks automated tools not to read this page.")
            self.consume(uid, "fetch", settings.fetches_per_hour)
            plan = rewrite(url)
            policy = self.policy(settings)
            last_error: WebError | None = None
            async with self._sem(uid):
                for candidate in plan.candidates:
                    try:
                        if candidate != url:
                            self.check_domain(candidate, settings)
                        result = await self.fetcher.fetch(candidate, policy)
                        page = await asyncio.to_thread(extract_page, result, MAX_STORED_CHARS, plan.kind)
                    except WebError as exc:
                        last_error = exc
                        continue
                    except Exception:  # noqa: BLE001 - a broken page must not break the answer
                        log.exception("web read failed unexpectedly host=%s", display_domain(candidate) or "?")
                        last_error = WebError("web.read_failed", "The page could not be processed.")
                        continue
                    data = SourceData(
                        url=url, status="ok", final_url=result.final_url if candidate == url else url,
                        title=page.title, site_name=page.site_name, description=page.description,
                        published=page.published, kind=page.kind, content_type=result.content_type,
                        content=page.markdown, truncated=page.truncated, notes=list(dict.fromkeys(plan.notes + page.notes)),
                        fetched_at=now_iso(),
                    )
                    last_error = None
                    break
            if last_error:
                raise last_error
        except WebError as exc:
            data.status, data.error_code, data.error_params = "error", exc.code, exc.params
            data.title = data.title or exc.params.get("title")
            log.info("web read failed host=%s code=%s", display_domain(url) or "?", exc.code)
        if data.status == "ok":  # failures are retried on the next request
            self._cache_put(uid, url, data)
        return data

    async def search(self, user: dict, query: str, count: int, settings: WebAdminSettings) -> list[SearchResult]:
        self.consume(user["id"], "search", settings.searches_per_hour)
        engine = self.engine(settings)
        return await engine.search(query, count)

    async def test(self, kind: str, settings: WebAdminSettings, config: EngineConfig, query: str) -> dict[str, Any]:
        engine = self.engine(settings, kind, config)
        report = await probe_engine(engine, query)
        report["checked_at"] = now_iso()
        self._set_app(f"web_status:{kind}", {k: v for k, v in report.items() if k != "sample"})
        return report

    @staticmethod
    def merge_results(batches: list[list[SearchResult]], settings: WebAdminSettings, limit: int) -> list[SearchResult]:
        """Interleave results of several queries by rank, drop duplicates
        (canonical URL), tracking parameters and filtered domains."""
        merged: list[SearchResult] = []
        seen: set[str] = set()
        depth = max((len(b) for b in batches), default=0)
        for i in range(depth):
            for batch in batches:
                if i >= len(batch):
                    continue
                item = batch[i]
                url = strip_tracking(item.url)
                if not url.lower().startswith(("http://", "https://")):
                    continue
                key = canonical_url(url)
                if key in seen:
                    continue
                try:
                    WebService.check_domain(url, settings)
                except WebError:
                    continue
                seen.add(key)
                merged.append(SearchResult(title=item.title, url=url, snippet=item.snippet, published=item.published,
                                           engine=item.engine, rank=len(merged) + 1))
                if len(merged) >= limit:
                    return merged
        return merged

    # ---------------------------------------------------------- persistence
    def store(self, user_id: str, conversation_id: str, message_id: str, origin: str, items: list[SourceData]) -> list[dict]:
        rows = []
        with self.db.transaction() as conn:
            for position, item in enumerate(items):
                sid = new_id()
                conn.execute(
                    """INSERT INTO web_sources(id, user_id, conversation_id, message_id, origin, position, url, final_url,
                           title, site_name, description, published, kind, content_type, status, error_code, error_params,
                           content, truncated, notes, query, snippet, rank, engine, fetched_at, created_at)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (sid, user_id, conversation_id, message_id, origin, position, item.url, item.final_url, item.title,
                     item.site_name, item.description, item.published, item.kind, item.content_type, item.status,
                     item.error_code, dumps(item.error_params or {}), item.content, int(item.truncated),
                     dumps(item.notes), item.query, item.snippet, item.rank, item.engine, item.fetched_at, now_iso()),
                )
                rows.append(sid)
        return [source_meta(r) for r in self.rows_by_ids(rows)]

    def rows_by_ids(self, ids: list[str]) -> list[dict]:
        if not ids:
            return []
        marks = ",".join("?" * len(ids))
        rows = {r["id"]: r for r in self.db.all(f"SELECT * FROM web_sources WHERE id IN ({marks})", tuple(ids))}
        return [rows[i] for i in ids if i in rows]

    def for_conversation(self, user_id: str, conversation_id: str) -> list[dict]:
        rows = self.db.all(
            f"SELECT {META_COLUMNS} FROM web_sources WHERE user_id = ? AND conversation_id = ? ORDER BY created_at, position",
            (user_id, conversation_id),
        )
        return [source_meta(r) for r in rows]

    def full_for_conversation(self, user_id: str, conversation_id: str) -> list[dict]:
        return self.db.all(
            "SELECT * FROM web_sources WHERE user_id = ? AND conversation_id = ? ORDER BY created_at, position",
            (user_id, conversation_id),
        )

    def owned(self, user_id: str, source_id: str) -> dict | None:
        return self.db.one("SELECT * FROM web_sources WHERE id = ? AND user_id = ?", (source_id, user_id))

    def update_from(self, source_id: str, data: SourceData) -> None:
        self.db.execute(
            """UPDATE web_sources SET final_url = ?, title = COALESCE(?, title), site_name = ?, description = ?, published = ?,
                   kind = ?, content_type = ?, status = ?, error_code = ?, error_params = ?, content = ?, truncated = ?,
                   notes = ?, fetched_at = ? WHERE id = ?""",
            (data.final_url, data.title, data.site_name, data.description, data.published, data.kind, data.content_type,
             data.status, data.error_code, dumps(data.error_params or {}), data.content, int(data.truncated),
             dumps(data.notes), data.fetched_at, source_id),
        )
