"""Onsite48 FastAPI entrypoint — reconstructed from restore chunks."""
from __future__ import annotations

import base64
from pathlib import Path

_dir = Path(__file__).resolve().parent
_parts = sorted(_dir.glob("restore_chunk_*.b64"), key=lambda p: int(p.stem.split("_")[-1]))
if not _parts:
    raise RuntimeError("main.py restore chunks missing (restore_chunk_*.b64)")
_src = base64.b64decode("".join(p.read_text() for p in _parts).encode()).decode()
_ns: dict = {"__name__": "app.main", "__file__": str(Path(__file__).resolve())}
exec(compile(_src, str(Path(__file__).resolve()), "exec"), _ns)
# Re-export public surface expected by workers / ASGI
app = _ns["app"]
lifespan = _ns["lifespan"]
for _k, _v in _ns.items():
    if not _k.startswith("_"):
        globals()[_k] = _v
