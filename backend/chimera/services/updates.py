"""Opt-in update notices for administrators.

Off by default (privacy-first, offline-capable). When an administrator turns
it on, the server asks the GitHub Releases API at most once a day which
releases exist, through the same hardened fetcher as everything else, with no
cookies and a generic User-Agent: no user, instance or version data is sent.
Administrators then see "Chimera X is available" with a link to the release
notes. Nothing is ever downloaded or installed by the server; updating stays a
deliberate step (scripts/update.sh). Failures are recorded for the admin
diagnostics and never shown to users.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from typing import Any

from .. import __version__
from ..db import Database, dumps, loads, now_iso
from ..web.errors import WebError
from ..web.fetcher import FetchPolicy, SafeFetcher

log = logging.getLogger("chimera.updates")

CHECK_INTERVAL = 24 * 3600
LOOP_INTERVAL = 3600
RELEASES_PAGE = "https://github.com/Riloative/chimera/releases"
SEMVER = re.compile(r"^(\d+)\.(\d+)\.(\d+)(?:-([0-9A-Za-z.-]+))?$")


def version_key(value: str) -> tuple | None:
    m = SEMVER.match(value)
    if not m:
        return None
    pre = m.group(4)
    ids = [] if pre is None else [(0, int(p), "") if p.isdigit() else (1, 0, p) for p in pre.split(".")]
    return (int(m.group(1)), int(m.group(2)), int(m.group(3)), 0 if pre else 1, ids)


def newest_stable(releases: list[dict], current: str) -> dict | None:
    """The newest non-draft, non-pre-release newer than ``current``."""
    cur = version_key(current)
    best: tuple | None = None
    found: dict | None = None
    for rel in releases if isinstance(releases, list) else []:
        if not isinstance(rel, dict) or rel.get("draft") or rel.get("prerelease"):
            continue
        version = str(rel.get("tag_name") or "").removeprefix("v")
        key = version_key(version)
        if key is None or key[3] == 0 or (cur is not None and key <= cur):
            continue
        if best is None or key > best:
            url = str(rel.get("html_url") or "")
            # Only ever link to this project's release pages.
            if not url.startswith(RELEASES_PAGE + "/"):
                url = f"{RELEASES_PAGE}/tag/v{version}"
            best, found = key, {"version": version, "url": url}
    return found


class UpdateChecker:
    def __init__(self, db: Database, fetcher: SafeFetcher, url: str):
        self.db = db
        self.fetcher = fetcher
        self.url = url
        self._task: asyncio.Task | None = None

    # ------------------------------------------------------------ settings
    def _get(self, key: str, default: Any) -> Any:
        row = self.db.one("SELECT value FROM app_settings WHERE key = ?", (key,))
        return loads(row["value"], default) if row else default

    def _set(self, key: str, value: Any) -> None:
        self.db.execute(
            "INSERT INTO app_settings(key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, dumps(value)),
        )

    def settings(self) -> dict:
        stored = self._get("updates", {}) or {}
        return {"enabled": bool(stored.get("enabled", False)), "asked": bool(stored.get("asked", False))}

    def set_enabled(self, enabled: bool) -> dict:
        self._set("updates", {"enabled": bool(enabled), "asked": True})
        return self.settings()

    def status(self) -> dict:
        return self._get("update_status", {}) or {}

    def view(self) -> dict:
        """What administrators see (Settings → About)."""
        status = self.status()
        latest = status.get("latest")
        available = bool(latest and version_key(latest["version"]) and version_key(__version__)
                          and version_key(latest["version"]) > version_key(__version__))
        return {**self.settings(), "current": __version__, "available": latest if available else None,
                "last_check": {k: status.get(k) for k in ("checked_at", "ok", "error")} if status else None}

    # --------------------------------------------------------------- check
    def due(self, now: float | None = None) -> bool:
        if not self.settings()["enabled"]:
            return False
        last = self.status().get("checked_ts")
        return not isinstance(last, (int, float)) or (now or time.time()) - last >= CHECK_INTERVAL

    async def check(self) -> dict:
        policy = FetchPolicy(
            # The URL comes from the operator's configuration, like a SearXNG address.
            trusted_endpoint=True,
            accept_types=("application/json",),
            accept="application/vnd.github+json, application/json",
            user_agent="Chimera-update-check",
            max_bytes=2 * 1024 * 1024,
            total_timeout=20,
        )
        status: dict[str, Any] = {"checked_at": now_iso(), "checked_ts": time.time()}
        try:
            result = await self.fetcher.fetch(self.url, policy)
            releases = json.loads(result.body.decode("utf-8", "replace"))
            status.update(ok=True, error=None, latest=newest_stable(releases, __version__))
        except (WebError, ValueError) as exc:
            status.update(ok=False, error=getattr(exc, "code", "update.bad_response"), latest=self.status().get("latest"))
            log.info("update check failed: %s", status["error"])
        self._set("update_status", status)
        return status

    async def maybe_check(self) -> None:
        if self.due():
            await self.check()

    # ---------------------------------------------------------- background
    def start(self) -> None:
        async def loop() -> None:
            while True:
                try:
                    await self.maybe_check()
                except Exception:  # noqa: BLE001 - never let the loop die
                    log.exception("update check crashed")
                await asyncio.sleep(LOOP_INTERVAL)

        self._task = asyncio.create_task(loop())

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
