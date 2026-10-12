"""Domain routing with deterministic fast paths for unambiguous requests."""

from __future__ import annotations

from typing import Any, Dict

import structlog
from langchain_core.messages import HumanMessage, SystemMessage  # type: ignore[import-not-found]

from app.core.model_router_kimi_deepseek import KimiDeepSeekRouter
from app.mcp_clients import MCPRegistry
from app.mcp_clients.mcp_registry import get_domain_prompt

logger = structlog.get_logger("aether.agents.router")


class DomainRouter:
    """Select the appropriate specialist context without wasting model calls."""

    DOMAINS = ["sales", "marketing", "finance", "executive", "healthcare", "product", "productivity"]
    DOMAIN_TERMS = {
        "sales": ("lead", "pipeline", "opportunity", "crm", "prospect", "deal", "salesforce"),
        "marketing": ("campaign", "brand", "seo", "content", "social media", "ad ", "ads", "marketing"),
        "finance": ("budget", "invoice", "expense", "revenue", "forecast", "financial", "cash flow"),
        "executive": ("kpi", "okr", "strategy", "board", "leadership", "initiative", "roadmap"),
        "healthcare": ("patient", "claim", "insurance", "medical", "healthcare"),
        "product": ("feature", "backlog", "user feedback", "product", "release", "sprint"),
        "productivity": ("calendar", "gmail", "email", "slack", "meeting", "schedule", "appointment", "event"),
    }

    def __init__(self, model_router: KimiDeepSeekRouter, mcp_registry: MCPRegistry):
        self.model_router = model_router
        self.mcp_registry = mcp_registry
        logger.info("domain_router.initialized")

    def _deterministic_domain(self, user_message: str) -> str | None:
        normalized = " ".join(user_message.lower().split())
        matching_domains = [
            domain
            for domain, terms in self.DOMAIN_TERMS.items()
            if any(term in normalized for term in terms)
        ]
        if len(matching_domains) == 1:
            return matching_domains[0]
        # A short request without a specialist signal is unambiguously general.
        if len(normalized) <= 240 and not matching_domains:
            return "general"
        return None

    async def _route_result(self, domain: str, user_id: str, thread_id: str, *, source: str) -> Dict[str, Any]:
        context_loader = getattr(self.mcp_registry, "get_domain_context", None)
        context: dict[str, Any] = {}
        if callable(context_loader):
            try:
                loaded = await context_loader(domain, user_id, thread_id)
                context = loaded if isinstance(loaded, dict) else {}
            except Exception as exc:
                logger.warning("domain_router.context_unavailable", domain=domain, error=str(exc)[:160])
        system_prompt = get_domain_prompt(domain)
        logger.info("domain_router.routed", domain=domain, source=source, user_message=user_id[:12])
        return {"domain": domain, "context": context, "system_prompt": system_prompt}

    async def route(self, user_message: str, user_id: str = "anonymous", thread_id: str = "default") -> Dict[str, Any]:
        """Route clear cases deterministically and use an LLM only for ambiguity."""
        deterministic = self._deterministic_domain(user_message)
        if deterministic is not None:
            return await self._route_result(deterministic, user_id, thread_id, source="deterministic")

        classification_prompt = SystemMessage(content=f"""
You are a domain classifier. Classify the user's request into one of these domains:
{', '.join(self.DOMAINS)}.

sales: leads, opportunities, pipeline, CRM, and closing deals.
marketing: campaigns, content, social media, lead generation, and branding.
finance: budgets, expenses, invoices, and financial analysis.
executive: strategic decisions, KPIs, goals, and high-level planning.
healthcare: claims, insurance, scheduling, and patient administration.
product: product management, roadmaps, features, and feedback.
productivity: email, calendar, slack, meetings, scheduling, and messaging.
general: anything else.

Respond with only the domain name.
""")
        try:
            response = await self.model_router.route("fallback", [classification_prompt, HumanMessage(content=user_message)], max_tokens=12)
            domain = str(response.content or "").strip().lower()
            if domain not in self.DOMAINS:
                domain = "general"
            return await self._route_result(domain, user_id, thread_id, source="model")
        except Exception as exc:
            logger.warning("domain_router.classification_failed", error=str(exc)[:160])
            return await self._route_result("general", user_id, thread_id, source="fallback")