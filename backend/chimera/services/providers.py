"""Provider registry: connections, ownership, status tracking, discovery.

Ownership model
---------------
* Every connection has an owner (``user_id``).
* A connection with ``shared = 1`` (only admins can share) is visible to and
  usable by every user, but only admins can edit it.
* API keys are encrypted at rest (``crypto.SecretBox``) and never returned to
  the browser: responses only say whether a key is set.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import httpx

from ..crypto import SecretBox
from ..db import Database, dumps, loads, new_id, now_iso
from ..providers import reasoning
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

EDITABLE = ("name", "kind", "base_url", "api_path", "api_key", "auth_header", "timeout", "enabled", "vision", "manual_models", "shared",
            "context_override", "service", "model_settings")
HEALTH_CODES = ("connection", "connect_timeout", "connection_closed", "timeout", "unavailable", "auth", "config")


def is_admin(user: dict) -> bool:
    return user.get("role") == "admin"


class ProviderRegistry:
    def __init__(self, db: Database, client: httpx.AsyncClient, box: SecretBox):
        self.db = db
        self.client = client
        self.box = box

    # -- access ---------------------------------------------------------------
    def rows(self, user: dict) -> list[dict]:
        return self.db.all(
            "SELECT * FROM providers WHERE user_id = ? OR shared = 1 ORDER BY shared DESC, position, created_at",
            (user["id"],),
        )

    def row(self, provider_id: str) -> dict | None:
        return self.db.one("SELECT * FROM providers WHERE id = ?", (provider_id,))

    def visible(self, user: dict, provider_id: str | None) -> dict | None:
        """The connection if this user may use it, else None (callers answer 404)."""
        if not provider_id:
            return None
        row = self.row(provider_id)
        if row and (row["user_id"] == user["id"] or row["shared"]):
            return row
        return None

    @staticmethod
    def can_edit(user: dict, row: dict) -> bool:
        return row["user_id"] == user["id"] or (bool(row["shared"]) and is_admin(user))

    def public(self, row: dict, user: dict | None = None) -> dict[str, Any]:
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
            "shared": bool(row["shared"]),
            "owned": bool(user and row["user_id"] == user["id"]),
            "editable": bool(user and self.can_edit(user, row)),
            "status": "disabled" if not row["enabled"] else row["status"],
            "last_error": row["last_error"],
            "last_error_code": row.get("last_error_code"),
            "last_error_params": loads(row.get("last_error_params"), {}),
            "latency_ms": row["latency_ms"],
            "checked_at": row["checked_at"],
            "models_fetched_at": row["models_fetched_at"],
            "capabilities": loads(row["capabilities"], {}),
            # Manual context size; null = use what the server reports (see services/context.py).
            "context_override": row.get("context_override"),
            # OpenAI-compatible: the hosted service (null = recognised by host, see "detected_service").
            "service": row.get("service"),
            "detected_service": reasoning.service_of(row["base_url"]) if row["kind"] == "openai" else None,
            "model_settings": loads(row.get("model_settings"), {}),
            "supported_params": list(cls.supported_params) if cls else [],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    # -- writes ---------------------------------------------------------------
    def create(self, user_id: str, data: dict[str, Any], *, shared: bool = False) -> dict:
        ts = now_iso()
        kind = data["kind"]
        defaults = DEFAULTS.get(kind, {})
        pid = new_id()
        position = (self.db.one("SELECT COALESCE(MAX(position), 0) + 1 AS p FROM providers") or {"p": 1})["p"]
        self.db.execute(
            """INSERT INTO providers (id, user_id, shared, name, kind, base_url, api_path, api_key, auth_header, timeout,
                   enabled, vision, manual_models, position, context_override, service, model_settings, created_at, updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                pid,
                user_id,
                1 if shared else 0,
                data["name"],
                kind,
                data.get("base_url") or defaults.get("base_url", ""),
                data.get("api_path") if data.get("api_path") is not None else defaults.get("api_path", "/v1"),
                self.box.encrypt(data.get("api_key") or None),
                data.get("auth_header") or None,
                float(data.get("timeout") or 300),
                1 if data.get("enabled", True) else 0,
                data.get("vision") or "auto",
                dumps(data.get("manual_models") or []),
                position,
                data.get("context_override"),
                data.get("service"),
                dumps(_model_settings(data.get("model_settings"))),
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
                connection_changed = connection_changed or value != self.box.decrypt(row.get("api_key"))
                value = self.box.encrypt(value or None)  # "" -> clear
            elif key in ("enabled", "shared"):
                value = 1 if value else 0
            elif key == "manual_models":
                value = dumps(value or [])
            elif key == "model_settings":
                value = dumps(_model_settings(value))
            elif key in ("base_url", "api_path", "auth_header", "kind"):
                connection_changed = connection_changed or value != row.get(key)
            sets.append(f"{key} = ?")
            values.append(value)
        if connection_changed:
            # A connection that changed has not been verified yet.
            sets += ["status = 'unknown'", "last_error = NULL", "last_error_code = NULL", "latency_ms = NULL", "cached_models = '[]'"]
        sets.append("updated_at = ?")
        values.append(now_iso())
        self.db.execute(f"UPDATE providers SET {', '.join(sets)} WHERE id = ?", (*values, provider_id))
        return self.row(provider_id)

    def delete(self, provider_id: str) -> bool:
        return self.db.execute("DELETE FROM providers WHERE id = ?", (provider_id,)).rowcount > 0

    def seed_default(self, admin_id: str, url: str) -> None:
        """First admin: a shared connection to the usual llama-server address."""
        if not url or self.db.one("SELECT id FROM providers LIMIT 1"):
            return
        if self.db.one("SELECT value FROM app_settings WHERE key = '_seeded'"):
            return
        self.create(admin_id, {"name": "llama-server", "kind": "llamacpp", "base_url": url, "api_path": "/v1"}, shared=True)
        self.db.execute("INSERT OR REPLACE INTO app_settings(key, value) VALUES ('_seeded', 'true')")

    def encrypt_legacy_keys(self) -> int:
        """Encrypt plaintext API keys left by versions before at-rest encryption."""
        n = 0
        for row in self.db.all("SELECT id, api_key FROM providers WHERE api_key IS NOT NULL AND api_key NOT LIKE 'enc:%'"):
            self.db.execute("UPDATE providers SET api_key = ? WHERE id = ?", (self.box.encrypt(row["api_key"]), row["id"]))
            n += 1
        return n

    # -- runtime ------------------------------------------------------------
    def config_from(self, row: dict) -> ProviderConfig:
        return ProviderConfig(
            id=row["id"],
            name=row["name"],
            kind=row["kind"],
            base_url=row["base_url"],
            api_path=row["api_path"],
            api_key=self.box.decrypt(row.get("api_key")),
            auth_header=row.get("auth_header"),
            timeout=row["timeout"],
            enabled=bool(row["enabled"]),
            vision=row["vision"],
            service=row.get("service"),
        )

    def build(self, row: dict) -> Provider:
        """A provider for a stored connection, with what thinking detection needs."""
        provider = build_provider(self.config_from(row), self.client)
        provider.hints = {m["id"]: m for m in loads(row.get("cached_models"), []) if isinstance(m, dict) and m.get("id")}
        provider.overrides = loads(row.get("model_settings"), {}) or {}
        provider.server_caps = loads(row.get("capabilities"), {}) or {}
        return provider

    def provider(self, user: dict, provider_id: str) -> Provider:
        row = self.visible(user, provider_id)
        if not row:
            raise ProviderError("The selected connection no longer exists.", code="provider_missing")
        if not row["enabled"]:
            raise ProviderError(f"The connection '{row['name']}' is disabled.", code="disabled", name=row["name"])
        return self.build(row)

    def thinking_caps(self, row: dict, model: str) -> reasoning.Caps:
        return self.build(row).reasoning_caps(model)

    def mark_ok(self, provider_id: str, latency_ms: float | None = None) -> None:
        self.db.execute(
            """UPDATE providers SET status='connected', last_error=NULL, last_error_code=NULL, last_error_params='{}',
                   checked_at=?, latency_ms=COALESCE(?, latency_ms) WHERE id=?""",
            (now_iso(), latency_ms, provider_id),
        )

    def mark_error(self, provider_id: str, error: ProviderError) -> None:
        # Only connection-level failures say anything about the server's health.
        if error.code in HEALTH_CODES:
            self.db.execute(
                "UPDATE providers SET status='error', last_error=?, last_error_code=?, last_error_params=?, checked_at=? WHERE id=?",
                (error.message, error.code, dumps(error.params), now_iso(), provider_id),
            )
        else:
            self.db.execute(
                "UPDATE providers SET last_error=?, last_error_code=?, last_error_params=? WHERE id=?",
                (error.message, error.code, dumps(error.params), provider_id),
            )

    async def test(self, provider_id: str) -> dict[str, Any]:
        row = self.row(provider_id)
        if not row:
            raise ProviderError("Connection not found.", code="provider_missing")
        provider = build_provider(self.config_from(row), self.client)
        report = await provider.check()
        ts = now_iso()
        self.db.execute(
            """UPDATE providers SET status=?, last_error=?, last_error_code=?, last_error_params=?, latency_ms=?,
                   checked_at=?, capabilities=?,
                   cached_models=CASE WHEN ? THEN ? ELSE cached_models END,
                   models_fetched_at=CASE WHEN ? THEN ? ELSE models_fetched_at END
               WHERE id=?""",
            (
                "connected" if report.ok else "error",
                report.error,
                report.error_code,
                dumps(report.error_params),
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
            service=data.get("service"),
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

    async def discover(self, row: dict, refresh: bool, user: dict) -> dict[str, Any]:
        """Models for one connection. Never invents models: only what the
        server reported (live or last cached) plus user-declared manual ones.
        The cache holds server facts only (model IDs/capabilities), never user data."""
        result: dict[str, Any] = {"provider": None, "models": [], "stale": False, "error": None, "error_code": None, "error_params": {}}
        cached = loads(row["cached_models"], [])
        if not row["enabled"]:
            result["provider"] = self.public(row, user)
            return result
        if refresh or not row["models_fetched_at"]:
            # A full check also refreshes capabilities (e.g. vision via /props).
            report = await self.test(row["id"])
            fresh = self.row(row["id"]) or row
            if report["models_ok"]:
                cached = loads(fresh["cached_models"], [])
            else:
                result["error"] = report["error"]
                result["error_code"] = report["error_code"]
                result["error_params"] = report["error_params"]
                result["stale"] = bool(cached)
        models = list(cached)
        known = {m["id"] for m in models}
        models += [m.to_dict() for m in self._manual_models(row) if m.id not in known]
        fresh = self.row(row["id"]) or row
        provider = self.build(fresh)
        # What the thinking control can offer for each model (never the provider's values).
        result["models"] = [{**{k: v for k, v in m.items() if k != "reasoning"}, "thinking": provider.reasoning_caps(m["id"]).public()}
                            for m in models]
        result["provider"] = self.public(fresh, user)
        return result

    async def discover_all(self, user: dict, refresh: bool) -> list[dict[str, Any]]:
        rows = self.rows(user)
        return list(await asyncio.gather(*(self.discover(r, refresh, user) for r in rows)))


def _model_settings(value: Any) -> dict[str, dict]:
    """Per-model settings as stored: only non-default choices."""
    out: dict[str, dict] = {}
    for model, setting in (value or {}).items():
        choice = setting.get("thinking") if isinstance(setting, dict) else getattr(setting, "thinking", None)
        if isinstance(model, str) and model.strip() and choice in reasoning.OVERRIDES and choice != "detect":
            out[model[:500]] = {"thinking": choice}
    return out


def report_dict(report) -> dict[str, Any]:
    return {
        "ok": report.ok,
        "latency_ms": report.latency_ms,
        "error": report.error,
        "error_code": report.error_code,
        "error_params": report.error_params,
        "models": [m.to_dict() for m in report.models],
        "models_ok": report.models_ok,
        "capabilities": report.capabilities,
        "checks": report.checks,
    }
