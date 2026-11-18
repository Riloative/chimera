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

    def __init__(
        self,
        message: str,
        code: str = "provider_error",
        status: int | None = None,
        *,
        name: str | None = None,
        detail: str | None = None,
    ):
        super().__init__(message)
        self.message = message
        self.code = code
        self.status = status
        # Interpolation parameters for localized messages in the UI. ``detail``
        # is server-supplied text and is shown verbatim (never translated).
        self.params: dict[str, Any] = {k: v for k, v in {"name": name, "status": status, "detail": detail}.items() if v}

    def to_dict(self) -> dict[str, Any]:
        return {"message": self.message, "code": self.code, "status": self.status, "params": self.params}


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
    error_params: dict[str, Any] = field(default_factory=dict)
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
    # A short utility answer (a title, search queries, a summary): ask the
    # model not to reason first where the server supports it. Reasoning models
    # otherwise spend the whole small token budget thinking and answer nothing.
    quick: bool = False


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

    def quick_body(self) -> dict[str, Any]:
        """Extra request fields for ``ChatRequest.quick`` (none by default)."""
        return {}

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

    # -- optional token counting (context management) --------------------
    async def tokenize_count(self, text: str) -> int | None:
        """Exact token count of ``text`` with the model's tokenizer, or ``None``
        when the server can't tell us (the caller then estimates)."""
        return None

    async def prompt_tokens(self, request: ChatRequest) -> int | None:
        """Exact token count of the whole rendered chat prompt, or ``None``."""
        return None


def translate_http_error(exc: Exception, provider_name: str) -> ProviderError:
    """Map transport exceptions to friendly, actionable messages."""
    n = provider_name
    if isinstance(exc, ProviderError):
        return exc
    if isinstance(exc, httpx.ConnectError):
        return ProviderError(
            f"Could not connect to {n}. Is the server running and is the URL correct?", code="connection", name=n
        )
    if isinstance(exc, (httpx.ReadTimeout, httpx.WriteTimeout, httpx.PoolTimeout)):
        return ProviderError(f"{n} did not respond within the configured timeout.", code="timeout", name=n)
    if isinstance(exc, httpx.ConnectTimeout):
        return ProviderError(f"Timed out connecting to {n}.", code="connect_timeout", name=n)
    if isinstance(exc, httpx.RemoteProtocolError):
        return ProviderError(f"{n} closed the connection unexpectedly.", code="connection_closed", name=n)
    if isinstance(exc, httpx.UnsupportedProtocol | httpx.InvalidURL):
        return ProviderError(f"Invalid server URL for {n}.", code="config", name=n)
    if isinstance(exc, httpx.HTTPError):
        return ProviderError(f"Request to {n} failed: {exc}", code="request_failed", name=n, detail=str(exc))
    return ProviderError(f"Unexpected error from {n}: {exc}", code="internal", name=n, detail=str(exc))


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
    err_obj = None
    try:
        parsed = json.loads(text)
        err_obj = parsed.get("error", parsed) if isinstance(parsed, dict) else None
    except (ValueError, AttributeError):
        pass
    if is_context_overflow(err_obj, message):
        return context_overflow_error(err_obj if isinstance(err_obj, dict) else {}, provider_name, message)
    if status in (401, 403):
        code = "auth"
        message = message or "Authentication failed — check the API key."
    elif status == 404:
        code = "not_found"
    elif status == 503:
        code = "unavailable"
        message = message or "The server is not ready (it may still be loading the model)."
    prefix = f"{provider_name} returned HTTP {status}"
    return ProviderError(
        f"{prefix}: {message}" if message else prefix, code=code, status=status, name=provider_name, detail=message or None
    )


def is_context_overflow(err: object, message: str = "") -> bool:
    """llama-server answers ``exceed_context_size_error`` (HTTP 400) when the
    prompt doesn't fit the slot's context; other servers use similar wording."""
    if isinstance(err, dict) and err.get("type") in ("exceed_context_size_error", "context_length_exceeded"):
        return True
    if isinstance(err, dict) and err.get("code") == "context_length_exceeded":
        return True
    text = (message or "").lower()
    return any(p in text for p in ("exceeds the available context size", "larger than the max context size",
                                    "maximum context length", "context length exceeded", "context window"))


def context_overflow_error(err: dict, provider_name: str, message: str = "") -> ProviderError:
    detail = str(err.get("message") or message or "")
    exc = ProviderError(
        f"{provider_name}: the request is too long for the model's context window.",
        code="context_exceeded", status=400, name=provider_name, detail=detail or None,
    )
    for key in ("n_prompt_tokens", "n_ctx"):
        if isinstance(err.get(key), int):
            exc.params[key] = err[key]
    return exc
