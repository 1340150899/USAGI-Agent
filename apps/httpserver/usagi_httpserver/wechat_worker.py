"""Direct Agent execution for ready wxauto material windows."""
import asyncio
import hashlib
import logging
from pathlib import Path

from pydantic import BaseModel

from usagi_agent.kernel import AuthContext
from usagi_agent.prompts import WECHAT_MATERIAL_PROMPT
from usagi_agent.types.content import ImageContentPart
from usagi_agent.types.refs import ArtifactOwner, PrincipalRef
from usagi_agent.types.run import RunStartRequest


log = logging.getLogger(__name__)

WECHAT_MATERIAL_OUTPUT_REF = "wechat.material_result@1.0.0"
WECHAT_MATERIAL_OUTPUT_SCHEMA = {
        "type": "object",
        "properties": {
            "status": {"type": "string", "enum": ["success", "fail", "insufficient"]},
            "message": {"type": "string", "minLength": 1},
            "selected_image_indices": {"type": "array", "items": {"type": "integer", "minimum": 1}, "uniqueItems": True},
            "material_ids": {"type": "array", "items": {"type": "string", "minLength": 1}, "uniqueItems": True},
            "reason_code": {"type": ["string", "null"]},
        },
        "required": ["status", "message", "selected_image_indices", "material_ids", "reason_code"],
        "additionalProperties": False,
}


class MaterialQuery(BaseModel):
    query: str
    image_paths: list[str] = []


def material_auth(uid):
    return AuthContext(
        principal=PrincipalRef(
            principal_kind="user", principal_opaque_id=str(uid)
        ),
        authorization_scope=("run.start", "run.execute", "run.read", "run.resume"),
    )


