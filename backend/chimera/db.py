"""SQLite persistence with forward-only, numbered migrations.

A single connection guarded by a lock is plenty for a single-user, self-hosted
app: queries are tiny and SQLite in WAL mode handles them in microseconds.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterator


def _migrate_tree(conn: sqlite3.Connection) -> None:
    """4 — message tree (branching), active branch, pins, context management.

    Every existing conversation becomes a single chain: each message's parent
    is the previous message by (position, created_at), ``position`` becomes the
    depth, and the last message is the active leaf. Contents and timestamps are
    untouched. Additive: an older Chimera ignores the new columns (and would
    show all branches of a conversation in creation order)."""
    for statement in (
        "ALTER TABLE messages ADD COLUMN parent_id TEXT",
        "ALTER TABLE messages ADD COLUMN active_child_id TEXT",
        "ALTER TABLE messages ADD COLUMN pinned INTEGER NOT NULL DEFAULT 0",
        "ALTER TABLE messages ADD COLUMN context TEXT NOT NULL DEFAULT '{}'",
        "ALTER TABLE conversations ADD COLUMN active_leaf_id TEXT",
        "ALTER TABLE conversations ADD COLUMN context_strategy TEXT",
        "ALTER TABLE conversations ADD COLUMN context_state TEXT NOT NULL DEFAULT '{}'",
        "ALTER TABLE providers ADD COLUMN context_override INTEGER",
        "ALTER TABLE web_sources ADD COLUMN loader TEXT NOT NULL DEFAULT 'static'",
        "CREATE INDEX idx_messages_parent ON messages(conversation_id, parent_id)",
        """CREATE TABLE context_summaries (
            id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
            covers_hash TEXT NOT NULL,
            last_message_id TEXT NOT NULL,
            message_count INTEGER NOT NULL,
            content TEXT NOT NULL,
            model TEXT,
            created_at TEXT NOT NULL
        )""",
        "CREATE INDEX idx_context_summaries_conv ON context_summaries(conversation_id, covers_hash)",
    ):
        conn.execute(statement)
    conversations = [r[0] for r in conn.execute("SELECT id FROM conversations")]
    for cid in conversations:
        rows = conn.execute(
            "SELECT id FROM messages WHERE conversation_id = ? ORDER BY position, created_at, rowid", (cid,)
        ).fetchall()
        previous = None
        for depth, (mid,) in enumerate(rows):
            conn.execute("UPDATE messages SET parent_id = ?, position = ? WHERE id = ?", (previous, depth, mid))
            if previous:
                conn.execute("UPDATE messages SET active_child_id = ? WHERE id = ?", (mid, previous))
            previous = mid
        conn.execute("UPDATE conversations SET active_leaf_id = ? WHERE id = ?", (previous, cid))


MIGRATIONS: list[str | Callable[[sqlite3.Connection], None]] = [
    # 1 — initial schema
    """
    CREATE TABLE providers (
        id TEXT PRIMARY KEY,
        name TEXT NOT NULL,
        kind TEXT NOT NULL,
        base_url TEXT NOT NULL,
        api_path TEXT NOT NULL DEFAULT '/v1',
        api_key TEXT,
        auth_header TEXT,
        timeout REAL NOT NULL DEFAULT 300,
        enabled INTEGER NOT NULL DEFAULT 1,
        vision TEXT NOT NULL DEFAULT 'auto',
        manual_models TEXT NOT NULL DEFAULT '[]',
        cached_models TEXT NOT NULL DEFAULT '[]',
        models_fetched_at TEXT,
        status TEXT NOT NULL DEFAULT 'unknown',
        last_error TEXT,
        latency_ms REAL,
        checked_at TEXT,
        capabilities TEXT NOT NULL DEFAULT '{}',
        position INTEGER NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    );

    CREATE TABLE conversations (
        id TEXT PRIMARY KEY,
        title TEXT NOT NULL DEFAULT 'New conversation',
        title_source TEXT NOT NULL DEFAULT 'default',
        provider_id TEXT,
        model TEXT,
        system_prompt TEXT,
        params TEXT NOT NULL DEFAULT '{}',
        collection_ids TEXT NOT NULL DEFAULT '[]',
        pinned INTEGER NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    );
    CREATE INDEX idx_conversations_updated ON conversations(updated_at DESC);

    CREATE TABLE messages (
        id TEXT PRIMARY KEY,
        conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
        position INTEGER NOT NULL,
        role TEXT NOT NULL,
        content TEXT NOT NULL DEFAULT '',
        reasoning TEXT NOT NULL DEFAULT '',
        status TEXT NOT NULL DEFAULT 'complete',
        error TEXT,
        provider_id TEXT,
        model TEXT,
        attachments TEXT NOT NULL DEFAULT '[]',
        sources TEXT NOT NULL DEFAULT '[]',
        metrics TEXT NOT NULL DEFAULT '{}',
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    );
    CREATE INDEX idx_messages_conversation ON messages(conversation_id, position);

    CREATE TABLE files (
        id TEXT PRIMARY KEY,
        filename TEXT NOT NULL,
        mime TEXT NOT NULL,
        size INTEGER NOT NULL,
        kind TEXT NOT NULL,
        text TEXT,
        truncated INTEGER NOT NULL DEFAULT 0,
        storage_name TEXT,
        created_at TEXT NOT NULL
    );

    CREATE TABLE prompts (
        id TEXT PRIMARY KEY,
        title TEXT NOT NULL,
        description TEXT NOT NULL DEFAULT '',
        category TEXT NOT NULL DEFAULT '',
        content TEXT NOT NULL,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    );

    CREATE TABLE settings (
        key TEXT PRIMARY KEY,
        value TEXT NOT NULL
    );

    CREATE TABLE collections (
        id TEXT PRIMARY KEY,
        name TEXT NOT NULL,
        description TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    );

    CREATE TABLE documents (
        id TEXT PRIMARY KEY,
        collection_id TEXT NOT NULL REFERENCES collections(id) ON DELETE CASCADE,
        filename TEXT NOT NULL,
        mime TEXT NOT NULL,
        size INTEGER NOT NULL,
        status TEXT NOT NULL DEFAULT 'ready',
        error TEXT,
        warning TEXT,
        chunk_count INTEGER NOT NULL DEFAULT 0,
        char_count INTEGER NOT NULL DEFAULT 0,
        embedding_model TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    );

    CREATE TABLE chunks (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        document_id TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
        collection_id TEXT NOT NULL,
        idx INTEGER NOT NULL,
        text TEXT NOT NULL,
        embedding BLOB
    );
    CREATE INDEX idx_chunks_collection ON chunks(collection_id);
    CREATE INDEX idx_chunks_document ON chunks(document_id);

    CREATE VIRTUAL TABLE chunks_fts USING fts5(
        text, content='chunks', content_rowid='id', tokenize='porter unicode61'
    );
    CREATE TRIGGER chunks_ai AFTER INSERT ON chunks BEGIN
        INSERT INTO chunks_fts(rowid, text) VALUES (new.id, new.text);
    END;
    CREATE TRIGGER chunks_ad AFTER DELETE ON chunks BEGIN
        INSERT INTO chunks_fts(chunks_fts, rowid, text) VALUES ('delete', old.id, old.text);
    END;
    CREATE TRIGGER chunks_au AFTER UPDATE OF text ON chunks BEGIN
        INSERT INTO chunks_fts(chunks_fts, rowid, text) VALUES ('delete', old.id, old.text);
        INSERT INTO chunks_fts(rowid, text) VALUES (new.id, new.text);
    END;
    """,
    # 2 — user accounts, sessions, ownership, per-user settings, error codes
    """
    CREATE TABLE users (
        id TEXT PRIMARY KEY,
        email TEXT NOT NULL,
        email_norm TEXT NOT NULL UNIQUE,
        username TEXT NOT NULL,
        username_norm TEXT NOT NULL UNIQUE,
        password_hash TEXT NOT NULL,
        role TEXT NOT NULL DEFAULT 'user',
        language TEXT,
        active INTEGER NOT NULL DEFAULT 1,
        must_change_password INTEGER NOT NULL DEFAULT 0,
        last_login_at TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    );

    CREATE TABLE sessions (
        id TEXT PRIMARY KEY,
        user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        token_hash TEXT NOT NULL UNIQUE,
        csrf_hash TEXT NOT NULL,
        remember INTEGER NOT NULL DEFAULT 0,
        user_agent TEXT,
        ip TEXT,
        created_at TEXT NOT NULL,
        last_used_at TEXT NOT NULL,
        expires_at TEXT NOT NULL
    );
    CREATE INDEX idx_sessions_user ON sessions(user_id);

    CREATE TABLE auth_throttle (
        key TEXT PRIMARY KEY,
        failures INTEGER NOT NULL DEFAULT 0,
        window_start REAL NOT NULL,
        locked_until REAL NOT NULL DEFAULT 0
    );

    CREATE TABLE user_settings (
        user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        key TEXT NOT NULL,
        value TEXT NOT NULL,
        PRIMARY KEY (user_id, key)
    );

    CREATE TABLE app_settings (
        key TEXT PRIMARY KEY,
        value TEXT NOT NULL
    );
    INSERT INTO app_settings(key, value) SELECT key, value FROM settings WHERE key LIKE '!_%' ESCAPE '!';
    DELETE FROM settings WHERE key LIKE '!_%' ESCAPE '!';

    ALTER TABLE conversations ADD COLUMN user_id TEXT REFERENCES users(id) ON DELETE CASCADE;
    ALTER TABLE prompts ADD COLUMN user_id TEXT REFERENCES users(id) ON DELETE CASCADE;
    ALTER TABLE files ADD COLUMN user_id TEXT REFERENCES users(id) ON DELETE CASCADE;
    ALTER TABLE collections ADD COLUMN user_id TEXT REFERENCES users(id) ON DELETE CASCADE;
    ALTER TABLE providers ADD COLUMN user_id TEXT REFERENCES users(id) ON DELETE CASCADE;
    ALTER TABLE providers ADD COLUMN shared INTEGER NOT NULL DEFAULT 0;
    ALTER TABLE providers ADD COLUMN last_error_code TEXT;
    ALTER TABLE providers ADD COLUMN last_error_params TEXT NOT NULL DEFAULT '{}';
    ALTER TABLE messages ADD COLUMN error_code TEXT;
    ALTER TABLE messages ADD COLUMN error_params TEXT NOT NULL DEFAULT '{}';
    ALTER TABLE documents ADD COLUMN warning_code TEXT;

    CREATE INDEX idx_conversations_user ON conversations(user_id, updated_at DESC);
    CREATE INDEX idx_prompts_user ON prompts(user_id);
    CREATE INDEX idx_files_user ON files(user_id);
    CREATE INDEX idx_collections_user ON collections(user_id);
    CREATE INDEX idx_providers_user ON providers(user_id);
    """,
    # 3 — web reading and search: fetched pages / search results per message,
    # and the step log, queries and citation map of an answer (messages.web).
    # Additive only: an older Chimera can still open a v3 database.
    """
    CREATE TABLE web_sources (
        id TEXT PRIMARY KEY,
        user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
        message_id TEXT NOT NULL REFERENCES messages(id) ON DELETE CASCADE,
        origin TEXT NOT NULL,
        position INTEGER NOT NULL DEFAULT 0,
        url TEXT NOT NULL,
        final_url TEXT,
        title TEXT,
        site_name TEXT,
        description TEXT,
        published TEXT,
        kind TEXT,
        content_type TEXT,
        status TEXT NOT NULL,
        error_code TEXT,
        error_params TEXT NOT NULL DEFAULT '{}',
        content TEXT NOT NULL DEFAULT '',
        truncated INTEGER NOT NULL DEFAULT 0,
        notes TEXT NOT NULL DEFAULT '[]',
        query TEXT,
        snippet TEXT,
        rank INTEGER,
        engine TEXT,
        fetched_at TEXT,
        created_at TEXT NOT NULL
    );
    CREATE INDEX idx_web_sources_conversation ON web_sources(conversation_id, created_at);
    CREATE INDEX idx_web_sources_message ON web_sources(message_id);
    CREATE INDEX idx_web_sources_user ON web_sources(user_id);
    ALTER TABLE messages ADD COLUMN web TEXT NOT NULL DEFAULT '{}';
    """,
    _migrate_tree,
]


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def new_id() -> str:
    return uuid.uuid4().hex


def dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def loads(value: str | None, default: Any = None) -> Any:
    if value is None or value == "":
        return default
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return default


class Database:
    def __init__(self, path: Path | str, backups_dir: Path | None = None):
        self.path = Path(path) if str(path) != ":memory:" else path
        self.backups_dir = backups_dir
        self.last_backup: Path | None = None
        self._lock = threading.RLock()
        self._conn = self._open()
        self.migrate()

    def _open(self) -> sqlite3.Connection:
        if isinstance(self.path, Path):
            self.path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(self.path), check_same_thread=False, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA synchronous = NORMAL")
        conn.execute("PRAGMA busy_timeout = 5000")
        return conn

    # -- schema -----------------------------------------------------------
    def schema_version(self) -> int:
        with self._lock:
            self._conn.execute("CREATE TABLE IF NOT EXISTS schema_version (version INTEGER NOT NULL)")
            row = self._conn.execute("SELECT MAX(version) AS v FROM schema_version").fetchone()
            return int(row["v"] or 0)

    def migrate(self) -> None:
        with self._lock:
            current = self.schema_version()
            if 0 < current < len(MIGRATIONS) and self.backups_dir is not None:
                # Never migrate a populated database without a restorable copy.
                self.backups_dir.mkdir(parents=True, exist_ok=True)
                stamp = now_iso().replace(":", "").replace("-", "").replace(".", "")
                target = self.backups_dir / f"pre-migration-v{current}-to-v{len(MIGRATIONS)}-{stamp}.sqlite"
                self.backup_to(target)
                self.last_backup = target
                logging.getLogger("chimera.db").warning(
                    "Database schema v%d -> v%d: backup written to %s", current, len(MIGRATIONS), target
                )
            for index, script in enumerate(MIGRATIONS, start=1):
                if index <= current:
                    continue
                self._conn.execute("BEGIN")
                try:
                    if callable(script):
                        script(self._conn)
                    else:
                        for statement in _split_sql(script):
                            self._conn.execute(statement)
                    self._conn.execute("INSERT INTO schema_version(version) VALUES (?)", (index,))
                    self._conn.execute("COMMIT")
                except Exception:
                    self._conn.execute("ROLLBACK")
                    raise

    # -- queries ----------------------------------------------------------
    def execute(self, sql: str, params: tuple | dict = ()) -> sqlite3.Cursor:
        with self._lock:
            return self._conn.execute(sql, params)

    def one(self, sql: str, params: tuple | dict = ()) -> dict | None:
        with self._lock:
            row = self._conn.execute(sql, params).fetchone()
            return dict(row) if row else None

    def all(self, sql: str, params: tuple | dict = ()) -> list[dict]:
        with self._lock:
            return [dict(r) for r in self._conn.execute(sql, params).fetchall()]

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            self._conn.execute("BEGIN")
            try:
                yield self._conn
            except BaseException:
                self._conn.execute("ROLLBACK")
                raise
            else:
                self._conn.execute("COMMIT")

    # -- maintenance ------------------------------------------------------
    def backup_to(self, target: Path) -> None:
        with self._lock:
            dest = sqlite3.connect(str(target))
            try:
                self._conn.backup(dest)
            finally:
                dest.close()

    def replace_with(self, source: Path) -> None:
        """Swap the live database for ``source`` (already validated)."""
        if not isinstance(self.path, Path):
            raise RuntimeError("cannot restore an in-memory database")
        with self._lock:
            src = sqlite3.connect(str(source))
            try:
                src.backup(self._conn)
            finally:
                src.close()
            self.migrate()

    def close(self) -> None:
        with self._lock:
            self._conn.close()


def _split_sql(script: str) -> list[str]:
    """Split a migration script into statements, keeping trigger bodies intact."""
    statements: list[str] = []
    buf: list[str] = []
    for line in script.splitlines():
        buf.append(line)
        candidate = "\n".join(buf).strip()
        if candidate and sqlite3.complete_statement(candidate):
            statements.append(candidate)
            buf = []
    tail = "\n".join(buf).strip()
    if tail:
        statements.append(tail)
    return statements


def is_chimera_database(path: Path) -> bool:
    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        try:
            names = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            return {"schema_version", "conversations", "messages", "providers"} <= names
        finally:
            conn.close()
    except sqlite3.DatabaseError:
        return False
