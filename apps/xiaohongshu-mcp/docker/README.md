# Docker 部署

本目录提供可直接启动 xiaohongshu-mcp 的 Compose 配置。镜像内置 CloakBrowser Chromium，服务默认监听 `18060`。

## 目录说明

| 路径 | 容器路径 | 用途 |
| --- | --- | --- |
| `./data` | `/app/data` | Cookies、浏览器配置和缓存 |
| `./images` | `/app/images` | 发布时需要访问的本地图片 |

`data/` 和 `images/` 已被 Git 忽略。不要提交 Cookies。

## 启动

在仓库根目录执行：

```bash
cd docker
mkdir -p data images
docker compose pull
docker compose up -d
```

查看状态和日志：

```bash
docker compose ps
docker compose logs -f xiaohongshu-mcp
curl http://127.0.0.1:18060/health
```

默认镜像为：

```text
xpzouying/xiaohongshu-mcp:latest
```

Linux ARM64 可将 `docker-compose.yml` 中的镜像改为：

```text
xpzouying/xiaohongshu-mcp:latest-arm64
```

国内网络也可以使用 Compose 文件中预留的阿里云镜像地址。

## 首次登录

容器镜像不需要单独运行登录程序。启动服务后，在任意 MCP 客户端中连接：

```text
http://127.0.0.1:18060/mcp
```

然后依次调用：

1. `check_login_status`
2. `get_login_qrcode`
3. 使用小红书 App 扫码
4. 再次调用 `check_login_status`

也可以运行 MCP Inspector：

```bash
npx @modelcontextprotocol/inspector
```

在 Inspector 中选择 Streamable HTTP，并连接上述 MCP 地址。

## 发布本机图片

容器无法直接读取宿主机任意路径。先将图片放入当前目录的 `images/`：

```bash
cp /path/to/photo.jpg ./images/
```

调用 `publish_content` 时使用容器路径：

```text
/app/images/photo.jpg
```

HTTP/HTTPS 图片 URL 不需要复制到该目录。

## 代理

需要浏览器代理时，在 `docker-compose.yml` 的 `environment` 中增加：

```yaml
environment:
  - XHS_PROXY=http://user:password@proxy-host:port
```

支持 HTTP、HTTPS 和 SOCKS5 代理。程序日志会隐藏代理认证信息。

更新后重建容器：

```bash
docker compose up -d --force-recreate
```

## 更新、停止与清理

```bash
docker compose pull
docker compose up -d
docker compose stop
docker compose down
```

`docker compose down` 不会删除绑定挂载的 `data/` 和 `images/`。如果手动删除 `data/`，登录状态和浏览器数据也会丢失。

## 从源码构建镜像

AMD64：

```bash
docker build -t xiaohongshu-mcp:local .
```

ARM64：

```bash
docker build -f Dockerfile.arm64 -t xiaohongshu-mcp:local-arm64 .
```

如使用自建镜像，请同步修改 `docker/docker-compose.yml` 的 `image` 字段。

## 网络与安全

- MCP 和 REST API 没有内置鉴权，只应在可信网络中使用。
- 客户端也运行在 Docker Desktop 时，可用 `http://host.docker.internal:18060/mcp` 访问宿主机服务。
- Linux 容器若要使用 `host.docker.internal`，需要配置 `host-gateway`，或使用双方可达的 Docker 网络地址。
- 远程访问时应增加防火墙、反向代理鉴权和 TLS。
- 同一个小红书账号不要同时保留多个网页端登录会话。

返回项目总览：[README.md](../README.md)。
