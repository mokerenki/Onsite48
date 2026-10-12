"""Print a deterministic digest of the tracked source tree.

This is intentionally based on Git's file list rather than filesystem order or
mtime, so CI, local builds, and release notes can refer to the same source
identity. Container image digests remain a deployment concern; this digest is
the reproducible input identity that should be recorded alongside them.
"""
from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def main() -> None:
    tracked = subprocess.check_output(
        ["git", "-C", str(ROOT), "ls-files", "-co", "--exclude-standard", "-z"], text=False
    ).split(b"\0")
    paths = [item for item in tracked if item]
    digest = hashlib.sha256()
    for relative in paths:
        path = ROOT / relative.decode("utf-8")
        if not path.is_file():
            raise SystemExit(f"tracked source file is missing: {relative.decode()}")
        data = path.read_bytes()
        digest.update(relative)
        digest.update(b"\0")
        digest.update(str(len(data)).encode("ascii"))
        digest.update(b"\0")
        digest.update(data)
        digest.update(b"\0")
    print(f"tracked_files={len(paths)} source_digest=sha256:{digest.hexdigest()}")


if __name__ == "__main__":
    main()