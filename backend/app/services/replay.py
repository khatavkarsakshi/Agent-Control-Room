"""Replay sessions: what-if re-execution and original-vs-replay comparison.

A replay is a first-class ``Run`` (so it gets traces, streaming and accounting)
plus a ``ReplaySession`` row that records the overrides and the comparison. Two
modes exist:

``live``
    Call the provider again with the (possibly overridden) configuration. Costs
    money and is non-deterministic, exactly like any new run.

``offline``
    Replay the recorded LLM responses from the original trace. The agent loop,
    tool execution and span collection still run, but no provider call is made.
    Any divergence (changed prompt/tools/model) fails loudly when the recording
    is exhausted.
"""

from __future__ import annotations

import difflib
import logging
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.agents.tools import tool_registry
from app.core.config import settings
from app.core.exceptions import AppError, BadRequestError, ConflictError, NotFoundError
from app.core.redis import request_cancel
from app.models import (
    Agent,
    Evaluation,
    EvaluatorType,
    ReplaySession,
    ReplayStatus,
    Run,
    RunStatus,
    ToolCall,
    Trace,
    utcnow,
)
from app.schemas.replay import ReplayCreate, ReplaySessionRead
from app.services.agent_runner import AgentRunner
from app.services.evaluation import evaluate_run
from app.services.llm.recorded_provider import load_recorded_responses, recorded_factory

logger = logging.getLogger(__name__)

CANCELLED_MESSAGE = "Cancelled by user"
REPLAYABLE_STATUSES = {RunStatus.COMPLETED, RunStatus.FAILED, RunStatus.REPLAYED}
TERMINAL_REPLAY_STATUSES = {ReplayStatus.COMPLETED, ReplayStatus.FAILED}

_EXECUTION_KEYS = ("model_name", "temperature", "max_iterations", "max_tokens", "timeout_seconds")


def _clean_overrides(payload: ReplayCreate) -> dict[str, Any]:
    """Overrides persisted on the session, including execution-affecting flags."""
    stored = payload.overrides.model_dump(exclude_none=True)
    stored["mode"] = payload.mode
    if payload.evaluate:
        stored["evaluate"] = True
        if payload.metrics:
            stored["metrics"] = payload.metrics
    return stored


def create_replay(
    db: Session, original: Run, payload: ReplayCreate
) -> tuple[ReplaySession, Run]:
    """Create the session + replay run. The caller dispatches the Celery task."""
    if original.status not in REPLAYABLE_STATUSES:
        raise ConflictError(
            "Only finished runs can be replayed",
            details={"status": original.status.value},
        )

    agent = db.get(Agent, original.agent_id)
    if agent is None:
        raise NotFoundError("The agent for this run no longer exists")

    unknown_tools = tool_registry.validate_names(
        [{"name": name} for name in (payload.overrides.tools or [])]
    )
    if unknown_tools:
        raise BadRequestError(
            f"Unknown tool(s): {', '.join(sorted(unknown_tools))}",
            details={"known_tools": tool_registry.names()},
        )

    if payload.mode == "offline" and not load_recorded_responses(db, original):
        raise BadRequestError(
            "This run has no recorded LLM calls, so it cannot be replayed offline. "
            "Use mode=live instead."
        )

    # Replays inherit the original execution settings, then apply overrides.
    execution: dict[str, Any] = {}
    stored_execution = (original.metadata_json or {}).get("execution")
    if isinstance(stored_execution, dict):
        execution.update(
            {key: value for key, value in stored_execution.items() if key in _EXECUTION_KEYS}
        )
    execution.update(payload.overrides.execution_overrides())

    session = ReplaySession(
        id=uuid4(),
        original_run_id=original.id,
        status=ReplayStatus.PENDING,
        overrides=_clean_overrides(payload),
    )
    db.add(session)
    db.flush()

    metadata: dict[str, Any] = {
        "execution": execution,
        "requested_by": "api",
        "replay": {
            "session_id": str(session.id),
            "original_run_id": str(original.id),
            "mode": payload.mode,
            "billed": payload.mode == "live",
            "overrides": payload.overrides.agent_overrides(),
        },
    }
    if payload.evaluate:
        metadata["replay"]["evaluate"] = True
        metadata["replay"]["metrics"] = payload.metrics

    replay_run = Run(
        agent_id=original.agent_id,
        input_prompt=payload.overrides.input_prompt or original.input_prompt,
        status=RunStatus.PENDING,
        metadata_json=metadata,
        replay_of_run_id=original.id,
    )
    db.add(replay_run)
    db.flush()

    session.replay_run_id = replay_run.id
    db.commit()
    db.refresh(session)
    db.refresh(replay_run)
    logger.info(
        "Created replay session %s (mode=%s original=%s replay=%s)",
        session.id,
        payload.mode,
        original.id,
        replay_run.id,
    )
    return session, replay_run


