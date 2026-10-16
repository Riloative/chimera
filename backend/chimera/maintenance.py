"""Backup, restore and the data-directory lock.

``chimera backup <path>`` writes one ``.tar.gz`` archive with everything needed
to bring an installation back:

* ``chimera.sqlite`` – a consistent copy made with SQLite's online backup API
  (safe while the server is running);
* ``uploads/`` – attachments and knowledge files;
* the key files from the data directory (``.encryption_key``…), without which
  stored API keys can't be decrypted;
* ``manifest.json`` – format, Chimera version, schema version, time.

``chimera restore <archive>`` puts such an archive back. It refuses while a
server is using the data directory (the server holds an exclusive lock on
``.chimera.lock``), validates the archive (paths, database integrity, schema
version not newer than this code supports) and first moves the current data
aside to ``backups/pre-restore-<time>/``.
"""

from __future__ import annotations

import fcntl
import io
import json
import os
import shutil
import sqlite3
import tarfile
import tempfile
import time
from pathlib import Path

from . import __version__
from .db import SCHEMA_VERSION, Database, schema_version_of

FORMAT = "chimera-backup"
LOCK_NAME = ".chimera.lock"
DB_NAME = "chimera.sqlite"
# Never part of an archive: the lock, earlier backups, SQLite side files.
EXCLUDE = {LOCK_NAME, "backups", "chimera.sqlite", "chimera.sqlite-wal", "chimera.sqlite-shm"}


class MaintenanceError(RuntimeError):
    pass


# ------------------------------------------------------------------ lock
class DataDirLock:
    """Exclusive advisory lock on the data directory, held by a running server."""

    def __init__(self, data_dir: Path):
        self.path = Path(data_dir) / LOCK_NAME
        self._fh = None

    def acquire(self) -> bool:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fh = open(self.path, "a+")  # noqa: SIM115 - kept open while locked
        try:
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            fh.close()
            return False
        self._fh = fh
        return True

    def release(self) -> None:
        if self._fh is not None:
            try:
                fcntl.flock(self._fh.fileno(), fcntl.LOCK_UN)
            finally:
                self._fh.close()
                self._fh = None


def private_dir(path: Path) -> Path:
    """Create a directory only its owner can read (backups contain keys)."""
    path.mkdir(parents=True, exist_ok=True)
    os.chmod(path, 0o700)
    return path


def _stamp() -> str:
    return time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())


def _key_files(data_dir: Path) -> list[Path]:
    return sorted(p for p in data_dir.iterdir() if p.is_file() and p.name.startswith(".") and p.name not in EXCLUDE)


# ---------------------------------------------------------------- backup
def backup(data_dir: Path, dest: Path) -> dict:
    """Write a backup archive to ``dest`` (a file, or a directory to put a
    timestamped archive in). Returns the manifest (with ``path``)."""
    data_dir = Path(data_dir)
    db_path = data_dir / DB_NAME
    if not db_path.is_file():
        raise MaintenanceError(f"No database at {db_path}.")
    dest = Path(dest)
    if dest.is_dir() or not dest.name.endswith((".tar.gz", ".tgz")):
        private_dir(dest)
        dest = dest / f"chimera-backup-{_stamp()}.tar.gz"
    elif not dest.parent.exists():
        private_dir(dest.parent)
    with tempfile.TemporaryDirectory(prefix="chimera-backup-", dir=dest.parent) as tmp:
        snapshot = Path(tmp) / DB_NAME
        src = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        try:
            out = sqlite3.connect(str(snapshot))
            try:
                src.backup(out)
            finally:
                out.close()
        finally:
            src.close()
        manifest = {
            "format": FORMAT,
            "chimera_version": __version__,
            "schema_version": schema_version_of(snapshot),
            "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "contents": ["database", "uploads", "keys"],
        }
        partial = Path(tmp) / "archive.tar.gz"
        fd = os.open(partial, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)  # contains secrets
        with os.fdopen(fd, "wb") as raw, tarfile.open(fileobj=raw, mode="w:gz") as tar:
            data = json.dumps(manifest, indent=2).encode()
            info = tarfile.TarInfo("manifest.json")
            info.size, info.mtime, info.mode = len(data), int(time.time()), 0o600
            tar.addfile(info, io.BytesIO(data))
            tar.add(snapshot, arcname=DB_NAME)
            uploads = data_dir / "uploads"
            if uploads.is_dir():
                tar.add(uploads, arcname="uploads")
            for key in _key_files(data_dir):
                tar.add(key, arcname=f"keys/{key.name}")
        os.replace(partial, dest)
    os.chmod(dest, 0o600)
    return {**manifest, "path": str(dest)}


