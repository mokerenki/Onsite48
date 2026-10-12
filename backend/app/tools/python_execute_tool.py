# backend/app/tools/python_execute_tool.py

from app.tools.registry import BaseTool, ToolSchema
from app.tools.chart_visualization.python_execute import PythonExecute


class PythonExecuteTool(BaseTool):
    """
    Execute Python code safely.
    Useful for data processing, calculations, and analysis.
    """
    
    def _build_schema(self) -> ToolSchema:
        return ToolSchema(
            name="python_execute",
            description=(
                "Execute Python code for data processing, calculations, "
                "and analysis. Safe sandboxed execution with output capture."
            ),
            parameters={
                "code": {
                    "type": "string",
                    "description": "Python code to execute",
                },
                "timeout": {
                    "type": "integer",
                    "description": "Timeout in seconds",
                    "default": 30,
                },
            },
            required=["code"],
        )
    
    async def execute(self, **kwargs) -> dict:
        code = kwargs.get("code", "")
        timeout = kwargs.get("timeout", 30)
        
        if not code:
            return {"output": "Error: No code provided."}
        
        try:
            result = await PythonExecute.execute(code, timeout=timeout)
            
            if result.get("success"):
                output = result.get("output", "")
                if result.get("return_value"):
                    output += f"\nReturn: {result['return_value']}"
                return {"output": output or "Code executed successfully."}
            else:
                return {"output": f"Code execution failed: {result.get('error')}"}
                
        except Exception as e:
            return {"output": f"Execution error: {str(e)}"}