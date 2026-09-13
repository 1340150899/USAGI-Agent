"""OpenTelemetry observability (design §25).

This module owns the *initialization* of tracing/metrics resources and exposes thin
helpers for manual instrumentation. It must never record ``ArtifactRef``, checkpoint id,
principal id or ``run_id`` in span attributes or metric attributes (§25.3/§25.4).
"""

from usagi_agent.observability.telemetry import (
    ObservabilityInitializer,
    ObservabilityProvider,
    current_trace_context,
    metric_counter,
    metric_histogram,
    metric_up_down_counter,
    operation,
    span,
)
from usagi_agent.observability.file_logging import configure_service_logging, default_log_root

__all__ = [
    "ObservabilityInitializer",
    "ObservabilityProvider",
    "current_trace_context",
    "metric_counter",
    "metric_histogram",
    "metric_up_down_counter",
    "operation",
    "span",
    "configure_service_logging",
    "default_log_root",
]
