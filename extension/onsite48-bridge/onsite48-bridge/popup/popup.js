// Onsite48 Browser popup controller
import { EXTENSION_NAME, EXTENSION_VERSION, PROTOCOL_VERSION, advertisedCapabilities, legacyPairCapabilities } from "../protocol.js";

const $ = (id) => document.getElementById(id);

function normalizeBase(value) { 
  return value.trim().replace(/\/$/, ""); 
}

// ─── INPUT VALIDATION ──────────────────────────────────────────────

function validateDeviceId(value) {
  // Device ID: alphanumeric, underscore, hyphen, 12-128 characters
  const pattern = /^[a-zA-Z0-9_-]{12,128}$/;
  if (!pattern.test(value)) {
    throw new Error("Device ID must be 12-128 characters (letters, numbers, underscore, hyphen)");
  }
  return value.trim();
}

function validatePairingCode(value) {
  // Pairing code: alphanumeric, underscore, hyphen, 12-256 characters
  const pattern = /^[a-zA-Z0-9_-]{12,256}$/;
  if (!pattern.test(value)) {
    throw new Error("Pairing code must be 12-256 characters (letters, numbers, underscore, hyphen)");
  }
  return value.trim();
}

function validateApiBase(value) {
  // API base: must be a valid URL, allow localhost for development
  const trimmed = value.trim();
  try {
    const url = new URL(trimmed);
    // Allow http for localhost, https for everything else
    if (url.protocol === 'http:' && url.hostname !== 'localhost' && url.hostname !== '127.0.0.1') {
      throw new Error("HTTP is only allowed for localhost. Use HTTPS for production.");
    }
    if (url.protocol !== 'http:' && url.protocol !== 'https:') {
      throw new Error("Must use http:// or https://");
    }
    return trimmed.replace(/\/$/, "");
  } catch {
    throw new Error("Enter a valid URL (e.g., http://localhost or https://onsite48.com)");
  }
}

function formatTime(value) { 
  if (!value) return "not yet"; 
  const date = new Date(value); 
  return Number.isNaN(date.valueOf()) ? "recently" : date.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }); 
}

function humanCommand(value) { 
  return String(value || "request").replace(/^bridge_/, "").replace(/_active_tab/g, "").replaceAll("_", " "); 
}

function setPairStatus(message, kind = "") {
  const node = $("pairStatus");
  node.hidden = false;
  node.textContent = message;
  node.className = `notice ${kind}`;
}

function setCommandStatus(title, detail, kind = "") {
  const node = $("commandStatus");
  node.className = `status-row ${kind}`;
  node.querySelector(".status-icon").textContent = kind === "error" ? "!" : kind === "paused" ? "Ⅱ" : "S";
  node.querySelector(".status-title").textContent = title;
  node.querySelector(".status-detail").textContent = detail;
}

async function send(type, payload = {}) {
  const response = await chrome.runtime.sendMessage({ type, ...payload });
  if (response?.error) throw new Error(response.error);
  return response;
}

function isPublicTab(tab) {
  try {
    const parsed = new URL(tab?.url || "");
    return Boolean(tab?.id) && (parsed.protocol === "http:" || parsed.protocol === "https:");
  } catch {
    return false;
  }
}

function optionalOriginForTab(tab) {
  const parsed = new URL(tab.url);
  return `${parsed.protocol}//${parsed.hostname}/*`;
}

async function requestSelectedTabPermissionFromPopup() {
  // This function is called directly from the popup button click. Do not move
  // chrome.permissions.request into the background worker: Chrome then rejects
  // it with "This function must be called during a user gesture".
  const [tab] = await chrome.tabs.query({ active: true, lastFocusedWindow: true });
  if (!isPublicTab(tab)) {
    throw new Error("Focus a normal http or https website tab, reopen Onsite48 Bridge, then grant access to this site.");
  }
  const origins = [optionalOriginForTab(tab)];
  const alreadyGranted = await chrome.permissions.contains({ origins });
  const granted = alreadyGranted || await chrome.permissions.request({ origins });
  if (!granted) {
    throw new Error(`Chrome did not grant Onsite48 Bridge access to ${new URL(tab.url).hostname}. Grant access again and approve the site prompt.`);
  }
  return tab;
}

