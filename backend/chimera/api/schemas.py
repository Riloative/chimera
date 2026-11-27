"""Request bodies (validated at the boundary)."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from ..services.settings import GenerationParams


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


def _validate_url(value: str | None) -> str | None:
    if value is None:
        return value
    value = value.strip()
    if not value.lower().startswith(("http://", "https://")):
        raise ValueError("URL must start with http:// or https://")
    if any(ch.isspace() for ch in value):
        raise ValueError("URL must not contain whitespace")
    return value.rstrip("/")


Kind = Literal["llamacpp", "openai", "ollama", "anthropic"]
Service = Literal["openai", "mistral", "gemini", "deepseek", "xai", "openrouter", "anthropic", "other"]
Thinking = Literal["default", "off", "on", "low", "medium", "high", "max"]


class ModelSetting(Strict):
    # Thinking control for this model: detect it, or the user says what it can do.
    thinking: Literal["detect", "none", "toggle", "levels"] = "detect"


def _model_keys(value):
    if value is not None and any(not k.strip() or len(k) > 500 for k in value):
        raise ValueError("model ids must be 1 to 500 characters")
    return value


class ProviderCreate(Strict):
    name: str = Field(min_length=1, max_length=80)
    kind: Kind
    base_url: str = Field(max_length=500)
    api_path: str | None = Field(None, max_length=200)
    api_key: str | None = Field(None, max_length=4000)
    auth_header: str | None = Field(None, max_length=100, pattern=r"^[A-Za-z0-9-]*$")
    timeout: float = Field(300, ge=1, le=3600)
    enabled: bool = True
    vision: Literal["auto", "on", "off"] = "auto"
    manual_models: list[str] = Field(default_factory=list, max_length=200)
    shared: bool = False
    context_override: int | None = Field(None, ge=256, le=4_000_000)
    # OpenAI-compatible connections: the hosted service; null = recognise it by host.
    service: Service | None = None
    model_settings: dict[str, ModelSetting] = Field(default_factory=dict, max_length=500)

    _url = field_validator("base_url")(_validate_url)
    _models = field_validator("model_settings")(_model_keys)


class ProviderUpdate(Strict):
    name: str | None = Field(None, min_length=1, max_length=80)
    kind: Kind | None = None
    base_url: str | None = Field(None, max_length=500)
    api_path: str | None = Field(None, max_length=200)
    api_key: str | None = Field(None, max_length=4000)
    auth_header: str | None = Field(None, max_length=100, pattern=r"^[A-Za-z0-9-]*$")
    timeout: float | None = Field(None, ge=1, le=3600)
    enabled: bool | None = None
    vision: Literal["auto", "on", "off"] | None = None
    manual_models: list[str] | None = Field(None, max_length=200)
    shared: bool | None = None
    context_override: int | None = Field(None, ge=256, le=4_000_000)
    service: Service | None = None
    # Replaces the stored per-model settings (keys are model ids).
    model_settings: dict[str, ModelSetting] | None = Field(None, max_length=500)

    _url = field_validator("base_url")(_validate_url)
    _models = field_validator("model_settings")(_model_keys)


class ProviderTest(ProviderCreate):
    name: str = Field("server", max_length=80)
    # When editing a saved connection, reuse its stored key if none is typed.
    provider_id: str | None = None


class ConversationCreate(Strict):
    title: str | None = Field(None, max_length=200)
    provider_id: str | None = None
    model: str | None = Field(None, max_length=500)
    system_prompt: str | None = Field(None, max_length=100_000)
    params: GenerationParams | None = None
    collection_ids: list[str] = Field(default_factory=list, max_length=20)
    context_strategy: Literal["sliding", "summarize", "off"] | None = None
    # Web search for this conversation; null = the user's default.
    web_search: Literal["off", "on", "auto"] | None = None
    # Thinking level for this conversation; null = the user's default.
    thinking: Thinking | None = None


class ConversationUpdate(Strict):
    title: str | None = Field(None, min_length=1, max_length=200)
    provider_id: str | None = None
    model: str | None = Field(None, max_length=500)
    system_prompt: str | None = Field(None, max_length=100_000)
    params: GenerationParams | None = None
    collection_ids: list[str] | None = Field(None, max_length=20)
    pinned: bool | None = None
    # null = the user's default strategy
    context_strategy: Literal["sliding", "summarize", "off"] | None = None
    web_search: Literal["off", "on", "auto"] | None = None
    thinking: Thinking | None = None


class Target(Strict):
    provider_id: str | None = None
    model: str | None = Field(None, max_length=500)
    params: GenerationParams | None = None


class WebOptions(Target):
    # Links (found in the message) to read; null = apply the user's default.
    urls: list[str] | None = Field(None, max_length=20)
    # A one-off override for this message; null = the conversation's search mode.
    web_search: bool | None = None
    # IANA time zone of the browser, used for dates in web-grounded answers.
    timezone: str | None = Field(None, max_length=64)


class SendMessage(WebOptions):
    content: str = Field("", max_length=1_000_000)
    attachments: list[str] = Field(default_factory=list, max_length=20)


class Regenerate(Target):
    message_id: str | None = None
    # A one-off override; null = the conversation's search mode.
    web_search: bool | None = None
    timezone: str | None = Field(None, max_length=64)


class EditMessage(WebOptions):
    content: str = Field(max_length=1_000_000)


class ActiveBranch(Strict):
    message_id: str = Field(min_length=1, max_length=64)


class MessageUpdate(Strict):
    pinned: bool


class PromptBody(Strict):
    title: str = Field(min_length=1, max_length=200)
    description: str = Field("", max_length=1000)
    category: str = Field("", max_length=80)
    content: str = Field(min_length=1, max_length=200_000)


class CollectionBody(Strict):
    name: str = Field(min_length=1, max_length=120)
    description: str = Field("", max_length=1000)


class CollectionUpdate(Strict):
    name: str | None = Field(None, min_length=1, max_length=120)
    description: str | None = Field(None, max_length=1000)


class SearchBody(Strict):
    query: str = Field(min_length=1, max_length=4000)
    top_k: int | None = Field(None, ge=1, le=50)


Language = Literal["en", "lt"]


class LoginBody(Strict):
    identifier: str = Field(max_length=254)
    password: str = Field(max_length=1000)
    remember: bool = False


class RegisterBody(Strict):
    email: str = Field(max_length=254)
    username: str = Field(max_length=64)
    password: str = Field(max_length=1000)
    password_confirm: str = Field(max_length=1000)
    language: Language | None = None
    setup_token: str | None = Field(None, max_length=500)
    remember: bool = False


class ProfileUpdate(Strict):
    email: str | None = Field(None, max_length=254)
    username: str | None = Field(None, max_length=64)
    language: Language | None = None
    current_password: str | None = Field(None, max_length=1000)


class PasswordChange(Strict):
    current_password: str = Field(max_length=1000)
    new_password: str = Field(max_length=1000)
    new_password_confirm: str = Field(max_length=1000)


class AccountDelete(Strict):
    password: str = Field(max_length=1000)


class AdminUserCreate(Strict):
    email: str = Field(max_length=254)
    username: str = Field(max_length=64)
    password: str | None = Field(None, max_length=1000)
    role: Literal["admin", "user"] = "user"


class AdminUserUpdate(Strict):
    role: Literal["admin", "user"] | None = None
    active: bool | None = None


class AdminSettings(Strict):
    registration_mode: Literal["open", "closed", "first_user_only"] | None = None
    provider_url_policy: Literal["any", "private", "custom"] | None = None
    provider_url_allowlist: list[str] | None = Field(None, max_length=100)


class ClearData(Strict):
    scope: Literal["conversations", "all"]
    confirm: Literal["DELETE"]


class UpdateSettings(Strict):
    enabled: bool
