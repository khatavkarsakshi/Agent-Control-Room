"""Built-in tools available to agents.

Tools are registered by name; an agent's ``tools_config`` selects a subset.
Every tool receives validated JSON arguments, returns JSON-safe output and
reports which external data sources it touched so the runner can record
``data_access`` spans.

Tool failures are raised as :class:`ToolError` and reported back to the model as
a tool result, allowing the agent to recover instead of failing the whole run.
"""

from __future__ import annotations

import ast
import ipaddress
import json
import logging
import math
import operator
import socket
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlparse
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)


class ToolError(Exception):
    """A tool could not complete the request."""


class ToolConfigurationError(Exception):
    """The agent references a tool that does not exist or has invalid config."""


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    parameters: dict[str, Any]


@dataclass
class ToolContext:
    run_id: UUID
    agent_id: UUID
    config: dict[str, Any] = field(default_factory=dict)


@dataclass
class ToolResult:
    output: Any
    data_touched: list[dict[str, Any]] = field(default_factory=list)


class AgentTool(ABC):
    spec: ToolSpec

    @abstractmethod
    def run(self, arguments: dict[str, Any], context: ToolContext) -> ToolResult:
        raise NotImplementedError

    def _require_string(self, arguments: dict[str, Any], key: str, *, max_length: int = 2_000) -> str:
        value = arguments.get(key)
        if not isinstance(value, str) or not value.strip():
            raise ToolError(f"argument '{key}' is required and must be a non-empty string")
        if len(value) > max_length:
            raise ToolError(f"argument '{key}' exceeds {max_length} characters")
        return value


# --- calculator -------------------------------------------------------------
_BINARY_OPERATORS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
}

_UNARY_OPERATORS = {ast.UAdd: operator.pos, ast.USub: operator.neg}

_MAX_RESULT = 1e15


class CalculatorTool(AgentTool):
    spec = ToolSpec(
        name="calculator",
        description="Evaluate a basic arithmetic expression (+ - * / // % ** and parentheses).",
        parameters={
            "type": "object",
            "properties": {
                "expression": {
                    "type": "string",
                    "description": "Arithmetic expression, for example '(120 * 3) + 8'.",
                }
            },
            "required": ["expression"],
            "additionalProperties": False,
        },
    )

    def run(self, arguments: dict[str, Any], context: ToolContext) -> ToolResult:
        expression = self._require_string(arguments, "expression", max_length=500)
        try:
            parsed = ast.parse(expression, mode="eval")
            result = self._eval(parsed.body, depth=0)
        except ToolError:
            raise
        except (SyntaxError, ValueError, ZeroDivisionError, OverflowError) as exc:
            raise ToolError(f"invalid expression: {exc}") from exc

        if isinstance(result, float) and (math.isnan(result) or math.isinf(result)):
            raise ToolError("expression result is not a finite number")
        if abs(result) > _MAX_RESULT:
            raise ToolError("expression result is too large")

        return ToolResult(output={"expression": expression, "result": result})

    def _eval(self, node: ast.AST, *, depth: int) -> float:
        if depth > 40:
            raise ToolError("expression is nested too deeply")
        if isinstance(node, ast.Constant):
            if isinstance(node.value, bool) or not isinstance(node.value, (int, float)):
                raise ToolError("only numeric literals are allowed")
            return node.value
        if isinstance(node, ast.UnaryOp) and type(node.op) in _UNARY_OPERATORS:
            return _UNARY_OPERATORS[type(node.op)](self._eval(node.operand, depth=depth + 1))
        if isinstance(node, ast.BinOp) and type(node.op) in _BINARY_OPERATORS:
            left = self._eval(node.left, depth=depth + 1)
            right = self._eval(node.right, depth=depth + 1)
            if isinstance(node.op, ast.Pow) and (abs(right) > 100 or abs(left) > 1e9):
                raise ToolError("exponent is too large")
            return _BINARY_OPERATORS[type(node.op)](left, right)
        raise ToolError("expression contains unsupported syntax")


