from decimal import Decimal

import pytest
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import InMemoryMetricReader
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from usagi_agent.observability import current_trace_context, operation
from usagi_agent.observability.telemetry import ObservabilityProvider
from usagi_agent.types.action import ToolObservation
from usagi_agent.types.model import ModelUsage


def _provider():
    resource = Resource.create({"service.name": "telemetry-test"})
    span_exporter = InMemorySpanExporter()
    tracer_provider = TracerProvider(resource=resource)
    tracer_provider.add_span_processor(SimpleSpanProcessor(span_exporter))
    metric_reader = InMemoryMetricReader()
    meter_provider = MeterProvider(resource=resource, metric_readers=[metric_reader])
    provider = ObservabilityProvider(
        tracer=tracer_provider.get_tracer("test"),
        meter=meter_provider.get_meter("test"),
        resource=resource,
        tracer_provider=tracer_provider,
        meter_provider=meter_provider,
    )
    return provider, span_exporter, metric_reader


def _metrics(reader: InMemoryMetricReader):
    data = reader.get_metrics_data()
    assert data is not None
    return {
        metric.name: metric
        for resource_metrics in data.resource_metrics
        for scope_metrics in resource_metrics.scope_metrics
        for metric in scope_metrics.metrics
    }


def test_operations_form_a_hierarchy_and_scrub_sensitive_attributes():
    provider, exporter, reader = _provider()
    try:
        with operation(
            provider,
            "workflow.run",
            run_id="secret-run",
            **{"usagi.scenario.name": "demo"},
        ):
            assert set(current_trace_context()) == {"trace_id", "span_id"}
            with operation(
                provider,
                "pipeline.stage",
                artifact_ref="secret-artifact",
                **{"usagi.pipeline.stage.name": "model"},
            ):
                pass

        spans = exporter.get_finished_spans()
        child, parent = spans
        assert child.parent is not None
        assert child.parent.span_id == parent.context.span_id
        assert "run_id" not in parent.attributes
        assert "artifact_ref" not in child.attributes
        assert child.attributes["usagi.pipeline.stage.name"] == "model"

        available = _metrics(reader)
        assert {"usagi.operation.count", "usagi.operation.duration"} <= available.keys()
    finally:
        provider.shutdown()


def test_error_model_tool_and_policy_measurements_are_recorded():
    provider, exporter, reader = _provider()
    try:
        with pytest.raises(ValueError), operation(provider, "rule.execute"):
            raise ValueError("payload must not be copied into telemetry")
        provider.record_model_usage(
            ModelUsage(input_tokens=12, output_tokens=3, cost=Decimal("0.004")),
            agent="writer",
            model="test-model",
        )
        provider.record_tool(ToolObservation(tool_name="search", status="failed"))
        provider.record_policy(action="tool.execute", effect="deny")

        span = exporter.get_finished_spans()[0]
        assert span.status.status_code.name == "ERROR"
        assert span.attributes["error.type"] == "ValueError"
        assert "payload must not be copied into telemetry" not in str(span.attributes)

        available = _metrics(reader)
        assert {
            "usagi.model.token.usage",
            "usagi.model.cost",
            "usagi.tool.execution.count",
            "usagi.policy.decision.count",
        } <= available.keys()
    finally:
        provider.shutdown()
