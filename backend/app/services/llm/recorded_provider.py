"""Offline replay provider.

Replays the LLM calls captured in an existing run's trace. The agent loop still
runs for real (prompt assembly, tool execution, span collection, accounting), but
no provider request is made and no tokens are billed. This makes offline replay a
deterministic regression harness for prompt/tool changes.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Run, SpanType, Trace
from app.services.llm import ResolvedProvider
from app.services.llm.base import (
    LLMError,
    LLMProvider,
    LLMResponse,
    ToolCallRequest,
    ToolSpec,
)

logger = logging.getLogger(__name__)


class RecordedProvider(LLMProvider):
    """Return the recorded responses in order; fail once they are exhausted."""

    name = "recorded"

    def __init__(self, responses: Sequence[LLMResponse], *, source_run_id: object | None = None) -> None:
        super().__init__()
        self._responses = list(responses)
        self._index = 0
        self.source_run_id = source_run_id

    @property
    def consumed(self) -> int:
        return self._index

    @property
    def remaining(self) -> int:
        return len(self._responses) - self._index

    def _complete(
        self,
        *,
        model: str,
        messages: list[dict[str, Any]],
        tools: list[ToolSpec] | None,
        temperature: float,
        max_tokens: int,
    ) -> LLMResponse:
        if self._index >= len(self._responses):
            raise LLMError(
                "Offline replay exhausted the recorded LLM responses. The agent loop "
                "diverged from the original run (prompt, tools or model changed)."
            )
        response = self._responses[self._index]
        self._index += 1
        return response


def load_recorded_responses(db: Session, run: Run) -> list[LLMResponse]:
    """Rebuild LLM responses from the run's ``llm_call`` spans, in order."""
    traces = db.scalars(
        select(Trace)
        .where(Trace.run_id == run.id, Trace.span_type == SpanType.LLM_CALL)
        .order_by(Trace.timestamp)
    ).all()

    responses: list[LLMResponse] = []
    for trace in traces:
        output = trace.output_data or {}
        content = output.get("content")
        tool_calls: list[ToolCallRequest] = []
        raw_calls = output.get("tool_calls")
        if isinstance(raw_calls, list):
            for index, call in enumerate(raw_calls):
                if not isinstance(call, dict) or not isinstance(call.get("name"), str):
                    continue
                arguments = call.get("arguments")
                tool_calls.append(
                    ToolCallRequest(
                        id=str(call.get("id") or f"recorded-{trace.id}-{index}"),
                        name=call["name"],
                        arguments=arguments if isinstance(arguments, dict) else {},
                    )
                )
        responses.append(
            LLMResponse(
                content=content if isinstance(content, str) else None,
                tool_calls=tool_calls,
                tokens_input=int(trace.tokens_input or 0),
                tokens_output=int(trace.tokens_output or 0),
                finish_reason=output.get("finish_reason") if isinstance(output.get("finish_reason"), str) else None,
                model=trace.model_name,
            )
        )
    return responses


class RecordedProviderFactory:
    """``provider_factory`` compatible with :class:`AgentRunner`."""

    def __init__(self, provider: RecordedProvider) -> None:
        self.provider = provider

    def __call__(self, model: str) -> ResolvedProvider:
        return ResolvedProvider(provider=self.provider, model=model)


def recorded_factory(db: Session, run: Run) -> RecordedProviderFactory:
    """Build a factory from the original run, raising if nothing was recorded."""
    responses = load_recorded_responses(db, run)
    if not responses:
        raise LLMError("The original run has no recorded LLM calls to replay offline.")
    return RecordedProviderFactory(
        RecordedProvider(responses, source_run_id=run.id)
    )
