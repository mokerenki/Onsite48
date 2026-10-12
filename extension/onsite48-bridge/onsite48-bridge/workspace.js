/**
 * Task-owned browser workspace registry.
 *
 * This is the single source of truth inside the extension for which tabs
 * belong to which Onsite48 task/session. Actions must resolve their target
 * from this registry. The user's currently focused tab is never used as
 * the execution identity.
 */
import { PROTOCOL_VERSION } from "./protocol.js";

const WORKSPACE_KEY = "synthTaskWorkspacesV3";
const WORKSPACE_LOCK_KEY = "synthTaskWorkspacesLock";

/**
 * @typedef {Object} WorkspaceRecord
 * @property {string} sessionId
 * @property {string|null} taskId
 * @property {string} title
 * @property {number|null} windowId
 * @property {number|null} tabGroupId
 * @property {number[]} tabIds
 * @property {number|null} primaryTabId
 * @property {string} status  // authorizing|authorized|starting|active|taken_over|stopped|...
 * @property {string} createdAt
 * @property {string} updatedAt
 */

// Simple mutex for workspace operations
async function withWorkspaceLock(fn) {
  const lockKey = WORKSPACE_LOCK_KEY;
  let acquired = false;
  for (let attempt = 0; attempt < 50; attempt++) {
    const stored = await chrome.storage.local.get(lockKey);
    if (!stored[lockKey]) {
      await chrome.storage.local.set({ [lockKey]: Date.now() });
      acquired = true;
      break;
    }
    await new Promise(r => setTimeout(r, 20));
  }
  if (!acquired) throw new Error("Could not acquire workspace lock");
  try {
    return await fn();
  } finally {
    await chrome.storage.local.remove(lockKey);
  }
}

export async function loadWorkspaces() {
  const stored = await chrome.storage.local.get(WORKSPACE_KEY);
  return stored[WORKSPACE_KEY] || {};
}

export async function saveWorkspaces(map) {
  await chrome.storage.local.set({ [WORKSPACE_KEY]: map });
}

export async function getWorkspace(sessionId) {
  const map = await loadWorkspaces();
  return map[sessionId] || null;
}

export async function upsertWorkspace(record) {
  return withWorkspaceLock(async () => {
    const map = await loadWorkspaces();
    const now = new Date().toISOString();
    map[record.sessionId] = {
      ...record,
      updatedAt: now,
      createdAt: record.createdAt || now,
    };
    await saveWorkspaces(map);
    return map[record.sessionId];
  });
}

export async function removeWorkspace(sessionId) {
  return withWorkspaceLock(async () => {
    const map = await loadWorkspaces();
    delete map[sessionId];
    await saveWorkspaces(map);
  });
}

/**
 * Create a Manus-style task workspace:
 *   1. Open a new tab (about:blank or startUrl)
 *   2. Create a named tab group "Onsite48 — <task name>"
 *   3. Move the tab into the group
 *   4. Register the workspace
 * Idempotent: if workspace for sessionId exists, return existing.
 */
export async function createTaskWorkspace({
  sessionId,
  taskId = null,
  title = "Onsite48 — Task",
  startUrl = "about:blank",
  windowId = null,
}) {
  if (!sessionId) throw new Error("sessionId is required to create a task workspace.");

  // Idempotency: check if workspace already exists
  const existing = await getWorkspace(sessionId);
  if (existing) {
    // Verify the tab group and tabs still exist
    let valid = true;
    if (existing.tabGroupId != null) {
      try {
        await chrome.tabGroups.get(existing.tabGroupId);
      } catch {
        valid = false;
      }
    }
    if (valid && existing.tabIds && existing.tabIds.length > 0) {
      // Verify at least one tab still exists
      let hasLiveTab = false;
      for (const tabId of existing.tabIds) {
        try {
          const tab = await chrome.tabs.get(tabId);
          if (tab && !tab.discarded) {
            hasLiveTab = true;
            break;
          }
        } catch {
          // tab closed
        }
      }
      if (!hasLiveTab) valid = false;
    } else {
      valid = false;
    }
    if (valid) {
      // Update status to active if it was stopped
      if (["stopped", "failed", "expired"].includes(existing.status)) {
        const updated = { ...existing, status: "active", updatedAt: new Date().toISOString() };
        await upsertWorkspace(updated);
        return updated;
      }
      return existing;
    }
    // Workspace invalid, fall through to create new
  }

  // Prefer the last focused window so the group appears where the user is.
  let targetWindowId = windowId;
  if (targetWindowId == null) {
    const wins = await chrome.windows.getAll({ populate: false, windowTypes: ["normal"] });
    const focused = wins.find((w) => w.focused) || wins[0];
    if (!focused) throw new Error("No browser window available for task workspace.");
    targetWindowId = focused.id;
  }

  const tab = await chrome.tabs.create({
    windowId: targetWindowId,
    url: startUrl || "about:blank",
    active: true,
  });

  let groupId = null;
  try {
    groupId = await chrome.tabs.group({
      tabIds: [tab.id],
      createProperties: { windowId: targetWindowId },
    });
    await chrome.tabGroups.update(groupId, {
      title: String(title).slice(0, 64),
      color: "blue",
      collapsed: false,
    });
  } catch (err) {
    // tabGroups may be unavailable in some environments; still register the tab.
    console.warn("tabGroups.create failed, continuing with bare tab:", err);
  }

  const record = {
    sessionId,
    taskId,
    title: String(title).slice(0, 64),
    windowId: targetWindowId,
    tabGroupId: groupId,
    tabIds: [tab.id],
    primaryTabId: tab.id,
    status: "active",
    createdAt: new Date().toISOString(),
    updatedAt: new Date().toISOString(),
  };
  await upsertWorkspace(record);
  return record;
}

