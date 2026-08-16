"""Best-effort OpenTelemetry initialization; exporters never gate task execution."""
import logging
from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, ConsoleSpanExporter
from app.config import get_settings

logger = logging.getLogger(__name__)

def configure_tracing() -> None:
    settings = get_settings()
    if not settings.otel_enabled or isinstance(trace.get_tracer_provider(), TracerProvider):
        return
    provider = TracerProvider(resource=Resource.create({"service.name": "distributed-task-platform"}))
    # Console exporter is useful locally and cannot make execution unavailable.
    provider.add_span_processor(BatchSpanProcessor(ConsoleSpanExporter()))
    try:
        if settings.otel_exporter_otlp_endpoint:
            from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
            provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint=settings.otel_exporter_otlp_endpoint, insecure=True)))
    except Exception:  # exporter configuration is observational only
        logger.exception("OTLP exporter initialization failed; console tracing remains available")
    trace.set_tracer_provider(provider)

def tracer(name: str):
    return trace.get_tracer(name)
