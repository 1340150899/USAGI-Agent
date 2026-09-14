# xiaohongshu-mcp

通过真实浏览器操作小红书的本地 MCP 服务，同时提供 Streamable HTTP MCP 与 REST API。

[English](./README_EN.md) · [Docker 指南](./docker/README.md) · [HTTP API](./docs/API.md) · [集成示例](./examples/README.md)

## 项目来源

本目录基于开源项目 [xpzouying/xiaohongshu-mcp](https://github.com/xpzouying/xiaohongshu-mcp) 修改，保留原项目的 MCP、浏览器自动化和 REST API 能力，并加入当前仓库需要的交互与发布改动。上游项目的许可和贡献记录以原仓库为准。

## 项目定位

本项目把登录、内容发布、搜索和互动能力封装为 MCP 工具。客户端连接后，可以通过自然语言调用这些工具；服务端使用浏览器完成实际操作，而不是调用未公开的平台接口。

当前发布、检索与互动流程共享一套更接近人工操作的交互层：

- 鼠标沿带轻微弧度的轨迹移动后点击
- 在可见、可用的页面元素上操作
- 使用真实鼠标滚轮逐段滚动页面并触发评论懒加载，不注入脚本跳转滚动位置
- 标题、正文和标签逐字输入，并加入随机停顿
- 内容检索从发现页点击搜索框、逐字输入关键词、点击搜索按钮，并在可见面板中选择筛选项
- 评论与回复逐字输入；点赞、收藏、展开回复和侧边栏主页入口均通过可见控件点击
- 自动处理图文发布、可见范围、定时发布和商品绑定
- `is_original=true` 时自动开启原创声明、勾选确认项并提交

首页 Feed、详情数据和用户资料等只读结果仍从浏览器已经加载的页面状态中提取，不调用未公开的平台接口，也不会为了模拟操作增加无意义点击。

> 这是浏览器自动化项目，页面结构变化可能导致功能失效。请遵守小红书平台规则，仅在你有权操作的账号和内容上使用。

## 功能

### 登录与会话

- 检查登录状态
- 获取二维码并等待 App 扫码
- 删除本地 Cookies，重置登录状态
- 独立的可视化登录程序

### 内容发布

- 发布图文笔记：支持本地绝对路径和 HTTP/HTTPS 图片
- 发布视频笔记：支持单个本地视频文件
- 添加话题标签
- 设置可见范围
- 设置 1 小时至 14 天内的定时发布
- 自动声明原创
- 按关键词或商品 ID 绑定带货商品

### 浏览与互动

- 获取首页 Feed
- 按关键词和筛选条件搜索笔记
- 获取笔记详情与评论
- 获取用户主页
- 发表评论和回复评论
- 点赞、取消点赞、收藏和取消收藏

## 快速开始

### 环境要求

- Go 1.24 或更高版本
- Chrome、Chromium 或兼容浏览器
- 可扫码登录的小红书 App

也可以直接使用 [Docker](#docker) 或从 [Releases](https://github.com/YuriGao/xiaohongshu-mcp/releases) 下载对应平台的程序。

### 从源码运行

```bash
cd apps/xiaohongshu-mcp
go build -o build/xiaohongshu-mcp .
go build -o build/login ./cmd/login
```

首次使用先登录：

```bash
./login.sh
```

登录成功后启动服务：

```bash
./run.sh
```

`run.sh` 默认以有界面模式运行，便于观察真实浏览器操作。服务地址：

- MCP：`http://127.0.0.1:18060/mcp`
- 健康检查：`http://127.0.0.1:18060/health`
- REST API：`http://127.0.0.1:18060/api/v1`

验证服务：

```bash
curl http://127.0.0.1:18060/health
```

项目禁止使用无头浏览器模式；服务必须以有界面模式运行。

直接运行二进制时，可使用 `-headless`、`-bin` 和 `-port` 参数：

```bash
./build/xiaohongshu-mcp -headless=false -port :18060
```

### Docker

```bash
cd docker
mkdir -p data images
docker compose pull
docker compose up -d
docker compose logs -f xiaohongshu-mcp
```

Docker 镜像内置浏览器。Cookies 和浏览器数据保存在 `docker/data/`；需要发布本机图片时，将文件复制到 `docker/images/`，并向工具传入容器路径 `/app/images/文件名`。

首次登录可在 MCP 客户端中调用 `get_login_qrcode`。完整说明见 [Docker README](./docker/README.md)。

## 连接 MCP 客户端

在客户端中新增一个 Streamable HTTP 服务：

```text
名称：xiaohongshu-mcp
地址：http://127.0.0.1:18060/mcp
```

以使用 `mcpServers` 配置格式的客户端为例：

```json
{
  "mcpServers": {
    "xiaohongshu-mcp": {
      "url": "http://127.0.0.1:18060/mcp"
    }
  }
}
```

不同客户端的字段名可能不同，请选择 `Streamable HTTP` 或 `HTTP` 传输类型。仓库内提供了可参考的 [Cursor 配置](./.cursor/mcp.json) 和 [VS Code 配置](./.vscode/mcp.json)。

连接完成后，建议依次执行：

1. 调用 `check_login_status`。
2. 未登录时调用 `get_login_qrcode` 并扫码。
3. 再次检查登录状态。
4. 先用测试内容验证发布，再接入自动化流程。

## MCP 工具

| 工具 | 作用 |
| --- | --- |
| `check_login_status` | 检查当前登录状态 |
| `get_login_qrcode` | 获取登录二维码 |
| `delete_cookies` | 删除 Cookies 并重置登录 |
| `publish_content` | 发布图文笔记 |
| `publish_with_video` | 发布视频笔记 |
| `list_feeds` | 获取首页 Feed |
| `search_feeds` | 搜索笔记并应用筛选条件 |
| `get_feed_detail` | 获取笔记详情和评论 |
| `user_profile` | 获取指定用户主页 |
| `post_comment_to_feed` | 发表评论 |
| `reply_comment_in_feed` | 回复评论 |
| `like_feed` | 点赞或取消点赞 |
| `favorite_feed` | 收藏或取消收藏 |

### 图文发布参数

`publish_content` 的主要参数：

| 参数 | 必填 | 说明 |
| --- | --- | --- |
| `title` | 是 | 标题，按平台限制最多 20 个中文字或英文单词 |
| `content` | 是 | 正文；话题标签通过 `tags` 单独传入 |
| `images` | 是 | 至少一张图片，支持 URL 或本地绝对路径 |
| `tags` | 否 | 话题标签数组 |
| `schedule_at` | 否 | ISO 8601 时间，支持 1 小时至 14 天内 |
| `is_original` | 否 | `true` 时自动完成原创声明确认 |
| `visibility` | 否 | `公开可见`、`仅自己可见` 或 `仅互关好友可见` |
| `products` | 否 | 商品关键词或商品 ID 数组 |

自然语言示例：

```text
发布一篇图文笔记：
标题：周末公园散步
正文：天气很好，记录一下今天的绿色。
图片：/Users/me/Pictures/park.jpg
标签：周末、散步、生活记录
声明原创，并设置为仅自己可见。
```

## 搜索筛选

`search_feeds` 支持以下筛选：

| 字段 | 可选值 |
| --- | --- |
| `sort_by` | `综合`、`最新`、`最多点赞`、`最多评论`、`最多收藏` |
| `note_type` | `不限`、`视频`、`图文` |
| `publish_time` | `不限`、`一天内`、`一周内`、`半年内` |
| `search_scope` | `不限`、`已看过`、`未看过`、`已关注` |
| `location` | `不限`、`同城`、`附近` |

从搜索或 Feed 列表中取得 `feed_id` 和 `xsec_token` 后，才能调用详情、评论、点赞和收藏相关工具。

## 配置

| 环境变量 | 作用 | 默认值 |
| --- | --- | --- |
| `PORT` | `run.sh` 使用的监听地址 | `:18060` |
| `HEADLESS` | 不支持；本项目禁止启用无头模式 | - |
| `BIN_PATH` | 登录脚本和启动脚本使用的浏览器路径 | 自动检测 |
| `ROD_BROWSER_BIN` | 服务程序使用的浏览器路径 | 自动检测或下载 |
| `COOKIES_PATH` | Cookies 文件位置 | 当前目录的 `cookies.json` |
| `XHS_PROXY` | 浏览器代理，支持 HTTP/HTTPS/SOCKS5 | 不使用代理 |

代理示例：

```bash
XHS_PROXY=http://127.0.0.1:7890 ./run.sh
```

如果旧版曾在系统临时目录生成 `cookies.json`，程序会优先继续使用该文件。登录异常时，可调用 `delete_cookies` 或检查实际 Cookies 路径。

## REST API

除 MCP 外，服务还提供 REST API，包括登录、发布、Feed、用户和评论接口。端点与请求示例见 [HTTP API 文档](./docs/API.md)。

## 集成示例

- [Cherry Studio](./examples/cherrystudio/README.md)
- [AnythingLLM](./examples/anythingLLM/readme.md)
- [n8n](./examples/n8n/README.md)
- [macOS LaunchAgent 后台运行](./deploy/macos/readme.md)
- [Windows 指南](./docs/windows_guide.md)

## 安全与使用边界

- 服务本身未提供鉴权，不要直接暴露到公网。
- `publish_*`、评论、点赞、收藏和删除 Cookies 都会改变账号或本地状态。
- 同一账号不要同时登录多个网页端，否则当前 Cookies 可能失效。
- 发布前确认内容版权、账号权限、可见范围和商品绑定结果。
- Cookies 包含登录信息，禁止提交到 Git 或发送给他人。
- 批量操作前先低频验证；平台风控和页面变化不在本项目控制范围内。

## 开发

```bash
gofmt -w .
go vet ./...
go test ./pkg/...
```

`xiaohongshu` 包中包含真实浏览器测试。运行前请阅读测试代码，避免意外访问或操作账号。浏览器集成测试使用：

```bash
go test -tags=integration ./xiaohongshu -run TestName -count=1
```

贡献代码前请阅读 [CONTRIBUTING.md](./CONTRIBUTING.md)。
