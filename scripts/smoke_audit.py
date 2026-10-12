from __future__ import annotations

import asyncio
from pathlib import Path

from fastapi.testclient import TestClient

from app.main import app
from app.api.connectors import CONNECTOR_CATALOG
from app.api.proactive import parse_chat_automation
from app.agents.codeact import CodeActRunner
from app.agents.planner import _website_build_clarification_plan


def main() -> None:
    routes = set(app.openapi().get("paths", {}))
    websocket_routes = {getattr(route, "path", "") for route in app.routes if route.__class__.__name__.endswith("WebSocketRoute")}
    required = {
        "/api/health",
        "/api/health/detailed",
        "/api/connectors",
        "/api/proactive/overview",
        "/api/bridge/devices",
    }
    missing = sorted(required - routes)
    if missing:
        raise AssertionError(f"missing routes: {missing}")
    assert "/ws" in websocket_routes and "/ws/bridge" in websocket_routes, websocket_routes

    client = TestClient(app)
    response = client.get("/api/health")
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "ok"
    client.close()

    assert len(CONNECTOR_CATALOG) >= 10, len(CONNECTOR_CATALOG)
    connector_ids = {str(item.get("id") or item.get("slug")) for item in CONNECTOR_CATALOG.values()} if isinstance(CONNECTOR_CATALOG, dict) else set()
    assert connector_ids or CONNECTOR_CATALOG, "connector catalog is empty"

    parsed = parse_chat_automation("Every Monday at 09:30, summarize new leads", "Europe/London")
    assert parsed.get("schedule") or parsed.get("scheduled") or parsed.get("run_at"), parsed

    clarification = _website_build_clarification_plan("Build a website for an unnamed startup")
    assert clarification and clarification[0].get("step_id") == "clarify-website-brief"
    complete = _website_build_clarification_plan(
        "Build a website for Acme called Acme Analytics with a black and white palette for data teams. Include hero, features, pricing, testimonials, contact, and a CTA."
    )
    assert complete is None, complete

    assert CodeActRunner._parse_proposal('{"code": "print(1)"}') == {"code": "print(1)"}
    assert CodeActRunner._parse_proposal("```python\nprint(1)\n```")["code"] == "print(1)"

    migration_files = sorted(Path("backend/alembic/versions").glob("*.py"))
    assert migration_files, "no migrations found"
    print(f"routes={len(routes)} websockets={len(websocket_routes)} connectors={len(CONNECTOR_CATALOG)} migrations={len(migration_files)} smoke=ok")


if __name__ == "__main__":
    main()