"""Versioned artifact manifest operations.

Artifact bytes never mutate. A manifest maps a logical artifact to immutable
object keys, records parent lineage, and makes rollback an append-only pointer
operation.
"""
from __future__ import annotations
import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from app.services.object_storage import ObjectStorage, object_storage

class ArtifactVersionStore:
    def __init__(self, storage: ObjectStorage | None = None) -> None:
        self.storage = storage or object_storage
        self.root = Path(os.getenv("ARTIFACT_MANIFEST_ROOT", "/tmp/onsite48/artifact-manifests"))
    def _path(self, organization_id: str, artifact_id: str) -> Path:
        path = self.root / organization_id / f"{artifact_id}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        return path
    def _load(self, organization_id: str, artifact_id: str) -> dict[str, Any]:
        path = self._path(organization_id, artifact_id)
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {"artifact_id": artifact_id, "versions": [], "current_version": None}
    def _save(self, organization_id: str, artifact_id: str, manifest: dict[str, Any]) -> None:
        self._path(organization_id, artifact_id).write_text(json.dumps(manifest, sort_keys=True), encoding="utf-8")
    def create_version(self, *, organization_id: str, project_id: str | None, artifact_id: str | None, filename: str, content: bytes, content_type: str, preview: dict[str, Any] | None = None, parent_version_id: str | None = None) -> dict[str, Any]:
        logical_id = artifact_id or str(uuid.uuid4())
        stored = self.storage.put(organization_id=organization_id, project_id=project_id, filename=filename, content=content, content_type=content_type)
        manifest = self._load(organization_id, logical_id)
        item = {"version_id": stored.version_id, "artifact_id": logical_id, "object_key": stored.key, "filename": filename, "content_type": content_type, "sha256": stored.sha256, "size": stored.size, "preview": preview or {}, "parent_version_id": parent_version_id or manifest.get("current_version"), "created_at": stored.created_at}
        manifest["versions"].append(item)
        manifest["current_version"] = stored.version_id
        self._save(organization_id, logical_id, manifest)
        return item
    def get(self, *, organization_id: str, artifact_id: str) -> dict[str, Any]:
        return self._load(organization_id, artifact_id)
    def rollback(self, *, organization_id: str, artifact_id: str, version_id: str) -> dict[str, Any]:
        manifest = self._load(organization_id, artifact_id)
        if not any(item.get("version_id") == version_id for item in manifest["versions"]):
            raise KeyError("artifact version not found")
        manifest["current_version"] = version_id
        manifest["rollback_at"] = datetime.now(timezone.utc).isoformat()
        self._save(organization_id, artifact_id, manifest)
        return manifest
    def export(self, *, organization_id: str, artifact_id: str, version_id: str | None = None) -> tuple[dict[str, Any], bytes]:
        manifest = self._load(organization_id, artifact_id)
        target = version_id or manifest.get("current_version")
        item = next((x for x in manifest["versions"] if x.get("version_id") == target), None)
        if item is None: raise KeyError("artifact version not found")
        return item, self.storage.get(str(item["object_key"]))

artifact_versions = ArtifactVersionStore()