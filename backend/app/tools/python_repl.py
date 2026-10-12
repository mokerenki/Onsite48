"""PythonREPLTool backed by an E2B sandbox manager."""

from typing import Optional

from app.sandbox.manager import SandboxManager, get_active_sandbox_manager, get_active_task_id
from app.tools.registry import BaseTool, ToolSchema


class PythonREPLTool(BaseTool):
    def __init__(self, sandbox_manager: Optional[SandboxManager] = None):
        self.sandbox_manager = sandbox_manager
        super().__init__()

    def _build_schema(self) -> ToolSchema:
        return ToolSchema(
            name="python_repl",
            description="Execute Python code and capture stdout.",
            parameters={
                "code": {"type": "string", "description": "Python code to execute."},
            },
            required=["code"],
            sandbox_template="terminal-sandbox",
        )

    async def execute(self, **kwargs):
        code = kwargs.get("code", "")
        task_id = kwargs.get("task_id") or kwargs.get("thread_id") or get_active_task_id() or "default"
        sandbox_manager = self.sandbox_manager or get_active_sandbox_manager()

        if not sandbox_manager:
            return {"output": "Sandbox manager is not configured."}

        try:
            await sandbox_manager.create(task_id)
            result = await sandbox_manager.execute(task_id, code)
            return {"output": result.get("output", "")}
        except Exception as exc:
            return {"output": f"Sandbox execution failed: {exc}"}