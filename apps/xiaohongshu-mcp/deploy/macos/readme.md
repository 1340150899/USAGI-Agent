# macOS 后台运行

本目录提供一个 LaunchAgent 模板，用于在当前 macOS 用户会话中启动 xiaohongshu-mcp。

## 前提

先准备可执行文件，并完成一次登录：

```bash
cd /path/to/xiaohongshu-mcp
go build -o build/xiaohongshu-mcp .
go build -o build/login ./cmd/login
./login.sh
```

确认 `cookies.json` 已生成，且主程序可以正常启动。

## 配置 LaunchAgent

复制模板：

```bash
mkdir -p ~/Library/LaunchAgents
cp deploy/macos/xhsmcp.plist ~/Library/LaunchAgents/xhsmcp.plist
```

编辑 `~/Library/LaunchAgents/xhsmcp.plist`：

- 将 `{二进制路径}` 替换为 `build/xiaohongshu-mcp` 的绝对路径。
- 将 `{工作路径}` 替换为仓库或 Cookies 所在目录的绝对路径。
- 如需登录后自动启动，将 `RunAtLoad` 改为 `true`。
- 如需进程退出后自动重启，将 `KeepAlive` 改为 `true`。
- 默认日志写入 `/tmp/xhsmcp.log` 和 `/tmp/xhsmcp.err`。

检查 plist：

```bash
plutil -lint ~/Library/LaunchAgents/xhsmcp.plist
```

## 安装与启动

首次安装：

```bash
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/xhsmcp.plist
```

手动启动或重启：

```bash
launchctl kickstart -k gui/$(id -u)/xhsmcp
```

查看状态：

```bash
launchctl print gui/$(id -u)/xhsmcp
curl http://127.0.0.1:18060/health
```

停止：

```bash
launchctl kill SIGTERM gui/$(id -u)/xhsmcp
```

查看日志：

```bash
tail -f /tmp/xhsmcp.log /tmp/xhsmcp.err
```

## 更新配置

修改 plist 后，先卸载再重新安装：

```bash
launchctl bootout gui/$(id -u)/xhsmcp
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/xhsmcp.plist
```

仅替换二进制时，替换完成后执行：

```bash
launchctl kickstart -k gui/$(id -u)/xhsmcp
```

## 卸载

```bash
launchctl bootout gui/$(id -u)/xhsmcp
rm ~/Library/LaunchAgents/xhsmcp.plist
```

## Fish 辅助函数

`xhsmcp.fish` 是可选的函数模板。使用前请先检查其中的命令和服务名，再复制到 Fish 配置目录。LaunchAgent 的标准管理方式仍是上面的 `launchctl` 命令。

返回项目总览：[README.md](../../README.md)。
