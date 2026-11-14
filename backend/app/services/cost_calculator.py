"""Model pricing and cost aggregation.

Prices are expressed in **USD per one million tokens** to match how providers
publish them. Everything is calculated with :class:`decimal.Decimal` and rounded
to six decimal places, matching the ``NUMERIC(10, 6)`` database columns.

Provider prices change frequently; the table below is a starting point that can
be overridden at runtime through ``CUSTOM_PRICING_JSON`` or :func:`set_override`.
"""

from __future__ import annotations

import json
import logging
import re
import threading
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any, Iterable
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models import Agent, Run, Trace

logger = logging.getLogger(__name__)

_MICRO = Decimal("0.000001")
_DATE_SUFFIX = re.compile(r"-(?:20\d{2}-\d{2}-\d{2}|\d{8}|latest)$", re.IGNORECASE)
_PROVIDER_PREFIX = re.compile(r"^(openai|anthropic|azure|local|ollama|openrouter)/", re.IGNORECASE)


def quantize_cost(value: Decimal | float | int) -> Decimal:
    """Round a monetary value to the database precision."""
    if not isinstance(value, Decimal):
        value = Decimal(str(value))
    return value.quantize(_MICRO, rounding=ROUND_HALF_UP)


@dataclass(frozen=True)
class ModelPricing:
    """USD per million tokens."""

    input_per_million: Decimal
    output_per_million: Decimal
    provider: str = "unknown"

    @property
    def is_free(self) -> bool:
        return self.input_per_million == 0 and self.output_per_million == 0


def _pricing(input_price: str, output_price: str, provider: str) -> ModelPricing:
    return ModelPricing(Decimal(input_price), Decimal(output_price), provider)


# Sorted longest-first at lookup time, so "gpt-4o-mini" beats "gpt-4o".
DEFAULT_PRICING: dict[str, ModelPricing] = {
    # OpenAI
    "gpt-4o": _pricing("2.50", "10.00", "openai"),
    "gpt-4o-mini": _pricing("0.15", "0.60", "openai"),
    "gpt-4.1": _pricing("2.00", "8.00", "openai"),
    "gpt-4.1-mini": _pricing("0.40", "1.60", "openai"),
    "gpt-4.1-nano": _pricing("0.10", "0.40", "openai"),
    "gpt-4-turbo": _pricing("10.00", "30.00", "openai"),
    "gpt-4": _pricing("30.00", "60.00", "openai"),
    "gpt-3.5-turbo": _pricing("0.50", "1.50", "openai"),
    "o1": _pricing("15.00", "60.00", "openai"),
    "o1-mini": _pricing("1.10", "4.40", "openai"),
    "o3": _pricing("2.00", "8.00", "openai"),
    "o3-mini": _pricing("1.10", "4.40", "openai"),
    "o4-mini": _pricing("1.10", "4.40", "openai"),
    # Anthropic
    "claude-3-5-sonnet": _pricing("3.00", "15.00", "anthropic"),
    "claude-3-5-haiku": _pricing("0.80", "4.00", "anthropic"),
    "claude-3-7-sonnet": _pricing("3.00", "15.00", "anthropic"),
    "claude-sonnet-4": _pricing("3.00", "15.00", "anthropic"),
    "claude-opus-4": _pricing("15.00", "75.00", "anthropic"),
    "claude-haiku-4-5": _pricing("1.00", "5.00", "anthropic"),
}


def normalize_model_name(model: str) -> str:
    """Strip provider prefixes, whitespace and dated suffixes."""
    normalized = model.strip().lower()
    normalized = _PROVIDER_PREFIX.sub("", normalized)
    normalized = _DATE_SUFFIX.sub("", normalized)
    return normalized


class PricingRegistry:
    """Resolves model names to prices and applies operator overrides."""

    def __init__(
        self,
        pricing: dict[str, ModelPricing] | None = None,
        overrides: dict[str, ModelPricing] | None = None,
    ) -> None:
        self._pricing = dict(pricing or DEFAULT_PRICING)
        self._overrides: dict[str, ModelPricing] = {}
        self._unknown_models: set[str] = set()
        self._lock = threading.Lock()
        if overrides:
            self.set_overrides(overrides)

    # -- overrides -----------------------------------------------------
    def set_override(self, model: str, input_per_million: Decimal, output_per_million: Decimal) -> None:
        with self._lock:
            self._overrides[normalize_model_name(model)] = ModelPricing(
                Decimal(input_per_million), Decimal(output_per_million), "custom"
            )

    def set_overrides(self, overrides: dict[str, ModelPricing]) -> None:
        for model, price in overrides.items():
            self._overrides[normalize_model_name(model)] = price

    def load_overrides_from_json(self, raw: str) -> None:
        """Parse ``{"model": {"input": 1, "output": 2}}`` style overrides."""
        try:
            payload = json.loads(raw or "{}")
        except json.JSONDecodeError:
            logger.exception("Ignoring invalid custom pricing JSON")
            return
        if not isinstance(payload, dict):
            logger.warning("custom_pricing_json must be an object; ignoring")
            return

        for model, price in payload.items():
            try:
                if isinstance(price, dict):
                    input_value = price.get("input", price.get("input_per_million"))
                    output_value = price.get("output", price.get("output_per_million"))
                elif isinstance(price, (list, tuple)) and len(price) == 2:
                    input_value, output_value = price
                else:
                    raise ValueError("expected an object with input/output")
                if input_value is None or output_value is None:
                    raise ValueError("both input and output prices are required")
                self.set_override(model, Decimal(str(input_value)), Decimal(str(output_value)))
            except (InvalidOperation, ValueError, TypeError):
                logger.warning("Ignoring invalid pricing override for %r: %r", model, price)

    # -- lookup --------------------------------------------------------
    def _local(self, normalized: str) -> bool:
        return any(normalized.startswith(prefix) for prefix in settings.local_model_prefix_list)

    def resolve(self, model: str | None) -> ModelPricing | None:
        if not model:
            return None

        normalized = normalize_model_name(model)

        with self._lock:
            if normalized in self._overrides:
                return self._overrides[normalized]
            if normalized in self._pricing:
                return self._pricing[normalized]

            # Longest matching prefix handles dated/suffixed model ids.
            candidates = [
                key
                for key in list(self._pricing) + list(self._overrides)
                if normalized.startswith(key)
            ]
            if candidates:
                best = max(candidates, key=len)
                return self._overrides.get(best) or self._pricing.get(best)

            if self._local(normalized):
                return ModelPricing(Decimal("0"), Decimal("0"), "local")

            if normalized not in self._unknown_models:
                self._unknown_models.add(normalized)
                logger.warning(
                    "No price configured for model %r; costs will be recorded as 0", model
                )
            return None

    def compute(self, model: str | None, input_tokens: int, output_tokens: int) -> Decimal:
        input_tokens = max(int(input_tokens or 0), 0)
        output_tokens = max(int(output_tokens or 0), 0)
        price = self.resolve(model)
        if price is None:
            return Decimal("0")
        cost = (
            Decimal(input_tokens) * price.input_per_million
            + Decimal(output_tokens) * price.output_per_million
        ) / Decimal(1_000_000)
        return quantize_cost(cost)

    def known_models(self) -> dict[str, dict[str, float]]:
        with self._lock:
            merged = {**self._pricing, **self._overrides}
            return {
                name: {
                    "input_per_million": float(price.input_per_million),
                    "output_per_million": float(price.output_per_million),
                    "provider": price.provider,
                }
                for name, price in sorted(merged.items())
            }

    def unknown_models(self) -> list[str]:
        return sorted(self._unknown_models)


