"""The public conversational execution facade."""
from __future__ import annotations

import logging

from usagi_agent.agents import AgentSpec, OutputContract, OutputSchemaDefinition
from usagi_agent.api.errors import DuplicateToolError, UnknownToolError
from usagi_agent.observability import operation
from usagi_agent.sessions.types import SessionMessage
from usagi_agent.tools.builtin import StructuredOutputTool
from usagi_agent.types.model import ModelSpec
from usagi_agent.types.refs import SchemaRef
from usagi_agent.types.run import CancellationReasonCode, ResumeEnvelope, RunHandle, RunOutcome, RunStartRequest

log = logging.getLogger(__name__)


class Server:
    def __init__(self, runtime) -> None:
        self._runtime = runtime
        self._ready = True

    @property
    def ready(self) -> bool:
        if not self._ready:
            return False
        try:
            self._runtime.validate_ready()
        except RuntimeError:
            return False
        return True

    @property
    def runtime(self):
        return self._runtime

    @property
    def erasure(self):
        return self._runtime.erasure_coordinator

    def _install_output_schema(
        self, output_schema: OutputSchemaDefinition
    ) -> OutputContract:
        contract = self._runtime.agent_manager.compile_output_schema(
            output_schema.ref,
            name=output_schema.name,
            schema=output_schema.json_schema,
            max_retries=output_schema.max_retries,
        )
        adapter = StructuredOutputTool(
            name=contract.terminal_tool_name,
            schema=contract.json_schema,
            schema_name=contract.name,
            schema_checksum=contract.schema_checksum,
        )
        try:
            registered = self._runtime.tool_manager.get_spec(
                contract.terminal_tool_name
            )
        except UnknownToolError:
            self._runtime.tool_manager.register(adapter)
        else:
            if registered != adapter.spec:
                raise DuplicateToolError(contract.terminal_tool_name)
        return contract

    def create_agent(
        self,
        *,
        id: str,
        input_schema: SchemaRef,
        model: ModelSpec,
        output_schema: OutputSchemaDefinition | None = None,
        allowed_tools: tuple[str, ...] = (),
    ) -> AgentSpec:
        """Register an Agent, including its initial output schema when supplied."""
        effective_tools = list(allowed_tools)
        output_schema_ref = None
        if output_schema is not None:
            contract = self._install_output_schema(output_schema)
            output_schema_ref = output_schema.ref
            effective_tools.append(contract.terminal_tool_name)
        return self._runtime.agent_manager.register(
            AgentSpec(
                id=id,
                input_schema=input_schema,
                output_schema=output_schema_ref,
                model=model,
                allowed_tools=tuple(dict.fromkeys(effective_tools)),
            )
        )

    async def create_session(
        self, request: RunStartRequest, *, auth=None
    ) -> SessionMessage:
        self._runtime.validate_ready()
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
        self._runtime.validate_ready()
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
