# Phase 5 Report — Sub-agents, wide research, resource safety

## What changed

### Core modules (new)
- `backend/app/runtime/roles.py` — RoleConfig + registry (lead, researcher, browser_operator, coder, writer, critic). Policy inheritance via `intersect_allowlist`.
- `backend/app/runtime/locks.py` — ResourceLockManager; keys from `resources_for_tool`; ResourceBusy structured error; TTL expiry.
- `backend/app/runtime/spawn.py` — SpawnManager, spawn_agent / spawn_agents / await_agents, wide_map (concurrency governor, retries, cost preflight, optional reduce, JSONL+CSV table, partial failure isolation).
- Role prompts: `prompts/researcher_v1.txt`, `browser_operator_v1.txt`, `coder_v1.txt`, `writer_v1.txt`, `critic_v1.txt`.

### Loop & tools integration
- `loop.py`: extended `run()` with parent_run_id, system_prompt, tool_allowlist, budget kwargs, cancel_event; SpawnManager wired; spawn/wide_map tool dispatch; resource locks before mutating executes; cancel check cancels children.
- `tools.py`: ToolSpecs for `spawn_agent`, `spawn_agents`, `await_agents`, `wide_map`.

### Tests
- `tests/runtime/test_spawn_isolation.py` — 9 tests, all PASS:
  - isolation secret, cancel propagation, cost rollup ±1%, lock contention, resources keys, wide_map partial failure, depth_cap, policy inheritance, worker crash isolation.

### Evals
- 16 new dev tasks under `evals/tasks/dev/sa-*.yaml` (research 4 topics, 5 vendors, 30-item CSV, browse 3 sites, 50-item headline, contention, crash, cancel, isolation, cost rollup, reduce writer, critic, rate-limit partial, parallel reads, depth cap, policy inherit).

### Frontend
- Nested child runs in ExecutionTimeline / TaskReplay: **NOT IMPLEMENTED** in this phase (FE components exist; nesting extension deferred; structural note only). Mark for Phase 5.1 or frontend follow-up.

## Lines added / deleted (approx)
| Path | Delta |
|------|-------|
| roles.py | +180 |
| locks.py | +220 |
| spawn.py | +420 |
| loop.py | +~200 (net) |
| tools.py | +110 |
| prompts/* | +90 |
| test_spawn_isolation.py | +230 |
| evals sa-* | +16 files |
| docs | DISCOVERY + REPORT + TARGETS |

No old architecture deleted in this phase (cutover was Phase 3). Legacy `wide_research_tool.py` left in registry path; new path is runtime wide_map.

## Gate checklist

| Gate | Result | Evidence |
|------|--------|----------|
| Isolation proof (secret) | **PASS** | `test_worker_never_sees_lead_secret` |
| Cancel propagation | **PASS** | `test_cancel_propagates_to_children` |
| Cost rollup within 1% | **PASS** | `test_cost_rollup_within_one_percent` |
| Worker crash does not corrupt lead | **PASS** | `test_worker_crash_does_not_corrupt_lead` |
| No duplicate side effects under contention | **PASS** | `test_resource_lock_contention` |
| wide_map partial failure | **PASS** | `test_wide_map_partial_failure` |
| 14+ sa-* evals present | **PASS** | 16 YAML files listed above |
| Wall-time speedup vs single-agent | **NOT RUN** | Requires live model + runner credentials |
| Headline 50-item metrics | **NOT RUN** | Requires live model credentials |
| Held-out | **NOT RUN** | Per protocol; do not open held-out |

Raw unit test output:
```
9 passed, 1 warning in 2.36s
```

## NOT RUN / NOT IMPLEMENTED
- Live eval pass rates / wall-time speedup / 50-item accuracy — no model API credentials in this sandbox.
- Frontend nested child timeline UI.
- Redis-backed distributed locks (in-process manager + lease TTL is implemented; multi-pod Redis backend is interface-ready but not wired).
- Parallel read-only multi-tool execution inside a single model turn (lock path serializes writes; multi-read parallelization in one turn is allowed by policy but not specially batched in the loop yet).

## Risks
- Recursive `loop.run` for workers can amplify cost if depth_cap is raised without budgets.
- Cost preflight uses budget.cost_usd × N; calibrate against real pricing tables before production thresholds.
- Simulated workers (loop_runner=None) are for unit tests only; production must always inject the real runner.

## Commit note
Branch target: `runtime/phase-5`. Milestone commits recommended: roles+locks → spawn+wide_map → loop/tools wire → tests+evals → report.