# --- execution --------------------------------------------------------------
def execute_replay(db: Session, session: ReplaySession) -> ReplaySession:
    """Execute the replay run, then persist the comparison (Celery entry point)."""
    if session.status != ReplayStatus.PENDING:
        logger.info("Replay session %s is %s; skipping", session.id, session.status.value)
        return session

    original = db.get(Run, session.original_run_id)
    replay_run = db.get(Run, session.replay_run_id) if session.replay_run_id else None
    if original is None or replay_run is None:
        session.status = ReplayStatus.FAILED
        session.completed_at = utcnow()
        session.comparison_result = {"error": "original or replay run is missing"}
        db.commit()
        return session

    agent = db.get(Agent, replay_run.agent_id)
    if agent is None:
        replay_run.status = RunStatus.FAILED
        replay_run.error_message = "Agent configuration was deleted before replay execution"
        replay_run.completed_at = utcnow()
        session.status = ReplayStatus.FAILED
        session.comparison_result = {"error": replay_run.error_message}
        session.completed_at = utcnow()
        db.commit()
        return session

    session.status = ReplayStatus.RUNNING
    db.commit()

    mode = str(session.overrides.get("mode") or "live")
    provider_factory = None
    if mode == "offline":
        try:
            provider_factory = recorded_factory(db, original)
        except Exception as exc:  # noqa: BLE001 - recorded trace is unusable
            replay_run.status = RunStatus.FAILED
            replay_run.error_message = f"Offline replay unavailable: {exc}"
            replay_run.completed_at = utcnow()
            session.status = ReplayStatus.FAILED
            session.comparison_result = {"error": replay_run.error_message, "mode": mode}
            session.completed_at = utcnow()
            db.commit()
            return session

    try:
        AgentRunner(db, replay_run, agent, provider_factory=provider_factory).execute()
    except Exception:  # noqa: BLE001 - the runner records expected failures itself
        logger.exception("Replay session %s crashed", session.id)
        db.rollback()
        replay_run = db.get(Run, session.replay_run_id)
        if replay_run is not None and replay_run.status not in {
            RunStatus.COMPLETED,
            RunStatus.FAILED,
        }:
            replay_run.status = RunStatus.FAILED
            replay_run.error_message = "Replay execution crashed; see worker logs"
            replay_run.completed_at = utcnow()
        session.status = ReplayStatus.FAILED
        session.comparison_result = {"error": "replay execution crashed"}
        session.completed_at = utcnow()
        db.commit()
        return session

    return finalize_replay(db, session)


def finalize_replay(db: Session, session: ReplaySession) -> ReplaySession:
    """Compare the two runs, optionally evaluate the replay, and close the session."""
    original = db.get(Run, session.original_run_id)
    replay_run = db.get(Run, session.replay_run_id) if session.replay_run_id else None
    if original is None or replay_run is None:
        session.status = ReplayStatus.FAILED
        session.completed_at = utcnow()
        session.comparison_result = {"error": "original or replay run is missing"}
        db.commit()
        return session

    mode = str(session.overrides.get("mode") or "live")
    comparison = build_comparison(db, original, replay_run, mode=mode)

    if session.overrides.get("evaluate") and replay_run.status == RunStatus.COMPLETED:
        metrics = session.overrides.get("metrics")
        try:
            report = evaluate_run(
                db,
                replay_run,
                evaluator_type=EvaluatorType.CUSTOM,
                metrics=metrics if isinstance(metrics, list) else None,
            )
            comparison["evaluation"] = {
                "evaluator_type": report.evaluator_type.value,
                "average_score": report.average_score,
                "scores": {item.metric_name: item.score for item in report.evaluations},
                "skipped": [item.model_dump() for item in report.skipped],
            }
        except AppError as exc:
            comparison["evaluation"] = {"error": exc.message}
        except Exception as exc:  # noqa: BLE001 - evaluation must not fail the replay
            logger.exception("Post-replay evaluation failed for session %s", session.id)
            comparison["evaluation"] = {"error": f"{type(exc).__name__}: {exc}"}

    session.status = (
        ReplayStatus.COMPLETED
        if replay_run.status == RunStatus.COMPLETED
        else ReplayStatus.FAILED
    )
    session.comparison_result = comparison
    session.completed_at = utcnow()
    db.commit()
    logger.info(
        "Replay session %s %s (original=%s replay=%s)",
        session.id,
        session.status.value,
        original.status.value,
        replay_run.status.value,
    )
    return session


def cancel_replay(db: Session, session: ReplaySession) -> ReplaySession:
    """Cancel an in-flight replay and close the session."""
    if session.status in TERMINAL_REPLAY_STATUSES:
        raise ConflictError(
            f"Replay session is already {session.status.value}",
            details={"status": session.status.value},
        )

    if session.replay_run_id:
        replay_run = db.get(Run, session.replay_run_id)
        if replay_run is not None:
            request_cancel(replay_run.id)
            task_id = replay_run.metadata_json.get("task_id")
            if isinstance(task_id, str):
                try:
                    from app.worker import dispatch

                    dispatch.revoke_run(task_id)
                except Exception:  # noqa: BLE001 - cooperative flag still applies
                    logger.exception("Could not revoke replay task %s", task_id)
            if replay_run.status in {RunStatus.PENDING, RunStatus.RUNNING}:
                replay_run.status = RunStatus.FAILED
                replay_run.error_message = CANCELLED_MESSAGE
                replay_run.completed_at = utcnow()

    mode = str(session.overrides.get("mode") or "live")
    session.status = ReplayStatus.FAILED
    session.comparison_result = {"cancelled": True, "mode": mode}
    session.completed_at = utcnow()
    db.commit()
    logger.info("Cancelled replay session %s", session.id)
    return session


