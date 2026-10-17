# Phase 1 — Observation Loop — Complete

**Date:** 2026-10-17  
**Repo:** mokerenki/onsite48  
**Status:** CLOSED

## Plan / checklist

| Step | Status |
|------|--------|
| 1. Define Observation contract (outcome, summary, fingerprint, evidence) | Done |
| 2. Classify tool results (ok / error / timeout / needs_input / cancelled) | Done |
| 3. ObservationTracker for no-progress (identical fingerprints) | Done |
| 4. format_observation_message for model tool turns | Done |
| 5. Wire into ContextBuilder.format_tool_result (every tool path) | Done |
| 6. Untrusted wrap without circular import | Done |
| 7. Unit tests (8 cases) | Done — 8/8 green |
| 8. Re-verify with budget_profiles regression | Done — 12/12 green |

## What shipped

### `backend/app/runtime/observation.py` (new)
- `Observation` dataclass
- `observe_tool_result()` — boundary classifier
- `ObservationTracker.record()` — stagnant detection (default 3 identical fingerprints)
- `format_observation_message()` — model-facing tool message

### `backend/app/runtime/context.py`
- `ContextBuilder` owns an `ObservationTracker` per instance
- `format_tool_result` always produces structured observations
- Logs `runtime.observation.stagnant` when no progress
- Attaches `progress.stagnant` / `fingerprint` into model payload when possible

### Tests
- `backend/tests/runtime/test_observation_loop.py` — 8 tests, all green

## Acceptance

| Criterion | Proof |
|-----------|--------|
| Every tool result is observed | `ContextBuilder.format_tool_result` → `observe_tool_result` |
| Model sees structured outcome + summary | `format_observation_message` payload `observation: true` |
| No-progress detectable | `ObservationTracker` stagnant after N identical fingerprints |
| Untrusted tools still wrapped | local `_wrap_untrusted` in observation module |
| Tests green | `pytest tests/runtime/test_observation_loop.py` → 8 passed |

## Relationship to existing Phase 1 flags

Loop still has `ENABLE_PARALLEL_RESEARCH`, `ENABLE_CONTEXT_SUMMARIZATION`, `ENABLE_STRUCTURED_TOOL_RESULTS` (default off). Those remain opt-in. The observation loop is **always on** via `format_tool_result` — it is the baseline intelligence upgrade for Phase 1.

## Gate

**Phase 1 complete.** Ready for Phase 2 (hierarchical plans).
