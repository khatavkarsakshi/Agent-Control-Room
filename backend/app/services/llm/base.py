"""Provider-agnostic LLM primitives.

The agent runner speaks this small neutral format. Each provider adapter is
responsible for translating it to the vendor payload, so the execution loop
never contains provider-specific branching.
"""

from __future__ import annotations

import abc
import logging
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from app.core.config import settings
from app.core.retry import retry_call

logger = logging.getLogger(__name__)


class LLMError(Exception):
    """Base class for provider failures."""


class LLMConfigurationError(LLMError):
    """Missing API key, unsupported model, or invalid request (never retried)."""


class LLMProviderError(LLMError):
    """Upstream failure. ``retryable`` controls the exponential backoff loop."""

    def __init__(self, message: str, *, retryable: bool = False, status_code: int | None = None):
        super().__init__(message)
        self.retryable = retryable
        self.status_code = status_code


class LLMTimeoutError(LLMProviderError):
    def __init__(self, message: str = "LLM request timed out"):
        super().__init__(message, retryable=True)


@dataclass(frozen=True)
class ToolSpec:
    """JSON-schema description of a tool exposed to the model."""

    name: str
    description: str
    parameters: dict[str, Any]


@dataclass
class ToolCallRequest:
    """A tool invocation requested by the model."""

    id: str
    name: str
    arguments: dict[str, Any] = field(default_factory=dict)


@dataclass
class LLMResponse:
    content: str | None
    tool_calls: list[ToolCallRequest]
    tokens_input: int
    tokens_output: int
    finish_reason: str | None = None
    model: str | None = None
    attempts: int = 1


class LLMProvider(abc.ABC):
    """Base adapter. Subclasses implement :meth:`_complete` only."""

    name: str = "unknown"

    def __init__(self) -> None:
        self.last_attempts = 1

    def complete(
        self,
        *,
        model: str,
        messages: list[dict[str, Any]],
        tools: list[ToolSpec] | None = None,
        temperature: float = 0.0,
        max_tokens: int = 1024,
    ) -> LLMResponse:
        """Execute one completion, retrying transient failures with backoff."""
        self.last_attempts = 1

        def _attempt() -> LLMResponse:
            return self._complete(
                model=model,
                messages=messages,
                tools=tools,
                temperature=temperature,
                max_tokens=max_tokens,
            )

        def _on_retry(_attempt_number: int, _exc: Exception, _delay: float) -> None:
            self.last_attempts += 1

        response = retry_call(
            _attempt,
            retries=settings.llm_max_retries,
            base_delay=settings.llm_retry_base_delay_seconds,
            max_delay=settings.llm_retry_max_delay_seconds,
            retry_if=lambda exc: isinstance(exc, LLMProviderError) and exc.retryable,
            on_retry=_on_retry,
        )
        response.attempts = self.last_attempts
        return response

    @abc.abstractmethod
    def _complete(
        self,
        *,
        model: str,
        messages: list[dict[str, Any]],
        tools: list[ToolSpec] | None,
        temperature: float,
        max_tokens: int,
    ) -> LLMResponse:
        raise NotImplementedError


def assistant_message(response: LLMResponse) -> dict[str, Any]:
    """Convert an LLM response to the neutral message format."""
    return {
        "role": "assistant",
        "content": response.content or "",
        "tool_calls": [
            {"id": call.id, "name": call.name, "arguments": call.arguments}
            for call in response.tool_calls
        ],
    }


def tool_message(tool_call_id: str, name: str, content: str, *, is_error: bool = False) -> dict[str, Any]:
    return {
        "role": "tool",
        "tool_call_id": tool_call_id,
        "name": name,
        "content": content,
        "is_error": is_error,
    }


def as_price_decimal(value: Decimal | float | int) -> Decimal:
    return value if isinstance(value, Decimal) else Decimal(str(value))
