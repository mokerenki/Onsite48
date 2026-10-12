# Onsite48 Bridge flow audit

## Interaction counts

The counts below describe the current checked-in flow for one read-only Bridge task.

| Scenario | Human interactions before the command can execute | Notes |
|---|---:|---|
| First-time browser, no pairing | 4–6 | Create pairing code in Onsite48; copy device ID/code into the extension; submit pairing; focus an ordinary page; grant the origin permission if Chrome prompts; resume the paused task if selection or permission was required. The exact count varies because active-tab read mode can avoid explicit tab locking. |
| Previously paired browser, new untrusted origin | 2–4 | Focus the target page; grant site permission if needed; resume after the typed permission handoff. Pairing is not repeated. High-impact commands still require task approval. |
| Previously paired browser, trusted origin and SAFE_TRUSTABLE read | 0–1 | The backend checks the fresh active context, exact normalized hostname, enabled `BrowserBridgeTrustRule`, and requested command membership. If all match, it skips the separate site approval; the user may still need to open the popup or resume the task if the browser transport/context is stale. |
| Previously paired browser, trusted origin but write/high-impact command | 1–2 | Origin trust never waives explicit approval for clicks, fills, captures, tab opening, or other high-impact actions. |

## Findings

`BrowserBridgeService.may_run_without_prompt()` is consulted by command creation. It only grants the five `SAFE_TRUSTABLE_COMMANDS` and requires a connected, user/organization-bound device, fresh selected context, exact hostname matching for navigation, and an enabled trust rule containing the command. The extension UI now explains that trusted-site rules reduce read prompts while high-impact actions remain gated.

The extension already used `poll_fallback` and a reconnect schedule internally, but a stale active context could otherwise leave the user waiting until they reopened the popup. The popup now exposes **Reconnect & resume** when transport is not live, the status is in transport error, or context/poll freshness exceeds two minutes. The action reconnects the transport, polls pending commands, and refreshes the visible state without re-pairing.

The executor now records one central `StepTarget` per guarded step: Bridge commands target the paired user session, `computer_*` commands target isolated Playwright, Python execution targets E2B, and other tools use their declared runtime or the local surface. This is a coordination abstraction; existing service-level authorization and preflight checks remain authoritative.