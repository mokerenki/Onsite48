# Onsite48 Manus-Level Implementation Report

## Scope

This release addresses the previously identified frontend reliability defects and the highest-impact planner capability failure: treating a request for Gmail draft counts as a request for recent received messages. The changes are implemented in the supplied Onsite48 codebase and validated with frontend type/build checks plus focused backend planner/connector tests.

## Implemented changes

### Shared typed data-resource state

Added `frontend/src/hooks/useDataResource.ts`, which models resource state as `loading`, `success`, or `error`, preserves the last successful value during refresh, cancels stale requests, and exposes a stable retry operation. Added `frontend/src/components/DataLoader.tsx`, which renders loading skeletons, actionable errors, successful empty states, data, and stale-data warnings consistently.

The home workspace now uses the resource hook for recent tasks. The sidebar receives explicit loading/error/retry props and no longer renders “No tasks yet” while the request is pending or after a failed request. Onsite48 Bridge now loads devices, trust rules, and commands independently, with a separate retryable state for each section; partial failure is visible instead of being mistaken for an empty result.

### Gmail capability and planner semantics

Added dedicated `gmail_count_drafts` and `gmail_list_drafts` connector tools. Counting paginates through Gmail drafts and returns `{resource, operation, count, exact}`. Listing returns structured draft metadata and pagination fields. The tools are registered in the shared capability catalog and explicitly documented as distinct from received-message search.

Added typed Gmail resource/operation intent parsing for draft count, draft list, draft creation, and message-list intent. The planner now deterministically routes draft-count and draft-list requests to the correct capabilities. A semantic guard rejects plans that select received-message search for a draft or exact-count request, including model-generated plans.

### Verification contract hardening

Postconditions now preserve a `field` reference during Pydantic normalization, allowing field-existence and equality postconditions to remain executable instead of silently discarding their target field. Gmail draft count/list plans include output schemas and postconditions for resource, operation, and result fields.

## Validation

| Check | Result |
|---|---|
| Frontend TypeScript (`tsc --noEmit`) | Passed |
| Frontend Vitest regressions | Passed: 5 tests |
| Frontend Next production build | Passed: 16 app pages and pages-router routes generated |
| Backend Python compileall | Passed |
| Focused backend planner/connector tests | Passed: 23 tests |
| Complete backend suite with Playwright Chromium installed | Passed: 247 tests |

The Playwright Chromium runtime was installed in the validation environment so the computer-use tests exercised their browser path. The run emitted one pre-existing `passlib` deprecation warning.

The backend run emitted one pre-existing dependency deprecation warning from `passlib`; it did not affect test results.

## Important product behavior now guaranteed

A request such as “How many drafts do I have?” is routed to an exact draft-count capability. A request such as “Show my latest 4 drafts” is routed to draft listing. A request such as “Show my latest 4 emails” remains routed to received-message search. A failed task or Bridge fetch is shown as an error with retry, not as an empty dataset.

## Remaining production rollout work

Provider integration tests should be run with a mocked Gmail API and, separately, a sandbox Gmail account before release. OAuth scopes must be re-authorized for existing users if the production connector configuration changes. The broader Manus-level roadmap still contains larger initiatives—long-horizon memory, richer computer-use recovery, evaluation replay, and production observability—that are not safely implementable without their deployment contracts and live provider/runtime fixtures; this release focuses on the concrete defects and contracts verified here.