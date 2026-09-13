"""Authenticated HTTP facade with optional durable application workers."""
from __future__ import annotations

import hmac
import asyncio
import logging
import time
from contextlib import suppress
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from pydantic import BaseModel, ConfigDict

from usagi_agent.api.errors import PolicyDeniedError
from usagi_agent.kernel import AuthContext
from usagi_agent.types.refs import PrincipalRef
from usagi_agent.types.run import RunStartRequest

log = logging.getLogger(__name__)


class MessageRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request_idempotency_key: str
    query: str


class UserQuery(BaseModel):
    query: str


def create_app(server, *, credentials: dict[str, str], scenario_key: str,
               close_server: bool = True, service=None) -> FastAPI:
    """credentials maps bearer secrets to authenticated principals (not client IDs)."""
    if not credentials or any(len(secret) < 24 for secret in credentials):
        raise ValueError("configure bearer credentials of at least 24 characters")

    @asynccontextmanager
    async def lifespan(app):
        tasks=[]
        if service:
            from .wechat_worker import WeChatMaterialWorker

            wechat_worker = WeChatMaterialWorker(
                server, service.store, service.settings, stopping=service.stopping
            )
            tasks=[
                asyncio.create_task(service.run()),
                asyncio.create_task(wechat_worker.run()),
            ]
            if service.settings.get('weixin_adapter'):
                tasks.append(asyncio.create_task(service.deliver()))
        app.state.worker_tasks=tasks
        try:
            yield
        finally:
            if service:
                service.stopping.set()
            for task in tasks:
                task.cancel()
                with suppress(asyncio.CancelledError):
                    await task
            if close_server:
                await server.shutdown()

    app = FastAPI(title="USAGI application API", lifespan=lifespan)

    @app.middleware("http")
    async def log_request(request: Request, call_next):
        started = time.monotonic()
        log.info("http_request_received method=%s path=%s", request.method, request.url.path)
        try:
            response = await call_next(request)
        except Exception as exc:
            log.exception(
                "http_request_failed method=%s path=%s duration_ms=%.3f error_type=%s",
                request.method, request.url.path, (time.monotonic() - started) * 1000,
                type(exc).__name__,
            )
            raise
        level = logging.INFO if response.status_code < 400 else logging.WARNING
        log.log(level, "http_request_completed method=%s path=%s status=%s duration_ms=%.3f",
                request.method, request.url.path, response.status_code,
                (time.monotonic() - started) * 1000)
        return response

    def authenticate(request: Request, authorization: str = Header(default="")) -> AuthContext:
        supplied = authorization.removeprefix("Bearer ") if authorization.startswith("Bearer ") else ""
        for secret, principal in credentials.items():
            if hmac.compare_digest(supplied, secret):
                if service:
                    binding=service.settings['credentials'][secret]
                    if binding.get('source')!='api' and not (
                        request.url.path.startswith('/v1/ingress/') or
                        request.url.path.startswith('/v1/media/') or
                        request.url.path=='/v1/adapters/heartbeat'):
                        raise HTTPException(403,'adapter credential cannot call control APIs')
                return AuthContext(
                    principal=PrincipalRef(principal_kind="user", principal_opaque_id=principal),
                    authorization_scope=("run.start", "run.execute", "run.read", "run.resume"),
                )
        raise HTTPException(401, "invalid credentials")

    async def outcome(run_id, auth):
        try:
            return await server.get_run(run_id, auth=auth)
        except PolicyDeniedError as exc:
            raise HTTPException(403, "run access denied") from exc

    @app.get("/health/live")
    async def health():
        workers=getattr(app.state,'worker_tasks',[])
        return {"alive": True, "ready": server.ready and all(not task.done() for task in workers)}

    @app.get("/v1/tools")
    async def tools(auth=Depends(authenticate)):
        return [{"name": s.name, "requires_approval": s.requires_approval,
                 "risk": s.risk, "source": s.adapter_kind}
                for s in server.runtime.tool_manager.all_specs()]

    @app.post("/v1/sessions")
    async def start(request: MessageRequest, auth=Depends(authenticate)):
        if service:
            from fastapi.responses import JSONResponse
            try:
                task_id=service.store.enqueue(auth.principal.principal_opaque_id,
                    request.request_idempotency_key,{'kind':'message','query':request.query})
            except ValueError as exc:
                raise HTTPException(409,str(exc)) from exc
            return JSONResponse({'task_id':task_id,'status':'queued'},status_code=202)
        # Injected runtimes can use the synchronous facade in tests/embedded use.
        return await server.create_session(RunStartRequest(
            scenario_key=scenario_key,
            request_idempotency_key=request.request_idempotency_key,
            input=UserQuery(query=request.query),
        ), auth=auth)

    @app.get("/v1/runs/{run_id}")
    async def get_run(run_id: str, auth=Depends(authenticate)):
        return await outcome(run_id, auth)

    if service:
        from .ingress import install_ingress
        install_ingress(app,service,authenticate)
    return app
