"""SQLAlchemy engine, session factory and FastAPI dependency.

The control plane uses synchronous SQLAlchemy sessions. FastAPI executes ``def``
endpoints in a threadpool, so a sync session never blocks the event loop. Celery
workers are synchronous by nature, which lets both processes share one session
factory and one set of models.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import MetaData, create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.config import settings

logger = logging.getLogger(__name__)

# Deterministic constraint names keep Alembic autogenerate diffs stable.
NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    """Declarative base shared by every ORM model."""

    metadata = MetaData(naming_convention=NAMING_CONVENTION)


def _engine_kwargs() -> dict:
    """Dialect-aware engine options (SQLite is used by the test suite)."""
    if settings.is_sqlite:
        kwargs: dict = {"connect_args": {"check_same_thread": False}}
        if ":memory:" in settings.database_url or settings.database_url.endswith("sqlite://"):
            # One shared connection keeps an in-memory database alive across sessions.
            kwargs["poolclass"] = StaticPool
        return kwargs
    return {
        "pool_pre_ping": True,
        "pool_size": settings.database_pool_size,
        "max_overflow": settings.database_max_overflow,
        "pool_recycle": settings.database_pool_recycle_seconds,
        "pool_timeout": 30,
        "echo": settings.database_echo,
        "future": True,
    }


engine: Engine = create_engine(settings.database_url, **_engine_kwargs())

if settings.is_sqlite:
    # SQLite does not enforce foreign keys unless asked; tests rely on cascades.
    from sqlalchemy import event

    @event.listens_for(engine, "connect")
    def _enable_sqlite_foreign_keys(dbapi_connection, _connection_record):  # type: ignore[no-untyped-def]
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

SessionLocal = sessionmaker(
    bind=engine,
    autoflush=False,
    autocommit=False,
    expire_on_commit=False,
    class_=Session,
)


def get_db() -> Iterator[Session]:
    """FastAPI dependency that yields a scoped session and always closes it."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@contextmanager
def session_scope() -> Iterator[Session]:
    """Transactional scope for background jobs and scripts."""
    db = SessionLocal()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def dispose_engine() -> None:
    """Dispose pooled connections during shutdown.

    In-memory SQLite is intentionally skipped: disposing would drop the test
    database between fixtures.
    """
    if settings.is_sqlite:
        return
    engine.dispose()
