"""Stable workspace identity used to carry project context across task runs."""

from __future__ import annotations

import hashlib


def workspace_context(organization_id: str, user_id: str, project_id: str | None = None) -> dict[str, str | None]:
    """Build non-secret continuity metadata; credentials and browser contents never belong here."""
    organization = str(organization_id).strip()
    user = str(user_id).strip()
    project = str(project_id).strip() if project_id else None
    scope = f"{organization}:{user}:{project or 'default'}"
    return {
        "workspace_key": hashlib.sha256(scope.encode("utf-8")).hexdigest()[:32],
        "project_id": project,
        "sandbox_owner": user,
    }


def merge_workspace_context(metadata: dict, organization_id: str, user_id: str, project_id: str | None = None) -> dict:
    """Preserve explicit task metadata while refreshing the derived continuity identity."""
    result = dict(metadata or {})
    result["workspace_continuity"] = workspace_context(organization_id, user_id, project_id)
    return result


__all__ = ["merge_workspace_context", "workspace_context"]