"""Liveness and readiness endpoints."""

from __future__ import annotations

import logging
import time

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from sqlalchemy import text

from app.core.config import settings
from app.core.database import SessionLocal
from app.core.redis import get_redis
from app.schemas.common import HealthResponse

logger = logging.getLogger(__name__)

router = APIRouter(tags=["health"])
_STARTED_AT = time.monotonic()


@router.get("/health", response_model=HealthResponse, summary="Liveness probe")
def health() -> HealthResponse:
    """Cheap liveness check: does not touch the database or Redis."""
    return HealthResponse(
        status="ok",
        version=settings.app_version,
        environment=settings.environment,
        uptime_seconds=round(time.monotonic() - _STARTED_AT, 3),
    )


@router.get("/health/ready", summary="Readiness probe (database + Redis)")
def readiness() -> JSONResponse:
    components: dict[str, str] = {}
    healthy = True

    db = SessionLocal()
    try:
        db.execute(text("SELECT 1"))
        components["database"] = "ok"
    except Exception as exc:  # noqa: BLE001 - reported as a component failure
        logger.warning("Readiness: database check failed: %s", exc)
        components["database"] = "error"
        healthy = False
    finally:
        db.close()

    client = get_redis()
    if client is None:
        components["redis"] = "disabled"
    else:
        try:
            components["redis"] = "ok" if client.ping() else "error"
            healthy = healthy and components["redis"] == "ok"
        except Exception as exc:  # noqa: BLE001
            logger.warning("Readiness: Redis check failed: %s", exc)
            components["redis"] = "error"
            healthy = False

    return JSONResponse(
        status_code=200 if healthy else 503,
        content={"status": "ok" if healthy else "degraded", "components": components},
    )
