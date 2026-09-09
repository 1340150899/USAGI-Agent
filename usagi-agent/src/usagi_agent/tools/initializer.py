"""Bootstrap construction for the tool system (init-only, no run logic)."""
from __future__ import annotations

from usagi_agent.tools.builtin import builtin_tools
from usagi_agent.tools.manager import ToolManager


class ToolInitializer:
    @staticmethod
    def init(persistence, *, specs=None) -> ToolManager:
        """Build the ToolManager with builtin tools and the execution store.

        Spec consistency (risk vs write_safety, §21.3) is enforced by
        ``ToolManager.register`` at construction time, so a bad declaration
        fails bootstrap rather than a run.
        """
        manager = ToolManager(
            execution_store=persistence.tool_execution_store,
            artifact_manager=persistence.artifact_manager,
            approval_store=persistence.approval_store,
        )
        manager.register_many(
            builtin_tools(
                persistence.artifact_manager, persistence.artifact_metadata_store, specs=specs,
            )
        )
        return manager
