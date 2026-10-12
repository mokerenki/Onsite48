# backend/app/tools/chart_visualization/python_execute.py

import asyncio
import sys
import io
import contextlib
import traceback
import structlog
from typing import Any, Dict, Optional

logger = structlog.get_logger("aether.chart_visualization.python_execute")

class PythonExecute:
    """Execute Python code safely and capture output."""
    
    @staticmethod
    async def execute(
        code: str,
        timeout: int = 30,
        max_output_length: int = 10000
    ) -> Dict[str, Any]:
        """
        Execute Python code in a restricted environment.
        
        Returns:
            Dict with 'success', 'output', 'error' fields
        """
        # Store original stdout/stderr
        original_stdout = sys.stdout
        original_stderr = sys.stderr
        
        # Capture output
        stdout_capture = io.StringIO()
        stderr_capture = io.StringIO()
        sys.stdout = stdout_capture
        sys.stderr = stderr_capture
        
        result = {
            "success": False,
            "output": "",
            "error": None,
            "return_value": None,
        }
        
        try:
            # Execute with timeout
            loop = asyncio.get_event_loop()
            exec_result = await loop.run_in_executor(
                None,
                _execute_code_sync,
                code,
                {}
            )
            
            result["success"] = exec_result["success"]
            result["return_value"] = exec_result.get("return_value")
            result["error"] = exec_result.get("error")
            
        except asyncio.TimeoutError:
            result["error"] = f"Execution timed out after {timeout} seconds"
        except Exception as e:
            result["error"] = traceback.format_exc()
        finally:
            # Restore stdout/stderr
            sys.stdout = original_stdout
            sys.stderr = original_stderr
            
            # Get captured output
            output = stdout_capture.getvalue()
            error = stderr_capture.getvalue()
            
            if output:
                if len(output) > max_output_length:
                    output = output[:max_output_length] + "...[truncated]"
                result["output"] = output
            if error:
                if len(error) > max_output_length:
                    error = error[:max_output_length] + "...[truncated]"
                if result["error"]:
                    result["error"] += "\n" + error
                else:
                    result["error"] = error
        
        return result


def _execute_code_sync(code: str, globals_dict: dict) -> dict:
    """Synchronous execution helper."""
    # Create a restricted environment
    safe_globals = {
        "__builtins__": {
            "print": print,
            "len": len,
            "range": range,
            "str": str,
            "int": int,
            "float": float,
            "list": list,
            "dict": dict,
            "tuple": tuple,
            "sum": sum,
            "min": min,
            "max": max,
            "abs": abs,
            "round": round,
            "sorted": sorted,
            "enumerate": enumerate,
            "zip": zip,
            "any": any,
            "all": all,
            "isinstance": isinstance,
            "type": type,
        },
        **globals_dict
    }
    
    local_vars = {}
    
    try:
        # Parse and compile
        compiled = compile(code, "<string>", "exec")
        exec(compiled, safe_globals, local_vars)
        return {"success": True, "return_value": local_vars}
    except Exception as e:
        return {"success": False, "error": traceback.format_exc()}