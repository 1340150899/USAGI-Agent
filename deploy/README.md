# 部署模板

先按照各应用 README 安装依赖、生成私有配置并完成账号登录，再使用模板。模板不会自动创建服务账号、填入密钥或操作真实微信。

Linux 模板假定代码位于 `/opt/usagi`，服务用户为 `usagi`，Python 环境为 `/opt/usagi/.venv`，Node/npm 安装在 `/usr/bin`。按实际安装路径修改。HTTP 与 MCP 使用同一用户和媒体目录；浏览器按 xhs-mcp 要求先启动并登录，无图形桌面时需先配置可用的浏览器运行环境。

将环境变量放到 `/etc/usagi/application.env`，文件只允许服务用户或 root 读取，内容格式：

```text
GLM_API_KEY=实际模型密钥
USAGI_HTTP_TOKEN=独立随机token
USAGI_WEIXIN_TOKEN=独立随机token
USAGI_WXAUTO_TOKEN=独立随机token
USAGI_ADAPTER_TOKEN=独立随机token
USAGI_RESUME_KEY=重启后保持不变的随机密钥
```

配置就绪后，由部署管理员执行：

```sh
sudo cp deploy/systemd/usagi-*.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now usagi-xhs usagi-weixin usagi-http
sudo systemctl status usagi-xhs usagi-weixin usagi-http
journalctl -u usagi-http -n 100
```

检查 `/health/live` 的 ready，以及认证后的 `/v1/adapters/status` 和 `/v1/notifications`。微信重新扫码前停止 Node 服务，使用相同运行用户和配置执行 login，然后再启动。

Windows 使用交互用户的登录计划任务：

```powershell
.\deploy\windows\register-edge.ps1 -Repository 'E:\learning\USAGI-Agent' -Python 'E:\learning\USAGI-Agent\.venv-edge\Scripts\python.exe'
```

运行前在该用户环境中设置持久 token，确认微信已登录并且采集端手动运行正常。任务在下次用户登录后启动，不会让 wxauto 在未登录桌面里工作。
