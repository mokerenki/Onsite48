import structlog
from typing import Dict, Any, Optional, List
import base64
from playwright.async_api import async_playwright, Browser, Page

logger = structlog.get_logger("aether.services.browser_automation_service")


class BrowserAutomationService:
    """
    A service for real browser automation using Playwright.
    Supports headless and headed modes.
    """

    def __init__(self, headless: bool = True, timeout_ms: int = 30000):
        """
        Initialize the browser automation service.
        
        Args:
            headless: Run browser in headless mode (default: True)
            timeout_ms: Default timeout for operations (default: 30000ms)
        """
        self.headless = headless
        self.timeout_ms = timeout_ms
        self.browser: Optional[Browser] = None
        self.page: Optional[Page] = None
        logger.info("browser_automation_service.initialized", headless=headless)

    async def start(self):
        """
        Start the browser instance.
        """
        try:
            self.playwright_instance = await async_playwright().start()
            self.browser = await self.playwright_instance.chromium.launch(headless=self.headless)
            self.page = await self.browser.new_page()
            logger.info("browser_automation_service.started")
        except Exception as e:
            logger.error("browser_automation_service.start_failed", error=str(e), exc_info=True)
            raise

    async def stop(self):
        """
        Stop the browser instance.
        """
        if self.browser:
            await self.browser.close()
            self.browser = None
        if hasattr(self, 'playwright_instance') and self.playwright_instance:
            await self.playwright_instance.stop()
            logger.info("browser_automation_service.stopped")

    async def navigate(self, url: str) -> Dict[str, Any]:
        """
        Navigate to a URL.
        
        Args:
            url: URL to navigate to
        
        Returns:
            Dictionary with navigation result
        """
        try:
            if not self.page:
                raise RuntimeError("Browser page not initialized. Call start() first.")
            
            await self.page.goto(url, timeout=self.timeout_ms)
            logger.info("browser_automation_service.navigated", url=url)
            
            return {
                "status": "success",
                "url": self.page.url,
                "title": await self.page.title()
            }
        
        except Exception as e:
            logger.error("browser_automation_service.navigate_failed", url=url, error=str(e))
            return {"status": "error", "error": str(e)}

    async def get_content(self) -> Dict[str, Any]:
        """
        Extract page HTML content.
        
        Returns:
            Dictionary with page content
        """
        try:
            if not self.page:
                raise RuntimeError("No page loaded. Navigate to a URL first.")
            
            content = await self.page.content()
            logger.info("browser_automation_service.content_extracted", length=len(content))
            
            return {
                "status": "success",
                "content": content,
                "url": self.page.url
            }
        
        except Exception as e:
            logger.error("browser_automation_service.get_content_failed", error=str(e))
            return {"status": "error", "error": str(e)}

    async def click(self, selector: str) -> Dict[str, Any]:
        """
        Click an element.
        
        Args:
            selector: CSS selector for the element
        
        Returns:
            Dictionary with result
        """
        try:
            if not self.page:
                raise RuntimeError("No page loaded.")
            
            await self.page.click(selector, timeout=self.timeout_ms)
            logger.info("browser_automation_service.clicked", selector=selector)
            
            return {"status": "success", "selector": selector}
        
        except Exception as e:
            logger.error("browser_automation_service.click_failed", selector=selector, error=str(e))
            return {"status": "error", "error": str(e)}

    async def fill(self, selector: str, text: str) -> Dict[str, Any]:
        """
        Fill a form field.
        
        Args:
            selector: CSS selector for the input field
            text: Text to fill
        
        Returns:
            Dictionary with result
        """
        try:
            if not self.page:
                raise RuntimeError("No page loaded.")
            
            await self.page.fill(selector, text, timeout=self.timeout_ms)
            logger.info("browser_automation_service.filled", selector=selector)
            
            return {"status": "success", "selector": selector, "text_length": len(text)}
        
        except Exception as e:
            logger.error("browser_automation_service.fill_failed", selector=selector, error=str(e))
            return {"status": "error", "error": str(e)}

    async def screenshot(self, path: Optional[str] = None) -> Dict[str, Any]:
        """
        Capture a screenshot.
        
        Args:
            path: Optional file path to save screenshot
        
        Returns:
            Dictionary with screenshot data (base64 encoded)
        """
        try:
            if not self.page:
                raise RuntimeError("No page loaded.")
            
            screenshot_bytes = await self.page.screenshot()
            screenshot_b64 = base64.b64encode(screenshot_bytes).decode("utf-8")
            
            if path:
                with open(path, "wb") as f:
                    f.write(screenshot_bytes)
                logger.info("browser_automation_service.screenshot_saved", path=path)
            
            return {
                "status": "success",
                "screenshot_base64": screenshot_b64,
                "size": len(screenshot_bytes)
            }
        
        except Exception as e:
            logger.error("browser_automation_service.screenshot_failed", error=str(e))
            return {"status": "error", "error": str(e)}

    async def extract_visual_elements(self) -> Dict[str, Any]:
        """
        Extract visual structure and elements from the page.
        Useful for understanding page layout for multi-modal reasoning.
        
        Returns:
            Dictionary with visual elements
        """
        try:
            if not self.page:
                raise RuntimeError("No page loaded.")
            
            elements = await self.page.evaluate("""
                () => {
                    const elements = [];
                    document.querySelectorAll('h1, h2, h3, button, a, input, textarea, img').forEach(el => {
                        elements.push({
                            tag: el.tagName,
                            text: el.innerText || el.placeholder || el.alt || '',
                            type: el.type || '',
                            id: el.id || '',
                            class: el.className || '',
                            visible: el.offsetParent !== null
                        });
                    });
                    return elements;
                }
            """)
            
            logger.info("browser_automation_service.visual_elements_extracted", count=len(elements))
            
            return {
                "status": "success",
                "elements": elements,
                "count": len(elements)
            }
        
        except Exception as e:
            logger.error("browser_automation_service.extract_visual_elements_failed", error=str(e))
            return {"status": "error", "error": str(e)}

    async def wait_for_selector(self, selector: str, timeout_ms: Optional[int] = None) -> Dict[str, Any]:
        """
        Wait for an element to appear on the page.
        
        Args:
            selector: CSS selector to wait for
            timeout_ms: Optional timeout override
        
        Returns:
            Dictionary with result
        """
        try:
            if not self.page:
                raise RuntimeError("No page loaded.")
            
            await self.page.wait_for_selector(selector, timeout=timeout_ms or self.timeout_ms)
            logger.info("browser_automation_service.selector_appeared", selector=selector)
            
            return {"status": "success", "selector": selector}
        
        except Exception as e:
            logger.error("browser_automation_service.wait_for_selector_failed", selector=selector, error=str(e))
            return {"status": "error", "error": str(e)}

    async def evaluate(self, script: str) -> Dict[str, Any]:
        """
        Evaluate JavaScript in the page context.
        
        Args:
            script: JavaScript code to evaluate
        
        Returns:
            Dictionary with evaluation result
        """
        try:
            if not self.page:
                raise RuntimeError("No page loaded.")
            
            result = await self.page.evaluate(script)
            logger.info("browser_automation_service.script_evaluated")
            
            return {"status": "success", "result": result}
        
        except Exception as e:
            logger.error("browser_automation_service.evaluate_failed", error=str(e))
            return {"status": "error", "error": str(e)}