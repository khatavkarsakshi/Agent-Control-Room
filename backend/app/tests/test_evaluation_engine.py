"""Evaluation engine tests: heuristics, catalog, context extraction, persistence.

Ragas and DeepEval are optional dependencies. These tests assert the *graceful*
behaviour when they are missing and never require a judge model or network
access; the custom evaluator carries the functional coverage.
"""

from __future__ import annotations

from decimal import Decimal
from uuid import uuid4

import pytest

from app.core.exceptions import BadRequestError, ServiceUnavailableError
from app.models import (
    Evaluation,
    EvaluatorType,
    RunStatus,
    SpanStatus,
    SpanType,
    ToolCall,
    Trace,
)
from app.services.evaluation import (
    aggregate_evaluations,
    build_evaluation_input,
    build_summary,
    delete_evaluations,
    evaluate_run,
    evaluator_catalog,
    extract_contexts,
    summarize_run,
)
from app.services.evaluation.base import EvaluationInput
from app.services.evaluation.heuristics import (
    answer_correctness,
    answer_relevancy,
    context_precision,
    context_recall,
    faithfulness,
)

ANSWER = (
    "Water evaporates from lakes and oceans, condenses into clouds, "
    "and falls back as precipitation."
)
CONTEXT = (
    "Water evaporates from lakes and oceans when the sun heats the surface. "
    "The vapour condenses into clouds and falls back as rain or snow."
)
REFERENCE = "Water evaporates, condenses into clouds and falls as precipitation."
PROMPT = "Explain the water cycle and how clouds form."


def _completed_run(make_run, **overrides):
    data = {
        "status": RunStatus.COMPLETED,
        "output_response": ANSWER,
        "input_prompt": PROMPT,
        "total_tokens_used": 120,
        "total_cost_usd": Decimal("0.000120"),
        "latency_ms": 250,
    }
    data.update(overrides)
    return make_run(**data)


def _trace(db, run, *, span_type, output=None, status=SpanStatus.OK):
    trace = Trace(
        run_id=run.id,
        span_name=f"{span_type.value}.test",
        span_type=span_type,
        status=status,
        trace_id=uuid4().hex,
        span_id=uuid4().hex[:16],
        output_data=output,
    )
    db.add(trace)
    db.flush()
    return trace


# --- heuristic metrics ------------------------------------------------------
def test_heuristic_metrics_are_bounded_and_explain_themselves():
    data = EvaluationInput(
        prompt=PROMPT,
        output=ANSWER,
        contexts=(CONTEXT,),
        reference=REFERENCE,
    )

    scores = {
        "answer_relevancy": answer_relevancy(data),
        "faithfulness": faithfulness(data),
        "answer_correctness": answer_correctness(data),
        "context_precision": context_precision(data),
        "context_recall": context_recall(data),
    }

    for name, score in scores.items():
        assert 0.0 <= score.value <= 1.0, name
        assert score.reason, name
    assert scores["answer_relevancy"].value > 0
    assert scores["faithfulness"].value > 0
    assert scores["answer_correctness"].value > 0
    assert scores["context_precision"].value > 0
    assert scores["context_recall"].value > 0


def test_heuristic_metrics_are_deterministic():
    data = EvaluationInput(prompt=PROMPT, output=ANSWER, contexts=(CONTEXT,), reference=REFERENCE)
    first = answer_correctness(data)
    second = answer_correctness(data)
    assert first == second


def test_heuristic_metrics_handle_empty_input():
    data = EvaluationInput(prompt="", output="", contexts=(), reference=None)
    assert answer_relevancy(data).value == 0.0
    assert faithfulness(data).value == 0.0
    assert answer_correctness(data).value == 0.0
    assert context_precision(data).value == 0.0
    assert context_recall(data).value == 0.0


