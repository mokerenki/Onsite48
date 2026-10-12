# RESULTS — Onsite48 Runtime Proof Package

All numbers below are regenerable by:
```
cd backend && PYTHONPATH=. python -m evals.report
```
Pinned inputs: committed JSON under `backend/evals/results/` and
`docs/runtime/EVAL_BASELINE_DEV.json`. Seeds are deterministic in the
offline adapter. Live model runs are marked NOT RUN when credentials
are absent.

## 1. Baseline (legacy offline adapter)

| Slice | n | pass@1 | SE | median steps | mean cost |
|-------|---|--------|----|--------------|-----------|
| overall (legacy) | 82 | 0.268 | 0.049 | 0 | 0.0000 |
| reliability | 20 | 0.850 | 0.080 | — | — |
| general | 12 | 0.000 | 0.000 | — | — |
| deliverables | 14 | 0.000 | 0.000 | — | — |
| research | 10 | 0.000 | 0.000 | — | — |
| data | 8 | 0.000 | 0.000 | — | — |
| browser | 12 | 0.167 | 0.108 | — | — |
| compound | 6 | 0.500 | 0.204 | — | — |

_Note: Offline deterministic adapter baseline; computer tiers expected near zero_

## 2. Long-horizon reliability (Phase 6a)

| Metric | Value |
|--------|-------|
| Tasks | 8 |
| Passed | 7 |
| Completion rate | 0.875 ± 0.117 |
| Mean cost USD | 0.0664 |
| Cost per success | 0.069329 |
| Mean steps | 32.0 |
| Mean tokens/step | 1530.2 |
| Mean cache hit rate | 0.2425 |

### Per-task

| run (truncated) | success | steps | cost | compactions | pauses | restarts | failure_cause |
|---------------|---------|-------|------|-------------|--------|----------|---------------|
| 474eae58 | True | 19 | $0.0401 | 0 | 1 | 0 | — |
| 4204671f | True | 34 | $0.0734 | 0 | 0 | 0 | — |
| f935f31e | True | 53 | $0.1162 | 2 | 0 | 0 | — |
| 46c5d0b1 | True | 25 | $0.0457 | 0 | 0 | 0 | — |
| cd3b61cf | True | 35 | $0.0767 | 0 | 0 | 1 | — |
| 09d40796 | True | 40 | $0.0881 | 3 | 0 | 0 | — |
| 6ae2f934 | True | 25 | $0.0452 | 0 | 0 | 0 | — |
| 947a2a41 | False | 25 | $0.0459 | 0 | 0 | 1 | worker_restart_state_loss |

_Source: simulated offline adapter. Live long-horizon: NOT RUN (no credentials)._

## 3. Cost / efficiency before → after (Phase 6b)

| Metric | Before | After | Δ |
|--------|--------|-------|---|
| Mean cost USD | 0.0515 | 0.0283 | -0.0232 |
| Tokens/step | 2990.0000 | 2300.0000 | -690.0000 |
| Steps/task | 9.8000 | 9.8000 | +0.0000 |
| Cache hit rate | 0.0500 | 0.3800 | +0.3300 |
| Pass rate | 0.4167 | 0.5000 | +0.0833 |

Fixes applied: stable system-prompt prefix, tool-output offload,
redundant-call guard, compaction metrics exposure.

## 4. Lane sweep (Phase 6c)

Timestamp: 2026-10-03T00:15:44Z
Slice: dev

| Lane | Model | pass_rate | cost | latency_ms | tool_validity | frontier | sim |
|------|-------|-----------|------|------------|---------------|----------|-----|
| loop | gpt-4o | 0.833 | 0.1416 | 1580 | 0.990 |  | Y |
| loop | gpt-4o-mini | 0.667 | 0.0212 | 636 | 0.980 |  | Y |
| loop | claude-3-5-sonnet | 0.917 | 0.1512 | 1280 | 0.990 | Y | Y |
| loop | gemini-1.5-pro | 0.833 | 0.1160 | 576 | 0.980 |  | Y |
| worker | gpt-4o-mini | 0.583 | 0.0228 | 924 | 0.970 |  | Y |
| worker | claude-3-haiku | 0.750 | 0.0171 | 684 | 0.970 |  | Y |
| worker | gemini-1.5-flash | 0.750 | 0.0102 | 732 | 0.960 |  | Y |
| worker | gpt-4o | 0.750 | 0.1272 | 1260 | 0.980 | Y | Y |
| verifier | gpt-4o-mini | 0.583 | 0.0204 | 852 | 0.960 |  | Y |
| verifier | claude-3-haiku | 0.667 | 0.0153 | 612 | 0.960 |  | Y |
| verifier | gpt-4o | 0.917 | 0.1368 | 1140 | 0.970 | Y | Y |
| summarizer | gpt-4o-mini | 0.667 | 0.0216 | 888 | 0.990 | Y | Y |
| summarizer | claude-3-haiku | 0.750 | 0.0162 | 648 | 0.990 |  | Y |
| summarizer | gemini-1.5-flash | 0.750 | 0.0116 | 696 | 0.980 |  | Y |
| vision | gpt-4o | 0.750 | 0.1224 | 1220 | 0.960 | Y | Y |
| vision | gemini-1.5-pro | 0.750 | 0.1000 | 840 | 0.950 |  | Y |

### Defaults chosen from data

```json
{
  "loop": "claude-3-5-sonnet",
  "worker": "gemini-1.5-flash",
  "verifier": "gpt-4o",
  "summarizer": "gemini-1.5-flash",
  "vision": "gemini-1.5-pro"
}
```

Free/cheap endpoints show a measurable ceiling vs frontier reference
(see pass_rate gap in the table). Defaults prefer the Pareto front
(pass_rate, then cost).

_Simulated offline adapter. Live sweep: NOT RUN (no credentials)._

## 5. Held-out and external (GAIA subset)

| Slice | n | pass@1 | SE | notes |
|-------|---|--------|----|-------|
| human-run held-out | — | — | — | NOT RUN — held-out set not present in workspace |
| GAIA subset | — | — | — | NOT RUN — GAIA files not present; never read for tuning |
| frontier reference (live) | — | — | — | NOT RUN — no model credentials |

Dev-versus-held-out gap: **not measurable** until held-out is run.

## Methodology (stranger can rerun)

1. `cd backend && PYTHONPATH=. python -m evals.long_horizon`
2. `PYTHONPATH=. python -m evals.lane_sweep`
3. `PYTHONPATH=. python -m evals.report`
4. Inspect `docs/proof/RESULTS.md` and `evals/results/*.json`.

Versions: runtime Phase 6; offline adapter seeds are pure functions of task id.
