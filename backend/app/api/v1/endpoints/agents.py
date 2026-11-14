"""Agent CRUD endpoints."""

from __future__ import annotations

import logging
from uuid import UUID

from fastapi import APIRouter, Query, Response, status
from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError

from app.agents.tools import tool_registry
from app.api.deps import AgentDep, DbSession, PaginationDep
from app.core.exceptions import BadRequestError, ConflictError, NotFoundError
from app.core.sanitization import escape_like
from app.models import Agent, Run, RunStatus
from app.schemas.agent import (
    AgentCloneRequest,
    AgentCreate,
    AgentRead,
    AgentUpdate,
)
from app.schemas.common import Page
from app.schemas.run import RunRead

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/agents", tags=["agents"])


def _validate_tools(tools_config: list[dict] | None) -> None:
    unknown = tool_registry.validate_names(tools_config)
    if unknown:
        raise BadRequestError(
            f"Unknown tool(s): {', '.join(unknown)}",
            details={"unknown_tools": unknown, "available_tools": tool_registry.names()},
        )


def _name_taken(db: DbSession, name: str, *, exclude_id: UUID | None = None) -> bool:
    stmt = select(Agent.id).where(func.lower(Agent.name) == name.lower())
    if exclude_id is not None:
        stmt = stmt.where(Agent.id != exclude_id)
    return db.scalar(stmt) is not None


@router.get("", response_model=Page[AgentRead], summary="List agents")
def list_agents(
    db: DbSession,
    page: PaginationDep,
    is_active: bool | None = Query(default=None, description="Filter by active flag"),
    search: str | None = Query(default=None, max_length=120, description="Name/description search"),
) -> Page[AgentRead]:
    conditions = []
    if is_active is not None:
        conditions.append(Agent.is_active == is_active)
    if search:
        pattern = f"%{escape_like(search.strip())}%"
        conditions.append(
            or_(Agent.name.ilike(pattern, escape="\\"), Agent.description.ilike(pattern, escape="\\"))
        )

    base = select(Agent)
    if conditions:
        base = base.where(*conditions)

    total = db.scalar(select(func.count()).select_from(base.subquery())) or 0
    agents = db.scalars(
        base.order_by(Agent.created_at.desc(), Agent.id).limit(page.limit).offset(page.offset)
    ).all()

    return Page[AgentRead](
        items=[AgentRead.model_validate(agent) for agent in agents],
        total=int(total),
        limit=page.limit,
        offset=page.offset,
    )


@router.post("", response_model=AgentRead, status_code=status.HTTP_201_CREATED, summary="Create an agent")
def create_agent(payload: AgentCreate, db: DbSession) -> AgentRead:
    _validate_tools(payload.tools_config)
    if _name_taken(db, payload.name):
        raise ConflictError(f"An agent named '{payload.name}' already exists")

    agent = Agent(**payload.model_dump())
    db.add(agent)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise ConflictError(f"An agent named '{payload.name}' already exists") from exc
    db.refresh(agent)
    logger.info("Created agent %s (%s)", agent.id, agent.name)
    return AgentRead.model_validate(agent)


@router.get("/{agent_id}", response_model=AgentRead, summary="Get an agent")
def get_agent(agent: AgentDep) -> AgentRead:
    return AgentRead.model_validate(agent)


@router.patch("/{agent_id}", response_model=AgentRead, summary="Update an agent")
def update_agent(payload: AgentUpdate, agent: AgentDep, db: DbSession) -> AgentRead:
    data = payload.model_dump(exclude_unset=True)

    # Nullable text fields accept an explicit null to clear them; everything
    # else must stay non-null.
    for field in ("name", "model_name", "tools_config", "is_active"):
        if field in data and data[field] is None:
            if field == "tools_config":
                data[field] = []
            else:
                raise BadRequestError(f"'{field}' cannot be null")

    if "tools_config" in data:
        _validate_tools(data["tools_config"])

    if "name" in data and _name_taken(db, data["name"], exclude_id=agent.id):
        raise ConflictError(f"An agent named '{data['name']}' already exists")

    for field, value in data.items():
        setattr(agent, field, value)

    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise ConflictError("Agent update conflicts with existing data") from exc
    db.refresh(agent)
    logger.info("Updated agent %s", agent.id)
    return AgentRead.model_validate(agent)


@router.delete(
    "/{agent_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Deactivate (or hard delete) an agent",
)
def delete_agent(
    agent: AgentDep,
    db: DbSession,
    hard: bool = Query(default=False, description="Permanently delete an agent with no runs"),
) -> Response:
    if hard:
        run_count = db.scalar(select(func.count(Run.id)).where(Run.agent_id == agent.id)) or 0
        if run_count:
            raise ConflictError(
                "Cannot delete an agent that has runs; deactivate it instead",
                details={"run_count": int(run_count)},
            )
        db.delete(agent)
        db.commit()
        logger.info("Hard deleted agent %s", agent.id)
    else:
        agent.is_active = False
        db.commit()
        logger.info("Deactivated agent %s", agent.id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/{agent_id}/clone",
    response_model=AgentRead,
    status_code=status.HTTP_201_CREATED,
    summary="Clone an agent configuration",
)
def clone_agent(
    payload: AgentCloneRequest, agent: AgentDep, db: DbSession
) -> AgentRead:
    if payload.name:
        candidate = payload.name
        if _name_taken(db, candidate):
            raise ConflictError(f"An agent named '{candidate}' already exists")
    else:
        base = f"{agent.name} (copy)"
        candidate = base
        suffix = 2
        while _name_taken(db, candidate):
            candidate = f"{base} {suffix}"
            suffix += 1

    clone = Agent(
        name=candidate,
        description=agent.description,
        model_name=agent.model_name,
        system_prompt=agent.system_prompt,
        tools_config=[dict(item) for item in agent.tools_config],
        is_active=True,
    )
    db.add(clone)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise ConflictError(f"An agent named '{candidate}' already exists") from exc
    db.refresh(clone)
    logger.info("Cloned agent %s -> %s", agent.id, clone.id)
    return AgentRead.model_validate(clone)


@router.get(
    "/{agent_id}/runs",
    response_model=Page[RunRead],
    summary="List runs for an agent",
)
def list_agent_runs(
    agent: AgentDep,
    db: DbSession,
    page: PaginationDep,
    run_status: RunStatus | None = Query(default=None, alias="status"),
) -> Page[RunRead]:
    base = select(Run).where(Run.agent_id == agent.id)
    if run_status is not None:
        base = base.where(Run.status == run_status)

    total = db.scalar(select(func.count()).select_from(base.subquery())) or 0
    runs = db.scalars(
        base.order_by(Run.created_at.desc(), Run.id).limit(page.limit).offset(page.offset)
    ).all()
    return Page[RunRead](
        items=[RunRead.model_validate(run) for run in runs],
        total=int(total),
        limit=page.limit,
        offset=page.offset,
    )
