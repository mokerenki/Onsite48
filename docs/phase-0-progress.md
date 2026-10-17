# Phase 0 Progress Report

**Repo:** `mokerenki/onsite48`  
**Date:** 2026-10-17  
**Scope completed:** 0.1 (full), 0.2 (worker side), 0.3 (full)  
**Still pending in Phase 0:** main.py lifespan sweeper wire-up, 0.4 timeouts, 0.5 research_depth, tests

---

## Conflicts previously reported (still open for 0.4 / 0.5)

| Assumption in prompt | Reality on disk |
|---|---|
| `select_fast_route` in `agents/router.py` | Lives in `app.services.fast_answer` |
| `research_depth` end-to-end | **Does not exist** anywhere in the repo |
| `initial_task_budgets()` has max_steps / wall / subagents | Only returns planner/executed counters |
| `model_complete()` with unbounded `asyncio.wait` | Function is named `complete()`; no such wait pattern |

Per Ground Rule 6 these were reported before coding. 0.1–0.3 matched the code and were implemented faithfully.

---

## Commits landed

1. `6acdbdd` — **Phase 0.1:** new `backend/app/services/orphan_sweeper.py`
2. `e144563` — **Phase 0.1:** `task_queue_consumer.py` writes heartbeat each poll
3. `8659987` — **Phase 0.1:** `docker-compose.yml` task-worker healthcheck → `onsite48:task_worker:heartbeat`
4. `36eeade` — **Phase 0.2 + 0.3:** `task_worker_entrypoint.py` starts sweeper + records failure on any exception

---

## Per-file changes

### NEW: `backend/app/services/orphan_sweeper.py`
- `SWEEP_INTERVAL_SECONDS` (env, default 60)
- `requeue_lapsed_queued_claims(session_factory, limit=50)` — QUEUED + lapsed lease → `release_for_retry(..., reason="lapsed claim reaped by orphan sweeper")` with `SKIP LOCKED`
- `sweep_once` — calls `enforce_timeouts` + requeue half, each in its own try/except; returns `{"timed_out", "requeued"}`; emits metrics
- `run_orphan_sweeper` / `start_orphan_sweeper` — jittered loop (0.8–1.2×)
- `write_task_worker_heartbeat(redis_client, worker_id)` — `SET onsite48:task_worker:heartbeat "<id>:<iso>" EX 90` (TTL env-overridable), exception-swallowing

### EDIT: `backend/app/services/task_queue_consumer.py`
- `consume_forever` now accepts optional `worker_id`
- Opens a Redis client from `REDIS_URL` (same pattern as task_queue.py)
- Calls `write_task_worker_heartbeat` at the top of every poll iteration
- Closes the client in `finally` on shutdown

### EDIT: `docker-compose.yml`
- `task-worker` healthcheck changed from `onsite48:proactive_worker:heartbeat` → `onsite48:task_worker:heartbeat`

### EDIT: `backend/app/services/task_worker_entrypoint.py`
**0.2 — sweeper startup**
- After `async with lifespan(app):`, creates `sweeper_stop = asyncio.Event()`, starts `start_orphan_sweeper(...)`
- In `finally`: sets the event and `await asyncio.gather(sweeper, return_exceptions=True)`
- Passes `worker_id=WORKER_ID` into `consume_forever` so the heartbeat is written

**0.3 — fail on any exception**
- New helper `_fail_claimed_task(session_factory, claim, exc)`:
  - Opens session, loads TaskThread
  - If already terminal → return
  - `record_event(..., {type: task_error, status: failed, retryable: True, error_code: worker_execution_failed, message: "The task stopped because of an internal execution error. You can run it again."})`
  - Belt-and-braces: `task.status = FAILED`, `task.completed_at = now`, commit
  - Entire helper wrapped in try/except so cleanup never masks the original error or kills the worker
- `execute_claim` body wrapped in `try/except Exception`:
  - Logs `task_worker.claim_execution_failed` with `error_type`
  - `metrics.inc("onsite48_task_failed_total", labels={"worker": WORKER_ID})`
  - Calls `_fail_claimed_task`
  - **Re-raises** so `consume_forever` still counts the dispatch attempt
- Happy path (terminal event mapping, heartbeat) preserved exactly

---

## Acceptance criteria status

| Criterion | Status |
|---|---|
| `docker compose up -d task-worker` shows (healthy) | **Needs runtime verify** — healthcheck key fixed; heartbeat written every poll |
| `redis-cli GET onsite48:task_worker:heartbeat` returns fresh value | **Needs runtime verify** |
| Killing a worker mid-task fails the row within ~2 sweep intervals | **Needs runtime verify** (sweeper + enforce_timeouts now running in worker) |
| Both processes log `orphan_sweeper.started` | **Worker: yes.** API process: **pending** (main.py not yet wired) |
| `loop.run` raises → row FAILED + `task_error(worker_execution_failed)` + exception propagates | **Code complete;** unit test still to add |

---

## Remaining Phase 0 work

1. **0.2 (API side):** start the same orphan sweeper in `main.py` lifespan; store on `app.state`; cancel + gather in cleanup before `onsite48.close()`.
2. **0.4:** bound tool and model awaits (needs careful loop.py / models.py inspection — current code diverges from prompt assumptions).
3. **0.5:** wire `research_depth` end-to-end (feature does not exist yet; will be introduced with documented adaptation).
4. **Tests:** `test_orphan_sweeper.py`, `test_execute_claim_failure.py`, plus existing suite green.

---

## Evaluation of modifications so far

**Correctness**
- Sweeper isolates the two recovery halves so one broken path cannot kill the cycle.
- Heartbeat is best-effort and never blocks the poll loop.
- Failure recording is belt-and-braces and never suppresses the original exception.
- Healthcheck now points at a key this process actually writes.

**Risks / notes**
- Both the API process and the task-worker will run a sweeper once main.py is wired. That is intentional and safe (`SKIP LOCKED` + independent try/except).
- `ClaimedTask` does not yet carry `research_depth`; the entrypoint already uses `getattr(claim, "research_depth", None)` so 0.5 can land without another entrypoint change.
- No tests have been added or run yet — Phase 0 is not gated until the suite is green.

**Next action:** wire sweeper into `main.py` lifespan, then tackle 0.4 / 0.5 with explicit deviation notes.
