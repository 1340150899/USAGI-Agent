"""Backend-neutral OpenTelemetry setup and Agent-runtime instrumentation."""

from __future__ import annotations

import json
import time
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from threading import Lock
from typing import TYPE_CHECKING, Any

from opentelemetry import metrics, trace
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor, SpanExporter, SpanExportResult
from opentelemetry.sdk.trace.sampling import ParentBased, TraceIdRatioBased
from opentelemetry.trace import Status, StatusCode

if TYPE_CHECKING:
    from usagi_agent.registry.bootstrap import BootstrapSettings
    from usagi_agent.types.action import ToolObservation
    from usagi_agent.types.model import ModelUsage


SPAN_NAMES = frozenset(
    {
        "workflow.run",
        "agent.loop",
        "agent.pass",
        "pipeline.stage",
        "rule.execute",
        "model.invoke",
        "tool.execute",
        "policy.evaluate",
        "memory.recall",
        "agent.request",
        "service.lifecycle",
    }
)

# Payloads and identifiers used to retrieve payloads belong in the AuditStore, not OTel.
_FORBIDDEN_ATTR_KEYS = frozenset(
    {
        "run_id",
        "thread_id",
        "session_id",
        "checkpoint_id",
        "trace_id",
        "user_id",
        "artifact_ref",
        "artifact_id",
        "tool_call_id",
        "principal_id",
        "prompt",
        "messages",
        "arguments",
        "output",
    }
)
_GLOBAL_PROVIDER_LOCK = Lock()
_GLOBAL_PROVIDER_CONFIGURED = False


class JsonlSpanExporter(SpanExporter):
    """Persist completed spans as one JSON object per line for local diagnosis."""

    def __init__(self, directory: Path) -> None:
        self.directory = directory

    def export(self, spans) -> SpanExportResult:
        try:
            self.directory.mkdir(parents=True, exist_ok=True)
            for item in spans:
                ended = item.end_time or item.start_time
                day = datetime.fromtimestamp(
                    ended / 1_000_000_000, timezone.utc
                ).astimezone().date().isoformat()
                context = item.context
                parent = item.parent
                record = {
                    "trace_id": f"{context.trace_id:032x}",
                    "span_id": f"{context.span_id:016x}",
                    "parent_span_id": f"{parent.span_id:016x}" if parent else None,
                    "name": item.name,
                    "start_time": datetime.fromtimestamp(
                        item.start_time / 1_000_000_000, timezone.utc
                    ).isoformat(),
                    "end_time": datetime.fromtimestamp(
                        ended / 1_000_000_000, timezone.utc
                    ).isoformat(),
                    "duration_ms": round((ended - item.start_time) / 1_000_000, 3),
                    "status": item.status.status_code.name.lower(),
                    "attributes": dict(item.attributes or {}),
                    "resource": dict(item.resource.attributes or {}),
                }
                with (self.directory / f"{day}.spans.jsonl").open(
                    "a", encoding="utf-8"
                ) as output:
                    output.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
            return SpanExportResult.SUCCESS
        except Exception:
            return SpanExportResult.FAILURE


def _scrub_attributes(attrs: dict[str, Any]) -> dict[str, Any]:
    """Allow scalar, non-sensitive attributes and reject known dangerous keys."""
    clean: dict[str, Any] = {}
    for key, value in attrs.items():
        normalized = key.lower().replace("-", "_")
        leaf = normalized.rsplit(".", 1)[-1]
        if normalized in _FORBIDDEN_ATTR_KEYS or leaf in _FORBIDDEN_ATTR_KEYS:
            continue
        if (
            isinstance(value, (str, bool, int, float))
            or isinstance(value, (tuple, list))
            and all(isinstance(item, (str, bool, int, float)) for item in value)
        ):
            clean[key] = value
    return clean


def _signal_endpoint(base: str, signal: str) -> str:
    endpoint = base.rstrip("/")
    return endpoint if endpoint.endswith(f"/v1/{signal}") else f"{endpoint}/v1/{signal}"


class TelemetryOperation:
    """Mutable outcome handle yielded by :func:`operation`."""

    def __init__(self, span: Any) -> None:
        self.span = span
        self.outcome = "success"

    def set_outcome(self, outcome: str) -> None:
        self.outcome = outcome
        self.span.set_attribute("usagi.operation.outcome", outcome)
        if outcome in {"error", "failed", "unknown"}:
            self.span.set_status(Status(StatusCode.ERROR))

    def set_attribute(self, key: str, value: Any) -> None:
        attrs = _scrub_attributes({key: value})
        if key in attrs:
            self.span.set_attribute(key, attrs[key])


