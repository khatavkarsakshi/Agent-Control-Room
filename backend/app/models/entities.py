"""ORM models for the Agent Control Room.

Schema overview
---------------
``agents``           registered agent configurations
``runs``             one execution of an agent
``traces``           span-level execution records (OpenTelemetry compatible)
``tool_calls``       tool invocations attached to a trace span
``evaluations``      quality metric results for a run
``replay_sessions``  original-vs-replay comparisons

Money is stored as ``NUMERIC(10, 6)`` (6 decimal places covers sub-cent token
costs). Timestamps are timezone aware. JSONB is used on PostgreSQL and falls back
to JSON on SQLite so the test suite runs without a database container.
"""

from __future__ import annotations

import enum
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

import sqlalchemy as sa
from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.mutable import MutableDict, MutableList
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def json_dict_type() -> Any:
    """Mutable JSONB dict, with a portable JSON variant for SQLite tests."""
    return MutableDict.as_mutable(JSONB().with_variant(sa.JSON(), "sqlite"))


def json_list_type() -> Any:
    return MutableList.as_mutable(JSONB().with_variant(sa.JSON(), "sqlite"))


def enum_type(enum_cls: type[enum.Enum], name: str) -> sa.Enum:
    """Store enum *values* (lower case) rather than member names."""
    return sa.Enum(
        enum_cls,
        name=name,
        values_callable=lambda cls: [member.value for member in cls],
        validate_strings=True,
    )