# --- comparison -------------------------------------------------------------
def _similarity(original: str, replay: str) -> float:
    if not original and not replay:
        return 1.0
    if not original or not replay:
        return 0.0
    limit = settings.replay_similarity_max_chars
    left = " ".join(original.split())[:limit]
    right = " ".join(replay.split())[:limit]
    return round(
        difflib.SequenceMatcher(None, left, right, autojunk=False).ratio(),
        4,
    )


def _delta(original: float | int | None, replay: float | int | None) -> dict[str, Any]:
    if original is None or replay is None:
        return {"original": original, "replay": replay, "delta": None, "ratio": None}
    difference = replay - original
    ratio = (replay / original) if original else None
    return {
        "original": original,
        "replay": replay,
        "delta": round(difference, 4) if isinstance(difference, float) else difference,
        "ratio": round(ratio, 4) if ratio is not None else None,
    }


def _span_stats(db: Session, run_id: UUID) -> dict[str, Any]:
    rows = db.execute(
        select(Trace.span_type, Trace.status, func.count(Trace.id))
        .where(Trace.run_id == run_id)
        .group_by(Trace.span_type, Trace.status)
    ).all()
    by_type: dict[str, int] = {}
    total = 0
    errors = 0
    for span_type, span_status, count in rows:
        total += int(count)
        by_type[span_type.value] = by_type.get(span_type.value, 0) + int(count)
        if span_status.value != "ok":
            errors += int(count)
    return {"total": total, "errors": errors, "by_type": by_type}


def _tool_stats(db: Session, run_id: UUID) -> dict[str, Any]:
    row = db.execute(
        select(
            func.count(ToolCall.id),
            func.count(ToolCall.id).filter(ToolCall.success.is_(True)),
            func.coalesce(func.sum(ToolCall.duration_ms), 0),
        )
        .join(Trace, ToolCall.trace_id == Trace.id)
        .where(Trace.run_id == run_id)
    ).one()
    return {
        "calls": int(row[0] or 0),
        "succeeded": int(row[1] or 0),
        "failed": int(row[0] or 0) - int(row[1] or 0),
        "duration_ms": int(row[2] or 0),
    }


def _evaluation_scores(
    db: Session, run_ids: list[UUID]
) -> dict[UUID, dict[str, float]]:
    scores: dict[UUID, dict[str, float]] = {}
    rows = db.scalars(select(Evaluation).where(Evaluation.run_id.in_(run_ids))).all()
    for row in rows:
        key = f"{row.evaluator_type.value}.{row.metric_name}"
        scores.setdefault(row.run_id, {})[key] = float(row.score)
    return scores


def build_comparison(
    db: Session, original: Run, replay_run: Run, *, mode: str
) -> dict[str, Any]:
    """Structured, JSON-safe comparison used by the UI and the export."""
    evaluation_scores = _evaluation_scores(db, [original.id, replay_run.id])
    original_scores = evaluation_scores.get(original.id, {})
    replay_scores = evaluation_scores.get(replay_run.id, {})
    evaluation_delta = {
        key: round(replay_scores[key] - original_scores[key], 4)
        for key in sorted(original_scores.keys() & replay_scores.keys())
    }

    return {
        "mode": mode,
        "billed": mode == "live",
        "status": {"original": original.status.value, "replay": replay_run.status.value},
        "prompt_changed": original.input_prompt != replay_run.input_prompt,
        "output": {
            "original_chars": len(original.output_response or ""),
            "replay_chars": len(replay_run.output_response or ""),
            "identical": (original.output_response or "") == (replay_run.output_response or ""),
            "similarity": _similarity(
                original.output_response or "", replay_run.output_response or ""
            ),
        },
        "latency_ms": _delta(original.latency_ms, replay_run.latency_ms),
        "tokens": _delta(original.total_tokens_used, replay_run.total_tokens_used),
        "cost_usd": _delta(float(original.total_cost_usd), float(replay_run.total_cost_usd)),
        "spans": {
            "original": _span_stats(db, original.id),
            "replay": _span_stats(db, replay_run.id),
        },
        "tool_calls": {
            "original": _tool_stats(db, original.id),
            "replay": _tool_stats(db, replay_run.id),
        },
        "evaluations": {
            "original": original_scores,
            "replay": replay_scores,
            "delta": evaluation_delta,
        },
    }


def session_read(session: ReplaySession) -> ReplaySessionRead:
    """Serialize a session, including both run summaries when loaded."""
    return ReplaySessionRead.model_validate(session)
