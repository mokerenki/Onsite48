"""User-controlled login handoff helpers for persistent browser sessions.

Synth may navigate to a login page and identify a visible human-verification
challenge, but it never enters target-site credentials, OTPs, CAPTCHA values, or
submits target-site authentication forms. The user completes login directly in
their own browser session, then can continue through Onsite48 Bridge read/inspect
operations.
"""

from __future__ import annotations

import structlog
from typing import Any, Dict, Optional

from playwright.async_api import Page

from app.services.persistent_browser import persistent_browser

logger = structlog.get_logger("onsite48.services.auth")


class AuthHandler:
    """Provide safe login-page navigation and human-controlled handoffs."""

    def __init__(self) -> None:
        # Retained only for compatibility with callers that previously stored
        # connector metadata here. Login() deliberately never reads or enters
        # these values into a target site.
        self._credentials: Dict[str, Dict[str, Any]] = {}

    async def get_credentials(self, user_id: str, site: str) -> Optional[Dict[str, Any]]:
        """Return compatibility metadata; it is never used for browser entry."""
        return self._credentials.get(user_id, {}).get(site)

    async def store_credentials(self, user_id: str, site: str, credentials: Dict[str, Any]) -> None:
        """Retain compatibility metadata without using it for target-site login."""
        self._credentials.setdefault(user_id, {})[site] = credentials
        logger.info("auth.credentials_metadata_stored", user_id=user_id, site=site)

    async def login(
        self,
        user_id: str,
        site: str,
        url: str,
        credentials: Optional[Dict[str, Any]] = None,
        selectors: Optional[Dict[str, str]] = None,
    ) -> Dict[str, Any]:
        """Navigate to login and return a user-controlled completion handoff.

        Parameters formerly accepted for automated form filling are retained for
        backward-compatible callers but are intentionally ignored. Synth never
        reads password/OTP values into page fields or submits the login form.
        """
        del credentials, selectors
        try:
            page = await persistent_browser.get_page(user_id)
            await page.goto(url, timeout=30000)
            await page.wait_for_load_state("networkidle", timeout=10000)
            if await self._detect_captcha(page):
                return await self._handle_captcha(page, user_id, site)
            logger.info("auth.user_login_required", user_id=user_id, site=site)
            return {
                "status": "requires_user_login",
                "code": "user_login_required",
                "reason": "Target-site login must be completed by the user in their own browser session.",
                "message": "Complete the login yourself in the browser, then return to Synth to continue with the approved tab.",
                "site": site,
                "url": page.url,
            }
        except Exception as exc:
            logger.exception("auth.login_navigation_failed", user_id=user_id, site=site, error=str(exc))
            return {
                "status": "error",
                "message": f"Could not open the login page: {str(exc)}",
            }

    async def _detect_captcha(self, page: Page) -> bool:
        """Detect a target-site challenge without extracting or solving it."""
        try:
            content_lower = (await page.content()).lower()
            indicators = (
                "captcha",
                "recaptcha",
                "hcaptcha",
                "turnstile",
                "verify you are human",
                "i'm not a robot",
            )
            return any(indicator in content_lower for indicator in indicators)
        except Exception:
            return False

    async def _handle_captcha(self, page: Page, user_id: str, site: str) -> Dict[str, Any]:
        """Return an explicit handoff instead of solving or submitting a challenge."""
        logger.info("auth.human_verification_required", user_id=user_id, site=site)
        return {
            "status": "requires_human_verification",
            "code": "human_verification_required",
            "reason": "A CAPTCHA or anti-bot challenge was detected during login.",
            "message": "Complete the verification yourself in the browser, then retry the login.",
            "site": site,
            "url": page.url,
        }


# ─── Global instance ─────────────────────────────────────────────

auth_handler = AuthHandler()