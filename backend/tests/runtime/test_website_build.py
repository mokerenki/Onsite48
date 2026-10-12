from __future__ import annotations

import uuid
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

import pytest

from app.runtime.website_build import (
    audit_static_site,
    export_project_files,
    is_website_build_request,
)
from app.services import website_preview


class _LocalWorkspace:
    def __init__(self, root: Path):
        self._home = root
        self._root = root

    def _resolve(self, value: str) -> Path:
        result = (self._root / value).resolve()
        result.relative_to(self._root.resolve())
        return result


@pytest.mark.parametrize("prompt", [
    "Build a website for a cafe",
    "Create a responsive landing page",
    "Update the website's mobile navigation",
    "Develop a web app for planning shifts",
])
def test_website_creation_requests_are_build_intent(prompt):
    assert is_website_build_request(prompt)


@pytest.mark.parametrize("prompt", [
    "Visit the company website and summarize the pricing page",
    "Open https://example.com and inspect the current page",
    "Click the login button in the browser",
])
def test_browsing_is_not_website_build_intent(prompt):
    assert not is_website_build_request(prompt)


@pytest.mark.asyncio
async def test_export_project_files_skips_dependencies_and_hidden_data(tmp_path):
    root = tmp_path / "workspace"
    (root / "site").mkdir(parents=True)
    (root / "site" / "index.html").write_text("<html></html>")
    (root / "site" / "node_modules").mkdir()
    (root / "site" / "node_modules" / "dependency.js").write_text("ignored")
    (root / "site" / ".env").write_text("secret")

    files = await export_project_files(_LocalWorkspace(root), "site")

    assert list(files) == ["index.html"]
    assert files["index.html"] == b"<html></html>"


@pytest.mark.asyncio
async def test_preview_route_rewrites_root_relative_html_and_css_assets(tmp_path, monkeypatch):
    from app.api.web_previews import get_preview_file

    monkeypatch.setattr(website_preview, "PREVIEW_ROOT", tmp_path / "previews")
    preview = website_preview.create_preview(
        {
            "index.html": b'<html><head><base href="/old/"></head><body><img src="/assets/logo.svg"></body></html>',
            "assets/site.css": b"body { background: url('/assets/background.png'); }",
            "assets/logo.svg": b"<svg></svg>",
            "assets/background.png": b"fake-png",
        },
        user_id="user-1",
        organization_id="org-1",
        thread_id="thread-1",
        project_name="Root paths",
    )
    parts = preview["preview_url"].split("/")
    preview_id, expires, signature = parts[3], int(parts[4]), parts[5]

    html = await get_preview_file(preview_id, expires, signature, "index.html")
    css = await get_preview_file(preview_id, expires, signature, "assets/site.css")

    assert b"<base href=\"/api/web-previews/" in html.body
    assert b"/api/web-previews/" in html.body and b"assets/logo.svg" in html.body
    assert b"/api/web-previews/" in css.body and b"assets/background.png" in css.body


def test_static_audit_detects_responsive_and_missing_local_assets():
    result = audit_static_site({
        "index.html": b'<!doctype html><html><head><title>Test</title></head><body><img src="missing.png"></body></html>'
    })
    assert result["ok"] is False
    assert "missing responsive viewport meta tag" in result["issues"]
    assert any("missing.png" in issue for issue in result["issues"])


def test_signed_preview_survives_process_local_registry_and_rejects_traversal(tmp_path, monkeypatch):
    root = tmp_path / "previews"
    monkeypatch.setattr(website_preview, "PREVIEW_ROOT", root)
    preview = website_preview.create_preview(
        {"index.html": b"<!doctype html><h1>hello</h1>", "assets/site.css": b"body{}"},
        user_id="user-1",
        organization_id="org-1",
        thread_id="thread-1",
        project_name="Example",
    )
    preview_id = preview["preview_id"]
    signature = preview["preview_url"].split("/")[5]
    expires = preview["expires_at"]

    # Simulate a request landing on another worker process: hydrate metadata from disk.
    website_preview._previews.pop(preview_id)
    found = website_preview.resolve_preview_file(preview_id, expires, signature, "index.html")
    assert found is not None
    assert found[1].read_bytes().startswith(b"<!doctype html>")
    assert website_preview.resolve_preview_file(preview_id, expires, signature, "../secret") is None
    assert website_preview.resolve_preview_file(preview_id, expires, "invalid", "index.html") is None


class _FakeResponse:
    def __init__(self, tool_calls):
        self.content = ""
        self.tool_calls = tool_calls
        self.usage = {"input_tokens": 2, "output_tokens": 1}
        self.cost_usd = 0.0
        self.path = "test"
        self.model = "test"


