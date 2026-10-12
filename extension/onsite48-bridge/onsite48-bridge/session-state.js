// extension/onsite48-bridge/session-state.js
// Session state machine: AUTHORIZING → ACTIVE → PAUSED_* → TERMINAL

const SESSION_STATES = {
  AUTHORIZING: "authorizing",
  ACTIVE: "active",
  PAUSED_USER: "paused_user",
  PAUSED_CHALLENGE: "paused_challenge",
  PAUSED_STOPPED: "paused_stopped",
  TERMINAL: "terminal",
};

const TRANSITIONS = {
  AUTHORIZING: ["ACTIVE"],
  ACTIVE: ["PAUSED_USER", "PAUSED_CHALLENGE", "PAUSED_STOPPED", "TERMINAL"],
  PAUSED_USER: ["ACTIVE", "TERMINAL"],
  PAUSED_CHALLENGE: ["ACTIVE", "TERMINAL"],
  PAUSED_STOPPED: ["ACTIVE", "TERMINAL"],
  TERMINAL: [],
};

function isValidTransition(from, to) {
  return TRANSITIONS[from]?.includes(to) ?? false;
}

export async function getSessionState() {
  const { sessionState } = await chrome.storage.local.get("sessionState");
  return sessionState || "AUTHORIZING";
}

/**
 * Transition session state.
 * Preserves sessionAuthorizedAt across pauses; clears only on TERMINAL.
 */
export async function setSessionState(newState) {
  const current = await getSessionState();
  if (!isValidTransition(current, newState)) {
    console.warn(`session-state: invalid transition ${current} → ${newState}`);
    return false;
  }

  const stored = await chrome.storage.local.get([
    "sessionAuthorizedAt",
    "authorizedOrigins",
    "authorizedCapabilities",
  ]);

  const nextAuthorizedAt =
    newState === "TERMINAL"
      ? null
      : stored.sessionAuthorizedAt ||
        (newState === "ACTIVE" ? Date.now() : null);

  await chrome.storage.local.set({
    sessionState: newState,
    sessionAuthorizedAt: nextAuthorizedAt,
    authorizedOrigins: stored.authorizedOrigins,
    authorizedCapabilities: stored.authorizedCapabilities,
  });
  return true;
}

export async function broadcastSessionState(newState) {
  const tabs = await chrome.tabs.query({});
  for (const tab of tabs) {
    try {
      await chrome.tabs.sendMessage(tab.id, {
        type: "sessionState",
        sessionState: newState,
      });
    } catch {
      // Tab might not have content script
    }
  }
}

/**
 * Authorize a session with origins and capabilities.
 * Validates origins is an array and capabilities is an object.
 */
export async function authorizeSession(origins, capabilities) {
  if (!Array.isArray(origins)) {
    return { ok: false, error: "origins must be an array" };
  }
  if (capabilities != null && typeof capabilities !== "object") {
    return { ok: false, error: "capabilities must be an object" };
  }

  for (const origin of origins) {
    if (
      origin === "<all_urls>" ||
      origin.startsWith("*://") ||
      origin.startsWith("http://") ||
      origin.startsWith("https://") ||
      origin.startsWith("file://")
    ) {
      continue;
    }
    try {
      new URL(origin);
    } catch {
      return { ok: false, error: `Invalid origin: ${origin}` };
    }
  }

  await chrome.storage.local.set({
    authorizedOrigins: origins,
    authorizedCapabilities: capabilities || {},
    sessionState: "ACTIVE",
    sessionAuthorizedAt: Date.now(),
  });
  await broadcastSessionState("ACTIVE");
  return { ok: true, authorized: true };
}

export { SESSION_STATES };
