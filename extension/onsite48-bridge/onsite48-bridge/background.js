// extension/onsite48-bridge/background.js

import {
  PROTOCOL_VERSION,
  TERMINAL_COMMAND_STATES,
  commandKey,
  evidenceEnvelope,
  isRetryableFailure,
  lifecycleSnapshot,
  sessionCommandKey,
  validateCommand,
} from "./protocol.js";
import { getSessionState, authorizeSession as sessionAuthorize } from "./session-state.js";
import {
  createTaskWorkspace,
  openTaskTab,
  resolveTaskTab,
  getWorkspace,
  upsertWorkspace,
  markWorkspaceStatus,
  destroyTaskWorkspace,
  workspaceLifecycleSnapshot,
  loadWorkspaces,
  saveWorkspaces,
} from "./workspace.js";
import { normalizeLegacyCommand } from "./protocol.js";

const DEFAULT_API_BASE = "http://localhost";
const POLL_ALARM = "onsite48-bridge-poll";
const OPERATOR_STATE_KEY = "operatorState";
const SELECTED_TAB_KEY = "selectedTab";
const OPERATOR_MODE_KEY = "operatorMode"; // "active_tab" (default) | "selected_only"
const MAX_VISIBLE_TEXT = 30000;
const MAX_SCREENSHOT_DATA_URL = 5_000_000;
const MAX_TASK_TABS = 3;
const TASK_CONTEXT_KEY = "taskContext";
const OPERATION_EVENTS = {
  NAVIGATE_START: "navigate_start",
  NAVIGATE_END: "navigate_end",
  CLICK_START: "click_start",
  CLICK_END: "click_end",
  FILL_START: "fill_start",
  FILL_END: "fill_end",
  READ_START: "read_start",
  READ_END: "read_end",
  SCROLL: "scroll",
  OBSERVE: "observe",
  CAPTURE: "capture",
  CONTROL_MEDIA: "control_media",
  CAPTCHA_DETECTED: "captcha_detected",
  MFA_DETECTED: "mfa_detected",
  ERROR: "error",
  RECOVERY: "recovery",
};
const COMMAND_RECEIPTS_KEY = "commandReceiptsV2";
const STAGED_UPLOAD_KEY = "stagedUploadV1";
const MAX_COMMAND_RECEIPTS = 120;

// ─── PERSISTENCE KEYS ──────────────────────────────────────────────
const COMMAND_RETRY_STATE_KEY = "commandRetryStateV1";
const COMMANDS_IN_FLIGHT_KEY = "commandsInFlightV1";
const SESSION_SEQUENCE_STATE_KEY = "sessionSequenceStateV1";

async function persistCommandRetryState() {
  const data = {};
  for (const [id, info] of commandRetryState) {
    data[id] = { attempts: info.attempts, nextDelay: info.nextDelay, timer: !!info.timer };
  }
  await chrome.storage.local.set({ [COMMAND_RETRY_STATE_KEY]: data });
}

async function restoreCommandRetryState() {
  // Transient local retry state must not survive service-worker death.
  // Backend leases + terminal receipts are the durable source of truth.
  await chrome.storage.local.remove(COMMAND_RETRY_STATE_KEY);
  commandRetryState.clear();
}

async function persistCommandsInFlight() {
  await chrome.storage.local.set({ [COMMANDS_IN_FLIGHT_KEY]: Array.from(commandsInFlight) });
}

async function restoreCommandsInFlight() {
  const stored = await chrome.storage.local.get(COMMANDS_IN_FLIGHT_KEY);
  const stale = Array.isArray(stored[COMMANDS_IN_FLIGHT_KEY]) ? stored[COMMANDS_IN_FLIGHT_KEY] : [];
  await chrome.storage.local.remove(COMMANDS_IN_FLIGHT_KEY);
  commandsInFlight.clear();

  if (stale.length) {
    await setOperatorState({
      lastError: "Recovered from interrupted commands. Waiting for backend redelivery under a fresh lease."
    });
  }
}

async function persistSessionSequenceState() {
  const data = {};
  for (const [key, value] of sessionSequenceState) {
    data[key] = value;
  }
  await chrome.storage.local.set({ [SESSION_SEQUENCE_STATE_KEY]: data });
}

async function restoreSessionSequenceState() {
  const stored = await chrome.storage.local.get(SESSION_SEQUENCE_STATE_KEY);
  const data = stored[SESSION_SEQUENCE_STATE_KEY] || {};
  sessionSequenceState.clear();
  for (const [key, value] of Object.entries(data)) {
    sessionSequenceState.set(key, value);
  }
}

// ─── RECONNECTION CONSTANTS ──────────────────────────────────────
const BRIDGE_WS_PROTOCOL = "onsite48-bridge.v1";
const BRIDGE_WS_KEEPALIVE_MS = 20_000;
const BRIDGE_WS_RECONNECT_MIN_MS = 1_000;
const BRIDGE_WS_RECONNECT_MAX_MS = 60_000;
const BRIDGE_WS_RECONNECT_MULTIPLIER = 1.8;
const BRIDGE_WS_MAX_RECONNECT_ATTEMPTS = 10;

// ─── COMMAND RETRY CONSTANTS ──────────────────────────────────────
const COMMAND_RETRY_MAX_ATTEMPTS = 3;
const COMMAND_RETRY_DELAY_MS = 2000;
const COMMAND_RETRY_MULTIPLIER = 2.0;
const COMMAND_RETRY_MAX_DELAY_MS = 16000;

// ─── State ────────────────────────────────────────────────────────
let bridgeSocket = null;
let bridgeSocketKeepalive = null;
let bridgeReconnectTimer = null;
let bridgeReconnectDelayMs = BRIDGE_WS_RECONNECT_MIN_MS;
let bridgeReconnectAttempts = 0;
let bridgeReconnectCancelled = false;
let isShuttingDown = false;
const injectedOverlayTabs = new Set();
let pollInFlight = false;
// activeCommand is now a Map keyed by session_id to support concurrent commands per session
const activeCommands = new Map();
const bridgeSocketWaiters = new Map();
const commandsInFlight = new Set();
const pendingRequests = new Map();
const commandRetryState = new Map();
const sessionSequenceState = new Map();
let liveStateTimer = null;
let lastLiveSnapshot = null;
let lastLiveSnapshotAt = 0;
let activeBrowserSessionId = null;
let activeContext = { tabId: null, url: null, title: null, hostname: null, lastUpdated: 0 };

// ─── Reconnection Helpers ────────────────────────────────────────

function resetReconnectionState() {
  bridgeReconnectDelayMs = BRIDGE_WS_RECONNECT_MIN_MS;
  bridgeReconnectAttempts = 0;
  bridgeReconnectCancelled = false;
}

function scheduleBridgeReconnect() {
  if (isShuttingDown) {
    // Allow reconnect after permanent disconnect once pair/enable is called again
    // (reset happens in pair/enable paths)
    return;
  }
  if (bridgeReconnectTimer) return;
  
  const baseDelay = Math.min(bridgeReconnectDelayMs, BRIDGE_WS_RECONNECT_MAX_MS);
  const jitter = baseDelay * (0.8 + Math.random() * 0.4);
  const delay = Math.round(jitter);
  
  bridgeReconnectAttempts += 1;
  
  if (bridgeReconnectAttempts > BRIDGE_WS_MAX_RECONNECT_ATTEMPTS) {
    const extendedDelay = 5 * 60 * 1000;
    console.warn(`Onsite48 Bridge: Max reconnection attempts (${BRIDGE_WS_MAX_RECONNECT_ATTEMPTS}) reached. Waiting ${extendedDelay/1000}s before next attempt.`);
    bridgeReconnectTimer = setTimeout(connectBridgeSocket, extendedDelay);
    bridgeReconnectAttempts = 0;
    return;
  }
  
  console.debug(`Onsite48 Bridge: Scheduling reconnect in ${Math.round(delay)}ms (attempt ${bridgeReconnectAttempts}/${BRIDGE_WS_MAX_RECONNECT_ATTEMPTS})`);
  
  bridgeReconnectTimer = setTimeout(() => {
    bridgeReconnectTimer = null;
    void connectBridgeSocket();
  }, delay);
  
  bridgeReconnectDelayMs = Math.min(
    bridgeReconnectDelayMs * BRIDGE_WS_RECONNECT_MULTIPLIER,
    BRIDGE_WS_RECONNECT_MAX_MS
  );
}

function cancelBridgeReconnect() {
  if (bridgeReconnectTimer) {
    clearTimeout(bridgeReconnectTimer);
    bridgeReconnectTimer = null;
  }
  bridgeReconnectCancelled = true;
}

function getReconnectionStatus() {
  return {
    attempts: bridgeReconnectAttempts,
    currentDelay: bridgeReconnectDelayMs,
    maxAttempts: BRIDGE_WS_MAX_RECONNECT_ATTEMPTS,
    isScheduled: !!bridgeReconnectTimer,
    isConnected: websocketReady(),
    isShuttingDown: isShuttingDown,
  };
}

// ─── Command Retry Helpers ────────────────────────────────────────

function shouldRetryCommand(error, command) {
  if (error?.cancelled || command?.cancelled) return false;
  if (error?.code === "stale_lease" || error?.status === 409) return false;
  if (error?.code === "selected_tab_permission_required") return false;
  if (error?.code === "selected_tab_required") return false;
  return isRetryableFailure(error);
}

function getRetryDelay(attempt) {
  const baseDelay = COMMAND_RETRY_DELAY_MS * Math.pow(COMMAND_RETRY_MULTIPLIER, attempt - 1);
  const jitter = baseDelay * (0.8 + Math.random() * 0.4);
  return Math.min(Math.round(jitter), COMMAND_RETRY_MAX_DELAY_MS);
}

async function cancelRetry(commandId) {
  const retryInfo = commandRetryState.get(commandId);
  if (retryInfo) {
    if (retryInfo.timer) {
      clearTimeout(retryInfo.timer);
    }
    commandRetryState.delete(commandId);
    await persistCommandRetryState();
  }
}

function getCommandRetryStatus() {
  const status = [];
  for (const [id, info] of commandRetryState) {
    status.push({
      commandId: id,
      attempts: info.attempts,
      nextRetryIn: info.timer ? "scheduled" : "none",
    });
  }
  return status;
}

function classifyFailure(error, result) {
  if (!error && !result) return { code: "unknown", category: "unknown", retryable: false, message: "Unknown failure" };
  
  const message = (error instanceof Error ? error.message : String(error || "")) || String(result || "");
  const lower = message.toLowerCase();
  
  if (/timeout|timed?\s*out/.test(lower)) {
    return { code: "tool_timeout", category: "transient", retryable: true, message };
  }
  if (/connector|not\s+connected|connection/.test(lower)) {
    return { code: "connector_not_connected", category: "transient", retryable: true, message, reconnect_required: true };
  }
  if (/permission|auth|unauthorized|forbidden/.test(lower)) {
    return { code: "permission_denied", category: "permanent", retryable: false, message };
  }
  if (/argument|invalid|validation|schema/.test(lower)) {
    return { code: "argument_error", category: "fixable", retryable: false, message };
  }
  if (/not\s+found|missing|absent/.test(lower)) {
    return { code: "missing_data", category: "missing_data", retryable: false, message };
  }
  if (/state|stale|expired/.test(lower)) {
    return { code: "wrong_state", category: "state", retryable: false, message };
  }
  if (/verification|postcondition|failed/.test(lower)) {
    return { code: "verification_failed", category: "verification", retryable: false, message };
  }
  if (/cancelled|stopped|user/.test(lower)) {
    return { code: "user_cancelled", category: "cancelled", retryable: false, message };
  }
  
  return { code: "tool_execution_failed", category: "unknown", retryable: false, message };
}

async function commandReceipts() {
  const stored = await chrome.storage.local.get(COMMAND_RECEIPTS_KEY);
  const receipts = stored[COMMAND_RECEIPTS_KEY];
  return receipts && typeof receipts === "object" ? receipts : {};
}

async function hasTerminalReceipt(command) {
  const receipt = (await commandReceipts())[commandKey(command)];
  return Boolean(receipt && TERMINAL_COMMAND_STATES.has(receipt.state));
}

async function recordTerminalReceipt(command, state, detail = null) {
  const key = commandKey(command);
  const receipts = await commandReceipts();
  receipts[key] = {
    state,
    command_id: command.id,
    lease_token: command.lease_token || command.leaseToken || null,
    detail: detail ? String(detail).slice(0, 500) : null,
    completed_at: new Date().toISOString(),
  };
  const compacted = Object.entries(receipts)
    .sort(([, left], [, right]) => (Date.parse(right.completed_at) || 0) - (Date.parse(left.completed_at) || 0))
    .slice(0, MAX_COMMAND_RECEIPTS);
  await chrome.storage.local.set({ [COMMAND_RECEIPTS_KEY]: Object.fromEntries(compacted) });
}

function clearBridgeKeepalive() {
  if (bridgeSocketKeepalive) {
    clearInterval(bridgeSocketKeepalive);
    bridgeSocketKeepalive = null;
  }
}

function rejectBridgeWaiters(message) {
  for (const waiter of bridgeSocketWaiters.values()) {
    waiter.reject(new Error(message));
  }
  bridgeSocketWaiters.clear();
}

function bridgeWaiterKey(type, commandId) {
  return `${type}:${commandId}`;
}

async function sendBridgeEvent(event) {
  if (!websocketReady()) return false;
  try {
    bridgeSocket.send(JSON.stringify({ protocol_version: PROTOCOL_VERSION, ...event }));
    return true;
  } catch {
    return false;
  }
}

async function bridgeRequest(type, commandId, payload = {}) {
  if (!websocketReady()) throw new Error("Onsite48 Bridge WebSocket is not connected.");
  const key = bridgeWaiterKey(type, commandId);
  if (bridgeSocketWaiters.has(key)) throw new Error("A matching Onsite48 Bridge request is already pending.");
  
  return new Promise((resolve, reject) => {
    const timer = setTimeout(() => {
      bridgeSocketWaiters.delete(key);
      reject(new Error("Onsite48 Bridge WebSocket acknowledgement timed out."));
    }, 15_000);
    bridgeSocketWaiters.set(key, {
      resolve: (value) => { clearTimeout(timer); resolve(value); },
      reject: (error) => { clearTimeout(timer); reject(error); },
      timestamp: Date.now(),
    });
    try {
      bridgeSocket.send(JSON.stringify({ ...payload, protocol_version: PROTOCOL_VERSION, type, command_id: commandId }));
    } catch (error) {
      bridgeSocketWaiters.delete(key);
      clearTimeout(timer);
      reject(error);
    }
  });
}

function cleanStaleWaiters() {
  const now = Date.now();
  for (const [key, waiter] of bridgeSocketWaiters) {
    if (waiter.timestamp && now - waiter.timestamp > 30000) {
      bridgeSocketWaiters.delete(key);
      waiter.reject(new Error("Request timed out"));
    }
  }
}

async function settings() {
  const stored = await chrome.storage.local.get([
    "apiBase", "bridgeToken", "enabled", "device", OPERATOR_STATE_KEY, SELECTED_TAB_KEY, OPERATOR_MODE_KEY,
  ]);
  const rawBase = typeof stored.apiBase === "string" && stored.apiBase.trim() ? stored.apiBase.trim() : DEFAULT_API_BASE;
  return {
    apiBase: rawBase.replace(/\/$/, ""),
    bridgeToken: stored.bridgeToken || "",
    enabled: Boolean(stored.enabled),
    device: stored.device || null,
    operatorState: stored[OPERATOR_STATE_KEY] || { phase: "idle", paused: false },
    selectedTab: stored[SELECTED_TAB_KEY] || null,
    operatorMode: stored[OPERATOR_MODE_KEY] === "selected_only" ? "selected_only" : "active_tab",
    reconnection: getReconnectionStatus(),
    commandRetry: getCommandRetryStatus(),
  };
}

async function setOperatorState(update) {
  const { operatorState } = await settings();
  const next = { ...operatorState, ...update, updatedAt: new Date().toISOString() };
  const command = next.lastCommand
    ? { id: next.lastCommand.id, command_type: next.lastCommand.type }
    : null;
  next.protocolVersion = PROTOCOL_VERSION;
  next.lifecycle = lifecycleSnapshot({
    phase: next.phase,
    command,
    transport: next.transport || "offline",
    selectedTab: next.selectedTab || null,
    error: next.lastError || null,
  });
  await chrome.storage.local.set({ [OPERATOR_STATE_KEY]: next });
  return next;
}

async function api(path, options = {}) {
  const { apiBase, bridgeToken } = await settings();
  if (!bridgeToken) throw new Error("Onsite48 Bridge is not paired.");
  const response = await fetch(`${apiBase}${path}`, {
    ...options,
    headers: { "Content-Type": "application/json", "X-Synth-Bridge-Token": bridgeToken, ...(options.headers || {}) },
  });
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    const detail = body.detail;
    const code = detail && typeof detail === "object" ? detail.code : null;
    const message = detail && typeof detail === "object"
      ? (detail.message || JSON.stringify(detail))
      : (detail || "Onsite48 Bridge request failed.");
    const error = new Error(typeof message === "string" ? message : "Onsite48 Bridge request failed.");
    error.status = response.status;
    error.code = code;
    throw error;
  }
  return response.json();
}

function websocketUrl(apiBase) {
  // Preserve any subpath on apiBase; only change protocol and append /ws/bridge if needed
  const base = String(apiBase || DEFAULT_API_BASE).replace(/\/$/, "");
  const url = new URL(base.startsWith("http") ? base : `https://${base}`);
  url.protocol = url.protocol === "https:" ? "wss:" : "ws:";
  // Append /ws/bridge relative to current pathname
  const path = url.pathname.replace(/\/$/, "");
  url.pathname = path.endsWith("/ws/bridge") ? path : `${path}/ws/bridge`;
  url.search = "";
  url.hash = "";
  return url.toString();
}

function websocketReady() {
  return bridgeSocket?.readyState === WebSocket.OPEN;
}

async function setTransportState(transport, extra = {}) {
  await setOperatorState({ transport, ...extra });
}

