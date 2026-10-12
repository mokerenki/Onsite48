"""Short-lived signed static website previews for generated projects.

Previews contain copied, regular files only; generated Python/Node servers are
never executed by the API. Each preview URL is signed and expires, and the
frontend embeds it only in a sandboxed iframe.
"""
from __future__ import annotations

import mimetypes
import json
import os
import re
import shutil
import time
import uuid
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Mapping

from app.services.artifact_contract import artifact_signature, verify_artifact_signature

PREVIEW_TTL_SECONDS = max(300, min(int(os.getenv("WEBSITE_PREVIEW_TTL_SECONDS", "14400")), 86_400))
MAX_PREVIEW_FILES = 500
MAX_PREVIEW_BYTES = 30 * 1024 * 1024
PREVIEW_ROOT = Path(os.getenv("WEBSITE_PREVIEW_ROOT", "/tmp/onsite48_web_previews")).resolve()
_EXCLUDED_PARTS = {".git", "node_modules", ".next", ".venv", "venv", "__pycache__", ".pytest_cache"}


@dataclass(frozen=True)
class PreviewRecord:
    preview_id: str
    root: Path
    user_id: str
    organization_id: str
    thread_id: str
    project_name: str
    expires_at: int


_previews: dict[str, PreviewRecord] = {}


def _safe_relative_path(value: str) -> PurePosixPath:
    path = PurePosixPath(value.replace("\\", "/"))
    if path.is_absolute() or not path.parts or any(part in ("", ".", "..") for part in path.parts):
        raise ValueError("preview files must use safe relative paths")
    if any(part.startswith(".") for part in path.parts):
        raise ValueError("hidden files are not permitted in website previews")
    return path


def create_preview(
    files: Mapping[str, bytes],
    *,
    user_id: str,
    organization_id: str,
    thread_id: str,
    project_name: str,
) -> dict[str, Any]:
    """Copy a bounded static website into the preview store and return its URL."""
    if not files or len(files) > MAX_PREVIEW_FILES:
        raise ValueError(f"website preview must contain 1–{MAX_PREVIEW_FILES} files")
    total_bytes = sum(len(content) for content in files.values())
    if total_bytes > MAX_PREVIEW_BYTES:
        raise ValueError(f"website preview exceeds the {MAX_PREVIEW_BYTES // (1024 * 1024)} MiB limit")

    normalized: dict[PurePosixPath, bytes] = {}
    for name, content in files.items():
        relative = _safe_relative_path(name)
        if any(part in _EXCLUDED_PARTS for part in relative.parts):
            continue
        if not isinstance(content, bytes):
            raise TypeError(f"preview file {name!r} must be bytes")
        normalized[relative] = content
    if PurePosixPath("index.html") not in normalized:
        raise ValueError("website preview requires index.html at the project root")

    PREVIEW_ROOT.mkdir(parents=True, exist_ok=True)
    preview_id = uuid.uuid4().hex
    destination = (PREVIEW_ROOT / preview_id).resolve()
    destination.mkdir(parents=True, exist_ok=False)
    try:
        for relative, content in normalized.items():
            target = (destination / Path(*relative.parts)).resolve()
            target.relative_to(destination)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)
    except Exception:
        shutil.rmtree(destination, ignore_errors=True)
        raise

    expires_at = int(time.time()) + PREVIEW_TTL_SECONDS
    record = PreviewRecord(
        preview_id=preview_id,
        root=destination,
        user_id=str(user_id),
        organization_id=str(organization_id),
        thread_id=str(thread_id),
        project_name=re.sub(r"[^A-Za-z0-9._ -]", "", project_name).strip()[:100] or "Website preview",
        expires_at=expires_at,
    )
    (destination / ".preview-meta.json").write_text(json.dumps({
        "preview_id": preview_id,
        "user_id": record.user_id,
        "organization_id": record.organization_id,
        "thread_id": record.thread_id,
        "project_name": record.project_name,
        "expires_at": record.expires_at,
    }), encoding="utf-8")
    _previews[preview_id] = record
    signature = artifact_signature(preview_id, expires_at)
    return {
        "preview_id": preview_id,
        "project_name": record.project_name,
        "preview_url": f"/api/web-previews/{preview_id}/{expires_at}/{signature}/index.html",
        "expires_at": expires_at,
        "expires_in_seconds": PREVIEW_TTL_SECONDS,
        "file_count": len(normalized),
        "size_bytes": total_bytes,
        "mode": "static",
    }


