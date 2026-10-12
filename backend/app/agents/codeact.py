"""Bounded CodeAct execution over Synth's existing sandbox runtime.

The model proposes code; the sandbox is the only execution surface.  Every
iteration is recorded, output is capped, repeated code is stopped, and an
artifact must be explicitly emitted by the sandbox program using the
``SYNTH_ARTIFACT`` JSON marker.
"""

from __future__ import annotations

import json
import mimetypes
import re
from dataclasses import dataclass
from typing import Any

from app.core.llm_json import parse_llm_json
from app.core.cost_accounting import record_model_cost
from app.sandbox.manager import SandboxManager, get_active_sandbox_manager
from app.services.artifact_contract import signed_download_url
from app.services.attachment_store import MAX_FILE_SIZE, attachment_store
from app.tools.registry import BaseTool, ToolSchema, ToolRegistry


@dataclass(frozen=True)
class CodeActConfig:
    max_iterations: int = 8
    max_code_chars: int = 20_000
    max_output_chars: int = 12_000
    timeout_seconds: int = 45


class CodeActRunner:
    def __init__(self, model_router: Any, sandbox_manager: SandboxManager | None = None, config: CodeActConfig | None = None, tool_registry: ToolRegistry | None = None) -> None:
        self.model_router = model_router
        self.sandbox_manager = sandbox_manager
        self.config = config or CodeActConfig()
        self.tool_registry = tool_registry

    async def run(self, goal: str, *, task_id: str, context: dict[str, Any] | None = None, state: dict[str, Any] | None = None) -> dict[str, Any]:
        manager = self.sandbox_manager or get_active_sandbox_manager()
        if manager is None:
            raise RuntimeError("CodeAct requires a configured task-scoped sandbox manager")
        await manager.create(task_id, template="code-interpreter-v1", timeout=self.config.timeout_seconds * 4)
        trace: list[dict[str, Any]] = []
        artifacts: list[dict[str, Any]] = []
        previous_code: set[str] = set()
        sdk = self.tool_registry.describe_all()[:20] if self.tool_registry is not None else []
        messages: list[dict[str, str]] = [
            {"role": "system", "content": "You are Synth CodeAct. Return either a JSON object with code/done or a fenced python block. Use only the bounded SDK below for tool calls. Do not access the host filesystem. " + json.dumps({"sdk": sdk, "steps_remaining": self.config.max_iterations, "cost_remaining": 2.0}, default=str)},
            {"role": "user", "content": json.dumps({"goal": goal, "context": context or {}}, default=str)},
        ]

        for iteration in range(self.config.max_iterations):
            response = await self.model_router.route("codeact", messages)
            if isinstance(state, dict):
                record_model_cost(state, response, role="code_execution")
            proposal = self._parse_proposal(getattr(response, "content", response))
            if not isinstance(proposal, dict):
                raise ValueError("CodeAct model response must be an object")
            if proposal.get("done"):
                return {"status": "completed", "answer": str(proposal.get("answer") or ""), "trace": trace, "artifacts": artifacts}
            tool_call = proposal.get("tool_call")
            if isinstance(tool_call, dict):
                if self.tool_registry is None:
                    raise RuntimeError("CodeAct tool calls require a registry")
                tool_name = str(tool_call.get("name") or "")
                tool = self.tool_registry.get(tool_name)
                if tool is None:
                    raise ValueError(f"Unknown CodeAct tool: {tool_name}")
                args = dict(tool_call.get("args") or {})
                tool.validate(args)
                if bool(tool.schema.approval_required if tool.schema.approval_required is not None else tool.schema.irreversible):
                    return {"status": "approval_required", "answer": f"Approval is required before calling {tool_name}.", "trace": trace, "artifacts": artifacts, "approval": {"tool_name": tool_name, "tool_args": args}}
                result = await tool.execute(**args)
                trace.append({"iteration": iteration + 1, "type": "tool_call", "tool_name": tool_name, "result": result})
                messages.append({"role": "user", "content": json.dumps({"tool_result": result}, default=str)})
                continue
            code = str(proposal.get("code") or "")
            if not code or len(code) > self.config.max_code_chars:
                raise ValueError("CodeAct proposal must contain bounded executable code")
            if code in previous_code:
                return {"status": "stopped", "answer": "CodeAct stopped after repeating the same proposal.", "trace": trace, "artifacts": artifacts}
            previous_code.add(code)
            result = await manager.execute(task_id, code, timeout=self.config.timeout_seconds, max_output_chars=self.config.max_output_chars)
            output = str(result.get("output") or "")
            marker_artifacts = self._extract_artifacts(output)
            materialized_artifacts = await self._materialize_artifacts(
                manager,
                task_id,
                marker_artifacts,
                context or {},
                goal,
            )
            artifacts.extend(materialized_artifacts)
            entry = {"iteration": iteration + 1, "code_sha256": __import__("hashlib").sha256(code.encode()).hexdigest(), "status": result.get("status"), "output": output, "artifacts": marker_artifacts}
            trace.append(entry)
            messages.append({"role": "assistant", "content": json.dumps(proposal, default=str)})
            messages.append({"role": "user", "content": json.dumps({"sandbox_result": result, "artifacts": materialized_artifacts}, default=str)})

        return {"status": "iteration_limit", "answer": "CodeAct stopped after reaching its iteration limit.", "trace": trace, "artifacts": artifacts}

    @staticmethod
    def _parse_proposal(value: Any) -> dict[str, Any]:
        text = str(value or "").strip()
        fenced = re.search(r"```python\s*(.*?)```", text, re.IGNORECASE | re.DOTALL)
        if fenced:
            return {"code": fenced.group(1).strip()}
        parsed = parse_llm_json(text)
        if isinstance(parsed, dict):
            return parsed
        raise ValueError("CodeAct response must be JSON or a fenced Python block")

    @staticmethod
    def _extract_artifacts(output: str) -> list[dict[str, Any]]:
        found: list[dict[str, Any]] = []
        for line in output.splitlines():
            if not line.startswith("SYNTH_ARTIFACT "):
                continue
            try:
                value = json.loads(line.removeprefix("SYNTH_ARTIFACT "))
            except json.JSONDecodeError:
                continue
            if isinstance(value, dict) and value.get("name") and value.get("path"):
                path = str(value["path"])
                if "\x00" in path or len(path) > 1024:
                    continue
                found.append({"name": str(value["name"]), "path": path, "artifact_type": str(value.get("artifact_type") or "file")})
        return found

    async def _materialize_artifacts(
        self,
        manager: SandboxManager,
        task_id: str,
        declarations: list[dict[str, Any]],
        context: dict[str, Any],
        goal: str,
    ) -> list[dict[str, Any]]:
        if not declarations:
            return []
        organization_id = str(context.get("organization_id") or context.get("org_id") or "").strip()
        user_id = str(context.get("user_id") or "").strip()
        if not organization_id or not user_id:
            raise RuntimeError("Sandbox artifacts require an organization and user scope before completion")
        materialized: list[dict[str, Any]] = []
        for declaration in declarations:
            path = str(declaration["path"])
            reader = getattr(manager, "read_file", None)
            if reader is None:
                raise RuntimeError("Sandbox artifacts cannot be materialized by this runtime")
            content = await reader(task_id, path)
            if len(content) > MAX_FILE_SIZE:
                raise RuntimeError(f"Artifact '{declaration['name']}' exceeds the 10 MB limit")
            content_type = mimetypes.guess_type(str(declaration["name"]))[0] or "application/octet-stream"
            metadata = attachment_store.save_generated_bytes(
                organization_id=organization_id,
                user_id=user_id,
                filename=str(declaration["name"]),
                content=content,
                content_type=content_type,
                artifact_type=str(declaration.get("artifact_type") or "generated_file"),
                source_title=goal,
            )
            materialized.append({
                "attachment_id": metadata["id"],
                "name": metadata["name"],
                "content_type": metadata["content_type"],
                "size": metadata["size"],
                "sha256": metadata["sha256"],
                "artifact_type": metadata["artifact_type"],
                "download_url": signed_download_url(metadata["id"]),
                "preview": metadata.get("preview"),
                "provenance": {"sandbox_path": path, "task_id": task_id, "goal": goal[:500]},
                "version": 1,
            })
        return materialized


