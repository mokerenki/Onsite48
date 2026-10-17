"""Phase 9 — Run artifact packaging.

Collects workspace outputs and finish metadata into a structured artifact
manifest the API / UI can list and download. Complements artifact_contract
and artifact_versions services.
"""
from __future__ import annotations

import hashlib
import mimetypes
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class ArtifactItem:
    path: str
    name: str
    size_bytes: int
    content_type: str
    sha256: str
    kind: str = "file"  # file | log | preview | package

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ArtifactManifest:
    run_id: str
    goal: str
    created_at: float = field(default_factory=time.time)
    items: list[ArtifactItem] = field(default_factory=list)
    finish_status: str = ""
    finish_summary: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "goal": self.goal,
            "created_at": self.created_at,
            "finish_status": self.finish_status,
            "finish_summary": self.finish_summary[:2000],
            "items": [i.to_dict() for i in self.items],
            "count": len(self.items),
            "total_bytes": sum(i.size_bytes for i in self.items),
        }


def _hash_file(path: Path, *, max_bytes: int = 5_000_000) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        remaining = max_bytes
        while remaining > 0:
            chunk = f.read(min(65536, remaining))
            if not chunk:
                break
            h.update(chunk)
            remaining -= len(chunk)
    return h.hexdigest()


def collect_workspace_artifacts(
    workspace_root: str | Path,
    *,
    run_id: str,
    goal: str = "",
    finish_status: str = "",
    finish_summary: str = "",
    max_files: int = 100,
    max_file_bytes: int = 20_000_000,
) -> ArtifactManifest:
    """Scan a workspace directory and build an artifact manifest."""
    root = Path(workspace_root)
    manifest = ArtifactManifest(
        run_id=run_id,
        goal=goal or "",
        finish_status=finish_status,
        finish_summary=finish_summary or "",
    )
    if not root.exists() or not root.is_dir():
        return manifest

    skip_names = {".workspace_meta.json", "__pycache__", ".git"}
    count = 0
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        if path.name in skip_names or any(p in skip_names for p in path.parts):
            continue
        try:
            size = path.stat().st_size
        except OSError:
            continue
        if size > max_file_bytes:
            continue
        rel = str(path.relative_to(root))
        ctype, _ = mimetypes.guess_type(path.name)
        kind = "file"
        lower = path.name.lower()
        if lower.endswith((".png", ".jpg", ".jpeg", ".webp")):
            kind = "preview"
        elif lower.endswith((".log", ".txt")) and "log" in lower:
            kind = "log"
        elif lower.endswith((".zip", ".tar", ".gz")):
            kind = "package"
        try:
            digest = _hash_file(path)
        except OSError:
            digest = ""
        manifest.items.append(
            ArtifactItem(
                path=rel,
                name=path.name,
                size_bytes=size,
                content_type=ctype or "application/octet-stream",
                sha256=digest,
                kind=kind,
            )
        )
        count += 1
        if count >= max_files:
            break
    return manifest


def manifest_from_paths(
    paths: list[str],
    *,
    run_id: str,
    goal: str = "",
    base: str | Path | None = None,
) -> ArtifactManifest:
    """Build a manifest from explicit relative/absolute paths."""
    base_path = Path(base) if base else None
    manifest = ArtifactManifest(run_id=run_id, goal=goal or "")
    for p in paths:
        path = Path(p)
        if base_path and not path.is_absolute():
            path = base_path / path
        if not path.is_file():
            continue
        size = path.stat().st_size
        ctype, _ = mimetypes.guess_type(path.name)
        try:
            digest = _hash_file(path)
        except OSError:
            digest = ""
        rel = str(path.relative_to(base_path)) if base_path and path.is_relative_to(base_path) else path.name
        manifest.items.append(
            ArtifactItem(
                path=rel,
                name=path.name,
                size_bytes=size,
                content_type=ctype or "application/octet-stream",
                sha256=digest,
            )
        )
    return manifest


__all__ = [
    "ArtifactItem",
    "ArtifactManifest",
    "collect_workspace_artifacts",
    "manifest_from_paths",
]