def resolve_preview_file(preview_id: str, expires: int, signature: str, file_path: str) -> tuple[PreviewRecord, Path] | None:
    """Validate a signed preview URL and safely resolve a file below its root."""
    try:
        if not verify_artifact_signature(preview_id, int(expires), signature):
            return None
    except (RuntimeError, ValueError, TypeError):
        return None
    if not re.fullmatch(r"[a-f0-9]{32}", preview_id):
        return None
    record = _previews.get(preview_id)
    if record is None:
        root = (PREVIEW_ROOT / preview_id).resolve()
        try:
            root.relative_to(PREVIEW_ROOT)
            metadata = json.loads((root / ".preview-meta.json").read_text(encoding="utf-8"))
            record = PreviewRecord(
                preview_id=preview_id,
                root=root,
                user_id=str(metadata["user_id"]),
                organization_id=str(metadata["organization_id"]),
                thread_id=str(metadata["thread_id"]),
                project_name=str(metadata["project_name"]),
                expires_at=int(metadata["expires_at"]),
            )
            _previews[preview_id] = record
        except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError):
            return None
    if record is None or record.expires_at != int(expires) or record.expires_at < int(time.time()):
        return None
    raw_path = file_path or "index.html"
    try:
        relative = _safe_relative_path(raw_path)
        target = (record.root / Path(*relative.parts)).resolve()
        target.relative_to(record.root)
    except (ValueError, OSError):
        return None
    if not target.is_file():
        # SPA client-side routes may fall back to the generated entry point.
        if "." not in PurePosixPath(raw_path).name:
            target = record.root / "index.html"
        else:
            return None
    return record, target


def cleanup_expired_previews(now: int | None = None) -> int:
    """Remove expired preview snapshots; safe to call at startup or periodically."""
    cutoff = int(now or time.time())
    expired = [key for key, record in _previews.items() if record.expires_at < cutoff]
    for key in expired:
        record = _previews.pop(key)
        shutil.rmtree(record.root, ignore_errors=True)
    if PREVIEW_ROOT.exists():
        for root in PREVIEW_ROOT.iterdir():
            if not root.is_dir() or root.name in expired:
                continue
            try:
                metadata = json.loads((root / ".preview-meta.json").read_text(encoding="utf-8"))
                if int(metadata.get("expires_at") or 0) < cutoff:
                    shutil.rmtree(root, ignore_errors=True)
                    _previews.pop(root.name, None)
                    expired.append(root.name)
            except (OSError, ValueError, TypeError, json.JSONDecodeError):
                continue
    return len(expired)


def preview_content_type(path: Path) -> str:
    return mimetypes.guess_type(path.name)[0] or "application/octet-stream"


def preview_security_headers() -> dict[str, str]:
    return {
        "X-Content-Type-Options": "nosniff",
        "Referrer-Policy": "no-referrer",
        "Cache-Control": "private, max-age=60",
        "Content-Security-Policy": (
            "default-src 'self' data: blob:; img-src 'self' data: blob:; "
            "style-src 'self' 'unsafe-inline'; script-src 'self' 'unsafe-inline'; "
            "font-src 'self' data:; connect-src 'none'; object-src 'none'; "
            "base-uri 'none'; form-action 'none'; frame-ancestors 'self'"
        ),
    }


def preview_metadata(preview_id: str) -> dict[str, Any] | None:
    record = _previews.get(preview_id)
    if record is None or record.expires_at < int(time.time()):
        return None
    return {
        "preview_id": record.preview_id,
        "project_name": record.project_name,
        "thread_id": record.thread_id,
        "expires_at": record.expires_at,
    }