class ObservabilityProvider:
    """Owns providers and stable, low-cardinality runtime instruments."""

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
        self.operation_count = meter.create_counter(
            "usagi.operation.count",
            unit="{operation}",
            description="Completed USAGI runtime operations",
        )
        self.operation_duration = meter.create_histogram(
            "usagi.operation.duration",
            unit="ms",
            description="USAGI runtime operation duration",
        )
        self.operation_active = meter.create_up_down_counter(
            "usagi.operation.active",
            unit="{operation}",
            description="Currently active USAGI runtime operations",
        )
        self.model_tokens = meter.create_counter(
            "usagi.model.token.usage",
            unit="{token}",
            description="Model input and output token usage",
        )
        self.model_cost = meter.create_counter(
            "usagi.model.cost", unit="USD", description="Estimated model cost"
        )
        self.tool_count = meter.create_counter(
            "usagi.tool.execution.count",
            unit="{execution}",
            description="Tool execution outcomes",
        )
        self.policy_count = meter.create_counter(
            "usagi.policy.decision.count",
            unit="{decision}",
            description="Policy decision outcomes",
        )

    def record_model_usage(self, usage: ModelUsage, *, agent: str, model: str) -> None:
        base = _scrub_attributes(
            {"usagi.agent.name": agent, "gen_ai.request.model": model}
        )
        self.model_tokens.add(
            usage.input_tokens, {**base, "gen_ai.token.type": "input"}
        )
        self.model_tokens.add(
            usage.output_tokens, {**base, "gen_ai.token.type": "output"}
        )
        self.model_cost.add(float(Decimal(usage.cost)), base)

    def record_tool(self, observation: ToolObservation) -> None:
        self.tool_count.add(
            1,
            _scrub_attributes(
                {
                    "usagi.tool.name": observation.tool_name,
                    "usagi.operation.outcome": observation.status,
                    "error.type": observation.error_code,
                }
            ),
        )

    def record_policy(self, *, action: str, effect: str) -> None:
        self.policy_count.add(
            1,
            _scrub_attributes(
                {"usagi.policy.action": action, "usagi.policy.effect": effect}
            ),
        )

    def force_flush(self, timeout_millis: int = 10_000) -> bool:
        results = []
        for provider in (self._tracer_provider, self._meter_provider):
            flush = getattr(provider, "force_flush", None)
            if flush is not None:
                try:
                    results.append(bool(flush(timeout_millis=timeout_millis)))
                except Exception:  # noqa: BLE001 - telemetry cannot fail the request
                    results.append(False)
        return all(results) if results else True

    def shutdown(self) -> None:
        for provider in (self._tracer_provider, self._meter_provider):
            shutdown = getattr(provider, "shutdown", None)
            if shutdown is not None:
                try:
                    shutdown()
                except Exception:  # noqa: BLE001, S110 - telemetry cannot fail shutdown
                    pass


