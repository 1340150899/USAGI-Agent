# 日志系统

默认日志根目录是仓库根目录的 `log`，也可通过配置项 `log_dir`（HTTP server、adapter）或环境变量 `USAGI_LOG_DIR` 覆盖。目录如下：

```text
log/
  agent-framework/
    2026-09-13.info.log
    2026-09-13.error.log
    spans/2026-09-13.spans.jsonl
  http-server/2026-09-13.info.log
  adapter-server/2026-09-13.info.log
  wechat-edge/2026-09-13.info.log
```

每个等级使用独立文件：`debug`、`info`、`warning`、`error`、`critical`（adapter 没有使用到的等级不会产生空文件）。文件在本地日期变化时自动切换。

Python 服务日志包含 logger 名称以及调用位置，格式为 `logger filename.py:line message`，便于从日志直接定位源码。

span 文件是 JSONL，一行一个已完成 span。解析完整链路：

```powershell
python scripts/parse_spans.py
python scripts/parse_spans.py --trace-id <trace_id>
python scripts/parse_spans.py --latest 5
python scripts/parse_spans.py --trace-id <trace_id> --show-ids
python scripts/parse_spans.py --json
```

树形输出不将 span 名称映射为业务标签，也不推断状态或轮次。`name`、`status`、`duration_ms` 和 `attributes` 按 JSONL 源记录输出；层级仅根据 `parent_span_id` 构建。目录输入会读取其中所有 `*.jsonl` 文件，也可以直接传入任意文件名的 JSONL span 文件。

业务日志不打印鉴权 token、AES 密钥值、工具参数、完整消息正文；AES 日志仅记录 `key_found=true/false`。
