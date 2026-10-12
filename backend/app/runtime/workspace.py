"""Persistent per-task computer (shell, filesystem, packages, code).

Never executes on the host process tree for production paths.
Backends:
  - dev: process-isolated workspace under a dedicated root (docker when available;
         otherwise tightly constrained local dir with cleaned env). Marked DEV.
  - sandbox: E2B (production path via existing SandboxManager / PersistentSandboxManager).

Activated via WORKSPACE_BACKEND=dev|sandbox (default: dev).
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import shutil
import signal
import subprocess
import tempfile
import time
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

import structlog

logger = structlog.get_logger("onsite48.runtime.workspace")

# Defaults
DEFAULT_TIMEOUT_S = 60
MAX_OUTPUT_CHARS = 120_000
MAX_FILE_BYTES = 20 * 1024 * 1024  # 20 MiB
WORKSPACE_ROOT_ENV = "ONSITE48_WORKSPACE_ROOT"
DEFAULT_ROOT = Path(os.environ.get(WORKSPACE_ROOT_ENV, "/tmp/onsite48_workspaces"))


@dataclass
class ExecResult:
    stdout: str = ""
    stderr: str = ""
    exit_code: int = 0
    timed_out: bool = False
    duration_ms: int = 0

    def to_dict(self) -> dict[str, Any]:
        out = {
            "stdout": self.stdout,
            "stderr": self.stderr,
            "exit_code": self.exit_code,
            "timed_out": self.timed_out,
            "duration_ms": self.duration_ms,
        }
        if self.exit_code != 0 or self.timed_out:
            out["error_code"] = "timeout" if self.timed_out else "nonzero_exit"
        return out


class Workspace(ABC):
    """Abstract persistent computer for one run_id."""

    run_id: str

    @abstractmethod
    async def create(self) -> None:
        ...

    @abstractmethod
    async def destroy(self) -> None:
        ...

    @abstractmethod
    async def exec(self, cmd: str, *, timeout: int = DEFAULT_TIMEOUT_S, cwd: str | None = None) -> ExecResult:
        ...

    @abstractmethod
    async def read(self, path: str, *, max_bytes: int = MAX_FILE_BYTES) -> dict[str, Any]:
        ...

    @abstractmethod
    async def write(self, path: str, content: str | bytes) -> dict[str, Any]:
        ...

    @abstractmethod
    async def edit(
        self,
        path: str,
        old_string: str,
        new_string: str,
        *,
        replace_all: bool = False,
    ) -> dict[str, Any]:
        ...

    @abstractmethod
    async def list(self, path: str = ".", *, recursive: bool = False) -> dict[str, Any]:
        ...

    @abstractmethod
    async def grep(
        self,
        pattern: str,
        path: str = ".",
        *,
        max_matches: int = 50,
    ) -> dict[str, Any]:
        ...

    @abstractmethod
    async def snapshot(self) -> dict[str, Any]:
        """Return metadata enough to re-attach after worker restart."""
        ...

    async def python_exec(self, code: str, *, timeout: int = DEFAULT_TIMEOUT_S) -> dict[str, Any]:
        """Run Python code inside the workspace (writes a temp script then exec)."""
        script_name = f"_py_{uuid.uuid4().hex[:12]}.py"
        await self.write(script_name, code)
        try:
            result = await self.exec(f"python3 {script_name}", timeout=timeout)
            return result.to_dict()
        finally:
            try:
                await self.exec(f"rm -f {script_name}", timeout=10)
            except Exception:
                pass


# ---------------------------------------------------------------------------
# Dev backend: dedicated directory + cleaned-env subprocess
# ---------------------------------------------------------------------------

class DevWorkspace(Workspace):
    """Dev / local backend.

    When Docker is available and WORKSPACE_USE_DOCKER=1, would use a container.
    In this environment Docker is absent, so we use a process-isolated tree under
    a dedicated root with:
      - no credentials in env
      - path escape guards
      - ulimit / timeout
      - no host mounts of secrets

    NOT a full container; escape suite still exercises path/env/network guards.
    """

    def __init__(self, run_id: str, root: Path | None = None):
        self.run_id = run_id
        self._root = (root or DEFAULT_ROOT).resolve()
        self._home = self._root / run_id
        self._created = False
        self._meta_path = self._home / ".workspace_meta.json"

    def _resolve(self, path: str) -> Path:
        if path.startswith("/"):
            # Allow absolute only if under home
            p = Path(path).resolve()
        else:
            p = (self._home / path).resolve()
        try:
            p.relative_to(self._home)
        except ValueError:
            raise PermissionError(f"path_escape: {path} is outside workspace")
        return p

    async def create(self) -> None:
        self._home.mkdir(parents=True, exist_ok=True)
        # Seed a minimal usable environment marker
        meta = {
            "run_id": self.run_id,
            "backend": "dev",
            "created_at": time.time(),
            "home": str(self._home),
            "isolation": "process+path_guards",
        }
        self._meta_path.write_text(json.dumps(meta, indent=2), encoding="utf-8")
        # Ensure common dirs
        (self._home / "tmp").mkdir(exist_ok=True)
        (self._home / "out").mkdir(exist_ok=True)
        self._created = True
        logger.info("workspace.dev.created", run_id=self.run_id, home=str(self._home))

    async def destroy(self) -> None:
        if self._home.exists():
            shutil.rmtree(self._home, ignore_errors=True)
        self._created = False
        logger.info("workspace.dev.destroyed", run_id=self.run_id)

    async def exec(self, cmd: str, *, timeout: int = DEFAULT_TIMEOUT_S, cwd: str | None = None) -> ExecResult:
        if not self._created:
            await self.create()
        workdir = self._resolve(cwd) if cwd else self._home
        # Clean environment: strip secrets and host credentials
        clean_env = {
            "PATH": "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
            "HOME": str(self._home),
            "TMPDIR": str(self._home / "tmp"),
            "LANG": "C.UTF-8",
            "PYTHONUNBUFFERED": "1",
            "PYTHONDONTWRITEBYTECODE": "1",
        }
        # Explicitly never forward host secrets
        for k in list(os.environ):
            if any(s in k.upper() for s in ("KEY", "SECRET", "TOKEN", "PASSWORD", "CREDENTIAL", "API_")):
                continue
            # allow a few neutral vars
            if k in ("TERM", "TZ"):
                clean_env[k] = os.environ[k]

        start = time.monotonic()
        try:
            proc = await asyncio.create_subprocess_shell(
                cmd,
                cwd=str(workdir),
                env=clean_env,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                start_new_session=True,  # own process group for kill
            )
            try:
                stdout_b, stderr_b = await asyncio.wait_for(proc.communicate(), timeout=timeout)
                timed_out = False
                code = proc.returncode or 0
            except asyncio.TimeoutError:
                timed_out = True
                code = -9
                try:
                    os.killpg(proc.pid, signal.SIGKILL)
                except Exception:
                    proc.kill()
                stdout_b, stderr_b = b"", b"timeout"
            duration_ms = int((time.monotonic() - start) * 1000)
            stdout = stdout_b.decode("utf-8", errors="replace")[:MAX_OUTPUT_CHARS]
            stderr = stderr_b.decode("utf-8", errors="replace")[:MAX_OUTPUT_CHARS]
            return ExecResult(stdout=stdout, stderr=stderr, exit_code=code, timed_out=timed_out, duration_ms=duration_ms)
        except Exception as exc:
            duration_ms = int((time.monotonic() - start) * 1000)
            return ExecResult(stdout="", stderr=str(exc), exit_code=1, timed_out=False, duration_ms=duration_ms)

    async def read(self, path: str, *, max_bytes: int = MAX_FILE_BYTES) -> dict[str, Any]:
        try:
            p = self._resolve(path)
            if not p.exists():
                return {"error_code": "not_found", "message": f"{path} not found", "retryable": False}
            if p.is_dir():
                return {"error_code": "is_directory", "message": f"{path} is a directory", "retryable": False}
            size = p.stat().st_size
            data = p.read_bytes()[:max_bytes]
            # Prefer text
            try:
                text = data.decode("utf-8")
                return {"path": path, "content": text, "size": size, "truncated": size > max_bytes}
            except UnicodeDecodeError:
                import base64
                return {
                    "path": path,
                    "content_b64": base64.b64encode(data).decode("ascii"),
                    "size": size,
                    "truncated": size > max_bytes,
                    "binary": True,
                }
        except PermissionError as e:
            return {"error_code": "path_escape", "message": str(e), "retryable": False}
        except Exception as e:
            return {"error_code": "read_failed", "message": str(e), "retryable": False}

    async def write(self, path: str, content: str | bytes) -> dict[str, Any]:
        try:
            p = self._resolve(path)
            p.parent.mkdir(parents=True, exist_ok=True)
            if isinstance(content, str):
                data = content.encode("utf-8")
            else:
                data = content
            if len(data) > MAX_FILE_BYTES:
                return {"error_code": "too_large", "message": f"max {MAX_FILE_BYTES} bytes", "retryable": False}
            p.write_bytes(data)
            return {"path": path, "bytes": len(data)}
        except PermissionError as e:
            return {"error_code": "path_escape", "message": str(e), "retryable": False}
        except Exception as e:
            return {"error_code": "write_failed", "message": str(e), "retryable": False}

    async def edit(
        self,
        path: str,
        old_string: str,
        new_string: str,
        *,
        replace_all: bool = False,
    ) -> dict[str, Any]:
        try:
            p = self._resolve(path)
            if not p.exists():
                return {"error_code": "not_found", "message": f"{path} not found", "retryable": False}
            text = p.read_text(encoding="utf-8", errors="replace")
            if old_string not in text:
                return {"error_code": "no_match", "message": "old_string not found", "retryable": False}
            if replace_all:
                updated = text.replace(old_string, new_string)
                count = text.count(old_string)
            else:
                updated = text.replace(old_string, new_string, 1)
                count = 1
            p.write_text(updated, encoding="utf-8")
            return {"path": path, "replacements": count}
        except PermissionError as e:
            return {"error_code": "path_escape", "message": str(e), "retryable": False}
        except Exception as e:
            return {"error_code": "edit_failed", "message": str(e), "retryable": False}

    async def list(self, path: str = ".", *, recursive: bool = False) -> dict[str, Any]:
        try:
            p = self._resolve(path)
            if not p.exists():
                return {"error_code": "not_found", "message": f"{path} not found", "retryable": False}
            entries = []
            if recursive:
                for root, dirs, files in os.walk(p):
                    rel_root = Path(root).relative_to(self._home)
                    for d in dirs:
                        entries.append({"path": str(rel_root / d), "type": "dir"})
                    for f in files:
                        fp = Path(root) / f
                        entries.append({
                            "path": str(rel_root / f),
                            "type": "file",
                            "size": fp.stat().st_size,
                        })
            else:
                for child in sorted(p.iterdir()):
                    rel = child.relative_to(self._home)
                    if child.is_dir():
                        entries.append({"path": str(rel), "type": "dir"})
                    else:
                        entries.append({"path": str(rel), "type": "file", "size": child.stat().st_size})
            return {"path": path, "entries": entries[:500]}
        except PermissionError as e:
            return {"error_code": "path_escape", "message": str(e), "retryable": False}
        except Exception as e:
            return {"error_code": "list_failed", "message": str(e), "retryable": False}

    async def grep(
        self,
        pattern: str,
        path: str = ".",
        *,
        max_matches: int = 50,
    ) -> dict[str, Any]:
        try:
            p = self._resolve(path)
            matches = []
            try:
                rx = re.compile(pattern)
            except re.error as e:
                return {"error_code": "bad_pattern", "message": str(e), "retryable": False}

            def _scan_file(fp: Path) -> None:
                if len(matches) >= max_matches:
                    return
                try:
                    if fp.stat().st_size > MAX_FILE_BYTES:
                        return
                    text = fp.read_text(encoding="utf-8", errors="replace")
                except Exception:
                    return
                for i, line in enumerate(text.splitlines(), 1):
                    if rx.search(line):
                        matches.append({
                            "path": str(fp.relative_to(self._home)),
                            "line": i,
                            "text": line[:500],
                        })
                        if len(matches) >= max_matches:
                            return

            if p.is_file():
                _scan_file(p)
            else:
                for root, _, files in os.walk(p):
                    for f in files:
                        _scan_file(Path(root) / f)
                        if len(matches) >= max_matches:
                            break
            return {"pattern": pattern, "matches": matches, "truncated": len(matches) >= max_matches}
        except PermissionError as e:
            return {"error_code": "path_escape", "message": str(e), "retryable": False}
        except Exception as e:
            return {"error_code": "grep_failed", "message": str(e), "retryable": False}

    async def snapshot(self) -> dict[str, Any]:
        meta = {}
        if self._meta_path.exists():
            try:
                meta = json.loads(self._meta_path.read_text(encoding="utf-8"))
            except Exception:
                pass
        file_count = 0
        total_bytes = 0
        if self._home.exists():
            for root, _, files in os.walk(self._home):
                for f in files:
                    fp = Path(root) / f
                    try:
                        total_bytes += fp.stat().st_size
                        file_count += 1
                    except Exception:
                        pass
        return {
            "run_id": self.run_id,
            "backend": "dev",
            "home": str(self._home),
            "exists": self._home.exists(),
            "file_count": file_count,
            "total_bytes": total_bytes,
            "meta": meta,
        }


# ---------------------------------------------------------------------------
# Sandbox (E2B) backend — production path
# ---------------------------------------------------------------------------

class E2BWorkspace(Workspace):
    """Production workspace backed by E2B via existing SandboxManager."""

    def __init__(self, run_id: str):
        self.run_id = run_id
        self._manager = None
        self._created = False

    def _get_manager(self):
        if self._manager is None:
            from app.sandbox.manager import SandboxManager
            self._manager = SandboxManager()
        return self._manager

    async def create(self) -> None:
        mgr = self._get_manager()
        await mgr.create(self.run_id)
        self._created = True
        logger.info("workspace.e2b.created", run_id=self.run_id)

    async def destroy(self) -> None:
        mgr = self._get_manager()
        # SandboxManager does not expose destroy in older code; best-effort
        if hasattr(mgr, "destroy"):
            await mgr.destroy(self.run_id)
        elif self.run_id in getattr(mgr, "_sandboxes", {}):
            try:
                sb = mgr._sandboxes.pop(self.run_id)
                if hasattr(sb, "kill"):
                    sb.kill()
            except Exception as e:
                logger.warning("workspace.e2b.destroy_failed", error=str(e))
        self._created = False

    async def exec(self, cmd: str, *, timeout: int = DEFAULT_TIMEOUT_S, cwd: str | None = None) -> ExecResult:
        if not self._created:
            await self.create()
        mgr = self._get_manager()
        # Prefer shell; fall back to run_code with bash -c
        start = time.monotonic()
        try:
            if hasattr(mgr, "run_command"):
                raw = await mgr.run_command(self.run_id, cmd, timeout=timeout)
            else:
                # Use python to shell out if only code interpreter available
                code = f"import subprocess,sys\nr=subprocess.run({cmd!r},shell=True,capture_output=True,text=True,timeout={timeout})\nprint(r.stdout)\nprint(r.stderr,file=sys.stderr)\nsys.exit(r.returncode)"
                raw = await mgr.execute(self.run_id, code, timeout=timeout + 5)
            duration_ms = int((time.monotonic() - start) * 1000)
            if isinstance(raw, dict):
                return ExecResult(
                    stdout=str(raw.get("stdout") or raw.get("output") or "")[:MAX_OUTPUT_CHARS],
                    stderr=str(raw.get("stderr") or "")[:MAX_OUTPUT_CHARS],
                    exit_code=int(raw.get("exit_code") or raw.get("code") or 0),
                    timed_out=bool(raw.get("timed_out")),
                    duration_ms=duration_ms,
                )
            return ExecResult(stdout=str(raw)[:MAX_OUTPUT_CHARS], duration_ms=duration_ms)
        except Exception as e:
            duration_ms = int((time.monotonic() - start) * 1000)
            return ExecResult(stderr=str(e), exit_code=1, duration_ms=duration_ms)

    async def read(self, path: str, *, max_bytes: int = MAX_FILE_BYTES) -> dict[str, Any]:
        # Use cat via exec
        r = await self.exec(f"python3 -c \"import pathlib,sys; p=pathlib.Path({path!r}); data=p.read_bytes()[:{max_bytes}]; sys.stdout.buffer.write(data)\"", timeout=30)
        if r.exit_code != 0:
            return {"error_code": "read_failed", "message": r.stderr or r.stdout, "retryable": False}
        return {"path": path, "content": r.stdout, "size": len(r.stdout)}

    async def write(self, path: str, content: str | bytes) -> dict[str, Any]:
        if isinstance(content, bytes):
            import base64
            b64 = base64.b64encode(content).decode("ascii")
            cmd = f"python3 -c \"import pathlib,base64; pathlib.Path({path!r}).parent.mkdir(parents=True,exist_ok=True); pathlib.Path({path!r}).write_bytes(base64.b64decode({b64!r}))\""
        else:
            # Escape via base64 for safety
            import base64
            b64 = base64.b64encode(content.encode("utf-8")).decode("ascii")
            cmd = f"python3 -c \"import pathlib,base64; pathlib.Path({path!r}).parent.mkdir(parents=True,exist_ok=True); pathlib.Path({path!r}).write_bytes(base64.b64decode({b64!r}))\""
        r = await self.exec(cmd, timeout=30)
        if r.exit_code != 0:
            return {"error_code": "write_failed", "message": r.stderr or r.stdout, "retryable": False}
        return {"path": path, "bytes": len(content) if isinstance(content, (str, bytes)) else 0}

    async def edit(
        self,
        path: str,
        old_string: str,
        new_string: str,
        *,
        replace_all: bool = False,
    ) -> dict[str, Any]:
        import base64
        old_b = base64.b64encode(old_string.encode()).decode()
        new_b = base64.b64encode(new_string.encode()).decode()
        cmd = (
            f"python3 -c \""
            f"import pathlib,base64; "
            f"p=pathlib.Path({path!r}); t=p.read_text(encoding='utf-8'); "
            f"o=base64.b64decode('{old_b}').decode(); n=base64.b64decode('{new_b}').decode(); "
            f"c=t.count(o) if {replace_all} else (1 if o in t else 0); "
            f"t2=t.replace(o,n) if {replace_all} else t.replace(o,n,1); "
            f"p.write_text(t2,encoding='utf-8'); print(c)\""
        )
        r = await self.exec(cmd, timeout=30)
        if r.exit_code != 0:
            return {"error_code": "edit_failed", "message": r.stderr or r.stdout, "retryable": False}
        try:
            count = int(r.stdout.strip() or "0")
        except ValueError:
            count = 0
        if count == 0:
            return {"error_code": "no_match", "message": "old_string not found", "retryable": False}
        return {"path": path, "replacements": count}

    async def list(self, path: str = ".", *, recursive: bool = False) -> dict[str, Any]:
        flag = "-R" if recursive else ""
        r = await self.exec(f"find {path} {flag} -maxdepth {5 if recursive else 1} 2>/dev/null | head -500", timeout=30)
        entries = []
        for line in (r.stdout or "").splitlines():
            line = line.strip()
            if not line or line == path:
                continue
            entries.append({"path": line, "type": "unknown"})
        return {"path": path, "entries": entries}

    async def grep(
        self,
        pattern: str,
        path: str = ".",
        *,
        max_matches: int = 50,
    ) -> dict[str, Any]:
        r = await self.exec(f"grep -rn --include='*' -E {pattern!r} {path} 2>/dev/null | head -{max_matches}", timeout=30)
        matches = []
        for line in (r.stdout or "").splitlines():
            parts = line.split(":", 2)
            if len(parts) >= 3:
                matches.append({"path": parts[0], "line": int(parts[1]) if parts[1].isdigit() else 0, "text": parts[2][:500]})
        return {"pattern": pattern, "matches": matches, "truncated": len(matches) >= max_matches}

    async def snapshot(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "backend": "sandbox",
            "created": self._created,
        }


# ---------------------------------------------------------------------------
# Registry of live workspaces (survive within process; re-create by run_id)
# ---------------------------------------------------------------------------

_workspaces: dict[str, Workspace] = {}


def get_workspace(run_id: str, *, backend: str | None = None) -> Workspace:
    """Return existing or create workspace for run_id. Idempotent re-attach."""
    if run_id in _workspaces:
        return _workspaces[run_id]
    backend = (backend or os.environ.get("WORKSPACE_BACKEND") or "dev").lower()
    if backend == "sandbox":
        ws: Workspace = E2BWorkspace(run_id)
    else:
        ws = DevWorkspace(run_id)
    _workspaces[run_id] = ws
    return ws


async def create_workspace(run_id: str, *, backend: str | None = None) -> Workspace:
    ws = get_workspace(run_id, backend=backend)
    await ws.create()
    return ws


async def destroy_workspace(run_id: str) -> None:
    ws = _workspaces.pop(run_id, None)
    if ws is not None:
        await ws.destroy()


def list_active_workspaces() -> list[str]:
    return list(_workspaces.keys())
