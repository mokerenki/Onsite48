import ast
import io
import sys
import traceback
import structlog
import multiprocessing
from typing import Dict, Any, Optional

logger = structlog.get_logger("aether.tools.python_repl_secure")


class SecurePythonREPL:
    """
    A secure Python REPL tool that executes code in a sandboxed environment.
    
    Features:
    - AST-based static analysis to block dangerous operations (imports, file I/O, eval).
    - Execution timeout to prevent infinite loops.
    - Output capture and truncation to prevent memory exhaustion.
    - Restricted built-ins.
    """

    def __init__(self, timeout_seconds: int = 10, max_output_length: int = 5000):
        """
        Initialize the secure REPL.
        
        Args:
            timeout_seconds: Maximum execution time in seconds.
            max_output_length: Maximum length of captured output.
        """
        self.timeout_seconds = timeout_seconds
        self.max_output_length = max_output_length
        self.history = []
        logger.info("python_repl_secure.initialized", timeout=timeout_seconds)

    def _is_safe_code(self, code: str) -> bool:
        """
        Perform static analysis to ensure the code doesn't contain dangerous operations.
        """
        try:
            tree = ast.parse(code)
        except SyntaxError as e:
            logger.warning("python_repl_secure.syntax_error", error=str(e))
            return False

        for node in ast.walk(tree):
            # Block imports
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                logger.warning("python_repl_secure.blocked_import", node=ast.dump(node))
                return False
            
            # Block dangerous function calls
            if isinstance(node, ast.Call):
                if isinstance(node.func, ast.Name):
                    if node.func.id in ["eval", "exec", "open", "compile", "__import__", "input"]:
                        logger.warning("python_repl_secure.blocked_function", func=node.func.id)
                        return False
                elif isinstance(node.func, ast.Attribute):
                    # Block os.system, subprocess.run, etc. (though imports are blocked, this is defense in depth)
                    if node.func.attr in ["system", "popen", "run", "call"]:
                        logger.warning("python_repl_secure.blocked_attribute", attr=node.func.attr)
                        return False

        return True

    def _execute_in_process(self, code: str, result_queue: multiprocessing.Queue):
        """
        The actual execution function that runs in a separate process.
        """
        # Restrict built-ins
        safe_builtins = {
            "print": print, "len": len, "range": range, "str": str, "int": int,
            "float": float, "bool": bool, "list": list, "dict": dict, "set": set,
            "tuple": tuple, "sum": sum, "min": min, "max": max, "abs": abs,
            "round": round, "sorted": sorted, "enumerate": enumerate, "zip": zip,
            "map": map, "filter": filter, "any": any, "all": all, "isinstance": isinstance,
            "type": type, "Exception": Exception, "ValueError": ValueError,
            "TypeError": TypeError, "KeyError": KeyError, "IndexError": IndexError
        }
        
        global_env = {"__builtins__": safe_builtins}
        local_env = {}

        # Capture stdout and stderr
        stdout_capture = io.StringIO()
        stderr_capture = io.StringIO()
        
        original_stdout = sys.stdout
        original_stderr = sys.stderr
        
        sys.stdout = stdout_capture
        sys.stderr = stderr_capture

        try:
            exec(code, global_env, local_env)
            output = stdout_capture.getvalue()
            error = stderr_capture.getvalue()
            
            result_queue.put({
                "status": "success",
                "output": output,
                "error": error,
                "locals": {k: str(v) for k, v in local_env.items() if not k.startswith("__")}
            })
        except Exception as e:
            error_msg = traceback.format_exc()
            result_queue.put({
                "status": "error",
                "error": error_msg
            })
        finally:
            # Restore stdout/stderr
            sys.stdout = original_stdout
            sys.stderr = original_stderr

    def execute(self, code: str) -> Dict[str, Any]:
        """
        Execute Python code securely.
        
        Args:
            code: The Python code to execute.
            
        Returns:
            Dictionary containing execution status, output, and errors.
        """
        logger.info("python_repl_secure.executing", code_length=len(code))
        
        if not self._is_safe_code(code):
            return {
                "status": "error",
                "error": "Code execution blocked: Contains unsafe operations (e.g., imports, file I/O, eval)."
            }

        result_queue = multiprocessing.Queue()
        process = multiprocessing.Process(target=self._execute_in_process, args=(code, result_queue))
        
        process.start()
        process.join(timeout=self.timeout_seconds)

        if process.is_alive():
            process.terminate()
            process.join()
            logger.warning("python_repl_secure.timeout", timeout=self.timeout_seconds)
            return {
                "status": "error",
                "error": f"Execution timed out after {self.timeout_seconds} seconds."
            }

        if not result_queue.empty():
            result = result_queue.get()
            
            # Truncate output if necessary
            if "output" in result and len(result["output"]) > self.max_output_length:
                result["output"] = result["output"][:self.max_output_length] + "\n...[Output truncated]..."
                
            self.history.append({"code": code, "result": result})
            return result
        else:
            return {
                "status": "error",
                "error": "Process terminated unexpectedly without returning a result."
            }

    def get_history(self) -> list:
        """Return the execution history."""
        return self.history