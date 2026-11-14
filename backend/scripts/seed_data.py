"""Seed the control room with demo agents, runs and traces.

Usage (from ``backend/``)::

    python -m scripts.seed_data
    python -m scripts.seed_data --reset   # remove previously seeded demo data first

The script is idempotent: agents are matched by name and demo runs carry
``metadata.seed = true`` so a second invocation does not duplicate them. It never
calls an LLM provider; runs, spans and tool calls are synthesised so the
dashboard is populated without spending tokens.
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import uuid4

# Allow ``python scripts/seed_data.py`` in addition to ``python -m scripts.seed_data``.
BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from sqlalchemy import delete, select  # noqa: E402

from app.core.database import SessionLocal  # noqa: E402
from app.models import (  # noqa: E402
    Agent,
    Evaluation,
    EvaluatorType,
    Run,
    RunStatus,
    SpanStatus,
    SpanType,
    ToolCall,
    Trace,
    utcnow,
)
from app.services.cost_calculator import compute_cost, quantize_cost  # noqa: E402

SEED_MARKER = "seed"


def _agent_specs() -> list[dict[str, Any]]:
    return [
        {
            "name": "Support Triage",
            "description": "Classifies inbound tickets and drafts a first reply.",
            "model_name": "gpt-4o-mini",
            "system_prompt": (
                "You are a support triage assistant. Classify the ticket, state the "
                "priority, and draft a concise, empathetic reply."
            ),
            "tools_config": [{"name": "current_time", "config": {}}],
        },
        {
            "name": "Research Analyst",
            "description": "Gathers sources, computes figures and writes summaries.",
            "model_name": "gpt-4o-mini",
            "system_prompt": (
                "You are a rigorous research analyst. Use tools for arithmetic and "
                "cite every external source you consult."
            ),
            "tools_config": [{"name": "calculator", "config": {}}, {"name": "http_get", "config": {}}],
        },
        {
            "name": "Data Steward",
            "description": "Answers questions about the warehouse schema.",
            "model_name": "claude-3-5-haiku-20241022",
            "system_prompt": "You explain database schemas precisely and briefly.",
            "tools_config": [],
        },
    ]


def _ensure_agents(db) -> dict[str, Agent]:
    agents: dict[str, Agent] = {}
    for spec in _agent_specs():
        agent = db.scalar(select(Agent).where(Agent.name == spec["name"]))
        if agent is None:
            agent = Agent(**spec, is_active=True)
            db.add(agent)
            db.flush()
            print(f"  + agent {agent.name} ({agent.model_name})")
        else:
            print(f"  = agent {agent.name} already exists")
        agents[agent.name] = agent
    return agents


def _make_span(
    *,
    run: Run,
    trace_id: str,
    span_name: str,
    span_type: SpanType,
    started_at: datetime,
    duration_ms: int,
    parent: Trace | None = None,
    status: SpanStatus = SpanStatus.OK,
    input_data: dict[str, Any] | None = None,
    output_data: dict[str, Any] | None = None,
    tokens_input: int = 0,
    tokens_output: int = 0,
    cost_usd: Decimal = Decimal("0"),
    model_name: str | None = None,
    error_details: dict[str, Any] | None = None,
) -> Trace:
    return Trace(
        run_id=run.id,
        parent_trace_id=parent.id if parent else None,
        span_name=span_name,
        span_type=span_type,
        input_data=input_data,
        output_data=output_data,
        status=status,
        duration_ms=max(duration_ms, 0),
        tokens_used=tokens_input + tokens_output,
        tokens_input=tokens_input,
        tokens_output=tokens_output,
        cost_usd=quantize_cost(cost_usd),
        error_details=error_details,
        timestamp=started_at,
        trace_id=trace_id,
        span_id=uuid4().hex[:16],
        model_name=model_name,
    )


def _seed_run(
    db,
    agent: Agent,
    *,
    prompt: str,
    output: str | None,
    status: RunStatus,
    created_at: datetime,
    latency_ms: int,
    tokens_input: int,
    tokens_output: int,
    with_tool: bool,
    error_message: str | None = None,
) -> Run:
    total_cost = compute_cost(agent.model_name, tokens_input, tokens_output)
    run = Run(
        agent_id=agent.id,
        input_prompt=prompt,
        output_response=output,
        status=status,
        created_at=created_at,
        started_at=created_at + timedelta(milliseconds=300),
        completed_at=created_at + timedelta(milliseconds=300 + latency_ms),
        latency_ms=latency_ms,
        total_tokens_used=tokens_input + tokens_output,
        total_cost_usd=total_cost,
        error_message=error_message,
        metadata_json={SEED_MARKER: True, "requested_by": "seed"},
    )
    db.add(run)
    db.flush()

    trace_id = uuid4().hex
    run.metadata_json["trace_id"] = trace_id

    root_started = created_at
    root = _make_span(
        run=run,
        trace_id=trace_id,
        span_name="agent.run",
        span_type=SpanType.DECISION,
        started_at=root_started,
        duration_ms=latency_ms + 300,
        input_data={"input_prompt": prompt, "model": agent.model_name},
        output_data={"output_response": output},
        tokens_input=tokens_input,
        tokens_output=tokens_output,
        cost_usd=total_cost,
        model_name=agent.model_name,
        status=SpanStatus.ERROR if status == RunStatus.FAILED else SpanStatus.OK,
        error_details={"message": error_message} if error_message else None,
    )
    db.add(root)
    db.flush()

    decision = _make_span(
        run=run,
        trace_id=trace_id,
        span_name="agent.iteration.1",
        span_type=SpanType.DECISION,
        started_at=root_started + timedelta(milliseconds=20),
        duration_ms=latency_ms,
        parent=root,
        input_data={"iteration": 1, "message_count": 2},
        output_data={"action": "final_answer" if not with_tool else "tools", "iteration": 1},
    )
    db.add(decision)
    db.flush()

    # Token usage is split across generation steps; the root span keeps the total.
    first_input = int(tokens_input * 0.6)
    first_output = int(tokens_output * 0.5)
    first_cost = compute_cost(agent.model_name, first_input, first_output)
    llm = _make_span(
        run=run,
        trace_id=trace_id,
        span_name=f"llm.{agent.model_name}",
        span_type=SpanType.LLM_CALL,
        started_at=root_started + timedelta(milliseconds=40),
        duration_ms=max(latency_ms // 2, 1),
        parent=decision,
        input_data={"model": agent.model_name, "messages": [{"role": "user", "content": prompt}]},
        output_data={"content": output, "finish_reason": "stop"},
        tokens_input=first_input,
        tokens_output=first_output,
        cost_usd=first_cost,
        model_name=agent.model_name,
    )
    db.add(llm)
    db.flush()

    if with_tool:
        tool_span = _make_span(
            run=run,
            trace_id=trace_id,
            span_name="tool.calculator",
            span_type=SpanType.TOOL_CALL,
            started_at=root_started + timedelta(milliseconds=80),
            duration_ms=12,
            parent=decision,
            input_data={"arguments": {"expression": "128 * 4"}},
            output_data={"tool_call_id": "seed-call-1", "output": {"result": 512}},
        )
        db.add(tool_span)
        db.flush()
        db.add(
            ToolCall(
                trace_id=tool_span.id,
                tool_name="calculator",
                tool_input={"expression": "128 * 4"},
                tool_output={"result": 512},
                duration_ms=12,
                success=True,
            )
        )
        # Second LLM step after the tool result.
        second_input = max(tokens_input - first_input, 0)
        second_output = max(tokens_output - first_output, 0)
        db.add(
            _make_span(
                run=run,
                trace_id=trace_id,
                span_name=f"llm.{agent.model_name}",
                span_type=SpanType.LLM_CALL,
                started_at=root_started + timedelta(milliseconds=100),
                duration_ms=max(latency_ms // 3, 1),
                parent=decision,
                input_data={"model": agent.model_name},
                output_data={"content": output, "finish_reason": "stop"},
                tokens_input=second_input,
                tokens_output=second_output,
                cost_usd=compute_cost(agent.model_name, second_input, second_output),
                model_name=agent.model_name,
            )
        )

    if status == RunStatus.COMPLETED:
        db.add(
            Evaluation(
                run_id=run.id,
                evaluator_type=EvaluatorType.CUSTOM,
                metric_name="answer_relevance",
                score=Decimal("0.8700"),
                reason="Seeded evaluation: answer addresses the request directly.",
            )
        )

    return run


def _seed_runs(db, agents: dict[str, Agent]) -> int:
    now = utcnow().replace(microsecond=0)
    scenarios = [
        {
            "agent": agents["Support Triage"],
            "prompt": "Customer says the export button does nothing after the latest update.",
            "output": "Priority: high. Ask for browser/version, confirm export is enabled, and offer the CSV workaround.",
            "status": RunStatus.COMPLETED,
            "offset_minutes": 15,
            "latency_ms": 1_840,
            "tokens_input": 620,
            "tokens_output": 210,
            "with_tool": False,
        },
        {
            "agent": agents["Research Analyst"],
            "prompt": "How many units were sold if each of 128 stores sold 4 units? Use the calculator.",
            "output": "128 stores x 4 units = 512 units.",
            "status": RunStatus.COMPLETED,
            "offset_minutes": 95,
            "latency_ms": 3_420,
            "tokens_input": 1_180,
            "tokens_output": 190,
            "with_tool": True,
        },
        {
            "agent": agents["Data Steward"],
            "prompt": "List the columns of the fact_orders table.",
            "output": "order_id, customer_id, ordered_at, amount_usd, currency.",
            "status": RunStatus.COMPLETED,
            "offset_minutes": 60 * 6,
            "latency_ms": 1_120,
            "tokens_input": 410,
            "tokens_output": 80,
            "with_tool": False,
        },
        {
            "agent": agents["Research Analyst"],
            "prompt": "Summarise https://example.com/report and compute the growth rate.",
            "output": None,
            "status": RunStatus.FAILED,
            "offset_minutes": 60 * 26,
            "latency_ms": 8_400,
            "tokens_input": 900,
            "tokens_output": 0,
            "with_tool": False,
            "error_message": "LLMTimeoutError: upstream provider timed out after 120s",
        },
        {
            "agent": agents["Support Triage"],
            "prompt": "Draft a refund policy response for a duplicate charge.",
            "output": "Acknowledge the duplicate charge, confirm the refund timeline, and provide a ticket reference.",
            "status": RunStatus.COMPLETED,
            "offset_minutes": 60 * 49,
            "latency_ms": 2_260,
            "tokens_input": 740,
            "tokens_output": 185,
            "with_tool": False,
        },
    ]

    existing = db.scalar(
        select(Run.id).where(Run.metadata_json[SEED_MARKER].as_boolean().is_(True)).limit(1)
    )
    if existing is not None:
        print("  = demo runs already present; skipping (use --reset to recreate)")
        return 0

    for scenario in scenarios:
        _seed_run(
            db,
            scenario["agent"],
            prompt=scenario["prompt"],
            output=scenario["output"],
            status=scenario["status"],
            created_at=now - timedelta(minutes=scenario["offset_minutes"]),
            latency_ms=scenario["latency_ms"],
            tokens_input=scenario["tokens_input"],
            tokens_output=scenario["tokens_output"],
            with_tool=scenario["with_tool"],
            error_message=scenario.get("error_message"),
        )
    print(f"  + {len(scenarios)} demo runs with traces")
    return len(scenarios)


def _reset(db) -> None:
    seeded_runs = db.scalars(
        select(Run).where(Run.metadata_json[SEED_MARKER].as_boolean().is_(True))
    ).all()
    if seeded_runs:
        run_ids = [run.id for run in seeded_runs]
        db.execute(delete(Evaluation).where(Evaluation.run_id.in_(run_ids)))
        db.execute(delete(Trace).where(Trace.run_id.in_(run_ids)))
        db.execute(delete(Run).where(Run.id.in_(run_ids)))
        print(f"  - removed {len(run_ids)} seeded runs")

    seeded_agents = db.scalars(
        select(Agent).where(Agent.name.in_([spec["name"] for spec in _agent_specs()]))
    ).all()
    for agent in seeded_agents:
        remaining = db.scalar(select(Run.id).where(Run.agent_id == agent.id).limit(1))
        if remaining is None:
            db.delete(agent)
            print(f"  - removed agent {agent.name}")


def seed(reset: bool = False) -> None:
    db = SessionLocal()
    try:
        if reset:
            print("Resetting seeded data...")
            _reset(db)
            db.commit()

        print("Creating demo agents...")
        agents = _ensure_agents(db)
        print("Creating demo runs...")
        _seed_runs(db, agents)
        db.commit()
        print("Seed complete. Open http://localhost:8000/docs or the dashboard.")
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--reset",
        action="store_true",
        help="delete previously seeded demo runs/agents before seeding",
    )
    args = parser.parse_args()
    seed(reset=args.reset)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
