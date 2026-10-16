"""Conversation and message persistence."""

from __future__ import annotations

from typing import Any

from ..db import Database, dumps, loads, new_id, now_iso

FALLBACK_TITLE_LENGTH = 60


def fallback_title(text: str) -> str:
    line = next((ln.strip() for ln in text.splitlines() if ln.strip()), "").lstrip("#>*- ").strip()
    if not line:
        return "New conversation"
    if len(line) <= FALLBACK_TITLE_LENGTH:
        return line
    cut = line[:FALLBACK_TITLE_LENGTH].rsplit(" ", 1)[0]
    return (cut or line[:FALLBACK_TITLE_LENGTH]).rstrip(",.;:") + "…"


def conversation_out(row: dict) -> dict[str, Any]:
    return {
        "id": row["id"],
        "title": row["title"],
        "title_source": row["title_source"],
        "provider_id": row["provider_id"],
        "model": row["model"],
        "system_prompt": row["system_prompt"],
        "params": loads(row["params"], {}),
        "collection_ids": loads(row["collection_ids"], []),
        "pinned": bool(row["pinned"]),
        "active_leaf_id": row.get("active_leaf_id"),
        "context_strategy": row.get("context_strategy"),
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def message_out(row: dict) -> dict[str, Any]:
    return {
        "id": row["id"],
        "conversation_id": row["conversation_id"],
        "parent_id": row.get("parent_id"),
        "position": row["position"],
        "role": row["role"],
        "content": row["content"],
        "reasoning": row["reasoning"],
        "status": row["status"],
        "error": row["error"],
        "error_code": row.get("error_code"),
        "error_params": loads(row.get("error_params"), {}),
        "provider_id": row["provider_id"],
        "model": row["model"],
        "attachments": loads(row["attachments"], []),
        "sources": loads(row["sources"], []),
        "metrics": loads(row["metrics"], {}),
        "web": loads(row.get("web"), {}),
        "pinned": bool(row.get("pinned")),
        "context": loads(row.get("context"), {}),
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


class ConversationStore:
    def __init__(self, db: Database):
        self.db = db

    def list(self, user_id: str, limit: int = 500) -> list[dict]:
        rows = self.db.all(
            "SELECT * FROM conversations WHERE user_id = ? ORDER BY pinned DESC, updated_at DESC LIMIT ?", (user_id, limit)
        )
        return [conversation_out(r) for r in rows]

    def get(self, cid: str) -> dict | None:
        """Unscoped lookup for internal use (the generation engine)."""
        return self.db.one("SELECT * FROM conversations WHERE id = ?", (cid,))

    def owned(self, user_id: str, cid: str) -> dict | None:
        return self.db.one("SELECT * FROM conversations WHERE id = ? AND user_id = ?", (cid, user_id))

    def create(self, user_id: str, data: dict[str, Any]) -> dict:
        cid, ts = new_id(), now_iso()
        self.db.execute(
            """INSERT INTO conversations(id, user_id, title, provider_id, model, system_prompt, params, collection_ids,
                   created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (
                cid,
                user_id,
                data.get("title") or "New conversation",
                data.get("provider_id"),
                data.get("model"),
                data.get("system_prompt"),
                dumps(data.get("params") or {}),
                dumps(data.get("collection_ids") or []),
                ts,
                ts,
            ),
        )
        if data.get("title"):
            self.db.execute("UPDATE conversations SET title_source = 'user' WHERE id = ?", (cid,))
        return self.get(cid)  # type: ignore[return-value]

    def update(self, cid: str, data: dict[str, Any], touch: bool = False) -> dict | None:
        sets: list[str] = []
        values: list[Any] = []
        for key in ("title", "provider_id", "model", "system_prompt", "pinned", "params", "collection_ids"):
            if key not in data:
                continue
            value = data[key]
            if key in ("params", "collection_ids"):
                value = dumps(value or ({} if key == "params" else []))
            if key == "pinned":
                value = 1 if value else 0
            sets.append(f"{key} = ?")
            values.append(value)
            if key == "title":
                sets.append("title_source = ?")
                values.append(data.get("title_source", "user"))
        if touch:
            sets.append("updated_at = ?")
            values.append(now_iso())
        if sets:
            self.db.execute(f"UPDATE conversations SET {', '.join(sets)} WHERE id = ?", (*values, cid))
        return self.get(cid)

    def delete(self, user_id: str, cid: str) -> bool:
        return self.db.execute("DELETE FROM conversations WHERE id = ? AND user_id = ?", (cid, user_id)).rowcount > 0

    def delete_all(self, user_id: str) -> int:
        return self.db.execute("DELETE FROM conversations WHERE user_id = ?", (user_id,)).rowcount

    def messages(self, cid: str) -> list[dict]:
        """All messages of every branch (the client shows the active path)."""
        rows = self.db.all("SELECT * FROM messages WHERE conversation_id = ? ORDER BY position, created_at, rowid", (cid,))
        return [message_out(r) for r in rows]

    def message(self, mid: str) -> dict | None:
        return self.db.one("SELECT * FROM messages WHERE id = ?", (mid,))

    def owned_message(self, user_id: str, mid: str) -> dict | None:
        return self.db.one(
            "SELECT m.* FROM messages m JOIN conversations c ON c.id = m.conversation_id WHERE m.id = ? AND c.user_id = ?",
            (mid, user_id),
        )

    def search(self, user_id: str, query: str, limit: int = 50) -> list[dict]:
        q = query.strip()
        if not q:
            return []
        pattern = "%" + q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        rows = self.db.all(
            """SELECT c.*, (
                   SELECT m.content FROM messages m
                   WHERE m.conversation_id = c.id AND m.content LIKE ? ESCAPE '\\'
                   ORDER BY m.position, m.created_at LIMIT 1
               ) AS match_text, (
                   SELECT m.id FROM messages m
                   WHERE m.conversation_id = c.id AND m.content LIKE ? ESCAPE '\\'
                   ORDER BY m.position, m.created_at LIMIT 1
               ) AS match_id
               FROM conversations c
               WHERE c.user_id = ? AND (c.title LIKE ? ESCAPE '\\'
                  OR EXISTS (SELECT 1 FROM messages m WHERE m.conversation_id = c.id AND m.content LIKE ? ESCAPE '\\'))
               ORDER BY c.updated_at DESC LIMIT ?""",
            (pattern, pattern, user_id, pattern, pattern, limit),
        )
        out = []
        for row in rows:
            item = conversation_out(row)
            item["snippet"] = _snippet(row.get("match_text") or "", q)
            # Opening a hit activates the branch that contains it (any branch is searched).
            item["match_message_id"] = row.get("match_id")
            out.append(item)
        return out


def _snippet(text: str, query: str, radius: int = 70) -> str:
    if not text:
        return ""
    idx = text.lower().find(query.lower())
    if idx < 0:
        return text[: radius * 2].replace("\n", " ")
    start = max(0, idx - radius)
    end = min(len(text), idx + len(query) + radius)
    snippet = text[start:end].replace("\n", " ")
    return ("…" if start > 0 else "") + snippet + ("…" if end < len(text) else "")
