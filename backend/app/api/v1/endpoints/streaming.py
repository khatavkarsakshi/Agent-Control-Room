"""Live run updates over Server-Sent Events and WebSocket.

The worker publishes JSON events to a Redis channel; these endpoints replay a
database snapshot on connect and then forward events until the run reaches a
terminal state. SSE is the primary transport (browser ``EventSource``), with a
WebSocket alternative for clients that want bidirectional control later.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import StreamingResponse
from redis.exceptions import RedisError
from sqlalchemy import select
from sqlalchemy.orm import selectinload
from starlette.concurrency import run_in_threadpool

from app.core.config import settings
from app.core.database import SessionLocal
from app.core.exceptions import NotFoundError
from app.core.redis import get_async_redis, run_channel
from app.models import Run, RunStatus, Trace
from app.schemas.run import RunRead
from app.schemas.trace import TraceRead

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/runs", tags=["runs"])

TERMINAL_STATUSES = {RunStatus.COMPLETED.value, RunStatus.FAILED.value, RunStatus.REPLAYED.value}
TERMINAL_EVENTS = {"run.completed", "run.failed"}


def _load_snapshot(run_id: UUID) -> dict[str, Any] | None:
    """Load a run and its spans in a fresh session (called in a threadpool)."""
    with SessionLocal() as db:
        run = db.scalar(
            select(Run)
            .options(selectinload(Run.traces).selectinload(Trace.tool_calls))
            .where(Run.id == run_id)
        )
        if run is None:
            return None
        return {
            "run": RunRead.model_validate(run).model_dump(mode="json"),
            "traces": [
                TraceRead.model_validate(trace).model_dump(mode="json") for trace in run.traces
            ],
        }


def _sse(event_type: str, data: Any) -> str:
    return f"event: {event_type}\ndata: {json.dumps(data, default=str)}\n\n"


async def _close_pubsub(pubsub: Any, channel: str) -> None:
    try:
        await pubsub.unsubscribe(channel)
    except Exception:  # noqa: BLE001 - cleanup best effort
        pass
    closer = getattr(pubsub, "aclose", None) or getattr(pubsub, "close", None)
    if closer is not None:
        try:
            result = closer()
            if asyncio.iscoroutine(result):
                await result
        except Exception:  # noqa: BLE001
            pass


async def _event_generator(
    request: Request, run_id: UUID, snapshot: dict[str, Any]
) -> AsyncIterator[str]:
    yield _sse("run.snapshot", snapshot)

    if snapshot["run"]["status"] in TERMINAL_STATUSES:
        return
    if not settings.events_enabled:
        yield _sse("stream.unavailable", {"message": "Live events are disabled"})
        return

    channel = run_channel(run_id)
    pubsub = None
    try:
        client = get_async_redis()
        pubsub = client.pubsub()
        await pubsub.subscribe(channel)

        while True:
            if await request.is_disconnected():
                break
            try:
                message = await pubsub.get_message(
                    ignore_subscribe_messages=True, timeout=15.0
                )
            except (RedisError, OSError) as exc:
                logger.warning("SSE subscription failed for run %s: %s", run_id, exc)
                yield _sse("stream.error", {"message": "Live event stream unavailable"})
                break

            if message is None:
                yield ": keepalive\n\n"
                continue

            raw = message.get("data")
            try:
                payload = json.loads(raw) if isinstance(raw, str) else raw
            except json.JSONDecodeError:
                continue
            if not isinstance(payload, dict):
                continue

            event_type = str(payload.get("type", "message"))
            yield _sse(event_type, payload)
            if event_type in TERMINAL_EVENTS:
                break
    except (RedisError, OSError) as exc:
        logger.warning("Could not subscribe to run %s events: %s", run_id, exc)
        yield _sse("stream.error", {"message": "Live event stream unavailable"})
    finally:
        if pubsub is not None:
            await _close_pubsub(pubsub, channel)


@router.get(
    "/{run_id}/stream",
    summary="Stream live run events (Server-Sent Events)",
    response_class=StreamingResponse,
)
async def stream_run(request: Request, run_id: UUID) -> StreamingResponse:
    snapshot = await run_in_threadpool(_load_snapshot, run_id)
    if snapshot is None:
        raise NotFoundError(f"Run {run_id} not found")

    return StreamingResponse(
        _event_generator(request, run_id, snapshot),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.websocket("/{run_id}/ws")
async def stream_run_ws(websocket: WebSocket, run_id: UUID) -> None:
    await websocket.accept()

    snapshot = await run_in_threadpool(_load_snapshot, run_id)
    if snapshot is None:
        await websocket.close(code=4404, reason="Run not found")
        return

    await websocket.send_json({"type": "run.snapshot", "data": snapshot})
    if snapshot["run"]["status"] in TERMINAL_STATUSES or not settings.events_enabled:
        await websocket.close(code=1000)
        return

    channel = run_channel(run_id)
    pubsub = None
    try:
        client = get_async_redis()
        pubsub = client.pubsub()
        await pubsub.subscribe(channel)

        while True:
            try:
                message = await pubsub.get_message(
                    ignore_subscribe_messages=True, timeout=15.0
                )
            except (RedisError, OSError):
                await websocket.send_json(
                    {"type": "stream.error", "data": {"message": "Live event stream unavailable"}}
                )
                break

            if message is None:
                await websocket.send_json({"type": "keepalive", "data": {}})
                continue

            raw = message.get("data")
            try:
                payload = json.loads(raw) if isinstance(raw, str) else raw
            except json.JSONDecodeError:
                continue
            if not isinstance(payload, dict):
                continue

            await websocket.send_json(payload)
            if payload.get("type") in TERMINAL_EVENTS:
                break
    except WebSocketDisconnect:
        logger.debug("WebSocket client disconnected from run %s", run_id)
    except (RedisError, OSError) as exc:
        logger.warning("WebSocket subscription failed for run %s: %s", run_id, exc)
    finally:
        if pubsub is not None:
            await _close_pubsub(pubsub, channel)
        try:
            await websocket.close(code=1000)
        except Exception:  # noqa: BLE001 - already closed
            pass
