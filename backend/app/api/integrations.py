import structlog
from fastapi import APIRouter, Depends, HTTPException
from typing import Dict, Any, List
from app.core import instances

logger = structlog.get_logger("aether.api.integrations")

router = APIRouter(prefix="/api/integrations", tags=["integrations"])

@router.get("/")
async def list_integrations() -> Dict[str, Any]:
    """List all MCP integrations and their status."""
    integrations = []
    mcp_registry = instances.mcp_registry
    if mcp_registry:
        for name, client in mcp_registry.clients.items():
            # Check if client is connected
            status = "connected" if client._session else "disconnected"
            integrations.append({
                "id": name,
                "name": name,
                "type": "mcp",
                "status": status,
                "config": {},
                "description": f"MCP server: {name}"
            })
    return {"integrations": integrations}

@router.post("/{integration_id}/sync")
async def sync_integration(integration_id: str) -> Dict[str, Any]:
    """Force sync tools from an MCP server."""
    mcp_registry = instances.mcp_registry
    if not mcp_registry:
        return {"error": "MCP registry not initialized"}
    client = mcp_registry.clients.get(integration_id)
    if not client:
        return {"error": "Integration not found"}
    try:
        await client.connect()
        tools = await client.list_tools()
        # Re-register tools (could be smarter)
        return {"status": "success", "tools_count": len(tools)}
    except Exception as e:
        return {"error": str(e)}

@router.get("/tools")
async def list_integration_tools() -> Dict[str, Any]:
    """List all available tools from all integrations with full schemas."""
    mcp_registry = instances.mcp_registry
    if not mcp_registry:
        return {"tools": [], "total": 0}
    
    all_tools = []
    for name, client in mcp_registry.clients.items():
        try:
            tools = await client.list_tools()
            for tool in tools:
                all_tools.append({
                    "id": f"{name}:{tool.name}",
                    "integration": name,
                    "name": tool.name,
                    "description": tool.description,
                    "input_schema": tool.inputSchema,
                    "status": "connected" if client._session else "disconnected",
                })
        except Exception as e:
            logger.error(f"Failed to list tools for {name}: {e}")
    
    return {
        "tools": all_tools,
        "total": len(all_tools),
        "integrations": list(mcp_registry.clients.keys())
    }

@router.get("/tools/{tool_id}")
async def get_tool_details(tool_id: str) -> Dict[str, Any]:
    """Get detailed information about a specific tool."""
    mcp_registry = instances.mcp_registry
    if not mcp_registry:
        raise HTTPException(404, "MCP registry not initialized")
    
    parts = tool_id.split(":", 1)
    if len(parts) != 2:
        raise HTTPException(400, "Invalid tool ID format. Expected 'integration:tool_name'")
    
    integration_name, tool_name = parts
    client = mcp_registry.clients.get(integration_name)
    if not client:
        raise HTTPException(404, f"Integration '{integration_name}' not found")
    
    try:
        tools = await client.list_tools()
        for tool in tools:
            if tool.name == tool_name:
                return {
                    "name": tool.name,
                    "integration": integration_name,
                    "description": tool.description,
                    "input_schema": tool.inputSchema,
                    "status": "connected" if client._session else "disconnected",
                }
        raise HTTPException(404, f"Tool '{tool_name}' not found in integration '{integration_name}'")
    except Exception as e:
        raise HTTPException(500, f"Failed to fetch tool details: {str(e)}")

# backend/app/api/integrations.py - Add test endpoint

@router.get("/test")
async def test_mcp_connection() -> Dict[str, Any]:
    """Test MCP connection and list available tools."""
    mcp_registry = instances.mcp_registry
    if not mcp_registry:
        return {"error": "MCP registry not initialized"}
    
    status = {
        "clients": list(mcp_registry.clients.keys()),
        "client_status": {},
        "tools": []
    }
    
    for name, client in mcp_registry.clients.items():
        connected = client._connected
        status["client_status"][name] = {
            "connected": connected,
            "url": client.url,
        }
        if connected:
            try:
                tools = await client.list_tools()
                status["client_status"][name]["tools_count"] = len(tools)
                status["client_status"][name]["tools"] = [
                    {"name": t.name, "description": t.description}
                    for t in tools[:5]  # Show first 5 tools
                ]
            except Exception as e:
                status["client_status"][name]["error"] = str(e)
    
    # Get all registered tools from tool registry
    tool_registry = instances.tool_registry
    if tool_registry:
        status["all_tools"] = tool_registry.list_tools()
    
    return status