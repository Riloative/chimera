"""Provider abstraction.

Every inference backend (llama-server, generic OpenAI-compatible servers,
Ollama, ...) is wrapped in a :class:`Provider`. The rest of the application only
sees normalized types defined here: :class:`ModelInfo`, :class:`StreamEvent`,
:class:`HealthReport` and :class:`ProviderError`. UI code never parses a
provider-specific payload.
"""

from __future__ import annotations

import abc
from dataclasses import asdict, dataclass, field
from typing import Any, AsyncIterator, Literal

import httpx

ProviderKind = Literal["llamacpp", "openai", "ollama"]
PROVIDER_KINDS: tuple[str, ...] = ("llamacpp", "openai", "ollama")

# Sampling parameters understood by the app. Each provider declares the
# subset it actually forwards to its server (see ``supported_params``).
ALL_PARAMS = (
    "temperature",
    "top_p",
    "top_k",
    "min_p",
    "max_tokens",
    "stop",
    "repeat_penalty",
    "presence_penalty",
    "frequency_penalty",
    "seed",
)


class ProviderError(Exception):
    """A normalized, user-presentable provider failure."""

    def __init__(self, message: str, code: str = "provider_error", status: int | None = None):
        super().__init__(message)
        self.message = message
        self.code = code
        self.status = status

    def to_dict(self) -> dict[str, Any]:
        return {"message": self.message, "code": self.code, "status": self.status}


@dataclass
class ProviderConfig:
    id: str
    name: str
    kind: str
    base_url: str
    api_path: str = "/v1"
    api_key: str | None = None
    auth_header: str | None = None
    timeout: float = 300.0
    enabled: bool = True
    vision: str = "auto"  # auto | on | off


@dataclass
class ModelInfo:
    id: str
    provider_id: str
    name: str | None = None
    context_length: int | None = None
    vision: bool | None = None
    loaded: bool | None = None
    source: str = "discovered"  # discovered | manual
    meta: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class HealthReport:
    ok: bool
    latency_ms: float | None = None
    error: str | None = None
    error_code: str | None = None
    models: list[ModelInfo] = field(default_factory=list)
    models_ok: bool = False
    capabilities: dict[str, Any] = field(default_factory=dict)
    checks: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class ChatMessage:
    role: str  # system | user | assistant
    content: str
    images: list[tuple[str, str]] = field(default_factory=list)  # (mime, base64)


@dataclass
class ChatRequest:
    model: str
    messages: list[ChatMessage]
    params: dict[str, Any] = field(default_factory=dict)


@dataclass
class StreamEvent:
    """One normalized streaming event.

    ``type`` is one of ``content``, ``reasoning``, ``usage`` or ``done``.
    """

    type: str
    text: str = ""
    finish_reason: str | None = None
    usage: dict[str, Any] = field(default_factory=dict)


class Provider(abc.ABC):
    kind: str = ""
    supported_params: tuple[str, ...] = ()

    def __init__(self, config: ProviderConfig, client: httpx.AsyncClient):
        self.config = config
        self.client = client

    # -- helpers ------------------------------------------------------------
    @property
    def root_url(self) -> str:
        return self.config.base_url.rstrip("/")

    def headers(self) -> dict[str, str]:
        headers = {"Accept": "application/json"}
        key = (self.config.api_key or "").strip()
        if key:
            header = (self.config.auth_header or "").strip() or "Authorization"
            if header.lower() == "authorization" and not key.lower().startswith(("bearer ", "basic ")):
                key = f"Bearer {key}"
            headers[header] = key
        return headers

    def timeout(self, read: float | None = None) -> httpx.Timeout:
        total = max(1.0, float(self.config.timeout or 300))
        return httpx.Timeout(connect=min(10.0, total), read=read or total, write=30.0, pool=10.0)

    def vision_override(self) -> bool | None:
        if self.config.vision == "on":
            return True
        if self.config.vision == "off":
            return False
        return None

    def filter_params(self, params: dict[str, Any]) -> dict[str, Any]:
        return {k: v for k, v in params.items() if k in self.supported_params and v is not None and v != []}

    # -- interface ----------------------------------------------------------
    @abc.abstractmethod
    async def list_models(self) -> list[ModelInfo]: ...

    @abc.abstractmethod
    async def check(self) -> HealthReport: ...

    @abc.abstractmethod
    def stream_chat(self, request: ChatRequest) -> AsyncIterator[StreamEvent]: ...

    async def complete(self, request: ChatRequest) -> str:
        """Non-streaming convenience built on the stream (used for titles)."""
        parts: list[str] = []
        async for event in self.stream_chat(request):
            if event.type == "content":
                parts.append(event.text)
        return "".join(parts)

    async def embed(self, model: str, texts: list[str]) -> list[list[float]]:
        raise ProviderError("This provider does not support embeddings.", code="unsupported")


def translate_http_error(exc: Exception, provider_name: str) -> ProviderError:
    """Map transport exceptions to friendly, actionable messages."""
    if isinstance(exc, ProviderError):
        return exc
    if isinstance(exc, httpx.ConnectError):
        return ProviderError(
            f"Could not connect to {provider_name}. Is the server running and is the URL correct?",
            code="connection",
        )
    if isinstance(exc, (httpx.ReadTimeout, httpx.WriteTimeout, httpx.PoolTimeout)):
        return ProviderError(
            f"{provider_name} did not respond within the configured timeout.", code="timeout"
        )
    if isinstance(exc, httpx.ConnectTimeout):
        return ProviderError(f"Timed out connecting to {provider_name}.", code="timeout")
    if isinstance(exc, httpx.RemoteProtocolError):
        return ProviderError(
            f"{provider_name} closed the connection unexpectedly.", code="connection"
        )
    if isinstance(exc, httpx.UnsupportedProtocol | httpx.InvalidURL):
        return ProviderError(f"Invalid server URL for {provider_name}.", code="config")
    if isinstance(exc, httpx.HTTPError):
        return ProviderError(f"Request to {provider_name} failed: {exc}", code="connection")
    return ProviderError(f"Unexpected error from {provider_name}: {exc}", code="internal")


def error_from_response(status: int, body: bytes | str, provider_name: str) -> ProviderError:
    """Extract the most useful message from an error response body."""
    import json

    text = body.decode("utf-8", "replace") if isinstance(body, bytes) else body
    message = ""
    try:
        data = json.loads(text)
        err = data.get("error", data) if isinstance(data, dict) else data
        if isinstance(err, dict):
            message = str(err.get("message") or err.get("detail") or "")
        elif isinstance(err, str):
            message = err
    except (ValueError, AttributeError):
        message = text.strip()[:300]
    code = "http"
    if status in (401, 403):
        code = "auth"
        message = message or "Authentication failed — check the API key."
    elif status == 404:
        code = "not_found"
    elif status == 503:
        code = "unavailable"
        message = message or "The server is not ready (it may still be loading the model)."
    prefix = f"{provider_name} returned HTTP {status}"
    return ProviderError(f"{prefix}: {message}" if message else prefix, code=code, status=status)
