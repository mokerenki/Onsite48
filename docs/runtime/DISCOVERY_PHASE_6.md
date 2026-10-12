# Discovery Phase 6 — Cost, long-horizon reliability, lane selection, proof package

## What was verified
- Runtime is the Phase 5 loop package only (partial extract). Full monorepo (FastAPI app, connectors, k8s, frontend, legacy agents/) is not present in this workspace.
- `loop.py` already tracks `state.cost_usd`, `state.token_count`, step budgets, wall time, and child cost rollup via SpawnManager.
- `models.py` attempts ModelRouter with role/lane overrides; cost comes from response.cost_usd when available.
- ContextBuilder + Journal support append-only history; compaction exists in context.py (restorable summaries).
- No `lane_sweep.py`, no long-horizon task suite, no `docs/proof/`, no cost-efficiency A/B harness, no static dashboard yet.
- Eval baseline (EVAL_BASELINE_DEV.json): overall pass@1 0.268 offline; reliability 0.85; computer tiers near 0.
- Live model credentials, browser credentials, Postgres, Redis, Qdrant are NOT available in this environment → live long-horizon runs, lane sweeps against frontier models, and GAIA are NOT RUN.

## What differs from the phase prompt
- No held-out set, no GAIA files, no Manus API access for head-to-head.
- No committed historical eval JSON trajectory beyond baseline.
- Model router is a soft import; pricing table may be absent → cost estimates use documented fallback rates.
- Frontend replay UI not in extract; demo script references it structurally.

## Plan
1. Long-horizon: 8 dev tasks (lh-*) forcing 50+ steps / multi-compaction / approval pause+resume / worker restart. Harness + metrics reporter. Simulated runner for structural proof; live marked NOT RUN.
2. Cost/efficiency: instrument prompt-cache hit (when provider exposes), tokens/step, steps/task; fix unstable prefixes, tool-output truncation, redundant reads. Before/after metrics on same slice.
3. Lane sweep: `backend/evals/lane_sweep.py` over loop/worker/verifier/summarizer/vision lanes × supported models; commit results JSON; set defaults from data.
4. Proof package under `docs/proof/`: RESULTS.md, TRANSCRIPTS/, HEAD_TO_HEAD.md, SECURITY.md, ARCHITECTURE.md, GAPS.md, demo script, static dashboard from runner JSON.
5. Regenerable numbers: one command `python -m evals.report` rebuilds tables from committed JSON.
6. PHASE_6_REPORT.md with gates, line counts, NOT RUN list.

## Risks
- Without live LLM, long-horizon completion rates are simulated (deterministic adapter) and must be labeled as such.
- Lane defaults set from simulated/offline data + documented frontier reference numbers; real sweep requires credentials.
- Investor proof package is honest about gaps and non-flattering.
