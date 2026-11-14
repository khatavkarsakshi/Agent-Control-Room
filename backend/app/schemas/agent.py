"""Request/response models for agents."""

from __future__ import annotations

import re
import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.core.sanitization import sanitize_optional_text, sanitize_text
from app.schemas.common import ORMModel

_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 ._\-()/]{0,119}$")
_TOOL_NAME_RE = re.compile(r"^[A-Za-z0-9_.\-]{1,64}$")


class ToolConfig(BaseModel):
    """A tool enabled for an agent.

    Stored as JSON so the set of tools can evolve without a migration. ``config``
    is passed through to the tool implementation (for example an HTTP allowlist).
    """

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=64)
    config: dict[str, Any] = Field(default_factory=dict)

    @field_validator("name")
    @classmethod
    def _validate_name(cls, value: str) -> str:
        value = sanitize_text(value, max_length=64, allow_newlines=False)
        if not _TOOL_NAME_RE.match(value):
            raise ValueError("tool name may only contain letters, digits, '.', '_' and '-'")
        return value


def _normalize_tools(value: Any) -> list[dict[str, Any]]:
    """Accept ``["calculator"]`` or ``[{"name": "calculator"}]``."""
    if value is None:
        return []
    if not isinstance(value, (list, tuple)):
        raise ValueError("tools_config must be a list")

    normalized: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in value:
        if isinstance(item, str):
            item = {"name": item}
        elif isinstance(item, ToolConfig):
            item = item.model_dump()
        elif not isinstance(item, dict):
            raise ValueError("each tool entry must be a string or an object")

        tool = ToolConfig.model_validate(item)
        if tool.name in seen:
            raise ValueError(f"duplicate tool '{tool.name}'")
        seen.add(tool.name)
        normalized.append(tool.model_dump())
    return normalized


class AgentCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=5_000)
    model_name: str = Field(min_length=1, max_length=120)
    system_prompt: str | None = Field(default=None, max_length=100_000)
    tools_config: list[dict[str, Any]] = Field(default_factory=list)
    is_active: bool = True

    @field_validator("name")
    @classmethod
    def _validate_name(cls, value: str) -> str:
        value = sanitize_text(value, max_length=120, allow_newlines=False)
        if not _NAME_RE.match(value):
            raise ValueError(
                "name must start with a letter or digit and contain only letters, digits, "
                "spaces, '.', '_', '-', '()' or '/'"
            )
        return value

    @field_validator("description")
    @classmethod
    def _clean_description(cls, value: str | None) -> str | None:
        return sanitize_optional_text(value, max_length=5_000)

    @field_validator("system_prompt")
    @classmethod
    def _clean_system_prompt(cls, value: str | None) -> str | None:
        return sanitize_optional_text(value, max_length=100_000)

    @field_validator("model_name")
    @classmethod
    def _clean_model(cls, value: str) -> str:
        return sanitize_text(value, max_length=120, allow_newlines=False)

    @field_validator("tools_config", mode="before")
    @classmethod
    def _tools(cls, value: Any) -> list[dict[str, Any]]:
        return _normalize_tools(value)


class AgentUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=5_000)
    model_name: str | None = Field(default=None, min_length=1, max_length=120)
    system_prompt: str | None = Field(default=None, max_length=100_000)
    tools_config: list[dict[str, Any]] | None = None
    is_active: bool | None = None

    @field_validator("name")
    @classmethod
    def _validate_name(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = sanitize_text(value, max_length=120, allow_newlines=False)
        if not _NAME_RE.match(value):
            raise ValueError("invalid agent name")
        return value

    @field_validator("description")
    @classmethod
    def _clean_description(cls, value: str | None) -> str | None:
        return sanitize_optional_text(value, max_length=5_000)

    @field_validator("system_prompt")
    @classmethod
    def _clean_system_prompt(cls, value: str | None) -> str | None:
        return sanitize_optional_text(value, max_length=100_000)

    @field_validator("model_name")
    @classmethod
    def _clean_model(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return sanitize_text(value, max_length=120, allow_newlines=False)

    @field_validator("tools_config", mode="before")
    @classmethod
    def _tools(cls, value: Any) -> list[dict[str, Any]] | None:
        if value is None:
            return None
        return _normalize_tools(value)


class AgentCloneRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=120)

    @field_validator("name")
    @classmethod
    def _validate_name(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = sanitize_text(value, max_length=120, allow_newlines=False)
        if not _NAME_RE.match(value):
            raise ValueError("invalid agent name")
        return value


class AgentRead(ORMModel):
    id: uuid.UUID
    name: str
    description: str | None
    model_name: str
    system_prompt: str | None
    tools_config: list[dict[str, Any]]
    created_at: datetime
    updated_at: datetime
    is_active: bool
