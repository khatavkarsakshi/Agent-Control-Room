"""Anthropic Messages API adapter."""

from __future__ import annotations

import logging
from typing import Any

import anthropic
from anthropic import Anthropic

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

# Anthropic requires every message to be non-empty.
_EMPTY_TEXT = "(empty)"


class AnthropicProvider(LLMProvider):
    name = "anthropic"

    def __init__(self, *, api_key: str | None = None) -> None:
        key = api_key if api_key is not None else settings.anthropic_api_key
        if not key:
            raise LLMConfigurationError(
                "ANTHROPIC_API_KEY is not configured; cannot execute Claude models"
            )
        self._client = Anthropic(
            api_key=key,
            timeout=settings.llm_timeout_seconds,
            max_retries=0,
        )

    # -- payload conversion -------------------------------------------
    @staticmethod
    def _convert_messages(messages: list[dict[str, Any]]) -> tuple[str | None, list[dict[str, Any]]]:
        system_parts: list[str] = []
        converted: list[dict[str, Any]] = []

        for message in messages:
            role = message.get("role")
            if role == "system":
                content = message.get("content")
                if content:
                    system_parts.append(content)
                continue

            if role == "user":
                converted.append(
                    {"role": "user", "content": message.get("content") or _EMPTY_TEXT}
                )
                continue

            if role == "assistant":
                blocks: list[dict[str, Any]] = []
                if message.get("content"):
                    blocks.append({"type": "text", "text": message["content"]})
                for call in message.get("tool_calls") or []:
                    blocks.append(
                        {
                            "type": "tool_use",
                            "id": call.get("id"),
                            "name": call["name"],
                            "input": call.get("arguments") or {},
                        }
                    )
                if not blocks:
                    blocks.append({"type": "text", "text": _EMPTY_TEXT})
                converted.append({"role": "assistant", "content": blocks})
                continue

            if role == "tool":
                block: dict[str, Any] = {
                    "type": "tool_result",
                    "tool_use_id": message.get("tool_call_id") or "",
                    "content": message.get("content") or "",
                }
                if message.get("is_error"):
                    block["is_error"] = True
                # Consecutive tool results must be merged into one user turn.
                if converted and converted[-1]["role"] == "user" and isinstance(
                    converted[-1]["content"], list
                ):
                    converted[-1]["content"].append(block)
                else:
                    converted.append({"role": "user", "content": [block]})
                continue

            raise LLMConfigurationError(f"Unsupported message role {role!r}")

        # The Messages API rejects conversations that do not start with a user turn.
        if not converted or converted[0]["role"] != "user":
            converted.insert(0, {"role": "user", "content": _EMPTY_TEXT})

        # And it rejects consecutive user turns, which can happen with tool errors.
        merged: list[dict[str, Any]] = []
        for message in converted:
            if merged and merged[-1]["role"] == message["role"] == "user":
                previous = merged[-1]["content"]
                current = message["content"]
                if isinstance(previous, str) and isinstance(current, str):
                    merged[-1]["content"] = f"{previous}\n\n{current}"
                else:
                    merged[-1]["content"] = _as_blocks(previous) + _as_blocks(current)
                continue
            merged.append(message)

        system = "\n\n".join(system_parts) if system_parts else None
        return system, merged

    @staticmethod
    def _convert_tools(tools: list[ToolSpec] | None) -> list[dict[str, Any]] | None:
        if not tools:
            return None
        return [
            {
                "name": tool.name,
                "description": tool.description,
                "input_schema": tool.parameters
                or {"type": "object", "properties": {}, "additionalProperties": False},
            }
            for tool in tools
        ]

    def _translate_error(self, exc: Exception) -> Exception:
        if isinstance(exc, anthropic.AuthenticationError):
            return LLMConfigurationError("Anthropic rejected the API key")
        if isinstance(exc, anthropic.PermissionDeniedError):
            return LLMConfigurationError("Anthropic denied access to this model")
        if isinstance(exc, anthropic.NotFoundError):
            return LLMConfigurationError("Anthropic model not found")
        if isinstance(exc, anthropic.BadRequestError):
            return LLMProviderError(f"Anthropic rejected the request: {exc}", retryable=False)
        if isinstance(exc, anthropic.RateLimitError):
            return LLMProviderError("Anthropic rate limit exceeded", retryable=True, status_code=429)
        if isinstance(exc, (anthropic.APITimeoutError, anthropic.APIConnectionError)):
            return LLMTimeoutError(f"Anthropic connection failed: {exc}")
        if isinstance(exc, anthropic.APIStatusError):
            retryable = exc.status_code >= 500 or exc.status_code == 429
            return LLMProviderError(
                f"Anthropic API error {exc.status_code}",
                retryable=retryable,
                status_code=exc.status_code,
            )
        return LLMProviderError(f"Anthropic call failed: {exc}", retryable=False)

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
        system, converted = self._convert_messages(messages)
        payload: dict[str, Any] = {
            "model": model,
            "messages": converted,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if system:
            payload["system"] = system
        converted_tools = self._convert_tools(tools)
        if converted_tools:
            payload["tools"] = converted_tools

        try:
            response = self._client.messages.create(**payload)
        except Exception as exc:  # noqa: BLE001 - translated below
            raise self._translate_error(exc) from exc

        text_parts: list[str] = []
        tool_calls: list[ToolCallRequest] = []
        for block in response.content:
            if block.type == "text":
                text_parts.append(block.text)
            elif block.type == "tool_use":
                tool_calls.append(
                    ToolCallRequest(
                        id=block.id,
                        name=block.name,
                        arguments=block.input if isinstance(block.input, dict) else {"value": block.input},
                    )
                )

        usage = getattr(response, "usage", None)
        return LLMResponse(
            content="\n".join(text_parts).strip() or None,
            tool_calls=tool_calls,
            tokens_input=int(getattr(usage, "input_tokens", 0) or 0),
            tokens_output=int(getattr(usage, "output_tokens", 0) or 0),
            finish_reason=getattr(response, "stop_reason", None),
            model=getattr(response, "model", model),
        )


def _as_blocks(content: Any) -> list[dict[str, Any]]:
    if isinstance(content, list):
        return content
    return [{"type": "text", "text": str(content) or _EMPTY_TEXT}]
