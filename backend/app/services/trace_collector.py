"""Trace collection service.

Every agent operation is recorded twice:

1. as an OpenTelemetry span (exported to the OTLP collector / Jaeger);
2. as a ``traces`` row (and optional ``tool_calls`` rows) in PostgreSQL.

The collector owns the span *tree*: nesting is tracked explicitly so
``traces.parent_trace_id`` is correct even when the OTel SDK is disabled, and
OpenTelemetry trace/span ids are copied onto each row for deep links into Jaeger.

Recording failures are logged and swallowed: observability must never break the
business operation it observes.
"""

from __future__ import annotations

import logging
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Iterator, Protocol
from uuid import UUID

from opentelemetry.trace import Span, SpanKind, Status, StatusCode
from sqlalchemy.orm import Session

from app.core import telemetry
from app.core.redis import NullPublisher
from app.core.serialization import to_jsonable, truncate_json
from app.models import Run, SpanStatus, SpanType, ToolCall, Trace
from app.services.cost_calculator import quantize_cost

logger = logging.getLogger(__name__)


class EventPublisherProtocol(Protocol):
    def publish(self, run_id: UUID | str, event_type: str, data: dict | None = None) -> None: ...


@dataclass
class ToolCallDraft:
    """A tool invocation captured while its parent span is still open."""

    tool_name: str
    tool_input: dict[str, Any] | None = None
    started_perf: float = field(default_factory=time.perf_counter)
    tool_output: dict[str, Any] | None = None
    duration_ms: int = 0
    success: bool = True
    error_message: str | None = None

    def succeed(self, output: Any) -> None:
        self.duration_ms = int((time.perf_counter() - self.started_perf) * 1000)
        self.tool_output = truncate_json(output) if output is not None else None
        self.success = True
        self.error_message = None

    def fail(self, message: str, output: Any = None) -> None:
        self.duration_ms = int((time.perf_counter() - self.started_perf) * 1000)
        self.tool_output = truncate_json(output) if output is not None else None
        self.success = False
        self.error_message = message[:4_000]


@dataclass
class SpanHandle:
    """Mutable span data filled in by the caller inside a ``with`` block."""

    id: UUID
    span_name: str
    span_type: SpanType
    input_data: dict[str, Any] | None
    started_at: datetime
    start_perf: float
    parent_trace_id: UUID | None
    otel_span: Span
    output_data: Any = None
    status: SpanStatus | None = None
    error_details: dict[str, Any] | None = None
    tokens_input: int = 0
    tokens_output: int = 0
    model_name: str | None = None
    cost_override: Decimal | None = None
    tool_calls: list[ToolCallDraft] = field(default_factory=list)
    exception: BaseException | None = None
    attributes: dict[str, Any] = field(default_factory=dict)
    trace_id: str = ""
    span_id: str = ""
    persisted: bool = False

    @property
    def tokens_used(self) -> int:
        return self.tokens_input + self.tokens_output

    def add_tool_call(
        self, tool_name: str, tool_input: dict[str, Any] | None = None
    ) -> ToolCallDraft:
        draft = ToolCallDraft(tool_name=tool_name, tool_input=tool_input)
        self.tool_calls.append(draft)
        return draft

    def set_error(self, message: str, *, details: dict[str, Any] | None = None) -> None:
        self.status = SpanStatus.ERROR
        self.error_details = {"message": message, **(details or {})}

    def set_timeout(self, message: str = "span timed out") -> None:
        self.status = SpanStatus.TIMEOUT
        self.error_details = {"message": message}

    def set_attribute(self, key: str, value: Any) -> None:
        self.attributes[key] = value


