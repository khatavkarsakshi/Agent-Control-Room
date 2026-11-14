"""Replay service tests: session creation, offline execution, comparison, cancel."""

from __future__ import annotations

from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import select

from app.core.exceptions import BadRequestError, ConflictError
from app.core.redis import NullPublisher
from app.models import (
    Evaluation,
    EvaluatorType,
    ReplaySession,
    ReplayStatus,
    Run,
    RunStatus,
    SpanStatus,
    SpanType,
    Trace,
)
from app.schemas.replay import ReplayCreate, ReplayOverrides
from app.services.agent_runner import AgentRunner
from app.services.replay import (
    build_comparison,
    cancel_replay,
    create_replay,
    execute_replay,
)
from app.services.llm.recorded_provider import load_recorded_responses


def _executed_run(db, make_run, make_agent, resolver, **overrides):
    """Create and execute an original run with a scripted fake provider."""
    agent = make_agent()
    run = make_run(agent, **overrides)
    AgentRunner(db, run, agent, publisher=NullPublisher(), provider_factory=resolver).execute()
    db.refresh(run)
    return run, agent


def _spans(db, run_id):
    db.expire_all()
    return db.scalars(
        select(Trace).where(Trace.run_id == run_id).order_by(Trace.timestamp)
    ).all()


def test_create_replay_links_runs_and_records_overrides(
    db, make_run, make_agent, fake_provider_factory, final_response
):
    original, agent = _executed_run(
        db,
        make_run,
        make_agent,
        fake_provider_factory([final_response()])[1],
    )

    payload = ReplayCreate(
        mode="offline",
        overrides=ReplayOverrides(
            model_name="gpt-4o-mini",
            system_prompt="You are terse.",
            tools=["calculator"],
            temperature=0.2,
            max_iterations=2,
        ),
    )

    session, replay_run = create_replay(db, original, payload)

    assert session.status == ReplayStatus.PENDING
    assert session.original_run_id == original.id
    assert session.replay_run_id == replay_run.id
    assert session.overrides["mode"] == "offline"
    assert session.overrides["system_prompt"] == "You are terse."

    assert replay_run.replay_of_run_id == original.id
    assert replay_run.status == RunStatus.PENDING
    assert replay_run.input_prompt == original.input_prompt
    replay_meta = replay_run.metadata_json["replay"]
    assert replay_meta["session_id"] == str(session.id)
    assert replay_meta["mode"] == "offline"
    assert replay_meta["billed"] is False
    assert replay_meta["overrides"] == {"system_prompt": "You are terse.", "tools": ["calculator"]}
    assert replay_run.metadata_json["execution"]["temperature"] == 0.2
    assert replay_run.metadata_json["execution"]["max_iterations"] == 2

    # The original run is history: replaying must not mutate it.
    assert original.status == RunStatus.COMPLETED
    assert original.replay_of_run_id is None


def test_create_replay_rejects_unfinished_runs(db, make_run):
    run = make_run(status=RunStatus.RUNNING)

    with pytest.raises(ConflictError):
        create_replay(db, run, ReplayCreate())


def test_create_replay_rejects_unknown_tools(
    db, make_run, make_agent, fake_provider_factory, final_response
):
    original, _ = _executed_run(
        db, make_run, make_agent, fake_provider_factory([final_response()])[1]
    )

    with pytest.raises(BadRequestError) as excinfo:
        create_replay(
            db,
            original,
            ReplayCreate(overrides=ReplayOverrides(tools=["rm_rf"])),
        )

    assert "Unknown tool" in excinfo.value.message


def test_offline_replay_without_recorded_calls_is_rejected(db, make_run):
    run = make_run(status=RunStatus.COMPLETED, output_response="done")

    with pytest.raises(BadRequestError) as excinfo:
        create_replay(db, run, ReplayCreate(mode="offline"))

    assert "offline" in excinfo.value.message


def test_offline_replay_reproduces_the_recorded_output(
    db, make_run, make_agent, fake_provider_factory, tool_call_response, final_response
):
    original, agent = _executed_run(
        db,
        make_run,
        make_agent,
        fake_provider_factory([tool_call_response(), final_response()])[1],
    )
    assert original.status == RunStatus.COMPLETED
    assert original.output_response == "The answer is 4."
    original_spans = _spans(db, original.id)
    assert len(original_spans) == 6

    session, replay_run = create_replay(db, original, ReplayCreate(mode="offline"))
    execute_replay(db, session)

    db.expire_all()
    session = db.get(ReplaySession, session.id)
    replay_run = db.get(Run, replay_run.id)
    assert session is not None and replay_run is not None
    assert session.status == ReplayStatus.COMPLETED
    assert replay_run.status == RunStatus.COMPLETED
    assert replay_run.output_response == original.output_response

    comparison = session.comparison_result
    assert comparison is not None
    assert comparison["mode"] == "offline"
    assert comparison["billed"] is False
    assert comparison["output"]["identical"] is True
    assert comparison["output"]["similarity"] == 1.0
    assert comparison["status"] == {"original": "completed", "replay": "completed"}
    assert comparison["tool_calls"]["replay"]["calls"] == 1
    assert comparison["spans"]["replay"]["total"] == len(_spans(db, replay_run.id))


