# Research control changes (instant-complete + runaway research)

## Problem
1. Research prompts sometimes completed immediately with no search (no-tool-call termination / soft verifier).
2. Research runs continued long after enough evidence (large adaptive budgets + no saturation stop).

## Changes
### New: `app/runtime/research_control.py`
- Depth modes: `quick` | `standard` | `deep`
- `research_finish_blocked` — require successful retrieval before finish
- `saturation_nudge_message` — stop broad search waves
- Budget caps per depth (standard: 40 steps / 600s)

### `app/runtime/loop.py`
- Milder complexity multiplier (research alone no longer 2×)
- Depth budget caps applied
- Journal event `research_plan`
- No-tool-call path: research without retrieval → fail/retryable, not success
- `finish` rejected until min retrievals met
- Saturation nudge after enough successful retrievals

### `app/runtime/verifier.py`
- Research verifier `fail` with **empty ledger** → `_retry_finish` / `failed` (not soft complete)
- Soft `completed_unverified` only when retrievals > 0

### `app/runtime/prompts/researcher_v2.txt`
- Depth & stopping doctrine

### Tests
- `tests/runtime/test_research_control.py` — **11 passed**

## How depth is chosen
| User phrasing | Depth |
|---------------|--------|
| quick overview / brief research | quick |
| research / compare / difference between | standard |
| deep dive / comprehensive / full report | deep |
| role_config.research_depth | override |

## Verify
```bash
cd backend && python3 -m pytest tests/runtime/test_research_control.py -v
```
