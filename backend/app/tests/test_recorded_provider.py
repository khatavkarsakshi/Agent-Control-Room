"""Offline replay provider tests: trace reconstruction and exhaustion behaviour."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

from app.models import RunStatus, SpanStatus, SpanType, Trace
from app.services.llm import LLMResponse, ToolCallRequest
from app.services.llm.base import LLMError
from app.services.llm.recorded_provider import (
    RecordedProvider,
    load_recorded_responses,
    recorded_factory,
)


def _llm_trace(
    db,
    run,
    *,
    content: str | None,
    tool_calls: list[dict] | None = None,
    tokens_input: int = 10,
    tokens_output: int = 5,
    offset_seconds: int = 0,
    model: str = "gpt-4o-mini",
) -> Trace:
    trace = Trace(
        run_id=run.id,
        span_name=f"llm.{model}",
        span_type=SpanType.LLM_CALL,
        status=SpanStatus.OK,
        trace_id=uuid4().hex,
        span_id=uuid4().hex[:16],
        timestamp=datetime.now(timezone.utc) + timedelta(seconds=offset_seconds),
        model_name=model,
        tokens_input=tokens_input,
        tokens_output=tokens_output,
        output_data={
            "content": content,
            "tool_calls": tool_calls or [],
            "finish_reason": "stop" if content else "tool_calls",
        },
    )
    db.add(trace)
    return trace


def test_load_recorded_responses_orders_and_parses_spans(db, make_run):
    run = make_run(status=RunStatus.COMPLETED, output_response="done")
    # Insert out of order: loading must follow trace timestamps, not row ids.
    _llm_trace(db, run, content=None, offset_seconds=0, tool_calls=[{"id": "call_1", "name": "calculator", "arguments": {"expression": "2+2"}}])
    _llm_trace(db, run, content="The answer is 4.", offset_seconds=1, tokens_input=20, tokens_output=8)
    db.commit()

    responses = load_recorded_responses(db, run)

    assert len(responses) == 2
    first, second = responses
    assert first.content is None
    assert first.finish_reason == "tool_calls"
    assert first.tool_calls[0].id == "call_1"
    assert first.tool_calls[0].name == "calculator"
    assert first.tool_calls[0].arguments == {"expression": "2+2"}
    assert first.tokens_input == 10
    assert first.tokens_output == 5
    assert first.model == "gpt-4o-mini"
    assert second.content == "The answer is 4."
    assert second.tool_calls == []
    assert second.tokens_input == 20
    assert second.tokens_output == 8


def test_load_recorded_responses_ignores_non_llm_spans_and_bad_calls(db, make_run):
    run = make_run(status=RunStatus.COMPLETED)
    _llm_trace(
        db,
        run,
        content=None,
        tool_calls=[
            "not-a-dict",
            {"id": "missing-name"},
            {"id": "call_ok", "name": "current_time", "arguments": "not-a-dict"},
        ],
    )
    db.add(
        Trace(
            run_id=run.id,
            span_name="agent.run",
            span_type=SpanType.DECISION,
            trace_id=uuid4().hex,
            span_id=uuid4().hex[:16],
            output_data={"content": "root span"},
        )
    )
    db.commit()

    responses = load_recorded_responses(db, run)

    assert len(responses) == 1
    assert [call.id for call in responses[0].tool_calls] == ["call_ok"]
    assert responses[0].tool_calls[0].arguments == {}


def test_recorded_provider_returns_responses_in_order_and_exhausts(db, make_run):
    run = make_run(status=RunStatus.COMPLETED)
    db.commit()
    responses = load_recorded_responses(db, run)
    assert responses == []

    first = LLMResponse(content="one", tool_calls=[], tokens_input=1, tokens_output=1)
    second = LLMResponse(
        content=None,
        tool_calls=[ToolCallRequest(id="c1", name="calculator", arguments={})],
        tokens_input=2,
        tokens_output=2,
    )
    provider = RecordedProvider([first, second])

    assert provider.remaining == 2
    assert provider.complete(model="m", messages=[], tools=None).content == "one"
    assert provider.consumed == 1
    assert provider.remaining == 1
    assert provider.complete(model="m", messages=[], tools=None).tool_calls[0].name == "calculator"
    assert provider.remaining == 0

    with pytest.raises(LLMError) as excinfo:
        provider.complete(model="m", messages=[], tools=None)
    assert "exhausted" in str(excinfo.value).lower()


def test_recorded_factory_requires_a_recording(db, make_run):
    run = make_run(status=RunStatus.COMPLETED)

    with pytest.raises(LLMError):
        recorded_factory(db, run)


def test_recorded_factory_resolves_provider(db, make_run):
    run = make_run(status=RunStatus.COMPLETED)
    _llm_trace(db, run, content="recorded")
    db.commit()

    factory = recorded_factory(db, run)
    resolved = factory("gpt-4o-mini")

    assert isinstance(resolved.provider, RecordedProvider)
    assert resolved.model == "gpt-4o-mini"
    assert resolved.provider.source_run_id == run.id
    assert resolved.provider.complete(model="gpt-4o-mini", messages=[]).content == "recorded"
