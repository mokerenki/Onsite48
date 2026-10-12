"""Feature-gated desktop agent boundary.

General desktop control is deliberately not advertised until the observe/propose/act
loop has real permissions, recording, and verification implementations.
"""

from __future__ import annotations

import os
from typing import Any, Dict


class DesktopCapabilityUnavailable(RuntimeError):
    code = "desktop_agent_unavailable"


class DesktopAgent:
    """Controlled boundary for future local-desktop execution."""

    capability_name = "desktop_computer_use"

    def __init__(self, *, enabled: bool | None = None) -> None:
        self.enabled = bool(enabled if enabled is not None else os.getenv("SYNTHAI_ENABLE_DESKTOP_AGENT", "false").lower() in {"1", "true", "yes"})

    def status(self) -> dict[str, Any]:
        return {
            "capability": self.capability_name,
            "enabled": self.enabled,
            "state": "experimental" if self.enabled else "unavailable",
            "reason": None if self.enabled else "Desktop control is not enabled until application permissions and verified session recording are available.",
        }

    def _require_enabled(self) -> None:
        if not self.enabled:
            raise DesktopCapabilityUnavailable(self.status()["reason"])

    async def observe(self, state: Dict[str, Any]) -> Dict[str, Any]:
        self._require_enabled()
        raise DesktopCapabilityUnavailable("Desktop observation is not implemented in this release.")

    async def propose(self, observation: Dict[str, Any]) -> Dict[str, Any]:
        self._require_enabled()
        raise DesktopCapabilityUnavailable("Desktop action proposals are not implemented in this release.")

    async def act(self, proposal: Dict[str, Any]) -> Dict[str, Any]:
        self._require_enabled()
        raise DesktopCapabilityUnavailable("Desktop action execution is not implemented in this release.")