# --- Enumerations -----------------------------------------------------------
class RunStatus(str, enum.Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    REPLAYED = "replayed"


class SpanType(str, enum.Enum):
    LLM_CALL = "llm_call"
    TOOL_CALL = "tool_call"
    DATA_ACCESS = "data_access"
    DECISION = "decision"
    ERROR = "error"


class SpanStatus(str, enum.Enum):
    OK = "ok"
    ERROR = "error"
    TIMEOUT = "timeout"


class EvaluatorType(str, enum.Enum):
    RAGAS = "ragas"
    DEEPEVAL = "deepeval"
    CUSTOM = "custom"


class ReplayStatus(str, enum.Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


# --- Tables -----------------------------------------------------------------
class Agent(Base):
    __tablename__ = "agents"
    __table_args__ = (
        Index("ix_agents_is_active", "is_active"),
        Index("ix_agents_created_at", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        # PostgreSQL generates ids for rows inserted outside the ORM too; the
        # Python default keeps inserts working on SQLite (tests).
        server_default=sa.text("gen_random_uuid()"),
    )
    name: Mapped[str] = mapped_column(String(120), nullable=False, unique=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    model_name: Mapped[str] = mapped_column(String(120), nullable=False)
    system_prompt: Mapped[str | None] = mapped_column(Text, nullable=True)
    tools_config: Mapped[list[dict[str, Any]]] = mapped_column(
        json_list_type(), nullable=False, default=list
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=utcnow,
        onupdate=utcnow,
        server_default=func.now(),
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=sa.true()
    )

    runs: Mapped[list["Run"]] = relationship(
        back_populates="agent", passive_deletes=True, order_by="Run.created_at"
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<Agent {self.name} ({self.model_name})>"


class Run(Base):
    __tablename__ = "runs"
    __table_args__ = (
        Index("ix_runs_agent_id_status", "agent_id", "status"),
        Index("ix_runs_status_created_at", "status", "created_at"),
        Index("ix_runs_created_at", "created_at"),
        CheckConstraint("total_tokens_used >= 0", name="total_tokens_non_negative"),
        CheckConstraint("total_cost_usd >= 0", name="total_cost_non_negative"),
        CheckConstraint("latency_ms IS NULL OR latency_ms >= 0", name="latency_non_negative"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        # PostgreSQL generates ids for rows inserted outside the ORM too; the
        # Python default keeps inserts working on SQLite (tests).
        server_default=sa.text("gen_random_uuid()"),
    )
    agent_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid(as_uuid=True),
        ForeignKey("agents.id", ondelete="RESTRICT"),
        nullable=False,
    )
    input_prompt: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[RunStatus] = mapped_column(
        enum_type(RunStatus, "run_status"),
        nullable=False,
        default=RunStatus.PENDING,
        server_default=RunStatus.PENDING.value,
    )
    output_response: Mapped[str | None] = mapped_column(Text, nullable=True)
    total_tokens_used: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=sa.text("0")
    )
    total_cost_usd: Mapped[Decimal] = mapped_column(
        Numeric(10, 6), nullable=False, default=Decimal("0"), server_default=sa.text("0")
    )
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(
        "metadata", json_dict_type(), nullable=False, default=dict
    )
    # Needed for ordering pending runs before they have started_at.
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow, server_default=func.now()
    )
    # A replay is a first-class run linked back to the run it replayed.
    replay_of_run_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.Uuid(as_uuid=True),
        ForeignKey("runs.id", ondelete="SET NULL"),
        nullable=True,
    )

    agent: Mapped[Agent] = relationship(back_populates="runs")
    traces: Mapped[list["Trace"]] = relationship(
        back_populates="run",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="Trace.timestamp",
    )
    evaluations: Mapped[list["Evaluation"]] = relationship(
        back_populates="run",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="Evaluation.metric_name",
    )
    replay_of: Mapped["Run | None"] = relationship(
        remote_side="Run.id", foreign_keys=[replay_of_run_id]
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<Run {self.id} {self.status.value}>"


class Trace(Base):
    __tablename__ = "traces"
    __table_args__ = (
        Index("ix_traces_run_id", "run_id"),
        Index("ix_traces_run_id_timestamp", "run_id", "timestamp"),
        Index("ix_traces_trace_id", "trace_id"),
        Index("ix_traces_parent_trace_id", "parent_trace_id"),
        Index("ix_traces_span_type", "span_type"),
        Index("ix_traces_status", "status"),
        UniqueConstraint("trace_id", "span_id", name="uq_traces_trace_id_span_id"),
        CheckConstraint("duration_ms >= 0", name="duration_non_negative"),
        CheckConstraint("tokens_used >= 0", name="tokens_non_negative"),
        CheckConstraint("cost_usd >= 0", name="cost_non_negative"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        # PostgreSQL generates ids for rows inserted outside the ORM too; the
        # Python default keeps inserts working on SQLite (tests).
        server_default=sa.text("gen_random_uuid()"),
    )
    run_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid(as_uuid=True),
        ForeignKey("runs.id", ondelete="CASCADE"),
        nullable=False,
    )
    parent_trace_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.Uuid(as_uuid=True),
        ForeignKey("traces.id", ondelete="CASCADE"),
        nullable=True,
    )
    span_name: Mapped[str] = mapped_column(String(200), nullable=False)
    span_type: Mapped[SpanType] = mapped_column(
        enum_type(SpanType, "span_type"), nullable=False
    )
    input_data: Mapped[dict[str, Any] | None] = mapped_column(json_dict_type(), nullable=True)
    output_data: Mapped[dict[str, Any] | None] = mapped_column(json_dict_type(), nullable=True)
    status: Mapped[SpanStatus] = mapped_column(
        enum_type(SpanStatus, "span_status"),
        nullable=False,
        default=SpanStatus.OK,
        server_default=SpanStatus.OK.value,
    )
    duration_ms: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=sa.text("0")
    )
    tokens_used: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=sa.text("0")
    )
    # Input/output split is required to price a call; tokens_used stays the total.
    tokens_input: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=sa.text("0")
    )
    tokens_output: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=sa.text("0")
    )
    cost_usd: Mapped[Decimal] = mapped_column(
        Numeric(10, 6), nullable=False, default=Decimal("0"), server_default=sa.text("0")
    )
    error_details: Mapped[dict[str, Any] | None] = mapped_column(json_dict_type(), nullable=True)
    timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow, server_default=func.now()
    )
    trace_id: Mapped[str] = mapped_column(String(32), nullable=False)
    span_id: Mapped[str] = mapped_column(String(16), nullable=False)
    model_name: Mapped[str | None] = mapped_column(String(120), nullable=True)

    run: Mapped[Run] = relationship(back_populates="traces")
    tool_calls: Mapped[list["ToolCall"]] = relationship(
        back_populates="trace",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    parent: Mapped["Trace | None"] = relationship(
        remote_side="Trace.id", foreign_keys=[parent_trace_id], back_populates="children"
    )
    children: Mapped[list["Trace"]] = relationship(
        back_populates="parent", foreign_keys=[parent_trace_id]
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<Trace {self.span_type.value} {self.span_name} {self.duration_ms}ms>"


class ToolCall(Base):
    __tablename__ = "tool_calls"
    __table_args__ = (
        Index("ix_tool_calls_trace_id", "trace_id"),
        Index("ix_tool_calls_tool_name", "tool_name"),
        CheckConstraint("duration_ms >= 0", name="duration_non_negative"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        # PostgreSQL generates ids for rows inserted outside the ORM too; the
        # Python default keeps inserts working on SQLite (tests).
        server_default=sa.text("gen_random_uuid()"),
    )
    trace_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid(as_uuid=True),
        ForeignKey("traces.id", ondelete="CASCADE"),
        nullable=False,
    )
    tool_name: Mapped[str] = mapped_column(String(120), nullable=False)
    tool_input: Mapped[dict[str, Any] | None] = mapped_column(json_dict_type(), nullable=True)
    tool_output: Mapped[dict[str, Any] | None] = mapped_column(json_dict_type(), nullable=True)
    duration_ms: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=sa.text("0")
    )
    success: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=sa.true()
    )
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    trace: Mapped[Trace] = relationship(back_populates="tool_calls")


