"""Run a live USAGI agent backed by Algovate xhs-mcp tools."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import mimetypes
import os
import uuid
from pathlib import Path

from pydantic import BaseModel

from usagi_agent.models import GLM_5_3_FLASH_MODEL, GLM_5_3_FLASH_CODING_PLAN_MODEL
from usagi_agent.persistence.ports.artifact import ArtifactManager
from usagi_agent.pipelines import ScenarioPipelineInitializer
from usagi_agent.pipelines.artifacts import get_text
from usagi_agent.pipelines.config.stage_config import SCENARIO_CONFIGS
from usagi_agent.registry import BootstrapSettings
from usagi_agent.ports import GovernedExecutionContext, ToolContext
from usagi_agent.server import Server, ServiceRuntimeInitializer
from usagi_agent.types.content import ImageContentPart
from usagi_agent.types.refs import ArtifactOwner
from usagi_agent.types.run import RunOptions, RunStartRequest

from xhs_autopost.mcp import build_xhs_mcp_config
from usagi_httpserver.tool_specs import PYTHON_TOOL_SPECS


class XhsRequest(BaseModel):
    query: str
    image_paths: list[str] = []


AGENT_INSTRUCTIONS = """\
你是小红书创作与资料检索助手。根据附件图片与用户要求创作文案，使用 xhs_ 前缀工具完成任务，不要臆造工具结果。
只在确有必要时调用工具；同一个调用成功后不要重复执行。
工具是否需要审批由服务端 ToolSpec 决定；发布结果不确定时不要重试。
工具返回文本中的 success:false 代表业务失败，不能当成成功。删除和重新发布必须分开调用，确认删除成功后才发布。
图片附件供你识图，发布时使用 image_paths 中的原始绝对路径。不要凭空补充图片中的鸟种、拍摄时间或具体地点。
最终用中文简洁回答，并说明实际调用了哪些能力。"""


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run USAGI with Algovate xhs-mcp.")
    parser.add_argument("query", nargs="?", help="Natural-language Xiaohongshu task.")
    parser.add_argument("--prompt-file", type=Path, help="Read a UTF-8 task prompt from a file.")
    parser.add_argument("--image", type=Path, action="append", default=[], help="Attach an image to the model; may be repeated.")
    parser.add_argument("--mcp-entry", type=Path, help="Installed xhs-mcp.cjs; start directly with Node.")
    parser.add_argument("--mcp-compat", action="store_true", help="Use the note-card compatibility fix for xhs-mcp 0.8.13; requires --mcp-entry.")
    parser.add_argument("--coding-plan", action="store_true", help="Use the configured GLM Responses endpoint.")
    parser.add_argument("--report", type=Path, help="New JSON report path; an existing report prevents accidental reruns.")
    parser.add_argument(
        "--mcp-url",
        help="Use an existing Streamable HTTP endpoint, e.g. http://127.0.0.1:3000/mcp.",
    )
    parser.add_argument(
        "--allow-writes",
        action="store_true",
        help="Expose write tools; approval remains controlled by ToolSpec.",
    )
    parser.add_argument(
        "--list-tools",
        action="store_true",
        help="Connect, print discovered tool metadata, and exit without calling a model.",
    )
    parser.add_argument("--database-path", default=".usagi/xhs-agent.db")
    return parser.parse_args()


async def attach_images(manager: ArtifactManager, paths: list[Path], scope: str) -> list[ImageContentPart]:
    parts = []
    for path in paths:
        media_type = mimetypes.guess_type(path.name)[0]
        if media_type is None or not media_type.startswith("image/"):
            raise ValueError(f"不支持的图片类型：{path}")
        payload = path.read_bytes()

        async def chunks(data=payload):
            yield data

        ref = await manager.put(
            operation_id=f"xhs-image:{scope}:{hashlib.sha256(payload).hexdigest()}",
            owner=ArtifactOwner(tenant_id="default", erasure_scope_id=scope),
            lineage=[], payload=chunks(), purpose="run_execution",
        )
        parts.append(ImageContentPart(
            media_type=media_type, artifact_ref=ref.model_copy(update={"content_type": media_type}), detail="high",
        ))
    return parts


async def run(args: argparse.Namespace) -> int:
    if args.prompt_file:
        if args.query:
            raise ValueError("query 和 --prompt-file 只能提供一个")
        args.query = args.prompt_file.read_text(encoding="utf-8")
    if args.mcp_entry and args.mcp_url:
        raise ValueError("--mcp-entry 和 --mcp-url 只能提供一个")
    if args.mcp_compat and not args.mcp_entry:
        raise ValueError("--mcp-compat 需要 --mcp-entry")
    image_paths = [path.resolve(strict=True) for path in args.image]
    if not args.list_tools and not args.query:
        raise ValueError("请提供 query，或使用 --list-tools 只验证 MCP 连接")
    if not args.list_tools and not os.getenv(GLM_5_3_FLASH_MODEL.api_key_env):
        raise RuntimeError(
            f"缺少环境变量 {GLM_5_3_FLASH_MODEL.api_key_env}，无法调用模型"
        )

    runtime = ServiceRuntimeInitializer.init(
        BootstrapSettings(model_execution_mode="live", sqlite_path=args.database_path),
        tool_specs=PYTHON_TOOL_SPECS,
    )
    mcp_config = build_xhs_mcp_config(
        allow_writes=args.allow_writes,
        url=args.mcp_url,
        cwd=Path.cwd(),
    )
    if args.mcp_entry:
        entry = args.mcp_entry.resolve(strict=True)
        node_args = (str(entry), "mcp")
        if args.mcp_compat:
            wrapper = Path(__file__).with_name("xhs-mcp-compat.cjs")
            node_args = (str(wrapper), str(entry), "mcp")
        mcp_config = mcp_config.model_copy(update={"command": "node", "args": node_args})
    try:
        adapters = await runtime.tool_manager.register_mcp(mcp_config)
        if args.list_tools:
            for adapter in adapters:
                print(f"{adapter.spec.name}\t{adapter.spec.risk}")
            return 0

        allowed_tools = tuple(adapter.spec.name for adapter in adapters)
        # The current framework scenario is generic. Application behavior is
        # supplied in the request while the business MCP remains in this package.
        model = GLM_5_3_FLASH_CODING_PLAN_MODEL if args.coding_plan else GLM_5_3_FLASH_MODEL
        runtime.agent_manager.create_agent(
            id="research_writer",
            input_schema="xhs.agent_request@1.0.0",
            output_schema="usagi.final_output@1.0.0",
            model=model,
            allowed_tools=allowed_tools,
        )
        scenarios = tuple(s.model_copy(update={"pipeline": s.pipeline.model_copy(update={"max_passes": 10, "max_tool_calls": 15})}) for s in SCENARIO_CONFIGS)
        ScenarioPipelineInitializer.init(runtime, scenarios)
        server = Server(runtime)
        attempt = uuid.uuid4().hex
        parts = await attach_images(runtime.persistence.artifact_manager, image_paths, attempt)
        report_path = args.report or Path(f".usagi/xhs-agent-run-{attempt}.json")
        report_path.parent.mkdir(parents=True, exist_ok=True)
        record = {"state": "started", "model": model.id, "prompt": args.query,
                  "image_paths": [str(p) for p in image_paths], "image_count": len(parts)}
        with report_path.open("x", encoding="utf-8") as stream:
            json.dump(record, stream, ensure_ascii=False, indent=2)
        print(f"Agent 启动：model={model.id}, images={len(parts)}, report={report_path}", flush=True)
        request = RunStartRequest(
            scenario_key="example.research_writer",
            request_idempotency_key=f"xhs-agent:{attempt}",
            input=XhsRequest(query=f"{AGENT_INSTRUCTIONS}\n\n用户请求：{args.query}", image_paths=[str(p) for p in image_paths]),
            content_parts=[*parts],
            options=RunOptions(),
        )
        message = await server.create_session(request)
        outcome = message.outcome
        record.update({"state": outcome.kind, "run_id": message.run_id, "session_id": message.session_id,
                       "usage": runtime.agent_manager.usage_for_agent("research_writer").model_dump(mode="json")})
        snapshot = await runtime.persistence.execution_context_store.get(message.run_id)
        control = await runtime.persistence.run_control_store.get(message.run_id)
        if snapshot is not None and control is not None:
            ctx = ToolContext(execution=GovernedExecutionContext(
                tenant_id=snapshot.tenant_id, principal=snapshot.original_principal,
                authorization_scope=snapshot.authorization_scope, control_kind="run",
                control_id=message.run_id, fencing_token=control.fencing_token,
            ))
            events = await runtime.memory_manager.list_events(message.session_id, ctx)
            record["events"] = [event.model_dump(mode="json") for event in events]
        report_path.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
        if outcome.kind != "completed":
            reasons = getattr(outcome, "reason_codes", [])
            print(f"运行未完成：{outcome.kind}，原因：{reasons}")
            return 1
        answer = await get_text(
            runtime.persistence.artifact_manager,
            outcome.result_ref.artifact_id,
        )
        record["answer"] = answer
        report_path.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
        print(answer)
        print(f"Agent 运行记录：{report_path}", flush=True)
        return 0
    finally:
        await runtime.shutdown()


def main() -> None:
    raise SystemExit(asyncio.run(run(parse_args())))
