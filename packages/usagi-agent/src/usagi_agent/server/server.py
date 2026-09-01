"""Thin public execution facade over ServerRuntime."""
from __future__ import annotations

from typing import AsyncIterator

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

    async def start_agent(self, request: RunStartRequest, *, auth=None) -> RunHandle:
        return await self._runtime.kernel_runtime.start_agent(request, auth=auth)

    async def start_workflow(self, request: RunStartRequest, *, auth=None) -> RunHandle:
        return await self._runtime.kernel_runtime.start_workflow(request, auth=auth)

    async def get_run(self, run_id: str, *, auth=None) -> RunOutcome:
        return await self._runtime.kernel_runtime.get_run(run_id, auth=auth)

    async def cancel(self, run_id: str, reason_code: CancellationReasonCode, *, auth=None) -> RunHandle:
        return await self._runtime.kernel_runtime.cancel(run_id, reason_code, auth=auth)

    async def resume(self, run_id: str, resume: ResumeEnvelope, *, auth=None) -> RunHandle:
        return await self._runtime.kernel_runtime.resume(run_id, resume, auth=auth)

    async def issue_resume_token(
        self, run_id: str, interrupt_id: str, expected_checkpoint_id: str, *, auth=None
    ):
        return await self._runtime.kernel_runtime.issue_resume_token(
            run_id, interrupt_id, expected_checkpoint_id, auth=auth
        )

    async def reissue_resume_token(
        self, run_id: str, interrupt_id: str, expected_checkpoint_id: str, *, auth=None
    ):
        return await self._runtime.kernel_runtime.reissue_resume_token(
            run_id, interrupt_id, expected_checkpoint_id, auth=auth
        )

    def stream(self, run_id: str, *, auth=None) -> AsyncIterator:
        return self._runtime.kernel_runtime.stream(run_id, auth=auth)

    async def shutdown(self) -> None:
        try:
            await self._runtime.shutdown()
        finally:
            self._ready = False