class Evaluation(Base):
    __tablename__ = "evaluations"
    __table_args__ = (
        Index("ix_evaluations_run_id", "run_id"),
        Index("ix_evaluations_metric_name", "metric_name"),
        Index("ix_evaluations_score", "score"),
        UniqueConstraint(
            "run_id", "evaluator_type", "metric_name", name="uq_evaluations_run_metric"
        ),
        CheckConstraint("score >= 0 AND score <= 1", name="score_range"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        # PostgreSQL generates ids for rows inserted outside the ORM too; the
        # Python default keeps inserts working on SQLite (tests).
        server_default=sa.text("gen_random_uuid()"),
    )
    run_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid(as_uuid=True),
        ForeignKey("runs.id", ondelete="CASCADE"),
        nullable=False,
    )
    evaluator_type: Mapped[EvaluatorType] = mapped_column(
        enum_type(EvaluatorType, "evaluator_type"), nullable=False
    )
    metric_name: Mapped[str] = mapped_column(String(80), nullable=False)
    score: Mapped[Decimal] = mapped_column(Numeric(5, 4), nullable=False)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow, server_default=func.now()
    )

    run: Mapped[Run] = relationship(back_populates="evaluations")


class ReplaySession(Base):
    __tablename__ = "replay_sessions"
    __table_args__ = (
        Index("ix_replay_sessions_original_run_id", "original_run_id"),
        Index("ix_replay_sessions_status", "status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        # PostgreSQL generates ids for rows inserted outside the ORM too; the
        # Python default keeps inserts working on SQLite (tests).
        server_default=sa.text("gen_random_uuid()"),
    )
    original_run_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid(as_uuid=True),
        ForeignKey("runs.id", ondelete="CASCADE"),
        nullable=False,
    )
    # The replay itself is stored as a new Run; this is the forward link.
    replay_run_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.Uuid(as_uuid=True), ForeignKey("runs.id", ondelete="SET NULL"), nullable=True
    )
    status: Mapped[ReplayStatus] = mapped_column(
        enum_type(ReplayStatus, "replay_status"),
        nullable=False,
        default=ReplayStatus.PENDING,
        server_default=ReplayStatus.PENDING.value,
    )
    comparison_result: Mapped[dict[str, Any] | None] = mapped_column(
        json_dict_type(), nullable=True
    )
    # What-if overrides applied before replaying (model, tools, prompt, ...).
    overrides: Mapped[dict[str, Any]] = mapped_column(
        json_dict_type(), nullable=False, default=dict
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow, server_default=func.now()
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    original_run: Mapped[Run] = relationship(foreign_keys=[original_run_id])
    replay_run: Mapped[Run | None] = relationship(foreign_keys=[replay_run_id])
