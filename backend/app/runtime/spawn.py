"""Sub-agent spawn, parallel fan-out, and wide_map.

Workers are the same loop (runtime.loop.run) with a different RoleConfig.
Isolated context: worker sees only its goal, input refs, and output schema.
Shared state is only the artifact store under workspace/shared/<worker_id>/.
"""
from __future__ import annotations

import asyncio
import csv
import io
import json
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Awaitable

try:
    import structlog
except ImportError:
    structlog = None  # type: ignore


from app.runtime.roles_v2 import (
    RoleConfig,
    get_role,
    intersect_allowlist,
    LEAD,
)

class _NullLogger:
    def debug(self,*a,**k): pass
    def info(self,*a,**k): pass
    def warning(self,*a,**k): pass
    def exception(self,*a,**k): pass

def _get_logger(name):
    if structlog:
        return structlog.get_logger(name)
    return _NullLogger()

logger = _get_logger("onsite48.runtime.spawn") if structlog else None

# Tunables
DEFAULT_CONCURRENCY = 4
MAX_CONCURRENCY = 16
DEFAULT_PER_ITEM_RETRIES = 2
COST_APPROVAL_THRESHOLD_USD = 2.0
SUMMARY_MAX_CHARS = 2000


@dataclass
class WorkerHandle:
    worker_id: str
    run_id: str
    role: str
    goal: str
    status: str = "pending"  # pending|running|completed|failed|cancelled
    result: dict[str, Any] | None = None
    summary: str = ""
    artifacts: list[str] = field(default_factory=list)
    evidence: list[str] = field(default_factory=list)
    cost_usd: float = 0.0
    error: str | None = None
    parent_run_id: str | None = None
    task: asyncio.Task | None = field(default=None, repr=False)


@dataclass
class SpawnBudget:
    steps: int = 20
    tokens: int = 60_000
    cost_usd: float = 1.0
    wall_s: float = 240.0


def _estimate_cost(n_items: int, per_item_budget: dict) -> float:
    """Conservative preflight cost estimate before fan-out."""
    unit = float(per_item_budget.get("cost_usd") or 0.5)
    return n_items * unit


