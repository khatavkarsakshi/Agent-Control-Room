"""Shared pytest fixtures.

The environment is configured *before* the application package is imported:
settings are read once at import time, so overriding ``DATABASE_URL`` afterwards
would have no effect. Tests therefore run against an in-memory SQLite database
with OpenTelemetry, Redis event fan-out and rate limiting disabled.
"""

from __future__ import annotations

import copy
import os
from collections.abc import Iterator
from decimal import Decimal
from typing import Any

# --- environment must be set before importing app modules --------------------
os.environ.update(
    {
        "DATABASE_URL": "sqlite+pysqlite:///:memory:",
        "ENVIRONMENT": "test",
        "LOG_JSON": "false",
        "LOG_LEVEL": "WARNING",
        "OTEL_ENABLED": "false",
        "EVENTS_ENABLED": "false",
        "RATE_LIMIT_ENABLED": "false",
        "CELERY_TASK_ALWAYS_EAGER": "true",
        "OPENAI_API_KEY": "test-key",
        "ANTHROPIC_API_KEY": "test-key",
        "CUSTOM_PRICING_JSON": "{}",
    }
)

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import select  # noqa: E402

import app.core.redis as redis_module  # noqa: E402
from app.core.database import Base, SessionLocal, engine  # noqa: E402
from app.main import create_app  # noqa: E402
from app.models import Agent, Run, RunStatus  # noqa: E402
from app.services.llm import (  # noqa: E402
    LLMProvider,
    LLMResponse,
    ResolvedProvider,
    ToolCallRequest,
)

# --- database ---------------------------------------------------------------
@pytest.fixture(scope="session", autouse=True)
def _create_schema() -> Iterator[None]:
    Base.metadata.create_all(engine)
    yield
    Base.metadata.drop_all(engine)


@pytest.fixture(autouse=True)
def _clean_database() -> Iterator[None]:
    """Delete rows after every test, children first."""
    yield
    with engine.begin() as connection:
        for table in reversed(Base.metadata.sorted_tables):
            connection.execute(table.delete())


@pytest.fixture(autouse=True)
def _disable_redis(monkeypatch: pytest.MonkeyPatch) -> None:
    """Never touch a real Redis in unit tests.

    ``get_redis`` is looked up through the module globals of every helper in
    ``app.core.redis``, so patching it here also disables cancellation checks and
    the default event publisher.
    """
    monkeypatch.setattr(redis_module, "get_redis", lambda: None)
    monkeypatch.setattr(redis_module, "get_async_redis", lambda: None)


@pytest.fixture(autouse=True)
def _stub_celery_dispatch(monkeypatch: pytest.MonkeyPatch) -> None:
    """API tests must never enqueue real Celery tasks."""
    from app.api.v1.endpoints import evaluations as evaluations_endpoint
    from app.api.v1.endpoints import replays as replays_endpoint
    from app.api.v1.endpoints import runs as runs_endpoint

    monkeypatch.setattr(
        runs_endpoint.dispatch, "dispatch_run", lambda run_id: f"task-{run_id}"
    )
    monkeypatch.setattr(runs_endpoint.dispatch, "revoke_run", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        evaluations_endpoint.dispatch,
        "dispatch_evaluation",
        lambda run_ids, *args, **kwargs: f"eval-task-{len(run_ids)}",
    )
    monkeypatch.setattr(
        replays_endpoint.dispatch,
        "dispatch_replay",
        lambda session_id: f"replay-task-{session_id}",
    )


@pytest.fixture
def db() -> Iterator[Any]:
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture
def client() -> Iterator[TestClient]:
    with TestClient(create_app()) as test_client:
        yield test_client


# --- domain factories -------------------------------------------------------
@pytest.fixture
def make_agent(db):
    def _make(**overrides: Any) -> Agent:
        data: dict[str, Any] = {
            "name": "test-agent",
            "description": "Agent used by the test suite",
            "model_name": "gpt-4o-mini",
            "system_prompt": "You are a concise assistant.",
            "tools_config": [{"name": "calculator", "config": {}}],
            "is_active": True,
        }
        data.update(overrides)
        agent = Agent(**data)
        db.add(agent)
        db.commit()
        db.refresh(agent)
        return agent

    return _make


@pytest.fixture
def make_run(db, make_agent):
    def _make(agent: Agent | None = None, **overrides: Any) -> Run:
        agent = agent or make_agent()
        data: dict[str, Any] = {
            "agent_id": agent.id,
            "input_prompt": "What is 2 + 2?",
            "status": RunStatus.PENDING,
            "metadata_json": {},
        }
        data.update(overrides)
        run = Run(**data)
        db.add(run)
        db.commit()
        db.refresh(run)
        return run

    return _make


# --- fake LLM provider ------------------------------------------------------
class FakeProvider(LLMProvider):
    """Deterministic provider that replays scripted responses."""

    name = "fake"

    def __init__(self, responses: list[LLMResponse]) -> None:
        super().__init__()
        self._responses = list(responses)
        self.requests: list[dict[str, Any]] = []

    def _complete(
        self,
        *,
        model: str,
        messages: list[dict[str, Any]],
        tools: list[Any] | None,
        temperature: float,
        max_tokens: int,
    ) -> LLMResponse:
        # Snapshot the conversation: the runner keeps mutating its ``messages``
        # list after each call, so storing a reference would change history.
        self.requests.append(
            {
                "model": model,
                "messages": copy.deepcopy(messages),
                "tools": copy.deepcopy(tools),
                "temperature": temperature,
                "max_tokens": max_tokens,
            }
        )
        if not self._responses:
            raise AssertionError("FakeProvider ran out of scripted responses")
        return self._responses.pop(0)


@pytest.fixture
def fake_provider_factory():
    def _factory(responses: list[LLMResponse], model: str = "gpt-4o-mini"):
        provider = FakeProvider(responses)

        def _resolve(_model: str) -> ResolvedProvider:
            return ResolvedProvider(provider=provider, model=model)

        return provider, _resolve

    return _factory


@pytest.fixture
def tool_call_response():
    def _build(
        name: str = "calculator",
        arguments: dict[str, Any] | None = None,
        *,
        tokens_input: int = 100,
        tokens_output: int = 20,
        call_id: str = "call_1",
    ) -> LLMResponse:
        return LLMResponse(
            content=None,
            tool_calls=[
                ToolCallRequest(
                    id=call_id,
                    name=name,
                    arguments=arguments or {"expression": "2 + 2"},
                )
            ],
            tokens_input=tokens_input,
            tokens_output=tokens_output,
            finish_reason="tool_calls",
            model="gpt-4o-mini",
        )

    return _build


@pytest.fixture
def final_response():
    def _build(
        content: str = "The answer is 4.",
        *,
        tokens_input: int = 150,
        tokens_output: int = 10,
    ) -> LLMResponse:
        return LLMResponse(
            content=content,
            tool_calls=[],
            tokens_input=tokens_input,
            tokens_output=tokens_output,
            finish_reason="stop",
            model="gpt-4o-mini",
        )

    return _build


@pytest.fixture
def reload_run(db):
    def _reload(run_id: Any) -> Run:
        db.expire_all()
        run = db.scalar(select(Run).where(Run.id == run_id))
        assert run is not None
        return run

    return _reload


@pytest.fixture
def money():
    """Assert helper for Decimal costs."""

    def _assert(value: Any, expected: str) -> None:
        assert Decimal(str(value)) == Decimal(expected)

    return _assert
