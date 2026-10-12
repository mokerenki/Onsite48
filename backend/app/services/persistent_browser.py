# backend/app/services/persistent_browser.py

import asyncio
import hashlib
import json
import os
import re
import structlog
from datetime import datetime, timezone
from typing import Any, Dict, Optional, List
from playwright.async_api import async_playwright, Browser, BrowserContext, Page

from app.core.context import onsite48

logger = structlog.get_logger("onsite48.services.persistent_browser")


class PersistentBrowserService:
    """
    Manages persistent browser sessions per user.
    Cookies, localStorage, and sessions persist across tasks.
    """
    
    def __init__(self, headless: bool = True):
        self.headless = headless
        self._sessions: Dict[str, Dict[str, Any]] = {}  # user_id -> session data
        self._playwright = None
        self._initialized = False
        
    async def initialize(self):
        """Initialize the browser service."""
        if self._initialized:
            return
        
        self._playwright = await async_playwright().start()
        self._initialized = True
        logger.info("persistent_browser.initialized")
    
    async def _get_browser_context(self, user_id: str) -> BrowserContext:
        """Get or create a browser context for a user."""
        await self.initialize()
        
        # Check if we already have a context for this user
        if user_id in self._sessions:
            session = self._sessions[user_id]
            context = session.get("context")
            
            # Check if context is still valid.
            #
            # This used to be `await context.pages`, which always raised
            # TypeError because `pages` is a property returning List[Page], not
            # an awaitable. The bare `except Exception` swallowed that and
            # destroyed the context on every single call, so a "persistent"
            # session was recreated each time -- throwing away live page state
            # mid-flow and making persistence pointless. Liveness is now the
            # browser connection, which is a real signal.
            if self._context_is_live(context):
                session["last_used"] = datetime.now(timezone.utc).isoformat()
                return context

            await self._close_session(user_id)
        
        # Create new context with persistent storage
        context = await self._create_context(user_id)
        
        self._sessions[user_id] = {
            "context": context,
            "user_id": user_id,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "last_used": datetime.now(timezone.utc).isoformat(),
            "pages": [],
        }
        
        logger.info("persistent_browser.context_created", user_id=user_id)
        return context
    
    @staticmethod
    def _context_is_live(context: Any) -> bool:
        """Whether the context's browser is still connected.

        Playwright exposes no is_closed() on a context, so the browser
        connection is the honest signal: when the browser process dies the
        context is unusable no matter what the context object reports.
        """
        if context is None:
            return False
        try:
            browser = context.browser
            if browser is None:
                return False
            return bool(browser.is_connected())
        except Exception:
            # A context that cannot answer the question is treated as dead.
            return False

    @staticmethod
    def _storage_slug(user_id: str) -> str:
        """A filesystem-safe, collision-free directory name for a user.

        The raw user id was interpolated straight into a path, so a value
        containing `../` escaped its directory -- and this directory holds
        browser storage state, which is another tenant's authenticated cookies.
        """
        raw = str(user_id or "").strip()
        if not raw:
            raise ValueError("A user id is required to create a browser session.")
        safe = re.sub(r"[^A-Za-z0-9._-]", "_", raw).strip("._-")
        if False:
            safe = safe
        digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]
        return f"{safe[:48] or 'user'}-{digest}" if safe else f"user-{digest}"

    async def _create_context(self, user_id: str) -> BrowserContext:
        """Create a new browser context with persistent storage."""
        # Use persistent storage path for this user
        slug = self._storage_slug(user_id)
        storage_path = f"/tmp/browser_data/{slug}"
        state_path = f"/tmp/browser_state/{slug}.json"
        launch_options = {
            "headless": self.headless,
            "args": ["--no-sandbox", "--disable-dev-shm-usage", "--disable-setuid-sandbox"],
            "viewport": {"width": 1280, "height": 720},
        }
        # Playwright persistent profiles are created by
        # launch_persistent_context, not by Browser.new_context with a
        # user_data_dir. Keep a compatibility fallback for older fakes and
        # embedded callers that only expose chromium.launch/new_context.
        launcher = getattr(self._playwright.chromium, "launch_persistent_context", None)
        if launcher is not None:
            if os.path.exists(state_path):
                launch_options["storage_state"] = state_path
            context = await launcher(storage_path, **launch_options)
        else:
            browser = await self._playwright.chromium.launch(
                headless=self.headless,
                args=launch_options["args"],
            )
            context = await browser.new_context(
                user_data_dir=storage_path,
                storage_state=state_path,
                viewport=launch_options["viewport"],
            )

        # Load saved storage state if exists
        try:
            await context.storage_state()
        except Exception:
            pass

        return context
    
    async def get_page(self, user_id: str) -> Page:
        """Get or create a page for the user."""
        context = await self._get_browser_context(user_id)
        
        session = self._sessions.get(user_id)
        if session and session.get("pages"):
            # Reuse last page
            page = session["pages"][-1]
            if not page.is_closed():
                return page
        
        # Create new page
        page = await context.new_page()
        if session:
            session["pages"].append(page)
            session["last_used"] = datetime.now(timezone.utc).isoformat()
        
        return page
    
    async def navigate(self, user_id: str, url: str) -> Dict[str, Any]:
        """Navigate to a URL in the user's browser."""
        try:
            page = await self.get_page(user_id)
            await page.goto(url, timeout=30000)
            
            return {
                "status": "ok",
                "url": page.url,
                "title": await page.title(),
            }
        except Exception as e:
            logger.exception("browser.navigate_failed", user_id=user_id, url=url, error=str(e))
            return {"status": "error", "error": str(e)}
    
    async def click(self, user_id: str, selector: str) -> Dict[str, Any]:
        """Click an element in the user's browser."""
        try:
            page = await self.get_page(user_id)
            await page.click(selector, timeout=30000)
            return {"status": "ok", "selector": selector}
        except Exception as e:
            logger.exception("browser.click_failed", user_id=user_id, selector=selector, error=str(e))
            return {"status": "error", "error": str(e)}
    
    async def fill(self, user_id: str, selector: str, text: str) -> Dict[str, Any]:
        """Fill a form field in the user's browser."""
        try:
            page = await self.get_page(user_id)
            await page.fill(selector, text, timeout=30000)
            return {"status": "ok", "selector": selector}
        except Exception as e:
            logger.exception("browser.fill_failed", user_id=user_id, selector=selector, error=str(e))
            return {"status": "error", "error": str(e)}
    
    async def get_content(self, user_id: str) -> Dict[str, Any]:
        """Get the page content."""
        try:
            page = await self.get_page(user_id)
            content = await page.content()
            return {
                "status": "ok",
                "content": content,
                "url": page.url,
            }
        except Exception as e:
            logger.exception("browser.get_content_failed", user_id=user_id, error=str(e))
            return {"status": "error", "error": str(e)}
    
    async def screenshot(self, user_id: str) -> Dict[str, Any]:
        """Take a screenshot of the user's browser."""
        try:
            page = await self.get_page(user_id)
            screenshot_bytes = await page.screenshot()
            import base64
            screenshot_b64 = base64.b64encode(screenshot_bytes).decode("utf-8")
            return {
                "status": "ok",
                "screenshot_base64": screenshot_b64,
                "size": len(screenshot_bytes),
            }
        except Exception as e:
            logger.exception("browser.screenshot_failed", user_id=user_id, error=str(e))
            return {"status": "error", "error": str(e)}
    
    async def evaluate(self, user_id: str, script: str) -> Dict[str, Any]:
        """Evaluate JavaScript in the user's browser."""
        try:
            page = await self.get_page(user_id)
            result = await page.evaluate(script)
            return {"status": "ok", "result": result}
        except Exception as e:
            logger.exception("browser.evaluate_failed", user_id=user_id, error=str(e))
            return {"status": "error", "error": str(e)}
    
    async def get_cookies(self, user_id: str) -> Dict[str, Any]:
        """Get cookies from the user's browser."""
        try:
            context = await self._get_browser_context(user_id)
            cookies = await context.cookies()
            return {"status": "ok", "cookies": cookies}
        except Exception as e:
            logger.exception("browser.get_cookies_failed", user_id=user_id, error=str(e))
            return {"status": "error", "error": str(e)}
    
    async def set_cookies(self, user_id: str, cookies: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Set cookies in the user's browser."""
        try:
            context = await self._get_browser_context(user_id)
            await context.add_cookies(cookies)
            return {"status": "ok", "cookies_set": len(cookies)}
        except Exception as e:
            logger.exception("browser.set_cookies_failed", user_id=user_id, error=str(e))
            return {"status": "error", "error": str(e)}
    
    async def close_session(self, user_id: str) -> None:
        """Close a user's browser session."""
        await self._close_session(user_id)
    
    async def _close_session(self, user_id: str) -> None:
        """Internal method to close a session."""
        if user_id in self._sessions:
            session = self._sessions.pop(user_id)
            context = session.get("context")
            if context:
                try:
                    # Save storage state before closing
                    try:
                        storage = await context.storage_state()
                        # Save to disk
                        import os
                        os.makedirs("/tmp/browser_state", exist_ok=True)
                        slug = self._storage_slug(user_id)
                        with open(f"/tmp/browser_state/{slug}.json", "w") as f:
                            json.dump(storage, f)
                    except Exception:
                        pass
                    
                    await context.close()
                except Exception as e:
                    logger.warning("browser.context_close_failed", user_id=user_id, error=str(e))
            
            logger.info("persistent_browser.session_closed", user_id=user_id)
    
    async def shutdown(self):
        """Shutdown all browser sessions."""
        for user_id in list(self._sessions.keys()):
            await self._close_session(user_id)
        
        if self._playwright:
            await self._playwright.stop()
        
        logger.info("persistent_browser.shutdown")


# ─── Global Instance ─────────────────────────────────────────────

persistent_browser = PersistentBrowserService(headless=True)