"""Phase 4: sandbox policy unit tests."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from app.runtime.sandbox_policy import (
    MAX_EXEC_TIMEOUT_S,
    check_command,
    check_python_code,
    clamp_timeout,
    denied_result,
)


def test_allows_benign_commands():
    for cmd in ("ls -la", "python3 script.py", "echo hello", "pip list"):
        d = check_command(cmd)
        assert d.allowed is True, cmd


def test_denies_cloud_metadata_curl():
    d = check_command("curl http://169.254.169.254/latest/meta-data/")
    assert d.allowed is False
    assert d.error_code == "cloud_metadata"


def test_denies_sudo_and_docker():
    assert check_command("sudo apt install foo").allowed is False
    assert check_command("docker run -it ubuntu").allowed is False
    assert check_command("kubectl get pods").allowed is False


def test_denies_sensitive_files_in_command():
    d = check_command("cat /etc/shadow")
    assert d.allowed is False
    assert d.error_code == "sensitive_host_file"


def test_denies_empty_and_null():
    assert check_command("").allowed is False
    assert check_command("echo \x00 hi").allowed is False


def test_clamp_timeout():
    assert clamp_timeout(None) >= 1
    assert clamp_timeout(0) == 1
    assert clamp_timeout(99999) == MAX_EXEC_TIMEOUT_S
    assert clamp_timeout(30) == 30


def test_python_code_blocks_metadata_and_os_system():
    assert check_python_code("print(1)").allowed is True
    assert check_python_code("import os; os.system('id')").allowed is False
    assert check_python_code("x = '169.254.169.254'").allowed is False


def test_denied_result_shape():
    d = check_command("sudo id")
    out = denied_result(d)
    assert out["ok"] is False
    assert out["exit_code"] == 126
    assert out["retryable"] is False
    assert out["error_code"]
