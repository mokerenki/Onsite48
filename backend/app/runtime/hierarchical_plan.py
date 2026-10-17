"""Phase 2 — Hierarchical plans for the agent runtime.

A hierarchical plan is a DAG of nodes:

    goal
      └── subgoal (0..n)
            └── step (0..n)   each step may depend_on other step ids

Flat plans (a single list of steps) are supported as a degenerate hierarchy
with one implicit subgoal. The structure is the contract between the planner
and the executor: the model sees a compact active window; the runtime tracks
status so replans and resumes stay consistent.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Iterable, Literal

NodeStatus = Literal["pending", "ready", "running", "done", "failed", "skipped"]
NodeKind = Literal["goal", "subgoal", "step"]


@dataclass
class PlanNode:
    id: str
    kind: NodeKind
    description: str
    parent_id: str | None = None
    depends_on: list[str] = field(default_factory=list)
    status: NodeStatus = "pending"
    tool_name: str | None = None
    tool_args: dict[str, Any] = field(default_factory=dict)
    expected_outcome: str | None = None
    success_condition: str | None = None
    notes: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class HierarchicalPlan:
    goal: str
    nodes: dict[str, PlanNode] = field(default_factory=dict)
    success_conditions: list[str] = field(default_factory=list)
    version: int = 1

    # ----- construction -------------------------------------------------

    @classmethod
    def from_flat_steps(
        cls,
        goal: str,
        steps: Iterable[dict[str, Any]],
        *,
        success_conditions: list[str] | None = None,
    ) -> "HierarchicalPlan":
        """Build a hierarchy with one implicit subgoal wrapping a flat step list."""
        plan = cls(goal=goal or "", success_conditions=list(success_conditions or []))
        plan.nodes["goal"] = PlanNode(id="goal", kind="goal", description=goal or "")
        plan.nodes["sg_main"] = PlanNode(
            id="sg_main",
            kind="subgoal",
            description="Main plan",
            parent_id="goal",
        )
        for i, raw in enumerate(steps or []):
            if not isinstance(raw, dict):
                continue
            sid = str(raw.get("step_id") or raw.get("id") or f"s{i+1}")
            deps = [str(d) for d in (raw.get("depends_on") or []) if d]
            plan.nodes[sid] = PlanNode(
                id=sid,
                kind="step",
                description=str(raw.get("description") or raw.get("title") or sid),
                parent_id="sg_main",
                depends_on=deps,
                tool_name=raw.get("tool_name") or raw.get("tool"),
                tool_args=dict(raw.get("tool_args") or raw.get("args") or {}),
                expected_outcome=raw.get("expected_outcome"),
            )
        plan._refresh_ready()
        return plan

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> "HierarchicalPlan":
        """Build from a ModelPlanPayload-shaped dict (sub_goals + optional flat plan)."""
        if not isinstance(payload, dict):
            raise ValueError("payload must be a dict")
        goal = str(payload.get("refined_goal") or payload.get("goal") or "").strip()
        plan = cls(
            goal=goal,
            success_conditions=[str(s) for s in (payload.get("success_conditions") or []) if s],
        )
        plan.nodes["goal"] = PlanNode(id="goal", kind="goal", description=goal or "(goal)")

        sub_goals = payload.get("sub_goals") or []
        flat = payload.get("plan") or payload.get("steps") or []

        if sub_goals:
            for i, sg in enumerate(sub_goals):
                if not isinstance(sg, dict):
                    continue
                sgid = str(sg.get("id") or f"sg{i+1}")
                plan.nodes[sgid] = PlanNode(
                    id=sgid,
                    kind="subgoal",
                    description=str(sg.get("description") or sgid),
                    parent_id="goal",
                    success_condition=sg.get("success_condition"),
                )
                for j, step in enumerate(sg.get("steps") or []):
                    if not isinstance(step, dict):
                        continue
                    sid = str(step.get("step_id") or step.get("id") or f"{sgid}_s{j+1}")
                    plan.nodes[sid] = PlanNode(
                        id=sid,
                        kind="step",
                        description=str(step.get("description") or sid),
                        parent_id=sgid,
                        depends_on=[str(d) for d in (step.get("depends_on") or []) if d],
                        tool_name=step.get("tool_name") or step.get("tool"),
                        tool_args=dict(step.get("tool_args") or step.get("args") or {}),
                        expected_outcome=step.get("expected_outcome"),
                    )
        elif flat:
            return cls.from_flat_steps(goal, flat, success_conditions=plan.success_conditions)
        else:
            # Empty plan — goal only
            pass

        plan.validate()
        plan._refresh_ready()
        return plan

    # ----- validation ---------------------------------------------------

    def validate(self) -> None:
        """Raise ValueError on unknown deps or cycles among step nodes."""
        step_ids = {n.id for n in self.nodes.values() if n.kind == "step"}
        for n in self.nodes.values():
            if n.kind != "step":
                continue
            unknown = set(n.depends_on) - step_ids
            if unknown:
                raise ValueError(f"step '{n.id}' depends on unknown step(s): {sorted(unknown)}")
        # Cycle detection
        graph = {n.id: set(n.depends_on) for n in self.nodes.values() if n.kind == "step"}
        visiting: set[str] = set()
        visited: set[str] = set()

        def visit(sid: str) -> None:
            if sid in visited:
                return
            if sid in visiting:
                raise ValueError("plan dependencies contain a cycle")
            visiting.add(sid)
            for dep in graph.get(sid, ()):
                visit(dep)
            visiting.remove(sid)
            visited.add(sid)

        for sid in graph:
            visit(sid)

    # ----- status / advancement -----------------------------------------

    def _deps_satisfied(self, node: PlanNode) -> bool:
        for dep in node.depends_on:
            d = self.nodes.get(dep)
            if d is None or d.status not in ("done", "skipped"):
                return False
        return True

    def _refresh_ready(self) -> None:
        for n in self.nodes.values():
            if n.kind != "step":
                continue
            if n.status in ("done", "failed", "skipped", "running"):
                continue
            n.status = "ready" if self._deps_satisfied(n) else "pending"

        # Roll up subgoal / goal status
        for n in self.nodes.values():
            if n.kind != "subgoal":
                continue
            children = [c for c in self.nodes.values() if c.parent_id == n.id and c.kind == "step"]
            if not children:
                continue
            if all(c.status == "done" for c in children):
                n.status = "done"
            elif any(c.status == "failed" for c in children):
                n.status = "failed"
            elif any(c.status == "running" for c in children):
                n.status = "running"
            elif any(c.status in ("ready", "running", "done") for c in children):
                n.status = "ready" if n.status == "pending" else n.status

        goal = self.nodes.get("goal")
        if goal:
            subs = [c for c in self.nodes.values() if c.parent_id == "goal"]
            steps = [c for c in self.nodes.values() if c.kind == "step"]
            pool = subs or steps
            if pool and all(c.status == "done" for c in pool):
                goal.status = "done"
            elif any(c.status == "failed" for c in pool):
                goal.status = "failed"
            elif any(c.status in ("running", "ready", "done") for c in pool):
                goal.status = "running"

    def ready_steps(self) -> list[PlanNode]:
        self._refresh_ready()
        return [n for n in self.nodes.values() if n.kind == "step" and n.status == "ready"]

    def mark_running(self, step_id: str) -> None:
        n = self.nodes.get(step_id)
        if n and n.kind == "step":
            n.status = "running"
            self._refresh_ready()

    def mark_done(self, step_id: str, *, notes: str = "") -> None:
        n = self.nodes.get(step_id)
        if n and n.kind == "step":
            n.status = "done"
            if notes:
                n.notes = notes[:500]
            self._refresh_ready()

    def mark_failed(self, step_id: str, *, notes: str = "") -> None:
        n = self.nodes.get(step_id)
        if n and n.kind == "step":
            n.status = "failed"
            if notes:
                n.notes = notes[:500]
            self._refresh_ready()

    def mark_skipped(self, step_id: str, *, notes: str = "") -> None:
        n = self.nodes.get(step_id)
        if n and n.kind == "step":
            n.status = "skipped"
            if notes:
                n.notes = notes[:500]
            self._refresh_ready()

    def is_complete(self) -> bool:
        steps = [n for n in self.nodes.values() if n.kind == "step"]
        if not steps:
            return False
        return all(n.status in ("done", "skipped") for n in steps)

    def progress(self) -> dict[str, int]:
        steps = [n for n in self.nodes.values() if n.kind == "step"]
        counts = {"total": len(steps), "done": 0, "failed": 0, "running": 0, "ready": 0, "pending": 0, "skipped": 0}
        for n in steps:
            counts[n.status] = counts.get(n.status, 0) + 1
        return counts

    # ----- presentation -------------------------------------------------

    def to_context_block(self, *, max_chars: int = 3000) -> str:
        """Compact hierarchical view for the model context window."""
        lines = [f"# Plan: {self.goal or '(untitled)'}"]
        prog = self.progress()
        lines.append(
            f"Progress: {prog.get('done', 0)}/{prog.get('total', 0)} done, "
            f"{prog.get('ready', 0)} ready, {prog.get('failed', 0)} failed"
        )
        if self.success_conditions:
            lines.append("Success conditions:")
            for sc in self.success_conditions[:8]:
                lines.append(f"  - {sc}")

        subgoals = [n for n in self.nodes.values() if n.kind == "subgoal"]
        if not subgoals:
            # Flat listing
            for n in self.nodes.values():
                if n.kind != "step":
                    continue
                mark = _status_mark(n.status)
                tool = f" [{n.tool_name}]" if n.tool_name else ""
                lines.append(f"{mark} {n.id}: {n.description}{tool}")
        else:
            for sg in subgoals:
                lines.append(f"## [{sg.status}] {sg.id}: {sg.description}")
                for n in self.nodes.values():
                    if n.parent_id != sg.id or n.kind != "step":
                        continue
                    mark = _status_mark(n.status)
                    tool = f" [{n.tool_name}]" if n.tool_name else ""
                    dep = f" (after: {', '.join(n.depends_on)})" if n.depends_on else ""
                    lines.append(f"  {mark} {n.id}: {n.description}{tool}{dep}")

        ready = self.ready_steps()
        if ready:
            lines.append("Next ready steps: " + ", ".join(r.id for r in ready[:8]))

        text = "\n".join(lines)
        return text[:max_chars]

    def to_todo_md(self) -> str:
        """Render a todo.md-style checklist from the hierarchy."""
        lines = [f"# {self.goal or 'Plan'}", ""]
        for n in self.nodes.values():
            if n.kind != "step":
                continue
            box = "x" if n.status in ("done", "skipped") else " "
            lines.append(f"- [{box}] {n.id}: {n.description}")
        return "\n".join(lines) + "\n"

    def to_dict(self) -> dict[str, Any]:
        return {
            "goal": self.goal,
            "version": self.version,
            "success_conditions": list(self.success_conditions),
            "nodes": {k: v.to_dict() for k, v in self.nodes.items()},
            "progress": self.progress(),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "HierarchicalPlan":
        plan = cls(
            goal=str(data.get("goal") or ""),
            success_conditions=list(data.get("success_conditions") or []),
            version=int(data.get("version") or 1),
        )
        for k, v in (data.get("nodes") or {}).items():
            if not isinstance(v, dict):
                continue
            plan.nodes[str(k)] = PlanNode(
                id=str(v.get("id") or k),
                kind=v.get("kind") or "step",  # type: ignore[arg-type]
                description=str(v.get("description") or ""),
                parent_id=v.get("parent_id"),
                depends_on=list(v.get("depends_on") or []),
                status=v.get("status") or "pending",  # type: ignore[arg-type]
                tool_name=v.get("tool_name"),
                tool_args=dict(v.get("tool_args") or {}),
                expected_outcome=v.get("expected_outcome"),
                success_condition=v.get("success_condition"),
                notes=str(v.get("notes") or ""),
            )
        plan._refresh_ready()
        return plan


def _status_mark(status: str) -> str:
    return {
        "done": "[x]",
        "skipped": "[~]",
        "failed": "[!]",
        "running": "[>]",
        "ready": "[*]",
        "pending": "[ ]",
    }.get(status, "[?]")


__all__ = [
    "PlanNode",
    "HierarchicalPlan",
    "NodeStatus",
    "NodeKind",
]
