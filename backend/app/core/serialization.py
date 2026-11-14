"""JSON-safe conversion helpers.

SQLAlchemy JSONB columns and Redis event payloads must only contain JSON
primitives. Agent code frequently produces ``Decimal``, ``datetime``, ``UUID``
and Enum values, so every write goes through :func:`to_jsonable`.
"""

from __future__ import annotations

import json
from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from typing import Any
from uuid import UUID

from pydantic import BaseModel


def _convert(value: Any, *, max_string: int | None) -> Any:
    if value is None or isinstance(value, (bool, int, str)):
        if isinstance(value, str) and max_string is not None:
            return value[:max_string]
        return value
    if isinstance(value, float):
        # NaN/Infinity are not valid JSON.
        if value != value or value in (float("inf"), float("-inf")):
            return None
        return value
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, Enum):
        return _convert(value.value, max_string=max_string)
    if isinstance(value, BaseModel):
        return _convert(value.model_dump(mode="python"), max_string=max_string)
    if isinstance(value, dict):
        return {
            str(key): _convert(item, max_string=max_string)
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple, set)):
        return [_convert(item, max_string=max_string) for item in value]
    if isinstance(value, bytes):
        return f"<{len(value)} bytes>"
    return _convert(str(value), max_string=max_string)


def to_jsonable(value: Any, *, max_string: int | None = None) -> Any:
    """Recursively convert ``value`` into JSON-serialisable primitives."""
    return _convert(value, max_string=max_string)


def json_dumps(value: Any) -> str:
    """Strict JSON serialisation for Redis payloads."""
    return json.dumps(to_jsonable(value), ensure_ascii=False, separators=(",", ":"))


def truncate_json(value: Any, *, max_string: int = 20_000, max_items: int = 200) -> Any:
    """Trim oversized payloads before persisting them.

    Agent prompts and tool outputs can be megabytes long. Persisting them
    verbatim inflates the trace store and slows the dashboard, so raw payloads
    are bounded while keeping a useful prefix for debugging.
    """
    converted = to_jsonable(value, max_string=max_string)
    if isinstance(converted, list):
        return converted[:max_items]
    if isinstance(converted, dict):
        return dict(list(converted.items())[:max_items])
    return converted
