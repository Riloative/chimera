"""Web feature settings: instance-wide (administrator) and per-user."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

EngineKind = Literal["searxng", "brave", "tavily"]
DEFAULT_USER_AGENT = "Chimera/0.3 (self-hosted AI assistant; fetches pages on behalf of its users)"


def _clean_list(values: list[str]) -> list[str]:
    out: list[str] = []
    for value in values:
        value = value.strip()
        if value and value not in out:
            out.append(value[:253])
    return out


class WebAdminSettings(BaseModel):
    """Stored in ``app_settings['web']``. Secrets (engine API keys) are stored
    separately, encrypted, and never appear in this model."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool = False
    engine: EngineKind | None = None
    searxng_url: str = Field("", max_length=500)
    searxng_auth_header: str = Field("", max_length=100, pattern=r"^[A-Za-z0-9-]*$")
    # Search pipeline
    query_generation: bool = True
    query_prompt: str = Field("", max_length=20_000)  # empty = built-in default
    grounding_prompt: str = Field("", max_length=20_000)  # empty = built-in default
    # Fetching
    fetch_allowlist: list[str] = Field(default_factory=list, max_length=100)
    domain_allowlist: list[str] = Field(default_factory=list, max_length=500)
    domain_denylist: list[str] = Field(default_factory=list, max_length=500)
    allowed_ports: list[int] = Field(default_factory=lambda: [80, 443], max_length=20)
    robots_for_search: bool = True
    user_agent: str = Field(DEFAULT_USER_AGENT, min_length=1, max_length=300)
    max_page_kb: int = Field(10_240, ge=64, le=51_200)
    timeout_seconds: float = Field(20, ge=2, le=120)
    # Limits
    max_urls_per_message: int = Field(5, ge=1, le=10)
    max_results: int = Field(8, ge=1, le=10)
    per_source_chars: int = Field(8_000, ge=1_000, le=200_000)
    total_chars: int = Field(24_000, ge=2_000, le=800_000)
    fetches_per_hour: int = Field(300, ge=1, le=100_000)
    searches_per_hour: int = Field(60, ge=1, le=100_000)
    # Headless browser (only when the browser service is configured)
    browser_enabled: bool = True
    browser_auto: bool = True  # render pages that need JavaScript automatically
    browser_timeout_seconds: float = Field(25, ge=5, le=60)
    browser_renders_per_hour: int = Field(60, ge=1, le=10_000)

    _lists = field_validator("fetch_allowlist", "domain_allowlist", "domain_denylist")(_clean_list)

    @field_validator("allowed_ports")
    @classmethod
    def _ports(cls, value: list[int]) -> list[int]:
        ports = sorted({p for p in value if 1 <= p <= 65535})
        if not ports:
            raise ValueError("At least one port is required")
        return ports

    @field_validator("searxng_url")
    @classmethod
    def _url(cls, value: str) -> str:
        value = value.strip().rstrip("/")
        if value and not value.lower().startswith(("http://", "https://")):
            raise ValueError("URL must start with http:// or https://")
        if any(ch.isspace() for ch in value):
            raise ValueError("URL must not contain whitespace")
        return value


class WebUserSettings(BaseModel):
    """Per-user preferences (part of the user's ``AppSettings``)."""

    # "ignore": stored settings from 0.3 may still carry ``context_tokens``,
    # which moved to the context settings (see services/settings.py).
    model_config = ConfigDict(extra="ignore")

    auto_read_links: bool = True
    search_by_default: bool = False
    keep_search_on: bool = False
    results: int = Field(5, ge=1, le=10)
    expand_steps: bool = False
    task_provider_id: str | None = None
    task_model: str | None = Field(None, max_length=500)
    privacy_acknowledged: bool = False
