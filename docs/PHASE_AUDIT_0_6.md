# CTO Audit — Phases 0–6

**Date:** 2026-10-17  
**Auditor posture:** senior engineer / CTO review of `main`  
**Repo:** mokerenki/onsite48

## Executive summary

Phases 0–6 delivered real, testable modules and several production wiring paths.
They are **not** uniformly “fully integrated into the agent loop.” Treat Phases 1, 3–5 as
production-adjacent (called from live paths). Treat Phases 2 and 6 as **policy libraries**
that still need deeper loop adoption. Phase 0 reliability work is substantially in place.

**Overall grade: B− (solid foundations, incomplete vertical integration)**

---

## Phase-by-phase

### Phase 0 — Reliability (orphan sweep, failures, timeouts, budgets)

| Item | Status | Notes |
|------|--------|-------|
| `orphan_sweeper.py` | **PASS** | Present; cycles `enforce_timeouts` + requeue |
| API lifespan start | **PASS** | `main.py` starts/stops sweeper |
| Task-worker heartbeat | **PASS** | Consumer writes heartbeat key |
| `execute_claim` except → FAILED | **PASS** | `_fail_claimed_task` + re-raise |
| Model hard timeout | **PASS** | `complete()` wrapped in `wait_for` |
| Tool timeouts | **PASS** | Loop uses `asyncio.wait_for` + `spec.timeout_s` |
| budget_profiles + tests | **PASS** | Module + unit tests |
| research_depth plumbing | **PARTIAL** | Fields exist in realtime/product paths; not a full end-to-end proof in this audit |
| Full async integration tests in sandbox | **PARTIAL** | Need structlog/sqlalchemy/fastapi; CI/Compose is the real gate |

**Verdict:** Phase 0 meets the reliability bar for production hygiene. Residual risk is
env-dependent test coverage, not missing code.

### Phase 1 — Observation loop

| Item | Status | Notes |
|------|--------|-------|
| `observation.py` | **PASS** | Structured Observation + tracker |
| Wired into `ContextBuilder.format_tool_result` | **PASS** | Always on |
| Loop calls `format_tool_result` | **PASS** | Multiple sites in `loop.py` |
| Unit tests | **PASS** | 8 tests |

**Verdict:** Correctly implemented and on the hot path.

### Phase 2 — Hierarchical plans

| Item | Status | Notes |
|------|--------|-------|
| `hierarchical_plan.py` DAG + status machine | **PASS** | Solid pure design |
| Context rendering | **PASS** | `_hierarchical_plan_block` |
| `RunState.hierarchical_plan` field | **FAIL / GAP** | **Not present** on `journal.RunState` in current main |
| Loop generates/advances plans | **FAIL / GAP** | No references in `loop.py` |
| Unit tests for pure module | **PASS** | |

**Verdict:** Correct as a library; **not integrated** into planning/execution. Agents will
only see a plan if something external sets `state.hierarchical_plan` or `role_config`.
This is the largest intelligence-gap vs. the prompt narrative.

### Phase 3 — Context engineering

| Item | Status | Notes |
|------|--------|-------|
| `context_engine.py` | **PASS** | Budgeted pack + extractive summary |
| `ContextBuilder.build` uses engine | **PASS** | |
| Loop uses `context.build` | **PASS** | |
| Unit tests | **PASS** | |

**Verdict:** Correctly implemented and on the hot path.

### Phase 4 — Sandbox tools / policy

| Item | Status | Notes |
|------|--------|-------|
| Existing Workspace backends | **PASS** (pre-existing) | Dev + E2B |
| `sandbox_policy.py` | **PASS** | Command/python denylist |
| Wired into `DevWorkspace.exec` / `python_exec` | **PASS** | |
| Escape suite (path/env) | **PASS** (pre-existing tests) | |
| Full container isolation | **OUT OF SCOPE / DEV** | Documented DEV path |

**Verdict:** Correct defense-in-depth layer; not a full multi-tenant sandbox product by itself.

### Phase 5 — Browser quality

| Item | Status | Notes |
|------|--------|-------|
| Protocol v4 operator (pre-existing) | **PASS** | Stale detection, policy |
| `quality.py` score + compact | **PASS** | |
| Wired into browser tool | **PASS** | observe/act enrich |
| Unit tests | **PASS** | |

**Verdict:** Correct incremental quality layer on a mature browser stack.

### Phase 6 — Model strategy

| Item | Status | Notes |
|------|--------|-------|
| `model_strategy.py` | **PASS** | Signals, lanes, profiles, chains |
| Detailed tests (32) | **PASS** | |
| Called from `loop.py` / `models.complete` | **FAIL / GAP** | **Zero call sites** outside its module |
| Coexists with ModelRouter failover | **PASS** (by design) | Transport vs policy |

**Verdict:** Correct policy library; **not applied** on live model calls yet. Router still
decides roles independently.

---

## Cross-cutting risks

1. **Integration depth varies** — modules shipped; loop adoption incomplete (P2, P6).
2. **GitHub tool size limits** earlier caused at least one near-miss (`workspace.py` PLACEHOLDER);
   restored, but process risk remains for large files.
3. **Sandbox test env** lacks `structlog`/DB deps intermittently — unit tests for pure modules
   are green; full suite needs CI.
4. **Phase naming collision** — repo already had historical “Phase 1–6” runtime reports;
   our phase docs are the intelligence-upgrade track, not the remediation roadmap track.

## Required follow-ups (ordered)

1. **P2:** Persist `RunState.hierarchical_plan`; build plan at run start; advance on tool results.
2. **P6:** Call `select_strategy` / `infer_signals` from `models.complete` or loop model turn.
3. Keep Phase 0 async tests green in Compose CI.

## Gate for Phase 7

Proceed with Phase 7 (claim verifier / claim grounding) as an additive evidence layer on the
**existing** `verifier.py` path (already integrated at finish). Do not pretend P2/P6 loop
wiring is done.
