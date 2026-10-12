# Onsite48 — Frontier Agent Architecture

**Rebrand:** Grekko / SynthAI → **Onsite48**

This document describes the architecture implemented toward Manus Browser Operator + Muse/Dots-class autonomy.

## Core invariant

```
User Intent
  → Goal Contract
  → Capability Contract
  → Plan (revisioned)
  → Policy Review (independent Sentinel)
  → Execution (resource-locked, journaled)
  → Observation
  → Evidence
  → Verification (terminal)
  → Recovery or Completion
```

## Browser model (Manus-style)

**Deleted as primary concept:** focused tab / selected tab / “whatever the user is looking at.”

**New model:**

```
Task
  → BrowserSession (server-owned, durable)
  → BrowserAuthorization (server-authoritative, revocable, task-scoped)
  → Extension creates tab group "Onsite48 — <task name>"
  → Task tabs
  → Page observations
  → Actions bound to observation_id + fingerprint
```

### Extension (v3)

- Protocol: `synth-bridge.v3` / Onsite48 Bridge Operator 3.0.0
- `workspace.js`: createTaskWorkspace, openTaskTab, resolveTaskTab, destroyTaskWorkspace
- Commands: create_task_workspace, claim_task_workspace, open_task_tab, list_task_tabs, takeover, resume, stop
- Tab resolution **never** falls back to user focus when a session workspace exists
- Manifest: tabGroups permission; debugger/activeTab removed

### Backend

- `app/core/browser_workspace.py` — domain model
- `app/services/browser_session.py` — rejects focused_tab; claim_workspace; stop revokes auth
- `BrowserAuthorizationRecord` table + migration `20261002_33`
- `app/core/goal_contract.py` — GoalContract + CapabilityContract
- `app/core/policy_engine.py` — independent ALLOW / DENY / APPROVAL / TAKEOVER

## Auth UI

- Star-field CSS sparks replaced with Onsite48 brand mark watermarks (`/Onsite48-light.png`)
- Logos use `/Onsite48-opt.png` and `/Onsite48-light.png`
- Storage keys: `onsite48_*`

## What remains for full Manus/Muse parity

1. Full background.js cleanup of all legacy active-tab paths
2. Multi-layer observation (a11y + visual grounding + coordinates) end-to-end
3. Resource locks in executor
4. Terminal proof gate wired into final answer path
5. Persistent agent computer for background work
6. Real Chromium E2E suite (10 mandated scenarios)
7. Specialist multi-agent orchestration under shared contracts

## Branding

All Grekko / SynthAI product strings in application code have been renamed to **Onsite48**.
Extension folder is `extension/onsite48-bridge`.

