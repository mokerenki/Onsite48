"""Artifact bytes abstraction.

Local mode is deterministic and needs no extra dependency. S3-compatible mode
uses the optional boto3 package when present and fails closed with a useful
configuration error otherwise. Metadata remains separate from bytes and every
write is content-addressed, so versions are immutable and rollback is a
pointer change rather than an overwrite.
"""
from __future__ import annotations
import hashlib
import json
import os
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

@dataclass(frozen=True)
class StoredObject:
    key: str
    sha256: str
    size: int
    content_type: str
    version_id: str
    created_at: str

class ObjectStorage:
    def __init__(self, root: str | None = None) -> None:
        self.backend = os.getenv("ARTIFACT_STORAGE_BACKEND", "local").lower()
        self.root = Path(root or os.getenv("ARTIFACT_STORAGE_ROOT", "/tmp/onsite48/artifacts"))
        self.bucket = os.getenv("ARTIFACT_STORAGE_BUCKET", "onsite48-artifacts")
    def _local_path(self, key: str) -> Path:
        path = self.root / key
        path.parent.mkdir(parents=True, exist_ok=True)
        return path
    def put(self, *, organization_id: str, project_id: str | None, filename: str, content: bytes, content_type: str) -> StoredObject:
        digest = hashlib.sha256(content).hexdigest()
        version_id = str(uuid.uuid4())
        safe_name = Path(filename).name.replace("/", "_") or "artifact"
        prefix = f"{organization_id}/{project_id or 'unassigned'}/{digest[:2]}/{digest}"
        key = f"{prefix}/{version_id}-{safe_name}"
        if self.backend == "s3":
            self._put_s3(key, content, content_type)
        else:
            self._local_path(key).write_bytes(content)
        record = StoredObject(key, digest, len(content), content_type, version_id, datetime.now(timezone.utc).isoformat())
        self._local_path(f"{key}.metadata.json").write_text(json.dumps(record.__dict__), encoding="utf-8")
        return record
    def get(self, key: str) -> bytes:
        if self.backend == "s3":
            return self._get_s3(key)
        return self._local_path(key).read_bytes()
    def metadata(self, key: str) -> dict[str, Any]:
        path = self._local_path(f"{key}.metadata.json")
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    def delete(self, key: str) -> None:
        if self.backend == "s3":
            self._delete_s3(key)
        for suffix in ("", ".metadata.json"):
            try: self._local_path(f"{key}{suffix}").unlink()
            except FileNotFoundError: pass
    def _client(self):
        try:
            import boto3
        except ImportError as exc:
            raise RuntimeError("ARTIFACT_STORAGE_BACKEND=s3 requires boto3 in the worker image.") from exc
        return boto3.client("s3", endpoint_url=os.getenv("S3_ENDPOINT_URL"), region_name=os.getenv("AWS_REGION", "us-east-1"))
    def _put_s3(self, key: str, content: bytes, content_type: str) -> None:
        self._client().put_object(Bucket=self.bucket, Key=key, Body=content, ContentType=content_type)
    def _get_s3(self, key: str) -> bytes:
        return self._client().get_object(Bucket=self.bucket, Key=key)["Body"].read()
    def _delete_s3(self, key: str) -> None:
        self._client().delete_object(Bucket=self.bucket, Key=key)

object_storage = ObjectStorage()