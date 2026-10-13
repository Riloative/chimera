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
import time
from dataclasses import dataclass, field
from typing import Any, AsyncIterator

from ..db import Database, dumps, loads, new_id, now_iso
from ..errors import AppError
from ..providers import ChatMessage, ChatRequest, ProviderError
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
    ):
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

    def _next_position(self, conversation_id: str) -> int:
        row = self.db.one("SELECT COALESCE(MAX(position), -1) + 1 AS p FROM messages WHERE conversation_id = ?", (conversation_id,))
        return int(row["p"]) if row else 0

    def _insert_message(self, conn, conversation_id: str, position: int, role: str, content: str, **extra) -> str:
        mid, ts = new_id(), now_iso()
        conn.execute(
            """INSERT INTO messages(id, conversation_id, position, role, content, status, provider_id, model,
                   attachments, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (
                mid, conversation_id, position, role, content, extra.get("status", "complete"),
                extra.get("provider_id"), extra.get("model"), dumps(extra.get("attachments") or []), ts, ts,
            ),
        )
        return mid

    def _start(self, user: dict, conversation_id: str, message_id: str, pid: str, model: str, params: dict) -> Generation:
        gen = Generation(id=new_id(), user=user, conversation_id=conversation_id, message_id=message_id, provider_id=pid, model=model, params=params)
        self.active[gen.id] = gen
        self.by_conversation[conversation_id] = gen.id
        return gen

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
    ) -> tuple[Generation, dict]:
        conv = self.conversations.owned(user["id"], conversation_id)
        if not conv:
            raise ChatError("Conversation not found.", status=404, code="conversation_not_found")
        self._ensure_idle(conversation_id)
        if not content.strip() and not attachment_ids:
            raise ChatError("Message is empty.", code="empty_message")
        pid, mdl = self._resolve_target(user, conv, provider_id, model)
        attachments = self._attachment_snapshots(user, attachment_ids, pid, mdl)
        position = self._next_position(conversation_id)
        first = position == 0
        with self.db.transaction() as conn:
            user_id = self._insert_message(conn, conversation_id, position, "user", content, attachments=attachments)
            asst_id = self._insert_message(
                conn, conversation_id, position + 1, "assistant", "", status="streaming", provider_id=pid, model=mdl
            )
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
        gen = self._start(user, conversation_id, asst_id, pid, mdl, self._params(user, conv, params))
        return gen, {"user_message": message_out(self.conversations.message(user_id))}  # type: ignore[arg-type]

    def prepare_regenerate(
        self, user: dict, conversation_id: str, message_id: str | None, provider_id: str | None, model: str | None, params: dict | None
    ) -> tuple[Generation, dict]:
        conv = self.conversations.owned(user["id"], conversation_id)
        if not conv:
            raise ChatError("Conversation not found.", status=404, code="conversation_not_found")
        self._ensure_idle(conversation_id)
        if message_id:
            target = self.conversations.message(message_id)
            if not target or target["conversation_id"] != conversation_id or target["role"] != "assistant":
                raise ChatError("Message not found.", status=404, code="message_not_found")
            position = target["position"]
        else:
            last = self.db.one(
                "SELECT * FROM messages WHERE conversation_id = ? ORDER BY position DESC LIMIT 1", (conversation_id,)
            )
            if not last:
                raise ChatError("Nothing to regenerate.", code="nothing_to_regenerate")
            position = last["position"] if last["role"] == "assistant" else last["position"] + 1
        prior_user = self.db.one(
            "SELECT id FROM messages WHERE conversation_id = ? AND position < ? AND role = 'user' LIMIT 1",
            (conversation_id, position),
        )
        if not prior_user:
            raise ChatError("Nothing to regenerate.", code="nothing_to_regenerate")
        pid, mdl = self._resolve_target(user, conv, provider_id, model)
        with self.db.transaction() as conn:
            conn.execute("DELETE FROM messages WHERE conversation_id = ? AND position >= ?", (conversation_id, position))
            asst_id = self._insert_message(
                conn, conversation_id, position, "assistant", "", status="streaming", provider_id=pid, model=mdl
            )
            conn.execute(
                "UPDATE conversations SET provider_id = ?, model = ?, updated_at = ? WHERE id = ?",
                (pid, mdl, now_iso(), conversation_id),
            )
        gen = self._start(user, conversation_id, asst_id, pid, mdl, self._params(user, conv, params))
        return gen, {}

    def prepare_edit(
        self, user: dict, conversation_id: str, message_id: str, content: str, provider_id: str | None, model: str | None, params: dict | None
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
        with self.db.transaction() as conn:
            conn.execute(
                "UPDATE messages SET content = ?, updated_at = ? WHERE id = ?", (content, now_iso(), message_id)
            )
            conn.execute(
                "DELETE FROM messages WHERE conversation_id = ? AND position > ?", (conversation_id, target["position"])
            )
            asst_id = self._insert_message(
                conn, conversation_id, target["position"] + 1, "assistant", "", status="streaming", provider_id=pid, model=mdl
            )
            conn.execute(
                "UPDATE conversations SET provider_id = ?, model = ?, updated_at = ? WHERE id = ?",
                (pid, mdl, now_iso(), conversation_id),
            )
        gen = self._start(user, conversation_id, asst_id, pid, mdl, self._params(user, conv, params))
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
        return "snapshot", {"generation_id": gen.id, "message": message}

    # -- the generation task ----------------------------------------------------
    def _persist(self, gen: Generation, **fields: Any) -> None:
        fields.setdefault("content", gen.content)
        fields.setdefault("reasoning", gen.reasoning)
        sets = ", ".join(f"{k} = ?" for k in fields)
        values = [dumps(v) if isinstance(v, (dict, list)) else v for v in fields.values()]
        self.db.execute(f"UPDATE messages SET {sets}, updated_at = ? WHERE id = ?", (*values, now_iso(), gen.message_id))

    async def build_request(self, gen: Generation) -> tuple[ChatRequest, list[dict]]:
        conv = self.conversations.get(gen.conversation_id) or {}
        settings = self.settings.get(gen.user["id"])
        rows = self.db.all(
            "SELECT * FROM messages WHERE conversation_id = ? AND position < (SELECT position FROM messages WHERE id = ?) ORDER BY position",
            (gen.conversation_id, gen.message_id),
        )
        vision = self.vision_supported(gen.provider_id, gen.model)
        messages: list[ChatMessage] = []
        for row in rows:
            if row["role"] == "assistant":
                if row["status"] == "error" or not row["content"].strip():
                    continue
                messages.append(ChatMessage(role="assistant", content=row["content"]))
            elif row["role"] == "user":
                messages.append(self._user_message(gen.user["id"], row, vision))
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
        if system:
            messages.insert(0, ChatMessage(role="system", content=system))
        return ChatRequest(model=gen.model, messages=messages, params=gen.params), sources

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
        try:
            request, sources = await self.build_request(gen)
            if sources:
                self._persist(gen, sources=sources)
                gen.publish("sources", {"sources": sources})
            provider = self.registry.provider(gen.user, gen.provider_id)
            stream = self.settings.get(gen.user["id"]).stream
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
            if not stream and (gen.content or gen.reasoning):
                gen.publish("delta", {"content": gen.content, "reasoning": gen.reasoning})
            self.registry.mark_ok(gen.provider_id)
        except asyncio.CancelledError:
            final_status = "cancelled"
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
            self._persist(gen, status=final_status, error=error, error_code=error_code, error_params=error_params, metrics=metrics)
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
        first = self.db.one(
            "SELECT content FROM messages WHERE conversation_id = ? AND role = 'user' ORDER BY position LIMIT 1",
            (gen.conversation_id,),
        )
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
