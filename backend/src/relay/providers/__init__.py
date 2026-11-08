"""Provider interfaces, adapters, and mock implementations."""

from __future__ import annotations

from typing import TYPE_CHECKING

from relay.providers.anthropic import AnthropicProvider
from relay.providers.base import (
    ChatCompletionChoice,
    ChatCompletionChunk,
    ChatCompletionRequest,
    ChatCompletionResponse,
    ChatMessage,
    DeltaMessage,
    Provider,
    ProviderResponse,
    RelayProviderError,
    StreamingChoice,
    TokenUsage,
)
from relay.providers.gemini import GeminiProvider
from relay.providers.mock import (
    MockChaosConfig,
    MockProvider,
    get_all_providers,
    get_provider,
    register_provider,
)
from relay.providers.openai import OpenAIProvider

if TYPE_CHECKING:
    from relay.config import Settings


def init_real_providers(settings: Settings | None = None) -> None:
    """Registers real provider adapters if their API keys are configured."""
    from relay.config import get_settings

    s = settings or get_settings()
    if s.openai_api_key:
        register_provider("openai", OpenAIProvider(api_key=s.openai_api_key))
    if s.anthropic_api_key:
        register_provider("anthropic", AnthropicProvider(api_key=s.anthropic_api_key))
    if s.gemini_api_key:
        register_provider("gemini", GeminiProvider(api_key=s.gemini_api_key))


__all__ = [
    "AnthropicProvider",
    "ChatCompletionChoice",
    "ChatCompletionChunk",
    "ChatCompletionRequest",
    "ChatCompletionResponse",
    "ChatMessage",
    "DeltaMessage",
    "GeminiProvider",
    "MockChaosConfig",
    "MockProvider",
    "OpenAIProvider",
    "Provider",
    "ProviderResponse",
    "RelayProviderError",
    "StreamingChoice",
    "TokenUsage",
    "get_all_providers",
    "get_provider",
    "init_real_providers",
    "register_provider",
]
