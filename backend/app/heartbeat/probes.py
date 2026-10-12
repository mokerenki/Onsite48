import httpx
import os
import glob
import asyncio
import time
import psutil
import redis.asyncio as aioredis
from redis import exceptions as redis_exceptions
from typing import Any, Dict, List, Optional
import structlog
from datetime import datetime, timezone

logger = structlog.get_logger("aether.heartbeat.probes")

class BaseProbe:
    """Base class for all heartbeat probes."""
    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self.name = config.get("name", "unnamed_probe")

    async def check(self) -> Dict[str, Any]:
        """Performs the probe check and returns a structured result."""
        raise NotImplementedError("BaseProbe.check() must be implemented by subclasses")

class HTTPProbe(BaseProbe):
    """Checks URL status, response time, and optionally content match using httpx."""
    async def check(self ) -> Dict[str, Any]:
        url = self.config.get("url")
        expected_status = self.config.get("expected_status", 200)
        timeout = self.config.get("timeout", 10)
        method = self.config.get("method", "GET").upper()
        headers = self.config.get("headers", {})
        data = self.config.get("data")
        verify_ssl = self.config.get("verify_ssl", True)

        if not url:
            return {"status": "error", "message": "URL not configured", "probe_name": self.name}

        try:
            start_time = time.perf_counter()
            async with httpx.AsyncClient(verify=verify_ssl) as client:
                response = await client.request(method, url, headers=headers, json=data, timeout=timeout)
            end_time = time.perf_counter()
            latency_ms = (end_time - start_time) * 1000

            status_ok = response.status_code == expected_status
            result = {
                "status": "ok" if status_ok else "critical",
                "probe_name": self.name,
                "url": url,
                "method": method,
                "actual_status": response.status_code,
                "expected_status": expected_status,
                "latency_ms": round(latency_ms, 2),
                "message": f"HTTP {method} to {url} returned status {response.status_code}"
            }
            if not status_ok:
                result["error_details"] = response.text[:200]
            return result
        except httpx.TimeoutException:
            return {"status": "critical", "probe_name": self.name, "url": url, "message": f"HTTP {method} to {url} timed out after {timeout}s"}
        except httpx.RequestError as e:
            return {"status": "critical", "probe_name": self.name, "url": url, "message": f"HTTP {method} to {url} failed: {str(e)}"}
        except Exception as e:
            return {"status": "error", "probe_name": self.name, "url": url, "message": f"Unexpected error during HTTP probe: {str(e)}"}

class FileProbe(BaseProbe):
    _last_checked_files: Dict[str, Dict[str, float]] = {}

    async def check(self) -> Dict[str, Any]:
        path = self.config.get("path")
        check_type = self.config.get("check", "exists")
        threshold = self.config.get("threshold")

        if not path:
            return {"status": "error", "message": "Path not configured", "probe_name": self.name}

        if not os.path.exists(path):
            return {"status": "critical", "message": f"Path does not exist: {path}", "probe_name": self.name}

        try:
            if check_type == "exists":
                return {"status": "ok", "path": path, "message": f"Path exists: {path}", "probe_name": self.name}
            elif check_type == "size":
                if not os.path.isfile(path):
                    return {"status": "critical", "message": f"Path is not a file for size check: {path}", "probe_name": self.name}
                current_size = os.path.getsize(path)
                status = "ok" if threshold is None or current_size >= threshold else "critical"
                return {"status": status, "path": path, "size_bytes": current_size, "threshold_bytes": threshold, "message": f"File size: {current_size} bytes", "probe_name": self.name}
            elif check_type == "modified_age":
                if not os.path.isfile(path):
                    return {"status": "critical", "message": f"Path is not a file for modified_age check: {path}", "probe_name": self.name}
                if threshold is None:
                    return {"status": "error", "message": "Threshold (seconds) not configured for modified_age check", "probe_name": self.name}
                mod_time = os.path.getmtime(path)
                age_seconds = datetime.now(timezone.utc).timestamp() - mod_time
                status = "ok" if age_seconds <= threshold else "critical"
                return {"status": status, "path": path, "age_seconds": round(age_seconds, 2), "threshold_seconds": threshold, "message": f"File last modified {round(age_seconds, 2)} seconds ago", "probe_name": self.name}
            elif check_type == "new_files":
                if not os.path.isdir(path):
                    return {"status": "critical", "message": f"Path is not a directory for new_files check: {path}", "probe_name": self.name}
                current_files = {f: os.path.getmtime(os.path.join(path, f)) for f in os.listdir(path) if os.path.isfile(os.path.join(path, f))}
                last_state = FileProbe._last_checked_files.get(path, {})
                new_files = {f for f in current_files if f not in last_state or current_files[f] > last_state.get(f, 0)}
                FileProbe._last_checked_files[path] = current_files
                if len(new_files) > 0:
                    return {"status": "warn", "path": path, "new_files_count": len(new_files), "new_files": list(new_files), "message": f"{len(new_files)} new or modified files detected in {path}", "probe_name": self.name}
                else:
                    return {"status": "ok", "path": path, "new_files_count": 0, "message": f"No new or modified files in {path}", "probe_name": self.name}
            else:
                return {"status": "error", "message": f"Unsupported file check type: {check_type}", "probe_name": self.name}
        except Exception as e:
            return {"status": "error", "probe_name": self.name, "path": path, "message": f"Unexpected error during File probe: {str(e)}"}

