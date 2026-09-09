# USAGI HTTP Server

应用组合入口：注册 Agent 与小红书 MCP，接收两端微信输入，执行持久化任务，收集工具审批并投递结果。完整架构见 [接入说明](../../docs/application-integration.md)。

## 安装与启动

在仓库根目录，使用 Python 3.11+：

```sh
python -m venv .venv
. .venv/bin/activate
python -m pip install -e './usagi-agent[sqlite,mcp]' -e ./apps/httpserver
cp apps/httpserver/config.example.json apps/httpserver/config.json
```

配置 `GLM_API_KEY`、`USAGI_HTTP_TOKEN`、`USAGI_WEIXIN_TOKEN`、`USAGI_WXAUTO_TOKEN`、`USAGI_ADAPTER_TOKEN`、`USAGI_RESUME_KEY` 环境变量。四种 HTTP token 必须不同，至少 24 字符；恢复密钥至少 32 字符且重启后保持不变。可用 `python -c 'import secrets; print(secrets.token_urlsafe(32))'` 分别生成。

先按 [Node 登录说明](../weixin-adapter/README.md) 扫码连接 Bot。微信 Adapter 凭据与 `account_ref` 是入口信任边界，扫码用户的 sender/conversation 路由在首次消息时动态建立，无需写入 HTTP 配置；wxauto 等桌面采集入口仍使用静态 sender/conversation refs。

先启动并登录小红书 MCP，已有细节见 [xhs-autopost](../xhs-autopost/README.md)：

```sh
npx --yes xhs-mcp@0.8.13 browser
npx --yes xhs-mcp@0.8.13 login --timeout 120
npx --yes xhs-mcp@0.8.13 mcp --mode http --port 3000
```

MCP 在单独终端/服务运行；随后在仓库根目录启动：

```sh
python -m usagi_httpserver --config apps/httpserver/config.json
```

### 本地查看 OpenTelemetry

示例配置默认使用 `otel_exporter: "console"`，不需要安装 Collector 或 Grafana。Trace 在对应操作完成后直接输出到启动终端，Metric 按 `otel_metric_export_interval_millis` 周期输出。需要关闭时将 `otel_exporter` 改为 `"none"`；改为 `"otlp"` 并配置 `otel_endpoint` 则可发送到外部 Collector。详细字段和指标清单见 [OpenTelemetry 监控](../../docs/open-telemetry.md)。

仅测试聊天可将 `xhs_url` 设为 `null`。HTTP 默认本机 8080，Node 默认 8090，MCP 默认 3000。Windows 跨机器访问使用 TLS 反向代理或受信任隧道；Node 和 MCP 保持内网。MCP 与 HTTP 必须共享 media 绝对路径和读取权限；容器部署时挂载同一目录。浏览器登录需部署账号实际完成。

`model_profile` 默认为 `chat`（DeepSeek V4 Flash），读取
`DEEPSEEK_API_KEY`。使用项目已有的 GLM Coding Plan 资源时设为
`coding_plan`，走 Responses 接口并读取 `GLM_API_KEY`。

`tool_specs.py` 是审批配置入口。所有工具默认需要审批，包括查询；只想发布工具审批时，显式将所需查询工具设为 `requires_approval=False`。变更后重启以重新注册。

## API

业务接口使用 `Authorization: Bearer <token>`。adapter 凭据仅允许 ingress、媒体和心跳；控制接口使用用户 API 凭据。Run 与媒体按 principal 校验归属，恢复令牌不返回微信。

| 接口 | 用途 |
|---|---|
| GET /health/live | 进程及 Runtime 就绪 |
| GET /v1/tools | 注册工具及审批开关 |
| POST /v1/sessions | `{query, request_idempotency_key}`，创建会话并返回 202 和 task_id |
| POST /v1/ingress/events | 标准渠道事件，持久化后返回 202 |
| POST /v1/ingress/media | 原始图片字节及正确 Content-Type，最大 20 MiB |
| GET /v1/media/{media_id} | 读取本人媒体 |
| GET /v1/tasks/{task_id} | 异步任务状态与 run_id |
| GET /v1/runs/{run_id} | 本人 Run 状态 |
| GET /v1/notifications | 最近 100 条本人通知、最终答案与投递状态 |
| POST /v1/adapters/heartbeat | 采集/接收状态 |
| GET /v1/adapters/status | 本人绑定渠道最近心跳 |
| POST /v1/conversations/{conversation_ref}/clear-gap | 人工核对后丢弃不完整的待处理素材范围 |

会话和审批均由 USAGI Runtime 处理；HTTP 层只根据 uid 或引用消息的 message_id 解析 session_id 并透传消息。待审批会话只接受 Yes/No（不区分大小写），其他内容由 Runtime 再次返回审批提示。HTTP 超时后先查询原 task/run，使用原幂等键重试消息请求，不换新键重新发布。

部署模板见 [systemd](../../deploy/systemd/usagi-http.service)。只启动一个进程，不使用 uvicorn 多 worker。数据目录只授权服务账号读取。
