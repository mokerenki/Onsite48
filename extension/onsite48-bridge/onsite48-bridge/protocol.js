// Onsite48 Browser protocol v3.1.0 — Manus-style task-owned workspace
// This module contains no Chrome APIs so it can be tested directly with Node.

export const PROTOCOL_VERSION = "onsite48-bridge.v3.1";
export const EXTENSION_VERSION = "3.1.0";
export const EXTENSION_NAME = "Onsite48 Browser";

/**
 * Command catalog.
 *
 * *_active_tab names are retained only as aliases that the background worker
 * remaps onto the task-owned tab. New code must use the task_* / session_*
 * forms. The focused-tab execution model is gone.
 */
export const COMMAND_TYPES = Object.freeze([
  // Workspace lifecycle
  "create_task_workspace",
  "claim_task_workspace",
  "list_task_tabs",
  "open_task_tab",
  "close_task_tab",
  "close_task_workspace",

  // Observation (against a task-owned tab)
  "observe",
  "inspect",
  "locate",
  "screenshot",
  "read_page",

  // Navigation & interaction
  "navigate",
  "back",
  "forward",
  "reload",
  "click",
  "double_click",
  "right_click",
  "type",
  "insert_text",

  // Waiting
  "wait",
  "wait_for_url",
  "wait_for_text",
  "wait_for_selector",
  "wait_for_navigation",
  "wait_for_network_idle",
  "wait_for_dom_stable",
  "wait_for_download",
  "wait_for_dialog",

  // Media / files
  "control_media",
  "download",
  "upload",

  // Operator control
  "takeover",
  "resume",
  "stop",

  // Legacy aliases (mapped by background to task-owned tab)
  "read_active_tab",
  "inspect_active_tab",
  "observe_surface",
  "capture_active_tab",
  "click_active_tab",
  "fill_active_tab",
  "download_active_tab",
  "upload_active_tab",
  "open_tab",
  "open_task_tabs",
  "close_task_tabs",
]);

export const READ_COMMAND_TYPES = new Set([
  "observe",
  "inspect",
  "locate",
  "screenshot",
  "read_page",
  "list_task_tabs",
  "read_active_tab",
  "inspect_active_tab",
  "observe_surface",
  "capture_active_tab",
]);

export const USER_GESTURE_COMMAND_TYPES = new Set([
  "create_task_workspace",
  "claim_task_workspace",
  "open_task_tab",
  "close_task_tab",
  "close_task_workspace",
  "navigate",
  "click",
  "double_click",
  "right_click",
  "type",
  "insert_text",
  "upload",
  "download",
  "takeover",
  "resume",
  "stop",
  "back",
  "forward",
  "reload",
  "wait",
  "wait_for_url",
  "wait_for_text",
  "wait_for_selector",
  "wait_for_navigation",
  "wait_for_network_idle",
  "wait_for_dom_stable",
  "wait_for_download",
  "wait_for_dialog",
  "control_media",
  // legacy
  "click_active_tab",
  "fill_active_tab",
  "download_active_tab",
  "upload_active_tab",
  "open_tab",
  "open_task_tabs",
  "close_task_tabs",
]);

export const TERMINAL_COMMAND_STATES = new Set([
  "completed",
  "failed",
  "cancelled",
  "rejected",
]);

export const VERIFICATION_RULES = Object.freeze({
  URL_MATCHES: "url_matches",
  TEXT_CONTAINS: "text_contains",
  ELEMENT_PRESENT: "element_present",
  PAGE_DIGEST_CHANGED: "page_digest_changed",
  STATE_CHANGED: "state_changed",
  OBSERVATION_MATCHES: "observation_matches",
});

export const SESSION_STATUSES = Object.freeze([
  "disconnected",
  "authorizing",
  "authorized",
  "starting",
  "active",
  "paused_for_user",
  "paused_for_approval",
  "taken_over",
  "resuming",
  "completing",
  "stopping",
  "stopped",
  "failed",
  "expired",
]);

