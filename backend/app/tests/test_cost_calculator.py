"""Pricing, cost computation and aggregation tests."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from app.models import RunStatus
from app.services.cost_calculator import (
    DEFAULT_PRICING,
    PricingRegistry,
    aggregate_agent_costs,
    aggregate_run_cost,
    compute_cost,
    daily_cost_report,
    filter_pricing,
    normalize_model_name,
    quantize_cost,
)


def test_quantize_rounds_half_up_to_six_places():
    assert quantize_cost(Decimal("0.0000005")) == Decimal("0.000001")
    assert quantize_cost(Decimal("0.0000004")) == Decimal("0.000000")
    assert quantize_cost("1.23456789") == Decimal("1.234568")


def test_normalize_strips_prefixes_and_dates():
    assert normalize_model_name("openai/gpt-4o-mini-2024-07-18") == "gpt-4o-mini"
    assert normalize_model_name("  GPT-4O  ") == "gpt-4o"
    assert normalize_model_name("claude-sonnet-4-20250514") == "claude-sonnet-4"


def test_compute_cost_uses_per_million_pricing():
    # gpt-4o-mini: $0.15 / 1M input, $0.60 / 1M output.
    assert compute_cost("gpt-4o-mini", 1_000_000, 1_000_000) == Decimal("0.750000")
    assert compute_cost("gpt-4o-mini", 1_000, 500) == Decimal("0.000450")
    assert compute_cost("gpt-4o-mini-2024-07-18", 1_000_000, 0) == Decimal("0.150000")


def test_unknown_model_is_free_but_tracked():
    registry = PricingRegistry()

    assert registry.compute("totally-unknown-model", 1_000, 1_000) == Decimal("0")
    assert registry.unknown_models() == ["totally-unknown-model"]


def test_override_takes_precedence():
    registry = PricingRegistry()
    registry.set_override("gpt-4o-mini", Decimal("1"), Decimal("2"))

    assert registry.compute("gpt-4o-mini", 1_000_000, 1_000_000) == Decimal("3.000000")


def test_json_overrides_are_validated():
    registry = PricingRegistry()
    registry.load_overrides_from_json(
        '{"custom-model": {"input": 0.5, "output": 1.5}, "bad": {"input": "x"}}'
    )

    assert registry.compute("custom-model", 1_000_000, 0) == Decimal("0.500000")
    assert "bad" not in registry.known_models()


def test_negative_token_counts_are_clamped():
    registry = PricingRegistry()

    assert registry.compute("gpt-4o-mini", -100, -100) == Decimal("0")


def test_filter_pricing_returns_only_known_models():
    assert "gpt-4o" in filter_pricing(["gpt-4o", "nope"])
    assert "nope" not in filter_pricing(["gpt-4o", "nope"])


def test_default_pricing_table_is_consistent():
    for name, pricing in DEFAULT_PRICING.items():
        assert pricing.input_per_million >= 0
        assert pricing.output_per_million >= 0
        assert pricing.provider


def test_aggregation_by_run_and_agent(db, make_agent, make_run):
    first_agent = make_agent(name="agent-a")
    second_agent = make_agent(name="agent-b")
    first = make_run(
        first_agent,
        status=RunStatus.COMPLETED,
        total_tokens_used=1_000,
        total_cost_usd=Decimal("0.100000"),
        latency_ms=1_000,
    )
    make_run(
        first_agent,
        status=RunStatus.FAILED,
        total_tokens_used=500,
        total_cost_usd=Decimal("0.050000"),
        latency_ms=2_000,
    )
    make_run(
        second_agent,
        status=RunStatus.COMPLETED,
        total_tokens_used=100,
        total_cost_usd=Decimal("0.010000"),
        latency_ms=500,
    )
    db.commit()

    assert aggregate_run_cost(db, first.id) == Decimal("0")

    rows = aggregate_agent_costs(db)
    by_name = {row["agent_name"]: row for row in rows}
    assert by_name["agent-a"]["run_count"] == 2
    assert by_name["agent-a"]["total_tokens"] == 1_500
    assert by_name["agent-a"]["total_cost_usd"] == 0.15
    assert by_name["agent-a"]["avg_latency_ms"] == 1_500

    only_b = aggregate_agent_costs(db, agent_id=second_agent.id)
    assert [row["agent_name"] for row in only_b] == ["agent-b"]


def test_daily_cost_report_buckets_utc_day(db, make_agent, make_run):
    agent = make_agent()
    today = date(2026, 5, 1)
    inside = datetime(2026, 5, 1, 12, 0, tzinfo=timezone.utc)
    outside = inside - timedelta(days=2)

    make_run(
        agent,
        status=RunStatus.COMPLETED,
        total_tokens_used=2_000,
        total_cost_usd=Decimal("0.250000"),
        latency_ms=400,
        created_at=inside,
    )
    make_run(
        agent,
        status=RunStatus.FAILED,
        total_tokens_used=100,
        total_cost_usd=Decimal("0.010000"),
        latency_ms=600,
        created_at=inside,
    )
    make_run(
        agent,
        status=RunStatus.COMPLETED,
        total_tokens_used=9_999,
        total_cost_usd=Decimal("9.000000"),
        created_at=outside,
    )
    db.commit()

    report = daily_cost_report(db, today)

    assert report["date"] == "2026-05-01"
    assert report["run_count"] == 2
    assert report["total_tokens"] == 2_100
    assert report["total_cost_usd"] == 0.26
    assert report["avg_latency_ms"] == 500
    assert report["by_status"] == {"completed": 1, "failed": 1}
    assert report["by_agent"][0]["agent_name"] == "test-agent"
