"""Canonical contracts for user-owned generated artifacts.

Artifacts are binary resources with typed metadata.  They are not JSON payloads
and must retain their authoritative filename and MIME type from creation through
an authenticated browser download.
"""

from __future__ import annotations

import re
import hashlib
import hmac
import os
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any
from urllib.parse import quote

MARKDOWN_CONTENT_TYPE = "text/markdown; charset=utf-8"
MARKDOWN_EVENT_CONTENT_TYPE = "text/markdown"


@dataclass(frozen=True)
class ArtifactDescriptor:
    """Transport-safe metadata for a stored artifact; never contains file bytes."""

    attachment_id: str
    name: str
    content_type: str
    size: int
    sha256: str
    artifact_type: str
    download_url: str
    source_title: str | None = None
    provenance: dict[str, Any] | None = None
    version: int = 1
    preview: str | None = None

    def event_payload(self) -> dict[str, Any]:
        return asdict(self)


def safe_filename(value: str | None, *, fallback: str, extension: str | None = None) -> str:
    """Return a portable display/download name with an optional exact extension."""
    filename = Path(value or fallback).name
    filename = re.sub(r"[^A-Za-z0-9._ -]", "_", filename).strip(" .")[:160]
    filename = filename or fallback
    if extension:
        normalized = extension.lower() if extension.startswith(".") else f".{extension.lower()}"
        stem = Path(filename).stem or Path(fallback).stem or "artifact"
        filename = f"{stem}{normalized}"
    return filename


def markdown_filename(value: str | None, *, fallback: str = "onsite48-report.md") -> str:
    return safe_filename(value, fallback=fallback, extension=".md")


def content_disposition(filename: str) -> str:
    """Build an RFC 5987-compatible attachment header without header injection."""
    safe = safe_filename(filename, fallback="onsite48-artifact")
    ascii_fallback = safe.encode("ascii", "ignore").decode("ascii") or "onsite48-artifact"
    return f"attachment; filename=\"{ascii_fallback}\"; filename*=UTF-8''{quote(safe)}"


def _signing_secret() -> bytes:
    secret = os.getenv("ARTIFACT_SIGNING_SECRET", "").strip()
    if not secret and os.getenv("SYNTHAI_ENV", "development").lower() not in {"production", "prod"}:
        secret = "development-only-artifact-signing-secret"
    if not secret:
        raise RuntimeError("ARTIFACT_SIGNING_SECRET must be configured for signed artifact URLs")
    return secret.encode("utf-8")


def artifact_signature(attachment_id: str, expires: int) -> str:
    payload = f"{attachment_id}:{int(expires)}".encode("utf-8")
    return hmac.new(_signing_secret(), payload, hashlib.sha256).hexdigest()


def signed_download_url(attachment_id: str, *, ttl_seconds: int = 900) -> str:
    expires = int(time.time()) + max(30, min(ttl_seconds, 86_400))
    return f"/api/files/{quote(str(attachment_id))}/download?expires={expires}&signature={artifact_signature(str(attachment_id), expires)}"


def verify_artifact_signature(attachment_id: str, expires: int, signature: str) -> bool:
    if int(expires) < int(time.time()):
        return False
    expected = artifact_signature(attachment_id, int(expires))
    return hmac.compare_digest(expected, str(signature or ""))


def is_markdown_content_type(value: str | None) -> bool:
    return str(value or "").lower().split(";", 1)[0].strip() == "text/markdown"