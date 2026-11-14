"""Request/response models for traces and tool calls."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import Field

from app.models import SpanStatus, SpanType
from app.schemas.common import ORMModel


class ToolCallRead(ORMModel):
    id: uuid.UUID
    trace_id: uuid.UUID
    tool_name: str
    tool_input: dict[str, Any] | None
    tool_output: dict[str, Any] | None
    duration_ms: int
    success: bool
    error_message: str | None


class TraceRead(ORMModel):
    id: uuid.UUID
    run_id: uuid.UUID
    parent_trace_id: uuid.UUID | None
    span_name: str
    span_type: SpanType
    input_data: dict[str, Any] | None
    output_data: dict[str, Any] | None
    status: SpanStatus
    duration_ms: int
    tokens_used: int
    tokens_input: int
    tokens_output: int
    cost_usd: float
    error_details: dict[str, Any] | None
    timestamp: datetime
    trace_id: str
    span_id: str
    model_name: str | None
    tool_calls: list[ToolCallRead] = Field(default_factory=list)


class TraceNode(TraceRead):
    """Recursive tree node used by the waterfall visualisation."""

    children: list["TraceNode"] = Field(default_factory=list)


TraceNode.model_rebuild()


class TraceTree(ORMModel):
    run_id: uuid.UUID
    trace_id: str | None
    roots: list[TraceNode]
    span_count: int
    total_duration_ms: int
