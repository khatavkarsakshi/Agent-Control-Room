"""Request/response models for replay sessions.

A replay is stored twice: as a ``ReplaySession`` (the comparison record) and as a
normal ``Run`` linked through ``runs.replay_of_run_id``. The new run gives replay
executions the same tracing, streaming and accounting behaviour as any other run.
"""

from __future__ import annotations

import re
import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models import ReplayStatus, RunStatus
from app.schemas.common import ORMModel

_TOOL_NAME_RE = re.compile(r"^[A-Za-z0-9_.\-]{1,64}$")


class ReplayOverrides(BaseModel):
    """What-if changes applied to the replayed run only.

    ``System prompt`` and ``tools`` cannot live on a Run row; they are stored in
    ``runs.metadata.replay.overrides`` and applied by the agent runner.
    """

    model_config = ConfigDict(extra="forbid")

    input_prompt: str | None = Field(default=None, max_length=100_000)
    model_name: str | None = Field(default=None, max_length=120)
    system_prompt: str | None = Field(default=None, max_length=100_000)
    tools: list[str] | None = Field(default=None, max_length=32)
    temperature: float | None = Field(default=None, ge=0.0, le=2.0)
    max_iterations: int | None = Field(default=None, ge=1, le=50)
    max_tokens: int | None = Field(default=None, ge=1, le=32_000)
    timeout_seconds: int | None = Field(default=None, ge=5, le=3_600)

    @field_validator("input_prompt", "system_prompt")
    @classmethod
    def _clean_prompt(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.strip()
        return cleaned or None

    @field_validator("tools")
    @classmethod
    def _clean_tools(cls, value: list[str] | None) -> list[str] | None:
        if value is None:
            return None
        seen: set[str] = set()
        cleaned: list[str] = []
        for item in value:
            name = item.strip()
            if not _TOOL_NAME_RE.match(name):
                raise ValueError(f"invalid tool name {name!r}")
            if name not in seen:
                seen.add(name)
                cleaned.append(name)
        return cleaned

    def execution_overrides(self) -> dict[str, Any]:
        """Return only the keys the runner stores under ``metadata.execution``."""
        return {
            key: value
            for key in (
                "model_name",
                "temperature",
                "max_iterations",
                "max_tokens",
                "timeout_seconds",
            )
            if (value := getattr(self, key)) is not None
        }

    def agent_overrides(self) -> dict[str, Any]:
        """Return the keys the runner applies on top of the agent configuration."""
        return {
            key: value
            for key in ("system_prompt", "tools")
            if (value := getattr(self, key)) is not None
        }


class ReplayCreate(BaseModel):
    """Body for ``POST /runs/{run_id}/replay``."""

    model_config = ConfigDict(extra="forbid")

    # live: call the provider again. offline: replay recorded LLM responses.
    mode: Literal["live", "offline"] = "live"
    overrides: ReplayOverrides = Field(default_factory=ReplayOverrides)
    # Run the custom evaluator over the replay once it finishes.
    evaluate: bool = False
    metrics: list[str] | None = Field(default=None, max_length=10)

    @field_validator("metrics")
    @classmethod
    def _clean_metrics(cls, value: list[str] | None) -> list[str] | None:
        if value is None:
            return None
        cleaned = [name.strip() for name in value if name and name.strip()]
        if not cleaned:
            raise ValueError("metrics must contain at least one name when provided")
        return cleaned


class ReplayRunSummary(ORMModel):
    """Compact run representation embedded in replay responses."""

    id: uuid.UUID
    agent_id: uuid.UUID
    status: RunStatus
    input_prompt: str
    output_response: str | None
    total_tokens_used: int
    total_cost_usd: float
    latency_ms: int | None
    error_message: str | None
    created_at: datetime
    completed_at: datetime | None
    replay_of_run_id: uuid.UUID | None


class ReplaySessionRead(ORMModel):
    id: uuid.UUID
    original_run_id: uuid.UUID
    replay_run_id: uuid.UUID | None
    status: ReplayStatus
    overrides: dict[str, Any]
    comparison_result: dict[str, Any] | None
    created_at: datetime
    completed_at: datetime | None
    original_run: ReplayRunSummary | None = None
    replay_run: ReplayRunSummary | None = None


class ReplaySessionList(BaseModel):
    items: list[ReplaySessionRead] = Field(default_factory=list)
    total: int = Field(ge=0)
    limit: int = Field(ge=1)
    offset: int = Field(ge=0)


class ReplayCreateResponse(BaseModel):
    session: ReplaySessionRead
    task_id: str | None = None
