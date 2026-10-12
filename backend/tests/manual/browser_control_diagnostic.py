# backend/test_browser_control.py

import asyncio
from app.tools.browser_control_tool import BrowserControlTool

async def test_browser_control():
    tool = BrowserControlTool()
    task_id = "test_task_1"
    
    # Test navigation
    result = await tool.execute(
        task_id=task_id,
        action="go_to_url",
        url="https://example.com"
    )
    print("Navigate:", result)
    
    # Test getting state
    result = await tool.execute(
        task_id=task_id,
        action="get_state"
    )
    print("State:", result["output"][:500])
    
    # Test extraction
    result = await tool.execute(
        task_id=task_id,
        action="extract_content"
    )
    print("Content:", result["output"][:500])
    
    # Close session
    result = await tool.execute(
        task_id=task_id,
        action="close"
    )
    print("Close:", result)

if __name__ == "__main__":
    asyncio.run(test_browser_control())