# --- current time -----------------------------------------------------------
class CurrentTimeTool(AgentTool):
    spec = ToolSpec(
        name="current_time",
        description="Return the current UTC time and the time in an optional IANA timezone.",
        parameters={
            "type": "object",
            "properties": {
                "timezone": {
                    "type": "string",
                    "description": "IANA timezone name, for example 'Europe/Berlin'. Defaults to UTC.",
                }
            },
            "additionalProperties": False,
        },
    )

    def run(self, arguments: dict[str, Any], context: ToolContext) -> ToolResult:
        timezone_name = arguments.get("timezone") or "UTC"
        if not isinstance(timezone_name, str):
            raise ToolError("argument 'timezone' must be a string")
        try:
            zone = ZoneInfo(timezone_name)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ToolError(f"unknown timezone '{timezone_name}'") from exc

        now = datetime.now(timezone.utc)
        return ToolResult(
            output={
                "utc": now.isoformat(),
                "timezone": timezone_name,
                "local": now.astimezone(zone).isoformat(),
                "unix": int(now.timestamp()),
            }
        )


# --- HTTP GET ---------------------------------------------------------------
_TEXT_CONTENT_PREFIXES = (
    "text/",
    "application/json",
    "application/xml",
    "application/xhtml+xml",
    "application/javascript",
    "application/x-ndjson",
)


def _validate_public_url(url: str, *, allow_private: bool) -> None:
    """Reject non-HTTP schemes, credentials and private network targets.

    DNS is resolved here and again by the HTTP client, so a determined attacker
    could still win a DNS-rebinding race. Redirects are therefore never followed
    and only textual responses are returned, which keeps the utility useful while
    materially limiting SSRF impact.
    """
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise ToolError("only http and https URLs are supported")
    if not parsed.hostname:
        raise ToolError("URL must contain a hostname")
    if parsed.username or parsed.password:
        raise ToolError("URLs with embedded credentials are not allowed")

    if allow_private:
        return

    try:
        addresses = socket.getaddrinfo(parsed.hostname, parsed.port or (443 if parsed.scheme == "https" else 80))
    except socket.gaierror as exc:
        raise ToolError(f"could not resolve hostname: {exc}") from exc

    for family, _, _, _, sockaddr in addresses:
        if family not in (socket.AF_INET, socket.AF_INET6):
            continue
        ip = ipaddress.ip_address(sockaddr[0])
        if not ip.is_global:
            raise ToolError(
                "requests to private, loopback or link-local addresses are blocked"
            )


