#!/usr/bin/env python3
"""Validate a backup bundle without touching production services.

This is intentionally a rehearsal validator, not a destructive restore command.
It verifies the manifest, checksums, and archive readability before a separate
operator-controlled restore into an isolated environment.
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
import tarfile
import time
from pathlib import Path


def usage() -> None:
    print("usage: restore_rehearsal.py [BUNDLE_PATH]\n\nValidates a backup bundle and reports measured rehearsal time.")


def main() -> int:
    if any(arg in {"-h", "--help"} for arg in sys.argv[1:]):
        usage()
        return 0
    root = Path(os.getenv("BACKUP_ROOT", "./backups"))
    latest = root / "latest"
    if len(sys.argv) > 1:
        bundle = Path(sys.argv[1])
    elif latest.exists():
        bundle = Path(latest.read_text(encoding="utf-8").strip())
    else:
        raise RuntimeError(f"no backup bundle supplied and {latest} does not exist")
    started = time.monotonic()
    manifest_path = bundle / "manifest.json"
    if not manifest_path.exists():
        raise RuntimeError(f"missing {manifest_path}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    checked: list[str] = []
    for item in manifest.get("items", []):
        path = bundle / str(item.get("path", ""))
        if not path.exists():
            raise RuntimeError(f"manifest item is missing: {path}")
        expected = str(item.get("sha256", ""))
        if expected and hashlib.sha256(path.read_bytes()).hexdigest() != expected:
            raise RuntimeError(f"checksum mismatch: {path.name}")
    for archive in bundle.glob("*.tar.gz"):
        with tarfile.open(archive, "r:gz") as handle:
            handle.getmembers()
        checked.append(archive.name)
    elapsed = time.monotonic() - started
    rto = int(manifest.get("rto_target_minutes", 120)) * 60
    result = {
        "bundle": str(bundle),
        "archives_checked": checked,
        "manifest_items_checked": len(manifest.get("items", [])),
        "elapsed_seconds": round(elapsed, 3),
        "rto_target_seconds": rto,
        "rto_pass": elapsed <= rto,
        "encryption_key_fingerprint_present": bool((manifest.get("encryption_key") or {}).get("sha256_prefix")),
        "restore_mode": "rehearsal_only",
    }
    print(json.dumps(result, indent=2))
    return 0 if result["rto_pass"] else 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, RuntimeError) as exc:
        print(f"restore rehearsal failed: {exc}", file=sys.stderr)
        raise SystemExit(2)