async function verifyBridgeAuthorization() {
  try {
    await api("/api/bridge/extension/status");
    return true;
  } catch (error) {
    if (error?.status === 401 || error?.status === 403) {
      cancelBridgeReconnect();
      clearBridgeKeepalive();
      await setTransportState("pairing_required", {
        lastError: "Onsite48 Bridge pairing is no longer authorized. Pair this browser again from Onsite48 Settings.",
        pairingRequiredAt: new Date().toISOString(),
      });
      return false;
    }
    // A transient API outage should not make the extension forget a valid pair.
    return true;
  }
}

async function connectBridgeSocket() {
  // Allow reconnect after a previous permanent disconnect (e.g. re-pair)
  isShuttingDown = false;

  const { apiBase, bridgeToken, enabled } = await settings();
  if (!enabled || !bridgeToken || websocketReady() || bridgeSocket?.readyState === WebSocket.CONNECTING) {
    if (websocketReady()) resetReconnectionState();
    return false;
  }
  
  bridgeReconnectCancelled = false;
  
  try {
    const socket = new WebSocket(websocketUrl(apiBase), [BRIDGE_WS_PROTOCOL]);
    bridgeSocket = socket;
    
    socket.onopen = async () => {
      // Authenticate with first message (token no longer in subprotocol)
      try {
        socket.send(JSON.stringify({
          type: "auth",
          token: bridgeToken,
          protocol_version: PROTOCOL_VERSION,
        }));
      } catch (err) {
        console.warn("Failed to send WebSocket auth", err);
      }
      resetReconnectionState();
      bridgeReconnectDelayMs = BRIDGE_WS_RECONNECT_MIN_MS;
      clearBridgeKeepalive();
      bridgeSocketKeepalive = setInterval(() => { void sendBridgeEvent({ type: "keepalive" }); }, BRIDGE_WS_KEEPALIVE_MS);
      await setTransportState("websocket", { 
        lastTransportConnectedAt: new Date().toISOString(), 
        lastError: null,
        reconnectAttempts: bridgeReconnectAttempts,
      });
      startLiveStateStream();
    };
    
    socket.onmessage = async (message) => {
      let event;
      try { event = JSON.parse(String(message.data || "{}")); } catch { return; }
      if (event.type === "auth_ack") {
        // Authenticated successfully
        return;
      }
      if (event.type === "auth_reject") {
        console.warn("WebSocket auth rejected — re-pair required");
        await setOperatorState({ lastError: "Bridge authentication rejected. Re-pair the extension." });
        return;
      }
      const commandId = event.command_id;
      if (event.type === "command_queued" && event.command) {
        void processCommand(event.command, "websocket");
        return;
      }
      if (event.type === "command_preflight_result" && commandId) {
        const waiter = bridgeSocketWaiters.get(bridgeWaiterKey("command_preflight", commandId));
        if (waiter) { bridgeSocketWaiters.delete(bridgeWaiterKey("command_preflight", commandId)); waiter.resolve(event); }
        return;
      }
      if ((event.type === "command_completed" || event.type === "command_failed" || event.type === "command_cancelled") && commandId && event.status) {
        const requestType = event.type === "command_cancelled" ? "command_cancel" : event.type;
        const waiter = bridgeSocketWaiters.get(bridgeWaiterKey(requestType, commandId));
        if (waiter) { bridgeSocketWaiters.delete(bridgeWaiterKey(requestType, commandId)); waiter.resolve(event); }
      }
    };
    
    socket.onerror = () => undefined;
    
    socket.onclose = async (event) => {
      if (isShuttingDown || bridgeReconnectCancelled) return;
      if (bridgeSocket === socket) bridgeSocket = null;
      clearBridgeKeepalive();
      stopLiveStateStream();
      rejectBridgeWaiters("Onsite48 Bridge WebSocket disconnected.");
      if (!(await verifyBridgeAuthorization())) return;
      await setTransportState("poll_fallback", { 
        lastTransportDisconnectedAt: new Date().toISOString(),
        closeCode: event.code,
        closeReason: event.reason || "No close reason supplied",
      });
      scheduleBridgeReconnect();
    };
    
    return true;
  } catch (error) {
    console.error("Onsite48 Bridge WebSocket connection error:", error);
    await setTransportState("poll_fallback");
    scheduleBridgeReconnect();
    return false;
  }
}

async function disconnectBridgeSocket(permanent = false) {
  if (permanent) isShuttingDown = true;
  cancelBridgeReconnect();
  clearBridgeKeepalive();
  const socket = bridgeSocket;
  bridgeSocket = null;
  if (socket && socket.readyState <= WebSocket.OPEN) {
    socket.close(1000, permanent ? "Permanent disconnect requested" : "Temporary disconnect");
  }
  if (permanent) resetReconnectionState();
}

function publicUrl(value) {
  try {
    const parsed = new URL(value);
    return parsed.protocol === "http:" || parsed.protocol === "https:";
  } catch {
    return false;
  }
}

function tabSummary(tab) {
  if (!tab || !publicUrl(tab.url || "")) return null;
  const parsed = new URL(tab.url);
  return {
    id: tab.id,
    windowId: tab.windowId,
    hostname: parsed.hostname,
    title: (tab.title || "Untitled page").slice(0, 180),
    url: tab.url,
    active: Boolean(tab.active),
  };
}

async function activeTab() {
  const [tab] = await chrome.tabs.query({ active: true, lastFocusedWindow: true });
  if (!tab?.id || !publicUrl(tab.url || "")) {
    throw new Error("Open a normal public website tab before allowing Onsite48 Bridge access.");
  }
  return tab;
}

function selectedTabError(code, message) {
  const error = new Error(message);
  error.code = code;
  return error;
}

function selectedTabSnapshot(tab, selectedAt = new Date().toISOString()) {
  const summary = tabSummary(tab);
  if (!summary || !Number.isInteger(tab.id) || !Number.isInteger(tab.windowId)) {
    throw selectedTabError("selected_tab_required", "Focus a normal public website tab before resuming.");
  }
  return {
    tabId: tab.id,
    windowId: tab.windowId,
    hostname: summary.hostname,
    title: summary.title,
    selectedAt,
    lastValidatedAt: new Date().toISOString(),
  };
}

async function updateActiveContext(tabId) {
  try {
    const tab = await chrome.tabs.get(tabId);
    if (!tab?.url || !publicUrl(tab.url)) return;
    activeContext = {
      tabId: tab.id,
      url: tab.url,
      title: tab.title || "",
      hostname: new URL(tab.url).hostname,
      lastUpdated: Date.now(),
    };
  } catch {
    // Tab might be closed
  }
}

async function getActiveContext() {
  if (activeContext.tabId && activeContext.lastUpdated > Date.now() - 30000) {
    return activeContext;
  }
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  if (tab?.url && publicUrl(tab.url)) {
    activeContext = {
      tabId: tab.id,
      url: tab.url,
      title: tab.title || "",
      hostname: new URL(tab.url).hostname,
      lastUpdated: Date.now(),
    };
  }
  return activeContext;
}

const TASK_CONTEXT_DEFAULTS = {
  taskId: null, threadId: null, taskGoal: null,
  plannedSteps: [], completedSteps: [], currentStep: null,
  tabHistory: [], activeTab: null, lockedTab: null, sessionStartAt: null,
};

async function getTaskContext() {
  const { [TASK_CONTEXT_KEY]: ctx } = await chrome.storage.local.get(TASK_CONTEXT_KEY);
  return { ...TASK_CONTEXT_DEFAULTS, ...ctx };
}

async function setTaskContext(updates) {
  const current = await getTaskContext();
  await chrome.storage.local.set({ [TASK_CONTEXT_KEY]: { ...current, ...updates, lastUpdated: Date.now() } });
}

async function startTask(taskId, threadId, goal) {
  await setTaskContext({ taskId, threadId, taskGoal: goal, sessionStartAt: Date.now(), plannedSteps: [], completedSteps: [], currentStep: null, tabHistory: [] });
}

async function recordTabSwitch(tabId, url, title) {
  const ctx = await getTaskContext();
  ctx.tabHistory.push({ tabId, url, title, timestamp: Date.now() });
  if (ctx.tabHistory.length > 20) ctx.tabHistory.shift();
  await setTaskContext({ activeTab: tabId, tabHistory: ctx.tabHistory });
}

async function completeTask(status) {
  await setTaskContext({
    ...TASK_CONTEXT_DEFAULTS,
    lastTaskStatus: status || null,
    lastTaskCompletedAt: Date.now(),
  });
}

async function emitOperationEvent(event) {
  const typedEvent = {
    ...event,
    eventId: `${Date.now()}-${Math.random().toString(36).slice(2, 8)}`,
    timestamp: Date.now(),
    sessionId: activeBrowserSessionId || null,
  };
  // Capped ring buffer — never create unique keys per event
  try {
    const stored = await chrome.storage.local.get("operationEvents");
    const events = Array.isArray(stored.operationEvents) ? stored.operationEvents : [];
    const next = [...events, typedEvent].slice(-200);
    await chrome.storage.local.set({ operationEvents: next });
  } catch (err) {
    console.warn("emitOperationEvent storage failed", err);
  }
  if (bridgeSocket && bridgeSocket.readyState === WebSocket.OPEN) {
    try {
      bridgeSocket.send(JSON.stringify({ type: "operation_event", event: typedEvent }));
    } catch { /* ignore */ }
  }
}

async function detectChallenges(tabId) {
  // Prefer content-script DOM detection; fall back to injected function.
  try {
    const response = await chrome.tabs.sendMessage(tabId, { type: "detectChallenges" });
    if (response && Array.isArray(response.challenges)) {
      return response.challenges;
    }
  } catch {
    // Content script may not be injected yet
  }
  try {
    const [{ result }] = await chrome.scripting.executeScript({
      target: { tabId },
      func: () => {
        const challenges = [];
        if (document.querySelector(".g-recaptcha, iframe[src*='recaptcha']")) {
          challenges.push({ type: "recaptcha", severity: "block", reason: "reCAPTCHA detected" });
        }
        if (document.querySelector(".h-captcha, iframe[src*='hcaptcha']")) {
          challenges.push({ type: "hcaptcha", severity: "block", reason: "hCaptcha detected" });
        }
        if (document.querySelector("iframe[src*='turnstile'], [data-sitekey]")) {
          challenges.push({ type: "turnstile", severity: "block", reason: "Turnstile detected" });
        }
        if (document.querySelector("iframe[src*='captcha']")) {
          challenges.push({ type: "captcha", severity: "block", reason: "CAPTCHA iframe detected" });
        }
        if (document.querySelector("input[autocomplete='one-time-code'], input[autocomplete='webauthn']")) {
          challenges.push({ type: "otp", severity: "block", reason: "OTP / WebAuthn detected" });
        }
        return challenges;
      },
    });
    return result || [];
  } catch {
    return [];
  }
}

async function saveSelectedTab(tab) {
  const snapshot = selectedTabSnapshot(tab);
  await chrome.storage.local.set({ [SELECTED_TAB_KEY]: snapshot });
  return snapshot;
}

async function clearSelectedTab(selectionState = "closed") {
  const { selectedTab } = await settings();
  await chrome.storage.local.remove(SELECTED_TAB_KEY);
  await setOperatorState({
    selectedTab: null,
    selectionState,
    lastError: selectionState === "closed" ? "The focused browser page was closed. Focus the intended page before resuming." : null,
  });
  try {
    await api("/api/bridge/extension/context", {
      method: "POST",
      body: JSON.stringify({ selection_state: selectionState, selected_at: selectedTab?.selectedAt || null }),
    });
  } catch {
    // A disconnected bridge will resync selection state on its next successful poll.
  }
}

async function reportTabContext(tab, selected) {
  try {
    await api("/api/bridge/extension/context", {
      method: "POST",
      body: JSON.stringify({
        selection_state: "selected",
        tab_id: selected.tabId,
        window_id: selected.windowId,
        url: tab.url || "",
        title: selected.title || "",
        selected_at: selected.selectedAt,
      }),
    });
  } catch {
    // Offline reporting is best-effort; the next successful poll will refresh.
  }
}

async function bindActiveTabForOperator(command = null) {
  const tab = await activeTab();
  try {
    await ensureSitePermission(tab);
  } catch (error) {
    await setOperatorState({ selectedTab: null, selectionState: "permission_required" });
    throw error;
  }
  const selected = selectedTabSnapshot(tab);
  const { operatorMode } = await settings();
  // Active-tab mode is session-scoped, like Manus Browser Operator: switching
  // focus changes the target without creating or refreshing a persistent lock.
  // Keep the selected-tab record only for the explicit legacy/precision mode.
  if (operatorMode === "selected_only") {
    await chrome.storage.local.set({ [SELECTED_TAB_KEY]: selected });
  }
  await setOperatorState({ selectedTab: operatorMode === "selected_only" ? selected : null, selectionState: "active", lastError: null });
  await reportTabContext(tab, selected);
  return { tab, selected };
}

async function selectedTabForCommand(command = null) {
  // Manus-style: task-owned workspace is the primary identity.
  // NEVER derive execution target from the user's currently focused tab
  // when an explicit session_id is present. Fail closed.
  const sessionId = String(
    command?.payload?.session_id || command?.session_id || ""
  ).trim();
  // Only use activeBrowserSessionId as soft hint when no explicit session_id
  const softSessionId = sessionId || String(activeBrowserSessionId || "").trim();
  const preferredTabId = command?.payload?.tab_id ?? command?.payload?.target_tab ?? null;

  if (sessionId) {
    // EXPLICIT session_id provided → must resolve through workspace. Fail closed.
    const resolved = await resolveTaskTab(
      sessionId,
      preferredTabId != null ? Number(preferredTabId) : null
    );
    if (resolved.tab) {
      const tab = resolved.tab;
      const selected = {
        tabId: tab.id,
        windowId: tab.windowId,
        url: tab.url || "",
        title: tab.title || "",
        selectedAt: new Date().toISOString(),
        fromWorkspace: true,
        sessionId,
      };
      return { tab, selected };
    }
    // Explicit session but no tab → FAIL CLOSED. Never fall back to focused tab.
    throw selectedTabError(
      resolved.error || "workspace_not_found",
      resolved.message ||
        "Task workspace tab unavailable. The task session may have been stopped or the tab closed."
    );
  }

  // Soft session (activeBrowserSessionId only) — still prefer workspace
  if (softSessionId) {
    const resolved = await resolveTaskTab(
      softSessionId,
      preferredTabId != null ? Number(preferredTabId) : null
    );
    if (resolved.tab) {
      const tab = resolved.tab;
      return {
        tab,
        selected: {
          tabId: tab.id,
          windowId: tab.windowId,
          url: tab.url || "",
          title: tab.title || "",
          selectedAt: new Date().toISOString(),
          fromWorkspace: true,
          sessionId: softSessionId,
        },
      };
    }
    // Soft session missing → fall through only for workspace-creation commands
  }

  // Workspace creation commands do not need an existing tab target.
  const commandType = String(command?.command_type || command?.type || "");
  if (["create_task_workspace", "claim_task_workspace"].includes(commandType)) {
    return { tab: null, selected: null };
  }

  // Production rule: task execution requires an explicit, resolvable workspace.
  // Never derive execution context from the user's currently focused tab.
  throw selectedTabError(
    "selected_tab_required",
    "No task workspace is bound. Create or claim a task workspace before running browser commands."
  );
}

function optionalOrigin(tab) {
  const parsed = new URL(tab.url);
  return `${parsed.protocol}//${parsed.hostname}/*`;
}

async function ensureSitePermission(tab) {
  const origins = [optionalOrigin(tab)];
  if (await chrome.permissions.contains({ origins })) return true;
  throw selectedTabError("selected_tab_permission_required", "This site needs permission. Approve the Chrome prompt, then resume the task.");
}

async function ensureOperatorOverlay(tabId) {
  if (injectedOverlayTabs.has(tabId)) return;
  try {
    await chrome.scripting.executeScript({
      target: { tabId },
      files: ["operator-overlay.js"],
    });
    injectedOverlayTabs.add(tabId);
  } catch (err) {
    // Tab may not allow scripting (chrome:// etc.)
    console.warn("ensureOperatorOverlay failed", tabId, err);
  }
}

async function syncSelectedTab(tabId = null) {
  let tab;
  if (Number.isInteger(tabId)) {
    try {
      tab = await chrome.tabs.get(tabId);
    } catch {
      throw selectedTabError("selected_tab_closed", "The focused page was closed before Synth could act. Focus the intended page and resume.");
    }
    if (!publicUrl(tab.url || "")) {
      throw selectedTabError("selected_tab_required", "Focus a normal http or https page before resuming.");
    }
  } else {
    tab = await activeTab();
  }
  await ensureSitePermission(tab);
  await ensureOperatorOverlay(tab.id);
  const { operatorMode } = await settings();
  if (operatorMode === "selected_only") await saveSelectedTab(tab);
  else await chrome.storage.local.remove(SELECTED_TAB_KEY);
  return reportActiveContext();
}

