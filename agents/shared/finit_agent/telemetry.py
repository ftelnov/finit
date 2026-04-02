"""OpenTelemetry instrumentation setup for Finit agents."""

from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)


def setup_telemetry(service_name: str) -> None:
    """Initialize OpenTelemetry with OTLP exporter.

    Configures tracing with an OTLP gRPC exporter. The endpoint is read from
    the OTEL_EXPORTER_OTLP_ENDPOINT environment variable (default:
    otel-collector:4317).

    If the OpenTelemetry SDK is not fully available or the collector is
    unreachable at startup, telemetry is silently disabled so the agent
    can still serve requests.
    """
    otlp_endpoint = os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT", "http://otel-collector:4317")

    try:
        from opentelemetry import trace
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter

        resource = Resource.create({"service.name": service_name})
        provider = TracerProvider(resource=resource)
        exporter = OTLPSpanExporter(endpoint=otlp_endpoint, insecure=True)
        provider.add_span_processor(BatchSpanProcessor(exporter))
        trace.set_tracer_provider(provider)

        logger.info(
            "OpenTelemetry configured for %s -> %s",
            service_name,
            otlp_endpoint,
        )
    except Exception as exc:
        logger.warning("OpenTelemetry setup skipped (%s): %s", service_name, exc)


def instrument_fastapi(app: object) -> None:
    """Instrument a FastAPI app with OpenTelemetry (best-effort)."""
    try:
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

        FastAPIInstrumentor.instrument_app(app)  # type: ignore[arg-type]
        logger.info("FastAPI instrumented with OpenTelemetry")
    except Exception as exc:
        logger.warning("FastAPI instrumentation skipped: %s", exc)
