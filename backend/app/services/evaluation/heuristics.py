"""Deterministic, offline quality metrics.

These metrics are lexical approximations of the Ragas/DeepEval families. They are
free (no judge model, no network), fully deterministic and therefore suitable as
the default evaluator and for CI. They are intentionally honest: a lexical score
measures term overlap, not semantic equivalence, and the reason string says so.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass

from app.models import EvaluatorType
from app.services.evaluation.base import (
    EvaluationInput,
    Evaluator,
    MetricDefinition,
    MetricResult,
)

_WORD_RE = re.compile(r"[a-z0-9]+(?:'[a-z]+)?")
_SENTENCE_RE = re.compile(r"[^.!?\n]+[.!?]?")

_STOPWORDS = frozenset(
    """
    a about above after again against all am an and any are aren't as at be because
    been before being below between both but by can can't cannot could couldn't did
    didn't do does doesn't doing don't down during each few for from further had
    hadn't has hasn't have haven't having he he'd he'll he's her here here's hers
    herself him himself his how how's i i'd i'll i'm i've if in into is isn't it
    it's its itself let's me more most mustn't my myself no nor not of off on once
    only or other ought our ours ourselves out over own same shan't she she'd
    she'll she's should shouldn't so some such than that that's the their theirs
    them themselves then there there's these they they'd they'll they're they've
    this those through to too under until up very was wasn't we we'd we'll we're
    we've were weren't what what's when when's where where's which while who who's
    whom why why's with won't would wouldn't you you'd you'll you're you've your
    yours yourself yourselves
    """.split()
)

_MIN_CLAIM_TERMS = 3
# A claim whose terms are at least this covered by the contexts is "supported".
_SUPPORT_THRESHOLD = 0.6


@dataclass(frozen=True)
class _Score:
    value: float
    reason: str


def content_terms(text: str) -> list[str]:
    """Lower-case words with stopwords and single characters removed."""
    return [
        match.group(0)
        for match in _WORD_RE.finditer(text.lower())
        if len(match.group(0)) > 1 and match.group(0) not in _STOPWORDS
    ]


def _clamp(value: float) -> float:
    return max(0.0, min(1.0, value))


def _coverage(terms: set[str], reference: set[str]) -> float:
    if not terms:
        return 0.0
    return len(terms & reference) / len(terms)


def answer_relevancy(data: EvaluationInput) -> _Score:
    prompt_terms = set(content_terms(data.prompt))
    answer_terms = set(content_terms(data.output))
    if not answer_terms:
        return _Score(0.0, "The answer contains no content words.")
    if not prompt_terms:
        return _Score(0.0, "The prompt contains no content words to compare against.")
    coverage = _coverage(prompt_terms, answer_terms)
    matched = len(prompt_terms & answer_terms)
    return _Score(
        _clamp(coverage),
        f"Lexical overlap: {matched}/{len(prompt_terms)} prompt terms appear in the answer "
        "(deterministic approximation, not a semantic judgement).",
    )


def faithfulness(data: EvaluationInput) -> _Score:
    if not data.has_contexts:
        return _Score(0.0, "Faithfulness requires retrieved contexts.")
    context_terms = set(content_terms(" ".join(data.contexts)))
    if not context_terms:
        return _Score(0.0, "The supplied contexts contain no content words.")

    claims = [
        sentence.strip()
        for sentence in _SENTENCE_RE.findall(data.output)
        if len(set(content_terms(sentence))) >= _MIN_CLAIM_TERMS
    ]
    if not claims:
        return _Score(0.0, "The answer contains no verifiable claims (sentences with content).")

    supports: list[float] = []
    fully_supported = 0
    for claim in claims:
        claim_terms = set(content_terms(claim))
        support = _coverage(claim_terms, context_terms)
        supports.append(support)
        if support >= _SUPPORT_THRESHOLD:
            fully_supported += 1

    score = _clamp(sum(supports) / len(supports))
    return _Score(
        score,
        f"Mean lexical support {score:.2f} across {len(claims)} claim(s); "
        f"{fully_supported} claim(s) reached the {_SUPPORT_THRESHOLD:.1f} support threshold.",
    )


def answer_correctness(data: EvaluationInput) -> _Score:
    if not data.has_reference:
        return _Score(0.0, "Answer correctness requires a reference answer.")
    output_terms = Counter(content_terms(data.output))
    reference_terms = Counter(content_terms(data.reference or ""))
    if not output_terms or not reference_terms:
        return _Score(0.0, "The answer or the reference contains no content words.")

    overlap = sum((output_terms & reference_terms).values())
    precision = overlap / sum(output_terms.values())
    recall = overlap / sum(reference_terms.values())
    if precision + recall == 0:
        return _Score(0.0, "No content words are shared with the reference answer.")
    f1 = 2 * precision * recall / (precision + recall)
    return _Score(
        _clamp(f1),
        f"Token F1 against the reference: precision {precision:.2f}, recall {recall:.2f}.",
    )


def context_precision(data: EvaluationInput) -> _Score:
    if not data.has_contexts:
        return _Score(0.0, "Context precision requires retrieved contexts.")
    target = data.reference or data.prompt
    target_terms = set(content_terms(target))
    if not target_terms:
        return _Score(0.0, "The prompt/reference contains no content words to judge relevance.")

    precisions = []
    for context in data.contexts:
        terms = set(content_terms(context))
        if terms:
            precisions.append(_coverage(terms, target_terms))
    if not precisions:
        return _Score(0.0, "The supplied contexts contain no content words.")
    score = _clamp(sum(precisions) / len(precisions))
    return _Score(
        score,
        f"Mean share of context terms relevant to the {'reference' if data.reference else 'prompt'}: "
        f"{score:.2f}.",
    )


def context_recall(data: EvaluationInput) -> _Score:
    if not data.has_contexts:
        return _Score(0.0, "Context recall requires retrieved contexts.")
    if not data.has_reference:
        return _Score(0.0, "Context recall requires a reference answer.")
    reference_terms = set(content_terms(data.reference or ""))
    if not reference_terms:
        return _Score(0.0, "The reference contains no content words.")
    context_terms = set(content_terms(" ".join(data.contexts)))
    score = _coverage(reference_terms, context_terms)
    return _Score(
        _clamp(score),
        f"{int(score * len(reference_terms))}/{len(reference_terms)} reference terms are "
        "present in the retrieved contexts.",
    )


_DEFINITIONS: tuple[MetricDefinition, ...] = (
    MetricDefinition(
        "answer_relevancy",
        "Share of distinctive prompt terms that appear in the answer.",
    ),
    MetricDefinition(
        "faithfulness",
        "Share of answer claim terms supported by the retrieved contexts.",
        requires_contexts=True,
    ),
    MetricDefinition(
        "answer_correctness",
        "Token F1 between the answer and a reference answer.",
        requires_reference=True,
    ),
    MetricDefinition(
        "context_precision",
        "Share of context terms relevant to the prompt or reference.",
        requires_contexts=True,
    ),
    MetricDefinition(
        "context_recall",
        "Share of reference terms present in the retrieved contexts.",
        requires_contexts=True,
        requires_reference=True,
    ),
)

_FUNCTIONS = {
    "answer_relevancy": answer_relevancy,
    "faithfulness": faithfulness,
    "answer_correctness": answer_correctness,
    "context_precision": context_precision,
    "context_recall": context_recall,
}


class HeuristicEvaluator(Evaluator):
    """Dependency-free lexical metrics (no LLM calls, no cost)."""

    evaluator_type = EvaluatorType.CUSTOM
    display_name = "Custom (deterministic heuristics)"

    def definitions(self) -> list[MetricDefinition]:
        return list(_DEFINITIONS)

    def availability(self) -> tuple[bool, str | None]:
        return True, None

    def evaluate(
        self, data: EvaluationInput, metrics: list[MetricDefinition]
    ) -> list[MetricResult]:
        results: list[MetricResult] = []
        for definition in metrics:
            function = _FUNCTIONS[definition.name]
            score = function(data)
            results.append(
                MetricResult(
                    metric_name=definition.name,
                    score=round(_clamp(score.value), 4),
                    reason=score.reason,
                )
            )
        return results
