"""OpenTelemetry bootstrap and helpers.

Traces are exported twice:

* to the OTLP collector (and from there to Jaeger), for distributed tracing;
* to PostgreSQL, via :mod:`app.services.trace_collector`, for the product UI.

The SDK is optional at runtime: if ``OTEL_ENABLED=false`` (tests, local hacking)
all helpers become no-ops so the application still starts without a collector.
"""

from __future__ import annotations

import logging
from typing import Any

from opentelemetry import metrics, trace
from opentelemetry.context import Context
from opentelemetry.propagate import extract, inject
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.sdk.trace.sampling import ParentBased, TraceIdRatioBased

from app.core.config import settings

logger = logging.getLogger(__name__)

_initialized = False
_tracer: trace.Tracer = trace.get_tracer("agent-control-room")

# Module-level metric instruments are proxies until a real provider is installed.
_meter = metrics.get_meter("agent-control-room")

runs_counter = _meter.create_counter("acr.runs", description="Runs finished, by status")
spans_counter = _meter.create_counter("acr.spans", description="Spans recorded, by type")
tokens_counter = _meter.create_counter("acr.tokens", unit="token", description="Tokens used")
cost_counter = _meter.create_counter("acr.cost", unit="USD", description="Spend in USD")
span_latency = _meter.create_histogram("acr.span.latency", unit="ms", description="Span latency")
active_runs = _meter.create_up_down_counter("acr.runs.active", description="Currently running agents")


def setup_telemetry(service_suffix: str = "") -> None:
    """Configure tracing, metrics and logging export. Idempotent."""
    global _initialized, _tracer, _meter

    if _initialized or not settings.otel_enabled:
        _initialized = True
        return

    service_name = settings.otel_service_name
    if service_suffix:
        service_name = f"{service_name}-{service_suffix}"

    resource = Resource.create(
        {
            "service.name": service_name,
            "service.version": settings.app_version,
            "deployment.environment": settings.environment,
        }
    )

    try:
        provider = TracerProvider(
            resource=resource,
            sampler=ParentBased(root=TraceIdRatioBased(settings.otel_traces_sampler_ratio)),
        )
        from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter

        provider.add_span_processor(
            BatchSpanProcessor(
                OTLPSpanExporter(
                    endpoint=settings.otel_exporter_otlp_endpoint,
                    insecure=settings.otel_insecure,
                ),
                max_queue_size=2048,
                schedule_delay_millis=5000,
                max_export_batch_size=512,
            )
        )
        trace.set_tracer_provider(provider)
        _tracer = trace.get_tracer("agent-control-room")
    except Exception:  # pragma: no cover - exporter unavailable in some environments
        logger.exception("OpenTelemetry trace exporter could not be configured; continuing without OTLP")

    if settings.otel_export_metrics:
        try:
            from opentelemetry.exporter.otlp.proto.grpc.metric_exporter import OTLPMetricExporter
            from opentelemetry.sdk.metrics import MeterProvider
            from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader

            reader = PeriodicExportingMetricReader(
                OTLPMetricExporter(
                    endpoint=settings.otel_exporter_otlp_endpoint,
                    insecure=settings.otel_insecure,
                ),
                export_interval_millis=30_000,
            )
            metrics.set_meter_provider(MeterProvider(resource=resource, metric_readers=[reader]))
            _meter = metrics.get_meter("agent-control-room")
        except Exception:  # pragma: no cover
            logger.exception("OpenTelemetry metric exporter could not be configured")

    if settings.otel_export_logs:
        try:
            from opentelemetry._logs import set_logger_provider
            from opentelemetry.exporter.otlp.proto.grpc._log_exporter import OTLPLogExporter
            from opentelemetry.sdk._logs import LoggerProvider, LoggingHandler
            from opentelemetry.sdk._logs.export import BatchLogRecordProcessor

            logger_provider = LoggerProvider(resource=resource)
            logger_provider.add_log_record_processor(
                BatchLogRecordProcessor(
                    OTLPLogExporter(
                        endpoint=settings.otel_exporter_otlp_endpoint,
                        insecure=settings.otel_insecure,
                    ),
                    max_queue_size=2048,
                    schedule_delay_millis=5000,
                )
            )
            set_logger_provider(logger_provider)
            logging.getLogger().addHandler(
                LoggingHandler(level=logging.INFO, logger_provider=logger_provider)
            )
        except Exception:  # pragma: no cover
            logger.exception("OpenTelemetry log exporter could not be configured")

    _initialized = True


def get_tracer() -> trace.Tracer:
    """Return the module tracer (a no-op tracer when telemetry is disabled)."""
    return _tracer


def get_meter() -> metrics.Meter:
    return _meter


# --- W3C context propagation ------------------------------------------------
def current_traceparent() -> str | None:
    """Serialise the active span as a W3C ``traceparent`` header value."""
    if not settings.otel_enabled:
        return None
    carrier: dict[str, str] = {}
    inject(carrier)
    return carrier.get("traceparent")


def context_from_traceparent(traceparent: str | None) -> Context | None:
    """Rebuild a remote parent context from a stored ``traceparent``."""
    if not settings.otel_enabled or not traceparent:
        return None
    return extract({"traceparent": traceparent})


def force_flush(timeout_millis: int = 5000) -> None:
    """Flush buffered spans (Celery tasks and shutdown hooks)."""
    provider = trace.get_tracer_provider()
    flush = getattr(provider, "force_flush", None)
    if callable(flush):
        try:
            flush(timeout_millis)
        except Exception:  # pragma: no cover - best effort
            logger.debug("Telemetry flush failed", exc_info=True)


def record_span_attributes(span: trace.Span, attributes: dict[str, Any]) -> None:
    """Set only primitive, non-null attributes, which is all OTel accepts."""
    for key, value in attributes.items():
        if value is None:
            continue
        if isinstance(value, (bool, int, float, str)):
            span.set_attribute(key, value)
        else:
            span.set_attribute(key, str(value))
