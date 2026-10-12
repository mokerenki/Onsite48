"""Wide Research: bounded parallel source-specialist research for Onsite48."""

from __future__ import annotations

import asyncio
import json
import re
import time
from typing import Any

import structlog

from app.core.model_router_kimi_deepseek import KimiDeepSeekRouter
from app.tools.browser_tool import BrowserTool
from app.tools.registry import BaseTool, ToolSchema
from app.agents.codeact import CodeActRunner
from app.core.cost_accounting import record_code_execution_cost

logger = structlog.get_logger("aether.tools.wide_research")

_DEFAULT_FACETS = (
    "official documentation and primary sources",
    "recent developments, announcements, and current status",
    "independent expert analysis and competing viewpoints",
    "market, practical, or operational implications",
)
_MAX_FACETS = 6
_MAX_QUERY_CHARS = 500
_MAX_RESULT_CHARS = 3500
_MAX_CONCURRENCY = 4
_BRANCH_TIMEOUT_SECONDS = 75
_URL_PATTERN = re.compile(r"https?://[^\s)\]>]+")


class WideResearchTool(BaseTool):
    """Run bounded parallel read-only research branches and return evidence dossiers."""

    def __init__(self, browser: BrowserTool, router: KimiDeepSeekRouter):
        self.browser = browser
        self.router = router
        super().__init__()

    def _build_schema(self) -> ToolSchema:
        return ToolSchema(
            name="wide_research",
            description=(
                "Run Wide Research across several independent source-specialist branches in parallel. "
                "Use for deep, multi-angle public research and comparisons. Each branch searches a different "
                "facet, preserves source URLs, and returns a bounded evidence dossier. Read-only; never mutates "
                "external systems. Provide explicit facets when the user has named the angles to investigate."
            ),
            parameters={
                "query": {
                    "type": "string",
                    "description": "The central research question or topic.",
                },
                "facets": {
                    "type": "array",
                    "description": "Optional list of specialist angles; maximum 6.",
                    "items": {"type": "string"},
                },
                "max_agents": {
                    "type": "integer",
                    "description": "Maximum parallel source-specialist branches, from 2 to 6.",
                },
            },
            required=["query"],
            runtime="internal",
            idempotency="read_only",
            approval_required=False,
            timeout_seconds=90,
            max_attempts=1,
        )

    @staticmethod
    def _clean(value: Any, limit: int) -> str:
        return re.sub(r"\s+", " ", str(value or "")).strip()[:limit]

    def _facets(self, raw: Any, max_agents: int) -> list[str]:
        candidates = raw if isinstance(raw, list) else []
        facets = [self._clean(item, 180) for item in candidates if self._clean(item, 180)]
        if not facets:
            facets = list(_DEFAULT_FACETS)
        deduped: list[str] = []
        for facet in facets:
            if facet.lower() not in {item.lower() for item in deduped}:
                deduped.append(facet)
        return deduped[: max(2, min(_MAX_FACETS, max_agents))]

    async def _branch(self, query: str, facet: str, index: int, semaphore: asyncio.Semaphore) -> dict[str, Any]:
        started = time.perf_counter()
        branch_query = f"{query}\nResearch angle: {facet}"
        async with semaphore:
            try:
                search = await self.browser.execute(query=branch_query)
                output = self._clean(search.get("output") if isinstance(search, dict) else search, _MAX_RESULT_CHARS)
                if output.lower().startswith("error:"):
                    return {"agent_id": f"research-{index + 1}", "facet": facet, "status": "failed", "error": output}

                prompt = (
                    "You are a source-specialist research sub-agent. Summarize only the evidence below. "
                    "Preserve source URLs exactly, distinguish facts from uncertainty, and do not invent citations. "
                    "Return concise markdown with: Findings, Sources, Caveats.\n\n"
                    f"Central question: {query}\nSpecialist angle: {facet}\nEvidence:\n{output}"
                )
                synthesis = await self.router.route(
                    "fallback",
                    [{"role": "user", "content": prompt}],
                    system_prompt="Be precise, source-grounded, and concise.",
                    temperature=0.1,
                    max_tokens=1200,
                )
                return {
                    "agent_id": f"research-{index + 1}",
                    "facet": facet,
                    "status": "completed",
                    "summary": self._clean(synthesis.content, _MAX_RESULT_CHARS),
                    "raw_evidence": output,
                    "evidence": [
                        {"source_url": url.rstrip(".,"), "facet": facet, "kind": "search_result"}
                        for url in _URL_PATTERN.findall(output)
                    ],
                    "provider": synthesis.provider,
                    "model": synthesis.model,
                    "latency_ms": round((time.perf_counter() - started) * 1000, 1),
                }
            except Exception as exc:
                logger.warning("wide_research_branch_failed", facet=facet, error=str(exc)[:240])
                return {
                    "agent_id": f"research-{index + 1}",
                    "facet": facet,
                    "status": "failed",
                    "error": self._clean(exc, 500),
                    "latency_ms": round((time.perf_counter() - started) * 1000, 1),
                }

    async def _bounded_branch(self, query: str, facet: str, index: int, semaphore: asyncio.Semaphore) -> dict[str, Any]:
        try:
            return await asyncio.wait_for(
                self._branch(query, facet, index, semaphore),
                timeout=_BRANCH_TIMEOUT_SECONDS,
            )
        except asyncio.TimeoutError:
            return {
                "agent_id": f"research-{index + 1}",
                "facet": facet,
                "status": "failed",
                "error": f"Research branch exceeded its {_BRANCH_TIMEOUT_SECONDS}-second budget.",
                "timed_out": True,
            }

    async def execute(self, query: str, facets: Any = None, max_agents: int = 4, **_: Any) -> dict[str, Any]:
        clean_query = self._clean(query, _MAX_QUERY_CHARS)
        if not clean_query:
            return {"output": "Error: Wide Research requires a non-empty research question.", "failure": {"code": "missing_query", "message": "Provide a research question."}}
        try:
            requested_agents = int(max_agents)
        except (TypeError, ValueError):
            requested_agents = 4
        selected = self._facets(facets, requested_agents)
        semaphore = asyncio.Semaphore(_MAX_CONCURRENCY)
        branches = await asyncio.gather(*(
            self._bounded_branch(clean_query, facet, index, semaphore)
            for index, facet in enumerate(selected)
        ))
        completed = sum(item.get("status") == "completed" for item in branches)
        payload = {
            "mode": "wide_research",
            "query": clean_query,
            "agent_count": len(branches),
            "completed_agents": completed,
            "failed_agents": len(branches) - completed,
            "timed_out_agents": sum(bool(item.get("timed_out")) for item in branches),
            "branches": branches,
            "next_action": "Synthesize across independent dossiers; cite URLs and report disagreements.",
            "evidence_count": sum(len(item.get("evidence", [])) for item in branches),
        }
        return {
            "output": json.dumps(payload, ensure_ascii=False),
            "wide_research": payload,
            "tool_data": {"wide_research": payload},
        }


