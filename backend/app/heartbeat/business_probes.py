
import structlog
from typing import Dict, Any
from app.heartbeat.probes import BaseProbe
from app.tools.registry import ToolRegistry

logger = structlog.get_logger("aether.heartbeat.business_probes")

class SalesPipelineProbe(BaseProbe):
    """Monitor sales pipeline using Salesforce MCP tool."""

    def __init__(self, config: Dict[str, Any], tool_registry: ToolRegistry):
        super().__init__(config)
        self.tool_registry = tool_registry
        self.threshold_pipeline = config.get("threshold_pipeline", 1000000)
        self.threshold_win_rate = config.get("threshold_win_rate", 0.3)

    async def check(self) -> Dict[str, Any]:
        try:
            # Get the Salesforce query tool from registry (assuming MCP tool name is "query")
            sf_query = self.tool_registry.get("query")  # or "salesforce_query"
            if not sf_query:
                return {"status": "error", "probe_name": self.name, "message": "Salesforce tool not found"}

            result = await sf_query.execute(
                soql="SELECT Id, Name, Amount, StageName, CloseDate FROM Opportunity WHERE IsClosed = False"
            )
            records = result.get("records", [])
            total_pipeline = sum(o.get("Amount", 0) for o in records)
            won_count = len([o for o in records if o.get("StageName") == "Closed Won"])
            total_count = len(records) if records else 1
            win_rate = won_count / total_count

            status = "ok"
            message = f"Pipeline: ${total_pipeline:,.2f}, Win Rate: {win_rate:.1%}"
            if total_pipeline < self.threshold_pipeline:
                status = "warn"
                message = f"Pipeline below threshold: ${total_pipeline:,.2f} < ${self.threshold_pipeline:,.2f}"
            if win_rate < self.threshold_win_rate:
                status = "critical"
                message = f"Win rate below threshold: {win_rate:.1%} < {self.threshold_win_rate:.1%}"

            return {
                "status": status,
                "probe_name": self.name,
                "total_pipeline": total_pipeline,
                "win_rate": win_rate,
                "opportunity_count": len(records),
                "message": message
            }
        except Exception as e:
            logger.error("sales_pipeline_probe.failed", error=str(e))
            return {"status": "error", "probe_name": self.name, "message": str(e)}

class MarketingEngagementProbe(BaseProbe):
    """Monitor marketing engagement using HubSpot MCP tool."""

    def __init__(self, config: Dict[str, Any], tool_registry: ToolRegistry):
        super().__init__(config)
        self.tool_registry = tool_registry
        self.threshold_engagement = config.get("threshold_engagement", 0.05)

    async def check(self) -> Dict[str, Any]:
        try:
            # Assuming we have a HubSpot tool named "search_contacts" or similar
            hs_search = self.tool_registry.get("search_contacts")
            if not hs_search:
                return {"status": "error", "probe_name": self.name, "message": "HubSpot tool not found"}

            result = await hs_search.execute(limit=100)
            contacts = result.get("contacts", [])
            total = len(contacts) if contacts else 1
            engaged = sum(1 for c in contacts if c.get("properties", {}).get("hs_lead_status") == "OPEN")
            engagement_rate = engaged / total

            status = "ok"
            message = f"Engagement Rate: {engagement_rate:.1%}"
            if engagement_rate < self.threshold_engagement:
                status = "warn"
                message = f"Engagement below threshold: {engagement_rate:.1%} < {self.threshold_engagement:.1%}"

            return {
                "status": status,
                "probe_name": self.name,
                "engagement_rate": engagement_rate,
                "total_contacts": total,
                "engaged_contacts": engaged,
                "message": message
            }
        except Exception as e:
            logger.error("marketing_engagement_probe.failed", error=str(e))
            return {"status": "error", "probe_name": self.name, "message": str(e)}