# --- catalog ----------------------------------------------------------------
def test_catalog_exposes_custom_evaluator_and_optional_ones():
    catalog = evaluator_catalog()
    entries = {entry.evaluator_type: entry for entry in catalog.evaluators}

    assert set(entries) == {EvaluatorType.CUSTOM, EvaluatorType.RAGAS, EvaluatorType.DEEPEVAL}
    custom = entries[EvaluatorType.CUSTOM]
    assert custom.available is True
    assert custom.reason is None
    assert "answer_relevancy" in custom.default_metrics
    # Context/reference-dependent metrics are not defaults: they need more data.
    assert "faithfulness" not in custom.default_metrics
    assert {metric.name for metric in custom.metrics} >= {
        "answer_relevancy",
        "faithfulness",
        "answer_correctness",
        "context_precision",
        "context_recall",
    }

    # Optional evaluators report unavailability instead of breaking the catalog.
    for optional in (EvaluatorType.RAGAS, EvaluatorType.DEEPEVAL):
        entry = entries[optional]
        if not entry.available:
            assert entry.reason


# --- context extraction -----------------------------------------------------
def test_extract_contexts_reads_data_access_spans_and_tool_outputs(db, make_run):
    run = _completed_run(make_run)

    _trace(
        db,
        run,
        span_type=SpanType.DATA_ACCESS,
        output={"source": {"url": "https://example.com/water", "title": "Water cycle reference"}},
    )
    tool_trace = _trace(db, run, span_type=SpanType.TOOL_CALL)
    db.add(
        ToolCall(
            trace_id=tool_trace.id,
            tool_name="fetch_url",
            tool_input={"url": "https://example.com"},
            tool_output={"content": CONTEXT},
            success=True,
        )
    )
    # Failed accesses must not leak into the contexts.
    _trace(
        db,
        run,
        span_type=SpanType.DATA_ACCESS,
        output={"content": "this failed document should be ignored"},
        status=SpanStatus.ERROR,
    )
    trace = _trace(db, run, span_type=SpanType.TOOL_CALL)
    db.add(
        ToolCall(
            trace_id=trace.id,
            tool_name="fetch_url",
            tool_output={"content": "this failed tool output should be ignored"},
            success=False,
        )
    )
    db.commit()

    contexts = extract_contexts(db, run)

    assert any("Water cycle reference" in context for context in contexts)
    assert CONTEXT in contexts
    assert all("ignored" not in context for context in contexts)


def test_extract_contexts_deduplicates_and_caps_length(db, make_run, monkeypatch):
    monkeypatch.setattr("app.services.evaluation.settings.evaluation_max_contexts", 2)
    run = _completed_run(make_run)
    for _ in range(3):
        _trace(db, run, span_type=SpanType.DATA_ACCESS, output={"content": CONTEXT})
    _trace(db, run, span_type=SpanType.DATA_ACCESS, output={"content": "Another distinct document."})
    db.commit()

    contexts = extract_contexts(db, run)

    assert contexts.count(CONTEXT) == 1
    assert len(contexts) <= 2


# --- persistence ------------------------------------------------------------
def test_evaluate_skips_metrics_whose_inputs_are_missing(db, make_run):
    run = _completed_run(make_run)

    report = evaluate_run(
        db,
        run,
        evaluator_type=EvaluatorType.CUSTOM,
        metrics=["faithfulness", "context_recall", "answer_relevancy"],
    )

    assert [item.metric_name for item in report.evaluations] == ["answer_relevancy"]
    skipped = {item.metric_name: item.reason for item in report.skipped}
    assert "faithfulness" in skipped
    assert "context_recall" in skipped
    assert report.average_score == report.evaluations[0].score


