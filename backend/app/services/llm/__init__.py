"""Provider resolution and exports."""

from __future__ import annotations

import logging
from dataclasses import dataclass

from app.core.config import settings
from app.services.llm.base import (
    LLMConfigurationError,
    LLMError,
    LLMProvider,
    LLMProviderError,
    LLMResponse,
    LLMTimeoutError,
    ToolCallRequest,
    ToolSpec,
    assistant_message,
    tool_message,
)
from app.services.llm.anthropic_provider import AnthropicProvider
from app.services.llm.local_provider import LocalProvider
from app.services.llm.openai_provider import OpenAIProvider

logger = logging.getLogger(__name__)

_ANTHROPIC_PREFIXES = ("claude-", "claude/")
_PROVIDER_PREFIXES = {
    "openai/": "openai",
    "anthropic/": "anthropic",
    "local/": "local",
    "ollama/": "local",
}


@dataclass(frozen=True)
class ResolvedProvider:
    provider: LLMProvider
    model: str


def _strip_prefix(model: str) -> tuple[str | None, str]:
    lowered = model.lower()
    for prefix, provider_name in _PROVIDER_PREFIXES.items():
        if lowered.startswith(prefix):
            return provider_name, model[len(prefix):]
    return None, model


def resolve_provider(model: str) -> ResolvedProvider:
    """Pick a provider for ``model`` and strip any explicit provider prefix."""
    explicit, clean_model = _strip_prefix(model)
    clean_model = clean_model.strip()
    if not clean_model:
        raise LLMConfigurationError("model name must not be empty")

    lowered = clean_model.lower()

    if explicit == "anthropic" or lowered.startswith(_ANTHROPIC_PREFIXES):
        return ResolvedProvider(AnthropicProvider(), clean_model)
    if explicit == "local" or any(lowered.startswith(p) for p in settings.local_model_prefix_list):
        return ResolvedProvider(LocalProvider(), clean_model)
    if explicit == "openai":
        return ResolvedProvider(OpenAIProvider(), clean_model)

    # Default to OpenAI-compatible routing, which also covers OpenRouter and
    # Azure-style deployments configured through OPENAI_BASE_URL.
    return ResolvedProvider(OpenAIProvider(), clean_model)


__all__ = [
    "AnthropicProvider",
    "LLMConfigurationError",
    "LLMError",
    "LLMProvider",
    "LLMProviderError",
    "LLMResponse",
    "LLMTimeoutError",
    "LocalProvider",
    "OpenAIProvider",
    "ResolvedProvider",
    "ToolCallRequest",
    "ToolSpec",
    "assistant_message",
    "resolve_provider",
    "tool_message",
]
