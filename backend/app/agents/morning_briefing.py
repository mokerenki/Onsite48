import asyncio
import json
import structlog
from datetime import datetime, timedelta, timezone
from typing import Dict, Any, List
from app.core.model_router_kimi_deepseek import KimiDeepSeekRouter
from app.memory.cognee_setup import CogneeMemory
from langchain_core.messages import HumanMessage, SystemMessage  # type: ignore[import-not-found]

logger = structlog.get_logger("aether.agents.morning_briefing")

class MorningBriefingOrchestrator:
    """
    Orchestrates the generation of a daily morning briefing.
    It queries the agent's memory for urgent items, scheduled tasks, in-progress matters,
    and recent changes, then synthesizes this information into a structured summary.
    """

    def __init__(self, model_router: KimiDeepSeekRouter, cognee_memory: CogneeMemory, *, organization_id: str = "default", user_id: str = "default", thread_id: str = "morning-briefing"):
        self.model_router = model_router
        self.cognee_memory = cognee_memory
        self.organization_id = organization_id
        self.user_id = user_id
        self.thread_id = thread_id
        logger.info("morning_briefing_orchestrator.initialized")
        # Prefer the agent path: ask "morning briefing" in chat so planner uses
        # gmail_search_messages + google_calendar_list_events with user identity.
        # This orchestrator remains for scheduled jobs that inject their own data.

    async def generate_briefing(self) -> str:
        """
        Generates the comprehensive morning briefing.
        """
        logger.info("morning_briefing.generation_started")
        
        briefing_components = {
            "urgent_items": await self._get_urgent_items(),
            "todays_schedule": await self._get_todays_schedule(),
            "in_progress_matters": await self._get_in_progress_matters(),
            "recent_changes": await self._get_recent_changes()
        }
        
        briefing_summary = await self._synthesize_briefing(briefing_components)
        
        logger.info("morning_briefing.generation_completed")
        return briefing_summary

    async def _get_urgent_items(self) -> List[Dict[str, Any]]:
        """
        Queries memory for urgent items like court deadlines, new filings, client emergencies.
        """
        # Example: Semantic search for urgent items
        query = "urgent legal deadlines, client emergencies, critical tasks"
        results = await self.cognee_memory.search(query=query, mode="semantic", top_k=5, organization_id=self.organization_id, user_id=self.user_id, thread_id=self.thread_id)
        logger.debug("morning_briefing.urgent_items_found", count=len(results.get("results", [])))
        return results.get("results", [])

    async def _get_todays_schedule(self) -> List[Dict[str, Any]]:
        """
        Queries memory for today's scheduled tasks and appointments.
        """
        today = datetime.now().strftime("%Y-%m-%d")
        today_start = datetime.strptime(today, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        results = await self.cognee_memory.search(query="tasks or appointments", mode="semantic", query_time=today_start, top_k=5, organization_id=self.organization_id, user_id=self.user_id, thread_id=self.thread_id)
        logger.debug("morning_briefing.todays_schedule_found", count=len(results.get("results", [])))
        return results.get("results", [])

    async def _get_in_progress_matters(self) -> List[Dict[str, Any]]:
        """
        Queries memory for in-progress matters and their last status.
        """
        # Example: Graph query for in-progress cases/contracts
        query = "MATCH (n)-[r]->(m) WHERE n.status = 'in_progress' RETURN n, r, m LIMIT 5"
        results = await self.cognee_memory.search(query=query, mode="graph", organization_id=self.organization_id, user_id=self.user_id, thread_id=self.thread_id)
        logger.debug("morning_briefing.in_progress_matters_found", count=len(results.get("results", [])))
        return results.get("results", [])

    async def _get_recent_changes(self) -> List[Dict[str, Any]]:
        """
        Queries memory for recent changes since the previous day.
        """
        yesterday = (datetime.now() - timedelta(days=1)).replace(tzinfo=timezone.utc)
        results = await self.cognee_memory.search(query="recent events and changes", mode="semantic", query_time=yesterday, top_k=10, organization_id=self.organization_id, user_id=self.user_id, thread_id=self.thread_id)
        logger.debug("morning_briefing.recent_changes_found", count=len(results.get("results", [])))
        return results.get("results", [])

    async def _synthesize_briefing(self, components: Dict[str, Any]) -> str:
        """
        Uses the LLM to synthesize the collected information into a coherent briefing.
        """
        system_message = SystemMessage(content=(
            "You are an expert personal assistant. Your task is to compile a concise and structured "
            "morning briefing from the provided information. Highlight urgent items, list today's "
            "schedule, summarize in-progress matters, and note recent changes. Format it professionally."
        ))
        
        user_message = HumanMessage(content=f"""
        Here is the information to compile for the morning briefing:
        
        Urgent Items: {json.dumps(components["urgent_items"], indent=2)}
        
        Today's Schedule: {json.dumps(components["todays_schedule"], indent=2)}
        
        In-Progress Matters: {json.dumps(components["in_progress_matters"], indent=2)}
        
        Recent Changes: {json.dumps(components["recent_changes"], indent=2)}
        
        Please generate the morning briefing.
        """)
        
        messages = [system_message, user_message]
        
        try:
            response = await self.model_router.route("fallback", messages)
            return response.content
        except Exception as e:
            logger.error("morning_briefing.synthesis_failed", error=str(e), exc_info=True)
            return f"Failed to generate briefing: {str(e)}"