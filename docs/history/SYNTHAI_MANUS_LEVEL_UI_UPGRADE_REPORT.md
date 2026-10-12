# Onsite48 Manus-Level UI/UX and Agent Workspace Upgrade Report

**Date:** 2026-09-15  
**Scope:** Attached implementation brief covering task presentation, sidebar navigation, ChatInterface conversation navigation, Bridge lifecycle UX, durable conversation identity, and production validation.

## Executive summary

Onsite48 now has a stronger task-oriented workspace foundation. New tasks receive concise semantic titles and typed presentation metadata, legacy task records receive safe presentation fallbacks, the sidebar groups and searches tasks, conversation turns receive durable IDs, the active task header uses the concise task identity, long conversations provide navigation and jump-to-latest controls, and Onsite48 Bridge device responses expose server-derived availability and lifecycle state.

The implementation preserved the existing execution, WebSocket, approval, browser-session, and extension protocols. No fake data or mock backend behavior was introduced.

## Modified

| Area | Files |
|---|---|
| Task presentation | `backend/app/core/task_presentation.py`, `backend/app/api/product_tasks.py`, `backend/app/api/realtime.py`, `backend/app/services/scheduled_task_runner.py` |
| Conversation identity | `backend/app/services/task_persistence.py`, `frontend/src/components/Chatinterface.tsx` |
| Workspace and task identity | `frontend/src/app/page.tsx`, `frontend/src/components/Chatinterface.tsx` |
| Sidebar | `frontend/src/components/Sidebar.tsx` |
| Bridge lifecycle | `backend/app/api/browser_bridge.py`, `frontend/src/app/bridge/page.tsx` |
| Tests | `backend/tests/test_task_presentation.py` |

## Added

### Backend

`backend/app/core/task_presentation.py` provides deterministic, side-effect-free helpers for:

- Concise task-title generation.
- Semantic category and icon assignment.
- Safe presentation metadata storage in existing `execution_metadata` JSON.
- Legacy-task presentation inference.
- Legacy raw-prompt title detection and fallback retitling in API summaries.

`backend/tests/test_task_presentation.py` covers long prompts, semantic Gmail classification, legacy raw-prompt fallback, and preservation of explicit short titles.

### Frontend

The sidebar now contains:

- Server metadata-first icon selection with client fallback classification.
- Semantic icons separate from lifecycle status dots.
- Today, Yesterday, and Earlier task grouping based on backend timestamps.
- Search across task title, category, and domain.
- Progressive status filtering: All, Running, Waiting, Completed, and Failed.
- One-line task labels with full-title tooltip behavior.
- Task turn-count metadata.
- Existing loading, empty, error, retry, selection, and deletion behavior preserved.

The ChatInterface now contains:

- Concise active-task title support.
- Expandable original-request context.
- Durable turn IDs when supplied by the backend, with legacy fallback IDs for old records.
- Stable `turn-*` DOM anchors and `data-turn-id` attributes.
- Conversation navigator for multi-turn conversations.
- IntersectionObserver-based active-turn highlighting.
- Jump-to-latest behavior that respects manual upward scrolling.

## Backend/API changes

### Task summaries

`TaskSummary` now exposes a `presentation` object containing category, icon key, icon color, and title-source metadata. The existing title, prompt, domain, status, timestamps, and turn count remain intact.

The presentation object is stored inside existing `TaskThread.execution_metadata`, avoiding a schema migration while maintaining backward compatibility. Existing tasks without metadata are classified at serialization time.

New REST, WebSocket, and scheduled task creation paths now use concise titles and attach semantic presentation metadata. The original prompt remains unchanged and continues to be the execution source of truth.

### Conversation persistence

Newly appended conversation entries include a `turn_id` alongside role, content, and timestamp. Older conversation entries without IDs remain readable and receive deterministic frontend fallback IDs during restoration.

### Bridge device lifecycle

Bridge device summaries now include:

- `lifecycle`: connected, offline, pending, revoked, or the underlying fallback state.
- `is_available`: server-derived availability.
- `last_seen_label`: a minimal freshness label.

A device is only considered available when it is connected and its heartbeat is fresh. Revoked and stale records remain available for history/audit but are no longer treated as active control targets by the Bridge page.

## Frontend changes

### Sidebar

The sidebar no longer depends primarily on raw prompt text or status-based icons. It uses semantic task presentation metadata when available and falls back to deterministic classification for legacy tasks. The task title is single-line and bounded visually, while the full value remains discoverable through the browser tooltip and the active task context.

### Chat workspace

The active header now prefers the concise task title and exposes the original user request through progressive disclosure. Long conversations can be navigated by stable question entries rather than endless scrolling. When a user reads older content while streaming continues, automatic scrolling stops and a **Jump to latest** affordance becomes available.

### Bridge page

The paired-browser section now behaves as a browser control center. Available browsers receive a connected presentation with current-tab context, capabilities, preference controls, disconnect behavior, and refresh status. Offline, pending, and revoked devices are separated into an expandable historical section rather than mixed with active browsers.

## Extension changes

The extension source was not functionally modified in this pass because the attached brief’s requested web Bridge lifecycle improvements could be implemented against the existing extension/backend protocol without changing command transport or security boundaries. The extension JavaScript syntax was validated successfully.

The existing extension behavior remains the source of truth for pairing, authentication, operator state, leases, retries, approval handling, evidence, and command execution. A future extension UI pass should consume the same lifecycle vocabulary introduced here: Browser connected, Browser offline, Current tab, Pinned tab, and Requires approval.

## Security and compatibility

The upgrade preserves:

- Existing pairing and authentication.
- Hostname-bound trust rules.
- Capability restrictions.
- Approval boundaries.
- Command leases and stale-claim recovery.
- WebSocket event identity and lifecycle guards.
- Existing task ownership and organization scoping.
- Existing task deletion, restoration, pause, resume, stop, and browser-session flows.

No cookies, passwords, session tokens, page text, screenshots, or typed values were added to general Bridge summaries.

## Testing and verification

| Check | Result |
|---|---|
| Python compilation of modified modules | Passed |
| Extension JavaScript syntax check | Passed |
| Focused backend tests before final additions | 35 passed |
| Full backend suite before final additions | 256 passed |
| New task-presentation tests | 3 passed |
| Final full backend suite | **259 passed**, 1 existing deprecation warning |
| Frontend regression tests | **5 passed** |
| Standalone TypeScript check after build settled | Passed |
| Next.js production build | Passed |
| Next.js integrated lint/type validation | Passed |

The one transient standalone TypeScript failure occurred when it ran concurrently with Next.js while `.next/types` was being regenerated. It was rerun after the build completed and passed. It was an environment/build-order race, not a source error.

## Known limitations

The current change set does not yet implement every item in the full brief. Specifically:

- Full task-level activity timeline normalization is not yet exposed as a dedicated UI component.
- Execution steps still use the existing event-derived plan/result presentation rather than a complete extracted `ExecutionTimeline` component hierarchy.
- Sidebar task action menus beyond existing deletion are not added because the backend does not currently expose all requested actions such as rename, share, or duplicate.
- No browser-based visual/E2E run was performed against authenticated external services in this sandbox. Automated compilation, builds, unit/regression suites, and protocol-level checks passed.
- The extension popup was syntax-checked but not visually redesigned in this pass.

These limitations are intentionally documented rather than presented as completed functionality.