class TraceCollector:
    """Create spans for one run and persist them as they complete."""

    def __init__(
        self,
        db: Session,
        run: Run,
        *,
        publisher: EventPublisherProtocol | None = None,
        traceparent: str | None = None,
    ) -> None:
        self.db = db
        self.run = run
        self.tracer = telemetry.get_tracer()
        self.publisher: EventPublisherProtocol = publisher or NullPublisher()
        self.traceparent = traceparent
        self.trace_id: str | None = None
        self._stack: list[SpanHandle] = []
        # Rows are inserted when a span opens and updated when it closes, so a
        # child always references an existing parent row (FK-safe, and the
        # dashboard can show in-flight spans).
        self._rows: dict[UUID, Trace] = {}

    # -- lifecycle -----------------------------------------------------
    @contextmanager
    def run_span(
        self,
        name: str = "agent.run",
        input_data: dict[str, Any] | None = None,
    ) -> Iterator[SpanHandle]:
        """Root span for the whole run. Nested spans should use :meth:`span`."""
        parent_context = telemetry.context_from_traceparent(self.traceparent)
        payload = input_data or {"input_prompt": self.run.input_prompt}
        with self.span(
            name,
            SpanType.DECISION,
            input_data=payload,
            parent_context=parent_context,
            attributes={
                "acr.run_id": str(self.run.id),
                "acr.agent_id": str(self.run.agent_id),
            },
        ) as handle:
            self.trace_id = handle.trace_id
            self.run.metadata_json["trace_id"] = self.trace_id
            try:
                self.db.commit()
            except Exception:  # noqa: BLE001 - observability must never break the run
                self.db.rollback()
                logger.exception("Could not persist trace id for run %s", self.run.id)
            yield handle

    @contextmanager
    def span(
        self,
        span_name: str,
        span_type: SpanType,
        *,
        input_data: dict[str, Any] | None = None,
        parent_context: Any = None,
        attributes: dict[str, Any] | None = None,
        model_name: str | None = None,
    ) -> Iterator[SpanHandle]:
        """Open a span, persist it on exit and re-raise any exception."""
        parent = self._stack[-1] if self._stack else None
        handle = SpanHandle(
            id=uuid.uuid4(),
            span_name=span_name[:200],
            span_type=span_type,
            input_data=truncate_json(input_data) if input_data is not None else None,
            started_at=datetime.now(timezone.utc),
            start_perf=time.perf_counter(),
            parent_trace_id=parent.id if parent else None,
            otel_span=None,  # type: ignore[arg-type] - set immediately below
            model_name=model_name,
            attributes=dict(attributes or {}),
        )

        with self.tracer.start_as_current_span(
            span_name,
            context=parent_context,
            kind=SpanKind.INTERNAL,
            record_exception=False,
            set_status_on_exception=False,
        ) as otel_span:
            handle.otel_span = otel_span
            ctx = otel_span.get_span_context()
            if ctx.is_valid:
                handle.trace_id = format(ctx.trace_id, "032x")
                handle.span_id = format(ctx.span_id, "016x")
            else:  # telemetry disabled: keep local ids so rows stay linkable
                handle.trace_id = self.trace_id or uuid.uuid4().hex
                handle.span_id = uuid.uuid4().hex[:16]
            self._set_otel_attributes(handle)
            self._stack.append(handle)
            self._open_row(handle)
            try:
                yield handle
            except BaseException as exc:  # noqa: BLE001 - recorded then re-raised
                handle.exception = exc
                handle.status = (
                    SpanStatus.TIMEOUT
                    if isinstance(exc, TimeoutError)
                    else SpanStatus.ERROR
                )
                handle.error_details = {
                    "type": type(exc).__name__,
                    "message": str(exc)[:4_000],
                }
                otel_span.record_exception(exc)
                raise
            finally:
                self._stack.pop()
                self._finalize(handle)

    # -- helpers -------------------------------------------------------
    def publish(self, event_type: str, data: dict[str, Any] | None = None) -> None:
        try:
            self.publisher.publish(self.run.id, event_type, data or {})
        except Exception:  # pragma: no cover - publishing is best effort
            logger.debug("Event publish failed", exc_info=True)

    def _set_otel_attributes(self, handle: SpanHandle) -> None:
        attributes = {
            "acr.run_id": str(self.run.id),
            "acr.span_id": str(handle.id),
            "acr.span_type": handle.span_type.value,
            **(handle.attributes or {}),
        }
        if handle.model_name:
            attributes["gen_ai.request.model"] = handle.model_name
        telemetry.record_span_attributes(handle.otel_span, attributes)

    def _open_row(self, handle: SpanHandle) -> None:
        """Insert a placeholder row when the span opens.

        Inserting on open (and updating on close) guarantees that a child span's
        ``parent_trace_id`` always references an existing row, regardless of the
        order in which nested spans finish. It also lets the dashboard show a
        span while it is still running.
        """
        trace = Trace(
            id=handle.id,
            run_id=self.run.id,
            parent_trace_id=handle.parent_trace_id,
            span_name=handle.span_name,
            span_type=handle.span_type,
            input_data=to_jsonable(handle.input_data) if handle.input_data is not None else None,
            status=SpanStatus.OK,
            duration_ms=0,
            tokens_used=0,
            cost_usd=Decimal("0"),
            timestamp=handle.started_at,
            trace_id=handle.trace_id,
            span_id=handle.span_id,
            model_name=handle.model_name,
        )
        try:
            self.db.add(trace)
            self.db.commit()
        except Exception:  # noqa: BLE001 - observability must never break the run
            self.db.rollback()
            logger.exception("Could not open trace span %s (%s)", handle.span_name, handle.id)
            return
        self._rows[handle.id] = trace
        handle.persisted = True

    def _finalize(self, handle: SpanHandle) -> None:
        """Persist the span and publish a completion event. Never raises."""
        duration_ms = int((time.perf_counter() - handle.start_perf) * 1000)
        status = handle.status or SpanStatus.OK
        cost = (
            handle.cost_override
            if handle.cost_override is not None
            else Decimal("0")
        )
        trace_id = handle.trace_id or self.trace_id or uuid.uuid4().hex
        span_id = handle.span_id or uuid.uuid4().hex[:16]

        output_data = (
            truncate_json(handle.output_data) if handle.output_data is not None else None
        )
        error_details = (
            to_jsonable(handle.error_details) if handle.error_details is not None else None
        )
        tool_calls = [
            ToolCall(
                trace_id=handle.id,
                tool_name=draft.tool_name[:120],
                tool_input=to_jsonable(draft.tool_input) if draft.tool_input is not None else None,
                tool_output=draft.tool_output,
                duration_ms=max(draft.duration_ms, 0),
                success=draft.success,
                error_message=draft.error_message,
            )
            for draft in handle.tool_calls
        ]

        trace = self._rows.pop(handle.id, None)
        try:
            if trace is None:
                # The open-time insert failed (or the handle predates the row):
                # try once more with the complete payload.
                trace = Trace(
                    id=handle.id,
                    run_id=self.run.id,
                    parent_trace_id=handle.parent_trace_id,
                    span_name=handle.span_name,
                    span_type=handle.span_type,
                    input_data=to_jsonable(handle.input_data)
                    if handle.input_data is not None
                    else None,
                    output_data=output_data,
                    status=status,
                    duration_ms=max(duration_ms, 0),
                    tokens_used=handle.tokens_used,
                    tokens_input=handle.tokens_input,
                    tokens_output=handle.tokens_output,
                    cost_usd=quantize_cost(cost),
                    error_details=error_details,
                    timestamp=handle.started_at,
                    trace_id=trace_id,
                    span_id=span_id,
                    model_name=handle.model_name,
                )
                trace.tool_calls = tool_calls
                self.db.add(trace)
            else:
                trace.output_data = output_data
                trace.status = status
                trace.duration_ms = max(duration_ms, 0)
                trace.tokens_used = handle.tokens_used
                trace.tokens_input = handle.tokens_input
                trace.tokens_output = handle.tokens_output
                trace.cost_usd = quantize_cost(cost)
                trace.error_details = error_details
                trace.model_name = handle.model_name
                trace.tool_calls = tool_calls
            self.db.commit()
        except Exception:  # noqa: BLE001 - observability must never break the run
            self.db.rollback()
            logger.exception("Could not persist trace %s (%s)", handle.span_name, handle.id)

        # OTel status mirrors the durable record.
        if status == SpanStatus.OK:
            handle.otel_span.set_status(Status(StatusCode.OK))
        else:
            handle.otel_span.set_status(
                Status(StatusCode.ERROR, (handle.error_details or {}).get("message", status.value))
            )

        attributes = {"type": handle.span_type.value, "status": status.value}
        try:
            telemetry.spans_counter.add(1, attributes)
            telemetry.span_latency.record(duration_ms, attributes)
            if handle.tokens_used:
                telemetry.tokens_counter.add(
                    handle.tokens_input, {"model": handle.model_name or "", "direction": "in"}
                )
                telemetry.tokens_counter.add(
                    handle.tokens_output, {"model": handle.model_name or "", "direction": "out"}
                )
            if cost:
                telemetry.cost_counter.add(float(cost), {"model": handle.model_name or ""})
        except Exception:  # pragma: no cover - metric backend issues
            logger.debug("Metric recording failed", exc_info=True)

        self.publish(
            "trace.completed",
            {
                "trace": {
                    "id": str(handle.id),
                    "span_name": handle.span_name,
                    "span_type": handle.span_type.value,
                    "status": status.value,
                    "duration_ms": max(duration_ms, 0),
                    "tokens_used": handle.tokens_used,
                    "cost_usd": float(quantize_cost(cost)),
                    "parent_trace_id": str(handle.parent_trace_id)
                    if handle.parent_trace_id
                    else None,
                    "trace_id": trace_id,
                    "span_id": span_id,
                    "model_name": handle.model_name,
                    "tool_call_count": len(handle.tool_calls),
                    "timestamp": handle.started_at.isoformat(),
                }
            },
        )
