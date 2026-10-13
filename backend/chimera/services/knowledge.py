"""Local knowledge base: chunking, indexing and hybrid retrieval.

* Keyword retrieval uses SQLite FTS5 (BM25) and always works offline with no
  extra services.
* Semantic retrieval is enabled when an embedding model is configured
  (Settings → Documents). Vectors are produced by any configured connection
  that exposes an embeddings endpoint (llama-server ``--embeddings``, Ollama,
  ...), stored as float32 blobs in SQLite and scored with cosine similarity.
* Results from both are merged with reciprocal-rank fusion.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any

import numpy as np

from ..db import Database, new_id, now_iso
from ..providers import ProviderError
from .providers import ProviderRegistry
from .settings import KnowledgeSettings

log = logging.getLogger("chimera.knowledge")

EMBED_BATCH = 16
SEPARATORS = ["\n\n", "\n", ". ", " "]


def chunk_text(text: str, size: int = 1000, overlap: int = 150) -> list[str]:
    text = text.strip()
    if not text:
        return []
    overlap = max(0, min(overlap, size // 2))
    pieces = _split(text, size, 0)
    chunks: list[str] = []
    current = ""
    for piece in pieces:
        if current and len(current) + len(piece) > size:
            chunks.append(current.strip())
            tail = current[-overlap:] if overlap else ""
            # Start the overlap at a word boundary when possible.
            if tail and " " in tail:
                tail = tail[tail.index(" ") + 1 :]
            current = tail + piece
        else:
            current += piece
    if current.strip():
        chunks.append(current.strip())
    return [c for c in chunks if c]


def _split(text: str, size: int, level: int) -> list[str]:
    if len(text) <= size:
        return [text]
    if level >= len(SEPARATORS):
        return [text[i : i + size] for i in range(0, len(text), size)]
    sep = SEPARATORS[level]
    parts = text.split(sep)
    out: list[str] = []
    for i, part in enumerate(parts):
        piece = part + (sep if i < len(parts) - 1 else "")
        if len(piece) > size:
            out.extend(_split(piece, size, level + 1))
        elif piece:
            out.append(piece)
    return out


def fts_query(query: str) -> str | None:
    tokens = [t for t in re.findall(r"\w+", query.lower()) if len(t) > 1][:32]
    if not tokens:
        return None
    return " OR ".join(f'"{t}"' for t in dict.fromkeys(tokens))


def to_blob(vector: list[float]) -> bytes:
    arr = np.asarray(vector, dtype=np.float32)
    norm = np.linalg.norm(arr)
    if norm > 0:
        arr = arr / norm
    return arr.tobytes()


@dataclass
class SearchHit:
    chunk_id: int
    document_id: str
    collection_id: str
    filename: str
    index: int
    text: str
    score: float
    method: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "chunk_id": self.chunk_id,
            "document_id": self.document_id,
            "collection_id": self.collection_id,
            "filename": self.filename,
            "index": self.index,
            "text": self.text,
            "score": round(self.score, 4),
            "method": self.method,
        }


class KnowledgeBase:
    def __init__(self, db: Database, registry: ProviderRegistry):
        self.db = db
        self.registry = registry

    # -- collections ----------------------------------------------------------
    def collections(self) -> list[dict]:
        return self.db.all(
            """SELECT c.*, COUNT(d.id) AS document_count, COALESCE(SUM(d.chunk_count), 0) AS chunk_count
               FROM collections c LEFT JOIN documents d ON d.collection_id = c.id
               GROUP BY c.id ORDER BY c.updated_at DESC"""
        )

    def collection(self, cid: str) -> dict | None:
        return self.db.one(
            """SELECT c.*, COUNT(d.id) AS document_count, COALESCE(SUM(d.chunk_count), 0) AS chunk_count
               FROM collections c LEFT JOIN documents d ON d.collection_id = c.id
               WHERE c.id = ? GROUP BY c.id""",
            (cid,),
        )

    def create_collection(self, name: str, description: str = "") -> dict:
        cid, ts = new_id(), now_iso()
        self.db.execute(
            "INSERT INTO collections(id, name, description, created_at, updated_at) VALUES (?,?,?,?,?)",
            (cid, name, description, ts, ts),
        )
        return self.collection(cid)  # type: ignore[return-value]

    def update_collection(self, cid: str, data: dict) -> dict | None:
        fields = {k: v for k, v in data.items() if k in ("name", "description") and v is not None}
        if fields:
            sets = ", ".join(f"{k} = ?" for k in fields)
            self.db.execute(
                f"UPDATE collections SET {sets}, updated_at = ? WHERE id = ?", (*fields.values(), now_iso(), cid)
            )
        return self.collection(cid)

    def delete_collection(self, cid: str) -> bool:
        with self.db.transaction() as conn:
            conn.execute("DELETE FROM chunks WHERE collection_id = ?", (cid,))
            cur = conn.execute("DELETE FROM collections WHERE id = ?", (cid,))
        return cur.rowcount > 0

    def documents(self, cid: str) -> list[dict]:
        return self.db.all(
            "SELECT * FROM documents WHERE collection_id = ? ORDER BY created_at DESC", (cid,)
        )

    def delete_document(self, doc_id: str) -> bool:
        with self.db.transaction() as conn:
            conn.execute("DELETE FROM chunks WHERE document_id = ?", (doc_id,))
            cur = conn.execute("DELETE FROM documents WHERE id = ?", (doc_id,))
        return cur.rowcount > 0

    # -- ingestion ------------------------------------------------------------
    async def add_document(
        self, cid: str, filename: str, mime: str, size: int, text: str, settings: KnowledgeSettings
    ) -> dict:
        doc_id, ts = new_id(), now_iso()
        chunks = chunk_text(text, settings.chunk_size, settings.chunk_overlap)
        with self.db.transaction() as conn:
            conn.execute(
                """INSERT INTO documents(id, collection_id, filename, mime, size, status, chunk_count, char_count,
                       created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?)""",
                (doc_id, cid, filename, mime, size, "ready", len(chunks), len(text), ts, ts),
            )
            conn.executemany(
                "INSERT INTO chunks(document_id, collection_id, idx, text) VALUES (?,?,?,?)",
                [(doc_id, cid, i, c) for i, c in enumerate(chunks)],
            )
            conn.execute("UPDATE collections SET updated_at = ? WHERE id = ?", (ts, cid))
        await self.embed_document(doc_id, settings)
        return self.db.one("SELECT * FROM documents WHERE id = ?", (doc_id,))  # type: ignore[return-value]

    async def embed_document(self, doc_id: str, settings: KnowledgeSettings) -> None:
        """Compute embeddings when configured; failures degrade to keyword search."""
        if not (settings.embedding_provider_id and settings.embedding_model):
            self.db.execute(
                "UPDATE documents SET embedding_model = NULL, warning = NULL WHERE id = ?", (doc_id,)
            )
            return
        rows = self.db.all("SELECT id, text FROM chunks WHERE document_id = ? ORDER BY idx", (doc_id,))
        try:
            provider = self.registry.provider(settings.embedding_provider_id)
            for start in range(0, len(rows), EMBED_BATCH):
                batch = rows[start : start + EMBED_BATCH]
                vectors = await provider.embed(settings.embedding_model, [r["text"] for r in batch])
                with self.db.transaction() as conn:
                    conn.executemany(
                        "UPDATE chunks SET embedding = ? WHERE id = ?",
                        [(to_blob(v), r["id"]) for v, r in zip(vectors, batch)],
                    )
            self.db.execute(
                "UPDATE documents SET embedding_model = ?, warning = NULL, updated_at = ? WHERE id = ?",
                (settings.embedding_model, now_iso(), doc_id),
            )
        except ProviderError as exc:
            log.warning("embedding failed for document %s: %s", doc_id, exc.message)
            self.db.execute("UPDATE chunks SET embedding = NULL WHERE document_id = ?", (doc_id,))
            self.db.execute(
                "UPDATE documents SET embedding_model = NULL, warning = ?, updated_at = ? WHERE id = ?",
                (f"Embeddings unavailable ({exc.message}). Keyword search only.", now_iso(), doc_id),
            )

    # -- retrieval --------------------------------------------------------------
    def _keyword(self, collection_ids: list[str], query: str, limit: int) -> list[SearchHit]:
        match = fts_query(query)
        if not match or not collection_ids:
            return []
        marks = ",".join("?" * len(collection_ids))
        rows = self.db.all(
            f"""SELECT c.id, c.document_id, c.collection_id, c.idx, c.text, d.filename, bm25(chunks_fts) AS rank
                FROM chunks_fts JOIN chunks c ON c.id = chunks_fts.rowid
                JOIN documents d ON d.id = c.document_id
                WHERE chunks_fts MATCH ? AND c.collection_id IN ({marks})
                ORDER BY rank LIMIT ?""",
            (match, *collection_ids, limit),
        )
        return [
            SearchHit(r["id"], r["document_id"], r["collection_id"], r["filename"], r["idx"], r["text"], -r["rank"], "keyword")
            for r in rows
        ]

    async def _semantic(
        self, collection_ids: list[str], query: str, limit: int, settings: KnowledgeSettings
    ) -> list[SearchHit]:
        if not (settings.embedding_provider_id and settings.embedding_model and collection_ids):
            return []
        marks = ",".join("?" * len(collection_ids))
        rows = self.db.all(
            f"""SELECT c.id, c.document_id, c.collection_id, c.idx, c.text, c.embedding, d.filename
                FROM chunks c JOIN documents d ON d.id = c.document_id
                WHERE c.collection_id IN ({marks}) AND c.embedding IS NOT NULL AND d.embedding_model = ?""",
            (*collection_ids, settings.embedding_model),
        )
        if not rows:
            return []
        provider = self.registry.provider(settings.embedding_provider_id)
        qvec = np.frombuffer(to_blob((await provider.embed(settings.embedding_model, [query]))[0]), dtype=np.float32)
        dims = qvec.shape[0]
        valid = [r for r in rows if len(r["embedding"]) == dims * 4]
        if not valid:
            return []
        matrix = np.frombuffer(b"".join(r["embedding"] for r in valid), dtype=np.float32).reshape(len(valid), dims)
        scores = matrix @ qvec
        order = np.argsort(-scores)[:limit]
        return [
            SearchHit(
                valid[i]["id"], valid[i]["document_id"], valid[i]["collection_id"], valid[i]["filename"],
                valid[i]["idx"], valid[i]["text"], float(scores[i]), "semantic",
            )
            for i in order
        ]

    async def search(
        self, collection_ids: list[str], query: str, settings: KnowledgeSettings, top_k: int | None = None
    ) -> list[SearchHit]:
        top_k = top_k or settings.top_k
        pool = max(top_k * 4, 20)
        keyword = self._keyword(collection_ids, query, pool)
        try:
            semantic = await self._semantic(collection_ids, query, pool, settings)
        except ProviderError as exc:
            log.warning("semantic search unavailable: %s", exc.message)
            semantic = []
        if not semantic:
            return keyword[:top_k]
        fused: dict[int, tuple[float, SearchHit]] = {}
        for ranked in (semantic, keyword):
            for rank, hit in enumerate(ranked):
                score = 1.0 / (60 + rank)
                prev = fused.get(hit.chunk_id)
                fused[hit.chunk_id] = (score + (prev[0] if prev else 0.0), prev[1] if prev else hit)
        ordered = sorted(fused.values(), key=lambda item: -item[0])[:top_k]
        out = []
        for score, hit in ordered:
            methods = {h.method for h in (semantic + keyword) if h.chunk_id == hit.chunk_id}
            hit.method = "hybrid" if len(methods) > 1 else hit.method
            hit.score = score
            out.append(hit)
        return out