class SpawnManager:
    """Manages child worker lifecycle for a lead run."""

    def __init__(
        self,
        *,
        parent_run_id: str,
        parent_role: RoleConfig,
        workspace_root: Path,
        loop_runner: Callable[..., Awaitable[dict[str, Any]]] | None = None,
        journal: Any = None,
        depth: int = 0,
        cancel_event: asyncio.Event | None = None,
    ):
        self.parent_run_id = parent_run_id
        self.parent_role = parent_role
        self.workspace_root = Path(workspace_root)
        self.loop_runner = loop_runner  # injected: runtime.loop.run
        self.journal = journal
        self.depth = depth
        self.cancel_event = cancel_event or asyncio.Event()
        self._handles: dict[str, WorkerHandle] = {}
        self._child_tasks: list[asyncio.Task] = []
        self._total_child_cost = 0.0
        self._lock = asyncio.Lock()

    def _shared_dir(self, worker_id: str) -> Path:
        d = self.workspace_root / "shared" / worker_id
        d.mkdir(parents=True, exist_ok=True)
        return d

    def _worker_workspace(self, worker_id: str) -> Path:
        d = self.workspace_root / "workers" / worker_id
        d.mkdir(parents=True, exist_ok=True)
        return d

    async def spawn_agent(
        self,
        role: str,
        goal: str,
        inputs: list[Any] | None = None,
        output_schema: dict[str, Any] | None = None,
        budget: dict[str, Any] | None = None,
    ) -> WorkerHandle:
        """Spawn one worker. Returns handle immediately; work runs in background."""
        if self.depth >= self.parent_role.depth_cap:
            raise RuntimeError(
                f"spawn depth_cap exceeded (depth={self.depth}, cap={self.parent_role.depth_cap})"
            )
        if not self.parent_role.may_spawn:
            raise RuntimeError(f"role {self.parent_role.name!r} may not spawn workers")

        role_cfg = get_role(role)
        # Policy inheritance: never exceed parent grants
        effective_allow = intersect_allowlist(
            self.parent_role.tool_allowlist, role_cfg.tool_allowlist
        )
        effective_role = RoleConfig(
            name=role_cfg.name,
            system_prompt_file=role_cfg.system_prompt_file,
            tool_allowlist=effective_allow,
            model_lane=role_cfg.model_lane,
            budget={**role_cfg.budget, **(budget or {})},
            may_spawn=False,  # depth cap 1 by default
            depth_cap=0,
            max_summary_tokens=role_cfg.max_summary_tokens,
        )

        worker_id = f"w_{uuid.uuid4().hex[:12]}"
        run_id = str(uuid.uuid4())
        handle = WorkerHandle(
            worker_id=worker_id,
            run_id=run_id,
            role=role,
            goal=goal,
            status="pending",
            parent_run_id=self.parent_run_id,
        )
        self._handles[worker_id] = handle

        # Materialize inputs as refs into shared artifact store
        input_refs = await self._materialize_inputs(worker_id, inputs or [])

        task = asyncio.create_task(
            self._run_worker(handle, effective_role, goal, input_refs, output_schema),
            name=f"worker-{worker_id}",
        )
        handle.task = task
        self._child_tasks.append(task)
        return handle

    async def spawn_agents(
        self, specs: list[dict[str, Any]]
    ) -> list[WorkerHandle]:
        handles = []
        for spec in specs:
            h = await self.spawn_agent(
                role=spec["role"],
                goal=spec["goal"],
                inputs=spec.get("inputs"),
                output_schema=spec.get("output_schema"),
                budget=spec.get("budget"),
            )
            handles.append(h)
        return handles

    async def await_agents(
        self,
        handles: list[WorkerHandle] | None = None,
        *,
        timeout_s: float | None = None,
    ) -> list[WorkerHandle]:
        targets = handles if handles is not None else list(self._handles.values())
        tasks = [h.task for h in targets if h.task and not h.task.done()]
        if not tasks:
            return targets
        try:
            await asyncio.wait_for(
                asyncio.gather(*tasks, return_exceptions=True),
                timeout=timeout_s,
            )
        except asyncio.TimeoutError:
            for h in targets:
                if h.status in ("pending", "running"):
                    h.status = "failed"
                    h.error = "await_timeout"
        return targets

    async def cancel_all(self) -> None:
        """Cancel parent → cancel all children."""
        self.cancel_event.set()
        for h in self._handles.values():
            if h.task and not h.task.done():
                h.task.cancel()
                h.status = "cancelled"
        await asyncio.gather(
            *[h.task for h in self._handles.values() if h.task],
            return_exceptions=True,
        )

    def total_child_cost(self) -> float:
        return sum(h.cost_usd for h in self._handles.values())

    async def _materialize_inputs(
        self, worker_id: str, inputs: list[Any]
    ) -> list[dict[str, Any]]:
        shared = self._shared_dir(worker_id)
        refs: list[dict[str, Any]] = []
        for i, item in enumerate(inputs):
            if isinstance(item, dict) and item.get("type") == "file_ref":
                refs.append(item)
                continue
            if isinstance(item, (dict, list)):
                path = shared / f"input_{i}.json"
                path.write_text(json.dumps(item, indent=2, default=str), encoding="utf-8")
                refs.append({"type": "file_ref", "path": str(path), "kind": "json"})
            else:
                path = shared / f"input_{i}.txt"
                path.write_text(str(item), encoding="utf-8")
                refs.append({"type": "file_ref", "path": str(path), "kind": "text"})
        return refs

    async def _run_worker(
        self,
        handle: WorkerHandle,
        role: RoleConfig,
        goal: str,
        input_refs: list[dict[str, Any]],
        output_schema: dict[str, Any] | None,
    ) -> None:
        handle.status = "running"
        ws = self._worker_workspace(handle.worker_id)
        shared = self._shared_dir(handle.worker_id)

        # Build isolated goal prompt — no lead history
        input_desc = "\n".join(
            f"- {r.get('kind', 'ref')}: {r.get('path')}" for r in input_refs
        ) or "(none)"
        schema_desc = (
            json.dumps(output_schema, indent=2)
            if output_schema
            else "Free-form result; keep large content in files under the workspace."
        )
        isolated_goal = (
            f"{goal}\n\n"
            f"## Inputs (file references into shared artifact store)\n{input_desc}\n\n"
            f"## Output schema\n{schema_desc}\n\n"
            f"Write large artifacts under {shared}. "
            f"Return a concise summary and schema-valid result via finish."
        )

        if self.cancel_event.is_set():
            handle.status = "cancelled"
            handle.error = "parent_cancelled"
            return

        try:
            if self.loop_runner is None:
                # Structural / unit-test path: simulate a successful worker
                handle.status = "completed"
                handle.summary = f"[simulated] completed goal for role={role.name}"
                handle.result = {"status": "completed", "goal": goal}
                handle.cost_usd = 0.01
                handle.artifacts = []
                return

            result = await self.loop_runner(
                goal=isolated_goal,
                run_id=handle.run_id,
                parent_run_id=self.parent_run_id,
                role_config=role.to_dict(),
                workspace=ws,
                max_steps=int(role.budget.get("steps", 20)),
                max_tokens=int(role.budget.get("tokens", 60_000)),
                max_cost_usd=float(role.budget.get("cost_usd", 1.0)),
                max_wall_s=float(role.budget.get("wall_s", 240)),
                system_prompt=role.load_system_prompt(),
                tool_allowlist=role.tool_allowlist,
                cancel_event=self.cancel_event,
            )
            handle.cost_usd = float(
                (result.get("state") or {}).get("cost_usd")
                or result.get("cost_usd")
                or 0.0
            )
            status = result.get("status") or "completed"
            handle.status = status if status in (
                "completed", "completed_unverified", "partial", "failed",
                "cancelled", "needs_input",
            ) else "completed"
            fp = result.get("result") or result.get("finish_payload") or {}
            handle.summary = str(fp.get("summary") or "")[:SUMMARY_MAX_CHARS]
            handle.result = fp if isinstance(fp, dict) else {"raw": fp}
            handle.artifacts = list(fp.get("evidence_refs") or [])
            handle.evidence = list(fp.get("evidence_refs") or [])
        except asyncio.CancelledError:
            handle.status = "cancelled"
            handle.error = "cancelled"
            raise
        except Exception as exc:
            handle.status = "failed"
            handle.error = f"{type(exc).__name__}: {exc}"
            logger.exception("worker_failed", worker_id=handle.worker_id, error=str(exc))
        finally:
            async with self._lock:
                self._total_child_cost += handle.cost_usd


