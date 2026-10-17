# Phase 4 — Sandbox Tools — Complete

**Date:** 2026-10-17  
**Repo:** mokerenki/onsite48  
**Status:** CLOSED

## Checklist

| Step | Status |
|------|--------|
| Sandbox command policy module | Done |
| Deny dangerous shell patterns | Done |
| Timeout clamp | Done |
| Python static checks | Done |
| Wire into DevWorkspace.exec | Done |
| Wire into Workspace.python_exec | Done |
| Wire into E2BWorkspace.exec | Done |
| Unit tests | Done — 8/8 |
| Integration (policy on exec) | Done |
| Phases 1–3 regression | Done — 24/24 |

## What shipped

### `backend/app/runtime/sandbox_policy.py`
- `check_command()` — denylist (sudo, docker, kubectl, cloud metadata, sensitive files, reverse shells, …)
- `check_python_code()` — blocks `os.system`, metadata IPs, `/etc/shadow`, etc.
- `clamp_timeout()` — hard ceiling (`SANDBOX_MAX_TIMEOUT_S`, default 120s)
- `denied_result()` — stable tool error shape (exit 126)

### `backend/app/runtime/workspace.py`
- `exec()` consults policy before subprocess
- `python_exec()` consults policy before write/run
- Isolation marker includes `sandbox_policy`

### Tests
- `backend/tests/runtime/test_sandbox_policy.py`

## Verification

```
pytest backend/tests/runtime/test_sandbox_policy.py → 8 passed
pytest … test_context_engine + observation + hierarchical → 24 passed
integration: sudo denied (126); echo allowed; os.system denied
```

## Note

Existing path-escape suite in `test_workspace.py` remains the FS/env guard layer.
Phase 4 adds the **command/code policy** layer on top.

## Gate

**Phase 4 complete.** Ready for Phase 5 (browser quality).
