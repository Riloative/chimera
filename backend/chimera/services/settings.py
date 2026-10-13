"""User preferences, persisted in the ``settings`` table and validated here."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from ..db import Database, dumps, loads


class GenerationParams(BaseModel):
    """Sampling parameters. ``None`` means "use the server's default"."""

    model_config = ConfigDict(extra="forbid")

    temperature: float | None = Field(None, ge=0, le=5)
    top_p: float | None = Field(None, ge=0, le=1)
    top_k: int | None = Field(None, ge=0, le=1000)
    min_p: float | None = Field(None, ge=0, le=1)
    max_tokens: int | None = Field(None, ge=1, le=1_000_000)
    stop: list[str] | None = Field(None, max_length=8)
    repeat_penalty: float | None = Field(None, ge=0, le=5)
    presence_penalty: float | None = Field(None, ge=-2, le=2)
    frequency_penalty: float | None = Field(None, ge=-2, le=2)
    seed: int | None = Field(None, ge=-1, le=2**63 - 1)


class KnowledgeSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    embedding_provider_id: str | None = None
    embedding_model: str | None = None
    chunk_size: int = Field(1000, ge=200, le=8000)
    chunk_overlap: int = Field(150, ge=0, le=2000)
    top_k: int = Field(5, ge=1, le=20)


class AppSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # Appearance
    theme: Literal["system", "light", "dark"] = "system"
    font_size: Literal["sm", "md", "lg"] = "md"
    chat_width: Literal["narrow", "default", "wide"] = "default"
    reduce_motion: bool = False
    sidebar_collapsed: bool = False
    # Models & chat
    default_provider_id: str | None = None
    default_model: str | None = None
    default_system_prompt: str = Field("", max_length=100_000)
    params: GenerationParams = Field(default_factory=GenerationParams)
    enter_to_send: bool = True
    stream: bool = True
    auto_title: bool = True
    show_reasoning: bool = True
    # Documents
    max_attachment_mb: int = Field(10, ge=1, le=100)
    max_attachment_chars: int = Field(100_000, ge=1_000, le=2_000_000)
    knowledge: KnowledgeSettings = Field(default_factory=KnowledgeSettings)


class SettingsStore:
    def __init__(self, db: Database):
        self.db = db

    def get(self) -> AppSettings:
        stored: dict = {}
        for row in self.db.all("SELECT key, value FROM settings WHERE key NOT LIKE '\\_%' ESCAPE '\\'"):
            stored[row["key"]] = loads(row["value"])
        known = {k: v for k, v in stored.items() if k in AppSettings.model_fields}
        try:
            return AppSettings.model_validate(known)
        except Exception:  # noqa: BLE001 - corrupted values fall back field by field
            settings = AppSettings()
            for key, value in known.items():
                try:
                    settings = AppSettings.model_validate({**settings.model_dump(), key: value})
                except Exception:  # noqa: BLE001
                    continue
            return settings

    def update(self, patch: dict) -> AppSettings:
        current = self.get().model_dump()
        for key, value in patch.items():
            if isinstance(value, dict) and isinstance(current.get(key), dict):
                current[key] = {**current[key], **value}
            else:
                current[key] = value
        validated = AppSettings.model_validate(current)
        dumped = validated.model_dump()
        with self.db.transaction() as conn:
            for key in patch:
                conn.execute(
                    "INSERT INTO settings(key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                    (key, dumps(dumped[key])),
                )
        return validated
