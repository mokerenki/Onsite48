"""Sandbox browser operator implementing protocol v4 on top of Playwright.

Uses AgentComputerManager sessions when available; can also operate a bare Page
for unit tests. Every action returns an ActionReceipt with post-action observation.
Stale observation detection: revision mismatch or fingerprint mismatch →
stale_observation=True + fresh observation, never act on a guess.
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import re
from typing import Any, Optional

import structlog

from app.runtime.browser.protocol import (
    ActionName,
    ActionReceipt,
    ActionRequest,
    ActionTarget,
    BBox,
    Element,
    ElementState,
    FrameInfo,
    Observation,
    fingerprint_element,
    new_observation_id,
)

logger = structlog.get_logger("onsite48.runtime.browser.operator")

# Accessibility roles we surface (subset of ARIA + common form controls).
_ROLE_MAP = {
    "a": "link",
    "button": "button",
    "input": "textbox",
    "textarea": "textbox",
    "select": "combobox",
    "option": "option",
    "img": "img",
    "h1": "heading",
    "h2": "heading",
    "h3": "heading",
    "h4": "heading",
    "h5": "heading",
    "h6": "heading",
    "li": "listitem",
    "ul": "list",
    "ol": "list",
    "nav": "navigation",
    "form": "form",
    "label": "label",
    "checkbox": "checkbox",
    "radio": "radio",
}

_PASSWORD_RE = re.compile(r"password|passcode|otp|one.?time", re.I)
_PAYMENT_RE = re.compile(r"card.?number|cvv|cvc|payment|billing|credit.?card", re.I)
_LOGIN_RE = re.compile(r"sign.?in|log.?in|authenticate|password", re.I)
_DESTRUCTIVE_RE = re.compile(
    r"\b(delete|remove|destroy|revoke|purge|unsubscribe|cancel\s+account|permanently)\b", re.I
)
_CAPTCHA_MARKERS = (
    "captcha", "recaptcha", "hcaptcha", "turnstile",
    "verify you are human", "checking your browser", "attention required",
)


class BrowserOperator:
    """Protocol-v4 operator bound to a single page / session."""

    def __init__(self, page: Any, *, thread_id: str = "test", max_elements: int = 120):
        self.page = page
        self.thread_id = thread_id
        self.max_elements = max_elements
        self._revision = 0
        self._last_obs: Optional[Observation] = None
        self._screenshots: dict[str, bytes] = {}

    # ------------------------------------------------------------------ observe

    async def observe(self, *, include_screenshot: bool = False, set_of_marks: bool = False) -> Observation:
        page = self.page
        url = page.url or ""
        title = ""
        try:
            title = await page.title()
        except Exception:
            title = ""

        frames_info: list[FrameInfo] = []
        elements: list[Element] = []
        ref_counter = 0

        try:
            all_frames = page.frames
        except Exception:
            all_frames = [page.main_frame] if hasattr(page, "main_frame") else []

        for fi, frame in enumerate(all_frames):
            frame_id = f"f{fi}"
            try:
                furl = frame.url or ""
                fname = frame.name or ("main" if fi == 0 else f"frame_{fi}")
            except Exception:
                furl, fname = "", f"frame_{fi}"
            parent_id = None
            if fi > 0:
                parent_id = "f0"
            frames_info.append(FrameInfo(frame_id=frame_id, url=furl, name=fname, parent_frame_id=parent_id))

            try:
                raw = await frame.evaluate(
                    """(maxN) => {
                    const out = [];
                    const nodes = document.querySelectorAll(
                      'a, button, input, textarea, select, [role], [contenteditable="true"]'
                    );
                    const pathOf = (el) => {
                      const parts = [];
                      let n = el;
                      while (n && n.nodeType === 1 && parts.length < 6) {
                        let s = n.tagName.toLowerCase();
                        if (n.id) { s += '#' + n.id; parts.unshift(s); break; }
                        const parent = n.parentElement;
                        if (parent) {
                          const sibs = Array.from(parent.children).filter(c => c.tagName === n.tagName);
                          if (sibs.length > 1) s += ':nth-of-type(' + (sibs.indexOf(n) + 1) + ')';
                        }
                        parts.unshift(s);
                        n = parent;
                      }
                      return parts.join('>');
                    };
                    for (const node of nodes) {
                      if (out.length >= maxN) break;
                      const r = node.getBoundingClientRect();
                      if (r.width < 1 && r.height < 1) continue;
                      const style = window.getComputedStyle(node);
                      if (style.visibility === 'hidden' || style.display === 'none') continue;
                      const tag = node.tagName.toLowerCase();
                      const role = node.getAttribute('role')
                        || (tag === 'input' ? (node.type || 'textbox') : tag);
                      const name = (
                        node.getAttribute('aria-label')
                        || node.innerText
                        || node.getAttribute('placeholder')
                        || node.getAttribute('name')
                        || node.getAttribute('title')
                        || node.value
                        || ''
                      ).trim().slice(0, 160);
                      out.push({
                        tag, role, name,
                        type: node.type || '',
                        disabled: !!node.disabled,
                        checked: !!node.checked,
                        readonly: !!node.readOnly,
                        required: !!node.required,
                        value: (node.value || '').slice(0, 80),
                        password: (node.type || '') === 'password',
                        file_input: (node.type || '') === 'file',
                        bbox: {x: r.x, y: r.y, w: r.width, h: r.height},
                        path: pathOf(node),
                      });
                    }
                    return out;
                }""",
                    self.max_elements,
                )
            except Exception as exc:
                logger.debug("frame_eval_failed", frame_id=frame_id, error=str(exc))
                raw = []

            for item in raw or []:
                ref_counter += 1
                ref = f"e{ref_counter}"
                tag = item.get("tag") or ""
                role = item.get("role") or _ROLE_MAP.get(tag, tag)
                name = item.get("name") or ""
                path = item.get("path") or ""
                fp = fingerprint_element(role, name, tag, path, frame_id)
                bbox = item.get("bbox") or {}
                state = ElementState(
                    disabled=bool(item.get("disabled")),
                    checked=bool(item.get("checked")),
                    readonly=bool(item.get("readonly")),
                    required=bool(item.get("required")),
                    value=str(item.get("value") or ""),
                    password=bool(item.get("password")),
                    file_input=bool(item.get("file_input")),
                )
                elements.append(
                    Element(
                        ref=ref,
                        role=str(role),
                        name=str(name),
                        state=state,
                        bbox=BBox(
                            x=float(bbox.get("x") or 0),
                            y=float(bbox.get("y") or 0),
                            w=float(bbox.get("w") or 0),
                            h=float(bbox.get("h") or 0),
                        ),
                        frame_id=frame_id,
                        fingerprint=fp,
                        tag=tag,
                        type=str(item.get("type") or ""),
                    )
                )

        scroll = None
        try:
            scroll = await page.evaluate(
                """() => ({
                  x: window.scrollX || 0,
                  y: window.scrollY || 0,
                  max_x: Math.max(0, (document.documentElement.scrollWidth || 0) - window.innerWidth),
                  max_y: Math.max(0, (document.documentElement.scrollHeight || 0) - window.innerHeight),
                })"""
            )
        except Exception:
            scroll = None

        dialog = None
        page_text = ""
        try:
            page_text = (await page.inner_text("body")).lower()[:4000]
        except Exception:
            pass
        for marker in _CAPTCHA_MARKERS:
            if marker in page_text:
                dialog = {"type": "challenge", "message": f"Detected challenge marker: {marker}"}
                break

        screenshot_ref = None
        if include_screenshot:
            try:
                png = await page.screenshot(type="png", full_page=False)
                screenshot_ref = f"shot_{hashlib.sha256(png).hexdigest()[:12]}"
                self._screenshots[screenshot_ref] = png
            except Exception:
                screenshot_ref = None

        self._revision += 1
        obs = Observation(
            observation_id=new_observation_id(),
            revision=self._revision,
            url=url,
            title=title,
            frames=frames_info,
            elements=elements,
            screenshot_ref=screenshot_ref,
            set_of_marks=set_of_marks,
            dialog=dialog,
            scroll=scroll,
        )
        self._last_obs = obs
        return obs

    # ------------------------------------------------------------------ policy

    def classify_action_policy(self, req: ActionRequest, obs: Optional[Observation]) -> dict[str, str]:
        """Return {decision, reason} for browser actions. Conservative."""
        action = req.action
        if action == "observe":
            return {"decision": "ALLOW", "reason": "observe is read-only"}

        if obs and obs.dialog and obs.dialog.get("type") == "challenge":
            return {"decision": "TAKEOVER", "reason": "CAPTCHA / human challenge detected"}

        target_el = None
        if req.target and req.target.element_ref and obs:
            target_el = obs.element_by_ref(req.target.element_ref)

        if target_el:
            name = (target_el.name or "").lower()
            role = (target_el.role or "").lower()
            if target_el.state.password or target_el.type == "password":
                return {"decision": "TAKEOVER", "reason": "password field requires user takeover"}
            if target_el.state.file_input and action in ("click", "upload"):
                # upload of known workspace path is ALLOW; unknown origin download handled elsewhere
                pass
            if _PAYMENT_RE.search(name) or _PAYMENT_RE.search(target_el.type or ""):
                return {"decision": "TAKEOVER", "reason": "payment field requires user takeover"}
            if action == "type" and _LOGIN_RE.search(name):
                return {"decision": "APPROVAL", "reason": "typing into login-related field"}
            if action == "click" and _DESTRUCTIVE_RE.search(name):
                return {"decision": "APPROVAL", "reason": f"destructive control: {target_el.name[:80]}"}
            if action == "click" and any(k in name for k in ("submit", "confirm", "pay now", "purchase", "buy")):
                if _PAYMENT_RE.search(name) or "pay" in name or "purchase" in name:
                    return {"decision": "APPROVAL", "reason": "payment / purchase control"}

        if action == "navigate" and req.url:
            lower = req.url.lower()
            if any(x in lower for x in ("login", "signin", "auth", "oauth")):
                return {"decision": "ALLOW", "reason": "navigate to auth page (no credentials entered)"}

        if action == "upload":
            return {"decision": "ALLOW", "reason": "upload from workspace path"}

        return {"decision": "ALLOW", "reason": "default allow"}

    # ------------------------------------------------------------------ act

    def _resolve_target(
        self, req: ActionRequest
    ) -> tuple[Optional[Element], Optional[str], Optional[Observation]]:
        """Returns (element, stale_reason, fresh_obs_if_stale)."""
        if not req.target:
            return None, None, None
        last = self._last_obs
        if req.target.observation_id and last and req.target.observation_id != last.observation_id:
            return None, "observation_id mismatch", last
        if req.target.element_ref:
            if not last:
                return None, "no prior observation", None
            el = last.element_by_ref(req.target.element_ref)
            if not el:
                return None, "element_ref not found in observation", last
            # Fingerprint re-check is done after fresh observe in act(); here we only check presence.
            return el, None, None
        return None, None, None

    async def act(self, req: ActionRequest) -> ActionReceipt:
        action = req.action
        if action == "observe":
            obs = await self.observe(include_screenshot=True)
            return ActionReceipt(ok=True, action=action, observation=obs)

        # Resolve target against last observation
        el, stale_reason, _ = self._resolve_target(req)
        if stale_reason and req.target and (req.target.element_ref or req.target.observation_id):
            fresh = await self.observe(include_screenshot=False)
            return ActionReceipt(
                ok=False,
                action=action,
                stale_observation=True,
                error=f"stale_observation: {stale_reason}",
                observation=fresh,
            )

        before = self._last_obs
        policy = self.classify_action_policy(req, before)
        if policy["decision"] in ("TAKEOVER", "DENY"):
            fresh = await self.observe(include_screenshot=False)
            return ActionReceipt(
                ok=False,
                action=action,
                policy=policy,
                error=policy["reason"],
                observation=fresh,
            )
        if policy["decision"] == "APPROVAL":
            # Surface approval requirement; caller (loop/policy) handles waiting_approval.
            fresh = await self.observe(include_screenshot=False)
            return ActionReceipt(
                ok=False,
                action=action,
                policy=policy,
                error=f"requires_approval: {policy['reason']}",
                observation=fresh,
            )

        # Fingerprint check: re-observe lightly if we have an element target
        if el and before:
            # Quick check — if DOM mutated, fingerprints diverge
            try:
                quick = await self.observe(include_screenshot=False)
                match = quick.element_by_ref(el.ref)
                if not match or match.fingerprint != el.fingerprint:
                    # Try find by fingerprint
                    by_fp = next((e for e in quick.elements if e.fingerprint == el.fingerprint), None)
                    if not by_fp:
                        return ActionReceipt(
                            ok=False,
                            action=action,
                            stale_observation=True,
                            error="stale_observation: fingerprint no longer matches",
                            observation=quick,
                        )
                    el = by_fp
                    self._last_obs = quick
                else:
                    el = match
                    self._last_obs = quick
            except Exception:
                pass

        error: Optional[str] = None
        try:
            if action == "click":
                await self._do_click(req, el)
            elif action == "type":
                await self._do_type(req, el)
            elif action == "press_key":
                await self.page.keyboard.press(req.key or "Enter")
            elif action == "hotkey":
                keys = req.keys or ([req.key] if req.key else [])
                combo = "+".join(keys)
                await self.page.keyboard.press(combo)
            elif action == "scroll":
                amount = int(req.amount or 600)
                direction = (req.direction or "down").lower()
                dx = dy = 0
                if direction == "down":
                    dy = amount
                elif direction == "up":
                    dy = -amount
                elif direction == "right":
                    dx = amount
                elif direction == "left":
                    dx = -amount
                await self.page.mouse.wheel(dx, dy)
            elif action == "wait_for":
                ms = int(req.timeout_ms or 1000)
                await asyncio.sleep(ms / 1000.0)
            elif action == "select":
                if el:
                    # Use coordinates center click then keyboard, or select_option if select
                    if el.tag == "select":
                        await self.page.locator(f"css={el.tag}").nth(0).select_option(req.option or req.text or "")
                    else:
                        await self._do_click(req, el)
                elif req.option:
                    error = "select requires element target"
            elif action == "drag":
                error = "drag requires start/end coordinates; use coordinates path"
                if req.target and req.target.coordinates:
                    # Minimal: click only
                    await self._do_click(req, el)
                    error = None
            elif action == "upload":
                if not req.file_path:
                    error = "upload requires file_path"
                elif el:
                    # Playwright set_input_files on file input
                    locator = self.page.locator("input[type=file]").first
                    await locator.set_input_files(req.file_path)
                else:
                    locator = self.page.locator("input[type=file]").first
                    await locator.set_input_files(req.file_path)
            elif action == "navigate":
                if not req.url:
                    error = "navigate requires url"
                else:
                    from app.services.agent_computer import validate_public_url
                    url = validate_public_url(req.url)
                    await self.page.goto(url, wait_until="domcontentloaded", timeout=30_000)
            elif action == "open_tab":
                # Single-page sessions: navigate instead
                if req.url:
                    from app.services.agent_computer import validate_public_url
                    url = validate_public_url(req.url)
                    await self.page.goto(url, wait_until="domcontentloaded", timeout=30_000)
            elif action == "close_tab":
                pass  # single-page: no-op
            else:
                error = f"unknown action {action}"
        except Exception as exc:
            error = str(exc)[:400]

        after = await self.observe(include_screenshot=False)
        diff = self._diff(before, after)
        return ActionReceipt(
            ok=error is None,
            action=action,
            error=error,
            policy=policy,
            observation=after,
            diff=diff,
        )

    async def _do_click(self, req: ActionRequest, el: Optional[Element]) -> None:
        if el:
            x = el.bbox.x + el.bbox.w / 2
            y = el.bbox.y + el.bbox.h / 2
            await self.page.mouse.click(x, y)
            return
        if req.target and req.target.coordinates:
            c = req.target.coordinates
            x, y = float(c["x"]), float(c["y"])
            if c.get("normalized"):
                vp = self.page.viewport_size or {"width": 1280, "height": 720}
                x *= vp["width"]
                y *= vp["height"]
            await self.page.mouse.click(x, y)
            return
        raise ValueError("click requires element_ref or coordinates")

    async def _do_type(self, req: ActionRequest, el: Optional[Element]) -> None:
        text = req.text or ""
        if el:
            x = el.bbox.x + el.bbox.w / 2
            y = el.bbox.y + el.bbox.h / 2
            await self.page.mouse.click(x, y)
            if req.clear:
                await self.page.keyboard.press("Control+a")
                await self.page.keyboard.press("Backspace")
            await self.page.keyboard.type(text, delay=10)
            return
        if req.clear:
            await self.page.keyboard.press("Control+a")
            await self.page.keyboard.press("Backspace")
        await self.page.keyboard.type(text, delay=10)

    @staticmethod
    def _diff(before: Optional[Observation], after: Observation) -> dict[str, Any]:
        if not before:
            return {
                "url_changed": True,
                "title_changed": True,
                "screenshot_changed": True,
                "elements_added": len(after.elements),
                "elements_removed": 0,
            }
        before_fps = {e.fingerprint for e in before.elements}
        after_fps = {e.fingerprint for e in after.elements}
        return {
            "url_changed": before.url != after.url,
            "title_changed": before.title != after.title,
            "screenshot_changed": before.screenshot_ref != after.screenshot_ref,
            "elements_added": len(after_fps - before_fps),
            "elements_removed": len(before_fps - after_fps),
        }


# ---------------------------------------------------------------------------
# Tool surface for the agent loop
# ---------------------------------------------------------------------------

BROWSER_TOOL_PARAMETERS = {
    "type": "object",
    "properties": {
        "action": {
            "type": "string",
            "enum": list(__import__("app.runtime.browser.protocol", fromlist=["PROTOCOL_ACTION_NAMES"]).PROTOCOL_ACTION_NAMES)
            if False
            else [
                "click", "type", "press_key", "hotkey", "scroll", "wait_for",
                "select", "drag", "upload", "navigate", "open_tab", "close_tab", "observe",
            ],
            "description": "Browser action per protocol v4",
        },
        "observation_id": {"type": "string", "description": "Observation id from the last observe/receipt"},
        "element_ref": {"type": "string", "description": "Element ref from observation.elements[].ref"},
        "coordinates": {
            "type": "object",
            "properties": {
                "x": {"type": "number"},
                "y": {"type": "number"},
                "normalized": {"type": "boolean"},
            },
        },
        "text": {"type": "string"},
        "key": {"type": "string"},
        "keys": {"type": "array", "items": {"type": "string"}},
        "direction": {"type": "string", "enum": ["up", "down", "left", "right"]},
        "amount": {"type": "number"},
        "url": {"type": "string"},
        "file_path": {"type": "string"},
        "option": {"type": "string"},
        "timeout_ms": {"type": "integer"},
        "clear": {"type": "boolean"},
        "include_screenshot": {"type": "boolean"},
    },
    "required": ["action"],
}
