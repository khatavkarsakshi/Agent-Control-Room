"""Read-only endpoint for the tools an agent can be configured with."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel

from app.agents.tools import tool_registry

router = APIRouter(prefix="/tools", tags=["tools"])


class ToolDescription(BaseModel):
    name: str
    description: str
    parameters: dict[str, Any]


@router.get("", response_model=list[ToolDescription], summary="List available tools")
def list_tools() -> list[ToolDescription]:
    return [
        ToolDescription(
            name=spec.name,
            description=spec.description,
            parameters=spec.parameters,
        )
        for spec in tool_registry.list_specs()
    ]
