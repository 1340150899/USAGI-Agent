"""Channel-neutral approval gate. Graph orchestration owns interrupt/resume."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Literal, TypeGuard, TypedDict

from usagi_agent.persistence.ports.approval import ApprovalStore, ApprovalTask
from usagi_agent.types.refs import ArtifactOwner


class ApprovalDecision(TypedDict):
    kind: str
    approval_id: str
    action_hash: str
    approval_scope: object
    expected_approval_version: int
    decision: Literal["approve", "reject"]


class ToolApprovalRequired(Exception):
    def __init__(self, task: ApprovalTask):
        super().__init__(f"approval required: {task.approval_id}")
        self.task = task

    def payload(self) -> dict:
        task = self.task
        return {
            "kind": "approval", "approval_id": task.approval_id,
            "approval_version": task.version, "approval_scope": task.approval_scope,
            "action_hash": task.action_hash, "tool_name": task.tool_name,
            "arguments_ref": task.arguments_ref.model_dump(mode="json") if task.arguments_ref else None,
        }


class ToolApprovalGate:
    def __init__(self, store: ApprovalStore | None, artifact_manager=None):
        self.store = store
        self.artifacts = artifact_manager

    async def check(self, *, name, arguments, context, operation_id, approval_result=None) -> bool:
        if self.store is None:
            return False  # Direct callers without approval infrastructure fail closed.
        digest = hashlib.sha256(json.dumps({
            "tenant": context.execution.tenant_id,
            "run": context.execution.control_id,
            "operation": operation_id, "tool": name, "arguments": arguments,
        }, sort_keys=True, ensure_ascii=False, allow_nan=False).encode()).hexdigest()
        operation_digest = hashlib.sha256(json.dumps([
            context.execution.tenant_id, context.execution.control_id, operation_id,
        ]).encode()).hexdigest()
        approval_id = f"approval_{operation_digest}"
        arguments_ref = None
        existing = await self.store.get(approval_id)
        if existing is None and self.artifacts is not None:
            async def chunks():
                yield json.dumps(arguments, ensure_ascii=False).encode()
            arguments_ref = await self.artifacts.put(
                operation_id=f"{approval_id}:arguments",
                owner=ArtifactOwner(tenant_id=context.execution.tenant_id,
                                    erasure_scope_id=context.execution.control_id),
                lineage=[], payload=chunks(), purpose="run_execution",
            )
        task = await self.store.get_or_create(ApprovalTask(
            approval_id=approval_id, approval_operation_id=approval_id,
            interrupt_id=approval_id, run_id=context.execution.control_id,
            session_id=context.execution.session_id,
            action_hash=digest, approval_scope=("tool.execute", name),
            tool_name=name, status="pending", version=0,
            arguments_ref=arguments_ref,
            created_at=datetime.now(timezone.utc),
        ))
        if task.action_hash != digest:
            return False  # One operation cannot silently change its approved arguments.
        if task.expires_at and task.expires_at <= datetime.now(timezone.utc):
            return False
        # LangGraph restarts an interrupted node from its beginning. Previously
        # decided approvals must interrupt again when no decision payload is
        # supplied so the saved resume value is consumed at the same call
        # position. Skipping a rejected approval shifts every later interrupt.
        if task.status == "pending" or (
            task.status in ("approved", "rejected") and approval_result is None
        ):
            raise ToolApprovalRequired(task)
        return (task.status == "approved" and self.matches(approval_result, task, decided=True)
                and approval_result["decision"] == "approve")

    @staticmethod
    def matches(
        resumed: object, task: ApprovalTask, *, decided: bool = False
    ) -> TypeGuard[ApprovalDecision]:
        return isinstance(resumed, dict) and all((
            resumed.get("kind") == "approval",
            resumed.get("approval_id") == task.approval_id,
            resumed.get("action_hash") == task.action_hash,
            tuple(resumed.get("approval_scope", ())) == task.approval_scope,
            resumed.get("decision") in ("approve", "reject"),
            resumed.get("expected_approval_version") == task.version - int(decided),
        ))

    async def decide(self, request: ToolApprovalRequired, resumed: object) -> bool:
        task = request.task
        if task.status in ("approved", "rejected"):
            return (
                self.matches(resumed, task, decided=True)
                and task.status == "approved"
                and resumed["decision"] == "approve"
            )
        if not self.matches(resumed, task):
            return False
        assert self.store is not None
        decided = await self.store.cas_decide(
            task.approval_id, expected_version=task.version,
            decision=resumed["decision"], evidence_ref=None,
        )
        return decided.status == "approved"
