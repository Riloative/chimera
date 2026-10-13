"""Attachment storage. Text lives in SQLite; image bytes in the uploads dir
under random, extension-less names (never a user-controlled path)."""

from __future__ import annotations

import base64
from pathlib import Path
from typing import Any

from ..db import Database, new_id, now_iso
from .documents import Extracted


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

    def save(self, filename: str, size: int, extracted: Extracted, data: bytes) -> dict:
        fid = new_id()
        storage_name = None
        if extracted.kind == "image":
            self.uploads_dir.mkdir(parents=True, exist_ok=True)
            storage_name = fid
            (self.uploads_dir / storage_name).write_bytes(data)
        self.db.execute(
            """INSERT INTO files(id, filename, mime, size, kind, text, truncated, storage_name, created_at)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (fid, filename, extracted.mime, size, extracted.kind, extracted.text, int(extracted.truncated), storage_name, now_iso()),
        )
        return self.get(fid)  # type: ignore[return-value]

    def get(self, fid: str) -> dict | None:
        return self.db.one("SELECT * FROM files WHERE id = ?", (fid,))

    def path(self, row: dict) -> Path | None:
        name = row.get("storage_name")
        if not name or "/" in name or "\\" in name or ".." in name:
            return None
        path = (self.uploads_dir / name).resolve()
        if path.parent != self.uploads_dir.resolve() or not path.is_file():
            return None
        return path

    def image_base64(self, row: dict) -> str | None:
        path = self.path(row)
        return base64.b64encode(path.read_bytes()).decode("ascii") if path else None

    def delete(self, fid: str) -> bool:
        row = self.get(fid)
        if not row:
            return False
        path = self.path(row)
        if path:
            path.unlink(missing_ok=True)
        self.db.execute("DELETE FROM files WHERE id = ?", (fid,))
        return True
