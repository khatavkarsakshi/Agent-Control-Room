"""OpenAI Chat Completions adapter (also used for OpenAI-compatible servers)."""

from __future__ import annotations

import json
import logging
import uuid
from typing import Any

import openai
from openai import OpenAI

from app.core.config import settings
from app.services.llm.base import (
    LLMConfigurationError,
    LLMProvider,
    LLMProviderError,
    LLMResponse,
    LLMTimeoutError,
    ToolCallRequest,
    ToolSpec,
)

logger = logging.getLogger(__name__)


class OpenAIProvider(LLMProvider):
    name = "openai"

    def __init__(
        self,
        *,
        api_key: str | None = None,
        base_url: str | None = None,
        provider_name: str = "openai",
    ) -> None:
        key = api_key if api_key is not None else settings.openai_api_key
        if not key:
            raise LLMConfigurationError(
                "OPENAI_API_KEY is not configured; cannot execute OpenAI models"
            )
        self.name = provider_name
        self._client = OpenAI(
            api_key=key,
            base_url=base_url or settings.openai_base_url,
            timeout=settings.llm_timeout_seconds,
            # Retries are handled by LLMProvider.complete with our own backoff.
            max_retries=0,
        )

    # -- payload conversion -------------------------------------------
    @staticmethod
    def _convert_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        converted: list[dict[str, Any]] = []
        for message in messages:
            role = message.get("role")
            if role == "system":
                converted.append({"role": "system", "content": message.get("content") or ""})
            elif role == "user":
                converted.append({"role": "user", "content": message.get("content") or ""})
            elif role == "assistant":
                payload: dict[str, Any] = {
                    "role": "assistant",
                    "content": message.get("content") or None,
                }
                tool_calls = message.get("tool_calls") or []
                if tool_calls:
                    payload["tool_calls"] = [
                        {
                            "id": call.get("id") or f"call_{uuid.uuid4().hex[:12]}",
                            "type": "function",
                            "function": {
                                "name": call["name"],
                                "arguments": json.dumps(call.get("arguments") or {}),
                            },
                        }
                        for call in tool_calls
                    ]
                converted.append(payload)
            elif role == "tool":
                converted.append(
                    {
                        "role": "tool",
                        "tool_call_id": message.get("tool_call_id") or "",
                        "content": message.get("content") or "",
                    }
                )
            else:  # pragma: no cover - guarded by the runner
                raise LLMConfigurationError(f"Unsupported message role {role!r}")
        return converted

    @staticmethod
    def _convert_tools(tools: list[ToolSpec] | None) -> list[dict[str, Any]] | None:
        if not tools:
            return None
        return [
            {
                "type": "function",
                "function": {
                    "name": tool.name,
                    "description": tool.description,
                    "parameters": tool.parameters
                    or {"type": "object", "properties": {}, "additionalProperties": False},
                },
            }
            for tool in tools
        ]

    @staticmethod
    def _content_to_text(content: Any) -> str | None:
        if content is None:
            return None
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts = []
            for item in content:
                if isinstance(item, dict) and item.get("type") == "text":
                    parts.append(str(item.get("text", "")))
                elif isinstance(item, str):
                    parts.append(item)
            return "".join(parts) or None
        return str(content)

    def _translate_error(self, exc: Exception) -> Exception:
        """Map SDK exceptions to our error taxonomy without leaking secrets."""
        if isinstance(exc, openai.AuthenticationError):
            return LLMConfigurationError("OpenAI rejected the API key")
        if isinstance(exc, openai.PermissionDeniedError):
            return LLMConfigurationError("OpenAI denied access to this model")
        if isinstance(exc, openai.NotFoundError):
            return LLMConfigurationError(f"OpenAI model not found ({self.name})")
        if isinstance(exc, openai.BadRequestError):
            return LLMProviderError(f"OpenAI rejected the request: {exc}", retryable=False)
        if isinstance(exc, openai.RateLimitError):
            return LLMProviderError("OpenAI rate limit exceeded", retryable=True, status_code=429)
        if isinstance(exc, (openai.APITimeoutError, openai.APIConnectionError)):
            return LLMTimeoutError(f"OpenAI connection failed: {exc}")
        if isinstance(exc, openai.APIStatusError):
            retryable = exc.status_code >= 500 or exc.status_code == 429
            return LLMProviderError(
                f"OpenAI API error {exc.status_code}", retryable=retryable, status_code=exc.status_code
            )
        return LLMProviderError(f"OpenAI call failed: {exc}", retryable=False)

    # -- completion ----------------------------------------------------
    def _complete(
        self,
        *,
        model: str,
        messages: list[dict[str, Any]],
        tools: list[ToolSpec] | None,
        temperature: float,
        max_tokens: int,
    ) -> LLMResponse:
        payload: dict[str, Any] = {
            "model": model,
            "messages": self._convert_messages(messages),
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        converted_tools = self._convert_tools(tools)
        if converted_tools:
            payload["tools"] = converted_tools
            payload["tool_choice"] = "auto"

        try:
            response = self._client.chat.completions.create(**payload)
        except Exception as exc:  # noqa: BLE001 - translated below
            raise self._translate_error(exc) from exc

        if not response.choices:
            raise LLMProviderError("OpenAI returned no choices", retryable=True)

        choice = response.choices[0]
        message = choice.message

        tool_calls: list[ToolCallRequest] = []
        for call in message.tool_calls or []:
            raw_arguments = call.function.arguments or "{}"
            try:
                parsed = json.loads(raw_arguments)
            except json.JSONDecodeError:
                logger.warning("Model returned non-JSON tool arguments: %r", raw_arguments[:200])
                parsed = {"_raw_arguments": raw_arguments}
            if not isinstance(parsed, dict):
                parsed = {"value": parsed}
            tool_calls.append(
                ToolCallRequest(
                    id=call.id or f"call_{uuid.uuid4().hex[:12]}",
                    name=call.function.name,
                    arguments=parsed,
                )
            )

        usage = response.usage
        return LLMResponse(
            content=self._content_to_text(message.content),
            tool_calls=tool_calls,
            tokens_input=int(getattr(usage, "prompt_tokens", 0) or 0),
            tokens_output=int(getattr(usage, "completion_tokens", 0) or 0),
            finish_reason=choice.finish_reason,
            model=response.model or model,
        )
