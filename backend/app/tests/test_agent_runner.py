"""Agent runner tests using a deterministic fake provider."""

from __future__ import annotations

from decimal import Decimal

import pytest
from sqlalchemy import select

from app.core.redis import NullPublisher
from app.models import RunStatus, SpanStatus, SpanType, Trace
from app.services.agent_runner import AgentRunner
from app.services.llm import LLMTimeoutError


def _runner(db, run, agent, resolver):
    return AgentRunner(
        db,
        run,
        agent,
        publisher=NullPublisher(),
        provider_factory=resolver,
    )


def _spans(db, run_id):
    db.expire_all()
    return db.scalars(select(Trace).where(Trace.run_id == run_id)).all()


def test_runner_executes_tool_loop_and_records_every_step(
    db, make_agent, make_run, fake_provider_factory, tool_call_response, final_response
):
    agent = make_agent()
    run = make_run(agent)
    provider, resolver = fake_provider_factory(
        [tool_call_response(), final_response()],
        model="gpt-4o-mini",
    )

    result = _runner(db, run, agent, resolver).execute()

    assert result.status == RunStatus.COMPLETED
    assert result.output == "The answer is 4."
    assert result.total_tokens == 280  # 100 + 20 + 150 + 10
    # 250 input tokens * 0.15 / 1M + 30 output tokens * 0.60 / 1M
    assert result.total_cost_usd == Decimal("0.000056")
    assert result.error is None
    assert result.latency_ms >= 0

    db.refresh(run)
    assert run.status == RunStatus.COMPLETED
    assert run.output_response == "The answer is 4."
    assert run.total_tokens_used == 280
    assert run.total_cost_usd == Decimal("0.000056")
    assert run.started_at is not None
    assert run.completed_at is not None
    assert run.metadata_json["trace_id"]

    spans = _spans(db, run.id)
    names = sorted(span.span_name for span in spans)
    assert names == [
        "agent.iteration.1",
        "agent.iteration.2",
        "agent.run",
        "llm.gpt-4o-mini",
        "llm.gpt-4o-mini",
        "tool.calculator",
    ]

    root = next(span for span in spans if span.span_name == "agent.run")
    assert root.parent_trace_id is None
    assert root.tokens_used == 280
    assert root.cost_usd == Decimal("0.000056")

    llm_spans = [span for span in spans if span.span_type == SpanType.LLM_CALL]
    assert sum(span.tokens_used for span in llm_spans) == 280
    assert all(span.model_name == "gpt-4o-mini" for span in llm_spans)

    tool_span = next(span for span in spans if span.span_type == SpanType.TOOL_CALL)
    assert tool_span.status == SpanStatus.OK
    assert len(tool_span.tool_calls) == 1
    assert tool_span.tool_calls[0].tool_output == {"expression": "2 + 2", "result": 4}

    # The second LLM call must see the assistant tool request and the tool result.
    second_messages = provider.requests[1]["messages"]
    roles = [message["role"] for message in second_messages]
    assert roles == ["system", "user", "assistant", "tool"]
    assert '"result": 4' in second_messages[-1]["content"]


def test_runner_records_tool_errors_and_lets_model_recover(
    db, make_agent, make_run, fake_provider_factory, tool_call_response, final_response
):
    agent = make_agent()
    run = make_run(agent)
    _, resolver = fake_provider_factory(
        [
            tool_call_response(arguments={"expression": "__import__('os')"}),
            final_response("I could not compute that."),
        ]
    )

    result = _runner(db, run, agent, resolver).execute()

    assert result.status == RunStatus.COMPLETED
    spans = _spans(db, run.id)
    tool_span = next(span for span in spans if span.span_type == SpanType.TOOL_CALL)
    assert tool_span.status == SpanStatus.ERROR
    assert tool_span.tool_calls[0].success is False


def test_runner_marks_run_failed_on_provider_error(db, make_agent, make_run, fake_provider_factory):
    agent = make_agent()
    run = make_run(agent)

    def _boom(_model):
        class _Provider:
            name = "broken"

            def complete(self, **_kwargs):
                raise LLMTimeoutError("upstream timed out")

        from app.services.llm import ResolvedProvider

        return ResolvedProvider(provider=_Provider(), model="gpt-4o-mini")

    result = _runner(db, run, agent, _boom).execute()

    assert result.status == RunStatus.FAILED
    assert "LLMTimeoutError" in (result.error or "")
    db.refresh(run)
    assert run.status == RunStatus.FAILED
    spans = _spans(db, run.id)
    assert any(span.status == SpanStatus.ERROR for span in spans)


def test_runner_fails_after_max_iterations(
    db, make_agent, make_run, fake_provider_factory, tool_call_response
):
    agent = make_agent()
    run = make_run(agent, metadata_json={"execution": {"max_iterations": 2}})
    _, resolver = fake_provider_factory([tool_call_response(), tool_call_response()])

    result = _runner(db, run, agent, resolver).execute()

    assert result.status == RunStatus.FAILED
    assert "maximum iterations" in (result.error or "")


def test_runner_rejects_unknown_configured_tool(db, make_agent, make_run, fake_provider_factory, final_response):
    agent = make_agent(tools_config=[{"name": "not_a_tool"}])
    run = make_run(agent)
    _, resolver = fake_provider_factory([final_response()])

    result = _runner(db, run, agent, resolver).execute()

    assert result.status == RunStatus.FAILED
    assert "ToolConfigurationError" in (result.error or "")


def test_runner_prompt_includes_system_message(db, make_agent, make_run, fake_provider_factory, final_response):
    agent = make_agent(system_prompt="Always answer in French.")
    run = make_run(agent, input_prompt="Say hello")
    provider, resolver = fake_provider_factory([final_response("Bonjour")])

    result = _runner(db, run, agent, resolver).execute()

    assert result.status == RunStatus.COMPLETED
    assert provider.requests[0]["messages"][0] == {
        "role": "system",
        "content": "Always answer in French.",
    }
    assert result.output == "Bonjour"


def test_runner_uses_execution_option_model_override(
    db, make_agent, make_run, fake_provider_factory, final_response
):
    agent = make_agent(model_name="gpt-4o")
    run = make_run(agent, metadata_json={"execution": {"model_name": "openai/gpt-4o-mini"}})
    _, resolver = fake_provider_factory([final_response()], model="gpt-4o-mini")

    result = _runner(db, run, agent, resolver).execute()

    assert result.status == RunStatus.COMPLETED
    # Prefix-stripped canonical model is used for pricing/tracing.
    assert result.total_cost_usd == Decimal("0.000029")
    db.refresh(run)
    assert run.total_cost_usd == Decimal("0.000029")
