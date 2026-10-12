# test_mcp.py - Run this to test your MCP profile

import asyncio
import httpx
from app.mcp_clients.mcp_registry import MCPRegistry
from app.tools.registry import ToolRegistry

async def test_mcp_profile():
    print("Testing MCP Profile Connection...")
    print("=" * 50)
    
    # Create registry
    registry = MCPRegistry()
    await registry.initialize()
    
    print(f"\nDiscovered {len(registry.clients)} MCP clients:")
    for name in registry.clients.keys():
        print(f"  - {name}")
    
    print("\nConnecting to clients and fetching tools...")
    
    tool_registry = ToolRegistry()
    total_tools = await registry.register_all_tools(tool_registry)
    
    print(f"\nRegistered {total_tools} MCP tools")
    print("\nAvailable tools:")
    for tool_name in tool_registry.list_tools():
        tool = tool_registry.get(tool_name)
        if tool:
            print(f"  - {tool_name}: {tool.schema.description[:50]}...")
    
    # Test a specific tool if available
    print("\n" + "=" * 50)
    print("Testing a tool call...")
    
    # Try filesystem list directory
    list_tool = tool_registry.get("filesystem_list_directory")
    if list_tool:
        try:
            result = await list_tool.execute(path="/workspace")
            print("Filesystem list directory result:")
            print(result.get("output", "No output")[:200])
        except Exception as e:
            print(f"Tool test failed: {e}")
    else:
        print("filesystem_list_directory not found - try a different tool")
    
    await registry.close_all()
    print("\nTest complete!")

if __name__ == "__main__":
    asyncio.run(test_mcp_profile())