pricing_registry = PricingRegistry()
pricing_registry.load_overrides_from_json(settings.custom_pricing_json)


def compute_cost(model: str | None, input_tokens: int, output_tokens: int) -> Decimal:
    """Convenience wrapper around the process-wide registry."""
    return pricing_registry.compute(model, input_tokens, output_tokens)


def set_override(model: str, input_per_million: Decimal, output_per_million: Decimal) -> None:
    pricing_registry.set_override(model, input_per_million, output_per_million)


# --- Aggregation ------------------------------------------------------------
def aggregate_run_cost(db: Session, run_id: UUID) -> Decimal:
    """Sum span costs for a run (useful for reconciliation against the run total)."""
    total = db.scalar(
        select(func.coalesce(func.sum(Trace.cost_usd), 0)).where(Trace.run_id == run_id)
    )
    return quantize_cost(total or 0)


def aggregate_agent_costs(
    db: Session,
    *,
    agent_id: UUID | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
) -> list[dict[str, Any]]:
    """Per-agent run counts, token totals, cost and latency aggregates."""
    stmt = (
        select(
            Agent.id,
            Agent.name,
            func.count(Run.id).label("run_count"),
            func.coalesce(func.sum(Run.total_tokens_used), 0).label("tokens"),
            func.coalesce(func.sum(Run.total_cost_usd), 0).label("cost"),
            func.coalesce(func.avg(Run.latency_ms), 0).label("avg_latency_ms"),
        )
        .join(Run, Run.agent_id == Agent.id)
        .group_by(Agent.id, Agent.name)
        .order_by(func.coalesce(func.sum(Run.total_cost_usd), 0).desc())
    )
    if agent_id is not None:
        stmt = stmt.where(Agent.id == agent_id)
    if since is not None:
        stmt = stmt.where(Run.created_at >= since)
    if until is not None:
        stmt = stmt.where(Run.created_at < until)

    rows = db.execute(stmt).all()
    return [
        {
            "agent_id": row.id,
            "agent_name": row.name,
            "run_count": int(row.run_count),
            "total_tokens": int(row.tokens),
            "total_cost_usd": float(quantize_cost(row.cost or 0)),
            "avg_latency_ms": float(row.avg_latency_ms or 0),
        }
        for row in rows
    ]


def _day_bounds(target: date) -> tuple[datetime, datetime]:
    start = datetime.combine(target, time.min, tzinfo=timezone.utc)
    return start, start + timedelta(days=1)


def daily_cost_report(db: Session, target: date) -> dict[str, Any]:
    """Aggregate one UTC day of spend, grouped by agent and status."""
    start, end = _day_bounds(target)

    totals = db.execute(
        select(
            func.count(Run.id),
            func.coalesce(func.sum(Run.total_tokens_used), 0),
            func.coalesce(func.sum(Run.total_cost_usd), 0),
            func.coalesce(func.avg(Run.latency_ms), 0),
        ).where(Run.created_at >= start, Run.created_at < end)
    ).one()

    by_status = db.execute(
        select(Run.status, func.count(Run.id))
        .where(Run.created_at >= start, Run.created_at < end)
        .group_by(Run.status)
    ).all()

    return {
        "date": target.isoformat(),
        "run_count": int(totals[0] or 0),
        "total_tokens": int(totals[1] or 0),
        "total_cost_usd": float(quantize_cost(totals[2] or 0)),
        "avg_latency_ms": float(totals[3] or 0),
        "by_status": {status.value: int(count) for status, count in by_status},
        "by_agent": aggregate_agent_costs(db, since=start, until=end),
    }


def filter_pricing(models: Iterable[str]) -> dict[str, dict[str, float]]:
    """Return the pricing subset for the given models (API helper)."""
    known = pricing_registry.known_models()
    return {model: known[model] for model in models if model in known}
