# USAGI-Agent

一个基于 LangGraph 工作流引擎的标准、通用、可扩展 Agent Framework；小红书自动发帖是基于该框架实现的首个业务应用。

## 文档

- [通用 Agent Framework 架构](docs/generic-agent-framework.md) — 框架权威设计（§29 工程结构、§7.1 bootstrap 序列等）
- [Agent 技术架构方案](docs/agent-architecture.md) — 小红书业务应用架构
- [原始需求流程图](requirements-flow.jpg)
- [原始 Agent 架构草图](agent-architecture-flow.jpg)

## 工程结构（对应设计 §29）

```
USAGI-Agent/
├─ usagi-agent/                 # 业务无关通用框架（Kernel + Capabilities + Ports）
│  ├─ src/usagi_agent/
│  │  ├─ types/                 # §4.1/§8.1 共享类型（跨模块唯一允许的类型共享面）
│  │  ├─ ports/                 # 稳定 Port 表面（基础设施 + 能力 Protocol）
│  │  ├─ persistence/           # §24 Port 接口 + inmemory/sqlite 实现
│  │  ├─ kernel/                # §10 Runtime/生命周期/预算/取消/fencing（执行根 + initializer）
│  │  ├─ registry/              # §11 静态 RuntimeBundleCatalog + BootstrapSettings + 校验
│  │  ├─ adapters/              # §11.6 AdapterContainer (DI)
│  │  ├─ pipelines/             # §9 Spec + Compiler + AgentLoop + 六 Rule
│  │  ├─ tools/ memory/ rag/ prompts/ policies/   # §21-23 能力
│  │  ├─ erasure/               # §24.5 Erasure/Lineage
│  │  ├─ observability/         # §25 OpenTelemetry
│  │  └─ server/               # 组合根：application_container(init 树根) + bootstrap + server(执行)
│  └─ tests/                    # contract + pipeline 测试
├─ plugins/                     # 可替换插件（§26，首版不实现 manifest 加载）
├─ apps/xhs-autopost/           # 首个业务应用（仅骨架）
└─ examples/structured_agent/   # 业务无关端到端示例：research_writer
```

## 架构硬约束（贯穿全库）

1. **初始化就是初始化**：每个模块 `initializer.py` 只在 Bootstrap 期构造/校验，绝不执行业务；执行件
   （`runtime.py`/`nodes.py`）只在 Run 期被调用。两者不互相 import 执行逻辑。
2. **模块解耦**：模块只允许 import 三类稳定表面 `types.*` / `ports.*` / `api.*`；跨模块连线由
   `server/application_container.py`（组合根）显式注入。
3. **静态 Catalog**：Spec/Adapter/Graph 在 Bootstrap 期构造、校验、编译并装入只读
   `RuntimeBundleCatalog`；Run 期只按 `scenario_key` 取 Bundle，不重新解析 Spec/建 Adapter/编译图（§2.3）。
4. **LangGraph 是唯一执行引擎**（§3.1）：`PipelineCompiler` 把 Spec 编译成 LangGraph StateGraph/subgraph。
5. **State 只存低敏路由字段 + ArtifactRef**（§8.2）：checkpoint 不内嵌领域 payload，由 contract test 强制。

## 初始化树（§7.1）

`server/application_container.py::ApplicationContainer.init` 按依赖顺序调用各模块 Initializer，
**只构造/校验/编译，不含 Run 期执行**：

```
ApplicationContainer.init(settings, scenarios)
 ├─ ObservabilityInitializer.init        → OTel Provider              # §25
 ├─ PersistenceInitializer.init          → InfrastructurePorts (全部 Store)  # §24
 ├─ CapabilityInitializer.init           → AgentManager 内置 live/scripted 模型执行、Memory/Tool/Policy  # §11.5
 ├─ KernelInitializer.init               → Budget/Cancellation/Middleware 执行件壳  # §10
 ├─ CatalogBuilder.build                 → 每 scenario: 六 Rule 装配 + LangGraph 编译 → RuntimeBundle  # §7.1
 ├─ health_check                         → 任一必需 Bundle 不全 → 启动失败  # §7.1 step8
 ├─ ErasureInitializer.init              → ErasureCoordinator        # §24.5
 └─ KernelRuntime(catalog, ports, ...)   # 执行根：只持有，不再 init
Server(runtime, ...)                     # 持 runtime 暴露 start/resume/cancel（纯执行）
```

## 安装与运行

```bash
pip install -e usagi-agent
# 端到端示例（InMemory 后端，两轮：Tool → next_pass → Final → run_completed）
python -m examples.structured_agent.run
# 测试（State 契约 / Bundle 校验 / FencedCheckpointer gate / ThreadControlBinding 唯一 / 六 Rule 流程 / 幂等）
pytest usagi-agent/tests
```

## 范围说明（v1）

- **完整实现（可运行、有 dev 实现）**：类型/Spec/Port、InMemory+SQLite 持久化、FencedCheckpointer
  （含 fencing gate）、Kernel Runtime 启动/取消/查询、PipelineCompiler+不变量、六 Rule+AgentLoop、
  ToolRuntime、MemoryManager、Policy/Guardrail、ArtifactManager、UsageLedger/AuditStore、OTel、
  research_writer 端到端示例与核心 contract test。
- **数据模型+状态机+关键流实现，最深边缘流以 `# TODO(§X)` 标注**：完整 resume/token 签发链路、
  Erasure 完整闭环、ExternalEffect finality/correlation tombstone、AgentTeam Router/Supervisor/Debate、
  Plugin manifest 加载。这些保留接口与状态机骨架，不破坏 init/execute 分离与模块解耦。
