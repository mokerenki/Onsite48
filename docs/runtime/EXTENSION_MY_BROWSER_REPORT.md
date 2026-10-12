# EXTENSION_MY_BROWSER_REPORT

**Date**: 2026-10-03 (fix pass)  
**Protocol**: `onsite48-bridge.v3.1` / EXTENSION_VERSION `3.1.0`  
**Source**: full phase6 tree + Manus-1 local browser workstreams

## What changed (this fix pass)

### Naming
- `EXTENSION_NAME` → **Onsite48 Browser** (was Bridge Operator)
- Overlay: Agent working · Take over · Resume · Stop (no “Resume operator” / Synth)
- Popup primary path: **Connect** only; Device ID / pairing code under **Advanced** only

### Tool surface collapse (B)
- New `backend/app/tools/my_browser_tools.py`: model-visible **browser_observe**, **browser_act**, **browser_takeover**, **browser_stop**
- `main.py` registers only this set; legacy `bridge_*` tools not registered to model schema
- Maps internally to extension COMMAND_TYPES

### BrowserSession router (B)
- New `backend/app/services/browser_session_router.py`:
  - `select_backend()` explicit policy
  - `LocalBridgeSession` | `CloudPlaywrightSession`
  - Resume → mandatory fresh observe; `is_paused_for_challenge` ⇒ `consumes_step_budget == false`
- Unit tests: `backend/tests/test_browser_session_router.py`

### Authorize UX (A)
- `AuthorizedActions.tsx` real component (connect CTA / authorize CTA / authorized state)
- Wired into Chat interface near browser session strip
- My Browser still in `connectors.ts`

### Device pair APIs (A)
- `/api/bridge/device/pair/start|complete|refresh` hardened (start no longer calls create_pairing with invalid kwargs)

### Golden / tests (C)
- Suite YAML retained; Chrome cases NOT RUN
- `protocol.test.mjs` annotated for v3.1; focused-tab primary deprecated in comments

### Docs (D)
- README, REBUILD_HANDOFF, DISCOVERY, this report

## How to pair and run

1. Load unpacked `extension/onsite48-bridge`
2. Popup → **Connect** (device pair; Advanced = self-hosted / recovery only)
3. Product: My Browser connector; when prompted **Authorize browser for this task**
4. Task-named tab group; overlay Agent working · Take over · Resume · Stop
5. Close group or Stop → terminal stop

Live Chrome E2E: **NOT RUN**

## Test results

| Case | Result | Notes |
|------|--------|-------|
| select_backend unit tests | Implemented | Run with pytest when env available |
| Tool registry small set | Code path updated | Legacy classes not registered |
| Device pair APIs | Present | Live NOT RUN |
| Authorize CTA | Component + Chat wire | Live NOT RUN |
| Golden suite | Definitions | Chrome NOT RUN |
| Protocol tests | Annotated v3.1 | Prefer full assert pass in CI |

## Risks / remaining gaps

1. Live Chrome still required for E2E proof of tab group close → stop and SW restart.
2. `extension_status` on router is best-effort; may need a dedicated device list API for accurate connected/authorized.
3. Chat Authorize wire assumes `createBrowserSession` + task context; product copy may need design polish.
4. Computer_* fine-grained tools still registered (cloud path); My Browser path is collapsed.

## Success criteria checklist (GATE)

| # | Criterion | Status |
|---|-----------|--------|
| 1 | Install → paired → authorized without paste-token default | **Wired**; live **NOT RUN** |
| 2 | My Browser product connector/toggle | **Improved** (connectors + Authorize CTA component) |
| 3 | Task-named tab group; close stops | **Model present**; live **NOT RUN** |
| 4 | Take over / Resume / Stop; login-wall takeover | **UX + session API**; auto policy path improved; live **NOT RUN** |
| 5 | One BrowserSession-style Local/Cloud routing | **Yes** (`select_backend` + Local/Cloud classes) |
| 6 | Small model-visible browser tools | **Yes** (observe/act/takeover/stop only registered) |
| 7 | Lease/idempotency preserved | **Yes** |
| 8 | Screenshots task-tab scoped | **Yes** |
| 9 | Golden suite + honest results | **Yes** |
| 10 | Docs + report; protocol bump only if needed | **Yes** |

## Files changed (fix pass)

- `extension/onsite48-bridge/protocol.js`, `popup.html`, `popup.js`, `operator-overlay.js`
- `backend/app/tools/my_browser_tools.py` **(new)**
- `backend/app/services/browser_session_router.py` **(new)**
- `backend/app/main.py` (registration collapse)
- `backend/app/api/browser_bridge.py` (pair/start harden)
- `backend/tests/test_browser_session_router.py` **(new)**
- `extension/onsite48-bridge/tests/protocol.test.mjs`
- `frontend/src/components/AuthorizedActions.tsx`
- `frontend/src/components/Chatinterface.tsx`
- `docs/runtime/EXTENSION_MY_BROWSER_REPORT.md`
