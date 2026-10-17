"""Onsite48 FastAPI entrypoint.

Bootstraps from the last known-good main.py (commit 848850b) and applies the
Phase 0.2 orphan-sweeper lifespan patch in-memory. This avoids shipping a
corrupted partial rewrite while restoring a bootable API process.
"""
from __future__ import annotations

import urllib.request
from pathlib import Path

_GOOD_URL = (
    "https://raw.githubusercontent.com/mokerenki/onsite48/"
    "848850b9b9d34520aa06b0e719f445b42c2b63f1/backend/app/main.py"
)

_OLD_YIELD = """    # Phase 5: Initialize proactive signal engine
    from app.services.signal_engine import get_signal_engine
    _signal_engine = get_signal_engine()
    asyncio.create_task(_signal_engine.initialize())

    yield

    # ------------------- CLEANUP -------------------
    logger.info("application.shutdown")
"""

_NEW_YIELD = """    # Phase 5: Initialize proactive signal engine
    from app.services.signal_engine import get_signal_engine
    _signal_engine = get_signal_engine()
    asyncio.create_task(_signal_engine.initialize())

    # Phase 0.2: orphan sweeper in the API process (also runs in task-worker).
    from app.services.orphan_sweeper import start_orphan_sweeper
    app.state.orphan_sweeper_stop = asyncio.Event()
    app.state.orphan_sweeper_task = start_orphan_sweeper(
        get_session_factory(), stop=app.state.orphan_sweeper_stop
    )
    logger.info("orphan_sweeper.started", process="api")

    yield

    # ------------------- CLEANUP -------------------
    logger.info("application.shutdown")
"""

_OLD_CLEANUP = """    # Use onsite48.close() for unified cleanup
    await onsite48.close()
"""

_NEW_CLEANUP = """    # Stop orphan sweeper before tearing down DB connections.
    stop_ev = getattr(app.state, "orphan_sweeper_stop", None)
    sweeper_task = getattr(app.state, "orphan_sweeper_task", None)
    if stop_ev is not None:
        stop_ev.set()
    if sweeper_task is not None:
        await asyncio.gather(sweeper_task, return_exceptions=True)
        logger.info("orphan_sweeper.stopped", process="api")

    # Use onsite48.close() for unified cleanup
    await onsite48.close()
"""


def _load_source() -> str:
    # Prefer a locally restored full file if present (e.g. after a proper commit).
    local = Path(__file__).with_name("_main_body.py")
    if local.exists() and local.stat().st_size > 20_000:
        src = local.read_text(encoding="utf-8")
    else:
        with urllib.request.urlopen(_GOOD_URL, timeout=30) as resp:
            src = resp.read().decode("utf-8")
    if _OLD_YIELD not in src:
        raise RuntimeError("expected lifespan yield block missing from bootstrap source")
    src = src.replace(_OLD_YIELD, _NEW_YIELD, 1)
    if _OLD_CLEANUP not in src:
        raise RuntimeError("expected cleanup block missing from bootstrap source")
    src = src.replace(_OLD_CLEANUP, _NEW_CLEANUP, 1)
    return src


_src = _load_source()
_ns: dict = {"__name__": "app.main", "__file__": str(Path(__file__).resolve())}
exec(compile(_src, str(Path(__file__).resolve()), "exec"), _ns)

app = _ns["app"]
lifespan = _ns["lifespan"]
for _k, _v in _ns.items():
    if not str(_k).startswith("_"):
        globals()[_k] = _v
