"""Request/response models for quality evaluations.

Evaluation rows are stored per metric, so a single request produces one row per
metric that the evaluator could compute. Metrics whose prerequisites are missing
(for example faithfulness without retrieved contexts) are reported as skipped
instead of failing the whole request.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models import EvaluatorType, RunStatus
from app.schemas.common import ORMModel

MAX_METRICS = 10
MAX_CONTEXTS = 100
MAX_CONTEXT_CHARS = 20_000


def _validate_metrics(value: list[str] | None) -> list[str] | None:
    if value is None:
        return None
    seen: set[str] = set()
    cleaned: list[str] = []
    for item in value:
        name = item.strip()
        if not name:
            raise ValueError("metric names must not be blank")
        if len(name) > 80:
            raise ValueError("metric names must be 80 characters or fewer")
        if name not in seen:
            seen.add(name)
            cleaned.append(name)
    if not cleaned:
        raise ValueError("metrics must contain at least one name when provided")
    return cleaned


def _validate_reference(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = value.strip()
    return cleaned or None


class EvaluationRequest(BaseModel):
    """Body for ``POST /runs/{run_id}/evaluations``."""

    model_config = ConfigDict(extra="forbid")

    evaluator_type: EvaluatorType = EvaluatorType.CUSTOM
    # Omit to let the evaluator choose the metrics that fit the supplied inputs.
    metrics: list[str] | None = Field(default=None, max_length=MAX_METRICS)
    reference: str | None = Field(default=None, max_length=MAX_CONTEXT_CHARS)
    contexts: list[str] | None = Field(default=None, max_length=MAX_CONTEXTS)
    # Replace earlier scores for the same metric instead of answering 409.
    replace: bool = True

    _clean_metrics = field_validator("metrics")(_validate_metrics)
    _clean_reference = field_validator("reference")(_validate_reference)

    @field_validator("contexts")
    @classmethod
    def _clean_contexts(cls, value: list[str] | None) -> list[str] | None:
        if value is None:
            return None
        cleaned = [context.strip() for context in value if context and context.strip()]
        if not cleaned:
            raise ValueError("contexts must contain at least one non-blank string")
        for context in cleaned:
            if len(context) > MAX_CONTEXT_CHARS:
                raise ValueError(f"each context must be {MAX_CONTEXT_CHARS} characters or fewer")
        return cleaned


class EvaluationRead(ORMModel):
    id: uuid.UUID
    run_id: uuid.UUID
    evaluator_type: EvaluatorType
    metric_name: str
    score: float
    reason: str | None
    created_at: datetime


class SkippedMetric(BaseModel):
    metric_name: str
    reason: str


class MetricStat(BaseModel):
    count: int = Field(ge=0)
    average: float | None = None
    minimum: float | None = None
    maximum: float | None = None


class EvaluationReport(BaseModel):
    """Result of one evaluation request."""

    run_id: uuid.UUID
    evaluator_type: EvaluatorType
    evaluations: list[EvaluationRead] = Field(default_factory=list)
    skipped: list[SkippedMetric] = Field(default_factory=list)
    average_score: float | None = None
    replaced: int = Field(default=0, ge=0)


class EvaluationSummary(BaseModel):
    """All persisted scores for a run, plus per-metric and per-evaluator stats."""

    run_id: uuid.UUID
    evaluations: list[EvaluationRead] = Field(default_factory=list)
    average_score: float | None = None
    by_metric: dict[str, MetricStat] = Field(default_factory=dict)
    by_evaluator: dict[str, MetricStat] = Field(default_factory=dict)


class MetricDefinitionRead(BaseModel):
    name: str
    description: str
    requires_contexts: bool
    requires_reference: bool


class EvaluatorCatalogEntry(BaseModel):
    evaluator_type: EvaluatorType
    display_name: str
    available: bool
    reason: str | None = None
    default_metrics: list[str] = Field(default_factory=list)
    metrics: list[MetricDefinitionRead] = Field(default_factory=list)


class EvaluationCatalog(BaseModel):
    evaluators: list[EvaluatorCatalogEntry] = Field(default_factory=list)


class MetricAggregate(BaseModel):
    evaluator_type: EvaluatorType
    metric_name: str
    run_count: int = Field(ge=0)
    evaluation_count: int = Field(ge=0)
    average: float
    minimum: float
    maximum: float
    latest_at: datetime


class EvaluationAggregateResponse(BaseModel):
    items: list[MetricAggregate] = Field(default_factory=list)
    total_evaluations: int = Field(ge=0)


class BatchEvaluationRequest(BaseModel):
    """Score several completed runs in the background."""

    model_config = ConfigDict(extra="forbid")

    run_ids: list[uuid.UUID] | None = Field(default=None, max_length=100)
    agent_id: uuid.UUID | None = None
    status: RunStatus = RunStatus.COMPLETED
    limit: int = Field(default=25, ge=1, le=100)
    evaluator_type: EvaluatorType = EvaluatorType.CUSTOM
    metrics: list[str] | None = Field(default=None, max_length=MAX_METRICS)
    reference: str | None = Field(default=None, max_length=MAX_CONTEXT_CHARS)
    replace: bool = True

    _clean_metrics = field_validator("metrics")(_validate_metrics)
    _clean_reference = field_validator("reference")(_validate_reference)


class BatchEvaluationResponse(BaseModel):
    task_id: str | None = None
    requested: int = Field(ge=0)
    run_ids: list[uuid.UUID] = Field(default_factory=list)


class DeleteEvaluationsResponse(BaseModel):
    run_id: uuid.UUID
    deleted: int = Field(ge=0)