/**
 * Open an additional tab inside an existing workspace.
 */
export async function openTaskTab(sessionId, { url = "about:blank", makePrimary = false } = {}) {
  return withWorkspaceLock(async () => {
    const ws = await getWorkspace(sessionId);
    if (!ws) throw new Error(`No workspace for session ${sessionId}`);
    if (ws.status === "stopped" || ws.status === "failed" || ws.status === "expired") {
      throw new Error(`Workspace ${sessionId} is ${ws.status}`);
    }

    const tab = await chrome.tabs.create({
      windowId: ws.windowId || undefined,
      url,
      active: false,
    });

    if (ws.tabGroupId != null) {
      try {
        await chrome.tabs.group({ tabIds: [tab.id], groupId: ws.tabGroupId });
      } catch (err) {
        console.warn("Failed to add tab to group:", err);
      }
    }

    const tabIds = [...(ws.tabIds || []), tab.id];
    const updated = {
      ...ws,
      tabIds,
      primaryTabId: makePrimary ? tab.id : ws.primaryTabId,
      updatedAt: new Date().toISOString(),
    };
    await upsertWorkspace(updated);
    return { tab, workspace: updated };
  });
}

/**
 * Resolve the tab that a command should act on.
 * NEVER falls back to the user's currently focused tab as identity.
 * Validates that preferredTabId belongs to the workspace.
 */
export async function resolveTaskTab(sessionId, preferredTabId = null) {
  const ws = await getWorkspace(sessionId);
  if (!ws) {
    return { error: "workspace_not_found", message: `No task workspace for session ${sessionId}` };
  }
  if (["stopped", "failed", "expired"].includes(ws.status)) {
    return { error: "workspace_stopped", message: `Workspace is ${ws.status}` };
  }

  // Validate preferredTabId belongs to workspace
  if (preferredTabId != null && ws.tabIds && !ws.tabIds.includes(preferredTabId)) {
    return { error: "tab_not_in_workspace", message: `Tab ${preferredTabId} is not part of workspace ${sessionId}` };
  }

  const candidateId = preferredTabId ?? ws.primaryTabId ?? (ws.tabIds || [])[0];
  if (candidateId == null) {
    return { error: "no_task_tabs", message: "Workspace has no tabs." };
  }

  try {
    const tab = await chrome.tabs.get(candidateId);
    if (!tab || tab.discarded) {
      // Try any remaining live tab in the workspace.
      for (const id of ws.tabIds || []) {
        try {
          const t = await chrome.tabs.get(id);
          if (t && !t.discarded) {
            return { tab: t, workspace: ws };
          }
        } catch {
          /* tab closed */
        }
      }
      return { error: "task_tabs_closed", message: "All task tabs for this workspace are closed." };
    }
    return { tab, workspace: ws };
  } catch {
    return { error: "task_tab_closed", message: `Task tab ${candidateId} is closed.` };
  }
}

export async function markWorkspaceStatus(sessionId, status, extra = {}) {
  return withWorkspaceLock(async () => {
    const ws = await getWorkspace(sessionId);
    if (!ws) return null;
    const updated = { ...ws, ...extra, status, updatedAt: new Date().toISOString() };
    await upsertWorkspace(updated);
    return updated;
  });
}

/**
 * Close all tabs belonging to the workspace and remove the group.
 * Called on Stop.
 */
export async function destroyTaskWorkspace(sessionId, { closeTabs = true } = {}) {
  return withWorkspaceLock(async () => {
    const ws = await getWorkspace(sessionId);
    if (!ws) return;
    if (closeTabs) {
      for (const id of ws.tabIds || []) {
        try {
          await chrome.tabs.remove(id);
        } catch {
          /* already closed */
        }
      }
    }
    // Clean up tab group
    if (ws.tabGroupId != null) {
      try {
        await chrome.tabGroups.remove(ws.tabGroupId);
      } catch {
        /* group already removed or tabs already closed */
      }
    }
    await removeWorkspace(sessionId);
  });
}

export function workspaceLifecycleSnapshot(ws) {
  if (!ws) {
    return {
      protocol_version: PROTOCOL_VERSION,
      status: "disconnected",
      session_id: null,
      tab_ids: [],
    };
  }
  return {
    protocol_version: PROTOCOL_VERSION,
    status: ws.status,
    session_id: ws.sessionId,
    task_id: ws.taskId,
    tab_group_id: ws.tabGroupId,
    window_id: ws.windowId,
    primary_tab_id: ws.primaryTabId,
    tab_ids: ws.tabIds || [],
    title: ws.title,
    updated_at: ws.updatedAt,
  };
}
