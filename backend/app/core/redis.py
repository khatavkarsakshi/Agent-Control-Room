"""Redis clients, run-event publishing and cancellation flags.

Redis has three jobs in this project:

* Celery broker / result backend,
* fan-out of live run events to SSE and WebSocket clients,
* cooperative cancellation flags for running agents.

Every helper degrades gracefully when Redis is unavailable: losing live
telemetry must never take down agent execution.
"""

from __future__ import annotations

import json
import logging
import threading
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

import redis
import redis.asyncio as redis_async
from redis.exceptions import RedisError

from app.core.config import settings
from app.core.serialization import to_jsonable

logger = logging.getLogger(__name__)

_sync_client: redis.Redis | None = None
_async_client: redis_async.Redis | None = None
_client_lock = threading.Lock()
_warned = False


def _warn_once(message: str, *args: Any) -> None:
    global _warned
    if not _warned:
        logger.warning(message, *args)
        _warned = True


def get_redis() -> redis.Redis | None:
    """Return a lazily created synchronous client, or ``None`` if disabled."""
    global _sync_client
    if _sync_client is None:
        with _client_lock:
            if _sync_client is None:
                _sync_client = redis.Redis.from_url(
                    settings.redis_url,
                    decode_responses=True,
                    socket_connect_timeout=settings.redis_connect_timeout_seconds,
                    socket_timeout=settings.redis_socket_timeout_seconds,
                )
    return _sync_client


def get_async_redis() -> redis_async.Redis:
    """Return a lazily created asyncio client for SSE/WebSocket endpoints."""
    global _async_client
    if _async_client is None:
        _async_client = redis_async.Redis.from_url(
            settings.redis_url,
            decode_responses=True,
            socket_connect_timeout=settings.redis_connect_timeout_seconds,
            socket_timeout=None,
            health_check_interval=30,
        )
    return _async_client


def close_redis() -> None:
    """Close both clients. Async close is scheduled by the caller when possible."""
    global _sync_client, _async_client
    if _sync_client is not None:
        try:
            _sync_client.close()
        except RedisError:  # pragma: no cover - shutdown best effort
            pass
        _sync_client = None
    _async_client = None


# --- Channels ---------------------------------------------------------------
def run_channel(run_id: UUID | str) -> str:
    return f"acr:runs:{run_id}"


def cancel_key(run_id: UUID | str) -> str:
    return f"acr:run:cancel:{run_id}"


class NullPublisher:
    """No-op publisher used in tests or when event publishing is disabled."""

    def publish(self, run_id: UUID | str, event_type: str, data: dict | None = None) -> None:
        return None


class EventPublisher:
    """Publish structured run events to a Redis pub/sub channel.

    Events are also appended to a capped list so a late subscriber can replay the
    most recent activity if needed. Publishing is best-effort: a dead Redis never
    raises into the agent execution path.
    """

    def __init__(self, client: redis.Redis | None = None) -> None:
        self._client = client

    @property
    def client(self) -> redis.Redis | None:
        return self._client or get_redis()

    def publish(self, run_id: UUID | str, event_type: str, data: dict | None = None) -> None:
        client = self.client
        if client is None:
            return
        event = {
            "type": event_type,
            "run_id": str(run_id),
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "data": to_jsonable(data or {}),
        }
        try:
            serialized = json.dumps(event)
            pipeline = client.pipeline()
            pipeline.publish(run_channel(run_id), serialized)
            pipeline.rpush(f"acr:runs:{run_id}:events", serialized)
            pipeline.ltrim(f"acr:runs:{run_id}:events", -200, -1)
            pipeline.expire(f"acr:runs:{run_id}:events", 3600)
            pipeline.execute()
        except RedisError as exc:
            _warn_once("Redis publish failed (events disabled): %s", exc)


def get_publisher() -> EventPublisher | NullPublisher:
    if not settings.events_enabled:
        return NullPublisher()
    return EventPublisher()


# --- Cancellation -----------------------------------------------------------
def request_cancel(run_id: UUID | str, ttl_seconds: int = 3600) -> bool:
    """Set a cooperative cancellation flag. Returns ``True`` when stored."""
    client = get_redis()
    if client is None:
        return False
    try:
        client.set(cancel_key(run_id), "1", ex=ttl_seconds)
        return True
    except RedisError as exc:
        logger.warning("Could not store cancellation flag for %s: %s", run_id, exc)
        return False


def clear_cancel(run_id: UUID | str) -> None:
    client = get_redis()
    if client is None:
        return
    try:
        client.delete(cancel_key(run_id))
    except RedisError:  # pragma: no cover - best effort
        pass


def is_cancelled(run_id: UUID | str) -> bool:
    """Check the cancellation flag; Redis failures mean "not cancelled"."""
    client = get_redis()
    if client is None:
        return False
    try:
        return client.exists(cancel_key(run_id)) == 1
    except RedisError as exc:
        _warn_once("Redis cancellation check failed: %s", exc)
        return False
