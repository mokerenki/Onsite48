# backend/app/sandbox/persistent_manager.py

import asyncio
import json
import os
import structlog
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, Optional, List
from pathlib import Path
import base64

from e2b_code_interpreter import Sandbox
from app.core.context import onsite48

logger = structlog.get_logger("onsite48.sandbox.persistent")

# Inactivity timeout: 30 minutes before sandbox goes to sleep
INACTIVITY_TIMEOUT_SECONDS = 30 * 60
# Max idle before full shutdown: 2 hours
MAX_IDLE_SECONDS = 2 * 60 * 60


class PersistentSandboxManager:
    """
    Manages persistent sandbox instances per user.
    Each user gets their own sandbox with persistent storage.
    """
    
    def __init__(self):
        self._sandboxes: Dict[str, Dict[str, Any]] = {}  # user_id -> sandbox data
        self._initialized = False
        self._cleanup_task: Optional[asyncio.Task] = None
        
    async def initialize(self):
        """Initialize the sandbox manager."""
        if self._initialized:
            return
        
        onsite48.ensure_initialized()
        self.redis = onsite48.redis_client
        self._initialized = True
        
        # Start cleanup task
        self._cleanup_task = asyncio.create_task(self._cleanup_loop())
        
        logger.info("persistent_sandbox.initialized")
    
    async def _cleanup_loop(self):
        """Background task to clean up stale sandboxes."""
        while True:
            try:
                await asyncio.sleep(60)  # Check every minute
                await self._cleanup_inactive_sandboxes()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.exception("sandbox.cleanup_loop_error", error=str(e))
    
    async def _cleanup_inactive_sandboxes(self):
        """Clean up sandboxes that have been inactive too long."""
        now = datetime.now(timezone.utc)
        
        for user_id, data in list(self._sandboxes.items()):
            last_used = data.get("last_used")
            if last_used:
                last_used_dt = datetime.fromisoformat(last_used)
                idle_seconds = (now - last_used_dt).total_seconds()
                
                # If idle for more than max, destroy
                if idle_seconds > MAX_IDLE_SECONDS:
                    logger.info("sandbox.auto_cleanup", user_id=user_id, idle_seconds=idle_seconds)
                    await self.destroy_sandbox(user_id)
    
    def _sandbox_key(self, user_id: str) -> str:
        return f"onsite48:sandbox:{user_id}"
    
    async def _get_sandbox_metadata(self, user_id: str) -> Optional[Dict[str, Any]]:
        """Get sandbox metadata from Redis."""
        key = self._sandbox_key(user_id)
        data = await self.redis.get(key)
        if data:
            return json.loads(data)
        return None
    
    async def _save_sandbox_metadata(self, user_id: str, metadata: Dict[str, Any]) -> None:
        """Save sandbox metadata to Redis."""
        key = self._sandbox_key(user_id)
        metadata["updated_at"] = datetime.now(timezone.utc).isoformat()
        await self.redis.setex(key, 86400 * 7, json.dumps(metadata))  # 7 days TTL
    
    async def get_or_create_sandbox(
        self,
        user_id: str,
        template: str = "code-interpreter-v1",
        timeout: int = 300
    ) -> Dict[str, Any]:
        """
        Get existing sandbox for user or create a new one.
        """
        await self.initialize()
        
        # Check if we already have a sandbox for this user
        if user_id in self._sandboxes:
            sandbox_data = self._sandboxes[user_id]
            sandbox = sandbox_data.get("sandbox")
            
            # Check if sandbox is still valid
            if sandbox:
                try:
                    # Test if sandbox is alive
                    sandbox.run_code("echo 'alive'", timeout=5)
                    
                    # Update last used time
                    sandbox_data["last_used"] = datetime.now(timezone.utc).isoformat()
                    self._sandboxes[user_id] = sandbox_data
                    
                    logger.info("sandbox.reused", user_id=user_id, sandbox_id=sandbox.sandbox_id)
                    return sandbox_data
                except Exception as e:
                    logger.warning("sandbox.stale", user_id=user_id, error=str(e))
                    # Sandbox is dead, recreate it
                    await self.destroy_sandbox(user_id)
        
        # Create new sandbox
        return await self._create_sandbox(user_id, template, timeout)
    
    async def _create_sandbox(
        self,
        user_id: str,
        template: str,
        timeout: int
    ) -> Dict[str, Any]:
        """Create a new sandbox with persistent storage."""
        # Check API key
        api_key = self._get_e2b_api_key()
        if not api_key:
            raise RuntimeError("E2B_API_KEY is not configured")
        
        os.environ["E2B_API_KEY"] = api_key
        
        try:
            # Create sandbox with user-specific metadata
            sandbox = Sandbox.create(
                template=template,
                timeout=timeout,
                metadata={
                    "user_id": user_id,
                    "created_at": datetime.now(timezone.utc).isoformat(),
                }
            )
            
            # Set up persistent directories
            await self._setup_persistent_directories(sandbox, user_id)
            
            sandbox_data = {
                "sandbox": sandbox,
                "sandbox_id": sandbox.sandbox_id,
                "user_id": user_id,
                "created_at": datetime.now(timezone.utc).isoformat(),
                "last_used": datetime.now(timezone.utc).isoformat(),
                "template": template,
            }
            
            self._sandboxes[user_id] = sandbox_data
            await self._save_sandbox_metadata(user_id, {
                "sandbox_id": sandbox.sandbox_id,
                "user_id": user_id,
                "created_at": sandbox_data["created_at"],
                "template": template,
            })
            
            logger.info("sandbox.created", user_id=user_id, sandbox_id=sandbox.sandbox_id)
            return sandbox_data
            
        except Exception as e:
            logger.exception("sandbox.create_failed", user_id=user_id, error=str(e))
            raise RuntimeError(f"Unable to create sandbox: {e}")
    
    async def _setup_persistent_directories(self, sandbox: Sandbox, user_id: str):
        """Set up persistent directories in the sandbox."""
        # Create standard directories
        directories = [
            "/workspace",
            "/workspace/projects",
            "/workspace/downloads",
            "/workspace/data",
            "/home/user",
            "/home/user/.config",
        ]
        
        for directory in directories:
            try:
                sandbox.run_code(f"mkdir -p {directory}", timeout=10)
            except Exception as e:
                logger.warning("sandbox.mkdir_failed", directory=directory, error=str(e))
        
        # Create a welcome file
        welcome_content = f"""# Welcome to Your Onsite48 Workspace

User: {user_id}
Created: {datetime.now(timezone.utc).isoformat()}

This is your persistent workspace. All files here will persist across tasks.

## Directory Structure
- /workspace/projects - Your project files
- /workspace/downloads - Downloaded files
- /workspace/data - Data files and databases
- /home/user - User configuration

## Quick Start
- Use the terminal to run commands
- Upload/download files via the UI
- Your browser sessions are persistent
"""
        
        try:
            sandbox.write_file("/workspace/README.md", welcome_content)
        except Exception as e:
            logger.warning("sandbox.welcome_write_failed", error=str(e))
    
    def _get_e2b_api_key(self) -> str:
        """Get E2B API key from environment."""
        raw_key = os.getenv("E2B_API_KEY", "").strip()
        if not raw_key:
            return ""
        raw_key = raw_key.strip('"').strip("'")
        if " " in raw_key:
            raw_key = raw_key.split()[0]
        return raw_key
    
    async def execute_in_sandbox(
        self,
        user_id: str,
        command: str,
        timeout: int = 30,
        max_output_chars: int = 12000
    ) -> Dict[str, Any]:
        """
        Execute a command in the user's persistent sandbox.
        """
        sandbox_data = await self.get_or_create_sandbox(user_id)
        sandbox = sandbox_data["sandbox"]
        
        try:
            # Execute with retry logic
            max_attempts = 2
            last_error = None
            
            for attempt in range(max_attempts):
                try:
                    execution = sandbox.run_code(
                        command,
                        timeout=timeout,
                        request_timeout=45,
                    )
                    break
                except Exception as e:
                    last_error = e
                    if attempt < max_attempts - 1:
                        await asyncio.sleep(1)
                        continue
                    raise
            
            # Parse output
            stdout = ""
            stderr = ""
            logs = getattr(execution, "logs", None)
            if logs is not None:
                stdout_value = getattr(logs, "stdout", "") or ""
                stderr_value = getattr(logs, "stderr", "") or ""
                if isinstance(stdout_value, list):
                    stdout = "".join(str(item) for item in stdout_value)
                else:
                    stdout = str(stdout_value)
                if isinstance(stderr_value, list):
                    stderr = "".join(str(item) for item in stderr_value)
                else:
                    stderr = str(stderr_value)
            
            output = (stdout or "") + (stderr or "")
            if len(output) > max_output_chars:
                output = output[:max_output_chars] + "\n...[output truncated]..."
            
            # Update last used time
            self._sandboxes[user_id]["last_used"] = datetime.now(timezone.utc).isoformat()
            
            return {
                "sandbox_id": sandbox.sandbox_id,
                "user_id": user_id,
                "output": output or (execution.text or ""),
                "stdout": stdout,
                "stderr": stderr,
                "exit_code": 0 if not execution.error else 1,
                "error": getattr(execution.error, "message", None) if execution.error else None,
                "status": "ok" if not execution.error else "error",
            }
            
        except Exception as e:
            logger.exception("sandbox.execute_failed", user_id=user_id, error=str(e))
            return {
                "sandbox_id": sandbox_data.get("sandbox_id", "unknown"),
                "user_id": user_id,
                "output": "",
                "stdout": "",
                "stderr": "",
                "exit_code": 1,
                "error": str(e),
                "status": "error",
            }
    
    # ─── File System Operations ────────────────────────────────────
    
    async def list_files(self, user_id: str, path: str = "/workspace") -> List[Dict[str, Any]]:
        """List files in the user's sandbox."""
        sandbox_data = await self.get_or_create_sandbox(user_id)
        sandbox = sandbox_data["sandbox"]
        
        try:
            # Use ls with JSON output for parsing
            result = sandbox.run_code(
                f"cd {path} && ls -la --time-style=iso 2>/dev/null || echo '[]'",
                timeout=10
            )
            
            output = result.text if result else ""
            
            # Parse ls output
            files = []
            for line in output.strip().split("\n"):
                if not line.strip() or line.startswith("total"):
                    continue
                
                parts = line.split()
                if len(parts) < 8:
                    continue
                
                # Parse permissions, owner, size, date, name
                permissions = parts[0]
                size = parts[4]
                date = f"{parts[5]} {parts[6]}"
                name = " ".join(parts[7:]) if len(parts) > 8 else parts[7]
                
                is_dir = permissions.startswith("d")
                
                files.append({
                    "name": name,
                    "path": f"{path}/{name}" if path != "/" else f"/{name}",
                    "is_directory": is_dir,
                    "size": int(size) if size.isdigit() else 0,
                    "permissions": permissions,
                    "modified_at": date,
                })
            
            return files
            
        except Exception as e:
            logger.exception("sandbox.list_files_failed", user_id=user_id, path=path, error=str(e))
            return []
    
    async def read_file(self, user_id: str, path: str) -> Dict[str, Any]:
        """Read a file from the user's sandbox."""
        sandbox_data = await self.get_or_create_sandbox(user_id)
        sandbox = sandbox_data["sandbox"]
        
        try:
            # Check if file exists
            check = sandbox.run_code(f"test -f {path} && echo 'exists' || echo 'not_found'", timeout=5)
            if "not_found" in (check.text or ""):
                return {"status": "error", "error": f"File not found: {path}"}
            
            # Read file
            result = sandbox.run_code(f"cat {path}", timeout=30)
            
            return {
                "status": "ok",
                "path": path,
                "content": result.text or "",
                "size": len(result.text or ""),
            }
            
        except Exception as e:
            logger.exception("sandbox.read_file_failed", user_id=user_id, path=path, error=str(e))
            return {"status": "error", "error": str(e)}
    
    async def write_file(self, user_id: str, path: str, content: str) -> Dict[str, Any]:
        """Write a file to the user's sandbox."""
        sandbox_data = await self.get_or_create_sandbox(user_id)
        sandbox = sandbox_data["sandbox"]
        
        try:
            # Create directory if needed
            dir_path = os.path.dirname(path)
            if dir_path and dir_path != "/":
                sandbox.run_code(f"mkdir -p {dir_path}", timeout=5)
            
            # Write file
            sandbox.write_file(path, content)
            
            logger.info("sandbox.file_written", user_id=user_id, path=path, size=len(content))
            return {
                "status": "ok",
                "path": path,
                "size": len(content),
            }
            
        except Exception as e:
            logger.exception("sandbox.write_file_failed", user_id=user_id, path=path, error=str(e))
            return {"status": "error", "error": str(e)}
    
    async def delete_file(self, user_id: str, path: str) -> Dict[str, Any]:
        """Delete a file from the user's sandbox."""
        sandbox_data = await self.get_or_create_sandbox(user_id)
        sandbox = sandbox_data["sandbox"]
        
        try:
            sandbox.run_code(f"rm -rf {path}", timeout=10)
            logger.info("sandbox.file_deleted", user_id=user_id, path=path)
            return {"status": "ok", "path": path}
            
        except Exception as e:
            logger.exception("sandbox.delete_file_failed", user_id=user_id, path=path, error=str(e))
            return {"status": "error", "error": str(e)}
    
    async def upload_file(self, user_id: str, path: str, content_bytes: bytes) -> Dict[str, Any]:
        """Upload binary content to the user's sandbox."""
        sandbox_data = await self.get_or_create_sandbox(user_id)
        sandbox = sandbox_data["sandbox"]
        
        try:
            # Create directory if needed
            dir_path = os.path.dirname(path)
            if dir_path and dir_path != "/":
                sandbox.run_code(f"mkdir -p {dir_path}", timeout=5)
            
            # Write binary file
            sandbox.write_file(path, content_bytes.decode('utf-8', errors='ignore'))
            
            logger.info("sandbox.file_uploaded", user_id=user_id, path=path, size=len(content_bytes))
            return {
                "status": "ok",
                "path": path,
                "size": len(content_bytes),
            }
            
        except Exception as e:
            logger.exception("sandbox.upload_file_failed", user_id=user_id, path=path, error=str(e))
            return {"status": "error", "error": str(e)}
    
    # ─── Sandbox Management ────────────────────────────────────────
    
    async def destroy_sandbox(self, user_id: str) -> bool:
        """Destroy a user's sandbox."""
        if user_id not in self._sandboxes:
            # Check Redis for metadata
            metadata = await self._get_sandbox_metadata(user_id)
            if metadata:
                # Try to destroy by sandbox_id
                sandbox_id = metadata.get("sandbox_id")
                if sandbox_id:
                    try:
                        # E2B sandbox can be killed by ID
                        sandbox = Sandbox.get(sandbox_id)
                        sandbox.kill()
                    except Exception as e:
                        logger.warning("sandbox.destroy_by_id_failed", sandbox_id=sandbox_id, error=str(e))
            
            # Clear from dict and Redis
            self._sandboxes.pop(user_id, None)
            await self.redis.delete(self._sandbox_key(user_id))
            return True
        
        sandbox_data = self._sandboxes.pop(user_id)
        sandbox = sandbox_data.get("sandbox")
        
        if sandbox:
            try:
                sandbox.kill()
            except Exception as e:
                logger.warning("sandbox.destroy_failed", user_id=user_id, error=str(e))
        
        await self.redis.delete(self._sandbox_key(user_id))
        logger.info("sandbox.destroyed", user_id=user_id)
        return True
    
    async def get_sandbox_status(self, user_id: str) -> Dict[str, Any]:
        """Get status of a user's sandbox."""
        if user_id in self._sandboxes:
            data = self._sandboxes[user_id]
            return {
                "exists": True,
                "sandbox_id": data.get("sandbox_id"),
                "created_at": data.get("created_at"),
                "last_used": data.get("last_used"),
                "status": "active",
            }
        
        metadata = await self._get_sandbox_metadata(user_id)
        if metadata:
            return {
                "exists": True,
                "sandbox_id": metadata.get("sandbox_id"),
                "created_at": metadata.get("created_at"),
                "last_used": metadata.get("updated_at"),
                "status": "inactive",
            }
        
        return {"exists": False}
    
    async def shutdown(self):
        """Shutdown all sandboxes."""
        if self._cleanup_task:
            self._cleanup_task.cancel()
            try:
                await self._cleanup_task
            except asyncio.CancelledError:
                pass
        
        for user_id in list(self._sandboxes.keys()):
            await self.destroy_sandbox(user_id)
        
        logger.info("persistent_sandbox.shutdown")


# ─── Global Instance ─────────────────────────────────────────────

persistent_sandbox_manager = PersistentSandboxManager()