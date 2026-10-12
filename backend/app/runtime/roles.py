"""Role configurations for lead and worker agents.

Roles are pure configuration + prompt files. No code branches per role.
Workers run the same runtime.loop.run with a different RoleConfig.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

PROMPTS_DIR = Path(__file__).parent / "prompts"

DEFAULT_BUDGET = {
    "steps": 25,
    "tokens": 80_000,
    "cost_usd": 1.5,
    "wall_s": 300,
}


@dataclass(frozen=True)
class RoleConfig:
    name: str
    system_prompt_file: str
    tool_allowlist: tuple[str, ...] | None  # None = all tools available to parent
    model_lane: str = "default"
    budget: dict[str, float | int] = field(default_factory=lambda: dict(DEFAULT_BUDGET))
    may_spawn: bool = False
    depth_cap: int = 1
    max_summary_tokens: int = 400

    def load_system_prompt(self) -> str:
        path = PROMPTS_DIR / self.system_prompt_file
        if not path.exists():
            # Fallback to base system prompt so misconfig never crashes
            fallback = PROMPTS_DIR / "system_v1.txt"
            if fallback.exists():
                return fallback.read_text(encoding="utf-8").strip()
            return f"You are a {self.name} worker agent. Complete the assigned goal using tools."
        return path.read_text(encoding="utf-8").strip()

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "system_prompt_file": self.system_prompt_file,
            "tool_allowlist": list(self.tool_allowlist) if self.tool_allowlist is not None else None,
            "model_lane": self.model_lane,
            "budget": dict(self.budget),
            "may_spawn": self.may_spawn,
            "depth_cap": self.depth_cap,
            "max_summary_tokens": self.max_summary_tokens,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "RoleConfig":
        allow = d.get("tool_allowlist")
        return cls(
            name=d["name"],
            system_prompt_file=d.get("system_prompt_file", "system_v1.txt"),
            tool_allowlist=tuple(allow) if allow is not None else None,
            model_lane=d.get("model_lane", "default"),
            budget=dict(d.get("budget") or DEFAULT_BUDGET),
            may_spawn=bool(d.get("may_spawn", False)),
            depth_cap=int(d.get("depth_cap", 1)),
            max_summary_tokens=int(d.get("max_summary_tokens", 400)),
        )


# Built-in roles — configuration only
LEAD = RoleConfig(
    name="lead",
    system_prompt_file="system_v1.txt",
    tool_allowlist=None,
    model_lane="default",
    budget={"steps": 40, "tokens": 200_000, "cost_usd": 5.0, "wall_s": 900},
    may_spawn=True,
    depth_cap=1,
)

RESEARCHER = RoleConfig(
    name="researcher",
    system_prompt_file="researcher_v1.txt",
    tool_allowlist=(
        "web_search", "open_page", "browser", "file_read", "file_write",
        "file_edit", "python_exec", "shell_exec", "finish", "ask_user",
        "load_skill",
    ),
    model_lane="default",
    budget={"steps": 20, "tokens": 60_000, "cost_usd": 1.0, "wall_s": 240},
    may_spawn=False,
)

BROWSER_OPERATOR = RoleConfig(
    name="browser_operator",
    system_prompt_file="browser_operator_v1.txt",
    tool_allowlist=(
        "browser", "file_read", "file_write", "python_exec", "finish", "ask_user",
    ),
    model_lane="default",
    budget={"steps": 25, "tokens": 50_000, "cost_usd": 1.2, "wall_s": 300},
    may_spawn=False,
)

CODER = RoleConfig(
    name="coder",
    system_prompt_file="coder_v1.txt",
    tool_allowlist=(
        "file_read", "file_write", "file_edit", "shell_exec", "python_exec",
        "finish", "ask_user", "load_skill", "preview_website", "package_website",
    ),
    model_lane="default",
    budget={"steps": 30, "tokens": 80_000, "cost_usd": 1.5, "wall_s": 360},
    may_spawn=False,
)

WRITER = RoleConfig(
    name="writer",
    system_prompt_file="writer_v1.txt",
    tool_allowlist=(
        "file_read", "file_write", "file_edit", "python_exec", "load_skill",
        "render_preview", "finish", "ask_user",
    ),
    model_lane="default",
    budget={"steps": 20, "tokens": 60_000, "cost_usd": 1.0, "wall_s": 240},
    may_spawn=False,
)

CRITIC = RoleConfig(
    name="critic",
    system_prompt_file="critic_v1.txt",
    tool_allowlist=(
        "file_read", "python_exec", "shell_exec", "finish",  # read-only intent
    ),
    model_lane="default",
    budget={"steps": 12, "tokens": 40_000, "cost_usd": 0.6, "wall_s": 180},
    may_spawn=False,
)

ROLE_REGISTRY: dict[str, RoleConfig] = {
    r.name: r
    for r in (LEAD, RESEARCHER, BROWSER_OPERATOR, CODER, WRITER, CRITIC)
}


def get_role(name: str) -> RoleConfig:
    key = (name or "").strip().lower()
    if key not in ROLE_REGISTRY:
        raise ValueError(
            f"Unknown role {name!r}. Known: {sorted(ROLE_REGISTRY)}"
        )
    return ROLE_REGISTRY[key]


def intersect_allowlist(
    parent_allow: tuple[str, ...] | None,
    child_allow: tuple[str, ...] | None,
) -> tuple[str, ...] | None:
    """Worker can never exceed parent grants."""
    if parent_allow is None and child_allow is None:
        return None
    if parent_allow is None:
        return child_allow
    if child_allow is None:
        return parent_allow
    return tuple(sorted(set(parent_allow) & set(child_allow)))
