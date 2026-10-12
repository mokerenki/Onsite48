# Onsite48 Manus-Level Upgrade Report

## Release scope

This release strengthens the existing Onsite48 runtime rather than replacing its durable run ledger, evidence, artifact, Bridge, planner, and frontend foundations. The goal is to make execution more deterministic, replay-safe, evidence-aware, and recoverable while preserving the prior fix for duplicate chat answers and stale New Task restoration.

## Implemented capabilities

### Authoritative lifecycle and replay safety

`backend/app/core/task_state_machine.py` defines explicit lifecycle phases, allowed transitions, event-to-phase mapping, lifecycle snapshots, and bounded event identity deduplication. `backend/app/api/realtime.py` now records lifecycle metadata, rejects invalid late transitions from repainting task state, preserves audit events, stores checkpoints at meaningful runtime boundaries, and ignores replayed events when a producer supplies an event identity.

### Planner quality gates

`backend/app/core/plan_quality.py` provides a deterministic plan critic with confidence dimensions for intent, tool availability, safety, and verification coverage. The planner records `plan_quality` in graph state and requests a bounded replan when a plan is structurally unavailable or contains an unapproved non-idempotent write. This complements the existing plan normalization, Gmail semantic guard, and coverage validator.

### Grounded synthesis and provenance

`backend/app/core/memory_contracts.py` defines evidence references, grounded answer envelopes, layered memory records, working-memory initialization, memory acceptance rules, and grounding reports. Both typed evidence objects and JSON event dictionaries are supported at the boundary. The existing durable `AgentEvidence` and `AgentArtifact` models remain the persistence source of truth.

### Onsite48 Bridge observation contracts

`backend/app/core/bridge_contracts.py` defines structured interactive elements, page observations, action expectations, action receipts, and before/after comparison helpers. The extension now emits stable `observation_id` values and stable `element_id` values in page observations. Existing extension behavior already captures before/after action observations and verification receipts; this release formalizes the contract for backend and evaluator consumers.

### Chat correctness preserved

The prior `Chatinterface.tsx` and realtime persistence fixes remain in the release. A completed task has one user-visible Answer surface: the collapsible Plan and the final Answer are separate presentation units, while completed answers are not duplicated as both an archived turn and an active answer. New Task clears persisted thread state and prevents stale automatic reselection.

### Sidebar and data-state reliability preserved

The shared typed resource state and DataLoader remain in place for loading, error, empty, and retry behavior. The richer per-task icon metadata and category-aware sidebar visual system remain included.

## Validation

| Check | Result |
|---|---:|
| Backend complete pytest suite | **256 passed** |
| Backend targeted Manus foundation/planner suite | **35 passed** |
| Frontend TypeScript | **Passed** |
| Frontend Vitest regression suite | **5 passed** |
| Next.js production build | **Passed; 16 routes generated** |
| Onsite48 Bridge `node --check` | **Passed** |
| Archive integrity | **Verified with `unzip -tq`** |

One initial test exposed that the new grounding helper was typed for Pydantic objects while runtime JSON uses dictionaries. The helper was corrected to normalize both representations, and the complete suite was rerun successfully.

## Operational notes

This release implements the high-value runtime contracts and quality gates. Full Manus-class product parity still requires provider-specific work outside a safe local refactor, including broader connector-specific evidence extraction, production event-stream storage guarantees under concurrent workers, UI timeline surfaces for lifecycle checkpoints, and a large replay/evaluation corpus. The new contracts provide the stable seams for those additions without allowing model output to bypass server-owned policy.