# backend/app/mcp_clients/mcp_tool_wrapper.py

from app.tools.registry import BaseTool, ToolSchema
from app.mcp_clients.base_mcp_client import MCPClient
from typing import Dict, Any
import structlog
import json

logger = structlog.get_logger("aether.mcp.wrapper")


class MCPToolWrapper(BaseTool):
    """Wrapper that makes any MCP tool callable via our ToolRegistry with validation."""

    def __init__(self, mcp_client: MCPClient, mcp_tool_name: str, description: str, input_schema: Dict[str, Any]):
        self._client = mcp_client
        self._mcp_tool_name = mcp_tool_name
        self._description = description
        self._input_schema = input_schema
        self.schema = self._build_schema()
        self._last_call_args: Dict[str, Any] = {}
        logger.debug("mcp_tool_wrapper.created", 
                     tool_name=mcp_tool_name, 
                     client=mcp_client.name)

    def _build_schema(self) -> ToolSchema:
        """Build the tool schema from MCP input schema."""
        parameters = self._input_schema.get("properties", {})
        required = self._input_schema.get("required", [])
        
        # Clean up parameters - remove any internal fields
        clean_params = {}
        for key, value in parameters.items():
            if key not in ["$schema", "$id"]:
                clean_params[key] = value
        
        return ToolSchema(
            name=self._mcp_tool_name,
            description=self._description,
            parameters=clean_params,
            required=required,
            # MCP tools are external and should be treated as irreversible by default
            irreversible=True,
            approval_required=True,
        )

    async def execute(self, **kwargs) -> Any:
        """Call the remote MCP tool with validation."""
        logger.info("mcp_tool_wrapper.executing", 
                    tool=self._mcp_tool_name, 
                    client=self._client.name,
                    args=kwargs)
        
        # ─── VALIDATION ─────────────────────────────────────────────
        # Validate against schema before calling
        try:
            validated_args = self.validate(kwargs)
        except (ValueError, TypeError) as e:
            logger.error("mcp_tool_wrapper.validation_failed", 
                         tool=self._mcp_tool_name, 
                         error=str(e))
            return {"output": f"Error: {str(e)}"}
        
        self._last_call_args = validated_args
        
        try:
            result = await self._client.call_tool(self._mcp_tool_name, validated_args)
            
            # Extract textual content from result
            if hasattr(result, 'content') and result.content:
                texts = []
                for item in result.content:
                    if hasattr(item, 'text'):
                        texts.append(item.text)
                    elif hasattr(item, 'data'):
                        texts.append(f"[Binary data: {len(item.data)} bytes]")
                if texts:
                    return {"output": "\n".join(texts)}
                return {"output": str(result.content)}
            
            return {"output": str(result)}
            
        except Exception as e:
            logger.error("mcp_tool_wrapper.execution_failed", 
                         tool=self._mcp_tool_name, 
                         client=self._client.name,
                         error=str(e))
            raise