async function reportActiveContext() {
  try {
    const { operatorMode, bridgeToken } = await settings();
    if (!bridgeToken) return null;

    const sessionState = await getSessionState();
    let tab = null;
    let selected = null;

    // When a browser session is active, ALWAYS resolve through the task workspace.
    // Never report the user's currently focused tab as the agent context.
    if (activeBrowserSessionId) {
      const resolved = await resolveTaskTab(activeBrowserSessionId, null);
      if (!resolved.tab) {
        // Workspace gone while session still active → fail closed, do not report focused tab
        await setOperatorState({ selectionState: "closed", selectedTab: null });
        return null;
      }
      tab = resolved.tab;
      selected = {
        tabId: tab.id,
        windowId: tab.windowId,
        url: tab.url || "",
        title: tab.title || "",
        selectedAt: new Date().toISOString(),
        fromWorkspace: true,
        sessionId: activeBrowserSessionId,
      };
    } else if (sessionState === "ACTIVE" || (operatorMode === "active_tab" && sessionState === "AUTHORIZING")) {
      await setOperatorState({
        selectedTab: null,
        selectionState: "required",
        lastError: "Session is active but no explicit task workspace is bound."
      });
      return null;
    } else {
      await setOperatorState({
        selectedTab: null,
        selectionState: "required",
        lastError: "No explicit task workspace is bound."
      });
      return null;
    }
    if (!tab?.id) {
      throw selectedTabError("selected_tab_required", "No tab is available to report context for.");
    }
    if (!await chrome.permissions.contains({ origins: [optionalOrigin(tab)] })) {
      await api("/api/bridge/extension/context", {
        method: "POST",
        body: JSON.stringify({ selection_state: "permission_required", selected_at: selected.selectedAt }),
      });
      await setOperatorState({
        selectedTab: selected,
        selectionState: "permission_required",
        sitePermissionPending: true,
        lastError: "This selected site needs permission. Focus it and choose Sync this tab.",
      });
      return null;
    }
    await api("/api/bridge/extension/context", {
      method: "POST",
      body: JSON.stringify({
        url: tab.url,
        title: tab.title || "",
        tab_id: selected.tabId,
        window_id: selected.windowId,
        selected_at: selected.selectedAt,
        selection_state: "selected",
      }),
    });
    await setOperatorState({
      selectedTab: selected,
      selectionState: "selected",
      sitePermissionPending: false,
      lastContextSyncAt: new Date().toISOString(),
      lastError: null,
    });
    return tab;
  } catch (error) {
    const message = error instanceof Error ? error.message : "No selected tab is available.";
    await setOperatorState({
      selectedTab: null,
      selectionState: error?.code === "selected_tab_closed" ? "closed" : "required",
      lastError: message,
    });
    return null;
  }
}

function liveStateDiff(previous, next) {
  if (!previous) return { changed: true, changed_fields: ["initial"], previous_digest: null, next_digest: next?.page_digest || null };
  const changedFields = [];
  for (const field of ["url", "title", "page_digest", "scroll_y", "scroll_height", "text_length", "element_count"]) {
    if (previous[field] !== next[field]) changedFields.push(field);
  }
  const previousElements = new Set((previous.elements || []).map((item) => item.fingerprint).filter(Boolean));
  const nextElements = new Set((next.elements || []).map((item) => item.fingerprint).filter(Boolean));
  const added = [...nextElements].filter((item) => !previousElements.has(item)).slice(0, 40);
  const removed = [...previousElements].filter((item) => !nextElements.has(item)).slice(0, 40);
  return { changed: changedFields.length > 0 || added.length > 0 || removed.length > 0, changed_fields: changedFields, added_element_fingerprints: added, removed_element_fingerprints: removed, previous_digest: previous.page_digest || null, next_digest: next.page_digest || null };
}

async function streamLiveTabState(reason = "timer") {
  if (!websocketReady() || activeCommands.size > 0 || (await settings()).operatorState?.paused) return;
  try {
    const { tab, selected } = await selectedTabForCommand({ command_type: "read_active_tab", payload: { resolve_mode: "active_tab" } });
    if (!tab?.id || !publicUrl(tab.url || "")) return;
    if (!await chrome.permissions.contains({ origins: [optionalOrigin(tab)] })) return;
    const snapshot = await executeInTab(tab.id, observeActionPage, [null]);
    const page = snapshot?.page || snapshot;
    if (!page || typeof page !== "object") return;
    const diff = liveStateDiff(lastLiveSnapshot, page);
    lastLiveSnapshot = page;
    lastLiveSnapshotAt = Date.now();
    if (!diff.changed && reason === "timer") return;
    const challenge = await executeInTab(tab.id, challengeState);
    await sendBridgeEvent({ type: "tab_state_diff", session_id: activeBrowserSessionId, tab_id: tab.id, window_id: tab.windowId, target_tab: selected, snapshot: { url: String(tab.url || "").slice(0, 2048), title: String(tab.title || "").slice(0, 240), page_digest: page.page_digest || null, scroll_y: page.scroll_y || 0, text_length: page.text_length || 0, element_count: page.element_count || 0, observed_at: new Date().toISOString() }, diff, challenge, reason });
    await setOperatorState({ liveTabState: { ...diff, observedAt: new Date().toISOString(), tabId: tab.id, challenge: challenge?.detected ? challenge : null } });
  } catch {
    // Live observation is opportunistic; durable commands remain authoritative.
  }
}

function startLiveStateStream() {
  if (liveStateTimer) return;
  void streamLiveTabState("connected");
  liveStateTimer = setInterval(() => { void streamLiveTabState("timer"); }, 2500);
}

function stopLiveStateStream() {
  if (liveStateTimer) clearInterval(liveStateTimer);
  liveStateTimer = null;
  lastLiveSnapshot = null;
}

function commandHost(command) {
  const expected = command.payload?.expected_hostname;
  return typeof expected === "string" ? expected.toLowerCase().replace(/\.$/, "") : "";
}

function verifyTrustedHost(tab, command) {
  const expected = commandHost(command);
  if (!expected) return;
  const current = new URL(tab.url).hostname.toLowerCase().replace(/\.$/, "");
  if (current !== expected) {
    throw selectedTabError("selected_tab_mismatch", `Onsite48 Bridge stopped because the selected tab changed from ${expected} to ${current}. Select/Sync the intended tab, then resume the task.`);
  }
}

function verifySessionOrigin(tab, command) {
  const grants = command?.payload?.session_origins;
  if (!Array.isArray(grants)) return;
  if (!grants.length) throw selectedTabError("selected_tab_mismatch", "This browser session has no authorized site origin.");
  const current = new URL(tab.url);
  const allowed = grants.some((grant) => {
    const raw = String(grant || "").trim().replace(/\/\*$/, "");
    if (!raw) return false;
    try {
      const parsed = new URL(raw.includes("://") ? raw : `https://${raw}`);
      if (parsed.hostname.startsWith("*.")) {
        const suffix = parsed.hostname.slice(1).toLowerCase();
        return current.protocol === parsed.protocol && current.hostname.toLowerCase().endsWith(suffix);
      }
      return current.origin === parsed.origin;
    } catch {
      return false;
    }
  });
  if (!allowed) throw selectedTabError("selected_tab_mismatch", `The focused tab origin ${current.origin} is outside this browser session's authorization scope.`);
}

