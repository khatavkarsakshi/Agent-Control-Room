"""Initial schema: agents, runs, traces, tool_calls, evaluations, replay_sessions.

Revision ID: 0001_initial
Revises:
Create Date: 2026-11-14
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001_initial"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# create_type=False: the types are created explicitly in upgrade() so that the
# migration is idempotent and the downgrade can drop them at the right time.
run_status = postgresql.ENUM(
    "pending", "running", "completed", "failed", "replayed",
    name="run_status", create_type=False,
)
span_type = postgresql.ENUM(
    "llm_call", "tool_call", "data_access", "decision", "error",
    name="span_type", create_type=False,
)
span_status = postgresql.ENUM("ok", "error", "timeout", name="span_status", create_type=False)
evaluator_type = postgresql.ENUM(
    "ragas", "deepeval", "custom", name="evaluator_type", create_type=False
)
replay_status = postgresql.ENUM(
    "pending", "running", "completed", "failed", name="replay_status", create_type=False
)

_ALL_ENUMS = (run_status, span_type, span_status, evaluator_type, replay_status)


def upgrade() -> None:
    bind = op.get_bind()
    for enum in _ALL_ENUMS:
        enum.create(bind, checkfirst=True)

    op.create_table(
        "agents",
        sa.Column(
            "id",
            sa.Uuid(as_uuid=True),
            nullable=False,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("model_name", sa.String(length=120), nullable=False),
        sa.Column("system_prompt", sa.Text(), nullable=True),
        sa.Column("tools_config", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.PrimaryKeyConstraint("id", name="pk_agents"),
        sa.UniqueConstraint("name", name="uq_agents_name"),
    )
    op.create_index("ix_agents_is_active", "agents", ["is_active"], unique=False)
    op.create_index("ix_agents_created_at", "agents", ["created_at"], unique=False)

    op.create_table(
        "runs",
        sa.Column(
            "id",
            sa.Uuid(as_uuid=True),
            nullable=False,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("agent_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("input_prompt", sa.Text(), nullable=False),
        sa.Column("status", run_status, nullable=False, server_default="pending"),
        sa.Column("output_response", sa.Text(), nullable=True),
        sa.Column("total_tokens_used", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("total_cost_usd", sa.Numeric(precision=10, scale=6), nullable=False, server_default=sa.text("0")),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("metadata", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("replay_of_run_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.CheckConstraint("total_tokens_used >= 0", name="ck_runs_total_tokens_non_negative"),
        sa.CheckConstraint("total_cost_usd >= 0", name="ck_runs_total_cost_non_negative"),
        sa.CheckConstraint("latency_ms IS NULL OR latency_ms >= 0", name="ck_runs_latency_non_negative"),
        sa.ForeignKeyConstraint(["agent_id"], ["agents.id"], name="fk_runs_agent_id_agents", ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["replay_of_run_id"], ["runs.id"], name="fk_runs_replay_of_run_id_runs", ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_runs"),
    )
    op.create_index("ix_runs_agent_id_status", "runs", ["agent_id", "status"], unique=False)
    op.create_index("ix_runs_status_created_at", "runs", ["status", "created_at"], unique=False)
    op.create_index("ix_runs_created_at", "runs", ["created_at"], unique=False)

    op.create_table(
        "traces",
        sa.Column(
            "id",
            sa.Uuid(as_uuid=True),
            nullable=False,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("run_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("parent_trace_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("span_name", sa.String(length=200), nullable=False),
        sa.Column("span_type", span_type, nullable=False),
        sa.Column("input_data", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("output_data", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("status", span_status, nullable=False, server_default="ok"),
        sa.Column("duration_ms", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("tokens_used", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("tokens_input", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("tokens_output", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("cost_usd", sa.Numeric(precision=10, scale=6), nullable=False, server_default=sa.text("0")),
        sa.Column("error_details", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("timestamp", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("trace_id", sa.String(length=32), nullable=False),
        sa.Column("span_id", sa.String(length=16), nullable=False),
        sa.Column("model_name", sa.String(length=120), nullable=True),
        sa.CheckConstraint("duration_ms >= 0", name="ck_traces_duration_non_negative"),
        sa.CheckConstraint("tokens_used >= 0", name="ck_traces_tokens_non_negative"),
        sa.CheckConstraint("cost_usd >= 0", name="ck_traces_cost_non_negative"),
        sa.ForeignKeyConstraint(["run_id"], ["runs.id"], name="fk_traces_run_id_runs", ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["parent_trace_id"], ["traces.id"], name="fk_traces_parent_trace_id_traces", ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_traces"),
        sa.UniqueConstraint("trace_id", "span_id", name="uq_traces_trace_id_span_id"),
    )
    op.create_index("ix_traces_run_id", "traces", ["run_id"], unique=False)
    op.create_index("ix_traces_run_id_timestamp", "traces", ["run_id", "timestamp"], unique=False)
    op.create_index("ix_traces_trace_id", "traces", ["trace_id"], unique=False)
    op.create_index("ix_traces_parent_trace_id", "traces", ["parent_trace_id"], unique=False)
    op.create_index("ix_traces_span_type", "traces", ["span_type"], unique=False)
    op.create_index("ix_traces_status", "traces", ["status"], unique=False)

    op.create_table(
        "tool_calls",
        sa.Column(
            "id",
            sa.Uuid(as_uuid=True),
            nullable=False,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("trace_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("tool_name", sa.String(length=120), nullable=False),
        sa.Column("tool_input", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("tool_output", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("success", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.CheckConstraint("duration_ms >= 0", name="ck_tool_calls_duration_non_negative"),
        sa.ForeignKeyConstraint(["trace_id"], ["traces.id"], name="fk_tool_calls_trace_id_traces", ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name="pk_tool_calls"),
    )
    op.create_index("ix_tool_calls_trace_id", "tool_calls", ["trace_id"], unique=False)
    op.create_index("ix_tool_calls_tool_name", "tool_calls", ["tool_name"], unique=False)

    op.create_table(
        "evaluations",
        sa.Column(
            "id",
            sa.Uuid(as_uuid=True),
            nullable=False,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("run_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("evaluator_type", evaluator_type, nullable=False),
        sa.Column("metric_name", sa.String(length=80), nullable=False),
        sa.Column("score", sa.Numeric(precision=5, scale=4), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.CheckConstraint("score >= 0 AND score <= 1", name="ck_evaluations_score_range"),
        sa.ForeignKeyConstraint(["run_id"], ["runs.id"], name="fk_evaluations_run_id_runs", ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name="pk_evaluations"),
        sa.UniqueConstraint("run_id", "evaluator_type", "metric_name", name="uq_evaluations_run_metric"),
    )
    op.create_index("ix_evaluations_run_id", "evaluations", ["run_id"], unique=False)
    op.create_index("ix_evaluations_metric_name", "evaluations", ["metric_name"], unique=False)
    op.create_index("ix_evaluations_score", "evaluations", ["score"], unique=False)

    op.create_table(
        "replay_sessions",
        sa.Column(
            "id",
            sa.Uuid(as_uuid=True),
            nullable=False,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("original_run_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("replay_run_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("status", replay_status, nullable=False, server_default="pending"),
        sa.Column("comparison_result", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("overrides", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["original_run_id"], ["runs.id"], name="fk_replay_sessions_original_run_id_runs", ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["replay_run_id"], ["runs.id"], name="fk_replay_sessions_replay_run_id_runs", ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_replay_sessions"),
    )
    op.create_index(
        "ix_replay_sessions_original_run_id", "replay_sessions", ["original_run_id"], unique=False
    )
    op.create_index("ix_replay_sessions_status", "replay_sessions", ["status"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_replay_sessions_status", table_name="replay_sessions")
    op.drop_index("ix_replay_sessions_original_run_id", table_name="replay_sessions")
    op.drop_table("replay_sessions")

    op.drop_index("ix_evaluations_score", table_name="evaluations")
    op.drop_index("ix_evaluations_metric_name", table_name="evaluations")
    op.drop_index("ix_evaluations_run_id", table_name="evaluations")
    op.drop_table("evaluations")

    op.drop_index("ix_tool_calls_tool_name", table_name="tool_calls")
    op.drop_index("ix_tool_calls_trace_id", table_name="tool_calls")
    op.drop_table("tool_calls")

    op.drop_index("ix_traces_status", table_name="traces")
    op.drop_index("ix_traces_span_type", table_name="traces")
    op.drop_index("ix_traces_parent_trace_id", table_name="traces")
    op.drop_index("ix_traces_trace_id", table_name="traces")
    op.drop_index("ix_traces_run_id_timestamp", table_name="traces")
    op.drop_index("ix_traces_run_id", table_name="traces")
    op.drop_table("traces")

    op.drop_index("ix_runs_created_at", table_name="runs")
    op.drop_index("ix_runs_status_created_at", table_name="runs")
    op.drop_index("ix_runs_agent_id_status", table_name="runs")
    op.drop_table("runs")

    op.drop_index("ix_agents_created_at", table_name="agents")
    op.drop_index("ix_agents_is_active", table_name="agents")
    op.drop_table("agents")

    bind = op.get_bind()
    for enum in reversed(_ALL_ENUMS):
        enum.drop(bind, checkfirst=True)
