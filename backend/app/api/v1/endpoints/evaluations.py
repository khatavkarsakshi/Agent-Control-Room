"""Quality evaluation endpoints.

* ``GET  /evaluations/metrics``          catalog + optional-dependency status
* ``GET  /evaluations/summary``          aggregate scores across runs
* ``POST /evaluations/batch``            background evaluation of many runs
* ``GET  /runs/{run_id}/evaluations``    scores persisted for one run
* ``POST /runs/{run_id}/evaluations``    evaluate one run now
* ``DELETE /runs/{run_id}/evaluations``  remove scores for one run
"""

from __future__ import annotations

import logging
from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Query, status
from sqlalchemy import select

from app.api.deps import DbSession, RunDep
from app.core.exceptions import BadRequestError, ServiceUnavailableError
from app.core.redis import get_publisher
from app.models import EvaluatorType, Run
from app.schemas.evaluation import (
    BatchEvaluationRequest,
    BatchEvaluationResponse,
    DeleteEvaluationsResponse,
    EvaluationAggregateResponse,
    EvaluationCatalog,
    EvaluationReport,
    EvaluationRequest,
    EvaluationSummary,
)
from app.services.evaluation import (
    aggregate_evaluations,
    evaluate_run,
    evaluator_catalog,
    summarize_run,
    delete_evaluations,
)
from app.worker import dispatch

logger = logging.getLogger(__name__)

router = APIRouter(tags=["evaluations"])


@router.get(
    "/evaluations/metrics",
    response_model=EvaluationCatalog,
    summary="List evaluators, their metrics and availability",
)
def list_evaluation_metrics() -> EvaluationCatalog:
    return evaluator_catalog()


@router.get(
    "/evaluations/summary",
    response_model=EvaluationAggregateResponse,
    summary="Aggregate scores by evaluator and metric",
)
def evaluation_summary(
    db: DbSession,
    agent_id: UUID | None = Query(default=None),
    evaluator_type: EvaluatorType | None = Query(default=None),
    metric_name: str | None = Query(default=None, max_length=80),
    date_from: datetime | None = Query(default=None, description="created_at >= (ISO 8601)"),
    date_to: datetime | None = Query(default=None, description="created_at <= (ISO 8601)"),
) -> EvaluationAggregateResponse:
    return aggregate_evaluations(
        db,
        agent_id=agent_id,
        evaluator_type=evaluator_type,
        metric_name=metric_name,
        date_from=date_from,
        date_to=date_to,
    )


def _resolve_batch_runs(db: DbSession, payload: BatchEvaluationRequest) -> list[Run]:
    if payload.run_ids:
        unique_ids = list(dict.fromkeys(payload.run_ids))
        runs = db.scalars(select(Run).where(Run.id.in_(unique_ids))).all()
        by_id = {run.id: run for run in runs}
        missing = [str(run_id) for run_id in unique_ids if run_id not in by_id]
        if missing:
            raise BadRequestError(
                "Unknown run id(s)", details={"missing": missing[:20]}
            )
        return [by_id[run_id] for run_id in unique_ids]

    statement = select(Run).where(Run.status == payload.status)
    if payload.agent_id is not None:
        statement = statement.where(Run.agent_id == payload.agent_id)
    statement = statement.order_by(Run.created_at.desc()).limit(payload.limit)
    return list(db.scalars(statement).all())


@router.post(
    "/evaluations/batch",
    response_model=BatchEvaluationResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Evaluate several runs in the background",
)
def batch_evaluate(payload: BatchEvaluationRequest, db: DbSession) -> BatchEvaluationResponse:
    runs = _resolve_batch_runs(db, payload)
    if not runs:
        return BatchEvaluationResponse(task_id=None, requested=0, run_ids=[])

    try:
        task_id = dispatch.dispatch_evaluation(
            [run.id for run in runs],
            payload.evaluator_type.value,
            payload.metrics,
            payload.reference,
            payload.replace,
        )
    except Exception as exc:  # noqa: BLE001 - broker failure is a 503
        logger.exception("Could not enqueue evaluation batch")
        raise ServiceUnavailableError(
            "Could not enqueue the evaluation. Is the Celery broker available?"
        ) from exc

    return BatchEvaluationResponse(
        task_id=task_id,
        requested=len(runs),
        run_ids=[run.id for run in runs],
    )


@router.get(
    "/runs/{run_id}/evaluations",
    response_model=EvaluationSummary,
    summary="Get every score recorded for a run",
)
def list_run_evaluations(run: RunDep, db: DbSession) -> EvaluationSummary:
    return summarize_run(db, run.id)


@router.post(
    "/runs/{run_id}/evaluations",
    response_model=EvaluationReport,
    summary="Evaluate a run synchronously",
)
def create_run_evaluation(
    run: RunDep, payload: EvaluationRequest, db: DbSession
) -> EvaluationReport:
    report = evaluate_run(
        db,
        run,
        evaluator_type=payload.evaluator_type,
        metrics=payload.metrics,
        contexts=payload.contexts,
        reference=payload.reference,
        replace=payload.replace,
    )
    get_publisher().publish(
        run.id,
        "evaluation.completed",
        {
            "run_id": str(run.id),
            "evaluator_type": report.evaluator_type.value,
            "average_score": report.average_score,
            "scores": {item.metric_name: item.score for item in report.evaluations},
            "skipped": len(report.skipped),
        },
    )
    return report


@router.delete(
    "/runs/{run_id}/evaluations",
    response_model=DeleteEvaluationsResponse,
    summary="Delete scores for a run",
)
def delete_run_evaluations(
    run: RunDep,
    db: DbSession,
    evaluator_type: EvaluatorType | None = Query(default=None),
) -> DeleteEvaluationsResponse:
    deleted = delete_evaluations(db, run.id, evaluator_type=evaluator_type)
    return DeleteEvaluationsResponse(run_id=run.id, deleted=deleted)
