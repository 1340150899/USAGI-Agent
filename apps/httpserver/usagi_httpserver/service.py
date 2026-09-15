"""Durable message forwarding between channel adapters and USAGI sessions."""
import asyncio
import hashlib
import json
import logging
from pathlib import Path

import httpx
from pydantic import BaseModel

from usagi_agent.kernel import AuthContext
from usagi_agent.types.refs import PrincipalRef, ArtifactOwner
from usagi_agent.types.content import ImageContentPart
from usagi_agent.types.run import RunStartRequest


log = logging.getLogger(__name__)


class Query(BaseModel):
    query: str
    image_paths: list[str] = []


def auth_for(uid):
    return AuthContext(
        principal=PrincipalRef(principal_kind="user", principal_opaque_id=str(uid)),
        authorization_scope=("run.start", "run.execute", "run.read", "run.resume"),
    )


class ApplicationService:
    def __init__(self, server, store, settings):
        self.server, self.store, self.settings = server, store, settings
        self.scenario = "example.research_writer"
        self.stopping = asyncio.Event()

    async def run(self):
        log.info("application_worker_initialized")
        self.store.recover()
        while not self.stopping.is_set():
            self.store.collect(
                silence=self.settings.get("silence_seconds", 10),
                maximum=self.settings.get("maximum_seconds", 900),
            )
            job = self.store.claim()
            if not job:
                await asyncio.sleep(0.5)
                continue
            try:
                await self.execute(job)
            except Exception as exc:
                log.exception("job %s failed", job["id"])
                self.store.finish(job["id"], "unknown", outcome={"reason": type(exc).__name__})
                body = json.loads(job["payload"])
                route = body.get("reply_route_ref") or self.settings.get("reply_routes", {}).get(job["principal"])
                self.store.notify(job["id"] + ":unknown", job["principal"], {
                    "reply_route_ref": route, "text": f"任务 {job['id']} 处理失败，请稍后重试。",
                })

    async def execute(self, job):
        """Resolve a session and leave conversation/approval logic to USAGI."""
        log.info("agent_job_processing_started job_id=%s", job["id"])
        body = json.loads(job["payload"])
        route = body.get("reply_route_ref") or self.settings.get("reply_routes", {}).get(job["principal"])
        ref_msg_id = body.get("ref_msg_id")
        session_id = self.store.session_for_message(ref_msg_id) if ref_msg_id else None
        if ref_msg_id and not session_id:
            self.store.notify(job["id"] + ":unmapped", job["principal"], {
                "reply_route_ref": route,
                "text": "无法识别被引用消息对应的会话，请直接发送新消息。",
            })
            self.store.finish(job["id"], "completed")
            return

        super_uid = str(self.settings.get("super_uid", "6"))
        uid = str(body.get("uid") or job["principal"])
        if body.get("mode") == "material":
            uid = super_uid
        if session_id:
            session = await self.server.runtime.session_manager.get(session_id)
            if session is None:
                raise ValueError("referenced session no longer exists")
            uid = session.uid
        elif uid != super_uid:
            session_id = self.store.session_for_uid(uid)
            if not session_id:
                existing = await self.server.runtime.session_manager.get_for_uid(uid)
                session_id = existing.id if existing else None

        image_parts, paths, media_ids = [], [], []
        for part in body.get("content_parts", []):
            if part["type"] != "image":
                continue
            media = self.store.media(part["media_id"], job["principal"])
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
                lineage=[], payload=chunks(), purpose="run_execution",
            )
            image_parts.append(ImageContentPart(media_type=media["mime"], artifact_ref=ref))
            paths.append(media["path"])
            media_ids.append(media["id"])

        request = RunStartRequest(
            scenario_key=self.scenario, request_idempotency_key=job["id"],
            input=Query(query=body.get("query", "").strip(), image_paths=paths),
            content_parts=image_parts,
        )
        auth = auth_for(uid)
        if session_id:
            result = await self.server.continue_session(session_id, request, auth=auth)
        else:
            result = await self.server.create_session(request, auth=auth)
            session_id = result.session_id
        log.info("agent_job_result_received job_id=%s run_id=%s outcome=%s has_result=%s",
                 job["id"], result.run_id, result.outcome.kind, bool(result.message))
        self.store.bind_session(uid, session_id)
        self.store.record_session_media(session_id, media_ids)
        self.store.record_session_run(session_id, result.run_id)
        status = "unknown" if result.outcome.kind in ("running", "resuming") else result.outcome.kind
        self.store.finish(job["id"], status, result.run_id, result.outcome.model_dump(mode="json"))
        payload = {
            "reply_route_ref": route, "text": result.message,
            "session_id": result.session_id, "broadcast": uid == super_uid,
        }
        reply_media_ids = self._reply_media_ids(session_id, result)
        if reply_media_ids:
            payload["media_ids"] = reply_media_ids
        self.store.notify(job["id"] + ":message", job["principal"], payload)

    def _reply_media_ids(self, session_id, result):
        """Attach images to an interactive reply only when the draft says so.

        Unlike the first material broadcast there is no all-images fallback:
        a conversation turn without a 配图 marker is a plain answer.
        """
        if result.outcome.kind != "completed":
            return []
        media_ids = self.store.session_media_ids(session_id)
        if not media_ids:
            return []
        output = getattr(result, "structured_output", None)
        indices = output.get("selected_image_indices", []) if isinstance(output, dict) else []
        if not isinstance(indices, list):
            return []
        if not indices:
            return []
        return [media_ids[index - 1] for index in indices]

    async def deliver(self):
        adapter = self.settings.get("weixin_adapter")
        if not adapter:
            return
        timeout = adapter.get("timeout_seconds", 180)
        async with httpx.AsyncClient(timeout=timeout) as client:
            while not self.stopping.is_set():
                for record in self.store.pending_deliveries():
                    payload = json.loads(record["payload"])
                    if not payload.get("reply_route_ref") and not payload.get("broadcast"):
                        log.warning("adapter_forward_unroutable delivery_id=%s", record["id"])
                        self.store.delivery_status(record["id"], "unroutable")
                        continue
                    try:
                        headers = {"Authorization": "Bearer " + adapter["token"]}
                        root = adapter["url"].rstrip("/")
                        endpoint = "/internal/broadcasts" if payload.get("broadcast") else "/internal/messages"
                        response = await client.post(
                            root + endpoint,
                            headers=headers,
                            json={"delivery_id": record["id"], **payload},
                        )
                        response.raise_for_status()
                        result = response.json()
                        status = result.get("status", "accepted")
                        complete = self.store.record_delivery_messages(
                            record["id"], payload.get("session_id"), result.get("messages", []), status
                        )
                        self.store.delivery_status(record["id"], "mapped" if complete else status)
                        log.info("adapter_forward_completed delivery_id=%s endpoint=%s status=%s mapped=%s",
                                 record["id"], endpoint, status, complete)
                    except httpx.HTTPError as exc:
                        log.exception("adapter_forward_failed delivery_id=%s error_type=%s",
                                      record["id"], type(exc).__name__)
                        self.store.delivery_status(record["id"], record["status"])
                await asyncio.sleep(1)