class WeChatMaterialWorker:
    """Select quiet windows, invoke Agent directly, and settle candidate states."""

    def __init__(self, server, store, settings, *, stopping):
        self.server = server
        self.store = store
        self.settings = settings
        self.stopping = stopping
        self.scenario = "example.research_writer"

    async def run(self):
        log.info("wechat_material_worker_initialized")
        self.store.recover_material_windows()
        while not self.stopping.is_set():
            await self.reconcile_selected_windows()
            window = self.store.select_material_window(
                silence=self.settings.get("silence_seconds", 10),
            )
            if window is None:
                await asyncio.sleep(0.5)
                continue
            try:
                await self.execute(window)
            except Exception as exc:
                log.exception("material window %s failed", window["id"])
                self.store.finish_material_window(window["id"], consumed=False)
                self._notify(
                    window,
                    f"素材处理失败（{type(exc).__name__}），已退回候选池。",
                )

    async def execute(self, window):
        """Create a fresh research_writer Session without creating a job row."""
        log.info("material_window_processing_started window_id=%s", window["id"])
        body = window["payload"]
        uid = str(self.settings.get("super_uid", "6"))
        image_parts, paths = await self._material_images(
            body.get("content_parts", []), window["principal"], uid
        )
        media_ids = [
            part["media_id"]
            for part in body.get("content_parts", [])
            if part["type"] == "image"
        ]
        request = RunStartRequest(
            scenario_key=self.scenario,
            request_idempotency_key=window["id"],
            input=MaterialQuery(
                query=WECHAT_MATERIAL_PROMPT.render(
                    {"material_text": str(body.get("query", "")).strip()}
                ),
                image_paths=paths,
            ),
            content_parts=image_parts,
        )
        result = await self.server.create_session(request, auth=material_auth(uid))
        log.info("material_agent_result_received window_id=%s run_id=%s outcome=%s has_result=%s",
                 window["id"], result.run_id, result.outcome.kind, bool(result.message))
        self.store.bind_session(uid, result.session_id)
        self.store.bind_material_window(
            window["id"], result.session_id, result.run_id
        )
        self.store.record_session_media(result.session_id, media_ids)
        output = self._result_output(result)
        await self._settle_initial_turn(
            window["id"], result.run_id, result.outcome.kind, output
        )
        # A draft the model rejected for thin material returns to the pool
        # silently; only actionable outcomes reach the user.
        insufficient = (
            result.outcome.kind == "completed"
            and output.get("status") == "insufficient"
        )
        if not insufficient:
            self._notify(
                window,
                result.message,
                session_id=result.session_id,
                broadcast=True,
                media_ids=self._draft_media_ids(result, media_ids),
            )
            log.info("material_result_queued window_id=%s session_id=%s", window["id"], result.session_id)
        else:
            log.info("material_result_insufficient window_id=%s", window["id"])

    async def reconcile_selected_windows(self):
        """Settle material whose approval was resumed by the interaction service."""
        uid = str(self.settings.get("super_uid", "6"))
        for window in self.store.selected_material_windows():
            run_id = (
                self.store.latest_run_for_session(window["session_id"])
                or window["run_id"]
            )
            outcome = await self.server.get_run(run_id, auth=material_auth(uid))
            if run_id != window["run_id"]:
                # A later turn means the user has answered the Agent's question.
                await self._settle_terminal(window["id"], run_id, outcome.kind)
            elif outcome.kind == "failed":
                self.store.finish_material_window(window["id"], consumed=False)
            elif await self._publication_state(run_id) == "published":
                # Defensive handling if a model violates the first-turn prompt.
                self.store.finish_material_window(window["id"], consumed=True)

    async def _settle_initial_turn(self, window_id, run_id, outcome_kind, output):
        """Keep a generated draft selected while waiting for the user's reply."""
        if outcome_kind in ("suspended", "running", "resuming", "cancelling"):
            return
        publication = await self._publication_state(run_id)
        if publication == "published":
            self.store.finish_material_window(window_id, consumed=True)
        elif outcome_kind != "completed" or output.get("status") == "insufficient":
            self.store.finish_material_window(window_id, consumed=False)

    async def _settle_terminal(self, window_id, run_id, outcome_kind):
        if outcome_kind in ("suspended", "running", "resuming", "cancelling"):
            return
        publication = await self._publication_state(run_id)
        if publication != "unknown":
            self.store.finish_material_window(
                window_id, consumed=publication == "published"
            )

    async def _material_images(self, content_parts, principal, uid):
        image_parts, paths = [], []
        for part in content_parts:
            if part["type"] != "image":
                continue
            media = self.store.media(part["media_id"], principal)
            if media is None:
                raise ValueError("media unavailable")
            data = Path(media["path"]).read_bytes()
            if hashlib.sha256(data).hexdigest() != media["sha256"]:
                raise ValueError("media changed")

            async def chunks(data=data):
                yield data

            ref = await self.server.runtime.persistence.artifact_manager.put(
                operation_id="media:" + media["id"],
                owner=ArtifactOwner(tenant_id="default", erasure_scope_id=uid),
                lineage=[],
                payload=chunks(),
                purpose="run_execution",
            )
            image_parts.append(
                ImageContentPart(media_type=media["mime"], artifact_ref=ref)
            )
            paths.append(media["path"])
        return image_parts, paths

    async def _publication_state(self, run_id):
        records = (
            await self.server.runtime.persistence.tool_execution_store.list_by_run(
                run_id
            )
        )
        publish_records = [
            record for record in records if record.tool_name == "xhs_publish_content"
        ]
        if any(
            record.execution_status == "settled_success"
            for record in publish_records
        ):
            return "published"
        if any(
            record.execution_status in ("reserved", "executing", "unknown")
            for record in publish_records
        ):
            return "unknown"
        return "not_published"

    def _draft_media_ids(self, result, window_media_ids):
        """Map the draft's 配图 marker to the window images it should carry.

        A completed draft without a marker still ships every window image —
        the user must see what the model picked from. Anything else (an
        explicit empty selection, suspended approvals, failures) stays
        text-only.
        """
        if result.outcome.kind != "completed":
            return []
        indices = self._result_output(result).get(
            "selected_image_indices", []
        )
        if not isinstance(indices, list):
            return []
        return [window_media_ids[index - 1] for index in indices]

    @staticmethod
    def _result_output(result):
        output = getattr(result, "structured_output", None)
        return output if isinstance(output, dict) else {}

    def _notify(self, window, text, *, session_id=None, broadcast=False, media_ids=None):
        route = self.settings.get("reply_routes", {}).get(window["principal"])
        payload = {
            "reply_route_ref": route,
            "text": text,
            "broadcast": broadcast,
        }
        if session_id:
            payload["session_id"] = session_id
        if media_ids:
            payload["media_ids"] = list(media_ids)
        self.store.notify(window["id"] + ":message", window["principal"], payload)
