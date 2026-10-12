# Merge report: blocking-fixes + patched + gaps A-F

Base: Onsite48-blocking-fixes (full codebase) -> overlay Onsite48-blocking-fixes-patched (top-level files only; its nested onsite48/onsite48 copy is a duplicate subset and was ignored) -> overlay Onsite48-gaps-A-F-remaining-fixed.

## Fixes applied on top of the merge
- Restored backend/evals/fakes/google/app.py (emptied to 0 bytes in the patched zip).
- extension protocol.js: validateCommand now returns the command; added commandAuthority; evidenceEnvelope/isRetryableFailure/lifecycleSnapshot/sessionCommandKey match their background.js call sites; background.js event sessionId uses the active session.
- extension tests/protocol.test.mjs rewritten for v3.1 (15 tests, incl. cross-module import/export contract check).
- Backend wiring: get_execution_identity import path (2 files), task_worker_entrypoint record_event, graph.py session factory, readback registry lookup, unattended-write path containment check in policy.py.
- Tests: test_journal_shape and test_metrics_phase6 fixed; test_trajectory_eval_gate and scripts/run_trajectory_eval.py explicitly marked legacy (planner removed in Phase 3; evals.report helpers gone).
- Alembic: removed 0034_initial_runtime_schema (second root that recreated users/organizations/run_events). Chain is now linear: 20260812_01 -> ... -> 20261003_34.
- YAML: k8s scaffold files converted from docstring headers to comments; heartbeat config operator keys quoted; two eval task files quoted.

## Not verified (no network in the build environment)
- Python pytest suite, frontend build, docker build, and a real `alembic upgrade head` against Postgres were NOT run. Run `cd backend && pytest` and `alembic upgrade head` before release.
- app/runtime/recovery.py maybe_refresh_auth imports a `connector_vault` object that app/services/connector_vault.py does not define; it fails gracefully (no token refresh ever happens).
