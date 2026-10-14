"""Platform control-plane endpoints used by the Manus-style workspace shell."""
from __future__ import annotations
from typing import Any
from fastapi import APIRouter, Depends, Request, HTTPException, Query
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.security import get_current_user, get_db_session
from app.core.model_lanes import lane_catalog
from app.core.platform_metrics import metrics
from app.models import AgentArtifact, AgentRun, OrganizationMember, TaskStatus, TaskThread, ToolApproval, User
from app.services.artifact_versions import artifact_versions
from app.services.artifact_contract import content_disposition

router = APIRouter(prefix="/api/platform", tags=["platform"])

class ArtifactRollback(BaseModel):
    version_id: str = Field(min_length=1, max_length=128)

async def membership(session: AsyncSession, user: User) -> OrganizationMember:
    return await session.scalar(select(OrganizationMember).where(OrganizationMember.user_id == user.id).order_by(OrganizationMember.created_at.asc()))

@router.get("/model-lanes")
async def model_lanes(request: Request, user: User = Depends(get_current_user)) -> dict[str, Any]:
    router_instance = getattr(request.app.state, "model_router", None)
    selected = router_instance.lane_catalog() if router_instance and hasattr(router_instance, "lane_catalog") else lane_catalog()
    return {"lanes": selected}

@router.get("/overview")
async def overview(user: User = Depends(get_current_user), session: AsyncSession = Depends(get_db_session)) -> dict[str, Any]:
    member = await membership(session, user)
    organization_id = member.organization_id
    task_rows = list((await session.scalars(select(TaskThread).where(TaskThread.organization_id == organization_id, TaskThread.user_id == user.id).order_by(TaskThread.updated_at.desc()).limit(20))).all())
    approval_count = await session.scalar(select(func.count(ToolApproval.id)).join(TaskThread, ToolApproval.task_id == TaskThread.id).where(TaskThread.organization_id == organization_id, ToolApproval.decision.is_(None)))
    artifact_rows = list((await session.scalars(select(AgentArtifact).join(AgentRun, AgentArtifact.run_id == AgentRun.id).join(TaskThread, AgentRun.task_id == TaskThread.id).where(TaskThread.organization_id == organization_id, TaskThread.user_id == user.id).order_by(AgentArtifact.created_at.desc()).limit(30))).all())
    counts = {status.value: sum(1 for task in task_rows if task.status == status) for status in TaskStatus}
    return {
        "workspace": {"organization_id": str(organization_id), "user_id": str(user.id)},
        "tasks": {"counts": counts, "recent": [{"thread_id": task.external_thread_id, "title": task.title, "status": task.status.value, "updated_at": task.updated_at.isoformat(), "project_id": (task.execution_metadata or {}).get("project_id")} for task in task_rows]},
        "approval_inbox": {"pending": int(approval_count or 0)},
        "artifacts": {"count": len(artifact_rows), "recent": [{"id": str(item.id), "filename": item.filename, "media_type": item.media_type, "storage_key": item.storage_key, "checksum": item.checksum, "created_at": item.created_at.isoformat(), "provenance": item.provenance or {}} for item in artifact_rows]},
    }

@router.get("/artifacts")
async def artifacts(limit: int = 100, user: User = Depends(get_current_user), session: AsyncSession = Depends(get_db_session)) -> dict[str, Any]:
    member = await membership(session, user)
    rows = list((await session.scalars(select(AgentArtifact).join(AgentRun, AgentArtifact.run_id == AgentRun.id).join(TaskThread, AgentRun.task_id == TaskThread.id).where(TaskThread.organization_id == member.organization_id, TaskThread.user_id == user.id).order_by(AgentArtifact.created_at.desc()).limit(min(limit, 200)))).all())
    return {"artifacts": [{"id": str(item.id), "filename": item.filename, "media_type": item.media_type, "storage_key": item.storage_key, "checksum": item.checksum, "created_at": item.created_at.isoformat(), "provenance": item.provenance or {}} for item in rows]}

@router.get("/metrics")
async def platform_metrics(user: User = Depends(get_current_user)) -> dict[str, Any]:
    return {"prometheus": metrics.text()}

@router.get("/artifacts/{artifact_id}/versions")
async def artifact_versions_list(artifact_id: str, user: User = Depends(get_current_user), session: AsyncSession = Depends(get_db_session)) -> dict[str, Any]:
    member = await membership(session, user)
    return artifact_versions.get(organization_id=str(member.organization_id), artifact_id=artifact_id)

@router.post("/artifacts/{artifact_id}/rollback")
async def artifact_rollback(artifact_id: str, payload: ArtifactRollback, user: User = Depends(get_current_user), session: AsyncSession = Depends(get_db_session)) -> dict[str, Any]:
    member = await membership(session, user)
    try:
        return artifact_versions.rollback(organization_id=str(member.organization_id), artifact_id=artifact_id, version_id=payload.version_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Artifact version not found.") from exc

@router.get("/artifacts/{artifact_id}/export")
async def artifact_export(artifact_id: str, version_id: str | None = None, user: User = Depends(get_current_user), session: AsyncSession = Depends(get_db_session)) -> Response:
    member = await membership(session, user)
    try:
        item, content = artifact_versions.export(organization_id=str(member.organization_id), artifact_id=artifact_id, version_id=version_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Artifact version not found.") from exc
    return Response(content=content, media_type=str(item.get("content_type") or "application/octet-stream"), headers={"Content-Disposition": content_disposition(str(item.get("filename") or "artifact")), "X-Onsite48-Artifact-Version": str(item.get("version_id"))})


@router.get("/artifacts/{artifact_id}/diff")
async def artifact_diff(
    artifact_id: str,
    from_version: str = Query(..., description="Source version_id"),
    to_version: str = Query(..., description="Target version_id"),
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    """Compute a structural diff between two artifact versions."""
    member = await membership(session, user)
    manifest = artifact_versions.get(
        organization_id=str(member.organization_id), artifact_id=artifact_id
    )
    versions = manifest.get("versions", [])
    from_item = next((v for v in versions if v.get("version_id") == from_version), None)
    to_item = next((v for v in versions if v.get("version_id") == to_version), None)
    if from_item is None or to_item is None:
        raise HTTPException(status_code=404, detail="One or both artifact versions not found.")

    from_content = artifact_versions.export(
        organization_id=str(member.organization_id), artifact_id=artifact_id, version_id=from_version
    )[1]
    to_content = artifact_versions.export(
        organization_id=str(member.organization_id), artifact_id=artifact_id, version_id=to_version
    )[1]

    from_text = from_content.decode("utf-8", errors="replace")
    to_text = to_content.decode("utf-8", errors="replace")

    from_lines = from_text.splitlines()
    to_lines = to_text.splitlines()

    changes: list[dict[str, Any]] = []
    import difflib
    matcher = difflib.SequenceMatcher(a=from_lines, b=to_lines, autojunk=False)
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            continue
        for line in from_lines[i1:i2]:
            changes.append({"type": "removed", "line": line})
        for line in to_lines[j1:j2]:
            changes.append({"type": "added", "line": line})

    added_lines = sum(1 for c in changes if c["type"] == "added")
    removed_lines = sum(1 for c in changes if c["type"] == "removed")

    return {
        "from_version": from_version,
        "to_version": to_version,
        "changes": changes,
        "added_lines": added_lines,
        "removed_lines": removed_lines,
        "modified_fields": len(set(c["line"] for c in changes if c["type"] == "added")),
    }