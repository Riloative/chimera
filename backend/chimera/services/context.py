"""Automatic context management.

For every request Chimera computes

    available = context_window − reserved_output − safety_margin

and fills it in priority order:

1. the system prompt (plus a summary of older turns, if one is used),
2. the latest user message with its attachments and web material,
3. exchanges the user pinned to the context,
4. recent history, newest first, in whole exchanges (a user message and the
   answers that follow it), never cutting a message in half.

If 1 and 2 alone don't fit, nothing is sent (``context.too_large``).

**Prompt-cache hysteresis.** llama-server reuses its KV cache for the longest
common prefix with the previous request (``cache_prompt``). Dropping one old
message per turn would change the prefix every time and force the whole
conversation to be re-processed. So when history overflows, it is cut down to
``hysteresis`` (default 75 %) of its budget, and that cut point is kept for as
long as everything after it still fits; only then is it moved again.

**Token counts** come from llama-server's ``/tokenize`` (exact), with the
chat template's per-message overhead measured once via ``/apply-template``;
the final request is verified with one exact count of the rendered prompt.
Other servers fall back to an estimate (≈3.5 characters per token plus a
per-message overhead) with the safety margin absorbing the error.
"""

from __future__ import annotations

import hashlib
import math
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from ..errors import AppError
from ..providers import ChatMessage, ChatRequest, Provider

CHARS_PER_TOKEN = 3.5
MESSAGE_OVERHEAD_ESTIMATE = 8
IMAGE_TOKENS = 768
SUMMARY_SHARE = 0.2
SUMMARY_MAX_TOKENS = 600
_CACHE_SIZE = 20_000
_cache: "OrderedDict[tuple[str, str, str], int]" = OrderedDict()
_overheads: dict[tuple[str, str], int] = {}


class ContextTooLarge(AppError):
    def __init__(self, needed: int, available: int):
        super().__init__(400, "context.too_large", "The message is too long for the model's context window.",
                         needed=needed, available=max(0, available))


def estimate_tokens(text: str) -> int:
    return math.ceil(len(text or "") / CHARS_PER_TOKEN)


@dataclass
class Window:
    size: int
    source: str  # manual | server | default
    slots: int | None = None


def context_window(row: dict | None, model: str, fallback: int) -> Window:
    """Per-connection override > what the server reports (llama-server's
    per-slot ``n_ctx`` from ``/props``) > the user's fallback."""
    from ..db import loads

    row = row or {}
    override = row.get("context_override")
    if isinstance(override, int) and override > 0:
        return Window(override, "manual")
    caps = loads(row.get("capabilities"), {}) or {}
    n = caps.get("n_ctx")
    if not (isinstance(n, int) and n > 0):
        for m in loads(row.get("cached_models"), []) or []:
            if m.get("id") == model and isinstance((m.get("meta") or {}).get("n_ctx"), int):
                n = m["meta"]["n_ctx"]
    if isinstance(n, int) and n > 0:
        return Window(n, "server", caps.get("slots") if isinstance(caps.get("slots"), int) else None)
    return Window(fallback, "default")


class TokenCounter:
    """Counts tokens for one provider/model, exactly when the server can."""

    def __init__(self, provider: Provider | None, model: str):
        self.provider = provider
        self.model = model
        self.key = provider.root_url if provider else "estimate"
        self.exact = provider is not None

    async def count(self, text: str) -> int:
        if not text:
            return 0
        digest = hashlib.sha1(text.encode("utf-8", "replace")).hexdigest()
        key = (self.key, self.model, digest)
        if key in _cache:
            _cache.move_to_end(key)
            return _cache[key]
        n = await self.provider.tokenize_count(text) if self.provider and self.exact else None
        if n is None:
            self.exact = False
            return estimate_tokens(text)
        _cache[key] = n
        if len(_cache) > _CACHE_SIZE:
            _cache.popitem(last=False)
        return n

    async def overhead(self) -> int:
        """Template tokens added per message (role markers etc.), measured once."""
        key = (self.key, self.model)
        if key in _overheads:
            return _overheads[key]
        value = MESSAGE_OVERHEAD_ESTIMATE
        if self.provider and self.exact:
            probe = "ping"
            whole = await self.provider.prompt_tokens(ChatRequest(model=self.model, messages=[ChatMessage(role="user", content=probe)]))
            text = await self.provider.tokenize_count(probe)
            if whole is not None and text is not None:
                value = max(1, whole - text)
                _overheads[key] = value
        return value

    async def message(self, msg: ChatMessage) -> int:
        return await self.count(msg.content) + await self.overhead() + IMAGE_TOKENS * len(msg.images)

    async def exact_total(self, request: ChatRequest) -> int | None:
        if not (self.provider and self.exact):
            return None
        return await self.provider.prompt_tokens(request)


@dataclass
class Turn:
    id: str | None
    msg: ChatMessage
    pinned: bool = False
    tokens: int = 0


@dataclass
class Plan:
    messages: list[ChatMessage]  # history + latest, in order (no system)
    system: str
    cut_id: str | None
    info: dict[str, Any] = field(default_factory=dict)


Summarizer = Callable[[list[Turn], int], Awaitable[str | None]]


def exchanges(turns: list[Turn]) -> list[list[Turn]]:
    """Group a path into exchanges: a user message and what answers it."""
    groups: list[list[Turn]] = []
    for turn in turns:
        if turn.msg.role == "user" or not groups:
            groups.append([turn])
        else:
            groups[-1].append(turn)
    return groups


