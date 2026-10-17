# Frontend F0–F2 progress

**Date:** 2026-10-17

## On main

| Item | Path |
|------|------|
| Silence watchdog hook | `frontend/src/hooks/useTaskWatchdog.ts` |
| Watchdog unit tests | `frontend/src/hooks/useTaskWatchdog.test.ts` |
| Error catalog | `frontend/src/lib/taskErrorCatalog.ts` (+ tests) |
| Research depth helpers | `frontend/src/lib/researchDepth.ts` (+ tests) |
| Stream catalog + unknown pass-through | `frontend/src/lib/streamEvent.ts` |
| Extended regression tests | `frontend/src/lib/streamEvent.regression.test.ts` |

### streamEvent contract changes

- Known types added: `mode_applied`, `step_assessment`, `step_retry`, `plan_revised`, `task_state`, `task_execution_interrupted`.
- Unknown event types now **accepted** as opaque frames (`ok: true`) so the UI can render a muted line (was: dropped).
- New optional fields: `research_depth`, `verdict`, `confidence`, `attempt`, `diff`.

### Regression

- Replaced “drop unknown” test with “accept unknown as opaque”.
- Added phase-event, partial status, and error_code catalog tests.

## Pending: Chatinterface.tsx (~129KB)

GitHub file API size limits prevented pushing the full chat UI patch in this session. Local patch applies these behaviors:

1. Import `useTaskWatchdog`, `taskErrorCatalog`, `researchDepth`.
2. State: `researchDepth`, `appliedDepth`, `taskEventSeq`, `silenceNotice`, `timelineNotes`.
3. Bump `taskEventSeq` on task progress events (not pong).
4. Cases: `mode_applied`, `step_retry`, `step_assessment`, `plan_revised`; unknown → muted timeline note.
5. `research_depth` on `run_task` / `continue_task` / `retry_task` / `resume_task` via `withResearchDepth`.
6. Composer: Quick/Standard/Deep selector + silence banner + connection-lost card.
7. Partial badge: “Partial — hit time/step budget”.
8. `task_error` uses catalog + `trace_id`.
9. `console.info("onsite48.trace", …)` on `task_complete`.

### Not done yet (F3–F8)

- Phased plan rendering / plan_revised diff counts in UI (events accepted; rich render partial).
- Artifact CSV preview + dedupe, intervention card polish, Sources/Flagged markdown, artifact-first gallery, cost_metrics one-liner.

## Verify

```bash
cd frontend && npm test
```

Expect streamEvent regression + helper tests green once deps installed.
