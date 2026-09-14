# USAGI-Agent

USAGI-Agent 是一个基于 LangGraph 的通用 Agent 框架。项目的应用层实现了：采集微信聊天内容，让 Agent 自动整理图片和文字、生成适合分享日常的小红书内容，并通过小红书 MCP 完成发布。

## 项目结构

```text
USAGI-Agent/
├── usagi-agent/                 # 通用 Agent 框架及测试
│   └── src/usagi_agent/
│       ├── agents/              # Agent 与模型配置
│       ├── kernel/              # Run 生命周期、预算、取消与执行控制
│       ├── pipelines/           # 场景流水线、阶段处理器与规则
│       ├── tools/               # 本地工具、MCP 工具和审批
│       ├── memory/              # 会话记忆与长期记忆
│       ├── persistence/         # 内存与 SQLite 持久化
│       ├── observability/       # 日志、Trace 与 Metric
│       └── server/              # Runtime 初始化与服务接口
├── examples/structured_agent/   # 通用框架示例
├── apps/httpserver/             # Agent HTTP 服务和业务编排
├── apps/weixin-adapter/         # 微信 iLink 消息适配服务
├── apps/wechat-edge/            # Windows 微信素材采集服务
├── apps/xiaohongshu-mcp/        # 小红书 MCP/REST 服务
├── deploy/                      # systemd 与 Windows 启动模板
└── plugins/                     # 可扩展组件预留目录
```

## Agent 框架简介

框架以场景配置组织 Agent 工作流。启动时注册模型、工具和场景并编译 LangGraph；运行时由 Pipeline 依次完成上下文构建、模型调用、工具执行、结果处理和会话持久化。工具支持白名单、参数校验、人工审批和 MCP 接入，运行数据可保存到 SQLite，并提供 OpenTelemetry 可观测能力。

`usagi-agent` 不包含微信或小红书等业务概念，具体渠道和业务编排放在 `apps/` 中。

## 启动方式

以下命令在 Linux 环境执行，要求 Python 3.11+。运行最小示例：

```sh
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -e './usagi-agent[dev]'
python -m examples.structured_agent.run
```

运行框架测试：

```sh
. .venv/bin/activate
python -m pytest usagi-agent/tests
```

启动完整应用时，按需依次启动以下服务：

1. [小红书 MCP](apps/xiaohongshu-mcp/README.md)：浏览器登录、内容发布和查询服务。
2. [USAGI HTTP Server](apps/httpserver/README.md)：Agent Runtime、HTTP API 和任务处理。
3. [微信 iLink Adapter](apps/weixin-adapter/README.md)：扫码登录后收发微信消息。
4. [Windows 微信素材采集](apps/wechat-edge/README.md)：与已登录的 Windows 微信客户端搭配使用，采集客户端收到的文字和图片消息；该服务单独使用 PowerShell 启动。

各服务的依赖安装、启动命令和配置字段以对应目录的 README 与 `config.example.json` 为准。
