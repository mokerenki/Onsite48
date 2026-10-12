"""Organization-scoped source-library indexing and retrieval."""

from __future__ import annotations

import hashlib
import re
import uuid
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import LibraryChunk, LibraryCollection, LibraryDocument

CHUNK_SIZE = 2200
CHUNK_OVERLAP = 260
MAX_DOCUMENT_CHARS = 250_000


def clean_text(value: str) -> str:
    return re.sub(r"\n{3,}", "\n\n", value.replace("\x00", "")).strip()


def chunks(value: str) -> list[str]:
    text = clean_text(value)[:MAX_DOCUMENT_CHARS]
    if not text:
        return []
    output: list[str] = []
    start = 0
    while start < len(text):
        end = min(len(text), start + CHUNK_SIZE)
        if end < len(text):
            boundary = max(text.rfind("\n", start + CHUNK_SIZE // 2, end), text.rfind(". ", start + CHUNK_SIZE // 2, end))
            if boundary > start:
                end = boundary + 1
        output.append(text[start:end].strip())
        start = max(end - CHUNK_OVERLAP, start + 1)
    return [item for item in output if item]


async def create_collection(
    session: AsyncSession,
    *,
    organization_id: uuid.UUID,
    user_id: uuid.UUID,
    name: str,
    description: str | None,
) -> LibraryCollection:
    collection = LibraryCollection(
        organization_id=organization_id,
        created_by_user_id=user_id,
        name=name.strip()[:160],
        description=(description or "").strip() or None,
    )
    session.add(collection)
    await session.flush()
    return collection


async def index_document(
    session: AsyncSession,
    *,
    organization_id: uuid.UUID,
    user_id: uuid.UUID,
    title: str,
    content_text: str,
    collection_id: uuid.UUID | None = None,
    source_attachment_id: str | None = None,
    source_type: str = "upload",
    metadata: dict[str, Any] | None = None,
) -> LibraryDocument:
    content = clean_text(content_text)[:MAX_DOCUMENT_CHARS]
    if not content:
        raise ValueError("No readable text is available to add to the Source Library.")
    if collection_id:
        collection = await session.scalar(select(LibraryCollection).where(
            LibraryCollection.id == collection_id,
            LibraryCollection.organization_id == organization_id,
        ))
        if collection is None:
            raise ValueError("The selected Source Library collection was not found.")
    content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
    existing = await session.scalar(select(LibraryDocument).where(
        LibraryDocument.organization_id == organization_id,
        LibraryDocument.content_hash == content_hash,
    ))
    if existing:
        return existing

    document = LibraryDocument(
        organization_id=organization_id,
        collection_id=collection_id,
        uploaded_by_user_id=user_id,
        source_attachment_id=source_attachment_id,
        title=title.strip()[:240] or "Untitled source",
        source_type=source_type,
        status="ready",
        content_text=content,
        content_hash=content_hash,
        char_count=len(content),
        metadata_json=metadata or {},
    )
    session.add(document)
    await session.flush()
    for index, content_chunk in enumerate(chunks(content)):
        session.add(LibraryChunk(
            document_id=document.id,
            organization_id=organization_id,
            chunk_index=index,
            content=content_chunk,
        ))
    await session.flush()
    return document


async def search_library(
    session: AsyncSession,
    *,
    organization_id: uuid.UUID,
    query: str,
    limit: int = 6,
) -> list[dict[str, Any]]:
    terms = [term for term in re.findall(r"[A-Za-z0-9]{3,}", query.lower())[:12] if term]
    if not terms:
        return []
    conditions = [LibraryChunk.content.ilike(f"%{term}%") for term in terms]
    candidates = list((await session.scalars(select(LibraryChunk).where(
        LibraryChunk.organization_id == organization_id,
        or_(*conditions),
    ).limit(120))).all())
    if not candidates:
        return []
    document_ids = {chunk.document_id for chunk in candidates}
    documents = {doc.id: doc for doc in (await session.scalars(select(LibraryDocument).where(LibraryDocument.id.in_(document_ids)))).all()}
    scored: list[tuple[int, LibraryChunk]] = []
    for chunk in candidates:
        text = chunk.content.lower()
        score = sum(text.count(term) for term in terms)
        if score:
            scored.append((score, chunk))
    scored.sort(key=lambda item: (-item[0], item[1].chunk_index))
    results: list[dict[str, Any]] = []
    seen: set[tuple[uuid.UUID, int]] = set()
    for score, chunk in scored:
        marker = (chunk.document_id, chunk.chunk_index)
        if marker in seen:
            continue
        seen.add(marker)
        document = documents.get(chunk.document_id)
        if not document:
            continue
        results.append({
            "document_id": str(document.id),
            "document_title": document.title,
            "collection_id": str(document.collection_id) if document.collection_id else None,
            "chunk_index": chunk.chunk_index,
            "content": chunk.content,
            "score": score,
        })
        if len(results) >= max(1, min(limit, 12)):
            break
    return results