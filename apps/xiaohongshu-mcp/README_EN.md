# xiaohongshu-mcp

A local MCP service that operates Xiaohongshu/RedNote through a real browser. It exposes both Streamable HTTP MCP and REST APIs.

[中文](./README.md) · [Docker guide](./docker/README.md) · [HTTP API](./docs/API.md) · [Integration examples](./examples/README.md)

## Overview

The project packages login, publishing, discovery, and engagement actions as MCP tools. An MCP client can invoke those tools through natural language while the server performs the operation in a browser instead of relying on an undocumented platform API.

Publishing, search, and engagement share a human-style interaction layer:

- Mouse movement follows a slightly curved path before clicking.
- Actions target visible and enabled page elements.
- Real mouse-wheel input scrolls the page incrementally and triggers lazy comment loading without script-injected jumps.
- Titles, body text, and tags are typed incrementally with randomized pauses.
- Search starts from the Explore page, types the query, clicks the search button, and selects filters through visible controls.
- Comments and replies are typed incrementally; likes, favorites, reply expansion, and profile navigation click visible controls.
- Image posts, visibility, scheduled publishing, and product binding are handled in the browser.
- When `is_original=true`, the original-content switch, acknowledgment checkbox, and confirmation are completed automatically.

Read-only results such as home feeds, note details, and user profiles are still extracted from browser-loaded page state. The service does not call undocumented platform APIs or add meaningless clicks merely to imitate activity.

> Browser automation can break when the website changes. Follow platform rules and use the project only with accounts and content you are authorized to operate.

## Features

### Login and session

- Check login status
- Request a QR code and wait for an App scan
- Delete local cookies to reset the session
- Run a dedicated visible login program

### Publishing

- Publish image posts from local absolute paths or HTTP/HTTPS image URLs
- Publish a single local video file
- Add topic tags
- Set visibility
- Schedule a post from 1 hour to 14 days ahead
- Confirm an original-content declaration automatically
- Bind products by keyword or product ID

### Discovery and engagement

- List home feeds
- Search with filters
- Read note details and comments
- Read user profiles
- Post and reply to comments
- Like, unlike, favorite, and unfavorite notes

## Quick start

### Requirements

- Go 1.24 or newer
- Chrome, Chromium, or a compatible browser
- The Xiaohongshu/RedNote mobile App for QR login

