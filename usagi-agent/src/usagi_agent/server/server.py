"""The public conversational execution facade."""
from __future__ import annotations

from usagi_agent.sessions.types import SessionMessage
from usagi_agent.types.run import CancellationReasonCode, ResumeEnvelope, RunHandle, RunOutcome, RunStartRequest


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
        return await self._runtime._kernel_runtime.create_session(request, auth=auth)

    async def continue_session(
        self, session_id: str, request: RunStartRequest, *, auth=None
    ) -> SessionMessage:
        return await self._runtime._kernel_runtime.continue_session(
            session_id, request, auth=auth
        )

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
        try:
            await self._runtime.shutdown()
        finally:
            self._ready = False