export function validateVerificationRule(rule) {
  if (!rule || typeof rule !== "object") {
    return { valid: false, error: "Verification rule must be an object." };
  }
  const type = String(rule.type || "").trim();
  if (!type) {
    return { valid: false, error: "Verification rule must have a type." };
  }
  const allowed = new Set(Object.values(VERIFICATION_RULES));
  if (!allowed.has(type)) {
    return { valid: false, error: `Unsupported verification rule type: ${type}` };
  }
  return { valid: true };
}

export function advertisedCapabilities() {
  return [...COMMAND_TYPES];
}

/**
 * Capabilities for the legacy /extension/pair fallback (manual device_id + pairing_code).
 * Curated to ≤20 to satisfy backend validation and match the primary device/pair flow.
 * Excludes legacy *_active_tab aliases that are normalized by the background worker.
 */
export function legacyPairCapabilities() {
  return [
    "observe", "inspect", "locate", "screenshot", "read_page",
    "navigate", "click", "double_click", "right_click", "type", "insert_text",
    "wait", "wait_for_selector", "wait_for_network_idle", "wait_for_dom_stable",
    "download", "upload", "takeover", "stop",
  ];
}

export function commandKey(command) {
  const id = String(command?.id || "").trim();
  const lease = String(command?.lease_token || command?.leaseToken || "").trim();
  if (!id) throw new Error("Bridge command is missing its identifier.");
  return `${id}:${lease || "unleased"}`;
}

export function validateCommand(command) {
  if (!command || typeof command !== "object") {
    throw new Error("Bridge command must be an object.");
  }
  const id = String(command.id || "").trim();
  const commandType = String(command.command_type || "").trim();
  if (!id || id.length > 128) {
    throw new Error("Bridge command has an invalid identifier.");
  }
  if (!COMMAND_TYPES.includes(commandType)) {
    throw new Error(`Unsupported bridge command: ${commandType || "unknown"}.`);
  }
  if (
    command.payload != null &&
    (typeof command.payload !== "object" || Array.isArray(command.payload))
  ) {
    throw new Error("Bridge command payload must be an object.");
  }
  if (command.payload?.verification) {
    if (!Array.isArray(command.payload.verification)) {
      throw new Error("Verification must be an array.");
    }
    for (const rule of command.payload.verification) {
      const result = validateVerificationRule(rule);
      if (!result.valid) throw new Error(result.error);
    }
  }
  // Callers do `command = validateCommand(command)`, so return the command
  // (shallow copy, payload defaulted) rather than a boolean.
  return { ...command, id, command_type: commandType, payload: command.payload ?? {} };
}

/**
 * Authority class of a command: "read" (observation only), "user_gesture"
 * (acts on the page / workspace as the user would) or "interact" (everything
 * else, e.g. waits, scrolling, hover).
 */
export function commandAuthority(command) {
  const type = String(command?.command_type || "");
  if (READ_COMMAND_TYPES.has(type)) return "read";
  if (USER_GESTURE_COMMAND_TYPES.has(type)) return "user_gesture";
  return "interact";
}

/**
 * Map a legacy *_active_tab command onto the task-owned primary tab.
 * The background worker must resolve the real tab_id from the workspace
 * registry; this helper only normalizes the command type and payload shape.
 */
export function normalizeLegacyCommand(command, primaryTabId) {
  const type = String(command?.command_type || "");
const map = {
    read_active_tab: "read",
    inspect_active_tab: "inspect",
    observe_surface: "observe",
    capture_active_tab: "screenshot",
    click_active_tab: "click",
    fill_active_tab: "type",
    download_active_tab: "download",
    upload_active_tab: "upload",
    open_task_tabs: "open_task_tab",
    close_task_tabs: "close_task_tab",
  };
  if (!map[type]) return command;
  return {
    ...command,
    command_type: map[type],
    payload: {
      ...(command.payload || {}),
      tab_id: command.payload?.tab_id ?? primaryTabId,
      _legacy_alias: type,
    },
  };
}

export function buildEvidenceEnvelope({
  commandId,
  observationId,
  success,
  error = null,
  before = null,
  after = null,
  metadata = {},
}) {
  return {
    protocol_version: PROTOCOL_VERSION,
    command_id: commandId,
    observation_id: observationId,
    success: Boolean(success),
    error: error ? String(error).slice(0, 500) : null,
    before,
    after,
    metadata,
    recorded_at: new Date().toISOString(),
  };
}

