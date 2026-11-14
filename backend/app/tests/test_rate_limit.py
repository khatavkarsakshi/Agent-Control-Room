"""Rate limiter behaviour (fixed window, Redis-backed, fail-open)."""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.core import middleware as middleware_module
from app.core.config import settings


class _FakePipeline:
    def __init__(self, state: dict[str, int]) -> None:
        self._state = state

    def incr(self, *_args: Any, **_kwargs: Any) -> "_FakePipeline":
        self._state["count"] += 1
        return self

    def expire(self, *_args: Any, **_kwargs: Any) -> "_FakePipeline":
        return self

    def ttl(self, *_args: Any, **_kwargs: Any) -> "_FakePipeline":
        return self

    async def execute(self) -> list[Any]:
        return [self._state["count"], True, 60]


class _FakeRedis:
    def __init__(self) -> None:
        self.state = {"count": 0}

    def pipeline(self) -> _FakePipeline:
        return _FakePipeline(self.state)


def _enable(monkeypatch: pytest.MonkeyPatch, client: Any, limit: int = 2) -> None:
    monkeypatch.setattr(settings, "rate_limit_enabled", True)
    monkeypatch.setattr(settings, "rate_limit_requests", limit)
    monkeypatch.setattr(settings, "rate_limit_window_seconds", 60)
    monkeypatch.setattr(middleware_module, "get_async_redis", lambda: client)


def test_requests_within_limit_are_allowed(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _enable(monkeypatch, _FakeRedis(), limit=2)

    assert client.get("/api/v1/tools").status_code == 200
    assert client.get("/api/v1/tools").status_code == 200


def test_requests_over_limit_return_429(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _enable(monkeypatch, _FakeRedis(), limit=2)

    assert client.get("/api/v1/tools").status_code == 200
    assert client.get("/api/v1/tools").status_code == 200
    blocked = client.get("/api/v1/tools")

    assert blocked.status_code == 429
    assert blocked.json()["error"]["code"] == "rate_limited"
    assert blocked.headers["retry-after"]
    assert blocked.headers["x-ratelimit-remaining"] == "0"


def test_limiter_fails_open_when_redis_is_down(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    class _BrokenRedis:
        def pipeline(self) -> None:
            raise OSError("redis unreachable")

    _enable(monkeypatch, _BrokenRedis())

    assert client.get("/api/v1/tools").status_code == 200
