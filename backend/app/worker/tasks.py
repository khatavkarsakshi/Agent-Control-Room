"""Celery tasks.

* :func:`run_agent_task` executes an agent run.
* :func:`evaluate_runs_task` scores finished runs with one evaluator family.
* :func:`execute_replay_task` re-executes a run and records the comparison.
* :func:`cleanup_old_traces_task` enforces the trace retention window.
* :func:`calculate_daily_costs_task` aggregates spend into Redis for dashboards.

Tasks are idempotent where it matters: a redelivered run task will not execute a
run that is already being handled by another worker.
"""

from __future__ import annotations

import json
import logging
from datetime import date, datetime, timedelta, timezone
from uuid import UUID

from sqlalchemy import delete, select
from sqlalchemy.exc import SQLAlchemyError

from app.core.config import settings
from app.core.database import SessionLocal
from app.core.exceptions import AppError
from app.core.redis import clear_cancel, get_publisher, get_redis
from app.models import Agent, EvaluatorType, ReplaySession, ReplayStatus, Run, RunStatus, Trace
from app.services.agent_runner import AgentRunner
from app.services.cost_calculator import daily_cost_report
from app.services.evaluation import evaluate_run
from app.services.replay import execute_replay
from app.worker.celery_app import celery_app

logger = logging.getLogger(__name__)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


@celery_app.task(bind=True, name="runs.run_agent", acks_late=True, max_retries=0)
def run_agent_task(self, run_id: str) -> dict[str, object]:
    """Execute one agent run and return a compact summary."""
    try:
        run_uuid = UUID(run_id)
    except (ValueError, TypeError):
        logger.error("run_agent_task received an invalid run id: %r", run_id)
        return {"run_id": run_id, "status": "invalid"}

    db = SessionLocal()
    try:
        run = db.get(Run, run_uuid)
        if run is None:
            logger.warning("Run %s no longer exists; task skipped", run_uuid)
            return {"run_id": run_id, "status": "missing"}

        if run.status == RunStatus.RUNNING:
            started = run.started_at or run.created_at
            stale_after = timedelta(seconds=settings.celery_task_time_limit_seconds)
            if started and started.tzinfo is None:
                started = started.replace(tzinfo=timezone.utc)
            if started and _utcnow() - started > stale_after:
                run.status = RunStatus.FAILED
                run.error_message = "Worker lost before the run completed; marked stale"
                run.completed_at = _utcnow()
                db.commit()
                logger.error("Marked stale run %s as failed", run_uuid)
                return {"run_id": run_id, "status": RunStatus.FAILED.value}
            logger.info("Run %s is already running; skipping duplicate delivery", run_uuid)
            return {"run_id": run_id, "status": "already_running"}

        if run.status != RunStatus.PENDING:
            logger.info("Run %s is %s; nothing to execute", run_uuid, run.status.value)
            return {"run_id": run_id, "status": run.status.value}

        agent = db.get(Agent, run.agent_id)
        if agent is None:
            run.status = RunStatus.FAILED
            run.error_message = "Agent configuration was deleted before execution"
            run.completed_at = _utcnow()
            db.commit()
            return {"run_id": run_id, "status": RunStatus.FAILED.value}

        clear_cancel(run_uuid)
        result = AgentRunner(db, run, agent).execute()

        if settings.evaluation_auto_on_complete and result.status == RunStatus.COMPLETED:
            try:
                evaluate_runs_task.delay(
                    [str(result.run_id)],
                    settings.evaluation_auto_evaluator,
                    settings.evaluation_auto_metric_list or None,
                )
            except Exception:  # noqa: BLE001 - evaluation is never critical to a run
                logger.exception("Could not enqueue automatic evaluation for %s", result.run_id)

        return {
            "run_id": str(result.run_id),
            "status": result.status.value,
            "total_tokens": result.total_tokens,
            "total_cost_usd": float(result.total_cost_usd),
            "latency_ms": result.latency_ms,
        }
    except SQLAlchemyError:
        logger.exception("Database error while executing run %s", run_uuid)
        db.rollback()
        raise
    finally:
        db.close()


@celery_app.task(name="evaluations.evaluate_runs", acks_late=True, max_retries=0)
def evaluate_runs_task(
    run_ids: list[str],
    evaluator_type: str,
    metrics: list[str] | None = None,
    reference: str | None = None,
    replace: bool = True,
) -> dict[str, object]:
    """Evaluate each run and publish one ``evaluation.*`` event per run."""
    try:
        resolved_type = EvaluatorType(evaluator_type)
    except ValueError as exc:
        raise ValueError(f"unknown evaluator_type {evaluator_type!r}") from exc

    publisher = get_publisher()
    summary: dict[str, object] = {"evaluated": 0, "failed": 0, "missing": 0, "runs": []}
    evaluated_runs: list[dict[str, object]] = []

    db = SessionLocal()
    try:
        for raw_id in run_ids:
            try:
                run_uuid = UUID(raw_id)
            except (ValueError, TypeError):
                summary["missing"] = int(summary["missing"]) + 1
                continue

            run = db.get(Run, run_uuid)
            if run is None:
                summary["missing"] = int(summary["missing"]) + 1
                continue

            try:
                report = evaluate_run(
                    db,
                    run,
                    evaluator_type=resolved_type,
                    metrics=metrics,
                    reference=reference,
                    replace=replace,
                )
            except AppError as exc:
                db.rollback()
                summary["failed"] = int(summary["failed"]) + 1
                publisher.publish(
                    run.id,
                    "evaluation.failed",
                    {
                        "run_id": str(run.id),
                        "evaluator_type": resolved_type.value,
                        "error": exc.message,
                    },
                )
                logger.warning("Evaluation failed for run %s: %s", run.id, exc.message)
                continue
            except Exception:  # noqa: BLE001 - one bad run must not stop the batch
                db.rollback()
                summary["failed"] = int(summary["failed"]) + 1
                logger.exception("Unexpected evaluation failure for run %s", run.id)
                continue

            scores = {item.metric_name: item.score for item in report.evaluations}
            summary["evaluated"] = int(summary["evaluated"]) + 1
            evaluated_runs.append({"run_id": str(run.id), "scores": scores})
            publisher.publish(
                run.id,
                "evaluation.completed",
                {
                    "run_id": str(run.id),
                    "evaluator_type": resolved_type.value,
                    "average_score": report.average_score,
                    "scores": scores,
                    "skipped": len(report.skipped),
                },
            )
    finally:
        db.close()

    summary["runs"] = evaluated_runs
    return summary