// ── Helpers used by background.js ─────────────────────────────────────────

/**
 * Evidence envelope for a command receipt.
 *
 * Primary form (used by background.js): evidenceEnvelope(command, before, after, verification).
 * A single already-shaped payload ({ command_id, observation_id, success, ... })
 * is still accepted and routed to buildEvidenceEnvelope.
 */
export function evidenceEnvelope(command, before, after, verification) {
  const legacyShape =
    before === undefined &&
    after === undefined &&
    verification === undefined &&
    command &&
    typeof command === "object" &&
    command.command_type === undefined &&
    (command.command_id !== undefined || command.commandId !== undefined || command.observation_id !== undefined);
  if (legacyShape) {
    return buildEvidenceEnvelope({
      commandId: command.command_id || command.commandId,
      observationId: command.observation_id || command.observationId,
      success: command.success,
      error: command.error,
      before: command.before,
      after: command.after,
      metadata: command.metadata || {},
    });
  }
  const payload = command?.payload || {};
  return {
    protocol_version: PROTOCOL_VERSION,
    extension_version: EXTENSION_VERSION,
    command_id: command?.id ?? null,
    command_type: command?.command_type ?? null,
    task_id: command?.task_id ?? payload.task_id ?? null,
    run_id: command?.run_id ?? payload.run_id ?? null,
    session_id: command?.session_id ?? payload.session_id ?? null,
    authority: commandAuthority(command),
    before: before ?? null,
    after: after ?? null,
    verification: verification ?? { status: "unverified" },
    recorded_at: new Date().toISOString(),
  };
}

/**
 * Narrow retry classifier: transient transport / server conditions retry,
 * client errors (bad selector, stale lease, ...) never do.
 */
export function isRetryableFailure(error) {
  if (!error) return false;
  const status = Number(error.status);
  if (Number.isFinite(status) && status > 0) {
    return status === 408 || status === 425 || status === 429 || status >= 500;
  }
  const msg = String(error.message || error || "").toLowerCase();
  const code = String(error.code || "").toLowerCase();
  if (code.includes("timeout") || code.includes("network") || code.includes("temporary")) return true;
  return (
    msg.includes("timeout") ||
    msg.includes("timed out") ||
    msg.includes("network") ||
    msg.includes("temporarily") ||
    msg.includes("failed to fetch") ||
    (msg.includes("websocket") && (msg.includes("disconnect") || msg.includes("closed")))
  );
}

/**
 * Lifecycle snapshot. Accepts both the operator-state shape used by
 * background.js ({ phase, command, transport, selectedTab, error }) and the
 * workspace shape ({ status, sessionId, taskId, tabGroupId, ... }).
 */
export function lifecycleSnapshot(state = {}) {
  const command = state.command || null;
  return {
    protocol_version: PROTOCOL_VERSION,
    extension_version: EXTENSION_VERSION,
    status: state.status || state.phase || "disconnected",
    phase: state.phase ?? null,
    command_id: command?.id ?? state.commandId ?? state.command_id ?? null,
    command_type: command?.command_type ?? state.commandType ?? state.command_type ?? null,
    transport: state.transport ?? null,
    selected_tab: state.selectedTab ?? state.selected_tab ?? null,
    error: state.error ?? null,
    session_id: state.sessionId || state.session_id || null,
    task_id: state.taskId || state.task_id || command?.task_id || null,
    run_id: state.runId || state.run_id || command?.run_id || null,
    tab_group_id: state.tabGroupId || state.tab_group_id || null,
    primary_tab_id: state.primaryTabId || state.primary_tab_id || null,
    tab_ids: state.tabIds || state.tab_ids || [],
    updated_at: new Date().toISOString(),
  };
}

/** Key for per-session command ordering. Accepts (sessionId, commandId) or a command object. */
export function sessionCommandKey(sessionOrCommand, commandId) {
  if (sessionOrCommand && typeof sessionOrCommand === "object") {
    const c = sessionOrCommand;
    return `${c.session_id || c.payload?.session_id || "nosession"}:${c.id || "nocommand"}`;
  }
  return `${sessionOrCommand || "nosession"}:${commandId || "nocommand"}`;
}
