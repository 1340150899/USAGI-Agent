"""Read-only smoke test for an explicitly trusted remote MCP endpoint.

This script connects, performs discovery, prints tool metadata and disconnects.
It never invokes a remote tool. Tokens are read from an environment variable so
they do not appear in the command line or configuration representation.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
from urllib.parse import urlparse

from pydantic import SecretStr

from usagi_agent.persistence.inmemory.artifact import (
    InMemoryArtifactBlobStore,
    InMemoryArtifactManager,
    InMemoryArtifactMetadataStore,
)
from usagi_agent.ports.context import GovernedExecutionContext, ToolContext
from usagi_agent.tools.manager import ToolManager
from usagi_agent.tools.mcp import MCPServerConfig, MCPServerSource
from usagi_agent.types.refs import PrincipalRef

DEFAULT_SAFE_URL = "https://mcp.deepwiki.com/mcp"


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Discover tools from a trusted HTTPS MCP endpoint without calling them."
    )
    parser.add_argument("--url", default=DEFAULT_SAFE_URL)
    parser.add_argument(
        "--bearer-token-env",
        help="Environment variable containing an optional OAuth bearer token.",
    )
    parser.add_argument(
        "--deepwiki-read-test",
        action="store_true",
        help="Call DeepWiki read_wiki_structure for the public MCP Python SDK repo.",
    )
    return parser.parse_args()


async def _run(
    url: str, bearer_token_env: str | None, deepwiki_read_test: bool
) -> None:
    parsed = urlparse(url)
    if parsed.scheme != "https" or not parsed.hostname:
        raise ValueError("live MCP smoke test only accepts an absolute HTTPS URL")
    if deepwiki_read_test and (
        parsed.hostname != "mcp.deepwiki.com" or parsed.path.rstrip("/") != "/mcp"
    ):
        raise ValueError("--deepwiki-read-test is restricted to mcp.deepwiki.com/mcp")

    headers: dict[str, SecretStr] = {}
    if bearer_token_env:
        token = os.environ.get(bearer_token_env)
        if not token:
            raise RuntimeError(
                f"bearer token environment variable {bearer_token_env!r} is empty"
            )
        headers["Authorization"] = SecretStr(f"Bearer {token}")

    artifacts = InMemoryArtifactManager(
        InMemoryArtifactMetadataStore(), InMemoryArtifactBlobStore()
    )
    manager = ToolManager(artifact_manager=artifacts)
    source = MCPServerSource(
        MCPServerConfig(
            name="live_smoke",
            transport="streamable_http",
            url=url,
            headers=headers,
            startup_timeout_seconds=20,
            read_timeout_seconds=30,
            spec_overrides=(
                {"read_wiki_structure": {"risk": "read"}}
                if deepwiki_read_test
                else {}
            ),
        ),
        artifact_manager=artifacts,
    )
    try:
        tools = await manager.load_source(source)
        print(f"connected={url} tools={len(tools)}")
        for tool in tools:
            print(
                f"{tool.spec.name}\trisk={tool.spec.risk}\t"
                f"description={tool.spec.description[:120]!r}"
            )
            print(json.dumps(tool.spec.parameters, ensure_ascii=False, sort_keys=True))
        if deepwiki_read_test:
            observation = await manager.execute(
                name="live_smoke__read_wiki_structure",
                arguments={"repoName": "modelcontextprotocol/python-sdk"},
                context=ToolContext(
                    execution=GovernedExecutionContext(
                        tenant_id="live-smoke",
                        principal=PrincipalRef(
                            principal_kind="system",
                            principal_opaque_id="live-mcp-smoke",
                        ),
                        authorization_scope=(),
                        control_kind="run",
                        control_id="live-mcp-smoke",
                        fencing_token=1,
                    )
                ),
                tool_call_id="deepwiki-read-test",
            )
            if observation.status != "success":
                raise RuntimeError(
                    f"safe tool call failed: {observation.error_code}: "
                    f"{observation.error_message}"
                )
            rendered = json.dumps(
                observation.output, ensure_ascii=False, default=str
            )
            print(f"tool_call=success output_preview={rendered[:500]}")
    finally:
        await manager.shutdown()


def main() -> None:
    options = _arguments()
    asyncio.run(
        _run(options.url, options.bearer_token_env, options.deepwiki_read_test)
    )


if __name__ == "__main__":
    main()
