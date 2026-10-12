# Discovery Phase 5 — Sub-agents, wide research, resource safety

## Verified from Phase 4 runtime (zip contents)

### Existing runtime (app/runtime/)
- `loop.py` (789 LOC): single tool-calling loop; `run()` entrypoint; budgets (steps/tokens/cost/wall); readback; recovery; verifier on finish.
- `tools.py` (702 LOC): ToolSpec with side_effect/idempotency/trust/readback; wraps registry tools + builtins (finish, ask_user, file_*, shell, python_exec, browser, load_skill, render_preview).
- `models.py` (164): RunEvent has `parent_run_id`; RunState has `role_config`, `parent_run_id`, cost/token/step counters.
- `journal.py` (160): append-only events; parent_run_id supported in schema.
- `workspace.py` (607): persistent sandbox paths; isolation tests exist.
- `policy.py` (291): TAKEOVER/APPROVAL/DENY/ALLOW; provenance rules from Phase 3.
- `context.py`: system prompt load, untrusted wrapping, compaction.
- Browser protocol v4 + deliverable preview from Phase 4 present.
- Prompts: only `system_v1.txt`.

### Legacy / adjacent
- `tools/wide_research_tool.py` (legacy BaseTool): facet-parallel research with browser + router; NOT integrated into runtime loop; uses old agents/codeact path. Will NOT be the Phase 5 wide_map; keep behind registry only if needed for compat, prefer new runtime spawn/wide_map.
- `tests/test_spawn_subagent.py`: old SpawnSubagentTool recursion-limit test against agents layer. Will be superseded by runtime tests; old test may be deleted if it asserts deleted architecture.
- Run ledger / task lease / policy_engine exist and are kept.

### Gaps vs Phase 5 prompt
1. No `RoleConfig`, no role prompt files (researcher, browser_operator, coder, writer, critic).
2. No `spawn_agent` / `spawn_agents` / `await_agents` / `wide_map` tools on the runtime surface.
3. No resource locks (`locks.py`) keyed by ToolSpec.resources(args).
4. Loop does not yet spawn child runs with isolated context, cost rollup, cancel propagation, or depth cap.
5. No concurrency governor / cost preflight for fan-out.
6. Frontend timeline does not yet nest child runs (ExecutionTimeline.tsx / TaskReplay.tsx exist but not verified for nesting).
7. No 14+ parallel-oriented dev evals; no 50-item wide-research headline task.
8. Isolation proof test (secret in lead context unreachable by worker) absent.
9. Parallel tool calls restricted to reads — not yet enforced in loop.

### What differs from the phase prompt
- Infrastructure partially ready: parent_run_id on events/state, role_config field, single loop design already correct for "worker is the same loop with different config".
- Legacy wide_research_tool is a parallel specialist but is not context-isolated workers and not schema-driven wide_map over items.
- No second execution engine required — confirm and reuse `runtime.loop.run`.

## Plan (Phase 5)
1. `roles.py`: RoleConfig dataclass + registry of named roles; load system_prompt from prompts/<role>_v1.txt; tool_allowlist; budget; may_spawn=False by default; depth_cap=1.
2. `locks.py`: async resource locks keyed by ToolSpec.resources(args); expire with lease; resource_busy structured error.
3. `spawn.py`: spawn_agent, spawn_agents, await_agents, wide_map; workers call loop.run with isolated ContextBuilder (no lead history); artifact store under /workspace/shared/<worker_id>/; return contract {status, result, summary, artifacts, evidence}.
4. Wire tools into `tools.py` + model surface; policy inheritance (worker grants ⊆ parent).
5. Loop updates: detect spawn tools; track child handles; cost rollup; cancel children on parent cancel; depth check; parallel read-only tool calls allowed, writes sequential.
6. Role prompts (researcher, browser_operator, coder, writer, critic).
7. Tests: isolation secret, cancel propagation, crash isolation, cost rollup ±1%, contention locks, wide_map partial failure.
8. ≥14 dev evals for parallelism + 50-item headline.
9. Frontend note: nested child runs in timeline (extend if FE extracted; else mark structural).
10. PHASE_5_REPORT.md with gates, line counts, NOT RUN list.

## Risks
- Live model + browser credentials required for full eval pass rates → unit/structural + simulated worker tests primary; live marked NOT RUN.
- Concurrency governor needs Redis/lease integration for multi-tenant; implement in-process + lease-aware interface.
- Cost preflight requires model pricing table; use existing cost accounting if present, else conservative estimate.
