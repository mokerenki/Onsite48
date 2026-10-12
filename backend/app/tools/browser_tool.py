"""
BrowserTool -- web search backed by the Tavily Search API.

Replaces the earlier DuckDuckGo Instant Answer stub. Tavily is purpose-built
for LLM/agent consumption: results come back as structured JSON with a real
source URL per result, which is exactly what the Verifier has been asking
for when it complains about missing citations.

Note: this tool is named "browser" for now to avoid touching every other
file that references it (planner prompts, executor, tool registry key).
Once real interactive browsing (BrowserAutomationService -- navigate/click/
fill/screenshot) is wired in as its own tool, consider renaming this one to
"web_search" to make the distinction between "search the web" and "drive an
actual browser" clear to the planner.
"""

import os

import httpx
import structlog
from bs4 import BeautifulSoup
from urllib.parse import quote_plus

from app.tools.registry import BaseTool, ToolSchema

logger = structlog.get_logger("aether.tools.browser")

TAVILY_API_URL = "https://api.tavily.com/search"


class BrowserTool(BaseTool):
    def _build_schema(self) -> ToolSchema:
        return ToolSchema(
            name="browser",
            description=(
                "Search the web for factual, up-to-date information. Returns a short "
                "synthesized answer plus a list of source results, each with a title, "
                "URL, and content snippet -- use the URLs as citations."
            ),
            parameters={
                "query": {
                    "type": "string",
                    "description": "The search query, e.g., 'current president of South Africa'",
                }
            },
            required=["query"],
            sandbox_template="browser-sandbox",  # Phase 2
            idempotency="read_only",
            approval_required=False,
        )

    async def execute(self, **kwargs):
        query = kwargs.get("query", "")
        if not query:
            return {"output": "No search query provided."}

        api_key = os.getenv("TAVILY_API_KEY", "").strip()
        if not api_key:
            # Scheduled and ordinary public research should remain usable in a
            # starter deployment. Tavily remains the preferred structured
            # provider, while this read-only fallback avoids failing before the
            # first plan step solely because an optional search key is absent.
            logger.warning("tavily_api_key_missing_using_public_fallback")
            return await self._public_search_fallback(query)

        payload = {
            "query": query,
            "search_depth": "basic",
            "max_results": 5,
            "include_answer": True,
        }
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }

        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.post(TAVILY_API_URL, json=payload, headers=headers)

            if resp.status_code != 200:
                logger.warning("tavily_api_bad_status", status=resp.status_code, query=query)
                if resp.status_code in {401, 403}:
                    return {
                        "output": "Error: Research unavailable because the configured public web search key was rejected.",
                        "error_code": "web_search_auth_failed",
                        "permanent": True,
                        "user_message": "Public web research is unavailable because TAVILY_API_KEY was rejected. Replace it in .env, then run docker compose up --build -d before retrying.",
                    }
                return {"output": f"Error: Public web search failed with status {resp.status_code}. Try again shortly."}

            data = resp.json()
            return {"output": self._format_results(data, query)}

        except httpx.TimeoutException:
            logger.warning("tavily_timeout", query=query)
            return {"output": "Error: Public web search timed out. Try again or use a more specific query."}
        except Exception:
            logger.exception("tavily_api_error", query=query)
            return {"output": "Error: Public web search failed due to an unexpected error."}

    async def _public_search_fallback(self, query: str) -> dict:
        """Best-effort public search for read-only tasks when Tavily is not configured."""
        url = f"https://html.duckduckgo.com/html/?q={quote_plus(query)}"
        try:
            async with httpx.AsyncClient(timeout=15.0, follow_redirects=True, headers={"User-Agent": "Mozilla/5.0"}) as client:
                response = await client.get(url)
            if response.status_code != 200:
                return {"output": f"Error: Public web search fallback returned status {response.status_code}. Add TAVILY_API_KEY for reliable structured research."}
            soup = BeautifulSoup(response.text, "html.parser")
            parts = ["Public web-search results (fallback source):"]
            for result in soup.select(".result")[:5]:
                link = result.select_one("a.result__a")
                snippet = result.select_one(".result__snippet")
                if not link:
                    continue
                title = link.get_text(" ", strip=True)
                href = link.get("href", "")
                text = snippet.get_text(" ", strip=True) if snippet else ""
                parts.append(f"- {title} ({href})\\n  {text}")
            if len(parts) == 1:
                return {"output": "Error: Public web-search fallback returned no readable results. Add TAVILY_API_KEY for reliable structured research."}
            return {"output": "\\n".join(parts), "source": "public_search_fallback"}
        except Exception as exc:
            logger.warning("public_search_fallback_failed", error=str(exc)[:160])
            return {"output": "Error: Public web search is unavailable. Check connectivity or add TAVILY_API_KEY for reliable structured research."}

    def _format_results(self, data: dict, query: str) -> str:
        parts = []

        answer = data.get("answer")
        if answer:
            parts.append(f"Answer: {answer}")

        results = data.get("results", [])
        if not results:
            return "\n".join(parts) if parts else f"No results found for '{query}'."

        parts.append("Sources:")
        for r in results:
            title = r.get("title", "Untitled")
            url = r.get("url", "")
            content = (r.get("content") or "").strip()
            if len(content) > 400:
                content = content[:400].rstrip() + "..."
            parts.append(f"- {title} ({url})\n  {content}")

        return "\n".join(parts)