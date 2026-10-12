#!/usr/bin/env python3
"""Create a timestamped, manifest-backed backup bundle.

The script is safe to run from cron or a worker container. It uses installed
CLIs where available, records encryption-key *presence and fingerprint* without
ever copying the secret into the manifest, and fails loudly when a required
backup component cannot be captured.
"""
from __future__ import annotations
import hashlib, json, os, shutil, subprocess, sys, tarfile, time
from datetime import datetime, timezone
from pathlib import Path

def truthy(value: str) -> bool:
    return value.strip().lower() in {"1", "true", "yes", "on"}

def add_file_item(items: list[dict[str, object]], component: str, path: Path) -> None:
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    items.append({"component": component, "path": path.name, "bytes": path.stat().st_size, "sha256": digest})

def run(command: list[str], output: Path) -> dict[str, object]:
    started = time.monotonic(); result = subprocess.run(command, capture_output=True, text=True)
    if result.returncode != 0: raise RuntimeError(f"{' '.join(command)} failed: {result.stderr[-500:]}")
    output.write_text(result.stdout, encoding="utf-8")
    return {"command": command, "duration_seconds": round(time.monotonic() - started, 3), "bytes": output.stat().st_size}

def main() -> int:
    strict = "--strict" in sys.argv[1:] or truthy(os.getenv("BACKUP_STRICT", "false"))
    root = Path(os.getenv("BACKUP_ROOT", "./backups")); stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ"); bundle = root / stamp; bundle.mkdir(parents=True, exist_ok=False); manifest: dict[str, object] = {"created_at": stamp, "strict": strict, "rpo_target_minutes": int(os.getenv("RPO_TARGET_MINUTES", "60")), "rto_target_minutes": int(os.getenv("RTO_TARGET_MINUTES", "120")), "items": []}
    pg_dump = shutil.which("pg_dump")
    if pg_dump and os.getenv("DATABASE_URL"):
        run([pg_dump, "--dbname", os.environ["DATABASE_URL"], "--format", "custom", "--file", str(bundle / "postgres.dump")], bundle / "postgres.command.json")
        add_file_item(manifest["items"], "postgres", bundle / "postgres.dump")
    elif strict: raise RuntimeError("strict backup requires pg_dump and DATABASE_URL")
    else: print("WARNING: pg_dump or DATABASE_URL unavailable; PostgreSQL backup skipped", file=sys.stderr)
    redis_cli = shutil.which("redis-cli")
    if redis_cli and os.getenv("REDIS_URL"):
        run([redis_cli, "-u", os.environ["REDIS_URL"], "--rdb", str(bundle / "redis.rdb")], bundle / "redis.command.json")
        add_file_item(manifest["items"], "redis", bundle / "redis.rdb")
    elif strict: raise RuntimeError("strict backup requires redis-cli and REDIS_URL")
    else: print("WARNING: redis-cli or REDIS_URL unavailable; Redis backup skipped", file=sys.stderr)
    qdrant_root = os.getenv("QDRANT_SNAPSHOT_ROOT", "").strip()
    if qdrant_root:
        qdrant = Path(qdrant_root)
        if not qdrant.exists():
            raise RuntimeError(f"QDRANT_SNAPSHOT_ROOT does not exist: {qdrant}")
        with tarfile.open(bundle / "qdrant.tar.gz", "w:gz") as archive: archive.add(qdrant, arcname="qdrant")
        add_file_item(manifest["items"], "qdrant", bundle / "qdrant.tar.gz")
    elif strict: raise RuntimeError("strict backup requires QDRANT_SNAPSHOT_ROOT")
    attachments = Path(os.getenv("ATTACHMENT_STORAGE_ROOT", "/tmp/onsite48/uploads"))
    if attachments.exists():
        with tarfile.open(bundle / "attachments.tar.gz", "w:gz") as archive: archive.add(attachments, arcname="attachments")
        add_file_item(manifest["items"], "attachments", bundle / "attachments.tar.gz")
    elif strict: raise RuntimeError("strict backup requires ATTACHMENT_STORAGE_ROOT")
    key = os.getenv("CONNECTOR_ENCRYPTION_KEY", "")
    if strict and not key: raise RuntimeError("strict backup requires CONNECTOR_ENCRYPTION_KEY")
    manifest["encryption_key"] = {"present": bool(key), "sha256_prefix": hashlib.sha256(key.encode()).hexdigest()[:16] if key else None, "restore_required": True}
    (bundle / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    (root / "latest").write_text(str(bundle), encoding="utf-8")
    print(json.dumps({"bundle": str(bundle), "manifest": manifest}, indent=2)); return 0
if __name__ == "__main__": raise SystemExit(main())