class HttpGetTool(AgentTool):
    spec = ToolSpec(
        name="http_get",
        description=(
            "Fetch a public HTTP(S) URL and return its textual content. "
            "Redirects are not followed and binary responses are rejected."
        ),
        parameters={
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "Absolute http(s) URL."},
                "headers": {
                    "type": "object",
                    "description": "Optional request headers (string values only).",
                    "additionalProperties": {"type": "string"},
                },
                "max_bytes": {
                    "type": "integer",
                    "minimum": 256,
                    "maximum": settings.http_tool_max_response_bytes,
                    "description": "Maximum number of response bytes to read.",
                },
            },
            "required": ["url"],
            "additionalProperties": False,
        },
    )

    def run(self, arguments: dict[str, Any], context: ToolContext) -> ToolResult:
        url = self._require_string(arguments, "url", max_length=2_000)
        allow_private = bool(
            context.config.get("allow_private_networks", settings.http_tool_allow_private_networks)
        )
        _validate_public_url(url, allow_private=allow_private)

        raw_headers = arguments.get("headers") or {}
        if not isinstance(raw_headers, dict):
            raise ToolError("argument 'headers' must be an object")
        headers: dict[str, str] = {}
        for key, value in raw_headers.items():
            if not isinstance(key, str) or not isinstance(value, str):
                raise ToolError("header names and values must be strings")
            if key.lower() in ("host", "content-length", "transfer-encoding"):
                continue
            if len(key) > 100 or len(value) > 1_000:
                raise ToolError("header is too long")
            headers[key] = value

        max_bytes = int(arguments.get("max_bytes") or settings.http_tool_max_response_bytes)
        max_bytes = max(256, min(max_bytes, settings.http_tool_max_response_bytes))

        try:
            with httpx.Client(
                timeout=settings.tool_timeout_seconds,
                follow_redirects=False,
                trust_env=False,
                headers={"User-Agent": "agent-control-room/0.2 (+tool:http_get)"},
            ) as client:
                with client.stream("GET", url, headers=headers) as response:
                    if 300 <= response.status_code < 400:
                        raise ToolError(
                            f"redirects are not followed (status {response.status_code})"
                        )
                    content_type = response.headers.get("content-type", "").lower()
                    if content_type and not content_type.startswith(_TEXT_CONTENT_PREFIXES):
                        return ToolResult(
                            output={
                                "url": str(response.url),
                                "status_code": response.status_code,
                                "content_type": content_type,
                                "text": None,
                                "note": "binary or unsupported content type; body not returned",
                            },
                            data_touched=[
                                {"type": "http", "url": str(response.url), "status": response.status_code}
                            ],
                        )

                    chunks: list[bytes] = []
                    total = 0
                    for chunk in response.iter_bytes():
                        chunks.append(chunk)
                        total += len(chunk)
                        if total >= max_bytes:
                            break
                    body = b"".join(chunks)[:max_bytes]
                    encoding = response.encoding or "utf-8"
                    text = body.decode(encoding, errors="replace")
                    truncated = total >= max_bytes

                    return ToolResult(
                        output={
                            "url": str(response.url),
                            "status_code": response.status_code,
                            "content_type": content_type,
                            "text": text,
                            "truncated": truncated,
                        },
                        data_touched=[
                            {"type": "http", "url": str(response.url), "status": response.status_code}
                        ],
                    )
        except httpx.HTTPError as exc:
            raise ToolError(f"HTTP request failed: {exc}") from exc


# --- registry ---------------------------------------------------------------
@dataclass
class ResolvedTool:
    tool: AgentTool
    config: dict[str, Any] = field(default_factory=dict)


class ToolRegistry:
    def __init__(self, tools: list[AgentTool] | None = None) -> None:
        self._tools: dict[str, AgentTool] = {}
        for tool in tools or []:
            self.register(tool)

    def register(self, tool: AgentTool) -> None:
        if tool.spec.name in self._tools:
            raise ValueError(f"tool '{tool.spec.name}' is already registered")
        self._tools[tool.spec.name] = tool

    def get(self, name: str) -> AgentTool | None:
        return self._tools.get(name)

    def names(self) -> list[str]:
        return sorted(self._tools)

    def list_specs(self) -> list[ToolSpec]:
        return [self._tools[name].spec for name in self.names()]

    def resolve(self, configs: list[dict[str, Any]] | None) -> dict[str, ResolvedTool]:
        """Validate ``tools_config`` and return ready-to-use tools keyed by name."""
        resolved: dict[str, ResolvedTool] = {}
        for entry in configs or []:
            if not isinstance(entry, dict):
                raise ToolConfigurationError("each tool entry must be an object")
            name = entry.get("name")
            config = entry.get("config") or {}
            if not isinstance(name, str) or not name:
                raise ToolConfigurationError("each tool entry needs a name")
            if not isinstance(config, dict):
                raise ToolConfigurationError(f"tool '{name}' config must be an object")
            tool = self._tools.get(name)
            if tool is None:
                raise ToolConfigurationError(f"unknown tool '{name}'")
            resolved[name] = ResolvedTool(tool=tool, config=config)
        return resolved

    def validate_names(self, configs: list[dict[str, Any]] | None) -> list[str]:
        """Return the unknown tool names in a config (empty when valid)."""
        unknown: list[str] = []
        for entry in configs or []:
            name = entry.get("name") if isinstance(entry, dict) else entry
            if not isinstance(name, str) or name not in self._tools:
                unknown.append(str(name))
        return unknown


tool_registry = ToolRegistry(
    [
        CalculatorTool(),
        CurrentTimeTool(),
        HttpGetTool(),
    ]
)


def to_json_argument(value: Any) -> str:
    """Compact JSON used when sending tool output back to the model."""
    return json.dumps(value, ensure_ascii=False, default=str)