function renderTab(tab, state = {}, operatorMode = "active_tab") {
  const available = Boolean(tab?.hostname && Number.isInteger(tab?.tabId));
  $("tabUnavailable").hidden = available;
  if (!available) {
    const selectionState = state.selectionState || "required";
    if (operatorMode === "active_tab" && selectionState !== "permission_required") {
      $("tabTitle").textContent = "Task workspace active";
      $("tabHost").textContent = "Focus any public website. Onsite48 follows the page you are viewing without a tab lock.";
      $("tabFreshness").textContent = "One-time site permission may still be required the first time on a new host.";
      return;
    }
    const message = selectionState === "closed"
      ? "The previously observed page was closed. Focus a public page again — task workspace owns its tabs."
      : selectionState === "permission_required"
        ? "This site needs one-time permission. Stay on the page and approve the Chrome prompt."
      : "Focus a public website. Onsite48 will use the active tab by default.";
    $("tabTitle").textContent = selectionState === "closed" ? "Tab closed" : selectionState === "permission_required" ? "Permission needed" : "Ready for active tab";
    $("tabHost").textContent = message;
    $("tabFreshness").textContent = operatorMode === "active_tab" ? "Operator: task workspace" : "Operator: task workspace";
    return;
  }
  $("tabTitle").textContent = tab.title || "Untitled page";
  $("tabHost").textContent = tab.hostname;
  $("tabFreshness").textContent = operatorMode === "active_tab"
    ? `Bound ${formatTime(tab.selectedAt || tab.lastValidatedAt)} · task workspace owns its tabs`
    : `Locked ${formatTime(tab.selectedAt || tab.lastValidatedAt)}. Focus another tab and Lock to change.`;
}

function renderMode(operatorMode = "active_tab") {
  const modeTitle = $("modeTitle");
  const modeDetail = $("modeDetail");
  if (!modeTitle || !modeDetail) return;
  if (operatorMode === "selected_only") {
    modeTitle.textContent = "Task workspace";
    modeDetail.textContent = " Onsite48 operates in dedicated task tab groups. Your other tabs stay untouched.";
    $("sync").textContent = "Lock this tab";
  } else {
    modeTitle.textContent = "Task workspace";
    modeDetail.textContent = " Commands run inside the task-owned browser workspace, not whatever tab you happen to be viewing.";
    $("sync").textContent = "Grant site access";
  }
}

