"""Shared FastAPI dependencies and pagination helpers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated
from uuid import UUID

from fastapi import Depends, Query
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.exceptions import NotFoundError
from app.models import Agent, Run

DbSession = Annotated[Session, Depends(get_db)]


@dataclass(frozen=True)
class Pagination:
    limit: int
    offset: int


def pagination(
    limit: Annotated[int, Query(ge=1, le=200, description="Page size")] = 50,
    offset: Annotated[int, Query(ge=0, description="Rows to skip")] = 0,
) -> Pagination:
    return Pagination(limit=limit, offset=offset)


PaginationDep = Annotated[Pagination, Depends(pagination)]


def get_agent_or_404(agent_id: UUID, db: DbSession) -> Agent:
    agent = db.get(Agent, agent_id)
    if agent is None:
        raise NotFoundError(f"Agent {agent_id} not found")
    return agent


AgentDep = Annotated[Agent, Depends(get_agent_or_404)]


def get_run_or_404(run_id: UUID, db: DbSession) -> Run:
    run = db.get(Run, run_id)
    if run is None:
        raise NotFoundError(f"Run {run_id} not found")
    return run


RunDep = Annotated[Run, Depends(get_run_or_404)]
