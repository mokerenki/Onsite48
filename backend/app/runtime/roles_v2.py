"""Role configurations v2 — adaptive budgets, upgraded prompts.

Drop-in replacement for app.runtime.roles. Re-exports everything from the
original module so unknown consumers keep working, then overrides the public
names the runtime uses (RoleConfig, get_role, LEAD, and built-in role
constants) with upgraded prompts and Manus-grade budgets.
"""
from __future__ import annotations

from app.runtime.roles import *  # noqa: F401,F403 — preserve every public name
from app.runtime.roles import RoleConfig, get_role as _orig_get_role, LEAD as _LEAD_V1  # noqa: F401

# ---------------------------------------------------------------------------
# v2 budgets: sized for long-horizon execution. Global safety rails live in
# runtime/loop.py (DEFAULT_*); these are per-role working budgets.
# ---------------------------------------------------------------------------
LEAD = RoleConfig(
    name="lead",
    system_prompt_file="system_v2.txt",
    tool_allowlist=None,
    model_lane="default",
    budget={"steps": 120, "tokens": 600_000, "cost_usd": 15.0, "wall_s": 3600},
    may_spawn=True,
    depth_cap=2,               # was 1: allow lead -> worker -> sub-worker for wide maps
    max_summary_tokens=800,
)

RESEARCHER = RoleConfig(
    name="researcher",
    system_prompt_file="researcher_v2.txt",
    tool_allowlist=("browser", "browser_observe", "browser_act", "file_read",
                    "file_write", "python_exec", "web_search", "open_page",
                    "finish", "ask_user"),
    model_lane="default",
    budget={"steps": 60, "tokens": 400_000, "cost_usd": 6.0, "wall_s": 1800},
    may_spawn=False,
    depth_cap=1,
    max_summary_tokens=800,
)

CODER = RoleConfig(
    name="coder",
    system_prompt_file="coder_v2.txt",
    tool_allowlist=("file_read", "file_write", "file_edit", "shell_exec",
                    "python_exec", "preview_website", "package_website",
                    "finish", "ask_user"),
    model_lane="default",
    budget={"steps": 80, "tokens": 500_000, "cost_usd": 8.0, "wall_s": 2400},
    may_spawn=False,
    depth_cap=1,
    max_summary_tokens=600,
)

WRITER = RoleConfig(
    name="writer",
    system_prompt_file="writer_v2.txt",
    tool_allowlist=("file_read", "file_write", "python_exec",
                    "render_preview", "finish", "ask_user"),
    model_lane="default",
    budget={"steps": 40, "tokens": 200_000, "cost_usd": 3.0, "wall_s": 900},
    may_spawn=False,
    depth_cap=1,
    max_summary_tokens=600,
)

CRITIC = RoleConfig(
    name="critic",
    system_prompt_file="critic_v2.txt",
    tool_allowlist=("file_read", "python_exec", "shell_exec", "finish"),
    model_lane="verifier",       # verification lane: critics run rarely, decide much
    budget={"steps": 20, "tokens": 100_000, "cost_usd": 1.0, "wall_s": 600},
    may_spawn=False,
    depth_cap=1,
    max_summary_tokens=1000,
)

BROWSER_OPERATOR = RoleConfig(
    name="browser_operator",
    system_prompt_file="browser_operator_v2.txt",
    tool_allowlist=("browser_observe", "browser_act", "browser_takeover",
                    "browser_stop", "file_write", "finish", "ask_user"),
    model_lane="default",
    budget={"steps": 60, "tokens": 300_000, "cost_usd": 5.0, "wall_s": 1800},
    may_spawn=False,
    depth_cap=1,
    max_summary_tokens=600,
)

_ROLES_V2 = {
    r.name: r for r in (LEAD, RESEARCHER, CODER, WRITER, CRITIC, BROWSER_OPERATOR)
}


def get_role(name: str) -> RoleConfig:
    """v2 role lookup with fallback to v1 roles for any name we don't override."""
    role = _ROLES_V2.get(name)
    if role is not None:
        return role
    return _orig_get_role(name)