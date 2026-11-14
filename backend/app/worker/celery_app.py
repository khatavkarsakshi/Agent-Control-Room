"""Celery application factory and schedule.

The worker uses the synchronous SQLAlchemy session factory and the same models
as the API. OpenTelemetry is configured inside the child process (``worker_process_init``)
so forked workers do not inherit an exporter tied to the parent event loop.
"""

from __future__ import annotations

import logging

from celery import Celery
from celery.schedules import crontab
from celery.signals import worker_process_init, worker_shutdown

from app.core.config import settings

logger = logging.getLogger(__name__)

celery_app = Celery(
    "agent_control_room",
    broker=settings.broker_url,
    backend=settings.result_backend,
    include=["app.worker.tasks"],
)

celery_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="UTC",
    enable_utc=True,
    task_track_started=True,
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    worker_prefetch_multiplier=1,
    task_time_limit=settings.celery_task_time_limit_seconds,
    task_soft_time_limit=settings.celery_task_soft_time_limit_seconds,
    result_expires=settings.celery_result_expires_seconds,
    task_always_eager=settings.celery_task_always_eager,
    task_eager_propagates=settings.environment == "test",
    broker_connection_retry_on_startup=True,
    task_default_queue="agent-control-room",
    beat_schedule={
        "cleanup-old-traces": {
            "task": "maintenance.cleanup_old_traces",
            "schedule": crontab(hour=3, minute=0),
        },
        "calculate-daily-costs": {
            "task": "maintenance.calculate_daily_costs",
            "schedule": crontab(hour=0, minute=15),
        },
    },
)


@worker_process_init.connect
def _init_worker(**_: object) -> None:
    """Configure telemetry and SQLAlchemy instrumentation in each worker child."""
    from opentelemetry.instrumentation.celery import CeleryInstrumentor
    from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor

    from app.core.database import engine
    from app.core.logging import configure_logging
    from app.core.telemetry import setup_telemetry

    configure_logging()
    setup_telemetry("worker")
    CeleryInstrumentor().instrument()
    if not settings.is_sqlite:
        SQLAlchemyInstrumentor().instrument(engine=engine)
    logger.info("Celery worker initialised (queue=%s)", celery_app.conf.task_default_queue)


@worker_shutdown.connect
def _shutdown_worker(**_: object) -> None:
    from app.core.telemetry import force_flush

    force_flush()


# Celery's ``-A module`` resolution looks for an attribute named ``app`` (or
# ``celery``) on the imported module. The canonical instance is ``celery_app``;
# this alias makes ``celery -A app.worker.celery_app`` work as documented.
app = celery_app
