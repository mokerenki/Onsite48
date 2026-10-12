"""Task-scoped, isolated browser contexts for Onsite48 agent web work."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import ipaddress
import re
from dataclasses import dataclass
from typing import Any, Literal
from urllib.parse import urlparse

from playwright.async_api import BrowserContext, Page, Route, TimeoutError as PlaywrightTimeoutError


class UnsafeBrowserTarget(ValueError):
    """Raised when a URL could reach local or private infrastructure."""


class AgentComputerUserTakeoverActive(RuntimeError):
    """Raised when an agent tool tries to act while the user controls the task browser."""


def validate_public_url(url: str) -> str:
    parsed = urlparse(url.strip())
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise UnsafeBrowserTarget("Only complete http:// or https:// public URLs are allowed.")
    hostname = parsed.hostname.lower().rstrip(".")
    if hostname == "localhost" or hostname.endswith(".local") or hostname.endswith(".internal"):
        raise UnsafeBrowserTarget("Local and internal network addresses are not available to the agent computer.")
    try:
        address = ipaddress.ip_address(hostname)
        if address.is_private or address.is_loopback or address.is_link_local or address.is_reserved or address.is_unspecified:
            raise UnsafeBrowserTarget("Private network addresses are not available to the agent computer.")
    except UnsafeBrowserTarget:
        raise
    except ValueError:
        # A DNS hostname is acceptable here; every eventual HTTP request is
        # still checked by the browser route guard before it proceeds.
        pass
    return parsed.geturl()


@dataclass
class ComputerSession:
    context: BrowserContext
    page: Page
    lock: asyncio.Lock


class AgentComputerManager:
    """Own ephemeral, cookie-isolated browser contexts keyed by task thread.

    ``_user_controlled_threads`` is intentionally process-local. A browser
    context itself cannot survive a process restart, so persisting a user-control
    flag separately would create a misleading claim that an interactive session
    remains available after restart. Durable task metadata records the handoff;
    this lock prevents concurrent agent actions while that live context exists.
    """

    # Detection is deliberately conservative and only creates a user handoff.
    # It never solves, submits, or otherwise attempts to bypass a challenge.
    _CHALLENGE_MARKERS = {
        "captcha": "The target site requires a CAPTCHA or human verification.",
        "recaptcha": "The target site requires a CAPTCHA or human verification.",
        "hcaptcha": "The target site requires a CAPTCHA or human verification.",
        "turnstile": "The target site requires a human verification challenge.",
        "verify you are human": "The target site requires human verification.",
        "verify that you are human": "The target site requires human verification.",
        "checking your browser": "The target site is checking the browser before allowing access.",
        "attention required": "The target site requires human verification before allowing access.",
        "access denied": "The target site denied automated access and may require a human verification step.",
        "unusual traffic": "The target site detected unusual traffic and requires human verification.",
    }
    _SENSITIVE_FIELD_MARKERS = re.compile(
        r"password|passcode|otp|one.?time|verification|captcha|turnstile|hcaptcha|recaptcha|"
        r"cvv|card.?number|payment|security.?code",
        re.IGNORECASE,
    )
    _ALLOWED_USER_KEYS = {
        "Enter", "Tab", "Escape", "Backspace", "Delete", "Space",
        "ArrowUp", "ArrowDown", "ArrowLeft", "ArrowRight",
        "PageUp", "PageDown", "Home", "End",
    }
    _KEY_COMBINATION_PATTERN = re.compile(
        r"^(?:(?:Control|Alt|Shift|Meta)\+)*(?:[A-Za-z0-9]|Enter|Tab|Escape|Backspace|Delete|Space|"
        r"ArrowUp|ArrowDown|ArrowLeft|ArrowRight|PageUp|PageDown|Home|End)$"
    )

    def __init__(self, browser_service: Any, visual_grounding: Any | None = None):
        self.browser_service = browser_service
        self.visual_grounding = visual_grounding
        self._sessions: dict[str, ComputerSession] = {}
        self._user_controlled_threads: set[str] = set()
        self._lock = asyncio.Lock()

    @staticmethod
    async def _block_unsafe_route(route: Route) -> None:
        """Prevent navigations and subresource fetches to local/private HTTP targets."""
        target = route.request.url
        parsed = urlparse(target)
        if parsed.scheme in {"data", "blob", "about"}:
            await route.continue_()
            return
        try:
            validate_public_url(target)
        except UnsafeBrowserTarget:
            await route.abort()
            return
        await route.continue_()

    async def _session(self, thread_id: str, *, create: bool = False) -> ComputerSession:
        async with self._lock:
            existing = self._sessions.get(thread_id)
            # A page can be closed by a browser crash, a navigation failure, or
            # an external cleanup path. Recreate only when a new browser action
            # explicitly requests it; old controls must never revive a closed task.
            if existing and not existing.page.is_closed():
                return existing
            if existing:
                self._sessions.pop(thread_id, None)
                self._user_controlled_threads.discard(thread_id)
                try:
                    await existing.context.close()
                except Exception:
                    pass
            if not create:
                raise RuntimeError("The Agent Computer session is inactive for this task. Start a new browser task to create a fresh isolated session.")
            browser = self.browser_service.browser
            if browser is None:
                raise RuntimeError("The agent computer is not available. Browser automation has not started.")
            context = await browser.new_context(viewport={"width": 1440, "height": 960}, accept_downloads=False)
            await context.route("**/*", self._block_unsafe_route)
            page = await context.new_page()
            session = ComputerSession(context=context, page=page, lock=asyncio.Lock())
            self._sessions[thread_id] = session
            return session

    async def _assert_agent_control(self, thread_id: str) -> None:
        async with self._lock:
            if thread_id in self._user_controlled_threads:
                raise AgentComputerUserTakeoverActive(
                    "The user is controlling this Agent Computer. Wait until they return control before continuing browser actions."
                )

    async def control_state(self, thread_id: str) -> dict[str, Any]:
        async with self._lock:
            session = self._sessions.get(thread_id)
            active = bool(session and not session.page.is_closed())
            mode = "user" if thread_id in self._user_controlled_threads and active else "agent"
        return {"mode": mode, "session_active": active}

    async def begin_user_takeover(self, thread_id: str) -> dict[str, Any]:
        session = await self._session(thread_id)
        async with self._lock:
            self._user_controlled_threads.add(thread_id)
        async with session.lock:
            return {"control": await self.control_state(thread_id), **await self._snapshot_locked(session)}

    async def return_to_agent(self, thread_id: str) -> dict[str, Any]:
        session = await self._session(thread_id)
        async with self._lock:
            self._user_controlled_threads.discard(thread_id)
        async with session.lock:
            return {"control": await self.control_state(thread_id), **await self._snapshot_locked(session)}

    @staticmethod
    async def _position(page: Page) -> dict[str, int | bool]:
        position = await page.evaluate(
            """() => ({
                scroll_y: Math.max(0, Math.round(window.scrollY)),
                scroll_height: Math.max(document.documentElement.scrollHeight, document.body ? document.body.scrollHeight : 0),
                viewport_height: Math.round(window.innerHeight),
                viewport_width: Math.round(window.innerWidth),
            })"""
        )
        position["can_scroll"] = bool(position["scroll_height"] > position["viewport_height"] + 2)
        return position

    @staticmethod
    async def _dom_element_count(page: Page) -> int:
        return int(await page.evaluate("() => document.querySelectorAll('*').length"))

    async def _challenge_state(self, page: Page) -> dict[str, str] | None:
        """Return a safe user-handoff signal when a page presents an anti-bot challenge."""
        try:
            title = (await page.title()).lower()
            url = page.url.lower()
            body = (await page.locator("body").inner_text(timeout=2_000)).lower()[:12_000]
            frame_sources = await page.locator("iframe").evaluate_all("nodes => nodes.map(node => node.src || '').join(' ')")
            evidence = " ".join((title, url, body, str(frame_sources).lower()))
        except Exception:
            return None
        for marker, message in self._CHALLENGE_MARKERS.items():
            if marker in evidence:
                return {"code": "human_verification_required", "reason": message, "handoff_surface": "agent_computer"}
        return None

    async def _page_state(self, session: ComputerSession, *, response: Any = None, warning: str | None = None) -> dict[str, Any]:
        state = {
            "url": session.page.url,
            "title": await session.page.title(),
            "status": response.status if response else None,
            "dom_element_count": await self._dom_element_count(session.page),
            **await self._position(session.page),
        }
        challenge = await self._challenge_state(session.page)
        if challenge:
            state["user_intervention"] = challenge
        if warning:
            state["warning"] = warning
        return state

    async def _post_action_state(self, session: ComputerSession, before: dict[str, Any], before_screenshot: bytes) -> dict[str, Any]:
        """Return deterministic evidence without a second model-verification call."""
        await session.page.wait_for_timeout(250)
        after = await self._page_state(session)
        screenshot = await session.page.screenshot(type="png")
        screenshot_changed = hashlib.sha256(before_screenshot).digest() != hashlib.sha256(screenshot).digest()
        changed = any((
            before.get("url") != after.get("url"),
            before.get("scroll_y") != after.get("scroll_y"),
            before.get("dom_element_count") != after.get("dom_element_count"),
            screenshot_changed,
        ))
        return {
            **after,
            "screenshot_base64": base64.b64encode(screenshot).decode("ascii"),
            "post_action": {
                "url_changed": before.get("url") != after.get("url"),
                "scroll_changed": before.get("scroll_y") != after.get("scroll_y"),
                "dom_element_count_changed": before.get("dom_element_count") != after.get("dom_element_count"),
                "screenshot_changed": screenshot_changed,
                "visible_change_detected": changed,
                "before": {key: before.get(key) for key in ("url", "scroll_y", "dom_element_count")},
                "after": {key: after.get(key) for key in ("url", "scroll_y", "dom_element_count")},
            },
        }

    async def navigate(self, thread_id: str, url: str) -> dict[str, Any]:
        await self._assert_agent_control(thread_id)
        target = validate_public_url(url)
        session = await self._session(thread_id, create=True)
        async with session.lock:
            return await self._navigate_locked(session, target)

    async def user_navigate(self, thread_id: str, url: str) -> dict[str, Any]:
        target = validate_public_url(url)
        session = await self._session(thread_id)
        async with session.lock:
            return await self._navigate_locked(session, target)

    async def _navigate_locked(self, session: ComputerSession, target: str) -> dict[str, Any]:
        try:
            response = await session.page.goto(target, wait_until="domcontentloaded", timeout=30_000)
            await session.page.wait_for_timeout(250)
            return await self._page_state(session, response=response)
        except PlaywrightTimeoutError:
            return await self._page_state(session, warning="The page kept loading after 30 seconds; visible content is still available to inspect.")

    async def reload(self, thread_id: str) -> dict[str, Any]:
        await self._assert_agent_control(thread_id)
        session = await self._session(thread_id)
        async with session.lock:
            return await self._reload_locked(session)

    async def user_reload(self, thread_id: str) -> dict[str, Any]:
        session = await self._session(thread_id)
        async with session.lock:
            return await self._reload_locked(session)

    async def _reload_locked(self, session: ComputerSession) -> dict[str, Any]:
        try:
            response = await session.page.reload(wait_until="domcontentloaded", timeout=30_000)
            await session.page.wait_for_timeout(250)
            return await self._page_state(session, response=response)
        except PlaywrightTimeoutError:
            return await self._page_state(session, warning="The page kept loading after 30 seconds; the current visible content was retained.")

    async def go_back(self, thread_id: str) -> dict[str, Any]:
        await self._assert_agent_control(thread_id)
        session = await self._session(thread_id)
        async with session.lock:
            return await self._back_locked(session)

    async def user_go_back(self, thread_id: str) -> dict[str, Any]:
        session = await self._session(thread_id)
        async with session.lock:
            return await self._back_locked(session)

    async def _back_locked(self, session: ComputerSession) -> dict[str, Any]:
        response = await session.page.go_back(wait_until="domcontentloaded", timeout=30_000)
        await session.page.wait_for_timeout(250)
        state = await self._page_state(session, response=response)
        state["history_available"] = response is not None
        return state

    async def scroll(self, thread_id: str, direction: Literal["up", "down"], amount: int | None = None) -> dict[str, Any]:
        await self._assert_agent_control(thread_id)
        return await self._scroll(thread_id, direction, amount)

    async def user_scroll(self, thread_id: str, direction: Literal["up", "down"], amount: int | None = None) -> dict[str, Any]:
        return await self._scroll(thread_id, direction, amount)

    async def _scroll(self, thread_id: str, direction: Literal["up", "down"], amount: int | None = None) -> dict[str, Any]:
        if direction not in {"up", "down"}:
            raise ValueError("Scroll direction must be 'up' or 'down'.")
        if amount is not None and not 50 <= amount <= 10_000:
            raise ValueError("Scroll amount must be between 50 and 10000 pixels.")
        session = await self._session(thread_id)
        async with session.lock:
            delta = amount or int(await session.page.evaluate("() => Math.max(480, Math.floor(window.innerHeight * 0.8))"))
            if direction == "up":
                delta = -delta
            before = await self._position(session.page)
            await session.page.evaluate("distance => window.scrollBy({ top: distance, left: 0, behavior: 'instant' })", delta)
            await session.page.wait_for_timeout(120)
            after = await self._position(session.page)
            state = await self._page_state(session)
            return {"direction": direction, "moved": before["scroll_y"] != after["scroll_y"], **state}

    # ── Hidden-content / prompt-injection sanitization for read_page ─────
    #
    # `read_page` is the tool that puts arbitrary third-party page content
    # directly in front of the planner/synthesis model. Playwright's native
    # `innerText` already excludes `display:none` and `visibility:hidden`
    # subtrees (that's real, tested browser rendering behavior, not
    # something this file has to reimplement) -- but it does NOT exclude
    # text that is visually suppressed by other means commonly used for
    # prompt-injection payloads: `opacity:0`, `font-size:0`, elements
    # shrunk to ~0x0 with clipped overflow, `aria-hidden="true"` subtrees,
    # or zero-width Unicode characters spliced into otherwise-visible text.
    # A page can carry a paragraph a sighted human would never notice while
    # it's still fully present in `body.innerText`, which is exactly the
    # gap a hostile page would use to smuggle instructions to the model.
    #
    # This does not mutate the live page (no style/DOM writes -- the page
    # may still be interacted with afterward via click/fill), and it is
    # deliberately conservative about what it strips: only text belonging
    # to a small, well-defined set of "this was made invisible on purpose"
    # patterns is removed, and only when it is at least a few characters
    # long, to avoid false-positive removal of ordinary short strings that
    # might coincidentally recur elsewhere in the visible text.
    _ZERO_WIDTH_PATTERN = re.compile(
        r"[\u200B-\u200F\u202A-\u202E\u2060-\u2064\uFEFF\u00AD]"
    )
    _MIN_STRIPPED_SNIPPET_CHARS = 6

    _RISKY_TEXT_JS = """() => {
        const isRisky = (node) => {
            const style = window.getComputedStyle(node);
            if (style.display === 'none' || style.visibility === 'hidden') return false; // already excluded from innerText natively
            if (parseFloat(style.opacity) === 0) return true;
            const fontSize = parseFloat(style.fontSize);
            if (!Number.isNaN(fontSize) && fontSize <= 0.5) return true;
            const rect = node.getBoundingClientRect();
            if ((rect.width <= 1 || rect.height <= 1) && style.overflow !== 'visible') return true;
            if (node.getAttribute && node.getAttribute('aria-hidden') === 'true') return true;
            return false;
        };
        const seen = new Set();
        const out = [];
        document.body.querySelectorAll('*').forEach((node) => {
            if (!isRisky(node)) return;
            const text = (node.innerText || node.textContent || '').trim();
            if (text.length < 3 || seen.has(text)) return;
            seen.add(text);
            out.push(text);
        });
        return out.slice(0, 200);
    }"""

    @classmethod
    def _strip_zero_width(cls, text: str) -> str:
        return cls._ZERO_WIDTH_PATTERN.sub("", text)

    async def _sanitize_page_text(self, page: Page, text: str) -> dict[str, Any]:
        """Strip zero-width characters and known visually-hidden injection
        patterns from extracted page text. Returns the cleaned text plus
        auditable metadata about what, if anything, was removed -- callers
        should surface this rather than silently swallow it, since a page
        that trips this is worth the executor/verifier knowing about.
        """
        cleaned = self._strip_zero_width(text)
        zero_width_removed = len(text) - len(cleaned)
        removed_snippets: list[str] = []
        try:
            risky_snippets = await page.evaluate(self._RISKY_TEXT_JS)
        except Exception:
            risky_snippets = []
        for snippet in risky_snippets or []:
            snippet = str(snippet).strip()
            if len(snippet) < self._MIN_STRIPPED_SNIPPET_CHARS:
                continue
            if snippet in cleaned:
                cleaned = cleaned.replace(snippet, "")
                removed_snippets.append(snippet[:120])
        return {
            "text": cleaned,
            "hidden_content_detected": bool(zero_width_removed or removed_snippets),
            "hidden_content_chars_removed": zero_width_removed + sum(len(s) for s in removed_snippets),
            "hidden_content_snippets_removed": len(removed_snippets),
        }

    async def read_page(
        self,
        thread_id: str,
        offset: int = 0,
        limit: int = 20_000,
        selector: str | None = None,
    ) -> dict[str, Any]:
        await self._assert_agent_control(thread_id)
        if offset < 0 or limit < 1 or limit > 30_000:
            raise ValueError("Read offset must be non-negative and limit must be between 1 and 30000.")
        if selector is not None and (not isinstance(selector, str) or len(selector) > 500):
            raise ValueError("The requested read selector is invalid.")
        session = await self._session(thread_id)
        async with session.lock:
            target = session.page.locator("body")
            selector_matched = True
            if selector:
                candidate = session.page.locator(selector).first
                try:
                    if await candidate.count() > 0:
                        target = candidate
                    else:
                        selector_matched = False
                except Exception:
                    selector_matched = False
            raw_text = await target.inner_text(timeout=10_000)
            sanitized = await self._sanitize_page_text(session.page, raw_text)
            text = sanitized["text"]
            total = len(text)
            end = min(total, offset + limit)
            state = await self._page_state(session)
            result = {
                "text": text[offset:end],
                "offset": offset,
                "end_offset": end,
                "total_characters": total,
                "has_more": end < total,
                "hidden_content_detected": sanitized["hidden_content_detected"],
                "hidden_content_chars_removed": sanitized["hidden_content_chars_removed"],
                **state,
            }
            if selector:
                result["selector"] = selector
                result["selector_matched"] = selector_matched
                if not selector_matched:
                    result["warning"] = (
                        f"Selector '{selector}' did not match any element; "
                        "returned the full page body instead."
                    )
            return result

    async def inspect(self, thread_id: str) -> dict[str, Any]:
        await self._assert_agent_control(thread_id)
        session = await self._session(thread_id)
        async with session.lock:
            elements = await session.page.locator("a, button, input, textarea, select").evaluate_all("""nodes => nodes.slice(0, 100).map((node, index) => ({index, tag: node.tagName, text: (node.innerText || node.getAttribute('aria-label') || node.placeholder || '').trim().slice(0, 160), type: node.type || '', selector: node.id ? '#' + CSS.escape(node.id) : node.getAttribute('data-testid') ? '[data-testid="' + node.getAttribute('data-testid') + '"]' : null}))""")
            return {"elements": elements, **await self._page_state(session)}

    async def click(self, thread_id: str, selector: str) -> dict[str, Any]:
        await self._assert_agent_control(thread_id)
        if not selector or len(selector) > 500:
            raise ValueError("A valid element selector is required.")
        session = await self._session(thread_id)
        async with session.lock:
            before = await self._page_state(session)
            before_screenshot = await session.page.screenshot(type="png")
            await session.page.locator(selector).first.click(timeout=15_000)
            state = await self._post_action_state(session, before, before_screenshot)
            return {"clicked": selector, **state}

    @staticmethod
    def _coordinate(value: float, dimension: int, normalized: bool) -> float:
        if not isinstance(value, (int, float)):
            raise ValueError("Browser coordinates must be numeric.")
        coordinate = float(value)
        if normalized:
            if not 0.0 <= coordinate <= 1.0:
                raise ValueError("Normalized browser coordinates must be between 0.0 and 1.0.")
            return min(max(0.0, coordinate * dimension), max(0.0, dimension - 1.0))
        if not 0.0 <= coordinate < dimension:
            raise ValueError("Pixel browser coordinates must be inside the current viewport.")
        return coordinate

    async def _click_at(self, thread_id: str, x: float, y: float, normalized: bool, *, actor: str) -> dict[str, Any]:
        session = await self._session(thread_id)
        async with session.lock:
            before = await self._page_state(session)
            before_screenshot = await session.page.screenshot(type="png")
            pixel_x = self._coordinate(x, int(before["viewport_width"]), normalized)
            pixel_y = self._coordinate(y, int(before["viewport_height"]), normalized)
            await session.page.mouse.click(pixel_x, pixel_y)
            state = await self._post_action_state(session, before, before_screenshot)
            return {"clicked": True, "actor": actor, "coordinates": {"x": pixel_x, "y": pixel_y, "normalized": normalized}, **state}

    async def click_at(self, thread_id: str, x: float, y: float, normalized: bool = True) -> dict[str, Any]:
        await self._assert_agent_control(thread_id)
        return await self._click_at(thread_id, x, y, normalized, actor="agent")

    async def user_click_at(self, thread_id: str, x: float, y: float, normalized: bool = True) -> dict[str, Any]:
        if (await self.control_state(thread_id))["mode"] != "user":
            raise AgentComputerUserTakeoverActive("Start user takeover before interacting with the Agent Computer.")
        return await self._click_at(thread_id, x, y, normalized, actor="user")

    async def type_at(self, thread_id: str, x: float, y: float, text: str, normalized: bool = True) -> dict[str, Any]:
        await self._assert_agent_control(thread_id)
        if not isinstance(text, str) or not text or len(text) > 10_000:
            raise ValueError("The requested browser input is invalid.")
        session = await self._session(thread_id)
        async with session.lock:
            before = await self._page_state(session)
            before_screenshot = await session.page.screenshot(type="png")
            pixel_x = self._coordinate(x, int(before["viewport_width"]), normalized)
            pixel_y = self._coordinate(y, int(before["viewport_height"]), normalized)
            await session.page.mouse.click(pixel_x, pixel_y)
            await session.page.keyboard.type(text)
            state = await self._post_action_state(session, before, before_screenshot)
            return {"typed": True, "text_length": len(text), "coordinates": {"x": pixel_x, "y": pixel_y, "normalized": normalized}, **state}

    @staticmethod
    async def _focused_field_classification(page: Page) -> str:
        descriptor = await page.evaluate("""() => {
            const node = document.activeElement;
            if (!node) return 'unknown';
            const values = [node.tagName, node.type, node.name, node.id, node.autocomplete, node.getAttribute?.('aria-label'), node.placeholder].filter(Boolean).join(' ');
            return values.slice(0, 500);
        }""")
        if AgentComputerManager._SENSITIVE_FIELD_MARKERS.search(str(descriptor)):
            return "sensitive"
        lowered = str(descriptor).lower()
        if "textarea" in lowered or "contenteditable" in lowered:
            return "long_text"
        if "search" in lowered:
            return "search"
        if "email" in lowered:
            return "email"
        return "ordinary_text"

    async def user_type_focused(self, thread_id: str, text: str) -> dict[str, Any]:
        """Type user-provided input into the already focused browser field.

        The returned payload intentionally contains no copy of ``text``. Callers
        must not append it to task events, error messages, or audit metadata.
        """
        if (await self.control_state(thread_id))["mode"] != "user":
            raise AgentComputerUserTakeoverActive("Start user takeover before interacting with the Agent Computer.")
        if not isinstance(text, str) or not text or len(text) > 512:
            raise ValueError("The requested user input is invalid.")
        session = await self._session(thread_id)
        async with session.lock:
            before = await self._page_state(session)
            before_screenshot = await session.page.screenshot(type="png")
            field_classification = await self._focused_field_classification(session.page)
            await session.page.keyboard.insert_text(text)
            state = await self._post_action_state(session, before, before_screenshot)
            return {"typed": True, "actor": "user", "text_length": len(text), "field_classification": field_classification, **state}

    @classmethod
    def _validate_user_key(cls, key: str) -> str:
        value = key.strip()
        if value in cls._ALLOWED_USER_KEYS or cls._KEY_COMBINATION_PATTERN.fullmatch(value):
            return value
        raise ValueError("That browser key is not supported by the secure takeover control.")

    async def user_press_key(self, thread_id: str, key: str) -> dict[str, Any]:
        if (await self.control_state(thread_id))["mode"] != "user":
            raise AgentComputerUserTakeoverActive("Start user takeover before interacting with the Agent Computer.")
        value = self._validate_user_key(key)
        session = await self._session(thread_id)
        async with session.lock:
            before = await self._page_state(session)
            before_screenshot = await session.page.screenshot(type="png")
            field_classification = await self._focused_field_classification(session.page)
            await session.page.keyboard.press(value)
            state = await self._post_action_state(session, before, before_screenshot)
            return {"key_pressed": value, "actor": "user", "field_classification": field_classification, **state}

    async def press_key(self, thread_id: str, key: str) -> dict[str, Any]:
        """Press a bounded keyboard key in agent mode, returning before/after evidence."""
        await self._assert_agent_control(thread_id)
        value = self._validate_user_key(key)
        session = await self._session(thread_id)
        async with session.lock:
            before = await self._page_state(session)
            before_screenshot = await session.page.screenshot(type="png")
            field_classification = await self._focused_field_classification(session.page)
            await session.page.keyboard.press(value)
            state = await self._post_action_state(session, before, before_screenshot)
            return {"key_pressed": value, "actor": "agent", "field_classification": field_classification, **state}

    async def locate_element(self, thread_id: str, description: str) -> dict[str, Any]:
        await self._assert_agent_control(thread_id)
        if not self.visual_grounding:
            raise RuntimeError("Vision grounding is not configured for the agent computer.")
        session = await self._session(thread_id)
        async with session.lock:
            screenshot = await session.page.screenshot(type="png")
            state = await self._page_state(session)
            dom_elements = await session.page.locator("a, button, input, textarea, select, [role]").evaluate_all("""nodes => nodes.slice(0, 150).map((node, index) => { const r = node.getBoundingClientRect(); const label = (node.innerText || node.getAttribute('aria-label') || node.getAttribute('placeholder') || '').trim().slice(0, 160); const selector = node.id ? '#' + CSS.escape(node.id) : node.getAttribute('data-testid') ? '[data-testid=\\"' + CSS.escape(node.getAttribute('data-testid')) + '\\"]' : null; return { dom_ref: 'dom-' + index, tag: node.tagName, text: label, selector, bbox: { x: r.x / innerWidth, y: r.y / innerHeight, width: r.width / innerWidth, height: r.height / innerHeight }, visible: r.width > 0 && r.height > 0 }; }).filter(item => item.visible)""")
        grounding = await self.visual_grounding.locate(
            screenshot_base64=base64.b64encode(screenshot).decode("ascii"),
            description=description,
            dom_elements=dom_elements,
        )
        return {"description": description, **grounding, **state}

    async def fill(self, thread_id: str, selector: str, value: str) -> dict[str, Any]:
        await self._assert_agent_control(thread_id)
        if not selector or len(selector) > 500 or len(value) > 10_000:
            raise ValueError("The requested browser input is invalid.")
        session = await self._session(thread_id)
        async with session.lock:
            before = await self._page_state(session)
            before_screenshot = await session.page.screenshot(type="png")
            await session.page.locator(selector).first.fill(value, timeout=15_000)
            state = await self._post_action_state(session, before, before_screenshot)
            return {"filled": selector, **state}

    async def _snapshot_locked(self, session: ComputerSession) -> dict[str, Any]:
        screenshot = await session.page.screenshot(type="png")
        position = await self._position(session.page)
        return {
            "url": session.page.url,
            "title": await session.page.title(),
            "screenshot_base64": base64.b64encode(screenshot).decode("ascii"),
            **position,
        }

    async def snapshot(self, thread_id: str) -> dict[str, Any]:
        session = await self._session(thread_id)
        async with session.lock:
            return await self._snapshot_locked(session)

    async def close(self, thread_id: str) -> None:
        async with self._lock:
            session = self._sessions.pop(thread_id, None)
            self._user_controlled_threads.discard(thread_id)
        if session:
            await session.context.close()