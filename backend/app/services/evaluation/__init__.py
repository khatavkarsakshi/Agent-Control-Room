"""Evaluation orchestration: metric selection, context extraction, persistence.

A request evaluates one run across one evaluator family and stores one row per
metric in ``evaluations``. Re-running a metric updates the existing row (the table
has a ``(run_id, evaluator_type, metric_name)`` unique constraint), so scores do
not accumulate duplicates.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Iterable
from uuid import UUID

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session, selectinload

from app.core.config import settings
from app.core.exceptions import BadRequestError, ProviderError, ServiceUnavailableError
from app.models import Evaluation, EvaluatorType, Run, SpanType, Trace, utcnow
from app.schemas.evaluation import (
    EvaluationAggregateResponse,
    EvaluationCatalog,
    EvaluationRead,
    EvaluationReport,
    EvaluationSummary,
    EvaluatorCatalogEntry,
    MetricAggregate,
    MetricDefinitionRead,
    MetricStat,
    SkippedMetric,
)
from app.services.evaluation.base import (
    EvaluationError,
    EvaluationInput,
    Evaluator,
    EvaluatorUnavailable,
    MetricDefinition,
)
from app.services.evaluation.deepeval_adapter import DeepEvalEvaluator
from app.services.evaluation.heuristics import HeuristicEvaluator
from app.services.evaluation.ragas_adapter import RagasEvaluator

logger = logging.getLogger(__name__)

_EVALUATORS: dict[EvaluatorType, Evaluator] = {
    EvaluatorType.CUSTOM: HeuristicEvaluator(),
    EvaluatorType.RAGAS: RagasEvaluator(),
    EvaluatorType.DEEPEVAL: DeepEvalEvaluator(),
}

MAX_REASON_CHARS = 4_000
_MIN_CONTEXT_CHARS = 8


def get_evaluator(evaluator_type: EvaluatorType) -> Evaluator:
    return _EVALUATORS[evaluator_type]


def evaluator_catalog() -> EvaluationCatalog:
    """Describe every evaluator and whether its optional dependency is present."""
    entries: list[EvaluatorCatalogEntry] = []
    for evaluator_type, evaluator in _EVALUATORS.items():
        available, reason = evaluator.availability()
        definitions = evaluator.definitions()
        entries.append(
            EvaluatorCatalogEntry(
                evaluator_type=evaluator_type,
                display_name=evaluator.display_name,
                available=available,
                reason=reason,
                default_metrics=[
                    definition.name
                    for definition in definitions
                    if not definition.requires_contexts and not definition.requires_reference
                ],
                metrics=[
                    MetricDefinitionRead(
                        name=definition.name,
                        description=definition.description,
                        requires_contexts=definition.requires_contexts,
                        requires_reference=definition.requires_reference,
                    )
                    for definition in definitions
                ],
            )
        )
    return EvaluationCatalog(evaluators=entries)


# --- contexts ---------------------------------------------------------------
def _collect_text(value: Any, out: list[str], *, depth: int = 0) -> None:
    """Collect human-readable strings from a nested span payload."""
    if depth > 4 or len(out) >= settings.evaluation_max_contexts:
        return
    if isinstance(value, str):
        text = value.strip()
        if len(text) >= _MIN_CONTEXT_CHARS:
            out.append(text)
        return
    if isinstance(value, dict):
        # Prioritise prose fields, then fall back to the remaining scalar values.
        for key in ("content", "text", "body", "result", "output", "snippet", "summary"):
            if key in value:
                _collect_text(value[key], out, depth=depth + 1)
        for key, item in value.items():
            if key in {"content", "text", "body", "result", "output", "snippet", "summary"}:
                continue
            if isinstance(item, (str, dict, list)):
                _collect_text(item, out, depth=depth + 1)
        return
    if isinstance(value, (list, tuple)):
        for item in value[:50]:
            _collect_text(item, out, depth=depth + 1)


def extract_contexts(db: Session, run: Run) -> list[str]:
    """Best-effort retrieval contexts from data-access spans and tool outputs.

    Explicit contexts always win; this path exists so RAG metrics can run without
    the caller re-supplying documents that were already captured in the trace.
    """
    traces = db.scalars(
        select(Trace)
        .options(selectinload(Trace.tool_calls))
        .where(Trace.run_id == run.id)
        .order_by(Trace.timestamp)
    ).all()

    raw: list[str] = []
    for trace in traces:
        if trace.span_type == SpanType.DATA_ACCESS and trace.status.value == "ok":
            _collect_text(trace.output_data, raw)
        for call in trace.tool_calls:
            if call.success and call.tool_output:
                _collect_text(call.tool_output, raw)

    contexts: list[str] = []
    seen: set[str] = set()
    for text in raw:
        if text in seen:
            continue
        seen.add(text)
        contexts.append(text[: settings.evaluation_max_context_chars])
        if len(contexts) >= settings.evaluation_max_contexts:
            break
    return contexts


def build_evaluation_input(
    run: Run,
    *,
    contexts: Iterable[str] | None = None,
    reference: str | None = None,
) -> EvaluationInput:
    execution = (run.metadata_json or {}).get("execution")
    model_name = execution.get("model_name") if isinstance(execution, dict) else None
    cleaned_reference = None
    if reference and reference.strip():
        cleaned_reference = reference.strip()[: settings.evaluation_reference_max_chars]
    return EvaluationInput(
        prompt=run.input_prompt,
        output=run.output_response or "",
        contexts=tuple(contexts or ()),
        reference=cleaned_reference,
        model_name=model_name if isinstance(model_name, str) else None,
        total_tokens=run.total_tokens_used,
        latency_ms=run.latency_ms,
    )


# --- single run -------------------------------------------------------------
def _select_metrics(
    evaluator: Evaluator,
    data: EvaluationInput,
    requested: list[str] | None,
) -> tuple[list[MetricDefinition], list[SkippedMetric]]:
    definitions = {definition.name: definition for definition in evaluator.definitions()}
    names = requested if requested is not None else evaluator.default_metrics(data)
    if not names:
        return [], [SkippedMetric(metric_name="*", reason="No metric applies to this run.")]

    unknown = sorted(name for name in names if name not in definitions)
    if unknown:
        raise BadRequestError(
            f"Unknown metric(s) for {evaluator.display_name}: {', '.join(unknown)}",
            details={"known_metrics": sorted(definitions)},
        )

    selected: list[MetricDefinition] = []
    skipped: list[SkippedMetric] = []
    for name in names:
        definition = definitions[name]
        if definition.requires_contexts and not data.has_contexts:
            skipped.append(
                SkippedMetric(
                    metric_name=name,
                    reason="Requires retrieved contexts (none captured and none supplied).",
                )
            )
        elif definition.requires_reference and not data.has_reference:
            skipped.append(
                SkippedMetric(metric_name=name, reason="Requires a reference answer.")
            )
        else:
            selected.append(definition)
    return selected, skipped


def _evaluation_read(row: Evaluation) -> EvaluationRead:
    return EvaluationRead(
        id=row.id,
        run_id=row.run_id,
        evaluator_type=row.evaluator_type,
        metric_name=row.metric_name,
        score=float(row.score),
        reason=row.reason,
        created_at=row.created_at,
    )


def _average(scores: list[float]) -> float | None:
    return round(sum(scores) / len(scores), 4) if scores else None


def evaluate_run(
    db: Session,
    run: Run,
    *,
    evaluator_type: EvaluatorType,
    metrics: list[str] | None = None,
    contexts: list[str] | None = None,
    reference: str | None = None,
    replace: bool = True,
) -> EvaluationReport:
    """Evaluate one run and persist one row per computed metric."""
    if not run.output_response or not run.output_response.strip():
        raise BadRequestError("This run has no output to evaluate yet")

    evaluator = get_evaluator(evaluator_type)
    available, reason = evaluator.availability()
    if not available:
        raise ServiceUnavailableError(
            f"The {evaluator.display_name} evaluator is not available: {reason}",
            details={"evaluator_type": evaluator_type.value},
        )

    resolved_contexts = contexts if contexts is not None else extract_contexts(db, run)
    data = build_evaluation_input(run, contexts=resolved_contexts, reference=reference)
    selected, skipped = _select_metrics(evaluator, data, metrics)

    results = []
    if selected:
        try:
            results = evaluator.evaluate(data, selected)
        except EvaluatorUnavailable as exc:
            raise ServiceUnavailableError(
                f"The {evaluator.display_name} evaluator is not available: {exc}",
                details={"evaluator_type": evaluator_type.value},
            ) from exc
        except EvaluationError as exc:
            logger.warning("Evaluation failed for run %s: %s", run.id, exc)
            raise ProviderError(
                f"{evaluator.display_name} evaluation failed: {exc}",
                details={"evaluator_type": evaluator_type.value},
            ) from exc

    existing = {
        row.metric_name: row
        for row in db.scalars(
            select(Evaluation).where(
                Evaluation.run_id == run.id,
                Evaluation.evaluator_type == evaluator_type,
            )
        ).all()
    }

    replaced = 0
    now = utcnow()
    for result in results:
        score = Decimal(str(round(max(0.0, min(1.0, result.score)), 4)))
        row = existing.get(result.metric_name)
        if row is None:
            db.add(
                Evaluation(
                    run_id=run.id,
                    evaluator_type=evaluator_type,
                    metric_name=result.metric_name,
                    score=score,
                    reason=result.reason[:MAX_REASON_CHARS] if result.reason else None,
                    created_at=now,
                )
            )
        elif replace:
            row.score = score
            row.reason = result.reason[:MAX_REASON_CHARS] if result.reason else None
            row.created_at = now
            replaced += 1
        else:
            skipped.append(
                SkippedMetric(
                    metric_name=result.metric_name,
                    reason="Already evaluated; pass replace=true to overwrite.",
                )
            )
    db.commit()

    rows = list(
        db.scalars(
            select(Evaluation)
            .where(
                Evaluation.run_id == run.id,
                Evaluation.evaluator_type == evaluator_type,
            )
            .order_by(Evaluation.metric_name)
        ).all()
    )
    return EvaluationReport(
        run_id=run.id,
        evaluator_type=evaluator_type,
        evaluations=[_evaluation_read(row) for row in rows],
        skipped=skipped,
        average_score=_average([float(row.score) for row in rows]),
        replaced=replaced,
    )


# --- summaries --------------------------------------------------------------
def _metric_stat(scores: list[float]) -> MetricStat:
    if not scores:
        return MetricStat(count=0)
    return MetricStat(
        count=len(scores),
        average=round(sum(scores) / len(scores), 4),
        minimum=round(min(scores), 4),
        maximum=round(max(scores), 4),
    )


def build_summary(run_id: UUID, rows: Iterable[Evaluation]) -> EvaluationSummary:
    rows = list(rows)
    by_metric: dict[str, list[float]] = {}
    by_evaluator: dict[str, list[float]] = {}
    for row in rows:
        score = float(row.score)
        by_metric.setdefault(row.metric_name, []).append(score)
        by_evaluator.setdefault(row.evaluator_type.value, []).append(score)

    scores = [float(row.score) for row in rows]
    return EvaluationSummary(
        run_id=run_id,
        evaluations=[_evaluation_read(row) for row in rows],
        average_score=_average(scores),
        by_metric={name: _metric_stat(values) for name, values in sorted(by_metric.items())},
        by_evaluator={
            name: _metric_stat(values) for name, values in sorted(by_evaluator.items())
        },
    )


def summarize_run(db: Session, run_id: UUID) -> EvaluationSummary:
    rows = db.scalars(
        select(Evaluation)
        .where(Evaluation.run_id == run_id)
        .order_by(Evaluation.created_at.desc(), Evaluation.metric_name)
    ).all()
    return build_summary(run_id, rows)


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value


def aggregate_evaluations(
    db: Session,
    *,
    agent_id: UUID | None = None,
    evaluator_type: EvaluatorType | None = None,
    metric_name: str | None = None,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
) -> EvaluationAggregateResponse:
    statement = (
        select(
            Evaluation.evaluator_type,
            Evaluation.metric_name,
            func.count(Evaluation.id).label("evaluation_count"),
            func.count(func.distinct(Evaluation.run_id)).label("run_count"),
            func.avg(Evaluation.score).label("average"),
            func.min(Evaluation.score).label("minimum"),
            func.max(Evaluation.score).label("maximum"),
            func.max(Evaluation.created_at).label("latest_at"),
        )
        .join(Run, Run.id == Evaluation.run_id)
        .group_by(Evaluation.evaluator_type, Evaluation.metric_name)
        .order_by(Evaluation.evaluator_type, Evaluation.metric_name)
    )
    if agent_id is not None:
        statement = statement.where(Run.agent_id == agent_id)
    if evaluator_type is not None:
        statement = statement.where(Evaluation.evaluator_type == evaluator_type)
    if metric_name is not None:
        statement = statement.where(Evaluation.metric_name == metric_name)
    if date_from is not None:
        statement = statement.where(Evaluation.created_at >= _utc(date_from))
    if date_to is not None:
        statement = statement.where(Evaluation.created_at <= _utc(date_to))

    items = [
        MetricAggregate(
            evaluator_type=row.evaluator_type,
            metric_name=row.metric_name,
            run_count=int(row.run_count),
            evaluation_count=int(row.evaluation_count),
            average=round(float(row.average), 4),
            minimum=round(float(row.minimum), 4),
            maximum=round(float(row.maximum), 4),
            latest_at=row.latest_at,
        )
        for row in db.execute(statement).all()
    ]
    return EvaluationAggregateResponse(
        items=items,
        total_evaluations=sum(item.evaluation_count for item in items),
    )


def delete_evaluations(
    db: Session, run_id: UUID, *, evaluator_type: EvaluatorType | None = None
) -> int:
    statement = delete(Evaluation).where(Evaluation.run_id == run_id)
    if evaluator_type is not None:
        statement = statement.where(Evaluation.evaluator_type == evaluator_type)
    result = db.execute(statement)
    db.commit()
    return int(result.rowcount or 0)
