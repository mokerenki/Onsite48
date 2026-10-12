# Onsite48 Bridge Manus-Parity Implementation

## Scope

This change follows the browser operator path end to end:

`planner → executor/browser session runtime → bridge tool → bridge queue/authorization → extension tab resolver → content-script action → evidence/observation`

The goal is **focused-tab session behavior by default**: the extension operates on the public tab the user is currently viewing at command execution time, without requiring a persistent tab lock. Explicit `task_tab` / `selected_only` behavior remains available where a task needs a bounded target.

## Root causes found

1. **The extension was not the only source of tab binding.** The backend bridge queue always resolved `_recent_context()` for page commands and injected `target_tab`, so a fresh active-tab selection could still be rejected as stale or missing.
2. **The browser runtime treated `primary_tab_id` as universal.** `BrowserSessionState.begin_step()` passed it into scope enforcement even for `focused_tab` sessions.
3. **The planner did not select the dedicated media capability.** A request such as “pause the YouTube video” could fall through to generic locate/click planning, even though `bridge_control_media` already existed and correctly used HTML5 media APIs.
4. **Origin scope needed to remain closed after removing target-tab binding.** Focused sessions now carry their server-authorized origins to the extension, which rejects a focused tab outside that scope before executing a command.

## Implemented contract

### Focused-tab sessions

- The persisted browser session mode is server-derived and propagated into executor state and bridge payloads.
- For `focused_tab`, the bridge queue does not require `_recent_context()` and does not inject a stale `target_tab`.
- The extension resolves `chrome.tabs.query({ active: true, currentWindow: true })` at execution time.
- The extension still requires explicit Chrome site permission and validates the current tab against the session's authorized origins.
- Changing focus between commands changes the target; it does not create or refresh a persistent lock.

### Task-tab sessions

- The existing stronger primary-tab/session-tab invariant remains available for `task_tab` sessions.
- Explicit selected-only extension mode remains available as a precision/legacy workflow.

### Media control

- Planner policy recognizes `pause`, `stop`, `play`, `resume`, and `toggle` requests involving YouTube, video, audio, media, playback, songs, or tracks.
- These requests deterministically produce `bridge_control_media` steps.
- The extension chooses the visible HTML5 media element, pauses all HTML media nodes for pause/toggle-stop behavior, and returns before/after playback evidence.
- The executor performs a bounded fresh inspection after media actions.

## Files changed

- `backend/app/core/browser_runtime.py`
- `backend/app/api/realtime.py`
- `backend/app/agents/executor.py`
- `backend/app/agents/planner.py`
- `backend/app/services/bridge_summary.py`
- `backend/app/services/browser_bridge.py`
- `backend/app/tools/browser_bridge_tools.py`
- `extension/onsite48-bridge/background.js`
- `backend/tests/test_phase0_security_remediation.py`
- `backend/tests/test_bridge_summary.py`
- `backend/tests/test_bridge_operator_permissions.py`

## Verification

- `PYTHONPATH=backend python3 -m pytest -q backend/tests/test_bridge_summary.py backend/tests/test_phase0_security_remediation.py backend/tests/test_bridge_operator_permissions.py`
  - **82 passed**, 1 pre-existing deprecation warning from `passlib`.
- `python3 -m compileall -q backend/app backend/tests` — passed.
- `node --check extension/onsite48-bridge/background.js` — passed.
- `node --check extension/onsite48-bridge/popup.js` — passed.
- `node --test extension/onsite48-bridge/tests/protocol.test.mjs` — **7 passed**.

## Operational smoke test

After loading the unpacked extension or installing the ZIP:

1. Pair the extension and leave **Active tab (default)** enabled.
2. Open a YouTube video and grant site access once from the popup if Chrome asks.
3. Ask the agent: **“Pause the YouTube video.”**
4. Confirm the task uses `bridge_control_media`, the video pauses, and the returned result contains `before`, `after`, and `verified` evidence.
5. Switch to another permitted public tab and run a new read/action; it should follow focus without Sync/Lock.
6. Confirm a tab outside the browser session's authorized origin is rejected closed, rather than silently acted on.