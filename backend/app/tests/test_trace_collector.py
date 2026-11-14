"""Trace collector persistence tests (OTel disabled, PostgreSQL-compatible rows)."""

from __future__ import annotations

from decimal import Decimal

import pytest
from sqlalchemy import select

from app.models import SpanStatus, SpanType, ToolCall, Trace
from app.services.trace_collector import TraceCollector


def _traces(db, run_id):
    db.expire_all()
    return db.scalars(select(Trace).where(Trace.run_id == run_id)).all()


def test_collector_builds_span_tree_and_persists_tool_calls(db, make_run):
    run = make_run()
    collector = TraceCollector(db, run)

    with collector.run_span("agent.run", input_data={"input_prompt": "hi"}) as root:
        with collector.span(
            "llm.gpt-4o-mini",
            SpanType.LLM_CALL,
            input_data={"messages": [{"role": "user", "content": "hi"}]},
            model_name="gpt-4o-mini",
        ) as llm_span:
            llm_span.tokens_input = 120
            llm_span.tokens_output = 30
            llm_span.cost_override = Decimal("0.000036")
            llm_span.output_data = {"content": "4"}

            with collector.span(
                "tool.calculator",
                SpanType.TOOL_CALL,
                input_data={"arguments": {"expression": "2 + 2"}},
            ) as tool_span:
                draft = tool_span.add_tool_call("calculator", {"expression": "2 + 2"})
                draft.succeed({"result": 4})
                tool_span.output_data = {"result": 4}

    traces = {trace.span_name: trace for trace in _traces(db, run.id)}
    assert set(traces) == {"agent.run", "llm.gpt-4o-mini", "tool.calculator"}

    root_trace = traces["agent.run"]
    llm_trace = traces["llm.gpt-4o-mini"]
    tool_trace = traces["tool.calculator"]

    assert root_trace.parent_trace_id is None
    assert llm_trace.parent_trace_id == root_trace.id
    assert tool_trace.parent_trace_id == llm_trace.id
    assert {trace.trace_id for trace in traces.values()} == {root_trace.trace_id}
    assert run.metadata_json["trace_id"] == root_trace.trace_id

    assert llm_trace.tokens_input == 120
    assert llm_trace.tokens_output == 30
    assert llm_trace.tokens_used == 150
    assert llm_trace.cost_usd == Decimal("0.000036")
    assert llm_trace.model_name == "gpt-4o-mini"
    assert llm_trace.status == SpanStatus.OK

    assert tool_trace.span_type == SpanType.TOOL_CALL
    assert len(tool_trace.tool_calls) == 1
    call = tool_trace.tool_calls[0]
    assert call.tool_name == "calculator"
    assert call.success is True
    assert call.tool_output == {"result": 4}


def test_collector_marks_errors_and_reraises(db, make_run):
    run = make_run()
    collector = TraceCollector(db, run)

    with pytest.raises(ValueError, match="boom"):
        with collector.run_span("agent.run"):
            with collector.span("dangerous.step", SpanType.ERROR):
                raise ValueError("boom")

    traces = {trace.span_name: trace for trace in _traces(db, run.id)}
    assert traces["dangerous.step"].status == SpanStatus.ERROR
    assert traces["dangerous.step"].error_details["type"] == "ValueError"
    assert traces["agent.run"].status == SpanStatus.ERROR


def test_failed_tool_call_is_recorded(db, make_run):
    run = make_run()
    collector = TraceCollector(db, run)

    with collector.run_span("agent.run"):
        with collector.span("tool.http_get", SpanType.TOOL_CALL) as span:
            draft = span.add_tool_call("http_get", {"url": "http://internal"})
            draft.fail("private network blocked")

    traces = {trace.span_name: trace for trace in _traces(db, run.id)}
    call = traces["tool.http_get"].tool_calls[0]
    assert call.success is False
    assert "private network" in call.error_message
    assert traces["tool.http_get"].status == SpanStatus.OK


def test_collector_survives_persistence_errors(db, make_run, monkeypatch):
    run = make_run()
    collector = TraceCollector(db, run)

    original_add = db.add

    def _broken_add(instance):
        if isinstance(instance, Trace) and instance.span_name == "broken":
            raise RuntimeError("database gone")
        return original_add(instance)

    monkeypatch.setattr(db, "add", _broken_add)

    with collector.run_span("agent.run"):
        with collector.span("broken", SpanType.DECISION):
            pass

    # The root span was still persisted; the failing span was swallowed.
    db.expire_all()
    spans = _traces(db, run.id)
    assert any(trace.span_name == "agent.run" for trace in spans)


def test_tool_calls_cascade_from_trace(db, make_run):
    run = make_run()
    collector = TraceCollector(db, run)

    with collector.run_span("agent.run"):
        with collector.span("tool.calculator", SpanType.TOOL_CALL) as span:
            span.add_tool_call("calculator", {"expression": "1"}).succeed({"result": 1})

    trace = next(trace for trace in _traces(db, run.id) if trace.span_name == "tool.calculator")
    assert db.scalar(select(ToolCall).where(ToolCall.trace_id == trace.id)) is not None
