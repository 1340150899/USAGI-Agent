"""Recognize folder images through the complete USAGI Agent pipeline.

Flow under test:
RunStartRequest -> Kernel -> PreRecall -> Recall -> ContextBuild -> Model ->
ResultProcess -> Memory -> End -> final Artifact.
"""
from __future__ import annotations

import argparse
import asyncio
import fnmatch
import hashlib
import mimetypes
import os
import sys
import tempfile
from collections.abc import AsyncIterator
from pathlib import Path

from pydantic import BaseModel

from usagi_agent.agents import create_model_adapter
from usagi_agent.persistence.ports.artifact import ArtifactManager
from usagi_agent.pipelines import ScenarioPipelineInitializer
from usagi_agent.pipelines.artifacts import get_text
from usagi_agent.pipelines.config.stage_config import SCENARIO_CONFIGS
from usagi_agent.ports import GovernedExecutionContext, ToolContext
from usagi_agent.registry import BootstrapSettings
from usagi_agent.server import Server, ServiceRuntimeInitializer
from usagi_agent.types.content import ImageContentPart
from usagi_agent.types.model import ModelSpec
from usagi_agent.types.refs import ArtifactOwner, ArtifactRef
from usagi_agent.types.run import RunStartRequest

DEFAULT_FOLDER = Path(__file__).resolve().parent / "test img"
IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp"}


class ImageRecognitionRequest(BaseModel):
    filename: str
    instruction: str


class RetryingVisibleModelAdapter:
    """Retry provider congestion while leaving model execution in USAGI."""

    adapter_ref = "usagi.image_recognition_live"

    def __init__(
        self,
        spec: ModelSpec,
        *,
        retries: int,
        retry_delay: float,
    ) -> None:
        self._adapter = create_model_adapter(spec)
        self._retries = retries
        self._retry_delay = retry_delay

    async def generate(self, request, ctx):
        for attempt in range(self._retries + 1):
            try:
                return await self._adapter.generate(request, ctx)
            except Exception as exc:
                status_code = getattr(exc, "status_code", None)
                if status_code != 429 or attempt >= self._retries:
                    print(
                        f"模型调用失败：{type(exc).__name__}: {str(exc)[:1000]}",
                        file=sys.stderr,
                    )
                    raise
                delay = self._retry_delay * (attempt + 1)
                print(f"模型繁忙，{delay:g} 秒后进行第 {attempt + 1} 次重试...")
                await asyncio.sleep(delay)
        raise RuntimeError("unreachable retry state")

    async def health(self):
        return await self._adapter.health()

    async def shutdown(self) -> None:
        await self._adapter.shutdown()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Recognize images through the complete USAGI pipeline."
    )
    parser.add_argument("folder", nargs="?", type=Path, default=DEFAULT_FOLDER)
    parser.add_argument(
        "--model", default=os.getenv("USAGI_VISION_MODEL", "glm-5.3-flash")
    )
    parser.add_argument(
        "--protocol",
        choices=["chat", "responses"],
        default=os.getenv("USAGI_VISION_PROTOCOL", "responses"),
        help="Provider wire protocol: chat/completions or OpenAI Responses.",
    )
    parser.add_argument(
        "--base-url",
        default=os.getenv(
            "USAGI_VISION_BASE_URL", "https://open.bigmodel.cn/api/v1"
        ),
    )
    parser.add_argument(
        "--api-key-env",
        default=os.getenv("USAGI_VISION_API_KEY_ENV", "GLM_API_KEY"),
        help="Environment variable containing the provider API key.",
    )
    parser.add_argument(
        "--prompt",
        default=(
            "请详细识别并描述附件图片。说明主要人物或物体、场景、正在发生的事情、"
            "可辨认的文字以及值得注意的细节；不确定的内容请明确说明。"
        ),
    )
    parser.add_argument("--max-output-tokens", type=int, default=8192)
    parser.add_argument("--pattern", default="*")
    parser.add_argument("--retries", type=int, default=3)
    parser.add_argument("--retry-delay", type=float, default=10.0)
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def find_images(folder: Path, pattern: str = "*") -> list[Path]:
    if not folder.is_dir():
        raise NotADirectoryError(f"image folder does not exist: {folder}")
    return sorted(
        path
        for path in folder.rglob("*")
        if path.is_file()
        and path.suffix.lower() in IMAGE_SUFFIXES
        and fnmatch.fnmatch(path.name, pattern)
    )


async def put_image(artifact_manager: ArtifactManager, path: Path) -> ArtifactRef:
    payload = path.read_bytes()

    async def chunks() -> AsyncIterator[bytes]:
        yield payload

    digest = hashlib.sha256(payload).hexdigest()[:24]
    ref = await artifact_manager.put(
        operation_id=f"image-smoke:{path.name}:{digest}",
        owner=ArtifactOwner(tenant_id="default", erasure_scope_id="image-smoke"),
        lineage=[],
        payload=chunks(),
        purpose="run_execution",
    )
    media_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    if not media_type.startswith("image/"):
        raise ValueError(f"unsupported image media type for {path}: {media_type}")
    return ref.model_copy(update={"content_type": media_type})


