"""Self-observability: Prometheus metrics and OpenTelemetry tracing across API -> job -> graph."""

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from opentelemetry import context as otel_context
from opentelemetry import propagate, trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, SimpleSpanProcessor, SpanExporter
from prometheus_client import CollectorRegistry, Counter, Gauge, Histogram, generate_latest

from causaldeploy.config import Settings

REGISTRY = CollectorRegistry()

HTTP_SECONDS = Histogram(
    "cd_http_request_seconds",
    "API request latency",
    ["method", "route", "status"],
    registry=REGISTRY,
)
JOBS_TOTAL = Counter(
    "cd_jobs_total", "Jobs finished by outcome", ["kind", "outcome"], registry=REGISTRY
)
JOB_SECONDS = Histogram("cd_job_seconds", "Pipeline stage latency", ["kind"], registry=REGISTRY)
EVENTS_INGESTED = Counter(
    "cd_events_ingested_total", "Events accepted by the ingest API", ["type"], registry=REGISTRY
)
GRAPH_SLOW_QUERIES = Counter(
    "cd_graph_slow_queries_total", "Graph queries slower than the slow threshold", registry=REGISTRY
)
QUEUE_DEPTH = Gauge("cd_queue_depth", "Jobs waiting in the ARQ queue", registry=REGISTRY)

_TRACER = trace.get_tracer("causaldeploy")


def render_metrics() -> bytes:
    return generate_latest(REGISTRY)


def init_tracing(settings: Settings, exporter: SpanExporter | None = None) -> TracerProvider | None:
    """Enable tracing when an OTLP endpoint (or an explicit exporter, in tests) is given."""
    if exporter is None and not settings.otlp_endpoint:
        return None
    provider = TracerProvider(resource=Resource.create({"service.name": "causaldeploy"}))
    if exporter is not None:
        provider.add_span_processor(SimpleSpanProcessor(exporter))
    else:
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

        provider.add_span_processor(
            BatchSpanProcessor(
                OTLPSpanExporter(endpoint=settings.otlp_endpoint.rstrip("/") + "/v1/traces")
            )
        )
    trace.set_tracer_provider(provider)
    global _TRACER
    _TRACER = provider.get_tracer("causaldeploy")
    return provider


@contextmanager
def span(name: str, parent: dict[str, str] | None = None, **attrs: Any) -> Iterator[trace.Span]:
    """Start a span, optionally as a child of a propagated trace context (job payloads)."""
    ctx = propagate.extract(parent) if parent else None
    token = otel_context.attach(ctx) if ctx is not None else None
    try:
        with _TRACER.start_as_current_span(name, attributes=attrs) as sp:
            yield sp
    finally:
        if token is not None:
            otel_context.detach(token)


def inject_trace() -> dict[str, str]:
    carrier: dict[str, str] = {}
    propagate.inject(carrier)
    return carrier