function renderConnection(status) {
  // Never show Ready/Connected when status is unknown or failed.
  const known = status && typeof status === "object";
  const paired = Boolean(known && status.paired && (status.device || status.bridgeToken));
  const failed = known && (status.error || status.transport === "failed" || status.phase === "error");
  $("pairing").hidden = paired;
  $("operator").hidden = !paired;
  const pill = $("connectionPill");
  if (!known || failed) {
    pill.className = "connection offline";
    pill.querySelector("span").textContent = failed ? "Connection failed" : "Status unknown";
    if (!paired) return;
  } else {
    pill.className = `connection ${paired ? "" : "offline"}`;
    const email = status.device?.user_email || status.user_email || status.device?.display_name;
    pill.querySelector("span").textContent = paired
      ? (email ? `Connected as ${email}` : "Connected · Ready")
      : "Not connected";
  }
  if (!paired) return;

  const state = status.operatorState || {};
  const lastSync = Date.parse(state.lastContextSyncAt || state.lastPollAt || "") || 0;
  const staleContext = lastSync > 0 && Date.now() - lastSync > 120000;
  const reconnectButton = $("resumeConnection");
  if (reconnectButton) reconnectButton.hidden = state.transport === "websocket" && !staleContext && state.phase !== "transport_error";
  const operatorMode = status.operatorMode || "active_tab";
  renderMode(operatorMode);
  renderTab(status.selectedTab || state.selectedTab, state, operatorMode);
  const live = state.liveTabState;
  if (live?.observedAt && live.tabId) {
    const fields = Array.isArray(live.changed_fields) && live.changed_fields.length ? live.changed_fields.join(", ") : "no visible change";
    $("tabFreshness").textContent = `${$("tabFreshness").textContent} · live state ${formatTime(live.observedAt)} · ${fields}`;
    if (live.challenge?.detected) setCommandStatus("Manual browser step needed", live.challenge.reason || "Complete the sign-in or verification step in the visible tab, then resume the operator.", "paused");
  }
  const deviceName = status.device?.display_name || EXTENSION_NAME;
  const transport = state.transport === "websocket" ? "live connection" : state.transport === "poll_fallback" ? "polling recovery" : state.transport || "connection pending";
  const reconnect = status.reconnection?.isScheduled ? ` · reconnecting (${status.reconnection.attempts || 0})` : "";
  const retries = Array.isArray(status.commandRetry) && status.commandRetry.length ? ` · ${status.commandRetry.length} command retry pending` : "";
  $("deviceText").textContent = `${deviceName} ${EXTENSION_VERSION} · ${transport}${reconnect}${retries} · Onsite48: ${status.apiBase} · last sync ${formatTime(state.lastContextSyncAt || state.lastPollAt)}`;
  $("pause").textContent = state.paused ? "Resume operator" : "Pause operator";

  if (state.paused) {
    const title = state.pauseSource === "takeover" ? "You control this local tab" : "Operator paused";
    const fallback = state.pauseSource === "takeover" ? "Complete the page yourself. Select Resume operator only when you want Onsite48 to process the next approved browser action." : "Resume when you are ready for Onsite48 to process approved browser work.";
    setCommandStatus(title, state.pauseReason || fallback, "paused");
  } else if (state.phase === "permission_required") {
    const origin = state.permissionOrigin || "the selected website";
    setCommandStatus("Site permission needed", `${state.permissionReason || `Grant access to ${origin}.`} The approved command is still pending; after granting access, resume the paused task in Onsite48.`, "paused");
  } else if (state.phase === "retrying") {
    setCommandStatus("Recovering browser action", state.lastError || "A transport failure is being retried with the same command lease.", "paused");
  } else if (state.phase === "running") {
    const commandId = state.lifecycle?.command_id ? ` Command ${String(state.lifecycle.command_id).slice(0, 12)}.` : "";
    setCommandStatus("Synth is working locally", `Approved action: ${humanCommand(state.lastCommand?.type)}.${commandId} Use the in-page controls to take over or stop.`, "");
  } else if (state.phase === "error") {
    setCommandStatus("Last action needs attention", state.lastError || "The browser command could not be completed.", "error");
  } else if (state.lastCommand?.status === "completed") {
    setCommandStatus("Last action completed", `${humanCommand(state.lastCommand.type)} completed and its result was returned to Onsite48.`, "");
  } else {
    setCommandStatus("Operator ready", "No approved browser action is waiting.", "");
  }
}

async function refresh() {
  try {
    const status = await send("status");
    renderConnection(status);
    // While the operator is running, approved browser actions must
    // execute without a manual "Check requests" click. The popup
    // refreshes every 3s, so a running operator polls on the same
    // cadence and executes within that window.
    const operatorState = status.operatorState || {};
    if (operatorState.phase === "running") {
      send("poll").catch(() => undefined);
    }
  } catch (error) {
    const stored = await chrome.storage.local.get(["bridgeToken", "enabled"]);
    if (stored.enabled && stored.bridgeToken) {
      const local = await chrome.storage.local.get(["apiBase", "device", "operatorState", "selectedTab", "operatorMode"]);
      renderConnection({
        paired: true,
        apiBase: local.apiBase || "",
        device: local.device || null,
        operatorState: { ...(local.operatorState || {}), phase: "transport_error", lastError: error instanceof Error ? error.message : "Status refresh failed." },
        selectedTab: local.selectedTab || null,
        operatorMode: local.operatorMode || "active_tab",
        reconnection: { isScheduled: false, attempts: 0 },
        commandRetry: [],
      });
      setCommandStatus("Connection status unavailable", error instanceof Error ? error.message : "The extension is paired, but its current transport state could not be read.", "error");
    } else {
      throw error;
    }
  }
}

// ─── DOM Ready ────────────────────────────────────────────────────

