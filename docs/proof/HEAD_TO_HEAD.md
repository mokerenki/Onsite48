# HEAD_TO_HEAD — Onsite48 vs comparable agent (Manus or peer)

## Purpose

A human runs the same 20 tasks on Manus (or another comparable agent) and on
Onsite48, then blind-grades both. This document provides:

1. The grading sheet template.
2. The harness for the Onsite48 side only.
3. Instructions so results are not cherry-picked.

Grok does **not** run the Manus side and does not grade.

## Task set (20)

Use a fixed list agreed before either system is run. Suggested composition
(adjust only before any run begins):

| # | Tier | Description (one-line) |
|---|------|------------------------|
| 1–4 | reliability | Multi-step file ops, resume after interrupt, idempotent mutation, budget stop |
| 5–8 | browser | Login wall → takeover, multi-page extract, stale recovery, form fill |
| 9–12 | deliverables | docx / pptx / xlsx / pdf produce + self-inspect |
| 13–16 | research | 4-topic research, 5-vendor compare, wide headline map, synthesis |
| 17–20 | compound | Email draft+approve, calendar batch, mixed tool chain, cost-guarded clean |

Exact prompts must be frozen in `docs/proof/h2h_tasks.yaml` before runs start.
(File is a placeholder; human fills prompts.)

## Grading sheet template

Copy for each task:

```
Task id: ________
Blind id A / B: ________   (do not reveal which system)

Outcome (0–2):
  0 = failed / wrong / incomplete
  1 = partial (usable with edits)
  2 = complete and correct

Deliverable quality (0–2): same scale, only if a file was produced
Steps / wall time (optional, not graded)
Notes (free text, no system name):

Winner for this task: A / B / tie
```

Aggregate:

| System | Wins | Losses | Ties | Mean outcome | Mean deliverable |
|--------|------|--------|------|--------------|------------------|
| Onsite48 |  |  |  |  |  |
| Peer (Manus/…) |  |  |  |  |  |

Do not drop tasks. Report all 20.

## Onsite48 harness

```bash
cd backend
PYTHONPATH=. python -m evals.h2h_runner --tasks docs/proof/h2h_tasks.yaml --out evals/results/h2h_onsite48.json
```

(`evals/h2h_runner.py` is a thin wrapper over the loop; live requires credentials.
Without credentials: NOT RUN.)

## Current status

- Grading sheet template: above.
- Onsite48 harness: structural (see `evals/long_horizon.py` pattern); live NOT RUN.
- Manus / peer runs: human responsibility.
- Wins / losses / ties: **not yet available** — do not invent.
