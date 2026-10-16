"""Generation engine.

A generation runs as a background asyncio task that is *not* tied to the
browser request: closing the tab or reloading does not lose the answer. The
task persists partial output every ~0.5 s and publishes events to any number
of subscribers (the request that started it, or a client that re-attaches
after a reload). Stopping cancels the task, which closes the upstream HTTP
connection so llama-server stops decoding and frees its slot.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from dataclasses import dataclass, field
from typing import Any, AsyncIterator

from ..db import Database, dumps, loads, new_id, now_iso
from ..errors import AppError
from ..providers import ChatMessage, ChatRequest, ProviderError
from ..web.errors import WebError
from ..web.pipeline import WebPlan, WebTurn, compose, finish_refs, generate_queries, read_links, run_search
from ..web.prompting import citations, grounding_prompt, new_token
from ..web.rank import estimate_tokens, tokens_to_chars
from .context import ContextTooLarge, TokenCounter, Turn, Window, context_window, covers_hash, plan_context, verify_exact
from ..web.service import WebService
from ..web.urls import dedupe_key, find_urls
from . import tree as message_tree
from .conversations import ConversationStore, fallback_title, message_out
from .files import FileStore
from .knowledge import KnowledgeBase
from .providers import ProviderRegistry
from .settings import SettingsStore

log = logging.getLogger("chimera.chat")

PERSIST_INTERVAL = 0.5
KEEPALIVE_SECONDS = 15
TITLE_TIMEOUT = 45
TERMINAL_EVENTS = {"end"}
SUMMARY_TIMEOUT = 60
SOURCE_HEADER_CHARS = 260  # url/title/status lines around each source block
SUMMARY_PROMPT = """Summarize the earlier part of a conversation between a user and an assistant so the assistant can continue it without the original messages.

Keep: facts, decisions, names, numbers, code identifiers, the user's goals and preferences, and open questions. Drop pleasantries. Write in the language of the conversation, as compact notes, at most {words} words. Do not add anything that is not in the text.