document.addEventListener("DOMContentLoaded", () => {
  // Show the saved address only if it came from a real pairing (a token exists). The previous
  // popup saved the address before the request succeeded, so a bare apiBase can be a wrong guess.
  chrome.storage.local.get(["apiBase", "bridgeToken"]).then((saved) => {
    if (saved?.apiBase && saved?.bridgeToken && $("apiBase")) $("apiBase").value = saved.apiBase;
  }).catch(() => undefined);

  // ─── Pair Button ──────────────────────────────────────────────────
  $("pair").addEventListener("click", async () => {
    const button = $("pair");
    button.disabled = true;
    $("pairStatus").hidden = true;
    setPairStatus("Starting device pairing…");
    let attemptedBase = "";
    try {
      const stored = await chrome.storage.local.get(["apiBase"]);
      const typedBase = ($("apiBase")?.value || "").trim();
      const apiBase = validateApiBase(typedBase || stored.apiBase || "http://localhost");
      attemptedBase = apiBase;
      if ($("apiBase")) $("apiBase").value = apiBase;

      const usePastedCode = Boolean(($("deviceId")?.value || "").trim() && ($("pairingCode")?.value || "").trim());
      let startRes = null;
      let startData = {};
      if (!usePastedCode) {
        startRes = await fetch(`${apiBase}/api/bridge/device/pair/start`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ display_name: "Chrome · Onsite48 Browser" }),
        });
        startData = await startRes.json().catch(() => ({}));
      }
      if (usePastedCode || !startRes.ok) {
        // Fallback: legacy paste pairing code (Advanced)
        const pairingCode = ($("pairingCode")?.value || "").trim();
        const deviceId = ($("deviceId")?.value || "").trim() || crypto.randomUUID().replace(/-/g, "");
        if (!pairingCode) {
          const detail = typeof startData.detail === "string" ? startData.detail : "Device pair unavailable.";
          throw new Error(`${detail} Check the Onsite48 address under Advanced (e.g. http://localhost:8000), or enter a pairing code from Settings → My Browser.`);
        }
        const response = await fetch(`${apiBase}/api/bridge/extension/pair`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            device_id: deviceId,
            pairing_code: pairingCode,
            extension_name: "Onsite48 Browser",
            requested_capabilities: legacyPairCapabilities(),
          }),
        });
        const data = await response.json().catch(() => ({}));
        if (!response.ok || !data.bridge_token) {
          throw new Error(typeof data.detail === "string" ? data.detail : "Pairing failed.");
        }
        await chrome.storage.local.set({
          apiBase,
          bridgeToken: data.bridge_token,
          deviceCredential: data.device_credential || data.bridge_token,
          device: data.device,
          enabled: true,
          operatorMode: "task_workspace",
          operatorState: { phase: "ready", pairedAt: new Date().toISOString(), paused: false, transport: "poll_fallback" },
        });
      } else {
        const deviceId = startData.device_id || startData.device?.device_id;
        const pollToken = startData.poll_token || startData.pairing_code;
        const userCode = startData.user_code || "";
        if (!deviceId || !pollToken) {
          throw new Error("Pairing start did not return device_id and poll_token. Update the Onsite48 backend.");
        }
        const verifyPath = startData.verification_uri || "/settings/connectors/my-browser";
        // The pairing window is ~90s. Count it down in the popup so the
        // user never has to guess how long the code stays valid.
        const pairDeadline = Date.now() + 90_000;
        const remainingSeconds = () => Math.max(0, Math.ceil((pairDeadline - Date.now()) / 1000));
        setPairStatus(
          userCode
            ? `Code ${userCode} — confirm in Onsite48 within ${remainingSeconds()}s (Settings → My Browser).`
            : `Confirm pairing in Onsite48 within ${remainingSeconds()}s (Settings → My Browser)…`,
        );
        // Best-effort: open product confirm page (same browser profile)
        try {
          const front = apiBase.replace(/:\\d+$/, "") || apiBase;
          // Prefer frontend on :3000 when API is :8000
          let productBase = apiBase;
          try {
            const u = new URL(apiBase);
            if (u.port === "8000") {
              u.port = "3000";
              productBase = u.toString().replace(/\/$/, "");
            }
          } catch (_) { /* keep apiBase */ }
          const confirmUrl = `${productBase}${verifyPath}${verifyPath.includes("?") ? "&" : "?"}code=${encodeURIComponent(userCode)}`;
          // The confirm page must open focused: a background tab makes
          // the user miss the 90s confirmation window entirely.
          chrome.tabs.create({ url: confirmUrl, active: true }).then((tab) => {
            if (tab?.windowId != null) {
              chrome.windows.update(tab.windowId, { focused: true }).catch(() => undefined);
            }
          }).catch(() => undefined);
        } catch (_) { /* optional */ }

        let data = null;
        const maxAttempts = 45; // ~90s
        for (let i = 0; i < maxAttempts; i++) {
          await new Promise((r) => setTimeout(r, 2000));
          let completeRes;
          try {
            completeRes = await fetch(`${apiBase}/api/bridge/device/pair/complete`, {
              method: "POST",
              headers: { "Content-Type": "application/json" },
              body: JSON.stringify({ device_id: deviceId, poll_token: pollToken }),
            });
          } catch (netErr) {
            setPairStatus(`Waiting for API at ${apiBase}… (${netErr instanceof Error ? netErr.message : "network"})`);
            continue;
          }
          data = await completeRes.json().catch(() => ({}));
          if (completeRes.ok && (data.device_credential || data.bridge_token)) {
            break;
          }
          if (data.status === "awaiting_confirmation" || completeRes.status === 202) {
            const code = data.user_code || userCode;
            setPairStatus(
              code
                ? `Waiting for confirm of code ${code} in Onsite48 (Settings → My Browser)… ${remainingSeconds()}s left`
                : `Waiting for confirmation in Onsite48… ${remainingSeconds()}s left`,
            );
            continue;
          }
          // 404: confirm not done yet or wrong process memory — keep polling briefly
          if (completeRes.status === 404 || completeRes.status === 400) {
            setPairStatus(
              userCode
                ? `Still waiting — confirm code ${userCode} in Onsite48 Settings → My Browser. ${remainingSeconds()}s left`
                : `Still waiting for confirmation in Onsite48… ${remainingSeconds()}s left`,
            );
            continue;
          }
          if (completeRes.status === 410) {
            throw new Error(typeof data.detail === "string" ? data.detail : "Pairing expired. Click Connect again.");
          }
          if (completeRes.status === 401) {
            // Should not happen on the fixed complete endpoint; surface clearly
            throw new Error("Pair complete rejected authentication unexpectedly. Update the Onsite48 backend pair/complete endpoint.");
          }
          if (!completeRes.ok) {
            throw new Error(typeof data.detail === "string" ? data.detail : `Pair complete failed (${completeRes.status}).`);
          }
        }
        if (!data || !(data.device_credential || data.bridge_token)) {
          throw new Error(
            userCode
              ? `Pairing timed out. In Onsite48 open Settings → My Browser, confirm code ${userCode}, then click Connect again.`
              : "Pairing timed out. Confirm the device in Onsite48 Settings → My Browser, then click Connect again.",
          );
        }
        await chrome.storage.local.set({
          apiBase,
          bridgeToken: data.bridge_token || data.device_credential,
          deviceCredential: data.device_credential || data.bridge_token,
          device: { ...(data.device || {}), user_email: data.user_email, device_id: deviceId },
          enabled: true,
          operatorMode: "task_workspace",
          operatorState: { phase: "ready", pairedAt: new Date().toISOString(), paused: false, transport: "poll_fallback" },
        });
      }
      await send("connectTransport").catch(() => undefined);
      await refresh();
      setCommandStatus("Browser connected", "My Browser is ready. Authorize a task in Onsite48 to open a task-named tab group.");
      setPairStatus("Connected.", "ok");
    } catch (error) {
      const unreachable = error instanceof TypeError && /failed to fetch|networkerror|load failed/i.test(error.message);
      setPairStatus(
        unreachable
          ? `Cannot reach Onsite48 at ${attemptedBase || "the address above"}. Open that address in your browser to check it loads. With Docker Compose the address is http://localhost (port 80).`
          : (error instanceof Error ? error.message : "Pairing failed."),
        "error",
      );
    } finally {
      button.disabled = false;
    }
  });