@pytest.mark.asyncio
async def test_publish_waits_for_exact_user_approval_then_replays_same_call():
    from app.runtime import loop as loop_module
    from app.runtime.journal import Journal
    from app.agents.legacy_compat import confirmation_key

    run_id = str(uuid.uuid4())
    thread_id = str(uuid.uuid4())
    journal = Journal(session_factory=None)
    Journal._memory.pop(run_id, None)
    calls: list[dict] = []
    publish_args = {"path": "site", "target": "staging"}
    responses = iter([
        _FakeResponse([{"id": "publish-call", "name": "publish_website", "arguments": publish_args}]),
        _FakeResponse([{"id": "finish-call", "name": "finish", "arguments": {"summary": "Staging published.", "status": "completed"}}]),
    ])

    async def fake_model(*_args, **_kwargs):
        return next(responses)

    async def fake_verifier(**_kwargs):
        return {"verdict": "pass", "unmet_requirements": [], "missing_evidence": [], "notes": "test evidence"}

    real_build_tool_specs = loop_module.build_tool_specs

    def fake_build_tool_specs(*args, **kwargs):
        specs = real_build_tool_specs(*args, **kwargs)

        async def fake_publish(path: str, target: str, **_kwargs):
            calls.append({"path": path, "target": target})
            return {"published": True, "target": target}

        specs["publish_website"] = replace(specs["publish_website"], _execute=fake_publish)
        return specs

    with patch.object(loop_module, "build_tool_specs", side_effect=fake_build_tool_specs), patch.object(
        loop_module, "model_complete", side_effect=fake_model
    ):
        waiting = await loop_module.run(
            run_id, "Publish the website to staging.", {},
            journal=journal, thread_id=thread_id, max_steps=5,
        )
    assert waiting["status"] == "waiting_approval"
    assert waiting["result"]["pending_call"]["arguments"] == publish_args
    assert calls == []

    approved_key = confirmation_key(thread_id, 0, "publish_website", publish_args)
    with patch.object(loop_module, "build_tool_specs", side_effect=fake_build_tool_specs), patch.object(
        loop_module, "model_complete", side_effect=fake_model
    ), patch.object(loop_module, "verify_completion", side_effect=fake_verifier):
        completed = await loop_module.run(
            run_id, "Publish the website to staging.", {},
            journal=journal, thread_id=thread_id, confirmed_actions={approved_key: True}, max_steps=5,
        )
    assert completed["status"] == "completed"
    assert calls == [publish_args], f"tool call was not executed: result={completed!r}; events={await journal.list_events(run_id)!r}"


@pytest.mark.asyncio
async def test_publish_rejection_never_executes_tool():
    from app.runtime import loop as loop_module
    from app.runtime.journal import Journal

    run_id = str(uuid.uuid4())
    thread_id = str(uuid.uuid4())
    journal = Journal(session_factory=None)
    Journal._memory.pop(run_id, None)
    args = {"path": "site", "target": "production"}

    async def fake_model(*_args, **_kwargs):
        return _FakeResponse([{"id": "call", "name": "publish_website", "arguments": args}])

    with patch.object(loop_module, "model_complete", side_effect=fake_model):
        waiting = await loop_module.run(run_id, "Publish the site to production.", {}, journal=journal, thread_id=thread_id)
    assert waiting["status"] == "waiting_approval"

    rejected = await loop_module.run(
        run_id,
        "Publish the site to production.",
        {},
        journal=journal,
        thread_id=thread_id,
        rejected_tool_call={"tool_name": "publish_website", "tool_args": args},
    )
    assert rejected["status"] == "cancelled"


@pytest.mark.asyncio
async def test_graph_retains_runtime_website_events_for_websocket_delivery():
    from app import graph as graph_module
    from app.runtime import loop as loop_module

    async def fake_run(*_args, event_emitter=None, **_kwargs):
        await event_emitter({
            "type": "website_preview",
            "preview_id": "preview-1",
            "preview_url": "/api/web-previews/preview-1/1/signature/index.html",
            "project_name": "Test site",
            "expires_at": 9999999999,
        })
        await event_emitter({
            "type": "artifact_created",
            "artifact": {"attachment_id": "artifact-1", "name": "site.zip"},
        })
        return {"status": "completed", "result": {"summary": "Site ready."}, "step_count": 2}

    compiled_graph = graph_module.create_graph(None, None, None)
    with patch.object(loop_module, "run", side_effect=fake_run):
        updates = [
            update async for update in compiled_graph.astream({
                "task_id": "task-1",
                "thread_id": "thread-1",
                "task": "Build a site",
            })
        ]

    node_state = next(iter(updates[0].values()))
    assert [event["type"] for event in node_state["website_events"]] == [
        "website_preview", "artifact_created"
    ]
