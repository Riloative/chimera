"""Per-turn web work: read pasted links, run a search, and fit the material
into the model's context. Runs inside the generation task, so pressing Stop
cancels every stage (query generation, searches, fetches) like generation.

Progress is published as ``web`` events carrying the complete current state
(steps, queries, source metadata), which is also persisted on the assistant
message (``messages.web``) so a reload shows exactly what was shown live.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any, Callable

from ..db import Database, dumps
from ..providers import ChatMessage, ChatRequest, ProviderError
from .errors import WebError
from .prompting import DEFAULT_AUTO_PROMPT, fallback_query, parse_decision, parse_queries, query_prompt, sanitize, source_block, wrap_sources
from .rank import BudgetItem, allocate
from .service import SourceData, WebService
from .settings import WebAdminSettings
from .urls import display_domain

log = logging.getLogger("chimera.web")

QUERY_TIMEOUT = 30.0
# Auto: a slow decision must not hold the answer up for long.
DECIDE_TIMEOUT = 20.0
MAX_QUERIES = 3


@dataclass
class WebPlan:
    urls: list[str] = field(default_factory=list)
    search: bool = False
    # "Auto": the model decides whether this message needs a search.
    auto: bool = False
    timezone: str | None = None

    @property
    def active(self) -> bool:
        return bool(self.urls or self.search or self.auto)


class WebTurn:
    """Steps, queries and sources of one answer; every change is published."""

    def __init__(self, db: Database, message_id: str, publish: Callable[[str, dict], None], plan: WebPlan):
        self.db = db
        self.message_id = message_id
        self._publish = publish
        self.state: dict[str, Any] = {"search": plan.search, "auto": plan.auto, "links": bool(plan.urls), "steps": [], "queries": [], "refs": []}
        self.sources: dict[str, dict] = {}

    def step(self, kind: str, status: str = "running", code: str | None = None, **params: Any) -> int:
        self.state["steps"].append({"id": len(self.state["steps"]), "kind": kind, "status": status, "code": code, "params": params})
        self.emit()
        return len(self.state["steps"]) - 1

    def update(self, step_id: int, status: str | None = None, code: str | None = None, **params: Any) -> None:
        step = self.state["steps"][step_id]
        if status:
            step["status"] = status
        if code:
            step["code"] = code
        step["params"].update(params)
        self.emit()

    def add_sources(self, metas: list[dict]) -> None:
        for meta in metas:
            self.sources[meta["id"]] = meta
        self.emit()

    def finish_running(self, status: str) -> None:
        for step in self.state["steps"]:
            if step["status"] == "running":
                step["status"] = status

    def persist(self) -> None:
        self.db.execute("UPDATE messages SET web = ? WHERE id = ?", (dumps(self.state), self.message_id))

    def emit(self) -> None:
        self.persist()
        self._publish("web", {"message_id": self.message_id, "web": self.state, "sources": list(self.sources.values())})


async def read_links(service: WebService, user: dict, urls: list[str], settings: WebAdminSettings, turn: WebTurn) -> list[SourceData]:
    """Read pasted links concurrently; never raises for page failures."""
    step = turn.step("read_links", total=len(urls), done=0, domains=[], failed=0)
    results: list[SourceData | None] = [None] * len(urls)

    async def one(i: int, url: str) -> None:
        data = await service.read(user, url, settings, robots=False)
        results[i] = data
        params = turn.state["steps"][step]["params"]
        turn.update(step, done=params["done"] + 1, domains=[*params["domains"], display_domain(url)],
                    failed=params["failed"] + (0 if data.ok else 1))

    await asyncio.gather(*(one(i, u) for i, u in enumerate(urls)))
    out = [r for r in results if r is not None]
    turn.update(step, status="done" if any(r.ok for r in out) else "error")
    return out


async def generate_queries(provider, model: str, message: str, history: list[tuple[str, str]], settings: WebAdminSettings,
                           tz: str | None) -> tuple[list[str], bool]:
    """Ask the model for 1–3 queries; fall back to the message text on any failure."""
    fallback = [fallback_query(message)] if fallback_query(message) else []
    if not settings.query_generation:
        return fallback, False
    convo = "\n".join(f"{'User' if role == 'user' else 'Assistant'}: {text[:600]}" for role, text in history[-6:])
    prompt = query_prompt(settings.query_prompt, conversation=convo, message=message[:4000], max_queries=MAX_QUERIES, tz_name=tz)
    request = ChatRequest(model=model, messages=[ChatMessage(role="user", content=prompt)], params={"temperature": 0, "max_tokens": 256},
                          quick=True)
    try:
        raw = await asyncio.wait_for(provider.complete(request), timeout=QUERY_TIMEOUT)
    except (ProviderError, asyncio.TimeoutError) as exc:
        log.info("query generation failed (%s); using the message as the query", type(exc).__name__)
        return fallback, True
    queries = parse_queries(raw, MAX_QUERIES)
    return (queries, False) if queries else (fallback, True)


async def decide_search(provider, model: str, message: str, history: list[tuple[str, str]], settings: WebAdminSettings,
                        tz: str | None) -> tuple[bool, str, list[str]] | None:
    """Auto mode: ask the model whether this message needs a search (and for
    the queries if so) in one quick call. None = no usable decision (failed,
    timed out or unreadable); the answer is then written without searching."""
    convo = "\n".join(f"{'User' if role == 'user' else 'Assistant'}: {text[:600]}" for role, text in history[-6:])
    template = settings.auto_prompt.strip() or DEFAULT_AUTO_PROMPT
    prompt = query_prompt(template, conversation=convo, message=message[:4000], max_queries=MAX_QUERIES, tz_name=tz)
    request = ChatRequest(model=model, messages=[ChatMessage(role="user", content=prompt)], params={"temperature": 0, "max_tokens": 256},
                          quick=True)
    try:
        raw = await asyncio.wait_for(provider.complete(request), timeout=DECIDE_TIMEOUT)
    except (ProviderError, asyncio.TimeoutError) as exc:
        log.info("search decision failed (%s); answering without search", type(exc).__name__)
        return None
    decision = parse_decision(raw, MAX_QUERIES)
    if decision and decision[0] and not decision[2]:
        fallback = fallback_query(message)
        decision = (True, decision[1], [fallback] if fallback else [])
    return decision


async def run_search(service: WebService, user: dict, queries: list[str], count: int, settings: WebAdminSettings,
                     turn: WebTurn) -> list[SourceData]:
    """Search, then read the top results. Raises WebError when nothing usable
    comes back (the answer is then not generated; the UI offers retry)."""
    step = turn.step("search", queries=queries, count=0)
    batches, errors = [], []
    per_query = min(10, count + 3)
    outcomes = await asyncio.gather(*(service.search(user, q, per_query, settings) for q in queries), return_exceptions=True)
    for outcome in outcomes:
        if isinstance(outcome, WebError):
            errors.append(outcome)
        elif isinstance(outcome, BaseException):
            raise outcome
        else:
            batches.append(outcome)
    if not batches:
        err = errors[0] if errors else WebError("web.engine_error", "The search engine reported an error.")
        turn.update(step, status="error", code=err.code)
        raise err
    results = service.merge_results(batches, settings, count)
    turn.update(step, count=len(results))
    if not results:
        turn.update(step, status="error", code="web.no_results")
        raise WebError("web.no_results", "The search returned no results.")
    turn.update(step, status="done")

    read = turn.step("read_results", total=len(results), done=0, domains=[], failed=0)
    out: list[SourceData | None] = [None] * len(results)

    async def one(i: int) -> None:
        r = results[i]
        data = await service.read(user, r.url, settings, robots=settings.robots_for_search)
        data.query, data.snippet, data.rank, data.engine = None, r.snippet or None, r.rank, r.engine
        data.title = data.title or r.title
        data.published = data.published or (r.published[:10] if r.published else None)
        if not data.ok and r.snippet:
            # Keep the failure code for the UI but give the model the snippet.
            data.status, data.content = "snippet", r.snippet
        out[i] = data
        params = turn.state["steps"][read]["params"]
        turn.update(read, done=params["done"] + 1, domains=[*params["domains"], display_domain(r.url)],
                    failed=params["failed"] + (0 if data.status == "ok" else 1))

    await asyncio.gather(*(one(i) for i in range(len(results))))
    sources = [s for s in out if s is not None]
    if not any(s.ok for s in sources):
        turn.update(read, status="error", code="web.all_fetches_failed")
        raise WebError("web.all_fetches_failed", "None of the search results could be read.")
    turn.update(read, status="done")
    return sources


@dataclass
class ComposeResult:
    refs: list[dict]
    used_current: int
    total_current: int
    omitted_older: int


def compose(
    *,
    turns: list[tuple[str, ChatMessage]],
    sources_by_message: dict[str, list[dict]],
    current_user_id: str | None,
    search_rows: list[dict],
    token: str,
    budget_chars: int,
    per_source_chars: int,
    query: str,
    start_n: int,
) -> ComposeResult:
    """Inject numbered source blocks into the user turns (newest material has
    priority), within ``budget_chars``. Mutates the ChatMessages in ``turns``."""
    current_rows = list(sources_by_message.get(current_user_id or "", [])) + list(search_rows)
    items: list[tuple[str, dict, BudgetItem]] = []
    for row in current_rows:
        ok = row["status"] in ("ok", "snippet")
        items.append((current_user_id or "", row, BudgetItem(
            key=row["id"], text=row["content"] or "", ok=ok, focused=row["origin"] == "search", query=query)))
    older = [mid for mid, _ in reversed(turns) if mid != current_user_id and sources_by_message.get(mid)]
    turn_text = {mid: msg.content for mid, msg in turns}
    for mid in older:
        for row in sources_by_message[mid]:
            if row["status"] not in ("ok", "snippet"):
                continue  # an old failure is not worth context; the earlier answer already said so
            items.append((mid, row, BudgetItem(key=row["id"], text=row["content"] or "", ok=True, focused=False,
                                               query=f"{turn_text.get(mid, '')}\n{query}")))
    allocate([b for _, _, b in items], budget_chars, per_source_chars)

    blocks: dict[str, list[str]] = {}
    notes: dict[str, list[str]] = {}
    refs: list[dict] = []
    # Search results of this turn that were read but didn't fit: listed under
    # the answer (numbered after everything the model sees), never sent.
    unsent: list[dict] = []
    n = start_n
    used_current = total_current = omitted = 0
    current_ids = {r["id"] for r in current_rows}
    for mid, row, item in items:
        is_current = row["id"] in current_ids
        if is_current and row["status"] in ("ok", "snippet"):
            total_current += 1
        if item.ok and not item.included:
            if is_current and row["origin"] == "pasted":
                blocks.setdefault(mid, []).append(source_block(
                    n, token, url=row["final_url"] or row["url"], title=row["title"], fetched_at=row["fetched_at"],
                    content="", ok=False, failure="it could not be included because the model's context window is full",
                    origin="pasted"))
                refs.append({"n": n, "source_id": row["id"], "included": False})
                n += 1
            elif is_current:
                unsent.append({"source_id": row["id"], "included": False})
            else:
                omitted += 1
                notes.setdefault(mid, []).append(
                    f"[A page read earlier was left out to fit the context window: "
                    f"{sanitize(row['title'] or '', token)[:120]} — {row['final_url'] or row['url']}]")
            continue
        origin = row["origin"] if row["status"] != "snippet" else "snippet"
        failure = None
        if not item.ok:
            failure = row["error_code"] or "unknown error"
        blocks.setdefault(mid, []).append(source_block(
            n, token, url=row["final_url"] or row["url"], title=row["title"], fetched_at=row["fetched_at"],
            content=item.fitted if item.ok else "", ok=item.ok, failure=failure, published=row["published"],
            trimmed=item.trimmed or bool(row["truncated"]), origin=origin if origin != "search" else "search",
            notes=_loads_list(row.get("notes"))))
        # Pages from earlier turns are re-sent so follow-ups work; they're
        # listed under this answer only if it cites them (see ``finish_refs``).
        refs.append({"n": n, "source_id": row["id"], "included": item.ok, **({} if is_current else {"earlier": True})})
        if is_current and item.ok:
            used_current += 1
        n += 1

    for ref in unsent:
        refs.append({"n": n, **ref})
        n += 1

    for mid, msg in turns:
        prefix = []
        if blocks.get(mid):
            prefix.append(wrap_sources(blocks[mid]))
        prefix.extend(notes.get(mid, []))
        if prefix:
            msg.content = "\n\n".join(prefix) + "\n\n" + msg.content
    return ComposeResult(refs=refs, used_current=used_current, total_current=total_current, omitted_older=omitted)


def ref_shown(ref: dict, cited: set[int]) -> bool:
    """Whether a source is listed under the answer: everything from this turn
    (refs without ``earlier``, which includes every ref stored before 0.4.1),
    plus earlier-turn pages the answer actually cites."""
    if "shown" in ref:
        return bool(ref["shown"])
    return not ref.get("earlier") or ref.get("n") in cited


def finish_refs(refs: list[dict], cited: list[int]) -> None:
    """Record the visibility decision on each ref once the answer is final."""
    used = set(cited)
    for ref in refs:
        ref["shown"] = not ref.get("earlier") or ref.get("n") in used


def _loads_list(value: object) -> list[str]:
    import json

    if isinstance(value, list):
        return value
    try:
        data = json.loads(value) if isinstance(value, str) else []
    except ValueError:
        return []
    return data if isinstance(data, list) else []
