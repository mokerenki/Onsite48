"""Authenticated Source Library API for organization knowledge retrieval."""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.product_tasks import get_current_membership
from app.core.security import get_current_user, get_db_session
from app.models import LibraryCollection, LibraryDocument, User
from app.services.attachment_store import attachment_store
from app.services.source_library import create_collection, index_document, search_library

router = APIRouter(prefix="/api/library", tags=["source-library"])


class CollectionCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    description: str | None = Field(default=None, max_length=4000)


class IndexAttachmentRequest(BaseModel):
    attachment_id: str = Field(min_length=36, max_length=64)
    collection_id: uuid.UUID | None = None
    title: str | None = Field(default=None, max_length=240)


class IndexTextRequest(BaseModel):
    title: str = Field(min_length=1, max_length=240)
    content: str = Field(min_length=1, max_length=250_000)
    collection_id: uuid.UUID | None = None


def collection_response(collection: LibraryCollection) -> dict[str, Any]:
    return {
        "id": str(collection.id),
        "name": collection.name,
        "description": collection.description,
        "created_at": collection.created_at.isoformat(),
        "updated_at": collection.updated_at.isoformat(),
    }


def document_response(document: LibraryDocument) -> dict[str, Any]:
    return {
        "id": str(document.id),
        "collection_id": str(document.collection_id) if document.collection_id else None,
        "title": document.title,
        "source_type": document.source_type,
        "status": document.status,
        "char_count": document.char_count,
        "metadata": document.metadata_json or {},
        "created_at": document.created_at.isoformat(),
        "updated_at": document.updated_at.isoformat(),
    }


@router.get("/collections")
async def list_collections(
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
):
    membership = await get_current_membership(session, user)
    collections = list((await session.scalars(select(LibraryCollection).where(
        LibraryCollection.organization_id == membership.organization_id,
    ).order_by(LibraryCollection.updated_at.desc()))).all())
    return {"collections": [collection_response(item) for item in collections]}


@router.post("/collections", status_code=status.HTTP_201_CREATED)
async def create_library_collection(
    payload: CollectionCreateRequest,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
):
    membership = await get_current_membership(session, user)
    try:
        collection = await create_collection(
            session,
            organization_id=membership.organization_id,
            user_id=user.id,
            name=payload.name,
            description=payload.description,
        )
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    return collection_response(collection)


@router.get("/documents")
async def list_documents(
    collection_id: uuid.UUID | None = None,
    limit: int = Query(default=50, ge=1, le=100),
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
):
    membership = await get_current_membership(session, user)
    query = select(LibraryDocument).where(LibraryDocument.organization_id == membership.organization_id)
    if collection_id:
        query = query.where(LibraryDocument.collection_id == collection_id)
    documents = list((await session.scalars(query.order_by(LibraryDocument.updated_at.desc()).limit(limit))).all())
    return {"documents": [document_response(item) for item in documents]}


@router.post("/documents/from-attachment", status_code=status.HTTP_201_CREATED)
async def index_attachment(
    payload: IndexAttachmentRequest,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
):
    membership = await get_current_membership(session, user)
    metadata, text = attachment_store.extracted_text(
        organization_id=str(membership.organization_id),
        user_id=str(user.id),
        attachment_id=payload.attachment_id,
    )
    try:
        document = await index_document(
            session,
            organization_id=membership.organization_id,
            user_id=user.id,
            title=payload.title or metadata["name"],
            content_text=text,
            collection_id=payload.collection_id,
            source_attachment_id=payload.attachment_id,
            metadata={"attachment": metadata},
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
    return document_response(document)


@router.post("/documents/text", status_code=status.HTTP_201_CREATED)
async def index_text(
    payload: IndexTextRequest,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
):
    membership = await get_current_membership(session, user)
    try:
        document = await index_document(
            session,
            organization_id=membership.organization_id,
            user_id=user.id,
            title=payload.title,
            content_text=payload.content,
            collection_id=payload.collection_id,
            source_type="manual_text",
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
    return document_response(document)


@router.get("/search")
async def search_sources(
    q: str = Query(min_length=2, max_length=1000),
    limit: int = Query(default=6, ge=1, le=12),
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
):
    membership = await get_current_membership(session, user)
    return {"results": await search_library(session, organization_id=membership.organization_id, query=q, limit=limit)}