async def run_one(server: Server, runtime, path: Path, args) -> bool:
    image_ref = await put_image(runtime.persistence.artifact_manager, path)
    usage_before = runtime.agent_manager.usage_for_agent("research_writer")
    digest = hashlib.sha256(str(path.resolve()).encode()).hexdigest()[:16]
    request = RunStartRequest(
        scenario_key="example.research_writer",
        request_idempotency_key=f"image-recognition:{digest}:{path.stat().st_mtime_ns}",
        input=ImageRecognitionRequest(filename=path.name, instruction=args.prompt),
        content_parts=[
            ImageContentPart(
                media_type=image_ref.content_type,
                artifact_ref=image_ref,
                detail="high",
            )
        ],
    )
    handle = await server.start_agent(request)
    outcome = await server.get_run(handle.run_id)
    usage_after = runtime.agent_manager.usage_for_agent("research_writer")

    snapshot = await runtime.persistence.execution_context_store.get(handle.run_id)
    control = await runtime.persistence.run_control_store.get(handle.run_id)
    event_roles: list[str] = []
    multimodal_event_count = 0
    if snapshot is not None and control is not None:
        memory_context = ToolContext(
            execution=GovernedExecutionContext(
                tenant_id=snapshot.tenant_id,
                principal=snapshot.original_principal,
                authorization_scope=snapshot.authorization_scope,
                control_kind="run",
                control_id=handle.run_id,
                fencing_token=control.fencing_token,
            )
        )
        events = await runtime.memory_manager.list_events(
            handle.thread_id, memory_context
        )
        event_roles = [event.role for event in events]
        multimodal_event_count = sum(bool(event.content_parts) for event in events)

    if outcome.kind != "completed":
        reason_codes = getattr(outcome, "reason_codes", [])
        print(f"USAGI 运行失败：{outcome.kind}, reasons={reason_codes}", file=sys.stderr)
        return False

    answer = await get_text(
        runtime.persistence.artifact_manager, outcome.result_ref.artifact_id
    )
    print(answer)
    print(
        "USAGI 验证："
        f"run_id={handle.run_id}, outcome={outcome.kind}, "
        f"events={event_roles}, multimodal_events={multimodal_event_count}, "
        f"input_tokens={usage_after.input_tokens - usage_before.input_tokens}, "
        f"output_tokens={usage_after.output_tokens - usage_before.output_tokens}, "
        f"final_artifact={outcome.result_ref.artifact_id}"
    )
    return True


async def recognize_folder(args: argparse.Namespace) -> int:
    images = find_images(args.folder.resolve(), args.pattern)
    if not images:
        print(f"没有找到图片：{args.folder}")
        return 1
    print(f"找到 {len(images)} 张图片：")
    for path in images:
        print(f"- {path} ({path.stat().st_size / 1024 / 1024:.1f} MiB)")
    if args.dry_run:
        return 0
    if not os.getenv(args.api_key_env):
        print(f"缺少环境变量 {args.api_key_env}，无法调用模型。", file=sys.stderr)
        return 2

    failures = 0
    with tempfile.TemporaryDirectory(prefix="usagi-image-recognition-") as temp_dir:
        runtime = ServiceRuntimeInitializer.init(
            BootstrapSettings(
                model_execution_mode="live",
                memory_path=str(Path(temp_dir) / "memory.json"),
            )
        )
        model = ModelSpec(
            id=f"vision:{args.model}",
            provider_model=args.model,
            protocol=(
                "openai_responses" if args.protocol == "responses" else "openai_chat"
            ),
            input_modalities=frozenset({"text", "image"}),
            base_url=args.base_url,
            api_key_env=args.api_key_env,
            context_window=128_000,
            default_max_output_tokens=args.max_output_tokens,
        )
        runtime.agent_manager.create_agent(
            id="research_writer",
            input_schema="usagi.image_recognition_request@1.0.0",
            output_schema="usagi.final_output@1.0.0",
            model=model,
            allowed_tools=(),
        )
        runtime.agent_manager.set_model_adapter(
            RetryingVisibleModelAdapter(
                model,
                retries=max(0, args.retries),
                retry_delay=max(0.0, args.retry_delay),
            )
        )
        ScenarioPipelineInitializer.init(runtime, SCENARIO_CONFIGS)
        server = Server(runtime)
        try:
            for index, path in enumerate(images, start=1):
                print(f"\n[{index}/{len(images)}] USAGI 全流程识别 {path.name} ...")
                try:
                    if not await run_one(server, runtime, path, args):
                        failures += 1
                except Exception as exc:
                    failures += 1
                    print(
                        f"识别失败：{type(exc).__name__}: {str(exc)[:1000]}",
                        file=sys.stderr,
                    )
        finally:
            await server.shutdown()
    return 1 if failures else 0


def main() -> None:
    raise SystemExit(asyncio.run(recognize_folder(parse_args())))


if __name__ == "__main__":
    main()
