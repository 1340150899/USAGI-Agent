# 独立微信 Adapter

Node.js 24+ 进程，通过微信 iLink 接口收发消息，通过 HTTP 接入 USAGI。无需 OpenClaw 宿主、模型或插件运行时。上游 API、扫码和 CDN 源码及 MIT 许可见 [UPSTREAM.md](UPSTREAM.md)。

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

Windows 可用 `npm.cmd`，PowerShell 环境变量语法为 `$env:NAME = 'value'`。扫码建立 Bot 登录或新增用户绑定；登录 token 仅保存在数据目录数据库中。之后 `npm start`，新用户主动向机器人发第一条消息即可建立回复 context token。

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
