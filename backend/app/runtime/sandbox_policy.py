"""Phase 4 — Sandbox policy for workspace shell / code execution.

Centralizes command denylists, timeout caps, and network target blocks so
DevWorkspace and future backends share one policy surface.

This is defense-in-depth on top of path guards and cleaned env in workspace.py.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import Any

# Hard ceiling even if caller requests more.
MAX_EXEC_TIMEOUT_S = int(os.environ.get("SANDBOX_MAX_TIMEOUT_S", "120"))
DEFAULT_EXEC_TIMEOUT_S = int(os.environ.get("SANDBOX_DEFAULT_TIMEOUT_S", "60"))

# Patterns that must never run inside the agent workspace shell.
# Matched case-insensitively against the full command string.
_DENIED_PATTERNS: list[tuple[str, str]] = [
    (r"\brm\s+(-[a-zA-Z]*f[a-zA-Z]*\s+)?/(?!tmp|home|var/tmp)", "destructive_rm_root"),
    (r"\bmkfs\b", "mkfs_blocked"),
    (r"\bdd\s+if=", "dd_blocked"),
    (r":\(\)\s*\{\s*:\|:\s*&\s*\}\s*;", "fork_bomb"),
    (r"\bshutdown\b|\breboot\b|\bpoweroff\b", "power_control"),
    (r"\bmount\b\s+-|umount\b", "mount_blocked"),
    (r"\bchmod\s+-R\s+777\s+/", "chmod_root"),
    (r"\bchown\s+-R\s+.*\s+/", "chown_root"),
    (r"curl\s+[^|]*169\.254\.169\.254", "cloud_metadata"),
    (r"wget\s+[^|]*169\.254\.169\.254", "cloud_metadata"),
    (r"curl\s+[^|]*metadata\.google", "cloud_metadata"),
    (r"/etc/shadow|/etc/sudoers", "sensitive_host_file"),
    (r"\bnc\s+-[el]|\bncat\s+-[el]|\bsocat\b.*EXEC", "reverse_shell"),
    (r"\bpython[23]?\s+-c\s+['\"][^'\"]*socket\.create_connection\(\(\s*['\"]169\.254",
     "cloud_metadata_py"),
    (r"\bdocker\s+(run|exec|build)\b", "docker_blocked"),
    (r"\bkubectl\b|\bcrictl\b", "k8s_blocked"),
    (r"\bsudo\b|\bsu\s+-", "privilege_escalation"),
    (r"\bexport\s+(AWS_|AZURE_|GOOGLE_|OPENAI_|ANTHROPIC_|KIMI_|DEEPSEEK_)[A-Z0-9_]*=",
     "secret_export"),
]

# Compiled once
_COMPILED = [(re.compile(p, re.IGNORECASE), code) for p, code in _DENIED_PATTERNS]


@dataclass
class PolicyDecision:
    allowed: bool
    reason: str = ""
    error_code: str = ""
    timeout_s: int = DEFAULT_EXEC_TIMEOUT_S

    def to_dict(self) -> dict[str, Any]:
        return {
            "allowed": self.allowed,
            "reason": self.reason,
            "error_code": self.error_code,
            "timeout_s": self.timeout_s,
        }


def clamp_timeout(timeout: int | float | None) -> int:
    """Clamp caller timeout into [1, MAX_EXEC_TIMEOUT_S]."""
    try:
        t = int(timeout) if timeout is not None else DEFAULT_EXEC_TIMEOUT_S
    except (TypeError, ValueError):
        t = DEFAULT_EXEC_TIMEOUT_S
    return max(1, min(t, MAX_EXEC_TIMEOUT_S))


def check_command(cmd: str, *, timeout: int | float | None = None) -> PolicyDecision:
    """Return whether a shell command is allowed under sandbox policy."""
    if not cmd or not str(cmd).strip():
        return PolicyDecision(
            allowed=False,
            reason="empty command",
            error_code="empty_command",
            timeout_s=clamp_timeout(timeout),
        )
    text = str(cmd).strip()
    # Null-byte injection
    if "\x00" in text:
        return PolicyDecision(
            allowed=False,
            reason="null byte in command",
            error_code="invalid_command",
            timeout_s=clamp_timeout(timeout),
        )
    for cre, code in _COMPILED:
        if cre.search(text):
            return PolicyDecision(
                allowed=False,
                reason=f"command denied by sandbox policy ({code})",
                error_code=code,
                timeout_s=clamp_timeout(timeout),
            )
    return PolicyDecision(allowed=True, timeout_s=clamp_timeout(timeout))


def check_python_code(code: str) -> PolicyDecision:
    """Light static checks on python_exec payloads."""
    if not code or not str(code).strip():
        return PolicyDecision(allowed=False, reason="empty code", error_code="empty_code")
    text = str(code)
    blocked_snippets = [
        ("169.254.169.254", "cloud_metadata"),
        ("metadata.google.internal", "cloud_metadata"),
        ("/etc/shadow", "sensitive_host_file"),
        ("subprocess.Popen", "subprocess_popen"),
        ("os.system", "os_system"),
        ("pty.spawn", "pty_spawn"),
    ]
    lower = text.lower()
    for snippet, code_name in blocked_snippets:
        if snippet.lower() in lower:
            return PolicyDecision(
                allowed=False,
                reason=f"python code denied by sandbox policy ({code_name})",
                error_code=code_name,
            )
    return PolicyDecision(allowed=True)


def denied_result(decision: PolicyDecision) -> dict[str, Any]:
    """Shape a tool-style error dict from a deny decision."""
    return {
        "ok": False,
        "error_code": decision.error_code or "policy_denied",
        "message": decision.reason or "denied by sandbox policy",
        "retryable": False,
        "stdout": "",
        "stderr": decision.reason or "denied",
        "exit_code": 126,
    }


__all__ = [
    "PolicyDecision",
    "check_command",
    "check_python_code",
    "clamp_timeout",
    "denied_result",
    "MAX_EXEC_TIMEOUT_S",
    "DEFAULT_EXEC_TIMEOUT_S",
]
