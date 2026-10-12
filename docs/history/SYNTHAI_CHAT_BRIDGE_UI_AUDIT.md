# Onsite48 ChatInterface and Onsite48 Bridge UI Audit

**Scope:** Frontend-only product and UI audit of the current `ChatInterface`, workspace sidebar, task-summary API, and Onsite48 Bridge page.  
**Change status:** **No source code was modified during this audit.**  
**Goal:** Define the work required to make the experience feel coherent, calm, information-dense, and closer to a Manus-class agent workspace.

## Executive conclusion

The two problems you identified are real and have clear code-level causes.

First, the sidebar displays the first user prompt as the task title because the backend creates `TaskThread.title` with `prompt[:240]`. The frontend then renders that value directly as the task label. The screenshot shows the result: a long prompt wraps inside a narrow 256-pixel sidebar, pushing the task list downward and making every task look like a transcript fragment rather than a saved work item.

Second, the Onsite48 Bridge page returns all devices for the current user and organization, regardless of whether they are still connected. The page renders every returned record in **Paired browsers**. The API exposes `status`, `last_seen_at`, and `revoked_at`, but the UI does not convert those fields into a lifecycle model, separate active devices from historical devices, or provide a clear archive/cleanup view. A browser that has been disconnected in reality can therefore continue to look like a paired browser in the product.

The broader issue is that the current UI exposes implementation state rather than product concepts. The sidebar exposes raw prompts, status dots, and heuristic icons. Bridge exposes database rows, trust rules, and command history as separate long cards. A Manus-level experience should present **work items, current state, next action, evidence, and recoverability** first. Technical details should be available progressively through expandable panels.

## Findings at a glance

| Priority | Finding | Current cause | Recommended direction |
|---|---|---|---|
| P0 | Sidebar uses full initial prompt as title | `TaskThread.title = prompt[:240]` in `backend/app/api/product_tasks.py`; frontend renders `task.title` | Introduce a short, durable task title generated from the first request with deterministic fallback and optional user rename |
| P0 | Disconnected Bridge devices remain visible as paired | `GET /api/bridge/devices` returns all user devices; page renders all records | Add server-owned lifecycle classification, active/archive filtering, cleanup actions, and clear offline/revoked states |
| P0 | Sidebar is too narrow for task identity | Fixed `w-64`; long title is allowed to wrap in a task row | Use single-line truncation, tooltip/detail on hover, wider/collapsible sidebar, and grouped task rows |
| P1 | Icon selection is heuristic and not durable | `TaskGlyph` infers icons from title/domain using regex; API does not return `icon_key` or `category` in `TaskSummary` | Store task presentation metadata or return a server-generated semantic task descriptor |
| P1 | Task list lacks Manus-style temporal grouping | Tasks are one flat list ordered by `updated_at` | Group by Today, Yesterday, Previous 7 Days, and Older with date separators |
| P1 | Bridge page is operationally accurate but visually fragmented | Pairing, trust scopes, activity, and devices are four large cards | Reframe as a device-management console with an active device hero, archived devices, trust scopes, and activity drawer |
| P1 | ChatInterface is a large stateful component | One component owns socket lifecycle, restoration, planner events, browser session, transcript, approvals, and rendering | Split into presentation and state domains without changing the runtime contract |
| P2 | Technical metadata competes with the user’s answer | Status, response mode, cost, verification, browser session, and timeline are all near the transcript | Use a compact task header and progressive disclosure for diagnostics |
| P2 | Bridge refresh is manual and not lifecycle-aware | `load()` retries three resources but does not establish freshness polling or stale classification | Add last-seen freshness, live/offline polling, and event-driven invalidation |

## 1. Sidebar: exact root cause of the long prompt problem

### What the code does now

The backend task summary includes a `title`, but task creation uses the raw prompt:

```python
TaskThread(
    title=prompt[:240],
    prompt=prompt,
    ...
)