$("check").addEventListener("click", async () => {
    const button = $("check");
    button.disabled = true;
    setCommandStatus("Checking now", "Forcing an immediate check for approved browser work from Onsite48.");
    try {
      const result = await send("poll");
      if (result.paused) setCommandStatus("Operator paused", "Resume the operator before checking a new approved request.", "paused");
      else if (result.state?.phase === "permission_required") setCommandStatus("Site permission needed", "Focus the intended page, grant access to this site, then resume the paused task in Onsite48.", "paused");
      else if (result.count) setCommandStatus("Requests completed", `${result.count} browser request${result.count === 1 ? "" : "s"} completed. Onsite48 is processing the result.`);
      else setCommandStatus("No request waiting", "Approve a browser step in Onsite48, then check again.");
      await refresh();
    } catch (error) {
      setCommandStatus("Unable to check requests", error instanceof Error ? error.message : "The request check failed.", "error");
    } finally {
      button.disabled = false;
    }
  });

  // ─── Pause/Resume Button ─────────────────────────────────────────
  $("pause").addEventListener("click", async () => {
    const button = $("pause");
    button.disabled = true;
    try {
      const state = await send("status");
      await send(state.operatorState?.paused ? "resume" : "pause");
      await refresh();
    } catch (error) {
      setCommandStatus("Unable to change operator state", error instanceof Error ? error.message : "Please try again.", "error");
    } finally {
      button.disabled = false;
    }
  });

  // ─── Mode Toggle ─────────────────────────────────────────────────
  $("toggleMode")?.addEventListener("click", async () => {
    try {
      const status = await send("status");
      const next = status.operatorMode === "selected_only" ? "active_tab" : "selected_only";
      renderConnection(await send("setOperatorMode", { mode: next }));
    } catch (error) {
      setCommandStatus("Could not switch mode", error instanceof Error ? error.message : "Try again.", "error");
    }
  });

  // ─── Sync Tab Button ─────────────────────────────────────────────
  $("sync").addEventListener("click", async () => {
    $("sync").disabled = true;
    try {
      const tab = await requestSelectedTabPermissionFromPopup();
      await send("sync", { tabId: tab.id });
      await refresh();
      setCommandStatus("Site access granted", "Active-tab mode can now operate on this site and will follow the page you are viewing. Locked selection remains optional.");
    } catch (error) {
      setCommandStatus("Unable to select this tab", error instanceof Error ? error.message : "The focused tab could not be selected.", "error");
    } finally {
      $("sync").disabled = false;
    }
  });

  // ─── Optional Controls ───────────────────────────────────────────
  $("openSynth")?.addEventListener("click", () => void send("openSynth"));
  $("openSynthFooter")?.addEventListener("click", () => void send("openSynth"));
  
  $("refreshConnection")?.addEventListener("click", async () => {
    try {
      await refresh();
    } catch (error) {
      setCommandStatus("Connection refresh failed", error instanceof Error ? error.message : "Please try again.", "error");
    }
  });

  $("resumeConnection")?.addEventListener("click", async () => {
    const button = $("resumeConnection");
    button.disabled = true;
    setCommandStatus("Reconnecting Bridge", "Restoring the paired browser connection and checking the paused task.", "paused");
    try {
      await send("connectTransport");
      await send("poll");
      await refresh();
    } catch (error) {
      setCommandStatus("Reconnect needs attention", error instanceof Error ? error.message : "The paired browser is still unavailable.", "error");
    } finally {
      button.disabled = false;
    }
  });
  
  $("disconnect")?.addEventListener("click", async () => {
    if (!confirm("Disconnect Onsite48 Bridge from this browser? Existing site rules remain in Onsite48, but the agent cannot send browser commands until you pair this browser again.")) return;
    await send("disconnectTransport").catch(() => undefined);
    await chrome.storage.local.remove(["bridgeToken", "device", "enabled", "operatorState", "selectedTab", "operatorMode"]);
    await refresh();
  });

  $("stageUpload")?.addEventListener("click", async () => {
    const input = $("uploadFile");
    const file = input?.files?.[0];
    if (!file) {
      setCommandStatus("Choose a file first", "Select a local file, then stage it for the next approved upload.", "error");
      return;
    }
    if (file.size > 6 * 1024 * 1024) {
      setCommandStatus("File is too large", "Onsite48 Bridge stages files up to 6 MB to keep the browser boundary bounded.", "error");
      return;
    }
    const reader = new FileReader();
    reader.onload = async () => {
      try {
        const result = await send("stageUpload", { file: { name: file.name, type: file.type, size: file.size, data_url: reader.result } });
        $("uploadState").textContent = `${result.name} staged`;
        setCommandStatus("Upload staged", "The file stays in this extension until an explicitly approved upload command consumes it.");
      } catch (error) {
        setCommandStatus("Could not stage file", error instanceof Error ? error.message : "Try again.", "error");
      }
    };
    reader.onerror = () => setCommandStatus("Could not read file", "The browser could not stage this file.", "error");
    reader.readAsDataURL(file);
  });

  // ─── Initial Load ────────────────────────────────────────────────
  void refresh();
  setInterval(() => { void refresh(); }, 3000);
});