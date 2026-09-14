# 独立微信 Adapter

Node.js 24+ 进程，通过微信 iLink 接口收发消息，通过 HTTP 接入 USAGI。无需 OpenClaw 宿主、模型或插件运行时。上游 API、扫码和 CDN 源码及 MIT 许可见 [UPSTREAM.md](UPSTREAM.md)。

## 项目来源

微信传输、扫码登录和 CDN 媒体处理代码选自腾讯开源项目 [Tencent/openclaw-weixin](https://github.com/Tencent/openclaw-weixin)，基准提交为 `2f4dcbf57bedf0e17e266fdedcf0cd2dd141b7d3`。本目录替换了账号存储、日志和应用接入逻辑，不需要安装或启动 OpenClaw。

## 安装与启动

在此目录执行：

```sh
npm ci
npm run build
cp config.example.json config.json
export USAGI_WEIXIN_CONFIG=/opt/usagi/apps/weixin-adapter/config.json
export USAGI_WEIXIN_TOKEN='与 HTTP 配置一致的入站 token'
export USAGI_ADAPTER_TOKEN='与 HTTP 配置一致的投递 token'
npm run login
```

扫码建立 Bot 登录或新增用户绑定；登录 token 仅保存在数据目录数据库中。之后执行 `npm start`，新用户主动向机器人发第一条消息即可建立回复 context token。

## 服务配置

| 字段 | 作用 | 示例/默认值 |
|---|---|---|
| `log_dir` | 日志目录 | `log` |
| `data_dir` | 登录凭据、游标、路由和投递记录目录 | `.usagi/weixin` |
| `account_ref` | 对外暴露的微信账号引用 | `weixin-main` |
| `database_environment` | 使用 `adapter-dev.db` 或 `adapter-debug.db` | `debug` |
| `server_url` | USAGI HTTP Server 地址 | `http://127.0.0.1:8080` |
| `api_token_env` | 保护 Adapter internal API 的 token 环境变量名 | `USAGI_ADAPTER_TOKEN` |
| `server_token_env` | 调用 HTTP Server 入站接口的 token 环境变量名 | `USAGI_WEIXIN_TOKEN` |
| `host` / `port` | Adapter 监听地址 | `127.0.0.1:8090` |
| `allowed_peers` | 可选的微信用户 ID 白名单 | 未配置时接受全部扫码用户 |
| `base_url` / `cdn_base_url` | 可选上游 API/CDN 地址 | 使用腾讯默认地址 |

`USAGI_WEIXIN_CONFIG` 用来指定配置文件路径，未设置时读取当前目录的 `config.json`。两个 token 环境变量必须已设置，Adapter API token 至少 24 字符。`config.json` 和数据目录包含私有信息，不要提交到 Git。

同一个 Adapter 可以服务多个扫码微信用户。iLink 会为每个扫码绑定返回独立的 Bot 连接凭据，Adapter 将这些凭据集中保存并并行轮询，对外仍表现为一个 Bot 服务。停止常驻 Adapter 后运行 `npm run bind`，让新用户扫码，再重启服务；新用户首次发消息时，Adapter 会自动保存 UID、回复路由、context token 及其连接归属，HTTP 无需预配置 sender/conversation refs。`allowed_peers` 是可选的显式限制名单；未配置时接受腾讯推送的所有扫码用户。

长轮询批次与游标先原子保存，HTTP 接收成功再确认。CDN 图片解密后上传 Linux。投递按 delivery_id 去重，进程在发送中退出标记 unknown，不自动重发。context token 缺失时投递 failed；微信限制或上下文过期需要实机核查，不能保证任意时间主动推送。

| 接口 | 用途 |
|---|---|
| GET /health/live | 登录及最近轮询状态 |
| POST /internal/messages | `{delivery_id,reply_route_ref,text,media_ids?,media_id?}`，等待发送完成并返回消息 ID |
| POST /internal/broadcasts | `{delivery_id,text,media_ids?,media_id?}`，等待向所有已学习路由发送完成 |
| GET /internal/messages/{delivery_id} | 幂等重试或诊断时读取已完成的发送结果 |
| GET /internal/accounts | 账号引用与登录状态 |

internal 接口使用 `USAGI_ADAPTER_TOKEN`，默认本机 8090。备份数据库包含登录 token，只授权服务用户读取。修改源码后执行 `npm run build` 和 `npm test`。

同一数据目录只允许一个进程，重新登录前先停止服务。异常退出的锁会在确认原 PID 已不存在后清理；不确定时保持停止，人工核查。
