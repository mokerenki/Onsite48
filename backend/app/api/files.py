"""Authenticated file attachment endpoints for Onsite48 tasks."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from fastapi.responses import Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import get_current_user, get_db_session
from app.models import OrganizationMember, User
from app.services.artifact_contract import (
    MARKDOWN_CONTENT_TYPE,
    content_disposition,
    markdown_filename,
    verify_artifact_signature,
)
from app.services.attachment_store import attachment_store

router = APIRouter(prefix="/api/files", tags=["attachments"])


async def workspace_membership(session: AsyncSession, user: User) -> OrganizationMember:
    membership = await session.scalar(
        select(OrganizationMember).where(OrganizationMember.user_id == user.id).order_by(OrganizationMember.created_at.asc())
    )
    assert membership is not None, "Authenticated users must have a workspace membership."
    return membership


@router.post("/upload")
async def upload_attachment(
    file: UploadFile = File(...),
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    membership = await workspace_membership(session, user)
    return await attachment_store.save(
        organization_id=str(membership.organization_id),
        user_id=str(user.id),
        upload=file,
    )


@router.get("/{attachment_id}/download")
async def download_attachment(
    attachment_id: str,
    expires: int | None = Query(default=None),
    signature: str | None = Query(default=None),
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
) -> Response:
    if not isinstance(expires, int):
        expires = None
    if not isinstance(signature, str):
        signature = None
    if (expires is None) != (signature is None):
        raise HTTPException(status_code=403, detail="Both artifact signature parameters are required.")
    if expires is not None and signature is not None:
        try:
            valid = verify_artifact_signature(attachment_id, expires, signature)
        except (RuntimeError, ValueError, TypeError):
            valid = False
        if not valid:
            raise HTTPException(status_code=403, detail="The artifact download signature is invalid or expired.")
    membership = await workspace_membership(session, user)
    metadata, content = attachment_store.read_content(
        organization_id=str(membership.organization_id),
        user_id=str(user.id),
        attachment_id=attachment_id,
    )
    generated_markdown = metadata.get("artifact_type") == "generated_markdown_report"
    filename = (
        markdown_filename(str(metadata.get("name") or "onsite48-report.md"))
        if generated_markdown
        else str(metadata.get("name") or "onsite48-artifact")
    )
    media_type = MARKDOWN_CONTENT_TYPE if generated_markdown else str(metadata.get("content_type") or "application/octet-stream")
    return Response(
        content=content,
        media_type=media_type,
        headers={
            "Content-Disposition": content_disposition(filename),
            "X-Content-Type-Options": "nosniff",
            "X-Onsite48-Artifact-Type": str(metadata.get("artifact_type") or "attachment"),
        },
    )


@router.get("/{attachment_id}")
async def get_attachment_metadata(
    attachment_id: str,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    membership = await workspace_membership(session, user)
    return attachment_store.get_metadata(
        organization_id=str(membership.organization_id),
        user_id=str(user.id),
        attachment_id=attachment_id,
    )