# Production Patch Audit — Scheduled Tasks and Onsite48 Bridge

## Audit scope

This audit assessed the complete attached specification against the current Onsite48 release before any production changes. The conclusions below are based on the currently implemented scheduler, scheduled runner, Bridge service, extension protocol, task identity context, models, deployment configuration, and existing regression tests.

## Confirmed scheduler defects

| Recommendation | Verdict | Evidence and implementation boundary |
|---|---|---|
| Atomic due-schedule claim | **Confirmed** | `ProactiveOperationsWorker._run_due_scheduled_agent_tasks()` first reads due IDs and `_process_scheduled_agent_task()` later reloads and clears `next_run_at` in a separate transaction. Two worker processes can observe the same due item before either commits. Replace this with row-locked, skip-locked claims. |
| Crash-safe execution creation | **Confirmed** | The worker clears `next_run_at` and commits before `run_scheduled_agent_task()` creates `ScheduledTaskExecution`. A process crash in this gap loses the claimed occurrence. Create the execution record inside the claim transaction, then hand its identifier to the runner. |
| Stale-running recovery | **Confirmed** | `ScheduledTaskExecution` has no lease/heartbeat field and no worker recovery pass. Add a small additive lease/heartbeat schema and recover only expired claimed/running executions under a row lock. |
| Deterministic recurring schedule calculation | **Confirmed** | The worker calculates the next daily occurrence from wall-clock `now`. This can drift after a delayed run. Calculate from the claimed scheduled occurrence in the schedule's IANA timezone and advance until it is in the future. |
| Visible missing-runtime state | **Confirmed** | The worker returns immediately when a runtime component is absent with no structured diagnostic state. Preserve schedules without marking them failed, but log and surface worker unavailability. |
| Stream execution without WebSocket | **Already implemented** | `run_scheduled_agent_task()` calls the canonical `stream_task_events()` directly and persists task events. Scheduled runtime tests also confirm the local Onsite48 Bridge is excluded. Preserve this behavior rather than introducing another runner. |
| Scheduled use of local Bridge | **Already prevented** | The dedicated worker registry intentionally excludes local-browser Bridge tools. Do not route a scheduled active-tab request into the isolated computer or local Bridge as a workaround. |
| Selected-IANA timezone conversion | **Confirmed in frontend contract** | Automations builds `new Date(\`${date}T${time}:00\`)` in the browser/Windows timezone and separately sends an IANA string. Add a local-date/local-time API contract interpreted by Python `ZoneInfo`; the frontend must stop creating the authoritative UTC instant. |

## Confirmed Onsite48 Bridge defects

| Recommendation | Verdict | Evidence and implementation boundary |
|---|---|---|
| Do not claim before browser permission check | **Confirmed; highest priority** | `list_commands_for_extension()` changes every pending command to `claimed` before extension `runCommand()` calls `ensureSitePermission()`. A missing optional host permission consumes the command. Split polling from an explicit, atomic claim endpoint after extension preflight. |
| Capability / permission preflight | **Confirmed** | The current protocol returns only command data and has no readiness report. Add command-specific preflight state, origin, and timestamp without sending page content or weakening optional host permissions. |
| Keep unavailable-permission command pending | **Confirmed** | A permission failure currently flows to extension completion and marks the already-claimed command failed. Preserve `pending`; persist a bounded `site_permission_required` preflight reason to make the operator state visible. |
| Stale claimed-command recovery | **Confirmed** | Claimed commands have `claimed_at` but no recovery pass. Add a bounded lease and atomically return abandoned `claimed` commands to `pending`, unless cancelled or expired. |
| Link command to originating task | **Confirmed** | `_BridgeTool._queue()` always calls `queue_and_wait(... task_id=None ...)` despite the command model supporting a task UUID. Resolve the `TaskThread` from the execution identity's external thread ID inside the service and persist its internal UUID. |
| Do not choose latest connected device globally | **Confirmed** | `queue_and_wait()` and trusted-site evaluation select the latest device. Bind the first command for a task to a single persisted device ID. If a new task has multiple paired devices and no selected device, fail safely with an actionable selection error rather than silently redirecting the command. |
| Exact approval binding | **Already implemented** | Existing executor regression coverage proves approvals use exact thread/step/tool/argument confirmation keys. Do not replace this contract. |
| CAPTCHA/MFA/password/payment safety | **Already implemented** | Extension-side challenge detection and sensitive-control rejection are present. Keep those boundaries unchanged. |
| Evidence and verification structure | **Already implemented, with one contract extension needed** | The extension captures bounded before/after observations and server persists evidence/verification. Extend only command lifecycle/preflight data; do not expand sensitive page-data collection. |
| Cancellation stale-result protection | **Partially confirmed** | Completion accepts only `claimed` commands for the matching device, so a cancelled command cannot be overwritten. Add task-cancellation propagation now that all tool-created commands will have an internal task ownership link. |
| Docker Desktop localhost path | **Already correct** | The extension defaults to `http://localhost`, while Nginx proxies `/api/` to backend. No change to `backend:8000` or `host.docker.internal` is justified. |
| Open CORS to all origins | **Rejected** | The security model must remain restricted. Extension host permission and current local endpoint design are preserved; no wildcard CORS change will be made. |

## Implementation decision

The implementation will use one additive migration for scheduled execution leases and Bridge preflight state, change only the existing worker/runner/Bridge service/API/tool/extension flows, and extend their existing regression suites. It will not add Redis scheduling, a second task runner, a new bridge protocol, an alternate browser framework, unrestricted host permissions, or CAPTCHA/MFA/password/payment automation.