"""Verify real XHS MCP tools and optionally publish one explicitly supplied note."""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "apps" / "httpserver"))
sys.path.insert(0, str(ROOT / "usagi-agent" / "src"))

from usagi_httpserver.xhs_mcp import build_xhs_mcp_config

from usagi_agent.policies.engine import DefaultPolicyEngine
from usagi_agent.ports.context import GovernedExecutionContext, ToolContext
from usagi_agent.tools import ToolManager
from usagi_agent.types.refs import PrincipalRef


def unpack(observation):
    if observation.status != "success":
        raise RuntimeError(observation.model_dump_json())
    output = observation.output
    for block in output.get("content", []):
        if block.get("type") == "text":
            try:
                return json.loads(block["text"])
            except json.JSONDecodeError:
                continue
    return output


async def run(args):
    config = build_xhs_mcp_config(allow_writes=args.publish, url=args.mcp_url)
    manager = ToolManager()
    context = ToolContext(execution=GovernedExecutionContext(
        tenant_id="xhs-live-test",
        principal=PrincipalRef(principal_kind="user", principal_opaque_id="local-user"),
        authorization_scope=(), control_kind="run",
        control_id="xhs-live-test", fencing_token=1,
    ))
    policy = DefaultPolicyEngine(manager)

    async def call(name, arguments):
        decision = await policy.evaluate(
            principal=context.execution.principal, action="tool.execute",
            tool_name=name, arguments=arguments, context=context,
        )
        if decision.effect != "allow":
            raise RuntimeError(f"Policy did not allow {name}: {decision.effect}")
        result = unpack(await manager.execute(
            name=name, arguments=arguments, context=context, tool_call_id=name,
        ))
        print(json.dumps({"tool": name, "result": result}, ensure_ascii=False), flush=True)
        if isinstance(result, dict) and result.get("success") is False:
            raise RuntimeError(f"{name} returned a business failure")
        return result

    try:
        adapters = await manager.register_mcp(config)
        print(json.dumps({"connected": True, "tools": [a.spec.name for a in adapters]}, ensure_ascii=False), flush=True)
        auth = await call("xhs_check_login_status", {})
        auth_text = json.dumps(auth, ensure_ascii=False)
        if "已登录" not in auth_text or "未登录" in auth_text:
            raise RuntimeError("XHS login was not confirmed")
        if not args.publish:
            return
        media = Path(args.image).resolve(strict=True)
        tags = [tag.strip() for tag in args.tags.split(",") if tag.strip()]
        arguments = {"title": args.title, "content": args.content,
                     "images": [str(media)], "tags": tags}
        report = Path(args.report).resolve()
        report.parent.mkdir(parents=True, exist_ok=True)
        record = {"state": "started", "arguments": arguments}
        # Exclusive creation prevents rerunning this publication after an uncertain result.
        with report.open("x", encoding="utf-8") as stream:
            json.dump(record, stream, ensure_ascii=False, indent=2)
        try:
            record["publish_result"] = await call("xhs_publish_content", arguments)
            record["state"] = "returned_success"
        except BaseException:
            record["state"] = "failed_or_unknown"
            raise
        finally:
            report.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
        record["feeds_after_publish"] = await call("xhs_list_feeds", {})
        report.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"REPORT {report}", flush=True)
    finally:
        await manager.shutdown()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mcp-url", default="http://127.0.0.1:18060/mcp")
    parser.add_argument("--publish", action="store_true")
    parser.add_argument("--image")
    parser.add_argument("--title")
    parser.add_argument("--content")
    parser.add_argument("--tags", default="")
    parser.add_argument("--report", default=str(ROOT / ".usagi" / "xhs-live-publish.json"))
    args = parser.parse_args()
    if args.publish and not all((args.image, args.title, args.content)):
        parser.error("--publish requires --image, --title and --content")
    asyncio.run(run(args))


if __name__ == "__main__":
    main()