def test_evaluate_persists_one_row_per_metric_and_replaces(db, make_run):
    run = _completed_run(make_run)

    first = evaluate_run(
        db,
        run,
        evaluator_type=EvaluatorType.CUSTOM,
        contexts=[CONTEXT],
        reference=REFERENCE,
        metrics=["answer_relevancy", "faithfulness", "answer_correctness"],
    )
    assert {item.metric_name for item in first.evaluations} == {
        "answer_relevancy",
        "faithfulness",
        "answer_correctness",
    }
    assert first.replaced == 0

    second = evaluate_run(
        db,
        run,
        evaluator_type=EvaluatorType.CUSTOM,
        contexts=[CONTEXT],
        reference=REFERENCE,
        metrics=["answer_relevancy"],
        replace=True,
    )
    assert second.replaced == 1
    assert db.query(Evaluation).filter(Evaluation.run_id == run.id).count() == 3
    # The report lists every score stored for this evaluator, not just the new one.
    assert len(second.evaluations) == 3

    third = evaluate_run(
        db,
        run,
        evaluator_type=EvaluatorType.CUSTOM,
        metrics=["answer_relevancy"],
        replace=False,
    )
    assert third.replaced == 0
    assert any(item.metric_name == "answer_relevancy" for item in third.skipped)


def test_evaluate_rejects_unknown_metrics(db, make_run):
    run = _completed_run(make_run)

    with pytest.raises(BadRequestError):
        evaluate_run(
            db,
            run,
            evaluator_type=EvaluatorType.CUSTOM,
            metrics=["not_a_metric"],
        )


def test_evaluate_requires_run_output(db, make_run):
    run = make_run(status=RunStatus.PENDING)

    with pytest.raises(BadRequestError):
        evaluate_run(db, run, evaluator_type=EvaluatorType.CUSTOM)


def test_optional_evaluator_reports_unavailability(db, make_run):
    catalog = {entry.evaluator_type: entry for entry in evaluator_catalog().evaluators}
    entry = catalog[EvaluatorType.RAGAS]
    if entry.available:
        pytest.skip("ragas is installed; the unavailability path cannot be exercised")

    run = _completed_run(make_run)
    with pytest.raises(ServiceUnavailableError):
        evaluate_run(db, run, evaluator_type=EvaluatorType.RAGAS)


def test_summary_and_aggregate_and_delete(db, make_run, make_agent):
    agent = make_agent(name="eval-agent")
    first = _completed_run(make_run, agent=agent)
    second = _completed_run(make_run, agent=agent)

    for run in (first, second):
        evaluate_run(
            db,
            run,
            evaluator_type=EvaluatorType.CUSTOM,
            metrics=["answer_relevancy"],
        )

    summary = summarize_run(db, first.id)
    assert summary.average_score == summary.evaluations[0].score
    assert summary.by_metric["answer_relevancy"].count == 1
    assert summary.by_evaluator["custom"].count == 1

    aggregate = aggregate_evaluations(db, agent_id=agent.id)
    assert aggregate.total_evaluations == 2
    metric = next(item for item in aggregate.items if item.metric_name == "answer_relevancy")
    assert metric.run_count == 2
    assert metric.evaluation_count == 2
    assert metric.minimum <= metric.average <= metric.maximum

    filtered = aggregate_evaluations(db, evaluator_type=EvaluatorType.DEEPEVAL)
    assert filtered.items == []

    assert delete_evaluations(db, first.id) == 1
    assert summarize_run(db, first.id).evaluations == []


def test_build_summary_handles_no_rows():
    run_id = uuid4()
    summary = build_summary(run_id, [])
    assert summary.run_id == run_id
    assert summary.average_score is None
    assert summary.by_metric == {}


def test_build_evaluation_input_trims_reference_and_reads_execution_model(make_run):
    run = _completed_run(
        make_run,
        metadata_json={"execution": {"model_name": "gpt-4o-mini"}},
    )

    data = build_evaluation_input(run, reference=f"  {REFERENCE}  ")

    assert data.prompt == PROMPT
    assert data.output == ANSWER
    assert data.reference == REFERENCE
    assert data.model_name == "gpt-4o-mini"
    assert data.total_tokens == 120