def _replay_payload(session: ReplaySession) -> dict[str, object]:
    comparison = session.comparison_result if isinstance(session.comparison_result, dict) else {}
    output = comparison.get("output")
    similarity = output.get("similarity") if isinstance(output, dict) else None
    return {
        "session_id": str(session.id),
        "original_run_id": str(session.original_run_id),
        "replay_run_id": str(session.replay_run_id) if session.replay_run_id else None,
        "status": session.status.value,
        "mode": session.overrides.get("mode") if isinstance(session.overrides, dict) else None,
        "similarity": similarity,
    }


@celery_app.task(bind=True, name="replay.execute", acks_late=True, max_retries=0)
def execute_replay_task(self, session_id: str) -> dict[str, object]:
    """Execute one replay session and notify both run channels."""
    try:
        session_uuid = UUID(session_id)
    except (ValueError, TypeError):
        logger.error("execute_replay_task received an invalid session id: %r", session_id)
        return {"session_id": session_id, "status": "invalid"}

    db = SessionLocal()
    try:
        session = db.get(ReplaySession, session_uuid)
        if session is None:
            logger.warning("Replay session %s no longer exists; task skipped", session_uuid)
            return {"session_id": session_id, "status": "missing"}

        execute_replay(db, session)
        payload = _replay_payload(session)
        event_type = (
            "replay.completed"
            if session.status == ReplayStatus.COMPLETED
            else "replay.failed"
        )
        publisher = get_publisher()
        publisher.publish(session.original_run_id, event_type, payload)
        if session.replay_run_id:
            publisher.publish(session.replay_run_id, event_type, payload)

        return {
            "session_id": str(session.id),
            "status": session.status.value,
            "replay_run_id": payload["replay_run_id"],
        }
    except SQLAlchemyError:
        db.rollback()
        logger.exception("Database error while executing replay session %s", session_uuid)
        raise
    finally:
        db.close()


@celery_app.task(name="maintenance.cleanup_old_traces")
def cleanup_old_traces_task(retention_days: int | None = None, batch_size: int | None = None) -> dict[str, object]:
    """Delete trace rows older than the retention window in bounded batches.

    ``tool_calls`` and child spans are removed by ``ON DELETE CASCADE`` at the
    database level. Batching avoids a long table lock on large installations.
    """
    days = retention_days or settings.trace_retention_days
    batch = batch_size or settings.trace_cleanup_batch_size
    cutoff = _utcnow() - timedelta(days=days)
    deleted_total = 0

    db = SessionLocal()
    try:
        while True:
            subquery = (
                select(Trace.id)
                .where(Trace.timestamp < cutoff)
                .limit(batch)
                .scalar_subquery()
            )
            result = db.execute(delete(Trace).where(Trace.id.in_(subquery)))
            db.commit()
            deleted = int(result.rowcount or 0)
            deleted_total += deleted
            logger.info("Deleted %s traces older than %s", deleted, cutoff.isoformat())
            if deleted < batch:
                break
    except SQLAlchemyError:
        db.rollback()
        logger.exception("Trace cleanup failed after deleting %s rows", deleted_total)
        raise
    finally:
        db.close()

    return {"deleted": deleted_total, "cutoff": cutoff.isoformat(), "retention_days": days}


@celery_app.task(name="maintenance.calculate_daily_costs")
def calculate_daily_costs_task(target_date: str | None = None) -> dict[str, object]:
    """Aggregate one UTC day of cost and cache the report in Redis."""
    if target_date:
        try:
            report_date = date.fromisoformat(target_date)
        except ValueError as exc:
            raise ValueError("target_date must be an ISO date (YYYY-MM-DD)") from exc
    else:
        report_date = (_utcnow() - timedelta(days=1)).date()

    db = SessionLocal()
    try:
        report = daily_cost_report(db, report_date)
    finally:
        db.close()

    client = get_redis()
    if client is not None:
        try:
            ttl = settings.daily_cost_retention_days * 86_400
            client.set(
                f"acr:daily_costs:{report_date.isoformat()}",
                json.dumps(report),
                ex=ttl,
            )
        except Exception as exc:  # noqa: BLE001 - Redis is an optimization here
            logger.warning("Could not cache daily cost report: %s", exc)

    logger.info(
        "Daily cost report %s: runs=%s cost=$%.6f tokens=%s",
        report_date.isoformat(),
        report["run_count"],
        report["total_cost_usd"],
        report["total_tokens"],
    )
    return report
