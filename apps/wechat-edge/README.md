# Windows 微信素材采集

在已登录 Windows 用户的交互桌面运行。wxauto 操作桌面 UI，因此使用登录计划任务，不能作为 Session 0 后台 Windows Service。

在仓库根目录：

```powershell
py -3.11 -m venv .venv-edge
.venv-edge\Scripts\python.exe -m pip install -e apps/wechat-edge
Copy-Item apps/wechat-edge/config.example.json apps/wechat-edge/config.json
$env:USAGI_WXAUTO_TOKEN = '与 Linux 绑定一致的至少24字符 token'
.venv-edge\Scripts\python.exe -m wechat_edge.main --config apps/wechat-edge/config.json
```

另行安装与桌面微信版本匹配的 wxauto 包，将 `wxauto_module` 设为 `wxauto`、`wxauto4` 或 `wxautox4`。实现使用 `WeChat`、`ChatWith`、`GetAllMessage`、图片 `download(dir_path=...)`，不依赖付费监听接口。当前 wxauto4 文档列出的 Python 范围为 3.9–3.12；建议使用 Python 3.11，并按 [上游文档](https://docs.wxauto.org/llms-full.txt) 核对微信版本。开发环境 Python 3.14 仅验证队列逻辑，不代表能驱动桌面。

配置联系人白名单、本人显示名、account/self/contact/conversation 引用和 Linux HTTPS 地址；引用与 HTTP bindings 一致。当前支持一对一聊天，群聊需要逐发送者绑定，不能共用一个 contact ref。

首次观察只建立内存基线，不上传已有聊天历史。随后按可见消息窗口重叠识别新增消息；Edge 不写 SQLite，待发送事件仅保存在进程内存中，重启后重新建立基线。日志只记录抓到消息和成功发送消息的时间，不记录正文、图片或联系人信息。图片仍会临时复制到内容寻址文件后上传。重复锚点歧义或窗口脱节会暂停该会话并上报 gap，无法保证找回进程退出或长时间离线期间的历史。

gap 是素材边界：断点及其之前的不确定范围不会与后续素材拼接；重新建立连续观察后，断点之后的新素材可以形成新的滑动窗口。需要恢复断点以前的素材时，仍应人工核对缺失范围并调用 HTTP clear-gap，不要通过清空数据库重发历史消息。

[登录任务脚本](../../deploy/windows/register-edge.ps1) 可在部署用户下手动运行，注册交互登录任务，不存储 Windows 密码。token 需在该用户环境中持久配置，计划任务启动时可见。
