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

__all__ = [
    "ObservabilityInitializer",
    "ObservabilityProvider",
    "current_trace_context",
    "metric_counter",
    "metric_histogram",
    "metric_up_down_counter",
    "operation",
    "span",
]