async def wide_map(
    items: list[Any],
    role: str,
    instruction_template: str,
    output_schema: dict[str, Any] | None = None,
    concurrency: int = DEFAULT_CONCURRENCY,
    per_item_budget: dict[str, Any] | None = None,
    *,
    parent_run_id: str,
    parent_role: RoleConfig,
    workspace_root: Path,
    loop_runner: Callable[..., Awaitable[dict[str, Any]]] | None = None,
    journal: Any = None,
    depth: int = 0,
    cancel_event: asyncio.Event | None = None,
    cost_approval_threshold: float = COST_APPROVAL_THRESHOLD_USD,
    max_retries: int = DEFAULT_PER_ITEM_RETRIES,
    reduce_role: str | None = None,
    reduce_instruction: str | None = None,
) -> dict[str, Any]:
    """Process N items in parallel with isolated workers.

    Returns:
      {
        "status": "completed"|"partial"|"failed"|"needs_approval",
        "table_path": str,          # CSV or JSONL on disk
        "summary": str,
        "failed_items": [{"index": i, "reason": str}, ...],
        "cost_usd": float,
        "handles": [...],
      }
    """
    if not items:
        return {
            "status": "completed",
            "table_path": None,
            "summary": "No items to process.",
            "failed_items": [],
            "cost_usd": 0.0,
            "handles": [],
        }

    concurrency = max(1, min(int(concurrency or DEFAULT_CONCURRENCY), MAX_CONCURRENCY))
    per_budget = dict(per_item_budget or get_role(role).budget)
    estimated = _estimate_cost(len(items), per_budget)

    if estimated > cost_approval_threshold:
        return {
            "status": "needs_approval",
            "table_path": None,
            "summary": (
                f"Cost preflight: estimated ${estimated:.2f} for {len(items)} items "
                f"(threshold ${cost_approval_threshold:.2f}). Approve to proceed."
            ),
            "failed_items": [],
            "cost_usd": 0.0,
            "estimated_cost_usd": estimated,
            "handles": [],
            "requires_approval": True,
        }

    manager = SpawnManager(
        parent_run_id=parent_run_id,
        parent_role=parent_role,
        workspace_root=workspace_root,
        loop_runner=loop_runner,
        journal=journal,
        depth=depth,
        cancel_event=cancel_event or asyncio.Event(),
    )

    sem = asyncio.Semaphore(concurrency)
    results: list[dict[str, Any] | None] = [None] * len(items)
    failed: list[dict[str, Any]] = []

    async def _one(idx: int, item: Any) -> None:
        async with sem:
            if manager.cancel_event.is_set():
                failed.append({"index": idx, "reason": "parent_cancelled"})
                return
            instruction = instruction_template.format(
                item=item if not isinstance(item, (dict, list)) else json.dumps(item),
                index=idx,
            )
            last_err = None
            for attempt in range(max_retries + 1):
                try:
                    handle = await manager.spawn_agent(
                        role=role,
                        goal=instruction,
                        inputs=[item],
                        output_schema=output_schema,
                        budget=per_budget,
                    )
                    await manager.await_agents([handle])
                    if handle.status in ("completed", "completed_unverified", "partial"):
                        results[idx] = {
                            "index": idx,
                            "status": handle.status,
                            "summary": handle.summary,
                            "result": handle.result,
                            "artifacts": handle.artifacts,
                            "cost_usd": handle.cost_usd,
                        }
                        return
                    last_err = handle.error or handle.status
                except Exception as exc:
                    last_err = f"{type(exc).__name__}: {exc}"
                await asyncio.sleep(0.25 * (attempt + 1))
            failed.append({"index": idx, "reason": str(last_err)})
            results[idx] = {
                "index": idx,
                "status": "failed",
                "summary": "",
                "result": None,
                "error": str(last_err),
                "cost_usd": 0.0,
            }

    await asyncio.gather(*[_one(i, it) for i, it in enumerate(items)])

    # Write table file
    out_dir = Path(workspace_root) / "shared" / "wide_map"
    out_dir.mkdir(parents=True, exist_ok=True)
    table_path = out_dir / f"wide_map_{parent_run_id[:8]}_{int(time.time())}.jsonl"
    with table_path.open("w", encoding="utf-8") as f:
        for row in results:
            if row is not None:
                f.write(json.dumps(row, default=str) + "\n")

    # Also CSV summary for convenience
    csv_path = table_path.with_suffix(".csv")
    with csv_path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(
            f, fieldnames=["index", "status", "summary", "cost_usd", "error"]
        )
        writer.writeheader()
        for row in results:
            if row is None:
                continue
            writer.writerow(
                {
                    "index": row.get("index"),
                    "status": row.get("status"),
                    "summary": (row.get("summary") or "")[:300],
                    "cost_usd": row.get("cost_usd", 0),
                    "error": row.get("error", ""),
                }
            )

    total_cost = manager.total_child_cost()
    n_ok = sum(1 for r in results if r and r.get("status") in ("completed", "completed_unverified", "partial"))
    status = (
        "completed" if not failed
        else ("partial" if n_ok else "failed")
    )
    summary = (
        f"wide_map: {n_ok}/{len(items)} succeeded, {len(failed)} failed, "
        f"cost=${total_cost:.4f}. Table: {table_path.name}"
    )

    out: dict[str, Any] = {
        "status": status,
        "table_path": str(table_path),
        "csv_path": str(csv_path),
        "summary": summary,
        "failed_items": failed,
        "cost_usd": total_cost,
        "n_ok": n_ok,
        "n_total": len(items),
        "handles": [
            {
                "worker_id": h.worker_id,
                "run_id": h.run_id,
                "status": h.status,
                "cost_usd": h.cost_usd,
                "error": h.error,
            }
            for h in manager._handles.values()
        ],
    }

    # Optional reduce step by writer/critic over the table file (by reference)
    if reduce_role and status in ("completed", "partial") and n_ok > 0:
        reduce_goal = (
            (reduce_instruction or "Summarize and reconcile the table into a final report.")
            + f"\n\nTable file (JSONL): {table_path}\nCSV: {csv_path}"
        )
        rh = await manager.spawn_agent(
            role=reduce_role,
            goal=reduce_goal,
            inputs=[{"type": "file_ref", "path": str(table_path), "kind": "jsonl"}],
            output_schema=None,
        )
        await manager.await_agents([rh])
        out["reduce"] = {
            "status": rh.status,
            "summary": rh.summary,
            "cost_usd": rh.cost_usd,
            "artifacts": rh.artifacts,
        }
        out["cost_usd"] = manager.total_child_cost()

    return out
