"""Task dispatch helpers.

Kept separate from the API so tests can monkeypatch ``dispatch_run`` without
starting a broker, and so the API never imports Celery internals directly.
"""

from __future__ import annotations

from uuid import UUID

from app.worker.tasks import evaluate_runs_task, execute_replay_task, run_agent_task


def dispatch_run(run_id: UUID) -> str:
    """Enqueue a run and return the Celery task id."""
    async_result = run_agent_task.delay(str(run_id))
    return async_result.id


def dispatch_evaluation(
    run_ids: list[UUID],
    evaluator_type: str,
    metrics: list[str] | None = None,
    reference: str | None = None,
    replace: bool = True,
) -> str:
    """Enqueue a batch evaluation and return the Celery task id."""
    async_result = evaluate_runs_task.delay(
        [str(run_id) for run_id in run_ids],
        evaluator_type,
        metrics,
        reference,
        replace,
    )
    return async_result.id


def dispatch_replay(session_id: UUID) -> str:
    """Enqueue a replay session and return the Celery task id."""
    async_result = execute_replay_task.delay(str(session_id))
    return async_result.id


def revoke_run(task_id: str | None, *, terminate: bool = True) -> None:
    """Best-effort revocation of an in-flight task."""
    if not task_id:
        return
    from app.worker.celery_app import celery_app

    celery_app.control.revoke(task_id, terminate=terminate, signal="SIGTERM")
