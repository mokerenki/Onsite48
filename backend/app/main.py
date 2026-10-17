"""Reconstruct main module from base64 chunks (temporary restore vehicle)."""
from __future__ import annotations
from pathlib import Path
import base64

_chunks = [
    Path(__file__).with_name("main_chunk_0.b64").read_text(),
    Path(__file__).with_name("main_chunk_1.b64").read_text(),
    Path(__file__).with_name("main_chunk_2.b64").read_text(),
    Path(__file__).with_name("main_chunk_3.b64").read_text(),
    Path(__file__).with_name("main_chunk_4.b64").read_text(),
]
_src = base64.b64decode("".join(_chunks).encode()).decode()
_ns: dict = {"__name__": "app.main", "__file__": str(Path(__file__).resolve())}
exec(compile(_src, __file__, "exec"), _ns)
app = _ns["app"]
lifespan = _ns["lifespan"]
globals().update({k: v for k, v in _ns.items() if not k.startswith("_")})
