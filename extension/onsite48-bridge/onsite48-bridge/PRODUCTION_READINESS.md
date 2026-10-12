
# Production readiness notes — Onsite48 Bridge v3.1 rebuilt patch

This package is the v3.1 rebuilt baseline with a production hardening patch applied.

## Safety invariants

- Browser commands require an explicit task workspace/session.
- The extension never derives execution context from the user's currently focused tab.
- Explicit `session_id` resolution fails closed.
- Context reporting fails closed when no explicit task workspace is bound.
- Transient retry/in-flight state is cleared on service-worker restart; backend leases and terminal receipts remain authoritative.
- High-impact capabilities (`download`, `upload`, `media`) require explicit session capabilities.
- Screenshots are workspace-window scoped and size-limited.
- CAPTCHA/MFA detection can pause the operator and require user takeover/resume.

## Changed from the reuploaded rebuilt zip

- Fixed undefined `sessionId` use in `runCommand()`.
- Removed stale `activeCommand` references and use the `activeCommands` map consistently.
- Fixed service-worker restart deadlock by clearing transient `commandsInFlight` / `commandRetryState` on startup.
- Removed legacy focused-tab fallback from `selectedTabForCommand()`.
- Made `reportActiveContext()` explicit-session only.
- Added capability gating for high-impact commands.
- Removed duplicate root popup files and unused `icon.svg`.
- Added production invariant tests.

## Verification performed

- `node --check` on all extension JS/MJS files.
- Existing protocol test suite: 15/15 passing.
- Added `tests/production-invariants.test.mjs` for the hardening rules above.

## Still recommended before real production rollout

- Run the extension against a staging backend and manually verify create/observe/click/type/screenshot/takeover/resume flows.
- Confirm backend sends `session_capabilities` for `download`, `upload`, and `media` commands.
- Consider splitting `background.js` into transport/runtime/injection modules when doing the next refactor.
