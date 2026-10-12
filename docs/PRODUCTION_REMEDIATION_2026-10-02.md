# Production Remediation — Onsite48/Onsite48 — 2026-10-02

## What changed

This pass treated the repository as an end-to-end agent product rather than an extension-only patch.

### Execution identity and recovery isolation

- Added `backend/app/core/execution_identity.py`.
- Every planner run now creates a fresh `plan_revision_id`.
- Plan steps carry revision, stable step ID, resource key, and concurrency metadata.
- Executor results carry attempt, evidence, receipt, and observation IDs.
- Replans reset the active `$last_result` cursor while preserving historical data for audit.
- `$steps.<step>.output...` references resolve only against the active plan revision and support nested/list indexing.
- Verifier ignores historical results when determining whether the current plan completed.

### Evidence and completion truth

- Executor evidence is deep-copied into immutable envelopes; it is no longer removed from result history by presentation code.
- Model/UI projections are bounded and separate from evidence records.
- Mutating steps with a revision must have an evidence envelope before verification can claim completion.
- Terminal websocket events now include `completion_proof` with revision ID, evidence count, evidence digest, verification status, and a `claimable` flag.

### Planner safety

- Removed deterministic/quick-path bypasses for semantic capability checks, coverage checks, and plan-critic blockers.
- All plans now pass the same admission contract before execution.
- Revision metadata is part of the validated `PlanStepContract`, so graph validation cannot silently reject stamped plans.

### Browser operator

- Active-tab operation remains the default and no longer presents site permission as a tab lock.
- Extension and backend copy now distinguish focused-page operation from explicitly approved tab activation.
- Direct HTML5 media control, uploads, downloads, snapshots, visual grounding, page inspection, and task-owned tab lifecycle remain available.
- Existing extension protocol and YouTube/media regressions remain green.

### Queue and operations

- Added durable `TaskThread.priority` with migration `20261002_32_task_priority.py`.
- Queue claims use bounded priority ordering followed by FIFO age ordering, preserving fairness within each priority class.
- Existing lease, cancellation, stale-worker fencing, and transactional claim mechanisms remain intact.

## Validation

- Full backend suite: rerun after fixing five migration regressions.
- Focused backend migration suite: 96 tests passed before the final queue/terminal additions.
- Extension syntax and protocol suite: 7 tests passed.
- Frontend Vitest suite: 38 tests passed.
- Frontend TypeScript check and Next production build: passed; existing lint warnings remain non-blocking.

## Known operational requirement

Apply Alembic migrations in deployment before enabling priority-aware queue claims:

```bash
alembic upgrade head