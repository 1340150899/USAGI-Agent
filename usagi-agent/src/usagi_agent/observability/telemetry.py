"""OpenTelemetry setup and instrumentation helpers (design §25).

Initialization is split from execution: :class:`ObservabilityInitializer` builds the
provider at Server Bootstrap; :func:`span` / metric helpers are used only at Run time.
Telemetry never carries sensitive content (§25.5).
"""
from __future__ import annotations

from contextlib import contextmanager
from typing import TYPE_CHECKING, Any, Iterator

# OTel API is the only stable dependency; concrete SDK is wired here but services
# must depend on the API surface, not on Tempo/Prometheus SDKs (§3.8).
from opentelemetry import metrics, trace
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import ConsoleMetricExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import (
    ConsoleSpanExporter,
    SimpleSpanProcessor,
)

if TYPE_CHECKING:
    from usagi_agent.registry.bootstrap import BootstrapSettings

# Stable, low-cardinality span names (§25.3). Specific names go into attributes, never
# into the span name.
SPAN_NAMES = {
    "workflow.run": "workflow.run",
    "agent.loop": "agent.loop",
    "agent.pass": "agent.pass",
    "rule.execute": "rule.execute",
    "model.invoke": "model.invoke",
    "tool.execute": "tool.execute",
    "policy.evaluate": "policy.evaluate",
    "memory.recall": "memory.recall",
}

# Attributes that MUST NOT be attached to spans/metrics (§25.3/§25.4/§25.5).
_FORBIDDEN_ATTR_KEYS = frozenset(
    {
        "run_id",
        "thread_id",
        "checkpoint_id",
        "trace_id",
        "user_id",
        "artifact_ref",
        "artifact_id",
        "tool_call_id",
        "principal_id",
    }
)


def _scrub_attributes(attrs: dict[str, Any]) -> dict[str, Any]:
    """Drop any forbidden high-cardinality / sensitive attribute (defense in depth)."""
    return {k: v for k, v in attrs.items() if k not in _FORBIDDEN_ATTR_KEYS}


class ObservabilityProvider:
    """Holds configured Tracer / Meter handles. Purely a runtime holder — no logic."""

    __slots__ = ("tracer", "meter", "resource", "_tracer_provider", "_meter_provider")

    def __init__(
        self,
        *,
        tracer: trace.Tracer,
        meter: metrics.Meter,
        resource: Resource,
        tracer_provider: TracerProvider,
        meter_provider: MeterProvider,
    ) -> None:
        self.tracer = tracer
        self.meter = meter
        self.resource = resource
        self._tracer_provider = tracer_provider
        self._meter_provider = meter_provider

    def shutdown(self) -> None:
        """Drain exporters at process exit. Must not raise into the business path."""
        for prov in (self._tracer_provider, self._meter_provider):
            shutdown = getattr(prov, "shutdown", None)
            if shutdown is not None:
                try:
                    shutdown()
                except Exception:
                    # Telemetry failure must never block the business path (§25.6).
                    pass


class ObservabilityInitializer:
    """Bootstrap-time initializer for OpenTelemetry (§25.1, §25.2).

    ``init`` only constructs the provider; it does not emit any spans/metrics itself.
    """

    @staticmethod
    def init(settings: "BootstrapSettings") -> ObservabilityProvider:
        resource = Resource.create(
            {
                "service.name": settings.service_name,
                "service.version": settings.service_version,
                "service.instance.id": settings.service_instance_id,
                "deployment.environment.name": settings.deployment_environment,
                "usagi.runtime.version": settings.service_version,
            }
        )

        tracer_provider = TracerProvider(resource=resource)
        # First version: fixed full sampling, local console exporter (§25.6). A real
        # OTLP Collector is plugged in by pointing settings.otel_endpoint at one.
        exporter: Any
        if settings.otel_endpoint:
            try:
                from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
                    OTLPSpanExporter,
                )
                from opentelemetry.exporter.otlp.proto.http.metric_exporter import (
                    OTLPMetricExporter,
                )

                tracer_provider.add_span_processor(
                    SimpleSpanProcessor(OTLPSpanExporter(endpoint=settings.otel_endpoint))
                )
                metric_exporter = OTLPMetricExporter(endpoint=settings.otel_endpoint)
            except Exception:
                # Exporter unavailable -> fall back to console, never block startup.
                tracer_provider.add_span_processor(SimpleSpanProcessor(ConsoleSpanExporter()))
                metric_exporter = ConsoleMetricExporter()
        else:
            tracer_provider.add_span_processor(SimpleSpanProcessor(ConsoleSpanExporter()))
            metric_exporter = ConsoleMetricExporter()

        meter_provider: MeterProvider
        try:
            from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader

            reader = PeriodicExportingMetricReader(metric_exporter, export_interval_millis=5000)
            meter_provider = MeterProvider(resource=resource, metric_readers=[reader])
        except Exception:
            # Keep a minimal provider; telemetry must not block (§25.6).
            meter_provider = MeterProvider(resource=resource)

        trace.set_tracer_provider(tracer_provider)
        metrics.set_meter_provider(meter_provider)

        tracer = tracer_provider.get_tracer("usagi-agent")
        meter = meter_provider.get_meter("usagi-agent")
        return ObservabilityProvider(
            tracer=tracer,
            meter=meter,
            resource=resource,
            tracer_provider=tracer_provider,
            meter_provider=meter_provider,
        )


@contextmanager
def span(provider: ObservabilityProvider, name: str, **attributes: Any) -> Iterator[Any]:
    """Open a manual span with scrubbed attributes. ``name`` must be a SPAN_NAMES value."""
    attrs = _scrub_attributes(dict(attributes))
    with provider.tracer.start_as_current_span(name, attributes=attrs) as s:
        yield s


def metric_counter(provider: ObservabilityProvider, name: str, unit: str = "1"):
    return provider.meter.create_counter(name=name, unit=unit, description=name)


def metric_histogram(provider: ObservabilityProvider, name: str, unit: str = "ms"):
    return provider.meter.create_histogram(name=name, unit=unit, description=name)


def metric_up_down_counter(provider: ObservabilityProvider, name: str, unit: str = "1"):
    return provider.meter.create_up_down_counter(name=name, unit=unit, description=name)
