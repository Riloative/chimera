"""Provider registry: persistence of connections, status tracking, discovery."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import httpx

from ..db import Database, dumps, loads, new_id, now_iso
from ..providers import (
    DEFAULTS,
    PROVIDER_CLASSES,
    ModelInfo,
    Provider,
    ProviderConfig,
    ProviderError,
    build_provider,
)

log = logging.getLogger("chimera.providers")

EDITABLE = ("name", "kind", "base_url", "api_path", "api_key", "auth_header", "timeout", "enabled", "vision", "manual_models")


class ProviderRegistry:
    def __init__(self, db: Database, client: httpx.AsyncClient):
        self.db = db
        self.client = client

    # -- rows ---------------------------------------------------------------
    def rows(self) -> list[dict]:
        return self.db.all("SELECT * FROM providers ORDER BY position, created_at")

    def row(self, provider_id: str) -> dict | None:
        return self.db.one("SELECT * FROM providers WHERE id = ?", (provider_id,))

    @staticmethod
    def public(row: dict) -> dict[str, Any]:
        """Client-safe representation. Secrets never leave the server."""
        cls = PROVIDER_CLASSES.get(row["kind"])
        return {
            "id": row["id"],
            "name": row["name"],
            "kind": row["kind"],
            "base_url": row["base_url"],
            "api_path": row["api_path"],
            "has_api_key": bool(row.get("api_key")),
            "auth_header": row.get("auth_header") or "",
            "timeout": row["timeout"],
            "enabled": bool(row["enabled"]),
            "vision": row["vision"],
            "manual_models": loads(row["manual_models"], []),
            "status": "disabled" if not row["enabled"] else row["status"],
            "last_error": row["last_error"],
            "latency_ms": row["latency_ms"],
            "checked_at": row["checked_at"],
            "models_fetched_at": row["models_fetched_at"],
            "capabilities": loads(row["capabilities"], {}),
            "supported_params": list(cls.supported_params) if cls else [],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    def create(self, data: dict[str, Any]) -> dict:
        ts = now_iso()
        kind = data["kind"]
        defaults = DEFAULTS.get(kind, {})
        pid = new_id()
        position = (self.db.one("SELECT COALESCE(MAX(position), 0) + 1 AS p FROM providers") or {"p": 1})["p"]
        self.db.execute(
            """INSERT INTO providers (id, name, kind, base_url, api_path, api_key, auth_header, timeout, enabled,
                   vision, manual_models, position, created_at, updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                pid,
                data["name"],
                kind,
                data.get("base_url") or defaults.get("base_url", ""),
                data.get("api_path") if data.get("api_path") is not None else defaults.get("api_path", "/v1"),
                data.get("api_key") or None,
                data.get("auth_header") or None,
                float(data.get("timeout") or 300),
                1 if data.get("enabled", True) else 0,
                data.get("vision") or "auto",
                dumps(data.get("manual_models") or []),
                position,
                ts,
                ts,
            ),
        )
        return self.row(pid)  # type: ignore[return-value]

    def update(self, provider_id: str, data: dict[str, Any]) -> dict | None:
        row = self.row(provider_id)
        if not row:
            return None
        sets: list[str] = []
        values: list[Any] = []
        connection_changed = False
        for key in EDITABLE:
            if key not in data:
                continue
            value = data[key]
            if key == "api_key":
                if value is None:
                    continue  # omitted -> keep the stored key
                value = value or None  # "" -> clear
            if key == "enabled":
                value = 1 if value else 0
            if key == "manual_models":
                value = dumps(value or [])
            if key in ("base_url", "api_path", "api_key", "auth_header", "kind"):
                connection_changed = connection_changed or value != row.get(key)
            sets.append(f"{key} = ?")
            values.append(value)
        if connection_changed:
            # A connection that changed has not been verified yet.
            sets += ["status = 'unknown'", "last_error = NULL", "latency_ms = NULL", "cached_models = '[]'"]
        sets.append("updated_at = ?")
        values.append(now_iso())
        self.db.execute(f"UPDATE providers SET {', '.join(sets)} WHERE id = ?", (*values, provider_id))
        return self.row(provider_id)

    def delete(self, provider_id: str) -> bool:
        cur = self.db.execute("DELETE FROM providers WHERE id = ?", (provider_id,))
        return cur.rowcount > 0

    def seed_default(self, url: str) -> None:
        if not url or self.db.one("SELECT id FROM providers LIMIT 1"):
            return
        if self.db.one("SELECT value FROM settings WHERE key = '_seeded'"):
            return
        self.create({"name": "llama-server", "kind": "llamacpp", "base_url": url, "api_path": "/v1"})
        self.db.execute("INSERT OR REPLACE INTO settings(key, value) VALUES ('_seeded', 'true')")

    # -- runtime ------------------------------------------------------------
    @staticmethod
    def config_from(row: dict) -> ProviderConfig:
        return ProviderConfig(
            id=row["id"],
            name=row["name"],
            kind=row["kind"],
            base_url=row["base_url"],
            api_path=row["api_path"],
            api_key=row.get("api_key"),
            auth_header=row.get("auth_header"),
            timeout=row["timeout"],
            enabled=bool(row["enabled"]),
            vision=row["vision"],
        )

    def provider(self, provider_id: str) -> Provider:
        row = self.row(provider_id)
        if not row:
            raise ProviderError("The selected connection no longer exists.", code="not_found")
        if not row["enabled"]:
            raise ProviderError(f"The connection '{row['name']}' is disabled.", code="disabled")
        return build_provider(self.config_from(row), self.client)

    def mark_ok(self, provider_id: str, latency_ms: float | None = None) -> None:
        self.db.execute(
            "UPDATE providers SET status='connected', last_error=NULL, checked_at=?, latency_ms=COALESCE(?, latency_ms) WHERE id=?",
            (now_iso(), latency_ms, provider_id),
        )

    def mark_error(self, provider_id: str, error: ProviderError) -> None:
        # Only connection-level failures say anything about the server's health.
        if error.code in ("connection", "timeout", "unavailable", "auth", "config"):
            self.db.execute(
                "UPDATE providers SET status='error', last_error=?, checked_at=? WHERE id=?",
                (error.message, now_iso(), provider_id),
            )
        else:
            self.db.execute("UPDATE providers SET last_error=? WHERE id=?", (error.message, provider_id))

    async def test(self, provider_id: str) -> dict[str, Any]:
        row = self.row(provider_id)
        if not row:
            raise ProviderError("Connection not found.", code="not_found")
        provider = build_provider(self.config_from(row), self.client)
        report = await provider.check()
        ts = now_iso()
        self.db.execute(
            """UPDATE providers SET status=?, last_error=?, latency_ms=?, checked_at=?, capabilities=?,
                   cached_models=CASE WHEN ? THEN ? ELSE cached_models END,
                   models_fetched_at=CASE WHEN ? THEN ? ELSE models_fetched_at END
               WHERE id=?""",
            (
                "connected" if report.ok else "error",
                report.error,
                report.latency_ms,
                ts,
                dumps(report.capabilities),
                report.models_ok,
                dumps([m.to_dict() for m in report.models]),
                report.models_ok,
                ts,
                provider_id,
            ),
        )
        return report_dict(report)

    async def test_unsaved(self, data: dict[str, Any]) -> dict[str, Any]:
        defaults = DEFAULTS.get(data["kind"], {})
        config = ProviderConfig(
            id="unsaved",
            name=data.get("name") or "server",
            kind=data["kind"],
            base_url=data.get("base_url") or defaults.get("base_url", ""),
            api_path=data.get("api_path") if data.get("api_path") is not None else defaults.get("api_path", "/v1"),
            api_key=data.get("api_key"),
            auth_header=data.get("auth_header"),
            timeout=float(data.get("timeout") or 300),
            vision=data.get("vision") or "auto",
        )
        report = await build_provider(config, self.client).check()
        return report_dict(report)

    def _manual_models(self, row: dict) -> list[ModelInfo]:
        vision = {"on": True, "off": False}.get(row["vision"])
        return [
            ModelInfo(id=m, provider_id=row["id"], name=m, source="manual", vision=vision)
            for m in loads(row["manual_models"], [])
            if isinstance(m, str) and m.strip()
        ]

    async def discover(self, row: dict, refresh: bool) -> dict[str, Any]:
        """Models for one connection. Never invents models: only what the
        server reported (live or last cached) plus user-declared manual ones."""
        result: dict[str, Any] = {"provider": None, "models": [], "stale": False, "error": None}
        cached = loads(row["cached_models"], [])
        if not row["enabled"]:
            result["provider"] = self.public(row)
            return result
        if refresh or not row["models_fetched_at"]:
            # A full check also refreshes capabilities (e.g. vision via /props).
            report = await self.test(row["id"])
            fresh = self.row(row["id"]) or row
            if report["models_ok"]:
                cached = loads(fresh["cached_models"], [])
            else:
                result["error"] = report["error"]
                result["stale"] = bool(cached)
        models = list(cached)
        known = {m["id"] for m in models}
        models += [m.to_dict() for m in self._manual_models(row) if m.id not in known]
        result["models"] = models
        result["provider"] = self.public(self.row(row["id"]) or row)
        return result

    async def discover_all(self, refresh: bool) -> list[dict[str, Any]]:
        rows = self.rows()
        return list(await asyncio.gather(*(self.discover(r, refresh) for r in rows)))


def report_dict(report) -> dict[str, Any]:
    return {
        "ok": report.ok,
        "latency_ms": report.latency_ms,
        "error": report.error,
        "error_code": report.error_code,
        "models": [m.to_dict() for m in report.models],
        "models_ok": report.models_ok,
        "capabilities": report.capabilities,
        "checks": report.checks,
    }
