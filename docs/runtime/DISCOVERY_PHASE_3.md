# Discovery Phase 3 — Verification, recovery, injection defense, cutover

## Verified state (from Phase 2 zip)

### Runtime loop (backend/app/runtime/)
- `loop.py` (181 LOC): single tool-calling loop. Handles finish, ask_user, policy, basic error wrapping. No automatic readback. Finish accepts status enum completed|partial|failed|needs_user; no independent verifier.
- `tools.py` (465 LOC): ToolSpec already has `readback: Callable | None = None` field but **nothing populates or invokes it**. Classification of side_effect/trust exists. Workspace tools: shell_exec, file_*, python_exec, deliver_file, load_skill. No readback for write/deliver/send.
- `policy.py` (100 LOC): ALLOW / APPROVAL / DENY. No argument provenance tracking yet.
- `context.py`: `wrap_untrusted` exists and is applied when `spec.trust == "untrusted"`. System prompt already says tool output is untrusted data.
- `journal.py`: event types already include `readback_result`. RunState has finish_payload; status values are limited (finished/failed/needs_user etc.).
- `models.py`: lanes include "verifier".
- Prompts: `system_v1.txt` (12 lines) — basic operating rules.

### Legacy brain still present
- `agents/plans/` ~1,779 LOC (gmail.py 769 alone)
- `agents/planner.py` ~106k, `executor.py` ~111k, `verifier.py` ~57k
- `core/compound_intent.py`, `intent_resolution.py`, `goal_contract.py`, capability layers
- `graph.py` still wires planner/executor/verifier
- `AGENT_RUNTIME` defaults to legacy (loop is opt-in)

### What differs from the Phase 3 prompt
1. readback field exists on ToolSpec but is dead — must implement and wire.
2. No recovery.py / failure classifier.
3. No independent completion verifier lane.
4. Terminal states incomplete (needs completed_unverified, waiting_approval, cancelled).
5. Provenance for policy arguments not implemented.
6. No injection evals.
7. Cutover not done; legacy is still default.

## Plan
1. Implement ToolSpec.readback for every write / external_send / destructive / deliver tool; auto-run after mutation; journal readback_result.
2. Independent verifier on finish (fresh context, read-only tools, strict JSON verdict).
3. recovery.py: deterministic failure classification + loop detection.
4. Honest terminal states + matching user-facing messages.
5. Argument provenance in policy; strengthen wrap_untrusted + system prompt.
6. 12 injection evals (dev) + 4 adversarial honesty evals.
7. Set AGENT_RUNTIME=loop default; delete legacy brain after gate evidence; report line counts.
8. Tests for readback, recovery, verifier, provenance, terminal states.
9. PHASE_3_REPORT.md with gates.

## Risks / NOT RUN
- Full live evals need model API keys and connector credentials → will run offline/unit and document NOT RUN for live held-out.
- False-completion audit of 40 runs requires eval runner + model → structural tests + synthetic adversarial cases.
- Deleting legacy may break imports in graph/main; clean carefully.
