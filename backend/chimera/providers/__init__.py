from .base import (  # noqa: F401
    ALL_PARAMS,
    PROVIDER_KINDS,
    ChatMessage,
    ChatRequest,
    HealthReport,
    ModelInfo,
    Provider,
    ProviderConfig,
    ProviderError,
    StreamEvent,
)
from .ollama import OllamaProvider
from .openai_compat import LlamaCppProvider, OpenAICompatibleProvider

PROVIDER_CLASSES: dict[str, type[Provider]] = {
    "llamacpp": LlamaCppProvider,
    "openai": OpenAICompatibleProvider,
    "ollama": OllamaProvider,
}

DEFAULTS = {
    "llamacpp": {"base_url": "http://127.0.0.1:8080", "api_path": "/v1"},
    "openai": {"base_url": "http://127.0.0.1:1234", "api_path": "/v1"},
    "ollama": {"base_url": "http://127.0.0.1:11434", "api_path": ""},
}


def build_provider(config: ProviderConfig, client) -> Provider:
    try:
        cls = PROVIDER_CLASSES[config.kind]
    except KeyError as exc:
        raise ProviderError(f"Unknown provider kind: {config.kind}", code="config") from exc
    return cls(config, client)