class ProcessProbe(BaseProbe):
    async def check(self) -> Dict[str, Any]:
        process_name = self.config.get("process_name")
        pid = self.config.get("pid")

        if not process_name and not pid:
            return {"status": "error", "message": "Process name or PID not configured", "probe_name": self.name}

        is_running = False
        found_pids = []
        process_info = []

        for proc in psutil.process_iter(['pid', 'name', 'status', 'cpu_percent', 'memory_percent']):
            try:
                if (process_name and proc.info['name'] == process_name) or (pid and proc.info['pid'] == pid):
                    is_running = True
                    found_pids.append(proc.info['pid'])
                    process_info.append({
                        "pid": proc.info['pid'],
                        "name": proc.info['name'],
                        "status": proc.info['status'],
                        "cpu_percent": proc.cpu_percent(interval=0.1),
                        "memory_percent": proc.memory_percent()
                    })
            except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                continue
        status = "ok" if is_running else "critical"
        message = f"Process `{process_name or pid}` running: {is_running}. Found PIDs: {found_pids}"
        return {"status": status, "probe_name": self.name, "target": process_name or pid, "running": is_running, "found_pids": found_pids, "process_info": process_info, "message": message}

class QueueProbe(BaseProbe):
    """Checks Redis Stream/List depth using redis.asyncio."""
    async def check(self) -> Dict[str, Any]:
        queue_type = self.config.get("queue_type", "stream")
        queue_name = self.config.get("queue_name")
        threshold_critical = self.config.get("threshold_critical", 100)
        threshold_warn = self.config.get("threshold_warn", 80)
        redis_url = self.config.get("redis_url", "redis://localhost:6379")

        if not queue_name:
            return {"status": "error", "message": "Queue name not configured", "probe_name": self.name}

        try:
            redis = await aioredis.from_url(redis_url)
            depth = 0
            if queue_type == "stream":
                info = await redis.xinfo_stream(queue_name, count=1000)
                depth = info.get("length", 0)
            elif queue_type == "list":
                depth = await redis.llen(queue_name)
            else:
                return {"status": "error", "message": f"Unsupported queue type: {queue_type}", "probe_name": self.name}
            await redis.close()

            status = "ok"
            message = f"Queue `{queue_name}` depth: {depth}"
            if depth >= threshold_critical:
                status = "critical"
                message = f"Queue `{queue_name}` depth ({depth}) exceeds critical threshold ({threshold_critical})"
            elif depth >= threshold_warn:
                status = "warn"
                message = f"Queue `{queue_name}` depth ({depth}) exceeds warning threshold ({threshold_warn})"

            return {"status": status, "probe_name": self.name, "queue_name": queue_name, "depth": depth, "threshold_warn": threshold_warn, "threshold_critical": threshold_critical, "message": message}
        except redis_exceptions.RedisError as e:
            return {"status": "critical", "probe_name": self.name, "queue_name": queue_name, "message": f"Redis error during queue probe: {str(e)}"}
        except Exception as e:
            return {"status": "error", "probe_name": self.name, "queue_name": queue_name, "message": f"Unexpected error during Queue probe: {str(e)}"} 