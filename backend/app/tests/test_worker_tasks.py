"""Celery task tests (executed eagerly, no broker).

The tasks are called through ``.run`` so the assertions target the task body and
not Celery's serialization layer, which is already covered by eager mode.
"""

from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.core.redis import NullPublisher
from app.models import (
    Agent,
    Evaluation,
    ReplaySession,
    ReplayStatus,
    Run,
    RunStatus,
)
from app.schemas.replay import ReplayCreate
from app.services.agent_runner import AgentRunner
from app.services.replay import create_replay
from app.services.llm.recorded_provider import load_recorded_responses
from app.worker import tasks as tasks_module
from app.worker.tasks import evaluate_runs_task, execute_replay_task, run_agent_task


def _completed_run(make_run, **overrides) -> Run:
    data = {
        "status": RunStatus.COMPLETED,
        "output_response": "Water evaporates, condenses and falls as precipitation.",
        "input_prompt": "Explain the water cycle.",
        "total_tokens_used": 30,
        "total_cost_usd": Decimal("0.000030"),
        "latency_ms": 75,
    }
    data.update(overrides)
    return make_run(**data)


def test_evaluate_runs_task_scores_each_run(db, make_run, make_agent):
    agent = make_agent()
    first = _completed_run(make_run, agent=agent)
    second = _completed_run(make_run, agent=agent)

    summary = evaluate_runs_task.run(
        [str(first.id), str(second.id), "not-a-uuid", "00000000-0000-0000-0000-000000000000"],
        "custom",
        ["answer_relevancy"],
    )

    assert summary["evaluated"] == 2
    assert summary["missing"] == 2
    assert summary["failed"] == 0
    assert {entry["run_id"] for entry in summary["runs"]} == {
        str(first.id),
        str(second.id),
    }
    assert db.query(Evaluation).count() == 2


def test_evaluate_runs_task_records_failures_without_stopping(db, make_run, make_agent):
    agent = make_agent()
    runnable = _completed_run(make_run, agent=agent)
    empty = _completed_run(make_run, agent=agent, output_response=None)

    summary = evaluate_runs_task.run(
        [str(empty.id), str(runnable.id)], "custom", ["answer_relevancy"]
    )

    assert summary["failed"] == 1
    assert summary["evaluated"] == 1
    assert len(summary["runs"]) == 1
    assert summary["runs"][0]["run_id"] == str(runnable.id)
    assert "answer_relevancy" in summary["runs"][0]["scores"]


def test_evaluate_runs_task_rejects_unknown_evaluator():
    with pytest.raises(ValueError):
        evaluate_runs_task.run(["00000000-0000-0000-0000-000000000000"], "nope")


def test_execute_replay_task_runs_offline_replay(
    db, make_run, make_agent, fake_provider_factory, tool_call_response, final_response
):
    agent = make_agent()
    original = make_run(agent)
    AgentRunner(
        db,
        original,
        agent,
        publisher=NullPublisher(),
        provider_factory=fake_provider_factory([tool_call_response(), final_response()])[1],
    ).execute()
    assert load_recorded_responses(db, original)

    session, _ = create_replay(db, original, ReplayCreate(mode="offline"))

    result = execute_replay_task.run(str(session.id))

    assert result["session_id"] == str(session.id)
    assert result["status"] == "completed"
    db.expire_all()
    stored = db.get(ReplaySession, session.id)
    assert stored.status == ReplayStatus.COMPLETED
    assert stored.comparison_result["output"]["identical"] is True


def test_execute_replay_task_handles_unknown_session():
    result = execute_replay_task.run("00000000-0000-0000-0000-000000000000")
    assert result["status"] == "missing"

    invalid = execute_replay_task.run("not-a-uuid")
    assert invalid["status"] == "invalid"


def test_run_task_enqueues_automatic_evaluation_when_enabled(
    db, make_agent, make_run, monkeypatch
):
    agent = make_agent()
    run = make_run(agent)
    queued: list[tuple] = []

    class _Runner:
        def __init__(self, _db, _run, _agent):
            pass

        def execute(self):
            return SimpleNamespace(
                run_id=run.id,
                status=RunStatus.COMPLETED,
                total_tokens=0,
                total_cost_usd=Decimal("0"),
                latency_ms=1,
            )

    monkeypatch.setattr(tasks_module, "AgentRunner", _Runner)
    monkeypatch.setattr(tasks_module.settings, "evaluation_auto_on_complete", True)
    monkeypatch.setattr(tasks_module.settings, "evaluation_auto_evaluator", "custom")
    monkeypatch.setattr(tasks_module.settings, "evaluation_auto_metrics", "answer_relevancy")
    monkeypatch.setattr(
        tasks_module,
        "evaluate_runs_task",
        SimpleNamespace(delay=lambda *args, **kwargs: queued.append((args, kwargs))),
    )

    result = run_agent_task.run(str(run.id))

    assert result["status"] == "completed"
    assert len(queued) == 1
    args, _kwargs = queued[0]
    assert args == ([str(run.id)], "custom", ["answer_relevancy"])


def test_run_task_skips_automatic_evaluation_when_disabled(
    db, make_agent, make_run, monkeypatch
):
    agent = make_agent()
    run = make_run(agent)
    queued: list[tuple] = []

    class _Runner:
        def __init__(self, _db, _run, _agent):
            pass

        def execute(self):
            return SimpleNamespace(
                run_id=run.id,
                status=RunStatus.COMPLETED,
                total_tokens=0,
                total_cost_usd=Decimal("0"),
                latency_ms=1,
            )

    monkeypatch.setattr(tasks_module, "AgentRunner", _Runner)
    monkeypatch.setattr(tasks_module.settings, "evaluation_auto_on_complete", False)
    monkeypatch.setattr(
        tasks_module,
        "evaluate_runs_task",
        SimpleNamespace(delay=lambda *args, **kwargs: queued.append((args, kwargs))),
    )

    run_agent_task.run(str(run.id))

    assert queued == []


def test_run_task_marks_missing_agent_failed(db, make_run, make_agent, monkeypatch):
    agent = make_agent()
    run = make_run(agent)

    class _MissingAgentSession:
        """Delegate to a real session but pretend the agent row is gone."""

        def __init__(self, inner):
            self._inner = inner

        def get(self, model, pk, **_kwargs):
            if model is Agent:
                return None
            return self._inner.get(model, pk)

        def __getattr__(self, name):
            return getattr(self._inner, name)

    inner = tasks_module.SessionLocal()
    monkeypatch.setattr(tasks_module, "SessionLocal", lambda: _MissingAgentSession(inner))

    result = run_agent_task.run(str(run.id))

    assert result["status"] == "failed"
    db.expire_all()
    stored = db.get(Run, run.id)
    assert stored.status == RunStatus.FAILED
    assert "deleted" in (stored.error_message or "")
