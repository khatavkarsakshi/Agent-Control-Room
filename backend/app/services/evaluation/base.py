"""Evaluator protocol shared by the heuristic, Ragas and DeepEval adapters."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

from app.models import EvaluatorType


class EvaluationError(Exception):
    """A metric could not be computed."""


class EvaluatorUnavailable(EvaluationError):
    """The optional dependency (or judge model) for an evaluator is missing."""


@dataclass(frozen=True)
class MetricDefinition:
    """Static description of one metric."""

    name: str
    description: str
    requires_contexts: bool = False
    requires_reference: bool = False

    def applicable(self, *, has_contexts: bool, has_reference: bool) -> bool:
        if self.requires_contexts and not has_contexts:
            return False
        if self.requires_reference and not has_reference:
            return False
        return True


@dataclass(frozen=True)
class EvaluationInput:
    """Everything an evaluator may need about one run."""

    prompt: str
    output: str
    contexts: tuple[str, ...] = ()
    reference: str | None = None
    model_name: str | None = None
    total_tokens: int = 0
    latency_ms: int | None = None

    @property
    def has_contexts(self) -> bool:
        return any(context.strip() for context in self.contexts)

    @property
    def has_reference(self) -> bool:
        return bool(self.reference and self.reference.strip())


@dataclass(frozen=True)
class MetricResult:
    metric_name: str
    score: float
    reason: str


class Evaluator(ABC):
    """A family of metrics (custom heuristics, Ragas, DeepEval)."""

    evaluator_type: EvaluatorType
    display_name: str

    @abstractmethod
    def definitions(self) -> list[MetricDefinition]:
        """Return every metric this evaluator can produce."""

    @abstractmethod
    def availability(self) -> tuple[bool, str | None]:
        """Return ``(available, reason)``; reason explains why it is not."""

    @abstractmethod
    def evaluate(
        self, data: EvaluationInput, metrics: list[MetricDefinition]
    ) -> list[MetricResult]:
        """Compute exactly the requested metrics."""

    def default_metrics(self, data: EvaluationInput) -> list[str]:
        """Metrics chosen when the request omits ``metrics``."""
        return [
            definition.name
            for definition in self.definitions()
            if definition.applicable(
                has_contexts=data.has_contexts, has_reference=data.has_reference
            )
        ]
