"""Runnable MCP fixture server for stdio and Streamable HTTP tests."""
import argparse
from collections.abc import Awaitable, Callable
from typing import Any

from mcp.server import MCPServer
from mcp.types import ToolAnnotations

server = MCPServer("usagi-test", version="1.0")


class RequireToken:
    def __init__(self, app: Callable[..., Awaitable[None]], token: str) -> None:
        self.app = app
        self.token = token.encode()

    async def __call__(
        self,
        scope: dict[str, Any],
        receive: Callable[..., Awaitable[dict[str, Any]]],
        send: Callable[..., Awaitable[None]],
    ) -> None:
        if scope.get("type") == "http":
            headers = dict(scope.get("headers", []))
            if headers.get(b"authorization") != b"Bearer " + self.token:
                await send(
                    {
                        "type": "http.response.start",
                        "status": 401,
                        "headers": [(b"content-type", b"text/plain")],
                    }
                )
                await send({"type": "http.response.body", "body": b"unauthorized"})
                return
        await self.app(scope, receive, send)


@server.tool(annotations=ToolAnnotations(read_only_hint=True))
def echo(message: str) -> dict[str, str]:
    """Echo one message."""
    return {"echo": message}


@server.tool()
def change_value(value: str) -> dict[str, str]:
    """Represent an untrusted write-like tool."""
    return {"changed": value}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--transport", choices=("stdio", "http"), default="stdio")
    parser.add_argument("--port", type=int, default=8000)
    options = parser.parse_args()
    if options.transport == "stdio":
        server.run(transport="stdio")
    else:
        import uvicorn

        app = server.streamable_http_app(
            streamable_http_path="/mcp",
            stateless_http=False,
            host="127.0.0.1",
        )
        uvicorn.run(
            RequireToken(app, "test-secret"),
            host="127.0.0.1",
            port=options.port,
            log_level="error",
        )
