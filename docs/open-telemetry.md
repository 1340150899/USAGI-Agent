# OpenTelemetry 监控

USAGI Agent 的 OpenTelemetry 接入默认关闭导出，不影响未配置 Collector 的本地开发；启用后通过 OTLP/HTTP 同时发送 trace 和 metric，并保持后端中立。

## 已接入的监控

Trace 的层级是：

```text
workflow.run
└─ pipeline.stage
   └─ rule.execute
      ├─ model.invoke
      └─ tool.execute
         └─ policy.evaluate
```

span 名保持低基数，具体场景、阶段、Rule、Agent、模型和 Tool 名放在 attribute 中。异常会设置 ERROR 状态并记录异常类型。开始、恢复和取消 Run 都有 workflow span；六个 Pipeline 阶段、所有配置 Rule、模型调用、Tool 执行和 Tool 策略判断都有各自 span。

当前指标如下：

| 指标 | 类型 | 用途 |
|---|---|---|
| `usagi.operation.count` | Counter | 按操作、场景、阶段、结果统计吞吐和成功率 |
| `usagi.operation.duration` | Histogram，ms | Workflow、Stage、Rule、模型、Tool、Policy 的延迟分布 |
| `usagi.operation.active` | UpDownCounter | 当前运行中的操作数，可观察积压与并发 |
| `usagi.model.token.usage` | Counter | 按 Agent、模型和 input/output 统计 token |
| `usagi.model.cost` | Counter，USD | 根据 ModelSpec 单价估算的模型成本 |
| `usagi.tool.execution.count` | Counter | 按 Tool 和 success/denied/failed/unknown 统计结果 |
| `usagi.policy.decision.count` | Counter | Tool 策略 allow/deny 分布 |

由这些数据可以建立吞吐、错误率、P50/P95/P99 延迟、并发量、最慢阶段/Rule、模型 token 与成本、Tool 失败率/超时率、策略拒绝率等 Dashboard 和告警。框架还提供 `current_trace_context()`，供结构化日志附加当前 `trace_id` 和 `span_id`，从日志跳转到完整调用链。

## 启用方式

生产环境推荐把数据发到 OpenTelemetry Collector，再由 Collector 转发到 Tempo/Jaeger、Prometheus/Grafana 或商业平台：

```python
from usagi_agent.registry import BootstrapSettings
from usagi_agent.server import ServiceRuntimeInitializer

runtime = ServiceRuntimeInitializer.init(BootstrapSettings(
    sqlite_path="./usagi.db",
    service_name="usagi-api",
    service_version="0.1.0",
    service_instance_id="api-1",
    deployment_environment="prod",
    otel_exporter="otlp",
    otel_endpoint="http://otel-collector:4318",
    otel_trace_sample_ratio=0.2,
    otel_metric_export_interval_millis=60_000,
))
```

`otel_endpoint` 是 OTLP/HTTP 基础地址，框架会分别追加 `/v1/traces` 和 `/v1/metrics`。只要设置了该地址，就会自动选择 `otlp` exporter。短生命周期进程可在退出前调用 `runtime.observability.force_flush()`；正常的 `runtime.shutdown()` 会关闭并排空 exporter。

本地排查可以设置 `otel_exporter="console"`。默认值 `none` 仍会创建 no-op 导出器和完整埋点，因此业务代码无需根据环境写条件分支。

通过 `apps/httpserver` 启动时，可直接在它的 JSON 配置中启用本地控制台输出：

```json
{
  "otel_exporter": "console",
  "otel_trace_sample_ratio": 1.0,
  "otel_metric_export_interval_millis": 1000,
  "otel_service_name": "usagi-httpserver",
  "service_instance_id": "httpserver-local-1",
  "deployment_environment": "dev"
}
```

不需要配置 `otel_endpoint`。Span 完成后立即写到 HTTP Server 所在终端，Metric 每秒写一次；`otel_exporter` 改回 `none` 即可关闭输出。

## 数据边界

遥测只记录受控的低基数元数据。防御性过滤会拒绝 Run/Thread/Session/Checkpoint/Artifact/主体 ID，以及 Prompt、消息、Tool 参数和输出。原始内容、审计事实、Tool receipt 与可恢复状态继续保存在受访问控制的业务存储中；OpenTelemetry 不是审计库，也不是可靠消息队列。

跨进程部署时应在 HTTP、消息队列或 Worker 边界注入/提取 W3C Trace Context。当前仓库内的异步 Pipeline 会自动继承 Python context；新增外部入口和客户端时，应使用对应的 OpenTelemetry instrumentation 或显式 propagator。
