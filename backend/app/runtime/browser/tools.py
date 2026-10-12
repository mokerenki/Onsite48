"""Single browser toolset for the agent loop (protocol v4).

Replaces the fragmented computer_* / browser_* tools for the loop runtime.
Legacy tools remain importable but the loop prefers these.
"""
from __future__ import annotations

from typing import Any, Optional

import structlog

from app.runtime.browser.operator import BrowserOperator, BROWSER_TOOL_PARAMETERS
from app.runtime.browser.protocol import ActionRequest, ActionTarget
from app.runtime.tools import ToolSpec

logger = structlog.get_logger("onsite48.runtime.browser.tools")

# Per-thread operators (weak; process-local).
_operators: dict[str, BrowserOperator] = {}


def _get_operator(thread_id: str, page: Any) -> BrowserOperator:
    op = _operators.get(thread_id)
    if op is None or op.page is not page:
        op = BrowserOperator(page, thread_id=thread_id)
        _operators[thread_id] = op
    return op


async def execute_browser_action(
    thread_id: str,
    page: Any,
    *,
    action: str,
    observation_id: Optional[str] = None,
    element_ref: Optional[str] = None,
    coordinates: Optional[dict] = None,
    text: Optional[str] = None,
    key: Optional[str] = None,
    keys: Optional[list[str]] = None,
    direction: Optional[str] = None,
    amount: Optional[float] = None,
    url: Optional[str] = None,
    file_path: Optional[str] = None,
    option: Optional[str] = None,
    timeout_ms: Optional[int] = None,
    clear: bool = False,
    include_screenshot: bool = False,
    **_: Any,
) -> dict[str, Any]:
    op = _get_operator(thread_id, page)
    target = None
    if observation_id or element_ref or coordinates:
        target = ActionTarget(
            observation_id=observation_id,
            element_ref=element_ref,
            coordinates=coordinates,
        )
    req = ActionRequest(
        action=action,  # type: ignore[arg-type]
        target=target,
        text=text,
        key=key,
        keys=keys,
        direction=direction,
        amount=amount,
        url=url,
        file_path=file_path,
        option=option,
        timeout_ms=timeout_ms,
        clear=clear,
    )
    if action == "observe":
        obs = await op.observe(include_screenshot=include_screenshot)
        return {"ok": True, "action": "observe", "observation": obs.to_dict()}
    receipt = await op.act(req)
    return receipt.to_dict()


def make_browser_tool_spec(get_page) -> ToolSpec:
    """Build a ToolSpec that closes over a page provider."""

    async def _execute(**kwargs: Any) -> dict[str, Any]:
        from app.services.execution_identity import get_execution_identity
        ident = get_execution_identity()
        thread_id = getattr(ident, "task_thread_id", None) or "default"
        page = await get_page(thread_id)
        return await execute_browser_action(thread_id, page, **kwargs)

    return ToolSpec(
        name="browser",
        description=(
            "Operate the isolated sandbox browser (protocol v4). "
            "Always observe first. Actions reference observation_id + element_ref. "
            "If the receipt has stale_observation=true, re-observe and retry — never guess. "
            "Password, CAPTCHA, and payment fields return TAKEOVER/APPROVAL; do not bypass."
        ),
        parameters=BROWSER_TOOL_PARAMETERS,
        side_effect="read",
        idempotency="idempotent",
        timeout_s=60,
        max_output_chars=50_000,
        trust="untrusted",
        _execute=_execute,
    )