Existing summary of even earlier messages (extend it, don't repeat it):
{previous}

Messages to summarize:
{transcript}"""


class ChatError(AppError):
    def __init__(self, message: str, status: int = 400, code: str = "bad_request", **params: Any):
        super().__init__(status, code, message, **params)


@dataclass
class Generation:
    id: str
    user: dict
    conversation_id: str
    message_id: str
    provider_id: str
    model: str
    params: dict[str, Any]
    web_plan: WebPlan = field(default_factory=WebPlan)
    user_message_id: str | None = None
    turn: WebTurn | None = None
    # context management: info for the UI, and one tighter retry on overflow
    context_info: dict = field(default_factory=dict)
    tighten: float = 1.0
    window_cap: int | None = None
    retried: bool = False
    content: str = ""
    reasoning: str = ""
    status: str = "streaming"
    finished: bool = False
    task: asyncio.Task | None = None
    subscribers: list[asyncio.Queue] = field(default_factory=list)
    history: list[tuple[str, dict]] = field(default_factory=list)

    def publish(self, event: str, data: dict) -> None:
        # Deltas are not replayed (the snapshot covers them); everything else is.
        if event not in ("delta",):
            self.history.append((event, data))
        for queue in list(self.subscribers):
            queue.put_nowait((event, data))


def sse(event: str, data: Any) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


class ChatEngine:
    def __init__(
        self,
        db: Database,
        conversations: ConversationStore,
        registry: ProviderRegistry,
        settings: SettingsStore,
        files: FileStore,
        knowledge: KnowledgeBase,
        web: WebService | None = None,
    ):
        self.web = web
        self.db = db
        self.conversations = conversations
        self.registry = registry
        self.settings = settings
        self.files = files
        self.knowledge = knowledge
        self.active: dict[str, Generation] = {}
        self.by_conversation: dict[str, str] = {}

    # -- lifecycle --------------------------------------------------------------
    def recover_interrupted(self) -> int:
        """Called at startup: nothing can still be streaming after a restart."""
        cur = self.db.execute(
            "UPDATE messages SET status = 'interrupted', error = COALESCE(error, ?), error_code = COALESCE(error_code, 'interrupted'), updated_at = ? WHERE status = 'streaming'",
            ("The server restarted before this response finished.", now_iso()),
        )
        return cur.rowcount

    async def shutdown(self) -> None:
        tasks = [g.task for g in self.active.values() if g.task and not g.task.done()]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    def active_for(self, user_id: str, conversation_id: str) -> Generation | None:
        gid = self.by_conversation.get(conversation_id)
        gen = self.active.get(gid) if gid else None
        return gen if gen and gen.user["id"] == user_id else None

    def cancel(self, user_id: str | None, generation_id: str) -> bool:
        """Cancel a generation. ``user_id=None`` is for internal callers only."""
        gen = self.active.get(generation_id)
        if not gen or (user_id is not None and gen.user["id"] != user_id):
            return False
        if gen.finished or not gen.task:
            return False
        gen.task.cancel()
        return True

    # -- resolution -------------------------------------------------------------
    def _resolve_target(self, user: dict, conv: dict, provider_id: str | None, model: str | None) -> tuple[str, str]:
        settings = self.settings.get(user["id"])
        pid = provider_id or conv.get("provider_id") or settings.default_provider_id
        mdl = model or conv.get("model") or settings.default_model
        if not pid or not mdl:
            raise ChatError("Select a model before sending a message.", code="no_model")
        row = self.registry.visible(user, pid)
        if not row:
            raise ChatError("The selected connection no longer exists. Choose another model.", code="provider_missing")
        if not row["enabled"]:
            raise ChatError(f"The connection '{row['name']}' is disabled.", code="provider_disabled", name=row["name"])
        return pid, mdl

    def _params(self, user: dict, conv: dict, overrides: dict | None) -> dict[str, Any]:
        merged = {k: v for k, v in self.settings.get(user["id"]).params.model_dump().items() if v is not None}
        merged.update({k: v for k, v in loads(conv.get("params"), {}).items() if v is not None})
        merged.update({k: v for k, v in (overrides or {}).items() if v is not None})
        return merged

    def vision_supported(self, provider_id: str, model: str) -> bool:
        row = self.registry.row(provider_id)
        if not row:
            return False
        if row["vision"] in ("on", "off"):
            return row["vision"] == "on"
        for m in loads(row["cached_models"], []):
            if m.get("id") == model and m.get("vision") is not None:
                return bool(m["vision"])
        return bool(loads(row["capabilities"], {}).get("vision"))

    def _attachment_snapshots(self, user: dict, attachment_ids: list[str], provider_id: str, model: str) -> list[dict]:
        snapshots = []
        for fid in dict.fromkeys(attachment_ids):
            row = self.files.owned(user["id"], fid)
            if not row:
                raise ChatError("An attachment could not be found. Remove it and try again.", code="attachment_missing")
            if row["kind"] == "image" and not self.vision_supported(provider_id, model):
                raise ChatError(
                    "The selected model does not accept images. Choose a vision-capable model or remove the image.",
                    code="vision_unsupported",
                )
            snapshots.append(
                {"id": row["id"], "filename": row["filename"], "mime": row["mime"], "kind": row["kind"],
                 "size": row["size"], "truncated": bool(row["truncated"])}
            )
        return snapshots

    def _ensure_idle(self, conversation_id: str) -> None:
        if conversation_id in self.by_conversation:
            raise ChatError("A response is already being generated in this conversation.", status=409, code="busy")

    def _insert_message(self, conn, conversation_id: str, parent: dict | None, role: str, content: str, **extra) -> dict:
        """Insert a node under ``parent`` (``None`` = a new root); returns a
        minimal node dict usable as the parent of the next insert."""
        mid, ts = new_id(), now_iso()
        position = parent["position"] + 1 if parent else 0
        conn.execute(
            """INSERT INTO messages(id, conversation_id, parent_id, position, role, content, status, provider_id, model,
                   attachments, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                mid, conversation_id, parent["id"] if parent else None, position, role, content,
                extra.get("status", "complete"), extra.get("provider_id"), extra.get("model"),
                dumps(extra.get("attachments") or []), ts, ts,
            ),
        )
        return {"id": mid, "position": position}

    @staticmethod
    def _activate_chain(conn, conversation_id: str, path: list[str]) -> None:
        """Point every node on ``path`` at its successor and make the last one the active leaf."""
        for parent, child in zip(path, path[1:]):
            conn.execute("UPDATE messages SET active_child_id = ? WHERE id = ?", (child, parent))
        conn.execute("UPDATE conversations SET active_leaf_id = ? WHERE id = ?", (path[-1] if path else None, conversation_id))

    def _node(self, message_id: str | None) -> dict | None:
        return self.db.one("SELECT id, position FROM messages WHERE id = ?", (message_id,)) if message_id else None

    def _start(self, user: dict, conversation_id: str, message_id: str, pid: str, model: str, params: dict,
               plan: WebPlan | None = None, user_message_id: str | None = None) -> Generation:
        gen = Generation(id=new_id(), user=user, conversation_id=conversation_id, message_id=message_id, provider_id=pid,
                         model=model, params=params, web_plan=plan or WebPlan(), user_message_id=user_message_id)
        self.active[gen.id] = gen
        self.by_conversation[conversation_id] = gen.id
        return gen

    @staticmethod
    def _timezone(name: str | None) -> str | None:
        if not name:
            return None
        try:
            from zoneinfo import ZoneInfo

            ZoneInfo(name)
            return name
        except Exception:  # noqa: BLE001 - unknown zone: fall back to UTC
            return None

    def web_plan(self, user: dict, content: str, urls: list[str] | None, web_search: bool | None, tz: str | None,
                 *, inherit_search: bool = False) -> WebPlan:
        """Decide what web work a turn does. Enforces the administrator switch
        server-side: with web access disabled nothing is fetched."""
        tz = self._timezone(tz)
        if not self.web:
            return WebPlan(timezone=tz)
        admin = self.web.admin()
        if not admin.enabled:
            if web_search or urls:
                raise WebError("web.disabled", "Web access is turned off on this server.")
            return WebPlan(timezone=tz)
        if web_search and not self.web.search_available(admin):
            if inherit_search:
                web_search = False
            else:
                raise WebError("web.search_unavailable", "Web search is not configured on this server.")
        limit = admin.max_urls_per_message
        if urls is None:
            chosen = find_urls(content, limit).urls if self.settings.get(user["id"]).web.auto_read_links else []
        else:
            # Only links that actually appear in the message can be read.
            present = {dedupe_key(u): u for u in find_urls(content, 100).urls}
            keys = [dedupe_key(u) for u in urls if isinstance(u, str) and "://" in u]
            chosen = [present[k] for k in dict.fromkeys(keys) if k in present][:limit]
        return WebPlan(urls=chosen, search=bool(web_search), timezone=tz)

    def launch(self, gen: Generation) -> None:
        gen.task = asyncio.create_task(self._run(gen), name=f"generation-{gen.id}")

    # -- entry points -----------------------------------------------------------
    def prepare_send(
        self,
        user: dict,
        conversation_id: str,
        content: str,
        attachment_ids: list[str],
        provider_id: str | None,
        model: str | None,
        params: dict | None,
        urls: list[str] | None = None,
        web_search: bool | None = None,
        timezone: str | None = None,
    ) -> tuple[Generation, dict]:
        conv = self.conversations.owned(user["id"], conversation_id)
        if not conv:
            raise ChatError("Conversation not found.", status=404, code="conversation_not_found")
        self._ensure_idle(conversation_id)
        if not content.strip() and not attachment_ids:
            raise ChatError("Message is empty.", code="empty_message")
        pid, mdl = self._resolve_target(user, conv, provider_id, model)
        plan = self.web_plan(user, content, urls, web_search, timezone)
        attachments = self._attachment_snapshots(user, attachment_ids, pid, mdl)
        tree = message_tree.load(self.db, conversation_id)
        path = message_tree.active_path(self.db, conversation_id, tree)
        first = not tree.nodes
        with self.db.transaction() as conn:
            # A new message continues the active branch.
            user_node = self._insert_message(conn, conversation_id, self._node(path[-1] if path else None), "user", content,
                                             attachments=attachments)
            asst_node = self._insert_message(conn, conversation_id, user_node, "assistant", "", status="streaming",
                                             provider_id=pid, model=mdl)
            self._activate_chain(conn, conversation_id, [*path, user_node["id"], asst_node["id"]])
            user_id, asst_id = user_node["id"], asst_node["id"]
            ts = now_iso()
            conn.execute(
                "UPDATE conversations SET provider_id = ?, model = ?, updated_at = ? WHERE id = ?",
                (pid, mdl, ts, conversation_id),
            )
            if first and conv["title_source"] == "default":
                title = fallback_title(content or (attachments[0]["filename"] if attachments else ""))
                conn.execute(
                    "UPDATE conversations SET title = ?, title_source = 'fallback' WHERE id = ?", (title, conversation_id)
                )
        gen = self._start(user, conversation_id, asst_id, pid, mdl, self._params(user, conv, params), plan, user_id)
        return gen, {"user_message": message_out(self.conversations.message(user_id))}  # type: ignore[arg-type]

    def prepare_regenerate(
        self, user: dict, conversation_id: str, message_id: str | None, provider_id: str | None, model: str | None, params: dict | None,
        web_search: bool | None = None, timezone: str | None = None,
    ) -> tuple[Generation, dict]:
        conv = self.conversations.owned(user["id"], conversation_id)
        if not conv:
            raise ChatError("Conversation not found.", status=404, code="conversation_not_found")
        self._ensure_idle(conversation_id)
        tree = message_tree.load(self.db, conversation_id)
        if message_id:
            target = self.conversations.message(message_id)
            if not target or target["conversation_id"] != conversation_id or target["role"] != "assistant":
                raise ChatError("Message not found.", status=404, code="message_not_found")
            previous = target
            parent_id = target["parent_id"]
        else:
            path = message_tree.active_path(self.db, conversation_id, tree)
            if not path:
                raise ChatError("Nothing to regenerate.", code="nothing_to_regenerate")
            last = self.conversations.message(path[-1])
            previous = last if last and last["role"] == "assistant" else None
            parent_id = last["parent_id"] if previous else path[-1]  # type: ignore[index]
        prior_user = self.conversations.message(parent_id) if parent_id else None
        if not prior_user or prior_user["role"] != "user":
            raise ChatError("Nothing to regenerate.", code="nothing_to_regenerate")
        pid, mdl = self._resolve_target(user, conv, provider_id, model)
        # Pages read for the user message are reused (no re-fetch). Search runs
        # again if the replaced answer used it, unless the caller says otherwise.
        inherit = web_search is None
        search = bool(loads(previous["web"], {}).get("search")) if inherit and previous else bool(web_search)
        plan = self.web_plan(user, "", [], search, timezone, inherit_search=inherit)
        # A new answer is a sibling of the old one, which stays as another
        # branch, except an attempt that failed or was stopped before writing
        # anything: that one is replaced so failed tries don't pile up.
        replace = previous is not None and previous["status"] in ("error", "cancelled", "interrupted") \
            and not (previous["content"] or "").strip() and not tree.kids(previous["id"])
        if replace:
            message_tree.delete_subtree(self.db, conversation_id, previous["id"])  # type: ignore[index]
        parent_path = tree.path_to(prior_user["id"])
        with self.db.transaction() as conn:
            asst_id = self._insert_message(conn, conversation_id, self._node(prior_user["id"]), "assistant", "",
                                           status="streaming", provider_id=pid, model=mdl)["id"]
            self._activate_chain(conn, conversation_id, [*parent_path, asst_id])
            conn.execute(
                "UPDATE conversations SET provider_id = ?, model = ?, updated_at = ? WHERE id = ?",
                (pid, mdl, now_iso(), conversation_id),
            )
        gen = self._start(user, conversation_id, asst_id, pid, mdl, self._params(user, conv, params), plan, prior_user["id"])
        return gen, {}

    def prepare_edit(
        self, user: dict, conversation_id: str, message_id: str, content: str, provider_id: str | None, model: str | None, params: dict | None,
        urls: list[str] | None = None, web_search: bool | None = None, timezone: str | None = None,
    ) -> tuple[Generation, dict]:
        conv = self.conversations.owned(user["id"], conversation_id)
        if not conv:
            raise ChatError("Conversation not found.", status=404, code="conversation_not_found")
        self._ensure_idle(conversation_id)
        target = self.conversations.message(message_id)
        if not target or target["conversation_id"] != conversation_id or target["role"] != "user":
            raise ChatError("Message not found.", status=404, code="message_not_found")
        if not content.strip() and not loads(target["attachments"], []):
            raise ChatError("Message is empty.", code="empty_message")
        pid, mdl = self._resolve_target(user, conv, provider_id, model)
        plan = self.web_plan(user, content, urls, web_search, timezone)
        tree = message_tree.load(self.db, conversation_id)
        parent_path = tree.path_to(target["parent_id"]) if target["parent_id"] else []
        with self.db.transaction() as conn:
            # The edit is a new sibling; the original message and everything
            # after it stay intact as another branch.
            edited = self._insert_message(conn, conversation_id, self._node(target["parent_id"]), "user", content,
                                          attachments=loads(target["attachments"], []))
            asst_id = self._insert_message(conn, conversation_id, edited, "assistant", "", status="streaming",
                                           provider_id=pid, model=mdl)["id"]
            self._activate_chain(conn, conversation_id, [*parent_path, edited["id"], asst_id])
            message_id = edited["id"]
            conn.execute(
                "UPDATE conversations SET provider_id = ?, model = ?, updated_at = ? WHERE id = ?",
                (pid, mdl, now_iso(), conversation_id),
            )
        gen = self._start(user, conversation_id, asst_id, pid, mdl, self._params(user, conv, params), plan, message_id)
        return gen, {"user_message": message_out(self.conversations.message(message_id))}  # type: ignore[arg-type]

    # -- streaming to clients ---------------------------------------------------
    def subscribe(self, gen: Generation, initial: list[tuple[str, dict]]) -> AsyncIterator[str]:
        """Attach a subscriber *now* (no events can be missed) and return its stream."""
        queue: asyncio.Queue = asyncio.Queue()
        already_finished = gen.finished
        if not already_finished:
            gen.subscribers.append(queue)
        return self._drain(gen, queue, initial, already_finished)

    async def _drain(
        self, gen: Generation, queue: asyncio.Queue, initial: list[tuple[str, dict]], already_finished: bool
    ) -> AsyncIterator[str]:
        try:
            for event, data in initial:
                yield sse(event, data)
            if already_finished:
                for event, data in gen.history:
                    yield sse(event, data)
                return
            while True:
                try:
                    event, data = await asyncio.wait_for(queue.get(), timeout=KEEPALIVE_SECONDS)
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"
                    continue
                yield sse(event, data)
                if event in TERMINAL_EVENTS:
                    return
        finally:
            if queue in gen.subscribers:
                gen.subscribers.remove(queue)

    def snapshot_event(self, gen: Generation) -> tuple[str, dict]:
        row = self.conversations.message(gen.message_id)
        message = message_out(row) if row else None
        if message:
            message["content"], message["reasoning"] = gen.content, gen.reasoning
        web_sources = self.web.for_conversation(gen.user["id"], gen.conversation_id) if self.web else []
        return "snapshot", {"generation_id": gen.id, "message": message, "web_sources": web_sources}

    # -- the generation task ----------------------------------------------------
    def _persist(self, gen: Generation, **fields: Any) -> None:
        fields.setdefault("content", gen.content)
        fields.setdefault("reasoning", gen.reasoning)
        sets = ", ".join(f"{k} = ?" for k in fields)
        values = [dumps(v) if isinstance(v, (dict, list)) else v for v in fields.values()]
        self.db.execute(f"UPDATE messages SET {sets}, updated_at = ? WHERE id = ?", (*values, now_iso(), gen.message_id))

    def path_rows(self, gen: Generation) -> list[dict]:
        """Messages on the branch leading to the message being generated (only
        this branch is ever sent to the model)."""
        tree = message_tree.load(self.db, gen.conversation_id)
        ids = tree.path_to(gen.message_id)[:-1] if gen.message_id in tree.nodes else []
        if not ids:
            return []
        rows = {r["id"]: r for r in self.db.all(
            f"SELECT * FROM messages WHERE id IN ({','.join('?' * len(ids))})", tuple(ids))}
        return [rows[i] for i in ids if i in rows]

    async def build_request(self, gen: Generation) -> tuple[ChatRequest, list[dict]]:
        conv = self.conversations.get(gen.conversation_id) or {}
        settings = self.settings.get(gen.user["id"])
        rows = self.path_rows(gen)
        vision = self.vision_supported(gen.provider_id, gen.model)
        messages: list[ChatMessage] = []
        user_turns: list[tuple[str, ChatMessage]] = []
        turns: list[Turn] = []
        for row in rows:
            if row["role"] == "assistant":
                if row["status"] == "error" or not row["content"].strip():
                    continue
                msg = ChatMessage(role="assistant", content=row["content"])
            elif row["role"] == "user":
                msg = self._user_message(gen.user["id"], row, vision)
                user_turns.append((row["id"], msg))
            else:
                continue
            messages.append(msg)
            turns.append(Turn(id=row["id"], msg=msg, pinned=bool(row.get("pinned"))))
        # Drop dangling consecutive user turns left by failed responses? Keep
        # them: the model should see everything the user actually said.
        system = conv.get("system_prompt")
        if system is None or not str(system).strip():
            system = settings.default_system_prompt
        system = (system or "").strip()

        sources: list[dict] = []
        collection_ids = loads(conv.get("collection_ids"), [])
        last_user = next((m for m in reversed(messages) if m.role == "user"), None)
        if collection_ids and last_user and last_user.content.strip():
            hits = await self.knowledge.search(gen.user, collection_ids, last_user.content[:2000], settings.knowledge)
            if hits:
                sources = [
                    {"n": i + 1, "filename": h.filename, "document_id": h.document_id, "chunk_index": h.index,
                     "text": h.text[:600], "score": h.score, "method": h.method}
                    for i, h in enumerate(hits)
                ]
                context = "\n\n".join(f"[{i + 1}] (from {h.filename})\n{h.text}" for i, h in enumerate(hits))
                knowledge_prompt = (
                    "Use the following excerpts from the user's documents when they are relevant. "
                    "Cite them inline as [1], [2], etc. If they do not contain the answer, say so "
                    "and answer from general knowledge only when appropriate.\n\n"
                    f"<sources>\n{context}\n</sources>"
                )
                system = f"{system}\n\n{knowledge_prompt}" if system else knowledge_prompt
        # -- context window budget (services/context.py) --------------------
        cctx = settings.context
        strategy = conv.get("context_strategy") or cctx.strategy
        provider = self.registry.provider(gen.user, gen.provider_id)
        counter = TokenCounter(provider, gen.model)
        window = context_window(self.registry.row(gen.provider_id), gen.model, cctx.fallback_window)
        if gen.window_cap and gen.window_cap < window.size:
            window = Window(gen.window_cap, "server", window.slots)  # what the server said in its error
        max_tokens = gen.params.get("max_tokens")
        reserve = max_tokens if isinstance(max_tokens, int) and max_tokens > 0 else min(cctx.reserve_tokens, max(64, window.size // 4))
        margin = max(32, int(window.size * cctx.safety_margin_pct / 100))
        available = int((window.size - reserve - margin) * gen.tighten)
        # Web material for the latest message comes first; history is trimmed after it.
        if strategy == "off":
            web_tokens = available - estimate_tokens(system) - sum(estimate_tokens(m.content) + 8 for m in messages)
        else:
            web_tokens = available - await counter.count(system) - (await counter.message(turns[-1].msg) if turns else 0)
        state = loads(conv.get("context_state"), {}) or {}
        base_system, originals = system, [m.content for m in messages]
        share = 1.0
        for attempt in range(4):
            for m, text in zip(messages, originals):
                m.content = text  # web composition prefixes source blocks; start clean
            self._drop_steps(gen, "budget", "older_omitted", "history_trimmed")
            system = self._compose_web(gen, messages, user_turns, base_system, len(sources), int(web_tokens * share), window.size)
            try:
                plan = await plan_context(
                    system=system, turns=turns, counter=counter, window=window, reserve=reserve, margin=margin,
                    strategy=strategy, hysteresis=cctx.hysteresis, prev_cut=state.get("cut_id"),
                    summarizer=lambda dropped, budget: self._summarize(gen, dropped, budget, window),
                    cached_summary=lambda dropped: self._cached_summary(gen, dropped), tighten=gen.tighten,
                )
                break
            except ContextTooLarge:
                # Web pages are what can shrink: give them less room and try again.
                if attempt == 3 or system == base_system and messages[-1].content == originals[-1]:
                    raise
                share *= 0.6
        # One exact count of the rendered prompt (llama-server); trim more if the estimate was off.
        for _ in range(6):
            if strategy == "off":
                break
            exact = await verify_exact(plan, counter, gen.model, gen.params, window, reserve, margin)
            if exact is None:
                break
            plan.info["used"], plan.info["exact"] = exact, True
            if exact <= window.size - reserve:
                break
            gen.tighten *= 0.85
            plan = await plan_context(
                system=system, turns=turns, counter=counter, window=window, reserve=reserve, margin=margin,
                strategy=strategy, hysteresis=cctx.hysteresis, prev_cut=None,
                cached_summary=lambda dropped: self._cached_summary(gen, dropped), tighten=gen.tighten,
            )
        if plan.cut_id != state.get("cut_id"):
            state["cut_id"] = plan.cut_id
            self.db.execute("UPDATE conversations SET context_state = ? WHERE id = ?", (dumps(state), gen.conversation_id))
        plan.info["window_slots"] = window.slots
        plan.info["retried"] = gen.retried
        gen.context_info = plan.info
        gen.publish("context", {"message_id": gen.message_id, "context": plan.info})
        if gen.turn and plan.info.get("dropped"):
            gen.turn.step("history_trimmed", status="done", count=plan.info["dropped"])
        final = ([ChatMessage(role="system", content=plan.system)] if plan.system else []) + plan.messages
        return ChatRequest(model=gen.model, messages=final, params=gen.params), sources

    @staticmethod
    def _drop_steps(gen: Generation, *kinds: str) -> None:
        """Remove budget notes from an earlier attempt (they are always the last steps)."""
        if gen.turn:
            gen.turn.state["steps"] = [s for s in gen.turn.state["steps"] if s["kind"] not in kinds]

    async def estimate_context(self, user: dict, conversation_id: str) -> dict:
        """What the next request would contain, without sending anything:
        same budget rules as a real send, cached counts, no new summaries."""
        conv = self.conversations.owned(user["id"], conversation_id)
        if not conv:
            raise ChatError("Conversation not found.", status=404, code="conversation_not_found")
        settings = self.settings.get(user["id"])
        cctx = settings.context
        pid = conv.get("provider_id") or settings.default_provider_id
        model = conv.get("model") or settings.default_model or ""
        row = self.registry.visible(user, pid) if pid else None
        window = context_window(row, model, cctx.fallback_window)
        strategy = conv.get("context_strategy") or cctx.strategy
        out: dict[str, Any] = {"window": window.size, "window_source": window.source, "window_slots": window.slots,
                               "strategy": strategy, "exact": False}
        tree = message_tree.load(self.db, conversation_id)
        ids = message_tree.active_path(self.db, conversation_id, tree)
        rows = {r["id"]: r for r in self.db.all(
            f"SELECT * FROM messages WHERE id IN ({','.join('?' * len(ids))})", tuple(ids))} if ids else {}
        vision = self.vision_supported(pid, model) if pid else False
        turns: list[Turn] = []
        for mid in ids:
            r = rows.get(mid)
            if not r or r["status"] in ("error", "streaming") or (r["role"] == "assistant" and not r["content"].strip()):
                continue
            msg = self._user_message(user["id"], r, vision) if r["role"] == "user" else ChatMessage(role="assistant", content=r["content"])
            turns.append(Turn(id=r["id"], msg=msg, pinned=bool(r.get("pinned"))))
        turns.append(Turn(id=None, msg=ChatMessage(role="user", content="")))  # the next message (drafted in the browser)
        system = (conv.get("system_prompt") or settings.default_system_prompt or "").strip()
        provider = None
        if row and row["enabled"]:
            try:
                provider = self.registry.provider(user, pid)  # type: ignore[arg-type]
            except ProviderError:
                provider = None
        counter = TokenCounter(provider, model)
        reserve = min(cctx.reserve_tokens, max(64, window.size // 4))
        params = self._params(user, conv, None)
        if isinstance(params.get("max_tokens"), int):
            reserve = params["max_tokens"]
        margin = max(32, int(window.size * cctx.safety_margin_pct / 100))
        state = loads(conv.get("context_state"), {}) or {}
        try:
            plan = await plan_context(
                system=system, turns=turns, counter=counter, window=window, reserve=reserve, margin=margin,
                strategy=strategy, hysteresis=cctx.hysteresis, prev_cut=state.get("cut_id"),
                cached_summary=lambda dropped: self._cached_summary_for(user["id"], conversation_id, dropped),
            )
        except ContextTooLarge as exc:
            out.update(used=exc.params.get("needed"), too_large=True)
            return out
        out.update(plan.info)
        return out

    def _cached_summary_for(self, user_id: str, conversation_id: str, dropped: list[Turn]) -> str | None:
        row = self.db.one(
            "SELECT content FROM context_summaries WHERE conversation_id = ? AND user_id = ? AND covers_hash = ?",
            (conversation_id, user_id, covers_hash(dropped)),
        )
        return row["content"] if row else None

    # -- context: summaries of older turns ---------------------------------------
    def _cached_summary(self, gen: Generation, dropped: list[Turn]) -> str | None:
        return self._cached_summary_for(gen.user["id"], gen.conversation_id, dropped)

    async def _summarize(self, gen: Generation, dropped: list[Turn], budget: int, window: Window) -> str | None:
        """Summarize the dropped part of this branch (incrementally when an
        earlier summary of a prefix exists). ``None`` on any failure."""
        if budget < 64:
            return None
        ids = [t.id for t in dropped]
        base, rest = None, dropped
        for row in self.db.all(
            "SELECT * FROM context_summaries WHERE conversation_id = ? AND user_id = ? ORDER BY created_at DESC",
            (gen.conversation_id, gen.user["id"]),
        ):
            if row["last_message_id"] in ids:
                k = ids.index(row["last_message_id"])
                if covers_hash(dropped[: k + 1]) == row["covers_hash"]:
                    base, rest = row["content"], dropped[k + 1 :]
                    break
        limit = int(window.size * 0.5 * 3.5)
        parts, size = [], 0
        for t in reversed(rest):  # newest first until the input budget is used
            text = f"{'User' if t.msg.role == 'user' else 'Assistant'}: {t.msg.content[:6000]}"
            if size + len(text) > limit and parts:
                break
            parts.append(text)
            size += len(text)
        transcript = "\n\n".join(reversed(parts))
        prompt = SUMMARY_PROMPT.format(words=max(40, int(budget * 0.6)), previous=base or "(none)", transcript=transcript or "(none)")
        settings = self.settings.get(gen.user["id"]).context
        provider, model = self.registry.provider(gen.user, gen.provider_id), gen.model
        if settings.summary_provider_id and settings.summary_model and self.registry.visible(gen.user, settings.summary_provider_id):
            provider, model = self.registry.provider(gen.user, settings.summary_provider_id), settings.summary_model
        request = ChatRequest(model=model, messages=[ChatMessage(role="user", content=prompt)],
                              params={"temperature": 0.2, "max_tokens": budget})
        try:
            raw = await asyncio.wait_for(provider.complete(request), timeout=SUMMARY_TIMEOUT)
        except (ProviderError, asyncio.TimeoutError) as exc:
            log.info("summary failed (%s); falling back to the sliding window", type(exc).__name__)
            return None
        text = re.sub(r"<think>[\s\S]*?(</think>|$)", "", raw or "").strip()
        if not text:
            return None
        self.db.execute(
            """INSERT INTO context_summaries(id, user_id, conversation_id, covers_hash, last_message_id, message_count,
                   content, model, created_at) VALUES (?,?,?,?,?,?,?,?,?)""",
            (new_id(), gen.user["id"], gen.conversation_id, covers_hash(dropped), ids[-1], len(ids), text, model, now_iso()),
        )
        return text

    # -- web ------------------------------------------------------------------
    def _compose_web(self, gen: Generation, messages: list[ChatMessage], user_turns, system: str, knowledge_count: int,
                     budget_tokens: int, window: int) -> str:
        if not self.web:
            return system
        rows = self.db.all(
            "SELECT * FROM web_sources WHERE conversation_id = ? AND user_id = ? ORDER BY created_at, position",
            (gen.conversation_id, gen.user["id"]),
        )
        turn_ids = {mid for mid, _ in user_turns}
        by_message: dict[str, list[dict]] = {}
        for row in rows:
            if row["origin"] == "pasted" and row["message_id"] in turn_ids:
                by_message.setdefault(row["message_id"], []).append(row)
        search_rows = [r for r in rows if r["origin"] == "search" and r["message_id"] == gen.message_id]
        if not by_message and not search_rows:
            return system
        admin = self.web.admin()
        token = new_token()
        grounding = grounding_prompt(admin.grounding_prompt, token, gen.web_plan.timezone)
        system = f"{system}\n\n{grounding}" if system else grounding
        # Room left for page text: minus the grounding prompt and each source block's header lines.
        blocks = sum(len(v) for v in by_message.values()) + len(search_rows)
        budget = min(admin.total_chars, int(tokens_to_chars(budget_tokens - estimate_tokens(grounding)) * 0.9) - SOURCE_HEADER_CHARS * blocks)
        last_user = user_turns[-1][1].content if user_turns else ""
        query = "\n".join([*(gen.turn.state["queries"] if gen.turn else []), last_user])
        result = compose(
            turns=user_turns, sources_by_message=by_message, current_user_id=gen.user_message_id, search_rows=search_rows,
            token=token, budget_chars=budget, per_source_chars=admin.per_source_chars, query=query, start_n=knowledge_count + 1,
        )
        if gen.turn is None:
            # Pages from earlier turns only: still record the citation map.
            gen.turn = WebTurn(self.db, gen.message_id, gen.publish, WebPlan())
        gen.turn.state["refs"] = result.refs
        gen.turn.state["context_tokens"] = window
        if result.total_current and result.used_current < result.total_current:
            gen.turn.step("budget", status="done", used=result.used_current, total=result.total_current)
        elif result.omitted_older:
            gen.turn.step("older_omitted", status="done", count=result.omitted_older)
        else:
            gen.turn.emit()
        if result.refs:
            gen.turn.add_sources([m for m in self.web.for_conversation(gen.user["id"], gen.conversation_id)
                                  if m["id"] in {r["source_id"] for r in result.refs}])
        return system

    async def _web_phase(self, gen: Generation) -> None:
        assert self.web is not None
        plan, uid = gen.web_plan, gen.user["id"]
        admin = self.web.admin()
        turn = gen.turn = WebTurn(self.db, gen.message_id, gen.publish, plan)
        turn.emit()
        if plan.urls and gen.user_message_id:
            data = await read_links(self.web, gen.user, plan.urls, admin, turn)
            turn.add_sources(self.web.store(uid, gen.conversation_id, gen.user_message_id, "pasted", data))
        if plan.search:
            settings = self.settings.get(uid)
            history = [(r["role"], r["content"]) for r in self.path_rows(gen) if r["status"] != "error"]
            message = next((text for role, text in reversed(history) if role == "user"), "")
            step = turn.step("queries")
            provider, model = self.registry.provider(gen.user, gen.provider_id), gen.model
            task = settings.web
            if task.task_provider_id and task.task_model and self.registry.visible(gen.user, task.task_provider_id):
                provider, model = self.registry.provider(gen.user, task.task_provider_id), task.task_model
            queries, fallback = await generate_queries(provider, model, message, history[:-1], admin, plan.timezone)
            if not queries:
                turn.update(step, status="error", code="web.no_query")
                raise WebError("web.no_query", "There is nothing to search for in this message.")
            turn.state["queries"] = queries
            turn.update(step, status="done", queries=queries, fallback=fallback)
            count = min(settings.web.results, admin.max_results)
            data = await run_search(self.web, gen.user, queries, count, admin, turn)
            turn.add_sources(self.web.store(uid, gen.conversation_id, gen.message_id, "search", data))

    def _finish_web(self, gen: Generation, status: str, knowledge_count: int) -> None:
        turn = gen.turn
        if not turn:
            return
        turn.finish_running({"complete": "done", "cancelled": "cancelled"}.get(status, "error"))
        valid = {r["n"] for r in turn.state.get("refs", []) if r.get("included", True)}
        used = citations(gen.content)
        turn.state["citations"] = {
            "used": [n for n in used if n in valid],
            "invalid": [n for n in used if n not in valid and n > knowledge_count],
        }
        finish_refs(turn.state.get("refs", []), turn.state["citations"]["used"])
        turn.persist()

    def _user_message(self, user_id: str, row: dict, vision: bool) -> ChatMessage:
        text_blocks: list[str] = []
        images: list[tuple[str, str]] = []
        omitted = 0
        for att in loads(row["attachments"], []):
            file = self.files.owned(user_id, att.get("id", ""))
            if not file:
                text_blocks.append(f'<file name="{att.get("filename", "file")}">[This file is no longer available.]</file>')
                continue
            if file["kind"] == "image":
                data = self.files.image_base64(file) if vision else None
                if data:
                    images.append((file["mime"], data))
                else:
                    omitted += 1
            else:
                note = " (truncated)" if file["truncated"] else ""
                text_blocks.append(f'<file name="{file["filename"]}"{note}>\n{file["text"]}\n</file>')
        content = row["content"]
        if text_blocks:
            content = "<attachments>\n" + "\n".join(text_blocks) + "\n</attachments>\n\n" + content
        if omitted:
            content += f"\n\n[{omitted} image attachment(s) omitted: the current model does not accept images.]"
        return ChatMessage(role="user", content=content, images=images)

    async def _run(self, gen: Generation) -> None:
        started = time.perf_counter()
        first_token: float | None = None
        metrics: dict[str, Any] = {}
        last_persist = 0.0
        final_status, error, error_code, error_params = "complete", None, None, {}
        sources: list[dict] = []
        try:
            if gen.web_plan.active and self.web:
                await self._web_phase(gen)
            provider = self.registry.provider(gen.user, gen.provider_id)
            stream = self.settings.get(gen.user["id"]).stream
            while True:
                request, sources = await self.build_request(gen)
                if sources:
                    self._persist(gen, sources=sources)
                    gen.publish("sources", {"sources": sources})
                try:
                    async for event in provider.stream_chat(request):
                        if event.type in ("content", "reasoning"):
                            if first_token is None:
                                first_token = time.perf_counter()
                            if event.type == "content":
                                gen.content += event.text
                            else:
                                gen.reasoning += event.text
                            if stream:
                                gen.publish("delta", {event.type: event.text})
                        elif event.type == "usage":
                            metrics.update(event.usage)
                        elif event.type == "done":
                            metrics["finish_reason"] = event.finish_reason
                        now = time.perf_counter()
                        if now - last_persist >= PERSIST_INTERVAL:
                            last_persist = now
                            self._persist(gen)
                except ProviderError as exc:
                    # The server measured the prompt as too long (estimate error,
                    # different template, changed -c): retry once, tighter.
                    if (exc.code == "context_exceeded" and not gen.retried and not gen.content and not gen.reasoning
                            and gen.context_info.get("strategy") != "off"):
                        gen.retried = True
                        gen.tighten = 0.7
                        if isinstance(exc.params.get("n_ctx"), int):
                            gen.window_cap = exc.params["n_ctx"]
                        log.info("generation=%s context exceeded; retrying once with less history", gen.id)
                        continue
                    raise
                break
            if not stream and (gen.content or gen.reasoning):
                gen.publish("delta", {"content": gen.content, "reasoning": gen.reasoning})
            self.registry.mark_ok(gen.provider_id)
        except asyncio.CancelledError:
            final_status = "cancelled"
        except (WebError, ContextTooLarge) as exc:
            final_status, error, error_code, error_params = "error", exc.message, exc.code, exc.params
            log.info("generation=%s not sent: %s", gen.id, exc.code)
        except ProviderError as exc:
            final_status, error, error_code, error_params = "error", exc.message, exc.code, exc.params
            self.registry.mark_error(gen.provider_id, exc)
            log.info("generation=%s provider error code=%s", gen.id, exc.code)
        except Exception:  # noqa: BLE001
            log.exception("generation=%s failed", gen.id)
            final_status, error, error_code = "error", "An unexpected error occurred while generating the response.", "internal"
        finally:
            metrics["duration_ms"] = round((time.perf_counter() - started) * 1000)
            if first_token is not None:
                metrics["ttft_ms"] = round((first_token - started) * 1000)
            gen.status = final_status
            self._finish_web(gen, final_status, len(sources))
            self._persist(gen, status=final_status, error=error, error_code=error_code, error_params=error_params, metrics=metrics,
                          context=gen.context_info)
            self.db.execute("UPDATE conversations SET updated_at = ? WHERE id = ?", (now_iso(), gen.conversation_id))
            self.by_conversation.pop(gen.conversation_id, None)
            row = self.conversations.message(gen.message_id)
            message = message_out(row) if row else None
            gen.publish(
                "error" if final_status == "error" else "done",
                {"message": message, "error": error, "error_code": error_code, "error_params": error_params},
            )

        try:
            if final_status == "complete":
                title = await self._maybe_title(gen)
                if title:
                    gen.publish("title", {"conversation_id": gen.conversation_id, "title": title})
        except asyncio.CancelledError:
            pass
        except Exception:  # noqa: BLE001
            log.exception("title generation failed")
        finally:
            gen.finished = True
            gen.publish("end", {})
            self.active.pop(gen.id, None)

    async def _maybe_title(self, gen: Generation) -> str | None:
        conv = self.conversations.get(gen.conversation_id)
        if not conv or conv["title_source"] != "fallback" or not self.settings.get(gen.user["id"]).auto_title:
            return None
        # The first exchange of this branch (titles are generated once, not per branch).
        first = next((r for r in self.path_rows(gen) if r["role"] == "user"), None)
        if not first or not first["content"].strip():
            return None
        prompt = (
            "Write a short, specific title (3 to 6 words) for a conversation that starts with the message below. "
            "Reply with the title only — no quotes, no trailing punctuation.\n\n"
            f"Message:\n{first['content'][:2000]}"
        )
        provider = self.registry.provider(gen.user, gen.provider_id)
        request = ChatRequest(
            model=gen.model,
            messages=[ChatMessage(role="user", content=prompt)],
            params={"temperature": 0.3, "max_tokens": 48},
        )
        try:
            raw = await asyncio.wait_for(provider.complete(request), timeout=TITLE_TIMEOUT)
        except (ProviderError, asyncio.TimeoutError):
            return None
        title = clean_title(raw)
        if not title:
            return None
        # Only replace the fallback if the user has not renamed it meanwhile.
        cur = self.db.execute(
            "UPDATE conversations SET title = ?, title_source = 'model' WHERE id = ? AND title_source = 'fallback'",
            (title, gen.conversation_id),
        )
        return title if cur.rowcount else None


def clean_title(raw: str) -> str | None:
    import re

    text = re.sub(r"<think>.*?</think>", "", raw or "", flags=re.S).strip()
    line = next((ln.strip() for ln in text.splitlines() if ln.strip()), "")
    line = re.sub(r"^(title\s*:\s*)", "", line, flags=re.I)
    line = line.strip().strip("\"'`*#").strip().rstrip(".")
    if not line or len(line) > 90:
        return None
    return line