async function executeInTab(tabId, func, args = []) {
  const operation = typeof func === "function" ? func.name : String(func || "");
  const [{ result }] = await chrome.scripting.executeScript({
    target: { tabId },
    func: (op, operationArgs) => {
      const args = Array.isArray(operationArgs) ? operationArgs : [];
      const clean = (value, limit = 160) => String(value || "").replace(/\s+/g, " ").trim().slice(0, limit);
      const visible = (element) => {
        if (!element || !element.isConnected) return false;
        const rect = element.getBoundingClientRect?.();
        if (!rect || rect.width <= 0 || rect.height <= 0) return false;
        const style = getComputedStyle(element);
        return style.display !== "none" && style.visibility !== "hidden" && style.opacity !== "0";
      };
      const hash = (value) => {
        let h = 2166136261;
        const input = String(value || "");
        for (let i = 0; i < input.length; i += 1) {
          h ^= input.charCodeAt(i);
          h = Math.imul(h, 16777619);
        }
        return `fnv1a-${(h >>> 0).toString(16)}`;
      };
      const selectorFor = (element) => {
        if (!element) return null;
        if (element.id) return `#${CSS.escape(element.id)}`;
        const testId = element.getAttribute("data-testid");
        if (testId && testId.length < 100) return `[data-testid="${CSS.escape(testId)}"]`;
        const name = element.getAttribute("name");
        if (name && /^[a-zA-Z0-9_-]{1,80}$/.test(name)) return `${element.tagName.toLowerCase()}[name="${CSS.escape(name)}"]`;
        const aria = element.getAttribute("aria-label");
        if (aria && aria.length < 90) return `${element.tagName.toLowerCase()}[aria-label="${CSS.escape(aria)}"]`;
        return null;
      };
      const fingerprint = (element, label = "") => hash([
        element?.tagName || "", element?.getAttribute?.("role") || "",
        element?.id || "", element?.getAttribute?.("name") || "",
        element?.getAttribute?.("aria-label") || "", element?.getAttribute?.("placeholder") || "",
        clean(element?.innerText || element?.textContent || "", 240), label
      ].join("|"));
      const mediaObservation = () => {
        const media = document.querySelector("audio, video");
        const hostname = location.hostname.toLowerCase();
        const title = [
          "ytmusic-player-bar .title", "ytmusic-player-bar #song-title",
          "[data-testid='now-playing-title']", "[data-testid='context-item-info-title']",
          "[aria-label*='Now playing' i]"
        ].map((s) => document.querySelector(s)).map((e) => visible(e) ? clean(e.innerText || e.textContent || e.getAttribute("aria-label"), 240) : "").find(Boolean) || "";
        const artists = [
          "ytmusic-player-bar .byline", "ytmusic-player-bar #byline",
          "[data-testid='now-playing-artist']", "[data-testid='context-item-info-subtitles']"
        ].map((s) => document.querySelector(s)).map((e) => visible(e) ? clean(e.innerText || e.textContent || e.getAttribute("aria-label"), 240) : "").find(Boolean) || "";
        const controls = [...document.querySelectorAll("button[aria-label], [role='button'][aria-label]")]
          .map((e) => String(e.getAttribute("aria-label") || ""))
          .filter((label) => /play|pause|shuffle|repeat/i.test(label)).slice(0, 24);
        if (!(title || artists || media || controls.length || hostname === "music.youtube.com")) return null;
        return {
          source: hostname, track_title: title || null, artists: artists || null,
          playing: media ? !media.paused : (controls.some((x) => /pause/i.test(x)) ? true : null),
          current_time_seconds: media && Number.isFinite(media.currentTime) ? Math.round(media.currentTime) : null,
          duration_seconds: media && Number.isFinite(media.duration) ? Math.round(media.duration) : null,
          controls,
        };
      };
      const meaningfulText = () => {
        const candidates = [];
        const selectors = ["main", "article", "[role='main']", "#content", ".content", "#main", ".main"];
        for (const selector of selectors) {
          for (const node of document.querySelectorAll(selector)) {
            if (visible(node)) {
              const value = clean(node.innerText || node.textContent, 30000);
              if (value) candidates.push({ source: selector, text: value });
            }
          }
        }
        const body = clean(document.body?.innerText || "", 30000);
        if (body) candidates.push({ source: "body", text: body });
        const aria = [...document.querySelectorAll("[aria-label], [role]")].slice(0, 600)
          .filter(visible)
          .map((e) => clean(e.getAttribute("aria-label") || e.getAttribute("role") || "", 240))
          .filter(Boolean)
          .join("\n");
        if (aria) candidates.push({ source: "aria", text: aria.slice(0, 12000) });
        candidates.sort((a, b) => b.text.length - a.text.length);
        return candidates[0] || { source: "none", text: "" };
      };
      const collectSurfaceEntries = (surface, maxItems = 20) => {
        const kind = String(surface || "page").toLowerCase();
        const selectors = kind === "notifications"
          ? ["[role='dialog'] [role='listitem']", "[role='dialog'] article", "[role='menu'] [role='menuitem']", "[role='listitem']", "article", "li"]
          : kind === "inbox"
            ? ["[role='main'] [role='row']", "[role='listitem']", "tr", "article", "li"]
            : kind === "table"
              ? ["[role='row']", "tr", "[role='gridcell']"]
              : kind === "feed"
                ? ["[role='feed'] > *", "[role='article']", "article", "[role='listitem']"]
                : ["main", "article", "[role='main']"];
        const limit = Math.max(1, Math.min(Number(maxItems) || 20, 50));
        const entries = [];
        const seen = new Set();
        for (const selector of selectors) {
          for (const element of document.querySelectorAll(selector)) {
            if (entries.length >= limit) break;
            if (seen.has(element) || !visible(element)) continue;
            const text = clean(element.innerText || element.textContent || "", 700);
            if (text.length < 3 || text.length > 1200) continue;
            const rect = element.getBoundingClientRect();
            const visibleHeight = Math.max(0, Math.min(rect.bottom, innerHeight) - Math.max(rect.top, 0));
            if (visibleHeight <= 0) continue;
            seen.add(element);
            entries.push({
              index: entries.length,
              text,
              tag: element.tagName.toLowerCase(),
              role: element.getAttribute("role") || "",
              aria_label: clean(element.getAttribute("aria-label") || "", 180),
              selector: selectorFor(element),
              fingerprint: fingerprint(element, kind),
              visible_fraction: Math.max(0, Math.min(1, visibleHeight / Math.max(1, rect.height))),
            });
          }
          if (entries.length >= limit) break;
        }
        return entries;
      };

      const collectElements = () => {
        const selector = [
          "a", "button", "input", "textarea", "select", "[contenteditable='true']",
          "[role='button']", "[role='link']", "[role='checkbox']", "[role='radio']",
          "[role='tab']", "[role='menuitem']", "[role='gridcell']", "[role='option']",
          "[aria-label]"
        ].join(",");
        const elements = [];
        const seen = new Set();
        for (const element of document.querySelectorAll(selector)) {
          if (elements.length >= 300 || seen.has(element) || !visible(element)) continue;
          seen.add(element);
          const rect = element.getBoundingClientRect();
          const role = element.getAttribute("role") || "";
          const aria = element.getAttribute("aria-label") || "";
          const text = clean(element.innerText || element.textContent || element.placeholder || aria, 220);
          if (!text && !aria && !["INPUT", "TEXTAREA", "SELECT"].includes(element.tagName)) continue;
          elements.push({
            index: elements.length,
            element_id: `el_${fingerprint(element)}_${elements.length}`,
            tag: element.tagName.toLowerCase(),
            role,
            type: element.type || "",
            name: clean(element.getAttribute("name"), 120),
            aria_label: clean(aria, 160),
            text,
            selector: selectorFor(element),
            rect: { x: Math.round(rect.x), y: Math.round(rect.y), width: Math.round(rect.width), height: Math.round(rect.height) },
            disabled: Boolean(element.disabled || element.getAttribute("aria-disabled") === "true"),
            fingerprint: fingerprint(element),
          });
        }
        return elements;
      };
      const observe = () => {
        const content = meaningfulText();
        const elements = collectElements();
        const root = document.documentElement;
        const text = content.text;
        return {
          url: location.href,
          title: clean(document.title, 240),
          ready_state: document.readyState,
          content_source: content.source,
          text,
          text_length: text.length,
          visible_text_length: text.length,
          scroll_y: Math.round(window.scrollY),
          scroll_height: Math.max(root?.scrollHeight || 0, document.body?.scrollHeight || 0),
          viewport_width: Math.round(window.innerWidth),
          viewport_height: Math.round(window.innerHeight),
          device_pixel_ratio: window.devicePixelRatio || 1,
          page_digest: hash(`${location.href}|${document.title}|${text}|${elements.map((e) => e.fingerprint).join(",")}`),
          element_count: elements.length,
          elements,
          media: mediaObservation(),
          observed_at: new Date().toISOString(),
        };
      };
      const actionSafety = (element, action) => {
        if (!element || !visible(element)) throw new Error("The requested browser target is not visible.");
        const form = element.closest?.("form");
        const signal = [
          element.type, element.name, element.id, element.autocomplete,
          element.getAttribute("aria-label"), element.placeholder,
          element.innerText, element.value, element.getAttribute("href"), form?.getAttribute("action")
        ].join(" ").toLowerCase();
        if (/password|passcode|otp|one.?time|verification|captcha|turnstile|hcaptcha|recaptcha|cvv|card.?number|payment|security.?code/.test(signal)) {
          throw new Error("Onsite48 Bridge requires the user to handle password, verification, CAPTCHA, payment, or security controls manually.");
        }
        if (element.matches("input[type='file']") || /upload|attach file|choose file/.test(signal)) {
          throw new Error("Onsite48 Bridge requires the user to handle file uploads manually.");
        }
        if (element.matches("a[download]") || /download|export file|save file/.test(signal)) {
          throw new Error("Onsite48 Bridge will not trigger downloads from the local browser.");
        }
        if (action === "click" && (element.type === "submit" || element.form ||
            /submit|checkout|buy|purchase|place order|pay now|send|post|publish|delete|remove|discard|confirm/.test(signal))) {
          throw new Error("Onsite48 Bridge will not click submission, purchase, send, deletion, or high-impact controls.");
        }
        if (action === "fill" && !element.matches("input:not([type]), input[type='text'], input[type='search'], input[type='email'], textarea, [contenteditable='true']")) {
          throw new Error("Onsite48 Bridge may type only into an ordinary text, search, email, textarea, or editable field.");
        }
      };
      const observeTarget = (selector) => {
        if (!selector) return null;
        const element = document.querySelector(selector);
        if (!element) return { selector, missing: true };
        return {
          selector, tag: element.tagName, role: element.getAttribute("role") || "",
          text: clean(element.innerText || element.getAttribute("aria-label") || element.placeholder || "", 160),
          fingerprint: fingerprint(element), visible: visible(element),
        };
      };
      const resolveActionTarget = (query, mode) => {
        const raw = String(query || "").trim();
        if (!raw) throw new Error("Onsite48 Bridge requires a visible target description.");
        try {
          const direct = document.querySelector(raw);
          if (direct && visible(direct)) return direct;
        } catch {
          // Human-readable targets are supported; invalid CSS falls through to matching.
        }
        const normalized = raw.toLowerCase().replace(/["']/g, "").replace(/\s+/g, " ").trim();
        const words = normalized.split(/\s+/).filter((word) => word.length > 1);
        const selector = mode === "fill"
          ? "input:not([type='hidden']), textarea, [contenteditable='true'], [role='textbox']"
          : "a, button, [role='button'], [role='link'], [role='tab'], [role='menuitem'], [role='option']";
        let best = null;
        let bestScore = 0;
        for (const element of [...document.querySelectorAll(selector)].filter(visible)) {
          const label = [element.innerText, element.textContent, element.getAttribute("aria-label"), element.getAttribute("title"), element.getAttribute("placeholder"), element.getAttribute("name"), element.id]
            .filter(Boolean).join(" ").toLowerCase().replace(/\s+/g, " ").trim();
          if (!label) continue;
          let score = label === normalized ? 100 : label.includes(normalized) ? 60 : 0;
          for (const word of words) if (label.includes(word)) score += 8;
          if (mode === "fill" && element.matches("textarea, [contenteditable='true'], [role='textbox'], input[type='text'], input[type='search'], input[type='email']")) score += 10;
          if (score > bestScore) { best = element; bestScore = score; }
        }
        if (!best || bestScore < (words.length > 1 ? 16 : 8)) {
          throw new Error(`Onsite48 Bridge could not find a visible ${mode === "fill" ? "text field" : "control"} matching '${raw}'. Inspect the page and retry.`);
        }
        return best;
      };
      const verify = (before, after, requested = {}) => {
        const rule = requested && typeof requested === "object" ? requested : {};
        const checks = [];
        const add = (name, passed, detail) => checks.push({ name, passed: Boolean(passed), detail: String(detail || "").slice(0, 240) });
        if (rule.url_matches) add("url_matches", after.url === rule.url_matches, after.url);
        if (rule.title_matches) add("title_matches", after.title.includes(String(rule.title_matches)), after.title);
        if (rule.url_changed) add("url_changed", after.url !== before.url, `${before.url} -> ${after.url}`);
        if (rule.page_digest_changed) add("page_digest_changed", after.page_digest !== before.page_digest, "Compared browser page fingerprints.");
        if (rule.scroll_position_changed) add("scroll_position_changed", after.scroll_y !== before.scroll_y, `${before.scroll_y} -> ${after.scroll_y}`);
        if (rule.element_present) add("element_present", Boolean(document.querySelector(rule.element_present)), String(rule.element_present));
        if (!checks.length) add("observation_available", true, "Before and after browser observations were captured.");
        return { status: checks.every((check) => check.passed) ? "verified" : "inconclusive", checks, observed_at: new Date().toISOString() };
      };
      const ops = {
        readActivePage: () => observe(),
        inspectActivePage: () => ({ ...observe(), text: undefined, elements: collectElements() }),
        observeSurface: (surface, maxItems) => {
          const page = observe();
          const entries = collectSurfaceEntries(surface, maxItems);
          return {
            surface: String(surface || "page").toLowerCase(),
            status: entries.length ? "available" : "not_rendered_or_empty",
            entries,
            entry_count: entries.length,
            page: { ...page, text: page.text.slice(0, 12000), elements: undefined },
            guidance: entries.length ? "Entries reflect currently rendered content only." : "The requested surface is not visibly rendered. Do not infer its contents; the user may need to open the panel manually.",
            observed_at: new Date().toISOString(),
          };
        },
        observeActionPage: (selector) => ({ page: observe(), target: observeTarget(selector) }),
        scrollActivePage: (direction, amount) => {
          const distance = Math.max(100, Math.min(Number(amount) || Math.floor(window.innerHeight * 0.8), 10000));
          window.scrollBy({ top: direction === "up" ? -distance : distance, left: 0, behavior: "instant" });
          return observe();
        },
        clickActivePage: (selector) => {
          const element = resolveActionTarget(selector, "click");
          actionSafety(element, "click");
          if (!element.matches("a, button, [role='button'], input[type='button'], [role='link']")) throw new Error("Onsite48 Bridge may click only a visible link or ordinary button after approval.");
          element.click();
          return { url: location.href, clicked: true, selector: selectorFor(element) || selector, target_label: visibleText(element.innerText || element.getAttribute("aria-label") || "", 160), post_action: observe() };
        },
        doubleClickActivePage: (selector) => {
          const element = resolveActionTarget(selector, "double_click");
          actionSafety(element, "double_click");
          if (!element.matches("a, button, [role='button'], input[type='button'], [role='link']")) throw new Error("Onsite48 Bridge may double-click only a visible link or ordinary button after approval.");
          const event = new MouseEvent("dblclick", { bubbles: true, cancelable: true, view: window });
          element.dispatchEvent(event);
          return { url: location.href, double_clicked: true, selector: selectorFor(element) || selector, target_label: visibleText(element.innerText || element.getAttribute("aria-label") || "", 160), post_action: observe() };
        },
        rightClickActivePage: (selector) => {
          const element = resolveActionTarget(selector, "right_click");
          actionSafety(element, "right_click");
          if (!element.matches("a, button, [role='button'], input[type='button'], [role='link']")) throw new Error("Onsite48 Bridge may right-click only a visible link or ordinary button after approval.");
          const event = new MouseEvent("contextmenu", { bubbles: true, cancelable: true, view: window });
          element.dispatchEvent(event);
          return { url: location.href, right_clicked: true, selector: selectorFor(element) || selector, target_label: visibleText(element.innerText || element.getAttribute("aria-label") || "", 160), post_action: observe() };
        },
        fillActivePage: (selector, value) => {
          const element = resolveActionTarget(selector, "fill");
          actionSafety(element, "fill");
          const text = String(value || "");
          if (text.length > 4000) throw new Error("Onsite48 Bridge limits a single approved text entry to 4,000 characters.");
          if (element.isContentEditable) element.textContent = text;
          else {
            const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")?.set ||
              Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, "value")?.set;
            if (setter) setter.call(element, text); else element.value = text;
          }
          element.dispatchEvent(new Event("input", { bubbles: true }));
          element.dispatchEvent(new Event("change", { bubbles: true }));
          return { url: location.href, typed: true, selector: selectorFor(element) || selector, text_length: text.length, post_action: observe() };
        },
        challengeState: () => {
          const text = clean(document.body?.innerText || "", 12000).toLowerCase();
          const selector = "iframe[src*='captcha'], iframe[src*='turnstile'], iframe[src*='hcaptcha'], iframe[src*='recaptcha'], [data-sitekey], .g-recaptcha, .h-captcha, #challenge-form";
          const present = Boolean(document.querySelector(selector));
          const marker = /verify you are human|checking your browser|security check|unusual traffic|captcha|turnstile|hcaptcha|recaptcha/.test(text);
          return present || marker ? { detected: true, reason: "A CAPTCHA or anti-bot challenge requires manual completion in the browser." } : { detected: false };
        },
        verifyAction: (before, after, requested) => verify(before, after, requested),
      };
      if (!ops[op]) throw new Error(`Unsupported page operation: ${op}`);
      return ops[op](...args);
    },
    args: [operation, args],
  });
  return result;
}

function visibleText(value, limit = 160) {
  return (value || "").replace(/\s+/g, " ").trim().slice(0, limit);
}

function stableDigest(value) {
  let hash = 2166136261;
  for (let index = 0; index < value.length; index += 1) {
    hash ^= value.charCodeAt(index);
    hash = Math.imul(hash, 16777619);
  }
  return `fnv1a-${(hash >>> 0).toString(16)}`;
}

function actionTargetSummary(element, selector) {
  return {
    selector: selector || null,
    tag: element?.tagName || null,
    role: element?.getAttribute?.("role") || null,
    type: element?.type || "",
    label: visibleText(element?.innerText || element?.getAttribute?.("aria-label") || element?.placeholder || "", 120),
  };
}

function observeActivePage() {
  const text = (document.body?.innerText || "").replace(/\s+/g, " ").trim().slice(0, 12000);
  const root = document.documentElement;
  return {
    observation_id: `obs_${stableDigest(`${location.href}|${document.title}|${text}|${Date.now()}`).slice(0, 24)}`,
    url: location.href,
    title: document.title.slice(0, 240),
    scroll_y: Math.round(window.scrollY),
    scroll_height: Math.max(root?.scrollHeight || 0, document.body?.scrollHeight || 0),
    viewport_height: Math.round(window.innerHeight),
    page_digest: stableDigest(`${location.href}|${document.title}|${text}`),
    visible_text_length: text.length,
    observed_at: new Date().toISOString(),
  };
}

function observeActionTarget(selector) {
  if (!selector) return null;
  const element = document.querySelector(selector);
  return element ? actionTargetSummary(element, selector) : { selector, missing: true };
}

function observeActionPage(selector) {
  const page = observeActivePage();
  return { observation_id: page.observation_id, page, target: observeActionTarget(selector) };
}

function selectorFor(element) {
  if (element.id) return `#${CSS.escape(element.id)}`;
  const testId = element.getAttribute("data-testid");
  if (testId) return `[data-testid="${CSS.escape(testId)}"]`;
  const name = element.getAttribute("name");
  if (name && /^[a-zA-Z0-9_-]{1,80}$/.test(name)) return `${element.tagName.toLowerCase()}[name="${CSS.escape(name)}"]`;
  const aria = element.getAttribute("aria-label");
  if (aria && aria.length < 90) return `${element.tagName.toLowerCase()}[aria-label="${CSS.escape(aria)}"]`;
  return null;
}

function firstVisibleText(selectors) {
  for (const selector of selectors) {
    const element = document.querySelector(selector);
    const rect = element?.getBoundingClientRect?.();
    const value = visibleText(element?.innerText || element?.textContent || element?.getAttribute?.("aria-label") || "", 240);
    if (value && rect && rect.width > 0 && rect.height > 0) return value;
  }
  return "";
}

function selectedTabMediaObservation() {
  const media = document.querySelector("audio, video");
  const hostname = location.hostname.toLowerCase();
  const title = firstVisibleText([
    "ytmusic-player-bar .title", "ytmusic-player-bar #song-title", "[data-testid='now-playing-title']",
    "[data-testid='context-item-info-title']", "[aria-label*='Now playing' i]",
  ]);
  const artists = firstVisibleText([
    "ytmusic-player-bar .byline", "ytmusic-player-bar #byline", "[data-testid='now-playing-artist']",
    "[data-testid='context-item-info-subtitles']",
  ]);
  const album = firstVisibleText(["ytmusic-player-bar .middle-controls + *", "[data-testid='now-playing-album']"]);
  const controlLabels = [...document.querySelectorAll("button[aria-label], [role='button'][aria-label]")]
    .map((element) => String(element.getAttribute("aria-label") || ""))
    .filter((label) => /play|pause|shuffle|repeat/i.test(label))
    .slice(0, 24);
  const pauseControl = controlLabels.find((label) => /pause/i.test(label));
  const playControl = controlLabels.find((label) => /play/i.test(label));
  const hasEvidence = Boolean(title || artists || media || pauseControl || playControl || hostname === "music.youtube.com");
  if (!hasEvidence) return null;
  return {
    source: hostname,
    track_title: title || null,
    artists: artists || null,
    album: album || null,
    playing: media ? !media.paused : (pauseControl ? true : playControl ? false : null),
    current_time_seconds: media && Number.isFinite(media.currentTime) ? Math.round(media.currentTime) : null,
    duration_seconds: media && Number.isFinite(media.duration) ? Math.round(media.duration) : null,
    controls: controlLabels,
  };
}

function readActivePage() {
  const text = (document.body?.innerText || "").replace(/\n{3,}/g, "\n\n").trim().slice(0, MAX_VISIBLE_TEXT);
  return {
    url: location.href,
    title: document.title,
    text,
    media: selectedTabMediaObservation(),
    scrollY: Math.round(window.scrollY),
    scrollHeight: Math.max(document.documentElement.scrollHeight, document.body?.scrollHeight || 0),
    viewportHeight: Math.round(window.innerHeight),
  };
}

// Operation marker: executeInTab uses this function name to select the injected observer.
function observeSurface() {}

async function executeSurfaceObservation(tabId, surface, maxItems = 20, maxScrolls = 2) {
  const limit = Math.max(1, Math.min(Number(maxItems) || 20, 50));
  const scrollLimit = Math.max(0, Math.min(Number(maxScrolls) || 0, 5));
  const observations = [];
  const entries = [];
  const seen = new Set();
  let current = await executeInTab(tabId, observeSurface, [surface, limit]);
  observations.push({ scroll_y: current?.page?.scroll_y ?? current?.scroll_y ?? null, entry_count: current?.entry_count ?? 0 });
  const merge = (value) => {
    for (const item of (value?.entries || [])) {
      const key = item?.fingerprint || `${item?.text || ""}|${item?.index || 0}`;
      if (!seen.has(key) && entries.length < limit) {
        seen.add(key);
        entries.push(item);
      }
    }
  };
  merge(current);
  for (let step = 0; step < scrollLimit && entries.length < limit; step += 1) {
    const beforeY = Number(current?.page?.scroll_y ?? current?.scroll_y ?? 0);
    const beforeHeight = Number(current?.page?.scroll_height ?? current?.scroll_height ?? 0);
    const afterScroll = await executeInTab(tabId, scrollActivePage, ["down", Math.floor((current?.page?.viewport_height || current?.viewport_height || 800) * 0.8)]);
    const afterY = Number(afterScroll?.scroll_y || 0);
    if (afterY <= beforeY || (beforeHeight > 0 && afterY >= beforeHeight - Number(afterScroll?.viewport_height || 800))) break;
    await new Promise((resolve) => setTimeout(resolve, 120));
    current = await executeInTab(tabId, observeSurface, [surface, limit]);
    observations.push({ scroll_y: current?.page?.scroll_y ?? current?.scroll_y ?? null, entry_count: current?.entry_count ?? 0 });
    merge(current);
  }
  return {
    ...current,
    entries,
    entry_count: entries.length,
    observation_steps: observations,
    iterative: true,
  };
}

function inspectActivePage() {
  const elements = [...document.querySelectorAll("a, button, input, textarea, select, [contenteditable='true']")]
    .filter((element) => {
      const rect = element.getBoundingClientRect();
      return rect.width > 0 && rect.height > 0;
    })
    .slice(0, 100)
    .map((element, index) => ({
      index,
      tag: element.tagName,
      text: visibleText(element.innerText || element.getAttribute("aria-label") || element.placeholder || ""),
      type: element.type || "",
      selector: selectorFor(element),
      disabled: Boolean(element.disabled),
    }));
  return { url: location.href, title: document.title, elements };
}

function verifyAction(before, after, requested = {}) {
  const rule = requested && typeof requested === "object" ? requested : {};
  const checks = [];
  const add = (name, passed, detail) => checks.push({ name, passed: Boolean(passed), detail: String(detail || "").slice(0, 240) });
  if (rule.url_matches) add("url_matches", after.url === rule.url_matches, after.url);
  if (rule.title_matches) add("title_matches", after.title.includes(String(rule.title_matches)), after.title);
  if (rule.url_changed) add("url_changed", after.url !== before.url, `${before.url} -> ${after.url}`);
  if (rule.page_digest_changed) add("page_digest_changed", after.page_digest !== before.page_digest, "Compared bounded page digests.");
  if (rule.scroll_position_changed) add("scroll_position_changed", after.scroll_y !== before.scroll_y, `${before.scroll_y} -> ${after.scroll_y}`);
  if (rule.element_present) {
    const found = typeof rule.element_present === "string" && Boolean(document.querySelector(rule.element_present));
    add("element_present", found, String(rule.element_present || ""));
  }
  if (!checks.length) {
    add("observation_available", true, "Before and after observations were captured.");
  }
  return { status: checks.every((check) => check.passed) ? "verified" : "inconclusive", checks, observed_at: new Date().toISOString() };
}

function scrollActivePage(direction, amount) {
  const distance = Math.max(100, Math.min(Number(amount) || Math.floor(window.innerHeight * 0.8), 10000));
  window.scrollBy({ top: direction === "up" ? -distance : distance, left: 0, behavior: "instant" });
  return {
    url: location.href,
    scrollY: Math.round(window.scrollY),
    scrollHeight: Math.max(document.documentElement.scrollHeight, document.body?.scrollHeight || 0),
    viewportHeight: Math.round(window.innerHeight),
  };
}

function challengeState() {
  const text = (document.body?.innerText || "").slice(0, 12000).toLowerCase();
  const selector = "iframe[src*='captcha'], iframe[src*='turnstile'], iframe[src*='hcaptcha'], iframe[src*='recaptcha'], [data-sitekey], .g-recaptcha, .h-captcha, #challenge-form";
  const present = Boolean(document.querySelector(selector));
  const marker = /verify you are human|checking your browser|security check|unusual traffic|captcha|turnstile|hcaptcha|recaptcha/.test(text);
  return present || marker ? { detected: true, reason: "A CAPTCHA or anti-bot challenge requires manual completion in the browser." } : { detected: false };
}

function sensitiveActionReason(element, action) {
  // The paired Onsite48 policy and the user-approved command are authoritative.
  // The extension no longer imposes a second, narrower action blacklist.
  return null;
}

function legacySensitiveActionReasonDisabled(element, action) {
  const form = element.closest?.("form");
  const signal = [
    element.type,
    element.name,
    element.id,
    element.autocomplete,
    element.getAttribute("aria-label"),
    element.placeholder,
    element.innerText,
    element.value,
    element.getAttribute("href"),
    form?.getAttribute("action"),
  ].join(" ").toLowerCase();
  if (/password|passcode|otp|one.?time|verification|captcha|turnstile|hcaptcha|recaptcha|cvv|card.?number|payment|security.?code/.test(signal)) return "password, verification, CAPTCHA, payment, or security control";
  if (element.matches("input[type='file']") || /upload|attach file|choose file/.test(signal)) return "file upload control";
  if (element.matches("a[download]") || /download|export file|save file/.test(signal)) return "download control";
  if (action === "click" && (element.type === "submit" || element.form || /submit|checkout|buy|purchase|place order|pay now|send|post|publish|delete|remove|discard|confirm/.test(signal))) return "submission, purchase, send, deletion, or high-impact control";
  return null;
}

function assertSafeActionTarget(selector, action) {
  if (typeof selector !== "string" || selector.length < 1 || selector.length > 500) {
    throw new Error("Onsite48 Bridge requires a short inspected selector for this action.");
  }
  const element = document.querySelector(selector);
  if (!element) throw new Error("The approved target is no longer present on this page.");
  const rect = element.getBoundingClientRect();
  if (rect.width <= 0 || rect.height <= 0) throw new Error("The approved target is not currently visible.");
  return element;
}

function clickActivePage(selector) {
  const element = assertSafeActionTarget(selector, "click");
  element.click();
  return { url: location.href, clicked: true, selector };
}

function doubleClickActivePage(selector) {
  const element = assertSafeActionTarget(selector, "double_click");
  const event = new MouseEvent("dblclick", { bubbles: true, cancelable: true, view: window });
  element.dispatchEvent(event);
  return { url: location.href, double_clicked: true, selector };
}

function rightClickActivePage(selector) {
  const element = assertSafeActionTarget(selector, "right_click");
  const event = new MouseEvent("contextmenu", { bubbles: true, cancelable: true, view: window });
  element.dispatchEvent(event);
  return { url: location.href, right_clicked: true, selector };
}

async function controlMedia(action) {
  const candidates = [...document.querySelectorAll("video, audio")].filter((item) => {
    if (!(item instanceof HTMLMediaElement)) return false;
    const rect = item.getBoundingClientRect?.();
    const style = getComputedStyle(item);
    return (item.readyState > 0 || !item.paused) && (!rect || (rect.width > 0 && rect.height > 0) || item.tagName === "AUDIO") && style.display !== "none" && style.visibility !== "hidden";
  });
  const media = candidates.sort((left, right) => {
    const leftRect = left.getBoundingClientRect?.();
    const rightRect = right.getBoundingClientRect?.();
    return ((rightRect?.width || 0) * (rightRect?.height || 0)) - ((leftRect?.width || 0) * (leftRect?.height || 0));
  })[0] || document.querySelector("video, audio");
  if (!(media instanceof HTMLMediaElement)) throw new Error("No HTML5 media element is present on this page.");
  const before = { paused: Boolean(media.paused), current_time: Number(media.currentTime || 0), duration: Number(media.duration || 0) };
  const requested = String(action || "pause");
  if (requested === "pause") {
    // YouTube can briefly retain an ad/preview media node alongside the main
    // player. Pausing every media node prevents the audible player from
    // continuing when the first DOM match is not the active one.
    for (const item of document.querySelectorAll("video, audio")) {
      if (item instanceof HTMLMediaElement) item.pause();
    }
  }
  else if (requested === "play") await media.play();
  else if (requested === "toggle") {
    if (media.paused) await media.play();
    else for (const item of document.querySelectorAll("video, audio")) if (item instanceof HTMLMediaElement) item.pause();
  }
  else throw new Error(`Unsupported media action: ${requested}`);
  await new Promise((resolve) => setTimeout(resolve, 80));
  const after = { paused: Boolean(media.paused), current_time: Number(media.currentTime || 0), duration: Number(media.duration || 0) };
  const allPaused = [...document.querySelectorAll("video, audio")].every((item) => !(item instanceof HTMLMediaElement) || item.paused);
  return { before, after, action: requested, verified: requested === "pause" ? allPaused : (requested === "toggle" && !before.paused ? allPaused : !after.paused) };
}

function fillActivePage(selector, value) {
  const element = assertSafeActionTarget(selector, "fill");
  const text = String(value || "");
  if (element.isContentEditable) element.textContent = text;
  else if ("value" in element) element.value = text;
  else element.textContent = text;
  element.dispatchEvent(new Event("input", { bubbles: true }));
  element.dispatchEvent(new Event("change", { bubbles: true }));
  return { url: location.href, typed: true, selector, text_length: text.length };
}

function triggerDownloadTarget(selector) {
  const element = assertSafeActionTarget(selector, "download");
  const href = element.href || element.closest("a")?.href || null;
  element.click();
  return { clicked: true, selector, href, url: location.href };
}

function uploadFileToPage(selector, file) {
  const target = selector ? document.querySelector(selector) : document.querySelector("input[type='file']");
  if (!target || target.type !== "file") throw new Error("A visible file input is required for upload.");
  const rect = target.getBoundingClientRect();
  if (rect.width <= 0 || rect.height <= 0) throw new Error("The file input is not visible.");
  const raw = String(file?.data_url || "");
  const comma = raw.indexOf(",");
  if (comma < 0) throw new Error("The staged upload is invalid.");
  const bytes = atob(raw.slice(comma + 1));
  const buffer = new Uint8Array(bytes.length);
  for (let index = 0; index < bytes.length; index += 1) buffer[index] = bytes.charCodeAt(index);
  const blob = new Blob([buffer], { type: file.type || "application/octet-stream" });
  const staged = new File([blob], file.name || "upload", { type: blob.type });
  const transfer = new DataTransfer();
  transfer.items.add(staged);
  target.files = transfer.files;
  target.dispatchEvent(new Event("change", { bubbles: true }));
  return { uploaded: true, selector: selector || "input[type='file']", file_name: staged.name, size: staged.size };
}

async function waitForTabLoad(tabId, timeoutMs = 15000) {
  return new Promise((resolve) => {
    let settled = false;
    const listener = (updatedId, changeInfo, tab) => { if (updatedId === tabId && changeInfo.status === "complete") finish(tab); };
    const finish = (tab) => {
      if (!settled) {
        settled = true;
        chrome.tabs.onUpdated.removeListener(listener);
        resolve(tab);
      }
    };
    chrome.tabs.onUpdated.addListener(listener);
    setTimeout(async () => finish(await chrome.tabs.get(tabId).catch(() => null)), timeoutMs);
  });
}

async function sendOverlay(tabId, state) {
  if (!tabId) return;
  await chrome.tabs.sendMessage(tabId, { type: "operatorState", state }).catch(() => undefined);
}



async function stagedUpload() {
  const stored = await chrome.storage.local.get(STAGED_UPLOAD_KEY);
  return stored[STAGED_UPLOAD_KEY] || null;
}

async function stageUpload(file) {
  if (!file || typeof file.data_url !== "string" || file.data_url.length > 9_000_000) throw new Error("Choose a file smaller than 6 MB in the extension before requesting an upload.");
  const staged = { name: String(file.name || "upload").slice(0, 240), type: String(file.type || "application/octet-stream").slice(0, 120), size: Number(file.size || 0), data_url: file.data_url, staged_at: new Date().toISOString() };
  await chrome.storage.local.set({ [STAGED_UPLOAD_KEY]: staged });
  return { staged: true, name: staged.name, size: staged.size, staged_at: staged.staged_at };
}

async function clearStagedUpload() {
  await chrome.storage.local.remove(STAGED_UPLOAD_KEY);
  return { cleared: true };
}

async function openTaskTabs(command) {
  // Prefer workspace-based open; this is a legacy multi-URL helper.
  const sessionId = String(command.payload?.session_id || command.session_id || activeBrowserSessionId || "");
  if (!sessionId) throw new Error("open_task_tabs requires a session_id.");
  const urls = [...new Set((Array.isArray(command.payload?.urls) ? command.payload.urls : []).map((url) => String(url || "").trim()))];
  if (!urls.length || urls.length > MAX_TASK_TABS || urls.some((url) => !publicUrl(url))) {
    throw new Error(`Onsite48 Bridge can open only 1 to ${MAX_TASK_TABS} complete public URLs for one approved task.`);
  }
  const tabs = [];
  for (const url of urls) {
    const result = await openTaskTab(sessionId, { url, makePrimary: tabs.length === 0 });
    if (result?.tab) tabs.push(result.tab);
  }
  return { opened: true, session_id: sessionId, tabs: tabs.map(tabSummary).filter(Boolean) };
}

async function closeTaskTabs(command) {
  const sessionId = String(command.payload?.session_id || command.session_id || activeBrowserSessionId || "");
  if (!sessionId) return { closed: false, reason: "No session_id provided." };
  await destroyTaskWorkspace(sessionId, { closeTabs: true });
  return { closed: true, session_id: sessionId };
}

function commandTitle(command) {
  return String(command.command_type || "browser action").replace(/_active_tab/g, "").replaceAll("_", " ");
}


/**
 * Phase 4 screenshot scope fix.
 * Activate the target tab inside its own window, verify it is the active tab
 * of that window, then capture with that windowId. Never capture the user's
 * focused window by accident.
 */
async function captureTabScoped(tab, selected, opts) {
  try {
    // Activate the target tab in its window.
    await chrome.tabs.update(tab.id, { active: true });
    // Focus the window that owns the task tab (workspace window).
    if (tab.windowId != null) {
      try { await chrome.windows.update(tab.windowId, { focused: true }); } catch (_) { /* headless / permission */ }
    }
    // Re-read the tab and verify ownership.
    const verified = await chrome.tabs.get(tab.id);
    if (!verified || verified.active !== true || verified.windowId !== tab.windowId) {
      return {
        available: false,
        target_tab: selected,
        reason: "capture_unavailable",
        detail: "target tab is not active in its workspace window",
        capture_mode: "unavailable",
      };
    }
    const dataUrl = await chrome.tabs.captureVisibleTab(verified.windowId, opts);
    if (dataUrl && dataUrl.length > MAX_SCREENSHOT_DATA_URL) {
      throw new Error("The visible screenshot is too large to return safely. Use page inspection or a smaller browser window.");
    }
    return {
      available: true,
      target_tab: selected,
      url: verified.url,
      title: verified.title || "",
      screenshot_data_url: dataUrl,
      screenshot_bytes_estimate: Math.floor((dataUrl || "").length * 0.75),
      capture_mode: "workspace_window",
      window_id: verified.windowId,
      tab_id: verified.id,
    };
  } catch (err) {
    return {
      available: false,
      target_tab: selected,
      reason: "capture_unavailable",
      detail: String(err && err.message ? err.message : err),
      capture_mode: "unavailable",
    };
  }
}

function commandSessionId(command) {
  return String(
    command?.payload?.session_id ||
    command?.session_id ||
    activeBrowserSessionId ||
    ""
  ).trim();
}

const HIGH_IMPACT_COMMAND_CAPABILITIES = Object.freeze({
  download: "download",
  upload: "upload",
  control_media: "media",
  trigger_download: "download"
});

function requireCommandCapability(command) {
  const required = HIGH_IMPACT_COMMAND_CAPABILITIES[command?.command_type];
  if (!required) return;

  const capabilities = command?.payload?.session_capabilities ||
                       command?.payload?.allowed_capabilities ||
                       command?.session_capabilities ||
                       command?.allowed_capabilities ||
                       [];

  const allowed = Array.isArray(capabilities) &&
    (capabilities.includes(required) || capabilities.includes("all"));

  if (!allowed) {
    throw Object.assign(
      new Error(`Missing required browser capability for ${command.command_type}: ${required}`),
      { code: "capability_denied" }
    );
  }
}

function ensureActive(command) {
  const sessionId = commandSessionId(command);
  const active = activeCommands.get(sessionId);
  if (active?.cancelled) throw new Error("The operator was stopped by the user before the action completed.");
  if (!active || active.id !== command.id) throw new Error("The browser operator command is no longer active.");
  if (active.leaseExpiresAt && Date.parse(active.leaseExpiresAt) <= Date.now()) {
    throw new Error("The browser operator lease expired before the action completed. Refresh the page state and request a new approved action.");
  }
}

async function invalidateActiveCommandLease(reason) {
  const sessionId = activeBrowserSessionId;
  const command = sessionId ? activeCommands.get(sessionId) : null;
  if (!command?.id) return;

  command.cancelled = true;
  if (websocketReady()) {
    try {
      await bridgeRequest("command_cancel", command.id, { reason, session_id: sessionId });
      return;
    } catch {
      // The authenticated REST endpoint remains the durable cancellation fallback.
    }
  }
  await api(`/api/bridge/extension/commands/${command.id}/cancel`, {
    method: "POST",
    body: JSON.stringify({ reason, session_id: sessionId }),
  }).catch(() => undefined);
}

async function readActiveTabWithEvidence(tabId, selected, command) {
  let best = null;
  let previousDigest = null;
  let stableCount = 0;

  for (let attempt = 0; attempt < 7; attempt += 1) {
    ensureActive(command);
    const observation = await executeInTab(tabId, readActivePage);
    best = observation;
    if (observation?.page_digest && observation.page_digest === previousDigest) stableCount += 1;
    else stableCount = 0;
    previousDigest = observation?.page_digest || null;

    const usefulText = Number(observation?.text_length || 0) >= 80;
    const usefulStructure = Number(observation?.element_count || 0) >= 3;
    if ((usefulText || usefulStructure) && stableCount >= 1) break;
    await new Promise((resolve) => setTimeout(resolve, 250 + attempt * 125));
  }

  best = best || { url: "", title: "", text: "", text_length: 0 };
  return {
    available: true,
    target_tab: selected,
    ...best,
    evidence_scope: "visible_dom",
    read_strategy: "dom",
  };
}

async function runCommand(command) {
  requireCommandCapability(command);
  // ── Manus-style task workspace lifecycle ──────────────────────────────
  if (command.command_type === "create_task_workspace") {
    ensureActive(command);
    const sessionId = String(command.payload?.session_id || command.session_id || "").trim();
    if (!sessionId) throw new Error("session_id is required to create a task workspace.");
    const title = String(command.payload?.title || command.payload?.task_name || "Onsite48 — Task").slice(0, 64);
    const startUrl = command.payload?.url || command.payload?.start_url || "about:blank";
    if (startUrl !== "about:blank" && !publicUrl(startUrl)) {
      throw new Error("Workspace start URL must be a public http(s) URL or about:blank.");
    }
    const ws = await createTaskWorkspace({
      sessionId,
      taskId: command.payload?.task_id || null,
      title,
      startUrl,
      windowId: command.payload?.window_id || null,
    });
    activeBrowserSessionId = sessionId;
    return {
      workspace_created: true,
      session_id: sessionId,
      window_id: ws.windowId,
      tab_group_id: ws.tabGroupId,
      tab_ids: ws.tabIds,
      primary_tab_id: ws.primaryTabId,
      title: ws.title,
      status: ws.status,
    };
  }
  if (command.command_type === "claim_task_workspace") {
    ensureActive(command);
    const sessionId = String(command.payload?.session_id || "").trim();
    const ws = await getWorkspace(sessionId);
    if (!ws) throw new Error("No workspace to claim for this session.");
    activeBrowserSessionId = sessionId;
    return {
      claimed: true,
      session_id: sessionId,
      window_id: ws.windowId,
      tab_group_id: ws.tabGroupId,
      tab_ids: ws.tabIds,
      primary_tab_id: ws.primaryTabId,
      status: ws.status,
    };
  }
  if (command.command_type === "open_task_tab") {
    ensureActive(command);
    const sessionId = String(command.payload?.session_id || activeBrowserSessionId || "").trim();
    if (!sessionId) throw new Error("session_id required");
    const url = command.payload?.url || "about:blank";
    if (url !== "about:blank" && !publicUrl(url)) throw new Error("Public http(s) URL required.");
    const { tab, workspace } = await openTaskTab(sessionId, { url, makePrimary: Boolean(command.payload?.make_primary) });
    return { tab_id: tab.id, url: tab.url || url, workspace_tab_ids: workspace.tabIds, primary_tab_id: workspace.primaryTabId };
  }
  if (command.command_type === "close_task_workspace" || command.command_type === "stop") {
    ensureActive(command);
    const sessionId = String(command.payload?.session_id || activeBrowserSessionId || "").trim();
    if (sessionId) {
      await destroyTaskWorkspace(sessionId, { closeTabs: command.payload?.close_tabs !== false });
      if (activeBrowserSessionId === sessionId) activeBrowserSessionId = null;
    }
    return { stopped: true, session_id: sessionId || null };
  }
  if (command.command_type === "list_task_tabs") {
    const sessionId = String(command.payload?.session_id || activeBrowserSessionId || "").trim();
    const ws = sessionId ? await getWorkspace(sessionId) : null;
    if (!ws) return { tabs: [], session_id: sessionId || null };
    const tabs = [];
    for (const id of ws.tabIds || []) {
      try {
        const t = await chrome.tabs.get(id);
        tabs.push(tabSummary(t));
      } catch { /* closed */ }
    }
    return { tabs, session_id: sessionId, tab_group_id: ws.tabGroupId, primary_tab_id: ws.primaryTabId };
  }
  if (command.command_type === "takeover") {
    const sessionId = String(command.payload?.session_id || activeBrowserSessionId || "").trim();
    if (sessionId) await markWorkspaceStatus(sessionId, "taken_over");
    await setOperatorState({ status: "taken_over", lastError: null });
    return { takeover: true, session_id: sessionId || null };
  }
  if (command.command_type === "resume") {
    const sessionId = String(command.payload?.session_id || activeBrowserSessionId || "").trim();
    if (sessionId) await markWorkspaceStatus(sessionId, "active");
    await setOperatorState({ status: "active", lastError: null });
    // Force fresh observation on resume — do not continue from stale world state.
    return { resumed: true, session_id: sessionId || null, requires_reobserve: true };
  }

  if (command.command_type === "open_task_tabs") {
    ensureActive(command);
    return openTaskTabs(command);
  }
  if (command.command_type === "close_task_tabs") {
    ensureActive(command);
    return closeTaskTabs(command);
  }

  const { tab, selected } = await selectedTabForCommand(command);
  await ensureSitePermission(tab, false);
  await ensureOperatorOverlay(tab.id);
  verifySessionOrigin(tab, command);
  verifyTrustedHost(tab, command);
  const sessionId = commandSessionId(command);
  const active = activeCommands.get(sessionId);
  if (active) active.tabId = tab.id;
  const tabChallenges = await detectChallenges(tab.id);
  const challenge = await executeInTab(tab.id, challengeState);
  const hasChallenge = (challenge?.detected) || tabChallenges.some(c => c.type === "captcha" || c.type === "mfa");
  if (hasChallenge) {
    const challengeInfo = tabChallenges.find(c => c.type === "captcha" || c.type === "mfa") || challenge;
    return {
      url: tab.url,
      title: tab.title || "Human verification required",
      user_intervention: { reason: challengeInfo.reason || "A CAPTCHA or MFA challenge requires manual completion in the browser.", handoff_options: ["complete_challenge", "take_over", "cancel"], handoff_surface: "synth_bridge" },
      handoff_surface: "synth_bridge",
      challenge_detected: true,
    };
  }
  await sendOverlay(tab.id, {
    phase: "running",
    title: `Synth is ${commandTitle(command)} on ${new URL(tab.url).hostname}`,
    detail: "This approved action is running locally. Take over or stop it at any time.",
  });
  ensureActive(command);

  try {
    switch (command.command_type) {
      case "read_active_tab": { await emitOperationEvent({ type: OPERATION_EVENTS.READ_START, tabId: tab.id, url: tab.url, commandId: command.id }); const result = await readActiveTabWithEvidence(tab.id, selected, command); await emitOperationEvent({ type: OPERATION_EVENTS.READ_END, tabId: tab.id, url: tab.url, commandId: command.id, status: "completed" }); return result; }
      case "inspect_active_tab": { await emitOperationEvent({ type: OPERATION_EVENTS.READ_START, tabId: tab.id, url: tab.url, commandId: command.id }); const result = { available: true, target_tab: selected, ...(await executeInTab(tab.id, inspectActivePage)) }; await emitOperationEvent({ type: OPERATION_EVENTS.READ_END, tabId: tab.id, url: tab.url, commandId: command.id, status: "completed" }); return result; }
      case "scroll_active_tab": { await emitOperationEvent({ type: OPERATION_EVENTS.SCROLL, tabId: tab.id, url: tab.url, commandId: command.id }); const result = { available: true, target_tab: selected, ...(await executeInTab(tab.id, scrollActivePage, [command.payload?.direction, command.payload?.amount])) }; await emitOperationEvent({ type: OPERATION_EVENTS.SCROLL, tabId: tab.id, url: tab.url, commandId: command.id, status: "completed" }); return result; }
      case "observe_surface": { await emitOperationEvent({ type: OPERATION_EVENTS.OBSERVE, tabId: tab.id, url: tab.url, commandId: command.id }); const result = { available: true, target_tab: selected, ...(await executeSurfaceObservation(tab.id, command.payload?.surface, command.payload?.max_items, command.payload?.max_scrolls)) }; await emitOperationEvent({ type: OPERATION_EVENTS.OBSERVE, tabId: tab.id, url: tab.url, commandId: command.id, status: "completed" }); return result; }
      case "click_active_tab": { await emitOperationEvent({ type: OPERATION_EVENTS.CLICK_START, tabId: tab.id, url: tab.url, selector: command.payload?.selector, commandId: command.id }); const result = { available: true, target_tab: selected, ...(await executeInTab(tab.id, clickActivePage, [command.payload?.selector])) }; await emitOperationEvent({ type: OPERATION_EVENTS.CLICK_END, tabId: tab.id, url: tab.url, selector: command.payload?.selector, commandId: command.id, status: "completed" }); return result; }
      case "control_media": { await emitOperationEvent({ type: OPERATION_EVENTS.CONTROL_MEDIA, tabId: tab.id, url: tab.url, commandId: command.id }); const result = { available: true, target_tab: selected, ...(await executeInTab(tab.id, controlMedia, [command.payload?.action || "pause"])) }; await emitOperationEvent({ type: OPERATION_EVENTS.CONTROL_MEDIA, tabId: tab.id, url: tab.url, commandId: command.id, status: "completed" }); return result; }
      case "fill_active_tab": { await emitOperationEvent({ type: OPERATION_EVENTS.FILL_START, tabId: tab.id, url: tab.url, selector: command.payload?.selector, commandId: command.id }); const result = { available: true, target_tab: selected, ...(await executeInTab(tab.id, fillActivePage, [command.payload?.selector, command.payload?.value])) }; await emitOperationEvent({ type: OPERATION_EVENTS.FILL_END, tabId: tab.id, url: tab.url, selector: command.payload?.selector, commandId: command.id, status: "completed" }); return result; }
      case "capture_active_tab": {
        await emitOperationEvent({ type: OPERATION_EVENTS.CAPTURE, tabId: tab.id, url: tab.url, commandId: command.id });
        // Phase 4: capture only the task workspace window. Activate the target
        // tab, verify tab.active && tab.windowId === workspace window, else
        // return capture_unavailable so we never leak the user's focused tab.
        const captureResult = await captureTabScoped(tab, selected, { format: "jpeg", quality: 70 });
        await emitOperationEvent({ type: OPERATION_EVENTS.CAPTURE, tabId: tab.id, url: tab.url, commandId: command.id, status: captureResult.available ? "completed" : "unavailable" });
        return captureResult;
      }
      case "snapshot_active_tab": {
        await emitOperationEvent({ type: OPERATION_EVENTS.OBSERVE, tabId: tab.id, url: tab.url, commandId: command.id });
        const snapshot = await executeInTab(tab.id, observeActionPage, [null]);
        let dataUrl = null;
        if (command.payload?.include_screenshot !== false) {
          const captureResult = await captureTabScoped(tab, selected, { format: "jpeg", quality: 65 });
          if (!captureResult.available) {
            await emitOperationEvent({ type: OPERATION_EVENTS.OBSERVE, tabId: tab.id, url: tab.url, commandId: command.id, status: "unavailable" });
            return { available: false, target_tab: selected, snapshot: snapshot?.page || snapshot, screenshot_data_url: null, capture_mode: "unavailable", reason: captureResult.reason || "capture_unavailable" };
          }
          dataUrl = captureResult.screenshot_data_url;
        }
        const result = { available: true, target_tab: selected, snapshot: snapshot?.page || snapshot, screenshot_data_url: dataUrl, capture_mode: "visible_tab_plus_dom" };
        await emitOperationEvent({ type: OPERATION_EVENTS.OBSERVE, tabId: tab.id, url: tab.url, commandId: command.id, status: "completed" });
        return result;
      }
      case "download_active_tab": { await emitOperationEvent({ type: OPERATION_EVENTS.NAVIGATE_START, tabId: tab.id, url: tab.url, commandId: command.id }); const url = String(command.payload?.url || "").trim(); if (url) { if (!publicUrl(url)) throw new Error("Onsite48 Bridge only permits public http or https download URLs."); const downloadId = await chrome.downloads.download({ url, saveAs: true }); const result = { downloaded: true, download_id: downloadId, url }; await emitOperationEvent({ type: OPERATION_EVENTS.NAVIGATE_END, tabId: tab.id, url: tab.url, commandId: command.id, status: "completed" }); return { available: true, target_tab: selected, ...result, download_triggered: true }; } const result = await executeInTab(tab.id, triggerDownloadTarget, [command.payload?.selector]); const dlResult = { available: true, target_tab: selected, ...result, download_triggered: true }; await emitOperationEvent({ type: OPERATION_EVENTS.NAVIGATE_END, tabId: tab.id, url: tab.url, commandId: command.id, status: "completed" }); return dlResult; }
      case "upload_active_tab": { await emitOperationEvent({ type: OPERATION_EVENTS.FILL_START, tabId: tab.id, url: tab.url, commandId: command.id }); const file = await stagedUpload(); if (!file || file.name !== String(command.payload?.file_name || "")) throw new Error("Stage the exact requested file in the Onsite48 Bridge popup before uploading."); const result = await executeInTab(tab.id, uploadFileToPage, [command.payload?.selector, file]); await clearStagedUpload(); await emitOperationEvent({ type: OPERATION_EVENTS.FILL_END, tabId: tab.id, url: tab.url, commandId: command.id, status: "completed" }); return { available: true, target_tab: selected, ...result }; }
      case "navigate_active_tab": {
        await emitOperationEvent({ type: OPERATION_EVENTS.NAVIGATE_START, tabId: tab.id, url: tab.url, commandId: command.id });
        const url = command.payload?.url;
        if (!publicUrl(url || "")) throw new Error("Onsite48 Bridge only permits complete public http or https URLs.");
        await recordTabSwitch(tab.id, url, "");
        const updated = await chrome.tabs.update(tab.id, { url });
        const loaded = await waitForTabLoad(updated.id);
        await emitOperationEvent({ type: OPERATION_EVENTS.NAVIGATE_END, tabId: tab.id, url: loaded?.url || url, targetUrl: url, commandId: command.id, status: "completed" });
        return { available: true, target_tab: selected, url: loaded?.url || url, title: loaded?.title || "", navigated: true };
      }
      // ── New task-owned workspace commands ────────────────────────────
      case "observe": {
        await emitOperationEvent({ type: OPERATION_EVENTS.OBSERVE, tabId: tab.id, url: tab.url, commandId: command.id });
        const obsResult = await executeInTab(tab.id, observeActionPage, [command.payload?.selector || null]);
        await emitOperationEvent({ type: OPERATION_EVENTS.OBSERVE, tabId: tab.id, url: tab.url, commandId: command.id, status: "completed" });
        return { available: true, target_tab: selected, ...obsResult };
      }
      case "inspect": {
        await emitOperationEvent({ type: OPERATION_EVENTS.READ_START, tabId: tab.id, url: tab.url, commandId: command.id });
        const inspectResult = await executeInTab(tab.id, inspectActivePage);
        await emitOperationEvent({ type: OPERATION_EVENTS.READ_END, tabId: tab.id, url: tab.url, commandId: command.id, status: "completed" });
        return { available: true, target_tab: selected, ...inspectResult };
      }
      case "locate": {
        await emitOperationEvent({ type: OPERATION_EVENTS.OBSERVE, tabId: tab.id, url: tab.url, commandId: command.id });
        const locateResult = await executeInTab(tab.id, observeActionPage, [command.payload?.selector || null]);
        await emitOperationEvent({ type: OPERATION_EVENTS.OBSERVE, tabId: tab.id, url: tab.url, commandId: command.id, status: "completed" });
        return { available: true, target_tab: selected, ...locateResult };
      }
      case "screenshot": {
        await emitOperationEvent({ type: OPERATION_EVENTS.CAPTURE, tabId: tab.id, url: tab.url, commandId: command.id });
        const captureResult = await captureTabScoped(tab, selected, { format: "jpeg", quality: 70 });
        await emitOperationEvent({ type: OPERATION_EVENTS.CAPTURE, tabId: tab.id, url: tab.url, commandId: command.id, status: captureResult.available ? "completed" : "unavailable" });
        return captureResult;
      }
      case "read_page": {
        await emitOperationEvent({ type: OPERATION_EVENTS.READ_START, tabId: tab.id, url: tab.url, commandId: command.id });
        const readResult = await readActiveTabWithEvidence(tab.id, selected, command);
        await emitOperationEvent({ type: OPERATION_EVENTS.READ_END, tabId: tab.id, url: tab.url, commandId: command.id, status: "completed" });
        return readResult;
      }
      case "navigate": {
        await emitOperationEvent({ type: OPERATION_EVENTS.NAVIGATE_START, tabId: tab.id, url: tab.url, commandId: command.id });
        const navUrl = command.payload?.url;
        if (!publicUrl(navUrl || "")) throw new Error("Onsite48 Bridge only permits complete public http or https URLs.");
        await recordTabSwitch(tab.id, navUrl, "");
        const navUpdated = await chrome.tabs.update(tab.id, { url: navUrl });
        const navLoaded = await waitForTabLoad(navUpdated.id);
        await emitOperationEvent({ type: OPERATION_EVENTS.NAVIGATE_END, tabId: tab.id, url: navLoaded?.url || navUrl, targetUrl: navUrl, commandId: command.id, status: "completed" });
        return { available: true, target_tab: selected, url: navLoaded?.url || navUrl, title: navLoaded?.title || "", navigated: true };
      }
      case "click": {
        await emitOperationEvent({ type: OPERATION_EVENTS.CLICK_START, tabId: tab.id, url: tab.url, selector: command.payload?.selector, commandId: command.id });
        const clickResult = await executeInTab(tab.id, clickActivePage, [command.payload?.selector]);
        await emitOperationEvent({ type: OPERATION_EVENTS.CLICK_END, tabId: tab.id, url: tab.url, selector: command.payload?.selector, commandId: command.id, status: "completed" });
        return { available: true, target_tab: selected, ...clickResult };
      }
      case "type": {
        await emitOperationEvent({ type: OPERATION_EVENTS.FILL_START, tabId: tab.id, url: tab.url, selector: command.payload?.selector, commandId: command.id });
        const typeResult = await executeInTab(tab.id, fillActivePage, [command.payload?.selector, command.payload?.value]);
        await emitOperationEvent({ type: OPERATION_EVENTS.FILL_END, tabId: tab.id, url: tab.url, selector: command.payload?.selector, commandId: command.id, status: "completed" });
        return { available: true, target_tab: selected, ...typeResult };
      }
      case "scroll": {
        await emitOperationEvent({ type: OPERATION_EVENTS.SCROLL, tabId: tab.id, url: tab.url, commandId: command.id });
        const scrollResult = await executeInTab(tab.id, scrollActivePage, [command.payload?.direction, command.payload?.amount]);
        await emitOperationEvent({ type: OPERATION_EVENTS.SCROLL, tabId: tab.id, url: tab.url, commandId: command.id, status: "completed" });
        return { available: true, target_tab: selected, ...scrollResult };
      }
      case "back": {
        await emitOperationEvent({ type: OPERATION_EVENTS.NAVIGATE_START, tabId: tab.id, url: tab.url, commandId: command.id });
        try {
          await chrome.tabs.goBack(tab.id);
        } catch (err) {
          throw new Error(`Cannot go back: ${err.message || err}`);
        }
        const backLoaded = await waitForTabLoad(tab.id);
        await emitOperationEvent({ type: OPERATION_EVENTS.NAVIGATE_END, tabId: tab.id, url: backLoaded?.url || tab.url, commandId: command.id, status: "completed" });
        return { available: true, target_tab: selected, url: backLoaded?.url || tab.url, title: backLoaded?.title || "", navigated: true };
      }
      case "forward": {
        await emitOperationEvent({ type: OPERATION_EVENTS.NAVIGATE_START, tabId: tab.id, url: tab.url, commandId: command.id });
        try {
          await chrome.tabs.goForward(tab.id);
        } catch (err) {
          throw new Error(`Cannot go forward: ${err.message || err}`);
        }
        const fwdLoaded = await waitForTabLoad(tab.id);
        await emitOperationEvent({ type: OPERATION_EVENTS.NAVIGATE_END, tabId: tab.id, url: fwdLoaded?.url || tab.url, commandId: command.id, status: "completed" });
        return { available: true, target_tab: selected, url: fwdLoaded?.url || tab.url, title: fwdLoaded?.title || "", navigated: true };
      }
      case "reload": {
        await emitOperationEvent({ type: OPERATION_EVENTS.NAVIGATE_START, tabId: tab.id, url: tab.url, commandId: command.id });
        await chrome.tabs.reload(tab.id);
        const reloadLoaded = await waitForTabLoad(tab.id);
        await emitOperationEvent({ type: OPERATION_EVENTS.NAVIGATE_END, tabId: tab.id, url: reloadLoaded?.url || tab.url, commandId: command.id, status: "completed" });
        return { available: true, target_tab: selected, url: reloadLoaded?.url || tab.url, title: reloadLoaded?.title || "", navigated: true };
      }
      case "double_click": {
        await emitOperationEvent({ type: OPERATION_EVENTS.CLICK_START, tabId: tab.id, url: tab.url, selector: command.payload?.selector, commandId: command.id });
        const dblResult = await executeInTab(tab.id, doubleClickActivePage, [command.payload?.selector]);
        await emitOperationEvent({ type: OPERATION_EVENTS.CLICK_END, tabId: tab.id, url: tab.url, selector: command.payload?.selector, commandId: command.id, status: "completed" });
        return { available: true, target_tab: selected, ...dblResult, double_click: true };
      }
      case "right_click": {
        await emitOperationEvent({ type: OPERATION_EVENTS.CLICK_START, tabId: tab.id, url: tab.url, selector: command.payload?.selector, commandId: command.id });
        const rightResult = await executeInTab(tab.id, rightClickActivePage, [command.payload?.selector]);
        await emitOperationEvent({ type: OPERATION_EVENTS.CLICK_END, tabId: tab.id, url: tab.url, selector: command.payload?.selector, commandId: command.id, status: "completed" });
        return { available: true, target_tab: selected, ...rightResult, right_click: true };
      }
      case "insert_text": {
        await emitOperationEvent({ type: OPERATION_EVENTS.FILL_START, tabId: tab.id, url: tab.url, selector: command.payload?.selector, commandId: command.id });
        const insertResult = await executeInTab(tab.id, fillActivePage, [command.payload?.selector, command.payload?.value]);
        await emitOperationEvent({ type: OPERATION_EVENTS.FILL_END, tabId: tab.id, url: tab.url, selector: command.payload?.selector, commandId: command.id, status: "completed" });
        return { available: true, target_tab: selected, ...insertResult };
      }
      case "wait": {
        await emitOperationEvent({ type: OPERATION_EVENTS.READ_START, tabId: tab.id, url: tab.url, commandId: command.id });
        await new Promise(resolve => setTimeout(resolve, command.payload?.duration_ms || 1000));
        const waitResult = await executeInTab(tab.id, observeActionPage, [command.payload?.selector || null]);
        await emitOperationEvent({ type: OPERATION_EVENTS.READ_END, tabId: tab.id, url: tab.url, commandId: command.id, status: "completed" });
        return { available: true, target_tab: selected, ...waitResult, waited: true };
      }
      case "wait_for_url":
      case "wait_for_text":
      case "wait_for_selector":
      case "wait_for_navigation":
      case "wait_for_network_idle":
      case "wait_for_dom_stable":
      case "wait_for_download":
      case "wait_for_dialog": {
        await emitOperationEvent({ type: OPERATION_EVENTS.READ_START, tabId: tab.id, url: tab.url, commandId: command.id });
        await new Promise(resolve => setTimeout(resolve, command.payload?.timeout_ms || 5000));
        const waitResult = await executeInTab(tab.id, observeActionPage, [command.payload?.selector || null]);
        await emitOperationEvent({ type: OPERATION_EVENTS.READ_END, tabId: tab.id, url: tab.url, commandId: command.id, status: "completed" });
        return { available: true, target_tab: selected, ...waitResult, waited: true };
      }
      case "control_media": {
        await emitOperationEvent({ type: OPERATION_EVENTS.CONTROL_MEDIA, tabId: tab.id, url: tab.url, commandId: command.id });
        const mediaResult = await executeInTab(tab.id, controlMedia, [command.payload?.action || "pause"]);
        await emitOperationEvent({ type: OPERATION_EVENTS.CONTROL_MEDIA, tabId: tab.id, url: tab.url, commandId: command.id, status: "completed" });
        return { available: true, target_tab: selected, ...mediaResult };
      }
      case "download": {
        await emitOperationEvent({ type: OPERATION_EVENTS.NAVIGATE_START, tabId: tab.id, url: tab.url, commandId: command.id });
        const dlUrl = String(command.payload?.url || "").trim();
        if (dlUrl) {
          if (!publicUrl(dlUrl)) throw new Error("Onsite48 Bridge only permits public http or https download URLs.");
          const downloadId = await chrome.downloads.download({ url: dlUrl, saveAs: true });
          const result = { downloaded: true, download_id: downloadId, url: dlUrl };
          await emitOperationEvent({ type: OPERATION_EVENTS.NAVIGATE_END, tabId: tab.id, url: tab.url, commandId: command.id, status: "completed" });
          return { available: true, target_tab: selected, ...result, download_triggered: true };
        }
        const dlResult = await executeInTab(tab.id, triggerDownloadTarget, [command.payload?.selector]);
        await emitOperationEvent({ type: OPERATION_EVENTS.NAVIGATE_END, tabId: tab.id, url: tab.url, commandId: command.id, status: "completed" });
        return { available: true, target_tab: selected, ...dlResult, download_triggered: true };
      }
      case "upload": {
        await emitOperationEvent({ type: OPERATION_EVENTS.FILL_START, tabId: tab.id, url: tab.url, commandId: command.id });
        const upFile = await stagedUpload();
        if (!upFile || upFile.name !== String(command.payload?.file_name || "")) throw new Error("Stage the exact requested file in the Onsite48 Bridge popup before uploading.");
        const upResult = await executeInTab(tab.id, uploadFileToPage, [command.payload?.selector, upFile]);
        await clearStagedUpload();
        await emitOperationEvent({ type: OPERATION_EVENTS.FILL_END, tabId: tab.id, url: tab.url, commandId: command.id, status: "completed" });
        return { available: true, target_tab: selected, ...upResult };
      }
      case "takeover": {
        const sessionId = String(command.payload?.session_id || activeBrowserSessionId || "").trim();
        if (sessionId) await markWorkspaceStatus(sessionId, "taken_over");
        await setOperatorState({ status: "taken_over", lastError: null });
        return { takeover: true, session_id: sessionId || null };
      }
      case "resume": {
        const sessionId = String(command.payload?.session_id || activeBrowserSessionId || "").trim();
        if (sessionId) await markWorkspaceStatus(sessionId, "active");
        await setOperatorState({ status: "active", lastError: null });
        return { resumed: true, session_id: sessionId || null, requires_reobserve: true };
      }
      case "stop": {
        const sessionId = String(command.payload?.session_id || activeBrowserSessionId || "").trim();
        if (sessionId) {
          await destroyTaskWorkspace(sessionId, { closeTabs: command.payload?.close_tabs !== false });
          if (activeBrowserSessionId === sessionId) activeBrowserSessionId = null;
        }
        return { stopped: true, session_id: sessionId || null };
      }
      default: throw new Error("Unsupported bridge command.");
    }
  } finally {
    await sendOverlay(tab.id, { phase: "ready" });
  }
}

async function transportPreflight(command, payload) {
  if (websocketReady()) {
    try { return await bridgeRequest("command_preflight", command.id, payload); } catch { /* use durable REST fallback */ }
  }
  return api(`/api/bridge/extension/commands/${command.id}/preflight`, {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

async function preflight(command) {
  const workspaceCommands = [
    "create_task_workspace", "claim_task_workspace", "open_task_tab", "close_task_tab",
    "close_task_workspace", "list_task_tabs", "takeover", "resume", "stop"
  ];
  if (workspaceCommands.includes(command.command_type)) {
    return transportPreflight(command, { ready: true });
  }
  try {
    const { tab } = await selectedTabForCommand(command);
    const origin = new URL(tab.url).origin;
    if (!await chrome.permissions.contains({ origins: [optionalOrigin(tab)] })) {
      await setOperatorState({ selectedTab: (await settings()).selectedTab, selectionState: "permission_required" });
      return transportPreflight(command, {
        ready: false,
        origin,
        failure_code: "selected_tab_permission_required",
        reason: `Grant Onsite48 Bridge access to ${new URL(tab.url).hostname} before this approved action can run.`,
      });
    }
    return transportPreflight(command, { ready: true, origin });
  } catch (error) {
    return transportPreflight(command, {
      ready: false,
      failure_code: error?.code || "selected_tab_required",
        reason: error instanceof Error ? error.message : "Focus the intended page before this action can run.",
    });
  }
}

async function complete(command, result, { evidence = {}, verification = {}, error = null, cancelled = false, cancelReason = null } = {}) {
  const sessionId = String(command.payload?.session_id || command.session_id || activeBrowserSessionId || "").trim();
  const active = activeCommands.get(sessionId);
  const leaseToken = active?.id === command.id ? active.leaseToken : command.lease_token;
  const payload = { result: result || {}, evidence, verification, lease_token: leaseToken || null, error, cancelled, cancel_reason: cancelReason };
  const terminalType = error || cancelled ? "command_failed" : "command_completed";
  const receiptState = cancelled ? "cancelled" : error ? "failed" : "completed";
  const clearActive = () => {
    if (active?.id === command.id) activeCommands.delete(sessionId);
  };
  if (websocketReady()) {
    try {
      await bridgeRequest(terminalType, command.id, payload);
      await recordTerminalReceipt(command, receiptState, error || cancelReason);
      clearActive();
      return;
    } catch {
      // Fall through to REST completion.
    }
  }
  try {
    await api(`/api/bridge/extension/commands/${command.id}/complete`, {
      method: "POST",
      body: JSON.stringify(payload),
    });
    await recordTerminalReceipt(command, receiptState, error || cancelReason);
    clearActive();
  } catch (err) {
    // stale_lease / already terminal: stop retrying this command.
    if (err && (err.code === "stale_lease" || err.status === 409)) {
      await recordTerminalReceipt(command, "rejected", "The command lease was stale or already terminal.");
      clearActive();
      return;
    }
    throw err;
  }
}

/**
 * Complete command with retry logic for completion only (not re-execution).
 * This prevents double-execution of commands like click, type, etc.
 */
async function completeCommandWithRetry(command, result, { evidence = {}, verification = {}, error = null, cancelled = false, cancelReason = null } = {}) {
  const maxCompletionRetries = 3;
  let attempt = 0;
  
  while (attempt < maxCompletionRetries) {
    try {
      await complete(command, result, { evidence, verification, error, cancelled, cancelReason });
      return; // Success
    } catch (err) {
      attempt++;
      // Check if it's a stale lease or already terminal - don't retry
      if (err && (err.code === "stale_lease" || err.status === 409)) {
        await recordTerminalReceipt(command, "rejected", "The command lease was stale or already terminal.");
        const sessionId = String(command.payload?.session_id || command.session_id || activeBrowserSessionId || "").trim();
        const active = activeCommands.get(sessionId);
        if (active?.id === command.id) activeCommands.delete(sessionId);
        return;
      }
      if (attempt >= maxCompletionRetries) {
        throw err; // Re-throw after max retries
      }
      // Wait before retry
      await new Promise(r => setTimeout(r, 500 * attempt));
    }
  }
}

async function captureActionObservation(command) {
  try {
    // These workspace commands don't operate on a single active page
    const workspaceCommands = [
      "create_task_workspace", "claim_task_workspace", "open_task_tab", "close_task_tab",
      "close_task_workspace", "list_task_tabs", "takeover", "resume", "stop"
    ];
    if (workspaceCommands.includes(command.command_type)) {
      return { available: false, reason: "This command does not operate on one active page." };
    }
    const { tab, selected } = await selectedTabForCommand(command);
    await ensureSitePermission(tab, false);
    const observation = await executeInTab(tab.id, observeActionPage, [command.payload?.selector || null]);
    return {
      available: true,
      tab_id: tab.id,
      target_tab: selected,
      command_type: command.command_type,
      selector: command.payload?.selector || null,
      observation,
      captured_at: new Date().toISOString(),
    };
  } catch (error) {
    return { available: false, reason: error instanceof Error ? error.message : "Observation was unavailable." };
  }
}

function evidenceFrom(command, before, after, verification) {
  return evidenceEnvelope(
    command,
    before?.available
      ? { target_tab: before.target_tab, observation: before.observation }
      : { unavailable: true, reason: before?.reason || "No before observation." },
    after?.available
      ? { target_tab: after.target_tab, observation: after.observation }
      : { unavailable: true, reason: after?.reason || "No after observation." },
    verification,
  );
}

async function verifyEvidence(command, before, after, rules = []) {
  if (!before?.available || !after?.available || before.tab_id !== after.tab_id) {
    return { 
      status: "inconclusive", 
      checks: [{ name: "observation_continuity", passed: false, detail: "A comparable before/after observation was unavailable." }],
      passed: 0,
      failed: 0,
      inconclusive: 1,
      observed_at: new Date().toISOString()
    };
  }
  
  // If no verification rules provided, do basic verification
  if (!rules || rules.length === 0) {
    try {
      return await executeInTab(after.tab_id, verifyAction, [before.observation.page, after.observation.page, command.payload?.verification || {}]);
    } catch (error) {
      return { 
        status: "inconclusive", 
        checks: [{ name: "verification_execution", passed: false, detail: error instanceof Error ? error.message : "Verification was unavailable." }],
        passed: 0,
        failed: 0,
        inconclusive: 1,
        observed_at: new Date().toISOString()
      };
    }
  }
  
  // Execute each verification rule
  const checks = [];
  let passed = 0;
  let failed = 0;
  let inconclusive = 0;
  
  for (const rule of rules) {
    const ruleType = rule.type;
    const expected = rule.expected || {};
    const required = rule.required !== undefined ? rule.required : true;
    const description = rule.description || "";
    
    try {
      if (ruleType === "url_matches" || ruleType === "browser_url") {
        const afterUrl = after.observation?.page?.url || "";
        const expectedUrl = expected.url || expected.expected;
        const matchType = expected.match_type || "contains";
        
        let checkPassed;
        if (matchType === "exact") {
          checkPassed = afterUrl === expectedUrl;
        } else {
          checkPassed = expectedUrl && afterUrl.includes(expectedUrl);
        }
        
        checks.push({ 
          name: "url_matches", 
          passed: checkPassed, 
          detail: checkPassed ? "URL matches expected" : `URL '${afterUrl}' does not match expected '${expectedUrl}'` 
        });
        if (checkPassed) passed++; else if (required) failed++; else inconclusive++;
        
      } else if (ruleType === "title_matches" || ruleType === "browser_title") {
        const afterTitle = after.observation?.page?.title || "";
        const expectedTitle = expected.title || expected.expected;
        const checkPassed = expectedTitle && afterTitle.includes(expectedTitle);
        checks.push({ 
          name: "title_matches", 
          passed: checkPassed, 
          detail: checkPassed ? "Title matches expected" : `Title '${afterTitle}' does not contain expected` 
        });
        if (checkPassed) passed++; else if (required) failed++; else inconclusive++;
        
      } else if (ruleType === "text_contains" || ruleType === "browser_text_contains") {
        const afterText = after.observation?.page?.text || "";
        const expectedText = expected.text || expected.expected;
        const checkPassed = expectedText && afterText.includes(expectedText);
        checks.push({ 
          name: "text_contains", 
          passed: checkPassed, 
          detail: checkPassed ? "Text contains expected" : `Text does not contain '${expectedText}'` 
        });
        if (checkPassed) passed++; else if (required) failed++; else inconclusive++;
        
      } else if (ruleType === "element_present" || ruleType === "browser_element_present") {
        const afterElements = after.observation?.elements || [];
        const selector = expected.selector;
        const checkPassed = selector && afterElements.some(e => e.selector === selector);
        checks.push({ 
          name: "element_present", 
          passed: checkPassed, 
          detail: checkPassed ? "Element found" : "Element not found" 
        });
        if (checkPassed) passed++; else if (required) failed++; else inconclusive++;
        
      } else if (ruleType === "url_changed" || ruleType === "before_after_changed") {
        const beforeUrl = before.observation?.page?.url || "";
        const afterUrl = after.observation?.page?.url || "";
        const field = expected.field || "url";
        let beforeValue, afterValue;
        
        if (field === "page_digest") {
          beforeValue = before.observation?.page?.page_digest || "";
          afterValue = after.observation?.page?.page_digest || "";
        } else if (field === "scroll_y" || field === "scroll_position") {
          beforeValue = before.observation?.page?.scroll_y || 0;
          afterValue = after.observation?.page?.scroll_y || 0;
        } else {
          beforeValue = beforeUrl;
          afterValue = afterUrl;
        }
        
        const checkPassed = beforeValue !== afterValue;
        checks.push({ 
          name: field === "page_digest" ? "page_digest_changed" : "state_changed", 
          passed: checkPassed, 
          detail: checkPassed ? "State changed as expected" : "State did not change" 
        });
        if (checkPassed) passed++; else if (required) failed++; else inconclusive++;
        
      } else if (ruleType === "element_attribute" || ruleType === "browser_element_attribute") {
        const afterElements = after.observation?.elements || [];
        const selector = expected.selector;
        const attribute = expected.attribute;
        const value = expected.value;
        
        const matching = afterElements.filter(e => e.selector === selector);
        let checkPassed;
        if (matching.length > 0) {
          const actualValue = matching[0][attribute];
          if (value === undefined || value === null) {
            checkPassed = actualValue !== undefined && actualValue !== null;
          } else {
            checkPassed = actualValue === value;
          }
        } else {
          checkPassed = false;
        }
        
        checks.push({ 
          name: "element_attribute", 
          passed: checkPassed, 
          detail: checkPassed ? "Element attribute matches" : "Element attribute does not match" 
        });
        if (checkPassed) passed++; else if (required) failed++; else inconclusive++;
        
      } else if (ruleType === "field_exists" || ruleType === "entity_exists") {
        const afterData = after.observation || {};
        const field = expected.field || expected.entity_type;
        const checkPassed = field && afterData[field] !== undefined && afterData[field] !== null;
        checks.push({ 
          name: "field_exists", 
          passed: checkPassed, 
          detail: checkPassed ? `Field '${field}' exists` : `Field '${field}' does not exist` 
        });
        if (checkPassed) passed++; else if (required) failed++; else inconclusive++;
        
      } else if (ruleType === "field_equals" || ruleType === "entity_state") {
        const afterData = after.observation || {};
        const field = expected.field || expected.entity_type;
        const value = expected.value;
        const checkPassed = field && afterData[field] === value;
        checks.push({ 
          name: "field_equals", 
          passed: checkPassed, 
          detail: checkPassed ? `Field '${field}' equals expected` : `Field '${field}' does not equal expected` 
        });
        if (checkPassed) passed++; else if (required) failed++; else inconclusive++;
        
      } else {
        checks.push({ 
          name: ruleType || "unknown_rule", 
          passed: null, 
          detail: `Unsupported rule type: ${ruleType}` 
        });
        inconclusive++;
      }
    } catch (error) {
      checks.push({ 
        name: ruleType || "unknown_rule", 
        passed: false, 
        detail: error instanceof Error ? error.message : "Verification failed" 
      });
      if (required) failed++; else inconclusive++;
    }
  }
  
  const status = failed > 0 ? "failed" : passed > 0 && inconclusive === 0 ? "verified" : "inconclusive";
  
  return {
    status,
    checks,
    passed,
    failed,
    inconclusive,
    observed_at: new Date().toISOString(),
  };
}

async function processCommand(command, transport = "poll", isRetry = false) {
  try {
    // Normalize legacy *_active_tab commands to new task-owned equivalents
    const primaryTabId = activeBrowserSessionId;
    command = normalizeLegacyCommand(command, primaryTabId);
    command = validateCommand(command);
  } catch (error) {
    console.warn("Onsite48 Bridge rejected an invalid command:", error);
    return 0;
  }
  if (command.payload?.session_id) activeBrowserSessionId = String(command.payload.session_id);
  const receiptKey = commandKey(command);
  if (command.session_id) {
    const sessionKey = String(command.session_id);
    const sequence = Number(command.session_sequence || 0);
    const previousSequence = Number(sessionSequenceState.get(sessionKey) || 0);
    if (sequence < previousSequence) {
      console.warn("Onsite48 Bridge rejected a stale session command", { sessionKey, sequence, previousSequence, command: sessionCommandKey(command) });
      return 0;
    }
    sessionSequenceState.set(sessionKey, Math.max(previousSequence, sequence));
  }
  if (commandsInFlight.has(receiptKey) || await hasTerminalReceipt(command)) return 0;

  // Check if this command is already being retried.
  if (!isRetry && commandRetryState.has(command.id)) {
    console.debug(`Onsite48 Bridge: Command ${command.id} is already being retried, skipping duplicate`);
    return 0;
  }

  commandsInFlight.add(receiptKey);
  await persistCommandsInFlight();
  let before = null;
  let evidence = {};

  // Wire task lifecycle: start task when first command for a thread arrives
  if (command.task_id) {
    const ctx = await getTaskContext();
    if (ctx.taskId !== command.task_id) {
      const threadId = command.payload?.thread_id || null;
      const goal = command.payload?.goal || command.payload?.task_goal || command.task_name || null;
      await startTask(command.task_id, threadId, goal);
      await setTaskContext({ activeTab: activeContext.tabId });
    }
  }
  
  try {
    const current = await settings();
    if (current.operatorState.paused) {
      await complete(command, {}, { 
        cancelled: true, 
        cancelReason: "The local browser operator is paused by the user. Resume it from the extension before retrying." 
      });
      return 0;
    }
    
    const readiness = await preflight(command);
    if (readiness.status !== "claimed") {
      await setOperatorState({
        phase: "permission_required",
        lastCommand: { id: command.id, type: command.command_type, status: "pending" },
        permissionOrigin: readiness.preflight_origin || command.preflight_origin || null,
        permissionReason: readiness.preflight_reason || "Grant this site in Onsite48 Bridge, then resume the paused task in Onsite48.",
        lastPollAt: new Date().toISOString(),
      });
      return 0;
    }
    
    const sessionId = String(command.payload?.session_id || command.session_id || activeBrowserSessionId || "").trim();
    
    activeCommands.set(sessionId, { 
      id: command.id,
      tabId: null,
      cancelled: false,
      transport,
      leaseToken: readiness.lease_token || command.lease_token || null,
      leaseExpiresAt: readiness.lease_expires_at || command.lease_expires_at || null,
      context: {
        goal: command.payload?.goal || null,
        step_id: command.payload?.step_id || null,
        expected_outcome: command.payload?.expected_outcome || null,
      },
    });
    
    await setOperatorState({ 
      phase: "running", 
      permissionOrigin: null, 
      permissionReason: null, 
      lastCommand: { id: command.id, type: command.command_type, status: "running" } 
    });
    
    await sendBridgeEvent({ 
      type: "command_started", 
      command_id: command.id, 
      detail: `Synth is ${commandTitle(command)} in the selected tab.` 
    });
    
    // ─── CAPTURE BEFORE OBSERVATION ──────────────────────────────
    before = await captureActionObservation(command);
    await sendBridgeEvent({ 
      type: "command_progress", 
      command_id: command.id, 
      detail: "Reading the selected browser tab before action." 
    });
    
    // ─── EXECUTE COMMAND ───────────────────────────────────────────
    const result = await runCommand(command);
    ensureActive(command);
    
    // Store result for potential retry of completion only
    const active = activeCommands.get(sessionId);
    if (active) {
      active.lastResult = result;
      active.lastBefore = before;
      active.lastVerificationRules = command.payload?.verification || [];
    }
    
    // ─── CAPTURE AFTER OBSERVATION ────────────────────────────────
    const after = await captureActionObservation(command);
    
    // ─── EXTRACT VERIFICATION RULES ───────────────────────────────
    const verificationRules = command.payload?.verification || [];
    
    // ─── RUN VERIFICATION ──────────────────────────────────────────
    const verification = await verifyEvidence(command, before, after, verificationRules);
    evidence = evidenceFrom(command, before, after, verification);
    
    await sendBridgeEvent({ 
      type: "command_evidence", 
      command_id: command.id, 
      detail: "Captured deterministic local-browser evidence.", 
      evidence 
    });
    
    // If verification failed, mark as failed with details
    if (verification.status === "failed") {
      const failedChecks = verification.checks.filter(c => c.passed === false).map(c => c.detail).join("; ");
      await complete(command, result, { 
        evidence, 
        verification,
        error: `Verification failed: ${failedChecks}`,
        cancelled: false,
      });
      await setOperatorState({ 
        phase: "error", 
        lastCommand: { id: command.id, type: command.command_type, status: "failed" },
        lastError: `Verification failed: ${failedChecks}`,
        lastPollAt: new Date().toISOString(),
      });
      commandsInFlight.delete(commandKey(command));
      await persistCommandsInFlight();
      activeCommands.delete(sessionId);
      return 0;
    }
    
    // ─── COMPLETE COMMAND (separate from execution for retry safety) ───
    await completeCommandWithRetry(command, result, { evidence, verification });
    
    const activeAfter = activeCommands.get(sessionId);
    if (activeAfter?.tabId) {
      await sendOverlay(activeAfter.tabId, { 
        phase: "done", 
        title: "Synth completed this browser action", 
        detail: "The result was returned to Synth." 
      });
    }
    
    await setOperatorState({ 
      phase: "ready", 
      lastCommand: { 
        id: command.id, 
        type: command.command_type, 
        status: "completed", 
        verification: verification.status 
      }, 
      lastPollAt: new Date().toISOString() 
    });
    
    // Clear any retry state on success
    cancelRetry(command.id);
    activeCommands.delete(sessionId);
    return 1;
    
  } catch (error) {
    const message = error instanceof Error ? error.message : "Bridge command failed.";
    const active = activeCommands.get(sessionId);
    const cancelled = Boolean(active?.cancelled) || /stopped by the user|user took over|paused by the user/i.test(message);
    
    // ─── RETRY LOGIC ───────────────────────────────────────────────
    if (!cancelled && shouldRetryCommand(error, command)) {
      const retryInfo = commandRetryState.get(command.id) || { attempts: 0, nextDelay: COMMAND_RETRY_DELAY_MS };
      retryInfo.attempts += 1;
      
      if (retryInfo.attempts >= COMMAND_RETRY_MAX_ATTEMPTS) {
        commandRetryState.delete(command.id);
        await complete(command, {}, { 
          error: `Command failed after ${COMMAND_RETRY_MAX_ATTEMPTS} attempts: ${message}`,
          cancelled: false,  // FIX: report as failed, not cancelled
        });
        await setOperatorState({
          phase: "error",
          lastCommand: { 
            id: command.id, 
            type: command.command_type, 
            status: "failed",
            attempts: retryInfo.attempts
          },
          lastError: `Failed after ${retryInfo.attempts} attempts: ${message}`,
          lastPollAt: new Date().toISOString(),
        });
        cancelRetry(command.id);
        return 0;
      }
      
      const delay = getRetryDelay(retryInfo.attempts);
      retryInfo.nextDelay = delay;
      commandRetryState.set(command.id, retryInfo);
      await persistCommandRetryState();
      
      console.log(`Onsite48 Bridge: Retrying command ${command.id} (attempt ${retryInfo.attempts}/${COMMAND_RETRY_MAX_ATTEMPTS}) in ${delay}ms`);
      
      await sendBridgeEvent({
        type: "command_progress",
        command_id: command.id,
        detail: `Retrying (attempt ${retryInfo.attempts}/${COMMAND_RETRY_MAX_ATTEMPTS})...`,
      });
      
      await setOperatorState({
        phase: "retrying",
        lastCommand: { 
          id: command.id, 
          type: command.command_type, 
          status: "retrying",
          attempt: retryInfo.attempts
        },
        lastError: `Retrying (attempt ${retryInfo.attempts})`,
        lastPollAt: new Date().toISOString(),
      });
      
      // Schedule retry
      const retryTimer = setTimeout(async () => {
        try {
          await processCommand(command, transport, true);
        } catch (retryError) {
          console.warn(`Onsite48 Bridge: Retry ${retryInfo.attempts} for command ${command.id} failed:`, retryError);
        }
      }, delay);
      
      retryInfo.timer = retryTimer;
      commandRetryState.set(command.id, retryInfo);
      await persistCommandRetryState();
      return 0;
    }
    
    // ─── FALLBACK: Command failed permanently ──────────────────────
    if (before && !Object.keys(evidence).length) {
      const after = await captureActionObservation(command);
      evidence = evidenceFrom(command, before, after);
    }
    
    await complete(command, {}, { 
      evidence, 
      error: message, 
      cancelled, 
      cancelReason: cancelled ? message : null 
    });
    
    await setOperatorState({ 
      phase: cancelled ? "paused" : "error", 
      lastCommand: { 
        id: command.id, 
        type: command.command_type, 
        status: cancelled ? "cancelled" : "failed" 
      }, 
      lastError: message, 
      lastPollAt: new Date().toISOString() 
    });
    
    // Clean up retry state
    cancelRetry(command.id);
    return 0;
    
  } finally {
    const active = activeCommands.get(sessionId);
    if (active?.tabId) {
      const phase = active.cancelled ? "paused" : "done";
      await sendOverlay(active.tabId, { 
        phase, 
        detail: active.cancelled 
          ? "This browser action was stopped. Resume in Synth only after reviewing the page." 
          : "This browser action is complete." 
      });
    }
    activeCommands.delete(sessionId);
    commandsInFlight.delete(commandKey(command));
    await persistCommandsInFlight();
  }
}

async function poll(reason = "scheduled", waitSeconds = 0) {
  if (pollInFlight) return { paired: false, count: 0, reason, skipped: "poll_in_flight" };
  const { bridgeToken, enabled, operatorState } = await settings();
  if (!enabled || !bridgeToken) return { paired: false, count: 0, reason };
  if (operatorState.paused) return { paired: true, count: 0, reason, paused: true, state: operatorState };
  if (websocketReady()) return { paired: true, count: 0, reason, transport: "websocket", state: operatorState };

  pollInFlight = true;
  try {
    await setTransportState("poll_fallback");
    await setOperatorState({ phase: "checking", lastError: null });
    await reportActiveContext();
    const { commands } = await api(`/api/bridge/extension/commands?wait_seconds=${Math.max(0, Math.min(waitSeconds, 25))}`);
    const items = commands || [];
    if (!items.length) {
      const state = await setOperatorState({ phase: "ready", lastCommand: null, lastPollAt: new Date().toISOString() });
      return { paired: true, count: 0, reason, state };
    }

    let completed = 0;
    for (const command of items) completed += await processCommand(command, "poll");
    const { operatorState: state } = await settings();
    return { paired: true, count: completed, reason, state };
  } finally {
    pollInFlight = false;
  }
}

async function operatorStatus() {
  const { apiBase, bridgeToken, enabled, device, operatorState, selectedTab, operatorMode } = await settings();
  const sessionState = await getSessionState();
  let selection = selectedTab;
  if (selection) {
    try {
      const { selected } = await selectedTabForCommand();
      selection = selected;
    } catch {
      selection = null;
    }
  }
  return {
    paired: Boolean(enabled && bridgeToken),
    apiBase,
    device,
    operatorState,
    sessionState,
    selectedTab: selection,
    operatorMode,
    activeCommand: activeCommands.size > 0 ? Array.from(activeCommands.values())[0] : null,
    reconnection: getReconnectionStatus(),
    commandRetry: getCommandRetryStatus(),
  };
}

async function setOperatorMode(mode) {
  const next = mode === "selected_only" ? "selected_only" : "active_tab";
  await chrome.storage.local.set({ [OPERATOR_MODE_KEY]: next });
  return operatorStatus();
}

async function setPaused(paused, reason, source = "user") {
  if (paused) {
    for (const [sessionId, active] of activeCommands) {
      await invalidateActiveCommandLease(reason);
      if (active.tabId) await sendOverlay(active.tabId, { phase: paused ? "paused" : "ready", title: paused ? "Onsite48 Bridge is paused" : "Onsite48 Bridge is ready", detail: reason });
    }
  } else {
    for (const [sessionId, active] of activeCommands) {
      if (active.tabId) await sendOverlay(active.tabId, { phase: paused ? "paused" : "ready", title: paused ? "Onsite48 Bridge is paused" : "Onsite48 Bridge is ready", detail: reason });
    }
  }
  if (paused) {
    const ctx = await getTaskContext();
    if (ctx.taskId) {
      if (source === "stop") {
        // Stop: destroy the task workspace (close tabs, remove group)
        await destroyTaskWorkspace(ctx.taskId, { closeTabs: true });
      } else {
        // Pause/Takeover: just mark task as paused
        await completeTask(source === "takeover" ? "cancelled" : "paused");
      }
    }
  }
  return setOperatorState({
    phase: paused ? "paused" : "ready",
    paused,
    pauseReason: reason,
    pauseSource: paused ? source : null,
    takeoverAt: paused && source === "takeover" ? new Date().toISOString() : null,
    lastError: null,
  });
}

async function openOperatorSettings() {
  const { apiBase } = await settings();
  await chrome.tabs.create({ url: `${apiBase}/bridge`, active: true });
  return { opened: true };
}

async function openSynth() {
  const { apiBase } = await settings();
  await chrome.tabs.create({ url: apiBase, active: true });
  return { opened: true };
}

function schedulePollAlarm() {
  chrome.alarms.create(POLL_ALARM, { periodInMinutes: 0.5 });
}

// ─── Cleanup stale waiters periodically ──────────────────────────
setInterval(cleanStaleWaiters, 10000);

// ─── Event Listeners ─────────────────────────────────────────────

async function restorePersistentState() {
  await restoreCommandRetryState();
  await restoreCommandsInFlight();
  await restoreSessionSequenceState();
}

chrome.runtime.onInstalled.addListener(async () => { 
  await restorePersistentState();
  schedulePollAlarm(); 
  void connectBridgeSocket(); 
  void poll("installed", 0); 
});
chrome.runtime.onStartup.addListener(async () => { 
  await restorePersistentState();
  schedulePollAlarm(); 
  void connectBridgeSocket(); 
  void poll("startup", 0); 
});
chrome.alarms.onAlarm.addListener((alarm) => { if (alarm.name === POLL_ALARM) { void connectBridgeSocket(); void poll("scheduled", 25); } });

chrome.tabs.onActivated.addListener(({ tabId }) => {
  void (async () => {
    await updateActiveContext(tabId);
    const sessionState = await getSessionState();
    const { operatorState } = await settings();
    if (sessionState === "ACTIVE" && !operatorState.paused) {
      await reportActiveContext();
      if (websocketReady()) await streamLiveTabState("tab_activated");
    }
    await sendBridgeEvent({ type: "tab_lifecycle", session_id: activeBrowserSessionId, event: "activated", tab_id: tabId });
  })();
});
chrome.tabs.onUpdated.addListener((tabId, changeInfo) => {
  if (changeInfo.status !== "complete") return;
  void (async () => {
    const sessionState = await getSessionState();
    if (sessionState === "ACTIVE") {
      const active = await getActiveContext();
      if (active.tabId === tabId) await updateActiveContext(tabId);
    } else {
      const { selectedTab } = await settings();
      if (selectedTab?.tabId === tabId) await reportActiveContext();
    }
    if (websocketReady()) await streamLiveTabState("tab_updated");
    await sendBridgeEvent({ type: "tab_lifecycle", session_id: activeBrowserSessionId, event: "updated", tab_id: tabId, change_info: changeInfo });
  })();
});
chrome.tabs.onCreated.addListener((tab) => {
  void sendBridgeEvent({ type: "tab_lifecycle", session_id: activeBrowserSessionId, event: "created", tab_id: tab.id, window_id: tab.windowId, url: tab.url || "" });
});
chrome.tabs.onRemoved.addListener((tabId, removeInfo) => {
  void (async () => {
    const { selectedTab } = await settings();
    if (selectedTab?.tabId === tabId) await clearSelectedTab("closed");
    
    // Clean up workspace if this tab was part of a task workspace
    const workspaces = await loadWorkspaces();
    for (const [sessionId, ws] of Object.entries(workspaces)) {
      if (ws.tabIds?.includes(tabId)) {
        // Remove tab from workspace
        ws.tabIds = ws.tabIds.filter(id => id !== tabId);
        if (ws.primaryTabId === tabId) {
          ws.primaryTabId = ws.tabIds[0] || null;
        }
        if (ws.tabIds.length === 0) {
          // No tabs left, clean up tab group and remove workspace
          if (ws.tabGroupId != null) {
            try { await chrome.tabGroups.remove(ws.tabGroupId); } catch { /* ignore */ }
          }
          delete workspaces[sessionId];
        } else {
          ws.updatedAt = new Date().toISOString();
        }
        await saveWorkspaces(workspaces);
        break;
      }
    }
    
    await sendBridgeEvent({ type: "tab_lifecycle", session_id: activeBrowserSessionId, event: "closed", tab_id: tabId });
  })();
});

// ─── Message Handlers ────────────────────────────────────────────

chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  const senderTabId = sender?.tab?.id;
  
  async function checkTaskTabAndAct(action, message) {
    if (action === "operatorTakeover") {
      if (!senderTabId) return { ok: false, error: "Takeover must be initiated from a browser tab" };
      const workspaces = await loadWorkspaces();
      const isTaskTab = Object.values(workspaces).some(ws => ws.tabIds?.includes(senderTabId));
      if (!isTaskTab) return { ok: false, error: "Takeover is only allowed in task-owned tabs" };
    }
    return handlers[action](message);
  }
  
  const handlers = {
    poll: () => poll("manual", 0),
    sync: () => syncSelectedTab(message?.tabId).then(() => operatorStatus()),
    status: () => operatorStatus(),
    setOperatorMode: () => setOperatorMode(message?.mode),
    authorizeSession: () => sessionAuthorize(message?.origins || [], message?.capabilities || []),
    pause: () => setPaused(true, "Paused from the Onsite48 Bridge extension.", "user"),
    resume: () => setPaused(false, "Resumed from the Onsite48 Bridge extension.", "user"),
    operatorPause: () => setPaused(true, "Paused by the user in the active tab.", "pause"),
    operatorStop: () => { setPaused(true, "Stopped by the user in the active tab.", "stop"); },
    operatorTakeover: () => setPaused(true, "You took over this local browser tab. Complete the page yourself, then choose Resume operator when ready for the next approved Synth action.", "takeover"),
    operatorResume: () => { void connectBridgeSocket(); setPaused(false, "The local browser operator is ready for the next approved Synth action.", "user"); },
    openSettings: () => openOperatorSettings(),
    openSynth: () => openSynth(),
    connectTransport: () => connectBridgeSocket(),
    disconnectTransport: () => disconnectBridgeSocket(message?.permanent === true),
    getReconnectionStatus: () => getReconnectionStatus(),
    getCommandRetryStatus: () => getCommandRetryStatus(),
    stageUpload: () => stageUpload(message?.file),
    clearStagedUpload: () => clearStagedUpload(),
  };
  const action = message?.type;
  if (!handlers[action]) return false;
  
  // Deduplicate requests
  const requestId = message?._requestId || `req-${Date.now()}-${Math.random().toString(36).slice(2, 6)}`;
  if (pendingRequests.has(requestId)) {
    sendResponse({ error: "Duplicate request" });
    return true;
  }
  pendingRequests.set(requestId, true);
  setTimeout(() => pendingRequests.delete(requestId), 5000);
  
  checkTaskTabAndAct(action, message).then(sendResponse).catch((error) => sendResponse({ error: error instanceof Error ? error.message : "Bridge operation failed." }));
  return true;
});