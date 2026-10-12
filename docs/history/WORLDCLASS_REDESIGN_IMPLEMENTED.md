# Onsite48 World-Class Runtime Redesign: Implemented Changes

## Scope

This delivery implements the requested redesign against the supplied `Onsite48_worldclass_complete.zip` baseline. The work was intentionally additive and compatibility-aware: existing planner, executor, verifier, Bridge, session, proactive worker, and chat contracts were retained while their weak boundaries were strengthened.

## Implemented changes

### Durable runtime correlation

The ORM and Alembic migration now support direct relationships among browser sessions, tasks, agent runs, step attempts, browser-session events, and Bridge commands.

Added fields include:

- `BrowserSessionEvent.task_id`
- `BrowserSessionEvent.run_id`
- `BrowserSessionEvent.step_attempt_id`
- `BrowserSessionEvent.bridge_command_id`
- `AgentRun.browser_session_id`
- `AgentStepAttempt.browser_session_id`
- `BrowserBridgeCommand.run_id`
- `BrowserBridgeCommand.step_attempt_id`
- `BrowserBridgeCommand.session_id`
- `BrowserBridgeCommand.sequence`
- `BrowserBridgeCommand.idempotency_key`
- `ScheduledAgentTask.schedule_policy`

The Bridge command creation path now stores session, step, sequence, and idempotency metadata when an explicit browser session is bound. Browser-session lifecycle events retain their owning task ID. Browser-session creation also validates that a supplied task belongs to the current user and organization.

### Explicit execution decisions

The new `ExecutionState` vocabulary provides a canonical set of states:

```text
queued, planning, validating, preflight, waiting_for_approval,
waiting_for_user, observing, executing, verifying, recovering,
completed, failed, cancelled