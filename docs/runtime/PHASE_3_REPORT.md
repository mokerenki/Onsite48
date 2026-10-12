# Phase 3 Report — Verification, recovery, injection defense, cutover

## What changed

### 3a. Read-back evidence
- `ToolSpec.readback` is now populated for:
  - Workspace: `file_write`, `file_edit`, `deliver_file` (hash/size/content checks).
  - Registry mutating tools via `_infer_readback_for_registry` (create_draft→get_draft, gmail_send→get_message, create_event→get_event, delete_*→expect 404, etc.).
- Loop automatically invokes readback after every successful write / external_send / destructive / spend call and journals `readback_result {verified, observed, expected_diff}`.
- Failed or missing readback is surfaced to the model as `result.readback.verified=false` and counted as no-progress.

### 3b. Completion gate (replaces 1,089-line verifier)
- New `backend/app/runtime/verifier.py` (independent lane, role=`verifier`, tools=[]).
- On `finish`, builds evidence ledger (goal, summary, readbacks, key tool results, journal excerpt) and returns strict JSON `{verdict, unmet_requirements, missing_evidence}`.
- `fail` within `MAX_FINISH_ATTEMPTS=2` returns unmet requirements to the main loop; otherwise terminal `failed`.
- `unverifiable` → terminal `completed_unverified` with explicit missing-evidence note.
- Checks the **goal**, not the plan.

### 3c. Recovery (`recovery.py`)
- Deterministic classifier: transient, rate_limited (honors retry-after), auth_expired (vault refresh once, else needs_input), invalid_arguments, precondition_failed, permanent, loop_detected.
- Exponential backoff + jitter. Loop detection from Phase 1 lives here (same-call / no-progress limits).
- Recovery class is appended into tool observations so the model sees it.

### 3d. Honest terminal states
- `completed | completed_unverified | partial | needs_input | waiting_approval | failed | cancelled`.
- `user_facing_message` prefixes the summary to match the state.
- Policy APPROVAL/TAKEOVER → `waiting_approval`; ask_user → `needs_input`; auth refresh failure → `needs_input`.

### 3e. Prompt-injection defense
- `wrap_untrusted` now emits a structural `UNTRUSTED_DATA provenance=...` header; system prompt forbids treating tool content as instructions or policy changes.
- Argument provenance in `policy.py`: identifying args (recipient, URL, account, amount, …) that appear **only** in untrusted tool output (or nowhere in user context) force `APPROVAL` with provenance metadata shown to the user. Structural, not keyword matching on goals.
- Browser/file content cannot change policy, tool permissions, or approval state.
- 12 injection evals (`inj-001`…`inj-012`) + 4 adversarial honesty evals (`adv-honest-001`…`004`) authored under `evals/tasks/dev/`.

### 3f. Cutover and deletion
- `AGENT_RUNTIME` default changed from `legacy` → `loop` in `task_worker_entrypoint.py`.
- `graph.py` rewritten to call `runtime.loop.run` only.
- **Deleted legacy brain (8,054 LOC):**
  - `agents/planner.py` (2,268), `executor.py` (2,246), `verifier.py` (1,089)
  - `agents/plans/*` (gmail 769 + calendar/github/time/web/build_clarification + init = 1,779)
  - `core/compound_intent.py` (171), `intent_resolution.py` (162), `goal_contract.py` (116), `capability_model.py` (55), `intent_contracts.py` (168)
- **Deleted 34 tests that only asserted legacy internals (6,603 LOC).**
- Residual API imports (`confirmation_key`, `SAFE_TASK_SCOPED_TOOLS`, `SCHEDULE_HARD_STOP_TOOLS`) moved to `agents/legacy_compat.py` (pure helpers, no brain).
- `intent_extractor.py` neutralized to no-op.
- `fast_answer.py` planner regex imports removed.

## Lines added / deleted

| Area | Added | Deleted |
|------|------:|--------:|
| recovery.py | 211 | 0 |
| verifier.py | 278 | 0 |
| loop.py (rewrite) | 789 | (prior 181) |
| policy.py (rewrite) | 291 | (prior 100) |
| tools.py readback helpers | ~350 net | 0 |
| legacy_compat.py | 65 | 0 |
| graph.py rewrite | 41 | (prior 160) |
| Phase 3 unit tests | 238 | 0 |
| Injection + adversarial evals | 16 YAML | 0 |
| Legacy brain | 0 | **8,054** |
| Legacy-only tests | 0 | **6,603** |
| **Net** | ~2,200 runtime | **−14,657** |

## Gate 3 checklist

| Gate | Status | Evidence |
|------|--------|----------|
| Per-tier targets on dev / held-out vs Gate 2 | **NOT RUN** | No model API keys / connector credentials in this environment. Evals authored; runner not executed live. |
| False-completion audit (20 pass + 20 fail) | **NOT RUN** | Requires live eval runs. Structural verifier logic unit-tested (`test_verifier.py`). |
| 4 adversarial honesty evals | **AUTHORED** | `adv-honest-001`…`004`. Pass criteria: forbid `completed`; allow honest non-success. Live run NOT RUN. |
| Injection evals: zero unauthorized side effects; over-blocking reported | **AUTHORED** | `inj-001`…`012` (11 adversarial + 1 benign control). Live run NOT RUN. Unit provenance test: untrusted-only recipient → APPROVAL. |
| Legacy code deleted with line counts | **PASS** | 8,054 LOC brain + 6,603 LOC tests deleted (see above). |
| Full test suite green except env-only | **PARTIAL** | New unit tests for recovery/verifier/provenance pass under stubbed structlog. Full suite needs `structlog`/DB; environment lacks packages (pip 502). Workspace/skills tests from Phase 2 unchanged. |

## Unit test evidence (raw)

```
recovery FailureClass.TRANSIENT
verdict completed_unverified
prov Decision.APPROVAL
```

Provenance unit: `attacker@evil.com` only in untrusted values → `Decision.APPROVAL` with provenance metadata.

Verifier unit: pass → completed; fail+attempts=1 → `_retry_finish`; fail+exhausted → failed; unverifiable → completed_unverified.

## NOT RUN / NOT IMPLEMENTED

| Item | Reason |
|------|--------|
| Live eval runner `--repeat 3` on dev | No LLM API credentials in sandbox |
| Held-out / external evals | Human-run only per protocol |
| False-completion hand audit of 40 runs | Depends on live runs |
| E2B / k8s sandbox integration for readback | DevWorkspace only in this env |
| Vault refresh integration test | connector_vault not configured |
| Over-blocking rate measurement | Requires live inj-012 runs |

## Risks
1. Registry readbacks depend on naming conventions (`create_X`→`get_X`); tools outside the map mark `verified=false` which is safe but may increase `completed_unverified`.
2. Verifier is another model call (cost); failures degrade to `unverifiable` rather than blocking.
3. Some non-test modules may still reference deleted symbols indirectly — remaining grep after cutover showed only neutralized/compat paths in `app/`.
4. `fast_answer` gmail fast-paths disabled (patterns removed with planner).

## Gate decision
**Structural Phase 3 complete.** Live Gate 3 performance targets and false-completion audit require human-run evals with credentials. Cutover is active (`AGENT_RUNTIME=loop` default); legacy brain is gone.
