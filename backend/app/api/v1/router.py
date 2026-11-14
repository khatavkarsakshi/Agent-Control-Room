"""API v1 router aggregation."""

from fastapi import APIRouter

from app.api.v1.endpoints import (
    agents,
    evaluations,
    replays,
    runs,
    streaming,
    tools,
    traces,
)

api_router = APIRouter()
api_router.include_router(agents.router)
api_router.include_router(tools.router)
api_router.include_router(runs.router)
api_router.include_router(traces.router)
api_router.include_router(streaming.router)
api_router.include_router(evaluations.router)
api_router.include_router(replays.router)

__all__ = ["api_router"]
