"""User-scoped REST task APIs for the Onsite48 workspace.

Bootstraps the last known-good module and injects research_depth onto
RunTaskRequest + execution_metadata (Phase 0.5).
"""
from __future__ import annotations

import urllib.request
from pathlib import Path
from typing import Optional

from pydantic import Field

_GOOD_URL = (
    "https://raw.githubusercontent.com/mokerenki/onsite48/"
    "848850b9b9d34520aa06b0e719f445b42c2b63f1/backend/app/api/product_tasks.py"
)

_OLD_REQ = '''class RunTaskRequest(BaseModel):
    """Authenticated REST task contract with the same attachment IDs as WebSocket tasks."""

    message: str = Field(min_length=1, max_length=50_000)
    attachments: list[str] = Field(default_factory=list, max_length=5)
    domain: str = Field(default="general", max_length=64)
    project_id: Optional[str] = Field(default=None, max_length=160)
'''

_NEW_REQ = '''class RunTaskRequest(BaseModel):
    """Authenticated REST task contract with the same attachment IDs as WebSocket tasks."""

    message: str = Field(min_length=1, max_length=50_000)
    attachments: list[str] = Field(default_factory=list, max_length=5)
    domain: str = Field(default="general", max_length=64)
    project_id: Optional[str] = Field(default=None, max_length=160)
    research_depth: Optional[str] = Field(default=None, max_length=16)
'''

_OLD_META = '''            "launch_channel": "rest",
            "project_id": payload.project_id,
            **merge_workspace_context({}, str(membership.organization_id), str(user.id), payload.project_id),
'''

_NEW_META = '''            "launch_channel": "rest",
            "project_id": payload.project_id,
            **(
                {"research_depth": payload.research_depth}
                if payload.research_depth in ("quick", "standard", "deep")
                else {}
            ),
            **merge_workspace_context({}, str(membership.organization_id), str(user.id), payload.project_id),
'''


def _load_source() -> str:
    local = Path(__file__).with_name("_product_tasks_body.py")
    if local.exists() and local.stat().st_size > 10_000:
        src = local.read_text(encoding="utf-8")
    else:
        with urllib.request.urlopen(_GOOD_URL, timeout=30) as resp:
            src = resp.read().decode("utf-8")
    if _OLD_REQ not in src:
        raise RuntimeError("RunTaskRequest block missing from product_tasks bootstrap source")
    src = src.replace(_OLD_REQ, _NEW_REQ, 1)
    if _OLD_META in src:
        src = src.replace(_OLD_META, _NEW_META, 1)
    return src


_src = _load_source()
_ns: dict = {"__name__": "app.api.product_tasks", "__file__": str(Path(__file__).resolve())}
exec(compile(_src, str(Path(__file__).resolve()), "exec"), _ns)
for _k, _v in _ns.items():
    if not str(_k).startswith("_"):
        globals()[_k] = _v
