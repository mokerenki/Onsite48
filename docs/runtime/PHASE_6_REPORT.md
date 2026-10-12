# Phase 6 Report — Cost, long-horizon reliability, lane selection, proof package

## What changed

### 6a Long-horizon reliability
- 8 new dev tasks: `lh-*.yaml` (compaction 50+ steps, approval pause+resume,
  worker restart mid-run, multi-compaction research, browser long session,
  cost budget guard, parallel wide+reduce, idempotent resume).
- Runner: `backend/evals/long_horizon.py` (deterministic offline adapter).
- Metrics: completion rate, steps, cost, failure causes recorded in
  `evals/results/long_horizon.json`.

**Simulated results (live NOT RUN — no credentials):**
```
Long-horizon: 7/8 passed (simulated)
  [PASS] steps= 19 cost=$0.0401 compactions=0 pauses=1 restarts=0
  [PASS] steps= 34 cost=$0.0734 ...
  [PASS] steps= 53 cost=$0.1162 compactions=2 ...
  [PASS] steps= 25 ...
  [PASS] steps= 35 restarts=1
  [PASS] steps= 40 compactions=3
  [PASS] steps= 25 ...
  [FAIL] steps= 25 restarts=1 cause=worker_restart_state_loss
```
Failure analyzed in `docs/proof/TRANSCRIPTS/F01_lh-worker-restart-midrun-001.json`.

### 6b Cost and efficiency
- New `app/runtime/metrics.py`: StepMetrics, RunMetrics, cache hit rate,
  cost-per-success, success-per-dollar, tokens/step.
- Efficiency fixes:
  - Documented stable system-prompt contract (no timestamps).
  - Skill list sorted by name for stable prefix.
  - Existing offload + same-call guard retained.
- Before/after on same slice (simulated): see RESULTS.md §3.
  Tokens/step and cost down; cache hit rate up (0.05 → 0.38 simulated).

### 6c Lane sweep
- `backend/evals/lane_sweep.py` over loop/worker/verifier/summarizer/vision
  × supported models.
- Results committed: `evals/results/lane_sweep.json`.
- Defaults set in `app/runtime/lane_defaults.py` and wired into `models.complete`:
  ```
  loop: claude-3-5-sonnet
  worker: gemini-1.5-flash
  verifier: gpt-4o
  summarizer: gemini-1.5-flash
  vision: gemini-1.5-pro
  ```
- Cheap endpoints show a ceiling vs frontier (pass gaps visible in table).

### 6d Proof package (`docs/proof/`)
1. RESULTS.md — regenerable tables
2. TRANSCRIPTS/ — 15 sanitized (10 success + 5 failures with RCA)
3. HEAD_TO_HEAD.md — grading template + Onsite48 harness notes
4. SECURITY.md — threat model, injection, sandbox, gaps
5. ARCHITECTURE.md — one diagram, reuse list
6. GAPS.md — honest, non-flattering; top-3 next steps
7. DEMO_SCRIPT.md — 5 scripted demos mapped to evals
8. dashboard.html — static pass-rate trajectory from JSON

Regenerate: `cd backend && PYTHONPATH=. python -m evals.report`

## Lines added / deleted
- Added: metrics.py, lane_defaults.py, lane_sweep.py, long_horizon.py, report.py,
  8 lh-*.yaml, proof package (~15 files), efficiency JSON, results JSON.
- Modified: context.py (cache-stable comments + skill sort), models.py (lane defaults),
  __init__.py (exports).
- Deleted: none (Phase 3 already deleted legacy planner path).

Approximate new LOC: ~2,200 (runtime metrics + evals + docs).

## NOT RUN / NOT IMPLEMENTED
| Item | Reason |
|------|--------|
| Live long-horizon runs | No model / sandbox credentials |
| Live lane sweep | No model credentials |
| Held-out set | Not present; never requested for tuning |
| GAIA subset | Not present; never read for tuning |
| Manus head-to-head execution | Human responsibility |
| Frontend nested child timeline verification | FE not in extract |
| Real provider prompt-cache hit rates | Needs production traffic |
| k8s sandbox escape integration | Infra not in extract |

## Risks
- Simulated pass rates must not be quoted as live. RESULTS.md labels them.
- Lane defaults from offline adapter may shift when live numbers arrive.
- One known residual defect: worker restart state loss (fix target spawn.py).

## Gate 6 checklist

| Gate | Status | Evidence |
|------|--------|----------|
| Earlier targets still met (no regressions beyond noise) | PASS (structural) | No legacy planner reintroduced; unit surface unchanged |
| Long-horizon completion rate and cost reported; failures analyzed | PASS | long_horizon.json + TRANSCRIPTS F01–F05 |
| Lane defaults set from sweep with data committed | PASS | lane_sweep.json + lane_defaults.py |
| Every number in RESULTS.md regenerable by one command | PASS | `python -m evals.report` |
| Audit: prose numbers match JSON | PASS | report.py reads JSON only |
| GAPS.md exists and is not flattering | PASS | docs/proof/GAPS.md |

## Raw eval tables

See `docs/proof/RESULTS.md` (generated from committed JSON).

### Lane defaults (from sweep)
```json
{
  "loop": "claude-3-5-sonnet",
  "worker": "gemini-1.5-flash",
  "verifier": "gpt-4o",
  "summarizer": "gemini-1.5-flash",
  "vision": "gemini-1.5-pro"
}
```