class ObservabilityInitializer:
    """Create isolated providers; exporters are opt-in and backend-neutral."""

    @staticmethod
    def init(settings: BootstrapSettings) -> ObservabilityProvider:
        global _GLOBAL_PROVIDER_CONFIGURED

        resource = Resource.create(
            {
                "service.name": settings.service_name,
                "service.version": settings.service_version,
                "service.instance.id": settings.service_instance_id,
                "deployment.environment.name": settings.deployment_environment,
            }
        )
        sampler = ParentBased(TraceIdRatioBased(settings.otel_trace_sample_ratio))
        tracer_provider = TracerProvider(resource=resource, sampler=sampler)
        if settings.span_file_exporter:
            from usagi_agent.observability.file_logging import default_log_root

            configured = Path(settings.log_dir).expanduser() if settings.log_dir else None
            root = (
                configured.resolve() if configured and configured.is_absolute()
                else (default_log_root().parent / configured).resolve() if configured
                else default_log_root()
            )
            tracer_provider.add_span_processor(
                SimpleSpanProcessor(JsonlSpanExporter(root / "agent-framework" / "spans"))
            )
        readers: list[Any] = []
        exporter = "otlp" if settings.otel_endpoint else settings.otel_exporter

        if exporter == "otlp":
            from opentelemetry.exporter.otlp.proto.http.metric_exporter import (
                OTLPMetricExporter,
            )
            from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
                OTLPSpanExporter,
            )
            from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
            from opentelemetry.sdk.trace.export import BatchSpanProcessor

            base = settings.otel_endpoint or "http://localhost:4318"
            tracer_provider.add_span_processor(
                BatchSpanProcessor(
                    OTLPSpanExporter(endpoint=_signal_endpoint(base, "traces"))
                )
            )
            readers.append(
                PeriodicExportingMetricReader(
                    OTLPMetricExporter(endpoint=_signal_endpoint(base, "metrics")),
                    export_interval_millis=settings.otel_metric_export_interval_millis,
                )
            )
        elif exporter == "console":
            from opentelemetry.sdk.metrics.export import (
                ConsoleMetricExporter,
                PeriodicExportingMetricReader,
            )
            from opentelemetry.sdk.trace.export import (
                ConsoleSpanExporter,
            )

            tracer_provider.add_span_processor(
                SimpleSpanProcessor(ConsoleSpanExporter())
            )
            readers.append(
                PeriodicExportingMetricReader(
                    ConsoleMetricExporter(),
                    export_interval_millis=settings.otel_metric_export_interval_millis,
                )
            )

        meter_provider = MeterProvider(resource=resource, metric_readers=readers)
        # Auto-instrumented HTTP/database clients use the process-global providers.
        # Keep no-export test runtimes isolated and configure globals once for a real
        # telemetry-enabled service process.
        if exporter != "none":
            with _GLOBAL_PROVIDER_LOCK:
                if not _GLOBAL_PROVIDER_CONFIGURED:
                    trace.set_tracer_provider(tracer_provider)
                    metrics.set_meter_provider(meter_provider)
                    _GLOBAL_PROVIDER_CONFIGURED = True
        return ObservabilityProvider(
            tracer=tracer_provider.get_tracer("usagi-agent", settings.service_version),
            meter=meter_provider.get_meter("usagi-agent", settings.service_version),
            resource=resource,
            tracer_provider=tracer_provider,
            meter_provider=meter_provider,
        )


@contextmanager
def operation(
    provider: ObservabilityProvider, name: str, **attributes: Any
) -> Iterator[TelemetryOperation]:
    """Trace and measure one operation, recording exceptions with a safe error type."""
    if name not in SPAN_NAMES:
        raise ValueError(f"unstable OpenTelemetry span name: {name!r}")
    attrs = _scrub_attributes(dict(attributes))
    metric_attrs = {"usagi.operation.type": name, **attrs}
    started = time.perf_counter()
    provider.operation_active.add(1, metric_attrs)
    with provider.tracer.start_as_current_span(
        name,
        attributes=attrs,
        record_exception=False,
        set_status_on_exception=False,
    ) as current_span:
        handle = TelemetryOperation(current_span)
        try:
            yield handle
        except BaseException as exc:
            handle.set_outcome("error")
            handle.set_attribute("error.type", type(exc).__name__)
            raise
        finally:
            duration_ms = (time.perf_counter() - started) * 1000
            completed_attrs = {
                **metric_attrs,
                "usagi.operation.outcome": handle.outcome,
            }
            provider.operation_active.add(-1, metric_attrs)
            provider.operation_count.add(1, completed_attrs)
            provider.operation_duration.record(duration_ms, completed_attrs)


span = operation  # compatibility name used by existing integrations


def current_trace_context() -> dict[str, str]:
    """Return IDs suitable for structured-log correlation; empty outside a valid span."""
    context = trace.get_current_span().get_span_context()
    if not context.is_valid:
        return {}
    return {
        "trace_id": f"{context.trace_id:032x}",
        "span_id": f"{context.span_id:016x}",
    }


def metric_counter(provider: ObservabilityProvider, name: str, unit: str = "1"):
    return provider.meter.create_counter(name=name, unit=unit, description=name)


def metric_histogram(provider: ObservabilityProvider, name: str, unit: str = "ms"):
    return provider.meter.create_histogram(name=name, unit=unit, description=name)


def metric_up_down_counter(provider: ObservabilityProvider, name: str, unit: str = "1"):
    return provider.meter.create_up_down_counter(name=name, unit=unit, description=name)
