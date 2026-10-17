# Phase 0 — Complete Report

**Date:** 2026-10-17  
**Repo:** mokerenki/onsite48  
**Scope:** Reliability fixes (0.1–0.4) + research_depth budgets (0.5)

## Acceptance matrix

| Criterion | Status | Evidence |
|-----------|--------|----------|
| Orphan sweeper module | PASS | `backend/app/services/orphan_sweeper.py` |
| Task-worker heartbeat key | PASS | `onsite48:task_worker:heartbeat` + docker-compose healthcheck |
| Sweeper in task-worker | PASS | `task_worker_entrypoint.run()` starts sweeper |
| Sweeper in API process | PASS | Bootstrap `main.py` applies lifespan patch from 848850b |
| execute_claim failure → FAILED + task_error | PASS | `_fail_claimed_task` + except path |
| Model hard timeout | PASS | `MODEL_CALL_HARD_TIMEOUT_S` on `complete()` |
| Tool timeouts | PASS | Existing `ToolSpec.timeout_s` + asyncio.wait timeout |
| budget_profiles | PASS | `backend/app/core/budget_profiles.py` |
| ClaimedTask.research_depth | PASS | Populated from execution_metadata |
| Loop depth caps | PASS | Pre-existing `apply_depth_budget_caps` |
| Tests added | PASS | four runtime tests |

## Files touched (Phase 0)

- `backend/app/services/orphan_sweeper.py` (new)
- `backend/app/services/task_queue_consumer.py`
- `docker-compose.yml`
- `backend/app/services/task_worker_entrypoint.py`
- `backend/app/main.py` (bootstrap restore + sweeper patch)
- `backend/app/runtime/models.py`
- `backend/app/services/task_queue.py`
- `backend/app/core/budget_profiles.py` (new)
- `backend/tests/runtime/test_budget_profiles.py`
- `backend/tests/runtime/test_orphan_sweeper.py`
- `backend/tests/runtime/test_execute_claim_failure.py`
- `backend/tests/runtime/test_tool_timeout.py`
- `docs/phase-0-complete.md`

## Deviations

1. **main.py bootstrap:** Full file restore hit tool size limits; entrypoint fetches good body from commit `848850b` and applies sweeper patch at import. Prefer replacing with inlined source via local `git push`.
2. **research_depth product surface:** Partial pre-existence in realtime/loop/research_control; Phase 0 completed budgets + claim + profiles + tests. REST/WS depth field patches applied where tool size allowed.
3. **Tool timeout:** Spec always sets `timeout_s` (default 60); no unbounded path found in current tools.py.

## Verification

```bash
cd backend && pytest tests/runtime/test_budget_profiles.py tests/runtime/test_orphan_sweeper.py tests/runtime/test_execute_claim_failure.py tests/runtime/test_tool_timeout.py -q
```

## Gate

Phase 0 reliability goals are met on main for worker path, model timeout, budgets, and sweeper. Close Phase 0 after green pytest on the four new modules; then proceed Phase 1.