class SpawnSubagentTool(BaseTool):
    """Run one bounded child CodeAct loop; children cannot spawn children."""

    def __init__(self, router: KimiDeepSeekRouter, registry: Any | None = None, sandbox_manager: Any | None = None):
        self.router = router
        self.registry = registry
        self.sandbox_manager = sandbox_manager
        super().__init__()

    def _build_schema(self) -> ToolSchema:
        return ToolSchema(
            name="spawn_subagent",
            description="Spawn a bounded sub-agent for an independent sub-goal. Maximum recursion depth is one.",
            parameters={
                "sub_goal": {"type": "string"},
                "tool_names": {"type": "array", "items": {"type": "string"}},
                "budget_usd": {"type": "number"},
                "budget_steps": {"type": "integer"},
                "subagent_depth": {"type": "integer"},
            },
            required=["sub_goal"], runtime="internal", idempotency="read_only", max_attempts=1,
            resources=["research_report"], operations=["create"], exactness="unknown",
        )

    async def execute(self, sub_goal: str, tool_names: Any = None, budget_usd: float = 0.50, budget_steps: int = 8, subagent_depth: int = 0, state: dict[str, Any] | None = None, **_: Any) -> dict[str, Any]:
        if int(subagent_depth or 0) >= 1:
            raise RuntimeError("spawn_subagent recursion limit reached (maximum depth is 1)")
        child_state = state if isinstance(state, dict) else {}
        runner = CodeActRunner(self.router, self.sandbox_manager, config=__import__("app.agents.codeact", fromlist=["CodeActConfig"]).CodeActConfig(max_iterations=max(1, min(8, int(budget_steps or 8))), max_output_chars=8000), tool_registry=self.registry)
        result = await runner.run(str(sub_goal), task_id=str(child_state.get("thread_id") or "subagent"), context={"tool_names": tool_names or [], "subagent_depth": 1})
        estimated_cost = min(max(float(budget_usd or 0.0), 0.0), max(0.01, 0.01 * len(result.get("trace") or [])))
        if isinstance(state, dict) and estimated_cost:
            record_code_execution_cost(state, "subagent", "codeact", {}, estimated_cost)
        return {"status": result.get("status"), "findings": result.get("answer", ""), "trace": result.get("trace", []), "artifacts": result.get("artifacts", []), "cost_usd": estimated_cost, "subagent_depth": 1}