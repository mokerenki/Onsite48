# Onsite48 Guarded Operator and Platform Upgrade Design

**Design date:** 19 August 2026  
**Purpose:** Deliver a Manus-like working model without copying unsafe broad browser privileges or destabilizing the existing task, scheduler, Bridge, and persistence paths.

## Product boundary

Onsite48 will support two distinct browser experiences. They solve different problems and must never be conflated.

| Surface | Where it runs | Login state | Intended use | Safety boundary |
|---|---|---|---|---|
| **Agent Computer** | A task-scoped Playwright context managed by Onsite48 | New, isolated session | Public browsing and user-controlled temporary sign-in when Onsite48 is self-hosted locally | The agent may not act while the user has taken control. User-sensitive input is never persisted or returned in task events. |
| **Onsite48 Bridge** | The user’s existing Chrome or Edge browser | The user’s real local session | Authenticated sites such as LinkedIn, Spotify, Gmail, or dashboards already signed in by the user | No cookies, passwords, saved credentials, or browser tokens are transmitted to Onsite48. Host permission is granted by the user for each site. |

The uploaded reference extension requests broad debugger and cookie privileges. Onsite48 will **not** copy those permissions. The improved Bridge will remain an explicitly paired, user-owned browser companion with optional per-site access and no cookie export.

## User-takeover contract

A user takeover is an explicit human-control state, not an agent action. The product will use the persisted task status **Paused** with a `computer_control` reason in task metadata and graph checkpoint state.

| Event | Durable task state | Browser context | Agent behavior | User behavior |
|---|---|---|---|---|
| Agent opens a public page | Running | Open | May use permitted task tools | May observe the workspace |
| User presses **Take over** | Paused / `computer_control.mode=user` | Open | Denied from all Agent Computer actions | May click, type, use keyboard, login, solve CAPTCHA/MFA, and submit actions personally |
| User presses **Return to Synth** | Paused / `computer_control.mode=agent_ready` | Open | Still paused until explicit resume | Reviews page state then chooses Resume |
| User resumes the task | Running | Open | Continues from the persisted checkpoint | May take over again or cancel |
| Task is cancelled, deleted, timed out, or fails | Terminal | Closed | Cannot reuse the session | Context is released |

The actual entry of a password, OTP, CAPTCHA answer, payment value, file upload, or form submission is treated as **user-only activity**. The interaction endpoint returns only bounded evidence such as action category, field classification, text length, page state, and a fresh screenshot. It never returns the entered value and does not write it to task events, logs, telemetry, or database metadata.

## Agent Computer API contract

The isolated browser will gain authenticated, task-owned user-control endpoints. All require a currently active user takeover record and use normalized viewport coordinates.

| Endpoint | Purpose | Response guarantee |
|---|---|---|
| `POST /api/computer/{thread}/takeover` | Pause the task and enter user control | Returns control state and fresh snapshot |
| `POST /api/computer/{thread}/return` | End user control but leave task safely paused | Returns current state and machine-readable resume readiness |
| `POST /api/computer/{thread}/user/click` | User click at a visible coordinate | Returns before/after evidence only |
| `POST /api/computer/{thread}/user/type` | User types at a visible coordinate | Returns only `text_length`, no value |
| `POST /api/computer/{thread}/user/key` | User sends a safe browser key or shortcut | Rejects unknown/high-risk key strings |
| `GET /api/computer/{thread}/control` | Recover the current mode after refresh | Returns no page content or credentials |

Agent-facing tool methods will verify that the thread is not under user control before every browser operation. User endpoints use separate `user_*` service methods so action provenance remains unambiguous in tests and future audit screens.

## Human-verification routing

A challenge in an **Agent Computer** page will retain its isolated session and emit `handoff_surface: "agent_computer"`; the user can take over the same task browser. A challenge in the **Onsite48 Bridge** local browser will emit `handoff_surface: "synth_bridge"`; Synth remains paused while the user completes the challenge in their own browser.

A task handoff therefore never silently chooses the wrong surface. Existing stale-task protections still apply: a closed or terminal Agent Computer session cannot be recreated by snapshot, scroll, read, reload, or old WebSocket events.

## Local Onsite48 Bridge contract

Onsite48 Bridge follows this approved local-browser workflow:

1. The agent requests a bounded action.
2. The user approves the task action, and the extension runs it in a local Chrome or Edge tab.
3. Site permission is granted by the user from the extension popup for the exact host.
4. The extension verifies the active host against the approved command, captures bounded before/after evidence, and returns a structured result.
5. CAPTCHA, MFA, credentials, payment, upload/download, submission, deletion, send, publish, and similarly high-impact controls stop the operator and place the user in control.
6. The user resumes the extension after manual completion. The next agent action requires the normal task approval policy.

Trusted-site rules may authorize only **read, inspect, scroll, and same-site navigation**. Click and fill remain per-action approved. Sensitive controls remain user-only even after approval.

## Custom connector contract

The connector workspace will add an explicit **Create** menu with the following first-class options:

| Option | Supported inputs | Runtime boundary |
|---|---|---|
| **Custom API** | Name, HTTPS base URL, optional health path, auth mode, header/query credentials, and user-written usage note | Generic bounded GET tool plus separately approval-gated mutation tool. Relative paths cannot leave the registered origin. Credentials are encrypted at rest. |
| **Custom MCP** | Name, HTTPS SSE endpoint, optional headers, description, and user-written usage note | Per-user, short-lived MCP client. Tool discovery and invocation are never globally registered; all arbitrary MCP calls require exact task approval. |
| **Import MCP by JSON** | One validated MCP object containing only a remote HTTPS SSE URL and optional headers | Uses the same persisted Custom MCP contract; duplicate names and private-network endpoints are rejected. |
| **Add MCP by URL** | An HTTPS SSE endpoint without secret headers | Uses a test/discovery action after the user saves the connector. |

The release does not accept local command execution, Docker commands, browser cookies, `file://` URLs, or private/internal targets from user-created connectors. A user-created secret is encrypted at rest and is never returned after creation; changing a secret is a replace operation.

## Scheduler and operational-history contract

Every scheduled task execution will create a durable execution record before work begins and finish it with one of **running, completed, needs_approval, needs_user_intervention, failed, cancelled, skipped**. The record will carry the linked task thread, bounded error, start/end timestamps, and attempt number.

Scheduler outcomes are visible in Automations and Marketing Operations. A user may retry a failed or attention-blocked scheduled task manually; this creates a new durable execution record and never silently duplicates an in-progress execution. Existing scheduled/background safety remains unchanged: scheduled tasks cannot use a private paired browser or Onsite48 Bridge.

## Marketing Operations and collaboration boundary

This release expands the existing organisation-scoped workspace rather than attempting a new social collaboration system. It will add a campaign operating board built from durable signals and approval proposals, outcome/status controls, owners from existing organisation members, and task-linked evidence. All mutations are organization and user scoped. The existing owner/admin/member roles remain the authorization foundation.

## Compatibility rules

1. Existing task IDs, task event shapes, Bridge pairing records, trust rules, and built-in connector connections remain valid.
2. All database additions are additive and run through one new Alembic revision.
3. The old read-only Agent Computer controls continue to work when no takeover is active.
4. Existing `needs_user_intervention` events retain their old fields and gain only optional `handoff_surface` metadata.
5. Existing safe Bridge capability rules remain safe by default; no site becomes trusted automatically.
6. The full backend suite and frontend production build must pass before release packaging.