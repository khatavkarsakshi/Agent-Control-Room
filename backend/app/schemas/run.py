"""Request/response models for runs."""

from __future__ import annotations

import json
import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.core.config import settings
from app.core.sanitization import sanitize_text
from app.models import RunStatus
from app.schemas.common import ORMModel
from app.schemas.evaluation import EvaluationRead
from app.schemas.trace import TraceRead

MAX_METADATA_BYTES = 16_384


class ExecutionOptions(BaseModel):
    """Per-run overrides. Stored in ``runs.metadata.execution``."""

    model_config = ConfigDict(extra="forbid")

    model_name: str | None = Field(default=None, max_length=120)
    temperature: float = Field(default_factory=lambda: settings.agent_temperature, ge=0.0, le=2.0)
    max_iterations: int = Field(
        default_factory=lambda: settings.agent_max_iterations, ge=1, le=50
    )
    max_tokens: int = Field(default_factory=lambda: settings.agent_max_tokens, ge=1, le=32_000)
    timeout_seconds: int = Field(
        default_factory=lambda: settings.agent_default_timeout_seconds, ge=5, le=3_600
    )


class RunCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    agent_id: uuid.UUID
    input_prompt: str = Field(min_length=1, max_length=settings.run_max_input_chars)
    metadata: dict[str, Any] = Field(default_factory=dict)
    execution_options: ExecutionOptions | None = None

    @field_validator("input_prompt")
    @classmethod
    def _clean_prompt(cls, value: str) -> str:
        cleaned = sanitize_text(value, max_length=settings.run_max_input_chars)
        if not cleaned:
            raise ValueError("input_prompt must not be blank")
        return cleaned

    @field_validator("metadata")
    @classmethod
    def _bounded_metadata(cls, value: dict[str, Any]) -> dict[str, Any]:
        if len(json.dumps(value, default=str)) > MAX_METADATA_BYTES:
            raise ValueError(f"metadata must be smaller than {MAX_METADATA_BYTES} bytes")
        return value


class RunRead(ORMModel):
    id: uuid.UUID
    agent_id: uuid.UUID
    input_prompt: str
    status: RunStatus
    output_response: str | None
    total_tokens_used: int
    total_cost_usd: float
    latency_ms: int | None
    started_at: datetime | None
    completed_at: datetime | None
    error_message: str | None
    metadata: dict[str, Any] = Field(validation_alias="metadata_json")
    created_at: datetime
    replay_of_run_id: uuid.UUID | None

    @property
    def trace_id(self) -> str | None:
        value = (self.metadata or {}).get("trace_id")
        return value if isinstance(value, str) else None


class RunDetail(RunRead):
    traces: list[TraceRead] = Field(default_factory=list)
    evaluations: list[EvaluationRead] = Field(default_factory=list)
