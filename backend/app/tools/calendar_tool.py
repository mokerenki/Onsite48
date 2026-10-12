"""
CalendarTool - Create calendar events.
"""

import os
from datetime import datetime, timezone
from typing import Any, Dict

import structlog

from app.tools.registry import BaseTool, ToolSchema

logger = structlog.get_logger("aether.tools.calendar")


class CalendarTool(BaseTool):
    def __init__(self):
        super().__init__()
        self.provider = os.getenv("CALENDAR_PROVIDER", "local")

    def _build_schema(self) -> ToolSchema:
        return ToolSchema(
            name="calendar",
            description="Create a calendar event. Configure CALENDAR_PROVIDER to 'google', 'outlook', or 'local'.",
            parameters={
                "title": {"type": "string", "description": "Event title"},
                "start": {"type": "string", "description": "Start ISO 8601 timestamp"},
                "end": {"type": "string", "description": "End ISO 8601 timestamp"},
                "attendees": {"type": "array", "items": {"type": "string"}, "description": "Attendee emails"},
                "location": {"type": "string", "description": "Event location"},
                "notes": {"type": "string", "description": "Event description/notes"},
            },
            required=["title", "start", "end"],
            irreversible=True,
            resources=["calendar_event"],
            operations=["create"],
            idempotency="write",
            exactness="exact",
        )

    async def execute(self, **kwargs) -> Dict[str, Any]:
        event = {
            "title": kwargs.get("title", "Untitled"),
            "start": kwargs.get("start"),
            "end": kwargs.get("end"),
            "attendees": kwargs.get("attendees", []),
            "location": kwargs.get("location", ""),
            "notes": kwargs.get("notes", ""),
            "provider": self.provider,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        if self.provider == "google":
            return await self._google_create(event)
        if self.provider == "outlook":
            return await self._outlook_create(event)
        logger.info("calendar.local_create", event=event)
        return {"output": f"Calendar event created locally: {event['title']} ({event['start']} - {event['end']})", "event": event}

    async def _google_create(self, event: Dict[str, Any]) -> Dict[str, Any]:
        try:
            from app.core import instances
            mcp_registry = getattr(instances, "mcp_registry", None)
            if not mcp_registry:
                logger.warning("calendar.mcp_registry_unavailable")
                return {"output": "Google Calendar provider not yet wired.", "event": event}

            client = mcp_registry.clients.get("google_calendar")
            if not client or not client._connected:
                logger.warning("calendar.google_client_unavailable")
                return {"output": "Google Calendar provider not yet wired.", "event": event}

            tools = await client.list_tools()
            tool_map = {t.name: t for t in tools}

            create_tool = None
            for candidate in ["create_event", "events_create", "calendar_events_insert", "insert_event"]:
                if candidate in tool_map:
                    create_tool = candidate
                    break

            if not create_tool:
                logger.warning("calendar.create_tool_not_found", available=list(tool_map.keys()))
                return {"output": "Google Calendar provider not yet wired.", "event": event}

            result = await client.call_tool(create_tool, event)
            return {"output": str(result)}
        except Exception as exc:
            logger.error("calendar.google_create_failed", error=str(exc))
            return {"output": f"Google Calendar provider not yet wired.", "event": event}

    async def _outlook_create(self, event: Dict[str, Any]) -> Dict[str, Any]:
        return {"output": "Outlook Calendar provider not yet wired.", "event": event}