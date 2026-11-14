"""DeepEval adapter.

Like the Ragas adapter this is optional (``requirements-eval.txt``) and imports its
dependency lazily. DeepEval decides which judge model to use from its own
configuration; set ``EVALUATION_JUDGE_MODEL`` / provider keys accordingly.
"""

from __future__ import annotations

import logging
from types import ModuleType
from typing import Any

from app.core.config import settings
from app.models import EvaluatorType
from app.services.evaluation.base import (
    EvaluationError,
    EvaluationInput,
    Evaluator,
    EvaluatorUnavailable,
    MetricDefinition,
    MetricResult,
)

logger = logging.getLogger(__name__)

_DEFINITIONS: tuple[MetricDefinition, ...] = (
    MetricDefinition(
        "answer_relevancy",
        "DeepEval: is the answer relevant to the question?",
    ),
    MetricDefinition(
        "faithfulness",
        "DeepEval: is the answer grounded in the retrieval context?",
        requires_contexts=True,
    ),
    MetricDefinition(
        "context_precision",
        "DeepEval: are the retrieved contexts relevant and well ranked?",
        requires_contexts=True,
        requires_reference=True,
    ),
    MetricDefinition(
        "context_recall",
        "DeepEval: do the retrieved contexts contain the reference answer?",
        requires_contexts=True,
        requires_reference=True,
    ),
)

_CLASS_NAMES = {
    "answer_relevancy": "AnswerRelevancyMetric",
    "faithfulness": "FaithfulnessMetric",
    "context_precision": "ContextualPrecisionMetric",
    "context_recall": "ContextualRecallMetric",
}


def _import_deepeval() -> tuple[ModuleType, dict[str, Any], Any]:
    try:
        from deepeval import metrics as deepeval_metrics
        from deepeval.test_case import LLMTestCase
    except ImportError as exc:  # pragma: no cover - exercised when deepeval is absent
        raise EvaluatorUnavailable(
            "deepeval is not installed; run `pip install -r backend/requirements-eval.txt`"
        ) from exc

    classes: dict[str, Any] = {}
    for name, class_name in _CLASS_NAMES.items():
        candidate = getattr(deepeval_metrics, class_name, None)
        if candidate is not None:
            classes[name] = candidate
    return deepeval_metrics, classes, LLMTestCase


def _build_test_case(case_cls: Any, data: EvaluationInput) -> Any:
    kwargs: dict[str, Any] = {
        "input": data.prompt,
        "actual_output": data.output,
    }
    if data.has_contexts:
        kwargs["retrieval_context"] = list(data.contexts)
    if data.has_reference:
        kwargs["expected_output"] = data.reference
    return case_cls(**kwargs)


class DeepEvalEvaluator(Evaluator):
    evaluator_type = EvaluatorType.DEEPEVAL
    display_name = "DeepEval"

    def definitions(self) -> list[MetricDefinition]:
        return list(_DEFINITIONS)

    def availability(self) -> tuple[bool, str | None]:
        try:
            _, classes, _ = _import_deepeval()
        except EvaluatorUnavailable as exc:
            return False, str(exc)
        if not classes:
            return False, "the installed deepeval version exposes no known metrics"
        return True, None

    def evaluate(
        self, data: EvaluationInput, metrics: list[MetricDefinition]
    ) -> list[MetricResult]:
        _, classes, case_cls = _import_deepeval()
        test_case = _build_test_case(case_cls, data)
        model = settings.evaluation_judge_model or None

        results: list[MetricResult] = []
        for definition in metrics:
            metric_cls = classes.get(definition.name)
            if metric_cls is None:
                raise EvaluatorUnavailable(
                    f"the installed deepeval version has no '{definition.name}' metric"
                )
            metric = self._instantiate(metric_cls, model)
            try:
                metric.measure(test_case)
            except Exception as exc:  # noqa: BLE001 - surfaced as a metric failure
                raise EvaluationError(
                    f"deepeval {definition.name} failed: {type(exc).__name__}: {exc}"
                ) from exc

            score = getattr(metric, "score", None)
            if score is None:
                raise EvaluationError(f"deepeval returned no score for {definition.name}")
            reason = getattr(metric, "reason", None) or f"DeepEval {definition.name}."
            results.append(
                MetricResult(
                    metric_name=definition.name,
                    score=max(0.0, min(1.0, float(score))),
                    reason=str(reason)[:2_000],
                )
            )
        return results

    @staticmethod
    def _instantiate(metric_cls: Any, model: str | None) -> Any:
        """Handle the constructor differences between deepeval releases."""
        attempts = [
            {"threshold": 0.0, "model": model} if model else {"threshold": 0.0},
            {},
        ]
        last_error: Exception | None = None
        for kwargs in attempts:
            try:
                return metric_cls(**kwargs)
            except TypeError as exc:  # unknown keyword for this version
                last_error = exc
        raise EvaluatorUnavailable(
            f"could not construct {getattr(metric_cls, '__name__', metric_cls)}: {last_error}"
        )