def test_offline_replay_divergence_fails_loudly(db, make_run, make_agent):
    """A recording that stops mid-loop must fail, not silently truncate."""
    agent = make_agent()
    original = make_run(agent, status=RunStatus.COMPLETED, output_response="partial")

    # Record exactly one tool-call turn and no follow-up completion.
    base = Trace(
        run_id=original.id,
        span_name="llm.gpt-4o-mini",
        span_type=SpanType.LLM_CALL,
        status=SpanStatus.OK,
        trace_id=uuid4().hex,
        span_id=uuid4().hex[:16],
        model_name="gpt-4o-mini",
        tokens_input=10,
        tokens_output=5,
        output_data={
            "content": None,
            "tool_calls": [{"id": "call_1", "name": "calculator", "arguments": {"expression": "2+2"}}],
            "finish_reason": "tool_calls",
        },
    )
    db.add(base)
    db.commit()
    assert len(load_recorded_responses(db, original)) == 1

    session, replay_run = create_replay(db, original, ReplayCreate(mode="offline"))
    execute_replay(db, session)

    db.expire_all()
    replay_run = db.get(Run, replay_run.id)
    session = db.get(ReplaySession, session.id)
    assert replay_run.status == RunStatus.FAILED
    assert "exhausted" in (replay_run.error_message or "").lower()
    assert session.status == ReplayStatus.FAILED
    assert session.comparison_result["status"]["replay"] == "failed"


def test_replay_overrides_apply_to_the_runner(
    db, make_run, make_agent, fake_provider_factory, final_response
):
    original, agent = _executed_run(
        db, make_run, make_agent, fake_provider_factory([final_response()])[1]
    )
    _, replay_run = create_replay(
        db,
        original,
        ReplayCreate(
            mode="offline",
            overrides=ReplayOverrides(
                system_prompt="You are a pirate.",
                tools=["current_time"],
                temperature=0.7,
            ),
        ),
    )

    provider, resolver = fake_provider_factory([final_response("Arr, 4 it be.")])
    AgentRunner(
        db, replay_run, agent, publisher=NullPublisher(), provider_factory=resolver
    ).execute()

    request = provider.requests[0]
    assert request["messages"][0] == {"role": "system", "content": "You are a pirate."}
    assert [tool.name for tool in request["tools"]] == ["current_time"]
    assert request["temperature"] == 0.7


def test_comparison_includes_evaluation_deltas(db, make_run, make_agent):
    agent = make_agent()
    original = make_run(agent, status=RunStatus.COMPLETED, output_response="alpha beta")
    replay_run = make_run(
        agent,
        status=RunStatus.COMPLETED,
        output_response="alpha beta gamma",
        replay_of_run_id=original.id,
    )

    for run, score in ((original, Decimal("0.5000")), (replay_run, Decimal("0.7500"))):
        db.add(
            Evaluation(
                run_id=run.id,
                evaluator_type=EvaluatorType.CUSTOM,
                metric_name="answer_relevancy",
                score=score,
                reason="test",
            )
        )
    db.commit()

    comparison = build_comparison(db, original, replay_run, mode="live")

    assert comparison["evaluations"]["original"] == {"custom.answer_relevancy": 0.5}
    assert comparison["evaluations"]["replay"] == {"custom.answer_relevancy": 0.75}
    assert comparison["evaluations"]["delta"] == {"custom.answer_relevancy": 0.25}
    assert comparison["output"]["identical"] is False
    assert 0.0 <= comparison["output"]["similarity"] < 1.0


def test_finalize_evaluates_replay_when_requested(
    db, make_run, make_agent, tool_call_response, final_response, fake_provider_factory
):
    original, _ = _executed_run(
        db,
        make_run,
        make_agent,
        fake_provider_factory([tool_call_response(), final_response()])[1],
    )
    session, replay_run = create_replay(
        db,
        original,
        ReplayCreate(
            mode="offline", evaluate=True, metrics=["answer_relevancy"]
        ),
    )
    execute_replay(db, session)

    db.expire_all()
    session = db.get(ReplaySession, session.id)
    assert session.status == ReplayStatus.COMPLETED
    evaluation = session.comparison_result["evaluation"]
    assert evaluation["evaluator_type"] == "custom"
    assert "answer_relevancy" in evaluation["scores"]
    assert db.query(Evaluation).filter(Evaluation.run_id == replay_run.id).count() == 1


def test_cancel_pending_replay_closes_session(
    db, make_run, make_agent, fake_provider_factory, final_response
):
    original, _ = _executed_run(
        db, make_run, make_agent, fake_provider_factory([final_response()])[1]
    )
    session, replay_run = create_replay(db, original, ReplayCreate(mode="offline"))

    cancelled = cancel_replay(db, session)

    assert cancelled.status == ReplayStatus.FAILED
    assert cancelled.comparison_result["cancelled"] is True
    db.expire_all()
    replay_run = db.get(Run, replay_run.id)
    assert replay_run.status == RunStatus.FAILED
    assert replay_run.error_message == "Cancelled by user"


def test_cancel_terminal_replay_is_conflict(
    db, make_run, make_agent, fake_provider_factory, final_response
):
    original, _ = _executed_run(
        db, make_run, make_agent, fake_provider_factory([final_response()])[1]
    )
    session, _ = create_replay(db, original, ReplayCreate(mode="offline"))
    execute_replay(db, session)
    db.refresh(session)

    with pytest.raises(ConflictError):
        cancel_replay(db, session)


def test_execute_replay_is_idempotent_for_finished_sessions(
    db, make_run, make_agent, fake_provider_factory, final_response
):
    original, _ = _executed_run(
        db, make_run, make_agent, fake_provider_factory([final_response()])[1]
    )
    session, _ = create_replay(db, original, ReplayCreate(mode="offline"))
    execute_replay(db, session)
    completed_at = session.completed_at

    again = execute_replay(db, session)

    assert again.status == ReplayStatus.COMPLETED
    assert again.completed_at == completed_at
