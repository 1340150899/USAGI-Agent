# USAGI HTTP Server

应用组合入口：注册 Agent 与小红书 MCP，接收两端微信输入，执行持久化任务并投递结果。完整架构见 [接入说明](../../docs/application-integration.md)。

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

先构建、登录并启动仓库内置的[小红书 MCP](../xiaohongshu-mcp/README.md)：

```sh
cd apps/xiaohongshu-mcp
go build -o build/xiaohongshu-mcp .
go build -o build/login ./cmd/login
./build/login
./build/xiaohongshu-mcp -headless=false -port=:18060
```

MCP 在单独终端/服务运行；随后在仓库根目录启动：

```sh
python -m usagi_httpserver --config apps/httpserver/config.json
```

### 本地查看 OpenTelemetry

示例配置默认使用 `otel_exporter: "console"`，不需要安装 Collector 或 Grafana。Trace 在对应操作完成后直接输出到启动终端，Metric 按 `otel_metric_export_interval_millis` 周期输出。需要关闭时将 `otel_exporter` 改为 `"none"`；改为 `"otlp"` 并配置 `otel_endpoint` 则可发送到外部 Collector。详细字段和指标清单见 [OpenTelemetry 监控](../../docs/open-telemetry.md)。

仅测试聊天可将 `xhs_url` 设为 `null`。HTTP 默认本机 8080，Node 默认 8090，MCP 默认 18060。Windows 跨机器访问使用 TLS 反向代理或受信任隧道；Node 和 MCP 保持内网。MCP 与 HTTP 必须共享 media 绝对路径和读取权限；容器部署时挂载同一目录。浏览器登录需部署账号实际完成，且内置 MCP 禁止无头模式。

`model_profile` 默认为 `chat`（DeepSeek V4 Flash），读取
`DEEPSEEK_API_KEY`。使用项目已有的 GLM Coding Plan 资源时设为
`coding_plan`，走 Responses 接口并读取 `GLM_API_KEY`。

`tool_specs.py` 是工具治理配置入口。所有工具默认需要框架审批，包括 `xhs_publish_content`。变更后重启以重新注册。

wxauto 素材进入三态候选池。收到新素材后，只有连续 10 秒没有更新且未消费窗口中至少有一张图片时，HTTP Server 才将窗口标记为 `selected`，并使用独立会话直接调用通用 `research_writer` 场景。第一轮 Agent 只判断素材、生成完整帖子并询问是否发布，不调用发布工具；用户引用该消息回复后会进入同一个 Session，Agent 再判断是否发起 `xhs_publish_content`。工具调用还需要 Runtime 的第二次审批，通过后才真正发布。发布成功转为 `consumed`，素材不足、用户未同意或工具审批被拒绝转回 `unconsumed`；退回后没有新消息不会重复调用 Agent，必须收到新消息并再次静默 10 秒。外部发布结果 unknown 时保持 `selected` 等待人工核验。

自动链路的草稿消息以“您的小红书运营助手：”开头，与普通消息区分；该前缀只用于聊天标识，Agent 不得把它带进发布工具参数。草稿（含用户要求修改后的修订稿）会在 answer 末尾用“配图：第1张、第3张”标明选用的图片，会话图片按接收顺序累计编号；HTTP Server 解析该标注后把对应图片随投递 payload 的 `media_ids` 一并发给 Adapter。首发草稿未带标注时回退发送全部窗口图片，保证用户总能看到候选图；会话内后续回复只有明确标注配图时才带图。素材不足时不再向用户广播说明，窗口直接静默退回候选池；仅系统异常（如素材处理失败）仍会通知。图片回传要求 weixin binding 与 wxauto binding 使用同一 principal，否则 Adapter 下载素材图会 404 导致投递 partial。

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

会话和工具审批均由 USAGI Runtime 处理；HTTP 层根据 uid 或引用消息的 message_id 解析 session_id 并透传消息。小红书草稿确认是普通的连续 Agent 对话，用户可以同意、拒绝或提出修改；Agent 决定调用发布工具后，Runtime 再进行独立的工具审批。HTTP 超时后先查询原 task/run，使用原幂等键重试消息请求，不换新键重新发布。

部署模板见 [systemd](../../deploy/systemd/usagi-http.service)。只启动一个进程，不使用 uvicorn 多 worker。数据目录只授权服务账号读取。
