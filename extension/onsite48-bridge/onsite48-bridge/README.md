# Onsite48 Browser (My Browser)

Pair once. Authorize a task. Watch the agent work in a task-named tab group. Take over, resume, or close the group to stop.

This extension is the **user-controlled local browser execution boundary**. Planning, policy, and the tool loop stay on the Onsite48 backend. The extension only executes browser actions inside tabs that belong to an authorized task workspace and returns typed evidence.

## Install (one time)

1. Open `chrome://extensions` (or Edge equivalent).
2. Enable **Developer mode**.
3. **Load unpacked** → select this `onsite48-bridge` folder.
4. Click the Onsite48 Browser icon.

## Connect (no paste-token engineer flow)

1. Open the extension popup.
2. Choose **Connect** (Sign in with Onsite48).
3. Approve in the web app (Settings → Connectors → My Browser) if prompted.
4. Popup shows **Connected as you@example.com · Ready**.

Advanced: API base override and recovery pairing code live under **Advanced** / Connection details only.

## Use with a task

1. In Onsite48, enable the **My Browser** connector (or leave it preferred).
2. Start a task that needs your local sessions (logins, MFA, internal sites).
3. When prompted, click **Authorize browser for this task**.
4. A Chrome tab group named after the task appears. The agent works only inside that group.
5. Overlay on task tabs: **Agent working · Take over · Resume · Stop**.
6. Close the last tab in the group or press **Stop** → hard stop for that task’s browser work.
7. Login / MFA / CAPTCHA challenges automatically request takeover so you can complete them, then **Resume**.

## What the agent can and cannot do

- Operates only in tabs authorized for the current task (task-owned tab group).
- Never treats your unrelated focused tab as the primary workspace.
- Screenshots are scoped to task tabs; on failure the extension returns `capture_unavailable` instead of another tab’s pixels.
- Cookies, passwords, and site session tokens are never sent to Onsite48.

## Permissions (honest)

The extension requests `tabs`, `tabGroups`, `scripting`, `storage`, `alarms`, `downloads`, `webNavigation`, and `<all_urls>` host access so it can create the task workspace and act inside authorized tabs. You stay in control via Authorize / Take over / Stop / close group.

## Reliability

- Commands use `command_id` + `lease_token`. Terminal receipts prevent double-execution on reconnect, poll retry, or service-worker restart.
- WebSocket preferred; authenticated long-poll fallback when WS drops.
- Protocol version: `onsite48-bridge.v3.1`.

## Engineers

See `REBUILD_HANDOFF.md` for leases, capabilities, and migration notes. Do not expand the public tool zoo; fine-grained commands stay extension-internal.
## Production hardening patch

See `PRODUCTION_READINESS.md`. Key changes: explicit task-owned execution only, no focused-tab fallback, restart-safe transient command state, high-impact capability gating, and additional invariant tests.