# --------------------------------------------------------------- restore
def _safe_members(tar: tarfile.TarFile) -> list[tarfile.TarInfo]:
    members = []
    for m in tar.getmembers():
        name = Path(m.name)
        if name.is_absolute() or ".." in name.parts:
            raise MaintenanceError(f"Unsafe path in archive: {m.name}")
        if not (m.isfile() or m.isdir()):
            raise MaintenanceError(f"Unsupported entry in archive (links are not allowed): {m.name}")
        if name.parts[0] not in ("manifest.json", DB_NAME, "uploads", "keys"):
            raise MaintenanceError(f"Unexpected file in archive: {m.name}")
        members.append(m)
    return members


def inspect_archive(archive: Path) -> dict:
    with tarfile.open(archive, "r:gz") as tar:
        _safe_members(tar)
        try:
            manifest = json.load(tar.extractfile("manifest.json"))  # type: ignore[arg-type]
        except (KeyError, ValueError, TypeError) as exc:
            raise MaintenanceError("Not a Chimera backup archive (manifest.json missing or invalid).") from exc
    if manifest.get("format") != FORMAT:
        raise MaintenanceError("Not a Chimera backup archive.")
    return manifest


def restore(data_dir: Path, archive: Path) -> dict:
    """Restore ``archive`` into ``data_dir``. The server must be stopped."""
    data_dir, archive = Path(data_dir), Path(archive)
    if not archive.is_file():
        raise MaintenanceError(f"No such archive: {archive}")
    manifest = inspect_archive(archive)
    lock = DataDirLock(data_dir)
    if not lock.acquire():
        raise MaintenanceError("Chimera is running and using this data directory. Stop it first "
                               "(docker compose stop chimera), then run the restore again.")
    try:
        with tempfile.TemporaryDirectory(prefix=".restore-", dir=data_dir) as tmp:
            staging = Path(tmp)
            with tarfile.open(archive, "r:gz") as tar:
                members = _safe_members(tar)
                try:
                    tar.extractall(staging, members=members, filter="data")
                except TypeError:  # Python < 3.11.4: members were validated above
                    tar.extractall(staging, members=members)  # noqa: S202
            db = staging / DB_NAME
            if not db.is_file():
                raise MaintenanceError("The archive contains no database.")
            conn = sqlite3.connect(str(db))
            try:
                result = conn.execute("PRAGMA integrity_check").fetchone()[0]
            finally:
                conn.close()
            if result != "ok":
                raise MaintenanceError(f"The database in the archive is damaged ({result}).")
            found = schema_version_of(db) or 0
            if found > SCHEMA_VERSION:
                raise MaintenanceError(
                    f"This backup was made by a newer Chimera (schema {found}; this version supports {SCHEMA_VERSION}). "
                    "Restore it with that version or newer.")
            # Keep what is there now, so a restore can itself be undone.
            private_dir(data_dir / "backups")
            aside = private_dir(data_dir / "backups" / f"pre-restore-{_stamp()}")
            for name in (DB_NAME, f"{DB_NAME}-wal", f"{DB_NAME}-shm", "uploads"):
                if (data_dir / name).exists():
                    shutil.move(str(data_dir / name), aside / name)
            for key in _key_files(data_dir):
                shutil.move(str(key), aside / key.name)
            shutil.move(str(db), data_dir / DB_NAME)
            if (staging / "uploads").is_dir():
                shutil.move(str(staging / "uploads"), data_dir / "uploads")
            for key in sorted((staging / "keys").glob(".*")) if (staging / "keys").is_dir() else []:
                shutil.move(str(key), data_dir / key.name)
                os.chmod(data_dir / key.name, 0o600)
        # Bring an older backup up to this version now (with the usual pre-migration copy).
        Database(data_dir / DB_NAME, backups_dir=data_dir / "backups").close()
    finally:
        lock.release()
    return {**manifest, "previous_data": str(aside)}
