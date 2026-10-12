"""
BrowserControlTool -- real interactive browser automation via `browser_use`.

Distinct from tools/browser_tool.py ("web_search"): that tool answers factual
questions with a Tavily search. This tool DRIVES a real browser -- navigate,
click, fill forms, extract content, manage tabs -- for tasks that require
interacting with a live web page (portals, dashboards, internal tools,
anything behind a login).

Adapted from OpenManus (github.com/FoundationAgents/OpenManus,
app/tool/browser_use_tool.py, MIT License, Copyright (c) 2025 manna_and_poem)
onto Synth's BaseTool/ToolSchema interface. Key differences from the
OpenManus original:
  - One BrowserContext per *task*, not one global browser for the process
    (services/browser_automation_service.py had this bug -- a single shared
    `self.page` -- which breaks under concurrent tasks. This tool keeps a
    dict of contexts keyed by task_id instead.)
  - Returns Synth's plain dict shape ({"output": ...}) instead of OpenManus's
    pydantic ToolResult, to match every other tool in this registry.
  - No dependency on OpenManus's own LLM/config/WebSearch classes -- those
    are replaced with nothing (web_search stays a separate tool you already
    have) and the DOM/vision loop is driven entirely by the calling agent.

Requires: browser-use>=0.1.0 (already in requirements.txt), playwright
(already in requirements.txt -- run `playwright install chromium` once).
"""

import asyncio
from typing import Dict, Optional

import structlog
from typing import Any

try:
    # `browser-use` has changed its public browser/context API repeatedly.
    # This legacy tool is intentionally not registered by the application;
    # Agent Computer is the supported task-scoped browser runtime. Keep this
    # import optional so the backend remains importable with current declared
    # dependency versions and return an actionable error if someone invokes it.
    from browser_use.browser.browser import Browser as BrowserUseBrowser
    from browser_use.browser.context import BrowserContext
except ImportError:  # pragma: no cover - depends on browser-use release
    BrowserUseBrowser = None  # type: ignore[assignment,misc]
    BrowserContext = Any  # type: ignore[assignment,misc]

from app.tools.registry import BaseTool, ToolSchema

logger = structlog.get_logger("aether.tools.browser_control")

MAX_EXTRACT_CONTENT_LENGTH = 8000


