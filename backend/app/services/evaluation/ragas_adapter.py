"""Ragas adapter.

Ragas is an optional dependency (``pip install -r requirements-eval.txt``). The
adapter imports it lazily so the control plane starts and runs without it, and so
unit tests never need a judge model. Both the modern ``EvaluationDataset`` API and
the legacy ``datasets.Dataset`` shape are supported because Ragas changed this
interface between 0.1 and 0.2.
"""

from __future__ import annotations

import logging
import math
from types import SimpleNamespace
from typing import Any

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
        "faithfulness",
        "Ragas: are the answer's claims grounded in the retrieved contexts?",
        requires_contexts=True,
    ),
    MetricDefinition(
        "answer_relevancy",
        "Ragas: how relevant is the answer to the question?",
    ),
    MetricDefinition(
        "context_precision",
        "Ragas: are the retrieved contexts relevant to the reference answer?",
        requires_contexts=True,
        requires_reference=True,
    ),
    MetricDefinition(
        "context_recall",
        "Ragas: do the retrieved contexts cover the reference answer?",
        requires_contexts=True,
        requires_reference=True,
    ),
)


def _import_ragas() -> SimpleNamespace:
    try:
        import ragas
        from ragas import evaluate as ragas_evaluate
        from ragas import metrics as ragas_metrics
    except ImportError as exc:  # pragma: no cover - exercised when ragas is absent
        raise EvaluatorUnavailable(
            "ragas is not installed; run `pip install -r backend/requirements-eval.txt`"
        ) from exc

    metric_objects = {}
    for definition in _DEFINITIONS:
        candidate = getattr(ragas_metrics, definition.name, None)
        if candidate is not None:
            metric_objects[definition.name] = candidate
    if not metric_objects:
        raise EvaluatorUnavailable("the installed ragas version exposes no known metrics")

    return SimpleNamespace(
        module=ragas,
        evaluate=ragas_evaluate,
        metrics=metric_objects,
        version=getattr(ragas, "__version__", "unknown"),
    )


def _build_dataset(sample: dict[str, Any]) -> Any:
    """Prefer the 0.2+ single-turn schema, fall back to a legacy Dataset."""
    try:
        from ragas import EvaluationDataset
        from ragas.dataset_schema import SingleTurnSample

        return EvaluationDataset(samples=[SingleTurnSample(**sample)])
    except Exception:  # noqa: BLE001 - the legacy path below is the fallback
        pass

    try:
        from datasets import Dataset

        return Dataset.from_dict({key: [value] for key, value in sample.items()})
    except Exception as exc:  # noqa: BLE001 - datasets is a ragas dependency
        raise EvaluatorUnavailable(
            "ragas is installed but neither EvaluationDataset nor datasets is importable"
        ) from exc


def _as_mapping(result: Any) -> dict[str, Any]:
    """Normalise the several shapes ragas results have taken over time."""
    if hasattr(result, "to_pandas"):
        frame = result.to_pandas()
        if len(frame.index) == 0:
            return {}
        return {str(column): frame[column].iloc[0] for column in frame.columns}

    scores = getattr(result, "scores", None)
    if isinstance(scores, list) and scores and isinstance(scores[0], dict):
        return dict(scores[0])
    if isinstance(scores, dict):
        return {key: (value[0] if isinstance(value, list) and value else value) for key, value in scores.items()}
    if isinstance(result, dict):
        return dict(result)
    return {}


def _clean_score(value: Any) -> float | None:
    try:
        score = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(score) or math.isinf(score):
        return None
    return max(0.0, min(1.0, score))


class RagasEvaluator(Evaluator):
    evaluator_type = EvaluatorType.RAGAS
    display_name = "Ragas"

    def definitions(self) -> list[MetricDefinition]:
        return list(_DEFINITIONS)

    def availability(self) -> tuple[bool, str | None]:
        try:
            _import_ragas()
        except EvaluatorUnavailable as exc:
            return False, str(exc)
        return True, None

    def evaluate(
        self, data: EvaluationInput, metrics: list[MetricDefinition]
    ) -> list[MetricResult]:
        ragas = _import_ragas()
        selected = []
        for definition in metrics:
            metric = ragas.metrics.get(definition.name)
            if metric is None:
                raise EvaluatorUnavailable(
                    f"the installed ragas version has no '{definition.name}' metric "
                    f"(ragas {ragas.version})"
                )
            selected.append(metric)

        sample = {
            "user_input": data.prompt,
            "response": data.output,
            "retrieved_contexts": list(data.contexts),
            "reference": data.reference or "",
        }
        dataset = _build_dataset(sample)
        try:
            result = ragas.evaluate(dataset=dataset, metrics=selected, raise_exceptions=False)
        except TypeError:
            # Older releases call the first argument `dataset` positionally.
            result = ragas.evaluate(dataset, metrics=selected)
        except Exception as exc:  # noqa: BLE001 - surfaced as a metric failure
            raise EvaluationError(f"ragas evaluation failed: {type(exc).__name__}: {exc}") from exc

        mapping = _as_mapping(result)
        results: list[MetricResult] = []
        missing: list[str] = []
        for definition in metrics:
            score = _clean_score(mapping.get(definition.name))
            if score is None:
                # Ragas also suffixes columns with the judge model in some versions.
                for key, value in mapping.items():
                    if key.startswith(definition.name):
                        score = _clean_score(value)
                        if score is not None:
                            break
            if score is None:
                missing.append(definition.name)
                continue
            results.append(
                MetricResult(
                    metric_name=definition.name,
                    score=round(score, 4),
                    reason=f"Ragas {definition.name} (ragas {ragas.version}).",
                )
            )

        if missing:
            raise EvaluationError(
                "ragas returned no score for: " + ", ".join(sorted(missing))
            )
        return results
