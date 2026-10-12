# Merged runtime: Phase 4 base + Phase 5 + Phase 6

This tree is the full Onsite48 codebase from the Phase 4 snapshot, with the
Phase 5 sub-agent runtime and Phase 6 cost/long-horizon/proof package overlaid.

## Overlay order
1. **Phase 4** full monorepo (FastAPI, frontend, extension, k8s, tools, services, agents, …)
2. **Phase 5** → `backend/app/runtime/` (spawn, roles, locks, prompts), `tests/runtime/`, `evals/tasks/dev/sa-*`, docs
3. **Phase 6** → `metrics.py`, `lane_defaults.py`, loop/context/models updates, `evals/{long_horizon,lane_sweep,report}.py`, `lh-*` tasks, `docs/proof/`

## Activation
```
AGENT_RUNTIME=loop   # default path for the new one-tool-calling-loop runtime
```
Legacy `graph.py` / `agents/` remain in the tree for reference and any residual
wiring; the Phase 3 cutover made the loop the default execution engine.

## Run
See root `README.md` and `.env.example`. Offline evals (no model keys required):
```bash
cd backend
PYTHONPATH=. python -m evals.long_horizon
PYTHONPATH=. python -m evals.lane_sweep
PYTHONPATH=. python -m evals.report
```