class BrowserControlTool(BaseTool):
    """
    Stateful browser control, one live session per task_id.

    Call sequence from the agent looks like:
        browser_control(task_id="t1", action="go_to_url", url="https://…")
        browser_control(task_id="t1", action="get_state")          # <- shows numbered elements
        browser_control(task_id="t1", action="click_element", index=4)
        browser_control(task_id="t1", action="close")              # <- release the session
    """

    def __init__(self):
        # task_id -> (BrowserUseBrowser, BrowserContext)
        self._sessions: Dict[str, tuple] = {}
        self._lock = asyncio.Lock()
        super().__init__()

    def _build_schema(self) -> ToolSchema:
        return ToolSchema(
            name="browser_control",
            description=(
                "Drive a real browser session: navigate, click, type, scroll, "
                "extract content, and manage tabs. Use this (not 'browser') "
                "whenever the task requires interacting with a live web page -- "
                "logging into a portal, filling a form, clicking through a "
                "dashboard -- rather than just looking up a fact. "
                "Call 'get_state' after navigating to see the numbered elements "
                "you can click or type into. Every call must pass the same "
                "task_id to keep using the same browser session; call "
                "'close' when done with the task."
            ),
            parameters={
                "task_id": {
                    "type": "string",
                    "description": "Stable id for this task's browser session (reuse across calls).",
                },
                "action": {
                    "type": "string",
                    "enum": [
                        "go_to_url", "get_state", "click_element", "input_text",
                        "scroll_down", "scroll_up", "scroll_to_text", "send_keys",
                        "go_back", "wait", "extract_content",
                        "switch_tab", "open_tab", "close_tab", "close",
                    ],
                    "description": "The browser action to perform.",
                },
                "url": {"type": "string", "description": "URL for 'go_to_url' / 'open_tab'."},
                "index": {"type": "integer", "description": "Numbered element index (from 'get_state') for 'click_element' / 'input_text'."},
                "text": {"type": "string", "description": "Text for 'input_text' or 'scroll_to_text'."},
                "scroll_amount": {"type": "integer", "description": "Pixels to scroll for 'scroll_down' / 'scroll_up'."},
                "tab_id": {"type": "integer", "description": "Tab id for 'switch_tab'."},
                "goal": {"type": "string", "description": "What to extract, for 'extract_content'."},
                "keys": {"type": "string", "description": "Key(s) to send for 'send_keys', e.g. 'Enter' or 'Control+a'."},
                "seconds": {"type": "integer", "description": "Seconds to wait for 'wait'."},
            },
            required=["task_id", "action"],
            irreversible=True,  # gate this behind RBAC/verifier approval -- it can submit forms, click buy buttons, etc.
        )

    async def _get_context(self, task_id: str) -> BrowserContext:
        if BrowserUseBrowser is None:
            raise RuntimeError(
                "The legacy browser_control tool is incompatible with the installed browser-use API. "
                "Use the supported Agent Computer tools instead."
            )
        async with self._lock:
            if task_id not in self._sessions:
                browser = BrowserUseBrowser(headless=True)
                context = await browser.new_context()
                self._sessions[task_id] = (browser, context)
                logger.info("browser_control.session_started", task_id=task_id)
            return self._sessions[task_id][1]

    async def _close_context(self, task_id: str) -> None:
        async with self._lock:
            pair = self._sessions.pop(task_id, None)
        if pair:
            browser, context = pair
            await context.close()
            await browser.close()
            logger.info("browser_control.session_closed", task_id=task_id)

    async def execute(self, **kwargs) -> dict:
        task_id = kwargs.get("task_id")
        action = kwargs.get("action")
        if not task_id:
            return {"output": "Error: task_id is required so the session can be reused across calls."}
        if not action:
            return {"output": "Error: action is required."}

        if action == "close":
            await self._close_context(task_id)
            return {"output": f"Browser session for task '{task_id}' closed."}

        try:
            context = await self._get_context(task_id)

            if action == "go_to_url":
                url = kwargs.get("url")
                if not url:
                    return {"output": "Error: 'url' is required for go_to_url."}
                page = await context.get_current_page()
                await page.goto(url)
                await page.wait_for_load_state()
                return {"output": f"Navigated to {url}"}

            elif action == "get_state":
                state = await context.get_state()
                return {"output": state.element_tree.clickable_elements_to_string()}

            elif action == "click_element":
                index = kwargs.get("index")
                if index is None:
                    return {"output": "Error: 'index' is required for click_element."}
                element = await context.get_dom_element_by_index(index)
                if element is None:
                    return {"output": f"Error: no element with index {index}. Call get_state first."}
                await context._click_element_node(element)
                return {"output": f"Clicked element {index}"}

            elif action == "input_text":
                index, text = kwargs.get("index"), kwargs.get("text")
                if index is None or text is None:
                    return {"output": "Error: 'index' and 'text' are required for input_text."}
                element = await context.get_dom_element_by_index(index)
                if element is None:
                    return {"output": f"Error: no element with index {index}. Call get_state first."}
                await context._input_text_element_node(element, text)
                return {"output": f"Typed '{text}' into element {index}"}

            elif action in ("scroll_down", "scroll_up"):
                amount = kwargs.get("scroll_amount", 500)
                direction = 1 if action == "scroll_down" else -1
                page = await context.get_current_page()
                await page.evaluate(f"window.scrollBy(0, {direction * amount});")
                return {"output": f"Scrolled {'down' if direction > 0 else 'up'} {amount}px"}

            elif action == "scroll_to_text":
                text = kwargs.get("text")
                page = await context.get_current_page()
                try:
                    locator = page.get_by_text(text, exact=False)
                    await locator.scroll_into_view_if_needed()
                    return {"output": f"Scrolled to text: '{text}'"}
                except Exception as e:
                    return {"output": f"Could not find text '{text}': {e}"}

            elif action == "send_keys":
                keys = kwargs.get("keys")
                page = await context.get_current_page()
                await page.keyboard.press(keys)
                return {"output": f"Sent keys: {keys}"}

            elif action == "go_back":
                await context.go_back()
                return {"output": "Navigated back"}

            elif action == "wait":
                seconds = kwargs.get("seconds", 3)
                await asyncio.sleep(seconds)
                return {"output": f"Waited {seconds}s"}

            elif action == "extract_content":
                # Cheap version: return visible text truncated. Swap in an LLM
                # summarization call here (via your model_router) if you want
                # goal-directed extraction instead of raw text dump.
                page = await context.get_current_page()
                text = await page.evaluate("() => document.body.innerText")
                if len(text) > MAX_EXTRACT_CONTENT_LENGTH:
                    text = text[:MAX_EXTRACT_CONTENT_LENGTH] + "...[truncated]"
                return {"output": text}

            elif action == "switch_tab":
                tab_id = kwargs.get("tab_id")
                await context.switch_to_tab(tab_id)
                return {"output": f"Switched to tab {tab_id}"}

            elif action == "open_tab":
                url = kwargs.get("url")
                await context.create_new_tab(url)
                return {"output": f"Opened new tab: {url}"}

            elif action == "close_tab":
                await context.close_current_tab()
                return {"output": "Closed current tab"}

            else:
                return {"output": f"Unknown action: {action}"}

        except Exception as e:
            logger.exception("browser_control.execute_failed", action=action, task_id=task_id)
            return {"output": f"Browser action '{action}' failed: {e}"}