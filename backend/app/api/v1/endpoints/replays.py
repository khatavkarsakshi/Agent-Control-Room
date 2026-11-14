"""Replay session endpoints.

* ``POST /runs/{run_id}/replay``          start a live or offline replay
* ``GET  /replay-sessions``               list sessions with filters
* ``GET  /replay-sessions/{id}``          session + original/replay run summary
* ``POST /replay-sessions/{id}/cancel``   cancel an in-flight replay
"""

from __future__ import annotations

import logging
from uuid import UUID

from fastapi import APIRouter, Query, status
from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

from app.api.deps import DbSession, PaginationDep, RunDep
from app.core.exceptions import NotFoundError, ServiceUnavailableError
from app.core.redis import get_publisher
from app.models import ReplaySession, ReplayStatus, RunStatus, utcnow
from app.schemas.replay import (
    ReplayCreate,
    ReplayCreateResponse,
    ReplaySessionList,
    ReplaySessionRead,
)
from app.services.replay import create_replay, cancel_replay, session_read
from app.worker import dispatch

logger = logging.getLogger(__name__)

router = APIRouter(tags=["replays"])


def _session_query():
    return select(ReplaySession).options(
        selectinload(ReplaySession.original_run),
        selectinload(ReplaySession.replay_run),
    )


def _get_session(db: DbSession, session_id: UUID) -> ReplaySession:
    session = db.scalar(_session_query().where(ReplaySession.id == session_id))
    if session is None:
        raise NotFoundError(f"Replay session {session_id} not found")
    return session


@router.post(
    "/runs/{run_id}/replay",
    response_model=ReplayCreateResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Replay a finished run with optional overrides",
)
def start_replay(
    run: RunDep, payload: ReplayCreate, db: DbSession
) -> ReplayCreateResponse:
    session, replay_run = create_replay(db, run, payload)

    try:
        task_id = dispatch.dispatch_replay(session.id)
    except Exception as exc:  # noqa: BLE001 - broker failure becomes a failed replay
        logger.exception("Could not enqueue replay session %s", session.id)
        replay_run.status = RunStatus.FAILED
        replay_run.error_message = "Failed to enqueue replay"
        replay_run.completed_at = utcnow()
        session.status = ReplayStatus.FAILED
        session.comparison_result = {"error": "failed to enqueue replay"}
        session.completed_at = utcnow()
        db.commit()
        raise ServiceUnavailableError(
            "Could not enqueue the replay. Is the Celery broker available?"
        ) from exc

    replay_run.metadata_json["task_id"] = task_id
    db.commit()
    db.refresh(session)
    return ReplayCreateResponse(session=session_read(session), task_id=task_id)


@router.get(
    "/replay-sessions",
    response_model=ReplaySessionList,
    summary="List replay sessions",
)
def list_replay_sessions(
    db: DbSession,
    page: PaginationDep,
    original_run_id: UUID | None = Query(default=None),
    replay_status: list[ReplayStatus] | None = Query(default=None, alias="status"),
) -> ReplaySessionList:
    conditions = []
    if original_run_id is not None:
        conditions.append(ReplaySession.original_run_id == original_run_id)
    if replay_status:
        conditions.append(ReplaySession.status.in_(replay_status))

    base = select(ReplaySession)
    if conditions:
        base = base.where(*conditions)

    total = db.scalar(select(func.count()).select_from(base.subquery())) or 0
    sessions = db.scalars(
        base.options(
            selectinload(ReplaySession.original_run),
            selectinload(ReplaySession.replay_run),
        )
        .order_by(ReplaySession.created_at.desc(), ReplaySession.id)
        .limit(page.limit)
        .offset(page.offset)
    ).all()

    return ReplaySessionList(
        items=[session_read(session) for session in sessions],
        total=int(total),
        limit=page.limit,
        offset=page.offset,
    )


@router.get(
    "/replay-sessions/{session_id}",
    response_model=ReplaySessionRead,
    summary="Get a replay session with its comparison",
)
def get_replay_session(session_id: UUID, db: DbSession) -> ReplaySessionRead:
    return session_read(_get_session(db, session_id))


@router.post(
    "/replay-sessions/{session_id}/cancel",
    response_model=ReplaySessionRead,
    summary="Cancel a pending or running replay",
)
def cancel_replay_session(session_id: UUID, db: DbSession) -> ReplaySessionRead:
    session = _get_session(db, session_id)
    cancel_replay(db, session)

    payload = {
        "session_id": str(session.id),
        "original_run_id": str(session.original_run_id),
        "replay_run_id": str(session.replay_run_id) if session.replay_run_id else None,
        "status": session.status.value,
        "mode": session.overrides.get("mode") if isinstance(session.overrides, dict) else None,
        "cancelled": True,
    }
    publisher = get_publisher()
    publisher.publish(session.original_run_id, "replay.failed", payload)
    if session.replay_run_id:
        publisher.publish(session.replay_run_id, "replay.failed", payload)

    db.refresh(session)
    return session_read(session)
