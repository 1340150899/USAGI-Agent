# Windows 微信素材采集

在已登录 Windows 用户的交互桌面运行（微信 4.x 已登录）。消息采集直接读取微信本地加密数据库（[wechatauto-replica](https://github.com/fanyuantaier/wechatauto-replica) 的 SQLCipher 解密 + `Listener` 轮询），不操作 UI；只有当一张图片本地仅有缩略图（`<md5>_t.dat`）时，才通过 UI 自动化点击图片消息触发微信下载原图（`download_image_original`）。UI 自动化仍需要交互桌面，因此使用登录计划任务，不能作为 Session 0 后台 Windows Service。

在仓库根目录：

```powershell
py -3.11 -m venv .venv-edge
.venv-edge\Scripts\python.exe -m pip install -e apps/wechat-edge
Copy-Item apps/wechat-edge/config.example.json apps/wechat-edge/config.json
$env:USAGI_WXAUTO_TOKEN = '与 Linux 绑定一致的至少24字符 token'
.venv-edge\Scripts\python.exe -m wechat_edge.main --config apps/wechat-edge/config.json
```

- 微信必须保持登录：数据库密钥从 Weixin.exe 进程内存提取（只读扫描），首次提取后缓存于临时目录，重新登录会自动重提。
- Python 必须是 64 位（读取 64 位微信进程内存）；wechatauto-replica 官方支持 3.9–3.12，建议 3.11。
- ffmpeg 由依赖 `imageio-ffmpeg` 自带（wxgf/HEVC 图片转 jpg），无需单独安装。依赖会拉入 opencv-python、pyautogui、winsdk 等，venv 体积较大属预期。
- 首次运行需探测图片 AES 密钥：优先由 cfgDword 离线派生（无需人工操作）；派生失败时需要在微信里人工点开任意一张图片，进程会在 120 秒内自动捕获密钥（期间状态为 `detecting_image_key`）。密钥命中后持久化，后续免扫。

## 图片三级获取

`EdgeMediaDownloader.download()` 按成本从低到高：

1. 本地 `<md5>.dat`（已落地的普通图，零 UI）；
2. 本地 `<md5>_h.dat`（原图，零 UI）；
3. 仅有 `<md5>_t.dat`（缩略图）→ `download_image_original()` 模拟点击图片消息并按「图片原始大小」，等待微信下载原图后解密。

第 3 级是唯一的 UI 路径：会切换会话窗口并使用鼠标点击（约 6–10 秒/张），期间不要操作电脑；锁屏/窗口锁定时点击失败，自动回退缩略图（`_thumb`）上传。所有产物经魔数校验后以内容寻址副本上传，`.wxgf` 等服务端不接受的格式自动降级。全链失败时上传 `[图片采集失败]` 文本占位，heartbeat 中 `failed_images` 计数可见。

## 会话与去重

配置联系人白名单（`conversations[].name` 为微信显示名，建议首次启动后按日志提示把解析出的 wxid 固化为 `username` 字段）、account/self/contact/conversation 引用和 Linux HTTPS 地址；引用与 HTTP bindings 一致。群聊需在 `sender_map` 中为成员 wxid 绑定 sender_ref，未映射成员回退到会话级 sender_ref；服务端 bindings 的 `sender_refs` 必须包含 `self_ref`（勿删 `owner-desktop`）。

去重基于数据库 `sort_seq` 游标（Listener watermark）：注册监听时以会话最新消息为基线，只推送之后的新消息。Edge 不写 SQLite，待发送事件仅保存在进程内存中；重启后重新建立基线，不补发停机期间的历史。日志只记录抓到消息和成功发送消息的时间（以及图片获取层级），不记录正文、图片或联系人信息。图片仍会临时复制到内容寻址文件后上传。

微信退出登录时数据库仍可读（消息停止更新），watchdog 通过 30 秒探活更新 `_status`，连续 10 次失败自动退出交由计划任务重启。

[登录任务脚本](../../deploy/windows/register-edge.ps1) 可在部署用户下手动运行，注册交互登录任务，不存储 Windows 密码。token 需在该用户环境中持久配置，计划任务启动时可见。
