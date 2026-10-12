# Onsite48 Browser (My Browser) — Engineer Handoff (v3.1)

## Protocol

- `PROTOCOL_VERSION`: `onsite48-bridge.v3.1`
- `EXTENSION_VERSION`: `3.1.0`
- User-facing name: **Onsite48 Browser** / **My Browser** (not Bridge Operator)

### Migration v3 → v3.1

- Default connect path is device credential (`/api/bridge/device/pair/start|complete|refresh`), not paste-token engineer flow.
- Task-named Chrome tab group is the sole primary workspace.
- Focused-tab command names remain only as internal aliases; do not advertise them to the model.
- Capture stays `captureTabScoped`; failure → `capture_unavailable`.
- Lease invariants unchanged: `command_id + lease_token`, terminal receipts, no double-execution on reconnect/poll/SW restart.

## Invariants (do not regress)

1. Planning / policy / tool loop stay on the backend. Extension is execution boundary only.
2. Terminal receipts survive service-worker restart; a completed lease is never re-applied.
3. Task-owned tabs only; never derive identity from the user’s unrelated focused tab.
4. Screenshots never leak another tab’s pixels.
5. Public model-visible tools remain a small set (`browser_observe`, `browser_act`, `browser_takeover`, `browser_stop` or equivalent single `browser` tool). Full `COMMAND_TYPES` stay extension-internal.

## Key files

| File | Role |
|------|------|
| `protocol.js` | Version, command catalog, leases, evidence, validation |
| `background.js` | WS + long-poll, claim/complete, workspace, overlay inject |
| `workspace.js` | Task tab group create / resolve / destroy → stop |
| `session-state.js` | ACTIVE after authorize |
| `operator-overlay.js` | Agent working · Take over · Resume · Stop |
| `popup.html` / `popup.js` | Connected as &lt;email&gt; · Ready \| Reconnect |
| `manifest.json` | Permissions narrative |

## Backend touchpoints

- `api/browser_bridge.py` — `/device/pair/*`, authorize, commands
- `services/browser_bridge.py` — device credential, leases
- `services/browser_session.py` + runtime — LocalBridgeSession \| CloudPlaywrightSession router
- Tools collapse: do not dump full command catalog into model schema

## Tests

- `tests/protocol.test.mjs` — update for v3.1; remove obsolete focused-tab assertions.
- Reliability targets: SW restart mid-command, WS drop → poll, duplicate completion → single side effect.

## Out of scope for this handoff

- Chrome Web Store publish
- Full agent-loop rewrite
- Per-SaaS browser tools
