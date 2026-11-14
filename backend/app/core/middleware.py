"""ASGI middleware: request correlation and Redis-backed rate limiting.

Pure ASGI middleware is used instead of Starlette's ``BaseHTTPMiddleware`` so
Server-Sent Events stream without buffering and disconnects propagate cleanly.
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from typing import Any

from redis.exceptions import RedisError
from starlette.datastructures import Headers, MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.core.config import settings
from app.core.logging import request_id_var
from app.core.redis import get_async_redis

logger = logging.getLogger(__name__)

REQUEST_ID_HEADER = "X-Request-ID"


def _client_ip(scope: Scope) -> str:
    headers = Headers(scope=scope)
    if settings.trust_proxy_headers:
        forwarded = headers.get("x-forwarded-for")
        if forwarded:
            return forwarded.split(",")[0].strip()
        real_ip = headers.get("x-real-ip")
        if real_ip:
            return real_ip.strip()
    client = scope.get("client")
    return client[0] if client else "unknown"


class RequestContextMiddleware:
    """Assign a request id, expose it to logs, and emit one access log line."""

    def __init__(self, app: ASGIApp, header_name: str = REQUEST_ID_HEADER) -> None:
        self.app = app
        self.header_name = header_name

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return

        headers = Headers(scope=scope)
        request_id = headers.get(self.header_name.lower()) or uuid.uuid4().hex
        scope.setdefault("state", {})
        scope["state"]["request_id"] = request_id
        token = request_id_var.set(request_id)
        started = time.perf_counter()
        status_code: int | None = None

        async def send_with_header(message: Message) -> None:
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = message["status"]
                if scope["type"] == "http":
                    response_headers = MutableHeaders(scope=message)
                    if self.header_name not in response_headers:
                        response_headers.append(self.header_name, request_id)
            await send(message)

        try:
            await self.app(scope, receive, send_with_header)
        finally:
            if scope["type"] == "http":
                duration_ms = (time.perf_counter() - started) * 1000
                log = logger.info if (status_code or 500) < 500 else logger.error
                log(
                    "%s %s -> %s (%.1f ms)",
                    scope.get("method", "-"),
                    scope.get("path", "-"),
                    status_code or "closed",
                    duration_ms,
                )
            request_id_var.reset(token)


class RateLimitMiddleware:
    """Fixed-window rate limiter backed by Redis.

    The limiter is deliberately fail-open: if Redis is unreachable the request is
    allowed and a warning is logged. Availability of the control plane is more
    important than perfect limiting during a Redis outage.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app
        self._warning_at = 0.0

    def _is_exempt(self, path: str) -> bool:
        if not settings.rate_limit_enabled:
            return True
        return any(path == exempt or path.startswith(exempt + "/") for exempt in settings.rate_limit_exempt_list)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or self._is_exempt(scope.get("path", "")):
            await self.app(scope, receive, send)
            return

        client_ip = _client_ip(scope)
        window = settings.rate_limit_window_seconds
        bucket = int(time.time() // window)
        key = f"acr:ratelimit:{client_ip}:{bucket}"

        count: int | None = None
        retry_after = window
        try:
            client = get_async_redis()
            pipe = client.pipeline()
            pipe.incr(key, 1)
            pipe.expire(key, window + 1, nx=True)
            pipe.ttl(key)
            count, _, retry_after = await pipe.execute()
        except (RedisError, OSError) as exc:
            now = time.monotonic()
            if now - self._warning_at > 60:
                logger.warning("Rate limiter bypassed (Redis unavailable): %s", exc)
                self._warning_at = now

        if count is not None and int(count) > settings.rate_limit_requests:
            body = json.dumps(
                {
                    "error": {
                        "code": "rate_limited",
                        "message": "Too many requests",
                        "details": {
                            "limit": settings.rate_limit_requests,
                            "window_seconds": window,
                        },
                        "request_id": scope.get("state", {}).get("request_id"),
                    }
                }
            ).encode()
            headers = [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(body)).encode()),
                (b"retry-after", str(max(int(retry_after or window), 1)).encode()),
                (b"x-ratelimit-limit", str(settings.rate_limit_requests).encode()),
                (b"x-ratelimit-remaining", b"0"),
            ]
            await send({"type": "http.response.start", "status": 429, "headers": headers})
            await send({"type": "http.response.body", "body": body, "more_body": False})
            return

        await self.app(scope, receive, send)


def request_id_from_scope(scope: Scope) -> str | None:
    """Small helper used by exception handlers and tests."""
    state: dict[str, Any] = scope.get("state") or {}
    return state.get("request_id")
