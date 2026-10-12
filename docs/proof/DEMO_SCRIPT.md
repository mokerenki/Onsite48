# Demo script — 5 live demos for diligence

Each demo maps to an eval. Recorded replays use the existing execution timeline /
replay UI when the full frontend is available.

## 1. Reliability — kill and resume

- **Eval**: `lh-idempotent-resume-001` / `lh-worker-restart-midrun-001`
- **Script**: Start a 30-event calendar batch. After ~15 events, kill the worker
  process. Resume from the journal. Show event ledger has exactly 30 unique ids.
- **What to show**: journal events, idempotency keys, no duplicates.
- **Replay**: TaskReplay of the resumed run.

## 2. Deliverable quality

- **Eval**: deliverable tier (`del-*` from Phase 4)
- **Script**: "Create a 6-slide pitch deck summarizing Q3 metrics from this CSV."
  Agent produces pptx, runs render_preview + lint, fixes one issue, hands over.
- **What to show**: before/after lint, final file download.
- **Replay**: timeline with deliverable inspect steps.

## 3. Wide research

- **Eval**: `sa-wide-50-headline-001` / `sa-research-4topics-001`
- **Script**: "Map 50 recent headlines on topic X; return top 10 with sources."
  Fan-out via `wide_map`, partial failures labeled, reduce step produces ranked list.
- **What to show**: child run count, cost rollup, final ranked markdown.
- **Replay**: nested child runs in timeline (when FE nesting ships).

## 4. Browser with takeover

- **Eval**: browser v4 login-wall / takeover tasks (`br-v4-*`)
- **Script**: Navigate to a site behind login. Agent hits login wall, requests
  takeover, human completes login, agent resumes and extracts the target page.
- **What to show**: protocol receipts, stale recovery, approval/takeover event.
- **Replay**: browser operator steps with screenshots.

## 5. Long-horizon + approval pause

- **Eval**: `lh-approval-pause-resume-001` / `lh-compaction-50steps-001`
- **Script**: Draft formal letter → send tool triggers approval → leave paused
  for minutes/hours → approve → resume → confirmation in `sent_log.md`.
  Optionally show compaction events on a 50-step notebook task.
- **What to show**: durable pause, resume, cost/step metrics, compaction count.
- **Replay**: full journal from start through resume.

## Recording checklist

- [ ] Start with a clean workspace
- [ ] Enable tracing / journal
- [ ] Capture wall clock
- [ ] Export replay JSON from the UI
- [ ] Note model + lane defaults used
- [ ] Do not re-run failures until after the recording set is frozen
