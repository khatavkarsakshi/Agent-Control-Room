"""Trace querying, waterfall tree building and Jaeger deep links."""

from __future__ import annotations

from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Query
from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

from app.api.deps import DbSession, PaginationDep
from app.core.exceptions import NotFoundError
from app.core.sanitization import escape_like
from app.models import Run, SpanStatus, SpanType, Trace
from app.schemas.common import Page
from app.schemas.trace import TraceNode, TraceRead, TraceTree

router = APIRouter(prefix="/traces", tags=["traces"])


def _base_trace_query():
    return select(Trace).options(selectinload(Trace.tool_calls))


@router.get("", response_model=Page[TraceRead], summary="List trace spans")
def list_traces(
    db: DbSession,
    page: PaginationDep,
    run_id: UUID | None = Query(default=None),
    trace_id: str | None = Query(default=None, max_length=32, description="OpenTelemetry trace id"),
    span_type: SpanType | None = Query(default=None),
    span_status: SpanStatus | None = Query(default=None, alias="status"),
    min_duration_ms: int | None = Query(default=None, ge=0),
    max_duration_ms: int | None = Query(default=None, ge=0),
    search: str | None = Query(default=None, max_length=200, description="span name search"),
    order: Literal["asc", "desc"] = Query(default="asc", description="Timestamp order"),
) -> Page[TraceRead]:
    conditions = []
    if run_id is not None:
        conditions.append(Trace.run_id == run_id)
    if trace_id:
        conditions.append(Trace.trace_id == trace_id)
    if span_type is not None:
        conditions.append(Trace.span_type == span_type)
    if span_status is not None:
        conditions.append(Trace.status == span_status)
    if min_duration_ms is not None:
        conditions.append(Trace.duration_ms >= min_duration_ms)
    if max_duration_ms is not None:
        conditions.append(Trace.duration_ms <= max_duration_ms)
    if search:
        pattern = f"%{escape_like(search.strip())}%"
        conditions.append(Trace.span_name.ilike(pattern, escape="\\"))

    base = _base_trace_query()
    if conditions:
        base = base.where(*conditions)

    total = db.scalar(select(func.count()).select_from(base.subquery())) or 0
    ordering = Trace.timestamp.asc() if order == "asc" else Trace.timestamp.desc()
    traces = db.scalars(
        base.order_by(ordering, Trace.id).limit(page.limit).offset(page.offset)
    ).unique().all()

    return Page[TraceRead](
        items=[TraceRead.model_validate(trace) for trace in traces],
        total=int(total),
        limit=page.limit,
        offset=page.offset,
    )


@router.get("/tree", response_model=TraceTree, summary="Get a run's spans as a tree")
def get_trace_tree(
    db: DbSession,
    run_id: UUID = Query(description="Run whose spans should be assembled"),
) -> TraceTree:
    run = db.get(Run, run_id)
    if run is None:
        raise NotFoundError(f"Run {run_id} not found")

    traces = db.scalars(
        _base_trace_query()
        .where(Trace.run_id == run_id)
        .order_by(Trace.timestamp.asc(), Trace.id)
    ).unique().all()

    nodes: dict[UUID, TraceNode] = {
        trace.id: TraceNode.model_validate(trace) for trace in traces
    }
    roots: list[TraceNode] = []
    for trace in traces:
        node = nodes[trace.id]
        parent = nodes.get(trace.parent_trace_id) if trace.parent_trace_id else None
        if parent is not None:
            parent.children.append(node)
        else:
            roots.append(node)

    trace_id = (run.metadata_json or {}).get("trace_id")
    if not isinstance(trace_id, str):
        trace_id = traces[0].trace_id if traces else None

    return TraceTree(
        run_id=run_id,
        trace_id=trace_id,
        roots=roots,
        span_count=len(traces),
        total_duration_ms=sum(root.duration_ms for root in roots),
    )


@router.get(
    "/by-trace-id/{trace_id}",
    response_model=list[TraceRead],
    summary="Get all spans sharing an OpenTelemetry trace id",
)
def get_by_trace_id(trace_id: str, db: DbSession) -> list[TraceRead]:
    traces = db.scalars(
        _base_trace_query()
        .where(Trace.trace_id == trace_id)
        .order_by(Trace.timestamp.asc(), Trace.id)
    ).unique().all()
    if not traces:
        raise NotFoundError(f"Trace {trace_id} not found")
    return [TraceRead.model_validate(trace) for trace in traces]


@router.get("/{trace_pk}", response_model=TraceRead, summary="Get one span")
def get_trace(trace_pk: UUID, db: DbSession) -> TraceRead:
    trace = db.scalar(_base_trace_query().where(Trace.id == trace_pk))
    if trace is None:
        raise NotFoundError(f"Trace span {trace_pk} not found")
    return TraceRead.model_validate(trace)
