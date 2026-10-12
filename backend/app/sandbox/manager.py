"""SandboxManager backed by E2B code-interpreter sandboxes."""

import asyncio
import inspect
import os
from contextvars import ContextVar
from typing import Any, Dict, Optional

import structlog
from dotenv import load_dotenv
from e2b_code_interpreter import Sandbox

load_dotenv()

logger = structlog.get_logger("aether.sandbox.manager")


def _get_e2b_api_key() -> str:
    raw_key = os.getenv("E2B_API_KEY", "").strip()
    if not raw_key:
        return ""

    raw_key = raw_key.strip('"').strip("'")
    if " " in raw_key:
        raw_key = raw_key.split()[0]
    return raw_key


active_sandbox_manager: ContextVar[Optional["SandboxManager"]] = ContextVar("active_sandbox_manager", default=None)
active_task_id: ContextVar[Optional[str]] = ContextVar("active_task_id", default=None)


def set_active_sandbox_manager(manager: Optional["SandboxManager"]) -> None:
    active_sandbox_manager.set(manager)


def get_active_sandbox_manager() -> Optional["SandboxManager"]:
    return active_sandbox_manager.get()


def set_active_task_id(task_id: Optional[str]) -> None:
    active_task_id.set(task_id)


def get_active_task_id() -> Optional[str]:
    return active_task_id.get()


class SandboxManager:
    """Manage task-scoped E2B sandboxes for code execution."""

    def __init__(self, runtime: str = "e2b", namespace: str = "aether-sandboxes"):
        self.runtime = runtime
        self.namespace = namespace
        self._sandboxes: Dict[str, Sandbox] = {}
        self._contexts: Dict[str, Any] = {}

    async def create(self, task_id: str, template: Optional[str] = None, timeout: int = 300) -> str:
        """Create an E2B sandbox for a task and cache it by task id."""
        api_key = _get_e2b_api_key()
        if not api_key:
            raise RuntimeError("E2B_API_KEY is not configured")

        os.environ["E2B_API_KEY"] = api_key

        if task_id in self._sandboxes:
            return task_id

        selected_template = template or "code-interpreter-v1"
        try:
            sandbox = Sandbox.create(template=selected_template, timeout=timeout, metadata={"task_id": task_id})
        except Exception as exc:  # pragma: no cover - defensive fallback
            logger.exception("sandbox.create_failed", task_id=task_id, error=str(exc))
            raise RuntimeError(f"Unable to create sandbox: {exc}") from exc

        self._sandboxes[task_id] = sandbox
        try:
            context = sandbox.create_code_context()
            self._contexts[task_id] = context
        except Exception as exc:  # pragma: no cover - defensive fallback
            logger.warning("sandbox.context_failed", task_id=task_id, error=str(exc))
        logger.info("sandbox.created", task_id=task_id, sandbox_id=sandbox.sandbox_id)
        return task_id

    async def execute(self, sandbox_id: str, command: str, timeout: int = 30, max_output_chars: int = 12000) -> Dict[str, Any]:
        """Execute code or a shell command in the sandbox and return structured output."""
        sandbox = self._sandboxes.get(sandbox_id)
        if sandbox is None:
            raise RuntimeError(f"Sandbox '{sandbox_id}' does not exist")

        context = self._contexts.get(sandbox_id)
        max_attempts = 2  # one retry for a genuine transient blip, not four
        last_error: Optional[Exception] = None

        for attempt in range(max_attempts):
            try:
                execution = sandbox.run_code(
                    command,
                    timeout=timeout,
                    request_timeout=45,  # was 180 -- this is network/transport timeout, not code runtime
                    context=context,
                )
                break
            except Exception as exc:
                last_error = exc
                logger.warning(
                    "sandbox.execute_attempt_failed",
                    sandbox_id=sandbox_id,
                    attempt=attempt,
                    error=str(exc),
                )
                if attempt < max_attempts - 1:
                    await asyncio.sleep(1)  # was 3
                    continue
                logger.exception("sandbox.execute_failed", sandbox_id=sandbox_id, error=str(exc))
                raise RuntimeError(f"Sandbox execution failed: {exc}") from exc

        stdout = ""
        stderr = ""
        logs = getattr(execution, "logs", None)
        if logs is not None:
            stdout_value = getattr(logs, "stdout", "") or ""
            stderr_value = getattr(logs, "stderr", "") or ""
            if isinstance(stdout_value, list):
                stdout = "".join(str(item) for item in stdout_value)
            else:
                stdout = str(stdout_value)
            if isinstance(stderr_value, list):
                stderr = "".join(str(item) for item in stderr_value)
            else:
                stderr = str(stderr_value)

        output = (stdout or "") + (stderr or "")
        if len(output) > max_output_chars:
            output = output[:max_output_chars] + "\n...[output truncated]..."

        return {
            "sandbox_id": sandbox_id,
            "output": output or (execution.text or ""),
            "stdout": stdout,
            "stderr": stderr,
            "exit_code": 0 if not execution.error else 1,
            "error": getattr(execution.error, "message", None) if execution.error else None,
            "status": "ok" if not execution.error else "error",
        }

    async def read_file(self, sandbox_id: str, path: str) -> bytes:
        """Read a file from the task sandbox without exposing host paths."""
        sandbox = self._sandboxes.get(sandbox_id)
        if sandbox is None:
            raise RuntimeError(f"Sandbox '{sandbox_id}' does not exist")
        files = getattr(sandbox, "files", None)
        reader = getattr(files, "read", None) or getattr(files, "read_bytes", None)
        if reader is None:
            raise RuntimeError("The sandbox runtime does not support file reads")
        value = reader(path)
        if inspect.isawaitable(value):
            value = await value
        if isinstance(value, bytes):
            return value
        if isinstance(value, str):
            return value.encode("utf-8")
        raise RuntimeError("The sandbox returned an unsupported file representation")

    async def destroy(self, sandbox_id: str) -> None:
        """Destroy a sandbox and remove it from the active cache."""
        sandbox = self._sandboxes.pop(sandbox_id, None)
        if sandbox is None:
            return

        try:
            sandbox.kill()
        except Exception as exc:  # pragma: no cover - defensive fallback
            logger.warning("sandbox.destroy_failed", sandbox_id=sandbox_id, error=str(exc))

    async def list_active(self) -> Dict[str, Any]:
        """Return active sandbox metadata."""
        return {
            "count": len(self._sandboxes),
            "sandboxes": [
                {
                    "sandbox_id": sandbox_id,
                    "runtime": self.runtime,
                }
                for sandbox_id in self._sandboxes.keys()
            ],
        }