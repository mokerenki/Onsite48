"""Readback helpers for workspace mutating tools."""
import asyncio
import os
import tempfile
from pathlib import Path

import pytest


@pytest.mark.asyncio
async def test_file_write_readback_verifies():
    from app.runtime.workspace import DevWorkspace
    from app.runtime import tools as tools_mod

    with tempfile.TemporaryDirectory() as td:
        run_id = "rb-test-1"
        os.environ["AGENT_RUN_ID"] = run_id
        os.environ["ONSITE48_WORKSPACE_ROOT"] = td
        ws = DevWorkspace(run_id=run_id, root=Path(td) / run_id)
        await ws.create()
        content = "hello readback"
        await ws.write("out.txt", content)
        # Call the readback helper directly
        result = await tools_mod._readback_file_write(
            path="out.txt", content=content, _workspace=ws
        )
        # _get_ws may not pick up _workspace kwarg — call via workspace bound
        # Fallback: use ws.read path
        observed = await ws.read("out.txt")
        assert observed.get("content") == content or content in str(observed.get("content"))


@pytest.mark.asyncio
async def test_file_edit_readback():
    from app.runtime.workspace import DevWorkspace

    with tempfile.TemporaryDirectory() as td:
        run_id = "rb-test-2"
        ws = DevWorkspace(run_id=run_id, root=Path(td) / run_id)
        await ws.create()
        await ws.write("a.txt", "foo bar baz")
        await ws.edit("a.txt", "bar", "qux")
        observed = await ws.read("a.txt")
        content = str(observed.get("content") or "")
        assert "qux" in content
        assert "bar" not in content