def summary_block(text: str) -> str:
    return ("Summary of the earlier part of this conversation (the original messages are not included; "
            "use it as background):\n" + text.strip())


async def plan_context(
    *,
    system: str,
    turns: list[Turn],
    counter: TokenCounter,
    window: Window,
    reserve: int,
    margin: int,
    strategy: str,
    hysteresis: float,
    prev_cut: str | None,
    summarizer: Summarizer | None = None,
    cached_summary: Callable[[list[Turn]], str | None] | None = None,
    tighten: float = 1.0,
) -> Plan:
    available = int((window.size - reserve - margin) * tighten)
    latest = turns[-1] if turns else None
    history = turns[:-1]
    sys_tokens = (await counter.count(system) + await counter.overhead()) if system else 0
    latest_tokens = await counter.message(latest.msg) if latest else 0
    for turn in history:
        turn.tokens = await counter.message(turn.msg)
    info: dict[str, Any] = {
        "window": window.size, "window_source": window.source, "strategy": strategy, "dropped": 0,
        "cut_before": None, "summary": None, "summary_failed": False,
    }

    def finish(included: list[Turn], summary: str | None, s_tokens: int, pinned_tokens: int, cut_before: str | None,
               dropped: int, cut_id: str | None) -> Plan:
        hist_tokens = sum(t.tokens for t in included if not t.pinned) if strategy != "off" else sum(t.tokens for t in included)
        info.update(
            exact=counter.exact,
            used=sys_tokens + s_tokens + latest_tokens + pinned_tokens + hist_tokens,
            breakdown={"system": sys_tokens, "summary": s_tokens, "latest": latest_tokens, "pinned": pinned_tokens,
                       "history": hist_tokens, "reserved": reserve},
            dropped=dropped, cut_before=cut_before, summary=summary,
        )
        sys_text = f"{system}\n\n{summary_block(summary)}" if summary and system else (summary_block(summary) if summary else system)
        msgs = [t.msg for t in included] + ([latest.msg] if latest else [])
        return Plan(messages=msgs, system=sys_text, cut_id=cut_id, info=info)

    if strategy == "off":
        return finish(history, None, 0, 0, None, 0, None)

    required = sys_tokens + latest_tokens
    if required > available:
        raise ContextTooLarge(needed=required, available=available)

    groups = exchanges(history)
    fixed = required
    pinned_in: set[int] = set()
    for i, group in enumerate(groups):
        if any(t.pinned for t in group):
            cost = sum(t.tokens for t in group)
            if fixed + cost <= available:
                pinned_in.add(i)
                fixed += cost
    rest = [i for i in range(len(groups)) if i not in pinned_in]
    cost = {i: sum(t.tokens for t in groups[i]) for i in range(len(groups))}
    hist_avail = available - fixed
    total = sum(cost[i] for i in rest)
    summary_budget = 0
    first_kept = 0  # index into ``rest``
    if total > hist_avail:
        if strategy == "summarize":
            summary_budget = min(SUMMARY_MAX_TOKENS, int(hist_avail * SUMMARY_SHARE))
            hist_avail -= summary_budget
        start_ids = [groups[i][0].id for i in rest]
        if prev_cut in start_ids and sum(cost[i] for i in rest[start_ids.index(prev_cut):]) <= hist_avail:
            first_kept = start_ids.index(prev_cut)  # keep the prefix stable (prompt cache)
        else:
            target, used = hist_avail * hysteresis, 0
            first_kept = len(rest)
            for pos in range(len(rest) - 1, -1, -1):
                if used + cost[rest[pos]] > target:
                    break
                used += cost[rest[pos]]
                first_kept = pos
    kept = set(rest[first_kept:]) | pinned_in
    dropped_groups = [i for i in range(len(groups)) if i not in kept]
    dropped_turns = [t for i in dropped_groups for t in groups[i]]
    included = [t for i in range(len(groups)) if i in kept for t in groups[i]]
    for i in pinned_in:
        for t in groups[i]:
            t.pinned = True  # count the whole pinned exchange as pinned
    pinned_tokens = sum(cost[i] for i in pinned_in)

    summary, s_tokens = None, 0
    if dropped_turns and strategy == "summarize":
        summary = cached_summary(dropped_turns) if cached_summary else None
        if summary is None and summarizer is not None:
            summary = await summarizer(dropped_turns, summary_budget)
            if summary is None:
                info["summary_failed"] = True
        if summary:
            s_tokens = await counter.count(summary_block(summary))
            if s_tokens > summary_budget + 64:  # model ignored the length limit: don't overflow
                summary, s_tokens = None, 0
                info["summary_failed"] = True
    cut_before = None
    if dropped_turns:
        later = [t for t in included if not t.pinned]
        cut_before = later[0].id if later else (latest.id if latest else None)
    cut_id = groups[rest[first_kept]][0].id if dropped_turns and first_kept < len(rest) else None
    return finish(included, summary, s_tokens, pinned_tokens, cut_before, len(dropped_turns), cut_id)


async def verify_exact(plan: Plan, counter: TokenCounter, model: str, params: dict, window: Window, reserve: int,
                       margin: int) -> int | None:
    """Exact prompt size of the final request (llama-server), or ``None``."""
    msgs = ([ChatMessage(role="system", content=plan.system)] if plan.system else []) + plan.messages
    return await counter.exact_total(ChatRequest(model=model, messages=msgs, params=params))


def covers_hash(turns: list[Turn]) -> str:
    return hashlib.sha256("|".join(t.id or "" for t in turns).encode()).hexdigest()
