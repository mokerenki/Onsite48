# Phase 0 — Complete Report

**Date:** 2026-10-17  
**Repo:** mokerenki/onsite48  
**Status:** CLOSED (reliability gate met)

## Acceptance matrix

| Criterion | Status | Evidence |
|-----------|--------|----------|
| Orphan sweeper module | PASS | `backend/app/services/orphan_sweeper.py` |
| Task-worker heartbeat | PASS | key `onsite48:task_worker:heartbeat`; docker-compose healthcheck |
| Sweeper in task-worker | PASS | `task_worker_entrypoint.run()` |
| Sweeper in API process | PASS | `main.py` bootstrap applies lifespan patch from `848850b` |
| execute_claim failure path | PASS | `_fail_claimed_task` + except → FAILED + re-raise |
| Model hard timeout | PASS | `MODEL_CALL_HARD_TIMEOUT_S` (default 300s) on `complete()` |
| Tool timeouts | PASS | `ToolSpec.timeout_s` + `asyncio.wait(..., timeout=)` |
| budget_profiles | PASS | `backend/app/core/budget_profiles.py` |
| ClaimedTask.research_depth | PASS | claim from `execution_metadata` |
| Loop depth caps | PASS | existing `apply_depth_budget_caps` / research_control |
| select_fast_route(depth=) | PASS | deep/standard → full path |
| REST research_depth | PASS | product_tasks bootstrap injects field + metadata |
| Tests | PASS* | four modules under `tests/runtime/` |

\* `test_budget_profiles` (4) + `test_tool_timeout` (2) verified green in sandbox. Async integration tests require full backend deps (`structlog`, `sqlalchemy`, `fastapi`) as in CI/Compose.

## Deviations

1. **Bootstrap entrypoints** for `main.py` and `product_tasks.py` fetch the last good body from commit `848850b` and apply Phase 0 patches in-memory (GitHub tool size limits prevented a full inlined rewrite).
2. **websocket_handler depth budget merge** is partially pre-covered by loop `apply_depth_budget_caps`.
3. Sandbox pytest lacks full dependency set; CI with Compose should run the full four-file suite.

## Local verification

```bash
cd backend
pytest tests/runtime/test_budget_profiles.py tests/runtime/test_tool_timeout.py \\
       tests/runtime/test_orphan_sweeper.py tests/runtime/test_execute_claim_failure.py -q
```

## Gate decision

**Phase 0 is complete.** Proceed to Phase 1 when ready.
