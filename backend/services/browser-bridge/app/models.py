"""Models for the standalone Browser Bridge service."""
from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, Field


class ExtensionAuth(BaseModel):
    token: str


class CommandPayload(BaseModel):
    command_type: str
    target_url: Optional[str] = None
    target_tab: Optional[int] = None
    arguments: dict[str, Any] = Field(default_factory=dict)


class StatusUpdate(BaseModel):
    session_id: Optional[str] = None
    status: str
    current_url: Optional[str] = None
    tab_ids: list[int] = Field(default_factory=list)