You can also use [Docker](#docker) or download a platform build from [Releases](https://github.com/YuriGao/xiaohongshu-mcp/releases).

### Run from source

```bash
git clone https://github.com/YuriGao/xiaohongshu-mcp.git
cd xiaohongshu-mcp

go build -o build/xiaohongshu-mcp .
go build -o build/login ./cmd/login
```

Log in before the first run:

```bash
./login.sh
```

Start the service:

```bash
./run.sh
```

`run.sh` uses a visible browser by default so you can observe the real browser interaction. Endpoints:

- MCP: `http://127.0.0.1:18060/mcp`
- Health: `http://127.0.0.1:18060/health`
- REST API: `http://127.0.0.1:18060/api/v1`

Verify the service:

```bash
curl http://127.0.0.1:18060/health
```

Headless browser mode is disabled in this project; the service must run with a visible browser.

The service binary accepts `-headless`, `-bin`, and `-port`:

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

The image includes a browser. Cookies and browser data are persisted in `docker/data/`. To publish a host image, copy it into `docker/images/` and pass its container path, such as `/app/images/photo.jpg`.

For the first login, call `get_login_qrcode` from an MCP client. See the [Docker README](./docker/README.md) for details.

## Connect an MCP client

Add a Streamable HTTP server:

```text
Name: xiaohongshu-mcp
URL:  http://127.0.0.1:18060/mcp
```

For clients that use an `mcpServers` object:

```json
{
  "mcpServers": {
    "xiaohongshu-mcp": {
      "url": "http://127.0.0.1:18060/mcp"
    }
  }
}
```

Configuration keys vary by client. Select the `Streamable HTTP` or `HTTP` transport. The repository includes example configuration for [Cursor](./.cursor/mcp.json) and [VS Code](./.vscode/mcp.json).

Recommended first-run sequence:

1. Call `check_login_status`.
2. If logged out, call `get_login_qrcode` and scan it.
3. Check login status again.
4. Validate with test content before connecting a larger automation.

## MCP tools

| Tool | Purpose |
| --- | --- |
| `check_login_status` | Check the current login state |
| `get_login_qrcode` | Request a login QR code |
| `delete_cookies` | Delete cookies and reset login |
| `publish_content` | Publish an image post |
| `publish_with_video` | Publish a video post |
| `list_feeds` | List home feeds |
| `search_feeds` | Search notes with filters |
| `get_feed_detail` | Read note details and comments |
| `user_profile` | Read a user profile |
| `post_comment_to_feed` | Post a comment |
| `reply_comment_in_feed` | Reply to a comment |
| `like_feed` | Like or unlike a note |
| `favorite_feed` | Favorite or unfavorite a note |

### Image-post parameters

Important `publish_content` parameters:

| Parameter | Required | Description |
| --- | --- | --- |
| `title` | Yes | Up to 20 Chinese characters or English words, following platform limits |
| `content` | Yes | Body text; pass topics separately through `tags` |
| `images` | Yes | One or more URLs or local absolute paths |
| `tags` | No | Topic-tag array |
| `schedule_at` | No | ISO 8601 timestamp from 1 hour to 14 days ahead |
| `is_original` | No | Set to `true` to complete the original-content confirmation |
| `visibility` | No | `公开可见`, `仅自己可见`, or `仅互关好友可见` |
| `products` | No | Product keyword or product ID array |

Example prompt:

```text
Publish an image post:
Title: Weekend park walk
Body: A quiet green afternoon worth remembering.
Image: /Users/me/Pictures/park.jpg
Tags: weekend, walking, daily life
Declare it as original content and make it visible only to me.
```

## Search filters

`search_feeds` accepts:

| Field | Values |
| --- | --- |
| `sort_by` | `综合`, `最新`, `最多点赞`, `最多评论`, `最多收藏` |
| `note_type` | `不限`, `视频`, `图文` |
| `publish_time` | `不限`, `一天内`, `一周内`, `半年内` |
| `search_scope` | `不限`, `已看过`, `未看过`, `已关注` |
| `location` | `不限`, `同城`, `附近` |

Use the `feed_id` and `xsec_token` returned by search or feed listing for detail, comment, like, and favorite tools.

## Configuration

| Environment variable | Purpose | Default |
| --- | --- | --- |
| `PORT` | Listen address used by `run.sh` | `:18060` |
| `HEADLESS` | Unsupported; this project prohibits headless mode | - |
| `BIN_PATH` | Browser path used by launch scripts | Auto-detect |
| `ROD_BROWSER_BIN` | Browser path used by the service | Auto-detect or download |
| `COOKIES_PATH` | Cookie file path | `cookies.json` in the working directory |
| `XHS_PROXY` | HTTP/HTTPS/SOCKS5 browser proxy | Disabled |

Proxy example:

```bash
XHS_PROXY=http://127.0.0.1:7890 ./run.sh
```

For backward compatibility, an existing `cookies.json` in the system temporary directory takes precedence. If login behavior is unexpected, call `delete_cookies` or inspect the active cookie path.

## REST API

The service also exposes REST endpoints for login, publishing, feeds, users, and comments. See the [HTTP API documentation](./docs/API.md).

## Integrations

- [Cherry Studio](./examples/cherrystudio/README.md)
- [AnythingLLM](./examples/anythingLLM/readme.md)
- [n8n](./examples/n8n/README.md)
- [macOS LaunchAgent](./deploy/macos/readme.md)
- [Windows guide](./docs/windows_guide.md)

## Security and operational boundaries

- The service has no built-in authentication. Do not expose it directly to the public internet.
- Publishing, comments, likes, favorites, and cookie deletion change account or local state.
- Do not keep the same account logged in on multiple web sessions; this can invalidate the active cookies.
- Verify content ownership, visibility, and product binding before publishing.
- Cookies contain login credentials. Never commit or share them.
- Start at low frequency before automating repeated operations. Platform controls and page changes are outside this project's control.

## Development

```bash
gofmt -w .
go vet ./...
go test ./pkg/...
```

The `xiaohongshu` package includes real-browser tests. Review a test before running it to avoid unintended account actions. Browser integration tests use:

```bash
go test -tags=integration ./xiaohongshu -run TestName -count=1
```

Read [CONTRIBUTING.md](./CONTRIBUTING.md) before submitting a change.

## Origin

This repository continues the work from [xpzouying/xiaohongshu-mcp](https://github.com/xpzouying/xiaohongshu-mcp) and adds human-style browser interaction and publishing-flow improvements.
