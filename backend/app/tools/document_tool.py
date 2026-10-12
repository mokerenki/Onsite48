"""DocumentTool — generates user-downloadable artifacts.

The document tool now writes through the attachment store so that any
generated HTML, Markdown, or plain-text artifact is owned by the
requesting user, is encrypted-at-rest alongside other user files, and
is downloadable through the same authenticated endpoint as uploaded
files. The previous implementation wrote to a shared host path, which
was not tenant-safe and could not be downloaded from the UI.
"""

from __future__ import annotations

import os
from typing import Any

import structlog

from app.services.attachment_store import attachment_store
from app.services.execution_identity import get_execution_identity
from app.tools.registry import BaseTool, ToolSchema

logger = structlog.get_logger("aether.tools.document")


class DocumentTool(BaseTool):
    def _build_schema(self) -> ToolSchema:
        return ToolSchema(
            name="document",
            description=(
                "Generate a user-downloadable document (Markdown, HTML, or plain text) "
                "and save it as an artifact owned by the requesting user. Use this for "
                "reports, websites, briefs, and any deliverable the user should be able "
                "to open or download."
            ),
            parameters={
                "title": {"type": "string", "description": "Document title (used as the filename base)."},
                "content": {"type": "string", "description": "Document body content."},
                "format": {
                    "type": "string",
                    "enum": ["md", "html", "txt"],
                    "description": "Output format (default: md).",
                },
            },
            required=["title", "content"],
            irreversible=False,
            idempotency="write",
            approval_required=False,
            resources=["file"],
            operations=["create"],
            exactness="exact",
        )

    async def execute(self, **kwargs: Any) -> dict[str, Any]:
        title = str(kwargs.get("title") or "document").strip() or "document"
        content = str(kwargs.get("content") or "")
        fmt = str(kwargs.get("format") or "md").lower()
        if fmt not in {"md", "html", "txt"}:
            fmt = "md"
        if not content.strip():
            return {"output": "Error: document content is empty."}

        identity = get_execution_identity()
        safe_title = "".join(c if c.isalnum() or c in "._- " else "_" for c in title).strip().replace(" ", "_") or "document"
        filename = f"{safe_title}.{fmt}"
        content_type = {"html": "text/html", "md": "text/markdown", "txt": "text/plain"}[fmt]

        try:
            stored = attachment_store.save_generated_markdown(
                organization_id=str(identity.organization_id),
                user_id=str(identity.user_id),
                filename=filename,
                markdown=content if fmt == "md" else content,
                source_title=title,
            )
            # The attachment store keeps Markdown canonical; for HTML and TXT
            # we rewrite the metadata so the download has the correct type.
            if fmt != "md":
                from pathlib import Path
                import json as _json
                directory = attachment_store._user_directory(str(identity.organization_id), str(identity.user_id))
                meta_path = directory / f"{stored['id']}.metadata.json"
                if meta_path.exists():
                    metadata = _json.loads(meta_path.read_text(encoding="utf-8"))
                    metadata["name"] = filename
                    metadata["content_type"] = content_type
                    metadata["artifact_type"] = f"generated_{fmt}_document"
                    meta_path.write_text(_json.dumps(metadata), encoding="utf-8")

            download_url = f"/api/files/{stored['id']}/download"
            logger.info(
                "document.created",
                attachment_id=stored["id"],
                filename=filename,
                size=stored["size"],
            )
            return {
                "output": f"Created {filename} ({stored['size']} bytes).",
                "path": download_url,
                "attachment_id": stored["id"],
                "name": filename,
                "content_type": content_type,
                "size": stored["size"],
                "sha256": stored["sha256"],
                "artifact_type": f"generated_{fmt}_document",
                "download_url": download_url,
            }
        except Exception as exc:
            logger.error("document.create_failed", error=str(exc))
            return {"output": f"Error creating document: {exc}"}