class CodeActTool(BaseTool):
    def __init__(self, model_router: Any, sandbox_manager: SandboxManager | None = None, tool_registry: ToolRegistry | None = None) -> None:
        self.runner = CodeActRunner(model_router, sandbox_manager, tool_registry=tool_registry)
        super().__init__()

    def _build_schema(self) -> ToolSchema:
        return ToolSchema(
            name="codeact",
            description="Iteratively write and execute bounded Python in the task sandbox, returning observations and declared artifacts.",
            parameters={"goal": {"type": "string"}, "task_id": {"type": "string"}, "context": {"type": "object"}},
            required=["goal", "task_id"],
            sandbox_template="code-interpreter-v1",
            runtime="e2b",
            idempotency="replayable",
            resources=["code"],
            operations=["execute"],
            output_schema={"type": "object", "required": ["status", "trace", "artifacts"]},
        )

    async def execute(self, **kwargs: Any) -> dict[str, Any]:
        return await self.runner.run(str(kwargs["goal"]), task_id=str(kwargs["task_id"]), context=kwargs.get("context"), state=kwargs.get("state"))


class CodeActNode:
    """LangGraph adapter for resource-routed CodeAct execution."""

    def __init__(self, model_router: Any, registry: ToolRegistry, sandbox_manager: SandboxManager | None = None) -> None:
        self.runner = CodeActRunner(model_router, sandbox_manager, tool_registry=registry)

    async def __call__(self, state: dict[str, Any]) -> dict[str, Any]:
        task_id = str(state.get("thread_id") or state.get("task_id") or "default")
        result = await self.runner.run(str(state.get("task") or ""), task_id=task_id, context=state, state=state)
        state.setdefault("results", []).append({"step_index": int(state.get("current_step", 0) or 0), "step": "codeact", "tool": "codeact", "output": result.get("answer", ""), "tool_data": result})
        state.setdefault("artifacts", []).extend(result.get("artifacts") or [])
        state.setdefault("codeact_trace", []).extend(result.get("trace") or [])
        state["final_answer"] = result.get("answer") or "CodeAct completed."
        if result.get("status") == "completed":
            state["status"] = "running"
            state["done"] = False
        elif result.get("status") == "approval_required":
            state["status"] = "waiting_for_approval"
            state["done"] = True
        else:
            state["status"] = "failed"
            state["done"] = True
            state["task_error"] = {"error_code": "codeact_incomplete", "message": state["final_answer"]}
        state["needs_replan"] = False
        state["execution_mode"] = "codeact"
        return state