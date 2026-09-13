"""The public conversational execution facade."""
from __future__ import annotations

import logging

from usagi_agent.observability import operation
from usagi_agent.sessions.types import SessionMessage
from usagi_agent.types.run import CancellationReasonCode, ResumeEnvelope, RunHandle, RunOutcome, RunStartRequest

log = logging.getLogger(__name__)


class Server:
    def __init__(self, runtime) -> None:
        runtime.validate_ready()
        self._runtime = runtime
        self._ready = True

    @property
    def ready(self) -> bool:
        return self._ready

    @property
    def runtime(self):
        return self._runtime

    @property
    def erasure(self):
        return self._runtime.erasure_coordinator

    async def create_session(
        self, request: RunStartRequest, *, auth=None
    ) -> SessionMessage:
        log.info("agent_interface_called operation=create_session scenario=%s", request.scenario_key)
        with operation(
            self._runtime.observability, "agent.request",
            **{"usagi.request.operation": "create_session", "usagi.scenario.name": request.scenario_key},
        ) as telemetry:
            try:
                result = await self._runtime._kernel_runtime.create_session(request, auth=auth)
            except Exception as exc:
                log.exception("agent_request_failed operation=create_session error_type=%s", type(exc).__name__)
                raise
            telemetry.set_outcome(result.outcome.kind)
        log.info("agent_result_returned operation=create_session outcome=%s run_id=%s", result.outcome.kind, result.run_id)
        return result

    async def continue_session(
        self, session_id: str, request: RunStartRequest, *, auth=None
    ) -> SessionMessage:
        log.info("agent_interface_called operation=continue_session session_id=%s", session_id)
        with operation(
            self._runtime.observability, "agent.request",
            **{"usagi.request.operation": "continue_session", "usagi.scenario.name": request.scenario_key},
        ) as telemetry:
            try:
                result = await self._runtime._kernel_runtime.continue_session(
                    session_id, request, auth=auth
                )
            except Exception as exc:
                log.exception("agent_request_failed operation=continue_session error_type=%s", type(exc).__name__)
                raise
            telemetry.set_outcome(result.outcome.kind)
        log.info("agent_result_returned operation=continue_session outcome=%s run_id=%s", result.outcome.kind, result.run_id)
        return result

    async def get_run(self, run_id: str, *, auth=None) -> RunOutcome:
        return await self._runtime._kernel_runtime.get_run(run_id, auth=auth)

    async def cancel(self, run_id: str, reason_code: CancellationReasonCode, *, auth=None) -> RunHandle:
        return await self._runtime._kernel_runtime.cancel(run_id, reason_code, auth=auth)

    async def resume(self, run_id: str, resume: ResumeEnvelope, *, auth=None) -> RunHandle:
        return await self._runtime._kernel_runtime.resume(run_id, resume, auth=auth)

    async def issue_resume_token(
        self, run_id: str, interrupt_id: str, expected_checkpoint_id: str, *, auth=None
    ):
        return await self._runtime._kernel_runtime.issue_resume_token(
            run_id, interrupt_id, expected_checkpoint_id, auth=auth
        )

    async def shutdown(self) -> None:
        log.info("agent_framework_shutdown_started")
        try:
            with operation(
                self._runtime.observability,
                "service.lifecycle",
                **{"usagi.lifecycle.phase": "shutdown_started"},
            ):
                pass
            await self._runtime.shutdown()
        except Exception as exc:
            log.exception(
                "agent_framework_shutdown_failed error_type=%s", type(exc).__name__
            )
            raise
        else:
            log.info("agent_framework_shutdown_complete")
        finally:
            self._ready = False
