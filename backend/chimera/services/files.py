"""Attachment storage. Text lives in SQLite; image bytes in the uploads dir
under random, extension-less names (never a user-controlled path)."""

from __future__ import annotations

import base64
import re
import shutil
from pathlib import Path
from typing import Any

from ..db import Database, new_id, now_iso
from .documents import Extracted

_HEX = re.compile(r"[0-9a-f]{32}")


def file_out(row: dict, include_text: bool = False) -> dict[str, Any]:
    out = {
        "id": row["id"],
        "filename": row["filename"],
        "mime": row["mime"],
        "size": row["size"],
        "kind": row["kind"],
        "truncated": bool(row["truncated"]),
        "char_count": len(row["text"] or ""),
        "created_at": row["created_at"],
    }
    if include_text:
        out["text"] = row["text"]
    return out


class FileStore:
    def __init__(self, db: Database, uploads_dir: Path):
        self.db = db
        self.uploads_dir = uploads_dir

    def save(self, user_id: str, filename: str, size: int, extracted: Extracted, data: bytes) -> dict:
        fid = new_id()
        storage_name = None
        if extracted.kind == "image":
            user_dir = self.uploads_dir / user_id
            user_dir.mkdir(parents=True, exist_ok=True)
            storage_name = f"{user_id}/{fid}"
            (user_dir / fid).write_bytes(data)
        self.db.execute(
            """INSERT INTO files(id, user_id, filename, mime, size, kind, text, truncated, storage_name, created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (fid, user_id, filename, extracted.mime, size, extracted.kind, extracted.text, int(extracted.truncated), storage_name, now_iso()),
        )
        return self.get(fid)  # type: ignore[return-value]

    def get(self, fid: str) -> dict | None:
        return self.db.one("SELECT * FROM files WHERE id = ?", (fid,))

    def owned(self, user_id: str, fid: str) -> dict | None:
        return self.db.one("SELECT * FROM files WHERE id = ? AND user_id = ?", (fid, user_id))

    def path(self, row: dict) -> Path | None:
        """Resolve a stored file, refusing anything outside the owner's directory."""
        name = row.get("storage_name") or ""
        parts = name.split("/")
        if len(parts) != 2 or parts[0] != row.get("user_id") or not all(_HEX.fullmatch(p) for p in parts):
            return None
        root = (self.uploads_dir / parts[0]).resolve()
        path = (root / parts[1]).resolve()
        if path.parent != root or not path.is_file():
            return None
        return path

    def delete_user_files(self, user_id: str) -> None:
        if _HEX.fullmatch(user_id):
            shutil.rmtree(self.uploads_dir / user_id, ignore_errors=True)

    def image_base64(self, row: dict) -> str | None:
        path = self.path(row)
        return base64.b64encode(path.read_bytes()).decode("ascii") if path else None

    def delete(self, user_id: str, fid: str) -> bool:
        row = self.owned(user_id, fid)
        if not row:
            return False
        path = self.path(row)
        if path:
            path.unlink(missing_ok=True)
        self.db.execute("DELETE FROM files WHERE id = ?", (fid,))
        return True
