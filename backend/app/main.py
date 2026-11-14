"""FastAPI application factory.

Routers are versioned under ``/api/v1``. Health probes stay unversioned because
orchestrators expect stable well-known paths.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

from app.api.v1.endpoints.health import router as health_router
from app.api.v1.router import api_router
from app.core.config import settings
from app.core.database import dispose_engine
from app.core.exceptions import register_exception_handlers
from app.core.logging import configure_logging
from app.core.middleware import RateLimitMiddleware, RequestContextMiddleware
from app.core.redis import close_redis
from app.core.telemetry import setup_telemetry

logger = logging.getLogger(__name__)

DESCRIPTION = """
Observability and control plane for AI agents.

* register agents (model, system prompt, tools)
* execute runs asynchronously through Celery
* inspect every LLM call, tool call and data access as a trace
* track tokens and cost per step, per run and per agent
* stream live run events over SSE or WebSocket

All resource endpoints are versioned under `/api/v1`.
"""


def create_app() -> FastAPI:
    configure_logging()
    setup_telemetry("api")

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        logger.info(
            "Starting %s v%s (%s)", settings.app_name, settings.app_version, settings.environment
        )
        if settings.otel_enabled:
            try:
                # Ensure task headers carry W3C trace context from API to worker.
                from opentelemetry.instrumentation.celery import CeleryInstrumentor

                CeleryInstrumentor().instrument()
            except Exception:  # pragma: no cover - instrumentation is optional
                logger.exception("Celery instrumentation could not be enabled")
        yield
        logger.info("Shutting down %s", settings.app_name)
        close_redis()
        dispose_engine()

    app = FastAPI(
        title=settings.app_name,
        version=settings.app_version,
        description=DESCRIPTION,
        debug=settings.debug,
        lifespan=lifespan,
        docs_url="/docs",
        redoc_url="/redoc",
        openapi_url="/openapi.json",
        openapi_tags=[
            {"name": "agents", "description": "Agent configuration CRUD"},
            {"name": "runs", "description": "Run creation, execution and live events"},
            {"name": "traces", "description": "Span-level execution records"},
            {"name": "tools", "description": "Tools available to agents"},
            {"name": "evaluations", "description": "Quality metrics for finished runs"},
            {"name": "replays", "description": "What-if re-execution and comparisons"},
            {"name": "health", "description": "Liveness and readiness"},
        ],
    )

    register_exception_handlers(app)

    # Middleware is applied bottom-up: rate limit -> request context -> CORS.
    app.add_middleware(RateLimitMiddleware)
    app.add_middleware(RequestContextMiddleware)

    origins = settings.cors_origins_list
    allow_credentials = settings.cors_allow_credentials
    if "*" in origins and allow_credentials:
        logger.warning("CORS: wildcard origin with credentials is invalid; disabling credentials")
        allow_credentials = False

    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_origin_regex=None,
        allow_credentials=allow_credentials,
        allow_methods=["GET", "POST", "PATCH", "PUT", "DELETE", "OPTIONS"],
        allow_headers=["*"],
        expose_headers=["X-Request-ID", "Content-Disposition"],
        max_age=600,
    )

    app.include_router(health_router)
    app.include_router(api_router, prefix=settings.api_v1_prefix)

    @app.get("/", include_in_schema=False)
    def root() -> dict[str, str]:
        return {
            "name": settings.app_name,
            "version": settings.app_version,
            "docs": "/docs",
            "api": settings.api_v1_prefix,
            "health": "/health",
        }

    if settings.otel_enabled:
        FastAPIInstrumentor.instrument_app(
            app, excluded_urls="health,health/ready,docs,openapi.json"
        )

    return app


app = create_app()
