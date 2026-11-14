"""Run creation, execution control, querying and export."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Literal
from uuid import UUID

from fastapi import APIRouter, Query, status
from fastapi.responses import JSONResponse
from sqlalchemy import func, or_, select
from sqlalchemy.orm import selectinload

from app.api.deps import DbSession, PaginationDep, RunDep
from app.core.exceptions import ConflictError, NotFoundError, ServiceUnavailableError
from app.core.redis import get_publisher, request_cancel
from app.core.sanitization import escape_like, safe_filename
from app.core.telemetry import current_traceparent
from app.models import Agent, Run, RunStatus, Trace
from app.schemas.common import Page
from app.schemas.run import ExecutionOptions, RunCreate, RunDetail, RunRead
from app.schemas.trace import TraceRead
from app.worker import dispatch

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/runs", tags=["runs"])

CANCELLED_MESSAGE = "Cancelled by user"
TERMINAL_STATUSES = {RunStatus.COMPLETED, RunStatus.FAILED, RunStatus.REPLAYED}

_SORT_COLUMNS = {
    "created_at": Run.created_at,
    "-created_at": Run.created_at.desc(),
    "total_cost_usd": Run.total_cost_usd,
    "-total_cost_usd": Run.total_cost_usd.desc(),
    "latency_ms": Run.latency_ms,
    "-latency_ms": Run.latency_ms.desc(),
    "total_tokens_used": Run.total_tokens_used,
    "-total_tokens_used": Run.total_tokens_used.desc(),
}

SortOption = Literal[
    "created_at",
    "-created_at",
    "total_cost_usd",
    "-total_cost_usd",
    "latency_ms",
    "-latency_ms",
    "total_tokens_used",
    "-total_tokens_used",
]


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value


def _detail_query():
    return select(Run).options(
        selectinload(Run.traces).selectinload(Trace.tool_calls),
        selectinload(Run.agent),
        selectinload(Run.evaluations),
    )


@router.post(
    "",
    response_model=RunRead,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Create and enqueue a run",
)
def create_run(payload: RunCreate, db: DbSession) -> RunRead:
    agent = db.get(Agent, payload.agent_id)
    if agent is None:
        raise NotFoundError(f"Agent {payload.agent_id} not found")
    if not agent.is_active:
        raise ConflictError("This agent is inactive; activate it before starting a run")

    execution = (payload.execution_options or ExecutionOptions()).model_dump(exclude_none=True)
    metadata: dict[str, Any] = dict(payload.metadata)
    metadata["execution"] = execution
    # W3C trace context captured from the API request, resumed by the worker.
    traceparent = current_traceparent()
    if traceparent:
        metadata["traceparent"] = traceparent
    metadata["requested_by"] = "api"

    run = Run(
        agent_id=agent.id,
        input_prompt=payload.input_prompt,
        status=RunStatus.PENDING,
        metadata_json=metadata,
    )
    db.add(run)
    db.commit()
    db.refresh(run)

    try:
        task_id = dispatch.dispatch_run(run.id)
    except Exception as exc:  # noqa: BLE001 - broker failure becomes a failed run
        logger.exception("Could not enqueue run %s", run.id)
        run.status = RunStatus.FAILED
        run.error_message = f"Failed to enqueue run: {type(exc).__name__}"
        run.completed_at = datetime.now(timezone.utc)
        db.commit()
        raise ServiceUnavailableError(
            "Could not enqueue the run. Is the Celery broker available?"
        ) from exc

    run.metadata_json["task_id"] = task_id
    db.commit()
    db.refresh(run)
    logger.info("Enqueued run %s (agent=%s task=%s)", run.id, agent.name, task_id)
    return RunRead.model_validate(run)


@router.get("", response_model=Page[RunRead], summary="List runs")
def list_runs(
    db: DbSession,
    page: PaginationDep,
    agent_id: UUID | None = Query(default=None),
    run_status: list[RunStatus] | None = Query(default=None, alias="status"),
    search: str | None = Query(default=None, max_length=200, description="Prompt/output search"),
    date_from: datetime | None = Query(default=None, description="created_at >= (ISO 8601)"),
    date_to: datetime | None = Query(default=None, description="created_at <= (ISO 8601)"),
    min_cost: float | None = Query(default=None, ge=0),
    max_cost: float | None = Query(default=None, ge=0),
    sort: SortOption = Query(default="-created_at"),
) -> Page[RunRead]:
    conditions = []
    if agent_id is not None:
        conditions.append(Run.agent_id == agent_id)
    if run_status:
        conditions.append(Run.status.in_(run_status))
    if search:
        pattern = f"%{escape_like(search.strip())}%"
        conditions.append(
            or_(
                Run.input_prompt.ilike(pattern, escape="\\"),
                Run.output_response.ilike(pattern, escape="\\"),
                Run.error_message.ilike(pattern, escape="\\"),
            )
        )
    if date_from is not None:
        conditions.append(Run.created_at >= _utc(date_from))
    if date_to is not None:
        conditions.append(Run.created_at <= _utc(date_to))
    if min_cost is not None:
        conditions.append(Run.total_cost_usd >= Decimal(str(min_cost)))
    if max_cost is not None:
        conditions.append(Run.total_cost_usd <= Decimal(str(max_cost)))

    base = select(Run)
    if conditions:
        base = base.where(*conditions)

    total = db.scalar(select(func.count()).select_from(base.subquery())) or 0
    order = _SORT_COLUMNS[sort]
    runs = db.scalars(base.order_by(order, Run.id).limit(page.limit).offset(page.offset)).all()

    return Page[RunRead](
        items=[RunRead.model_validate(run) for run in runs],
        total=int(total),
        limit=page.limit,
        offset=page.offset,
    )


@router.get("/{run_id}", response_model=RunDetail, summary="Get a run with its traces")
def get_run(run_id: UUID, db: DbSession) -> RunDetail:
    run = db.scalar(_detail_query().where(Run.id == run_id))
    if run is None:
        raise NotFoundError(f"Run {run_id} not found")
    return RunDetail.model_validate(run)


@router.get("/{run_id}/export", summary="Export a run as JSON")
def export_run(run_id: UUID, db: DbSession) -> JSONResponse:
    run = db.scalar(
        _detail_query()
        .options(selectinload(Run.evaluations))
        .where(Run.id == run_id)
    )
    if run is None:
        raise NotFoundError(f"Run {run_id} not found")

    traces = [TraceRead.model_validate(trace).model_dump(mode="json") for trace in run.traces]
    tool_calls = [
        call
        for trace in traces
        for call in trace.get("tool_calls", [])
    ]
    evaluations = [
        {
            "id": str(evaluation.id),
            "evaluator_type": evaluation.evaluator_type.value,
            "metric_name": evaluation.metric_name,
            "score": float(evaluation.score),
            "reason": evaluation.reason,
            "created_at": evaluation.created_at.isoformat(),
        }
        for evaluation in run.evaluations
    ]

    payload = {
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "run": RunRead.model_validate(run).model_dump(mode="json"),
        "agent": {
            "id": str(run.agent.id),
            "name": run.agent.name,
            "model_name": run.agent.model_name,
            "tools_config": run.agent.tools_config,
        },
        "traces": traces,
        "tool_calls": tool_calls,
        "evaluations": evaluations,
    }
    filename = safe_filename(f"run-{run.id}.json")
    return JSONResponse(
        content=payload,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.post("/{run_id}/cancel", response_model=RunRead, summary="Cancel a pending or running run")
def cancel_run(run: RunDep, db: DbSession) -> RunRead:
    if run.status in TERMINAL_STATUSES:
        raise ConflictError(
            f"Run is already {run.status.value} and cannot be cancelled",
            details={"status": run.status.value},
        )

    request_cancel(run.id)
    task_id = run.metadata_json.get("task_id")
    if isinstance(task_id, str):
        try:
            dispatch.revoke_run(task_id)
        except Exception:  # noqa: BLE001 - cooperative flag still applies
            logger.exception("Could not revoke Celery task %s", task_id)

    run.status = RunStatus.FAILED
    run.error_message = CANCELLED_MESSAGE
    run.completed_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(run)

    get_publisher().publish(
        run.id,
        "run.failed",
        {
            "run_id": str(run.id),
            "status": RunStatus.FAILED.value,
            "error_message": CANCELLED_MESSAGE,
            "cancelled": True,
        },
    )
    logger.info("Cancelled run %s", run.id)
    return RunRead.model_validate(run)
