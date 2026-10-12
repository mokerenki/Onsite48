/**
 * Protocol tests for onsite48-bridge v3.1 (task-owned tab-group workspace).
 *
 * *_active_tab names are legacy aliases only; the task-owned workspace is the
 * primary execution model. Run with:  node --test tests/protocol.test.mjs
 */
import assert from "node:assert/strict";
import test from "node:test";
import { readFile } from "node:fs/promises";
import {
  COMMAND_TYPES,
  EXTENSION_VERSION,
  PROTOCOL_VERSION,
  READ_COMMAND_TYPES,
  TERMINAL_COMMAND_STATES,
  USER_GESTURE_COMMAND_TYPES,
  advertisedCapabilities,
  buildEvidenceEnvelope,
  commandAuthority,
  commandKey,
  evidenceEnvelope,
  isRetryableFailure,
  lifecycleSnapshot,
  normalizeLegacyCommand,
  sessionCommandKey,
  validateCommand,
  validateVerificationRule,
} from "../protocol.js";

const root = new URL("../", import.meta.url);
const read = async (name) => readFile(new URL(name, root), "utf8");

function command(overrides = {}) {
  return {
    id: "cmd_123",
    lease_token: "lease_abc",
    task_id: "task_456",
    run_id: "run_789",
    command_type: "read_active_tab",
    payload: {},
    ...overrides,
  };
}

/** Names exported by an extension module, parsed textually (modules touch chrome.* at import time). */
function exportedNames(source) {
  const names = new Set();
  for (const m of source.matchAll(/^export\s+(?:async\s+)?(?:function|const|let|class)\s+([A-Za-z0-9_$]+)/gm)) {
    names.add(m[1]);
  }
  return names;
}

/** [{ names: [...], from: "./x.js" }] for every static import in a source file. */
function importsOf(source) {
  const out = [];
  for (const m of source.matchAll(/import\s*\{([^}]*)\}\s*from\s*["']([^"']+)["']/g)) {
    const names = m[1]
      .split(",")
      .map((part) => part.trim().split(/\s+as\s+/)[0].trim())
      .filter(Boolean);
    out.push({ names, from: m[2] });
  }
  return out;
}

test("protocol identity is v3.1 and matches the manifest", async () => {
  assert.equal(PROTOCOL_VERSION, "onsite48-bridge.v3.1");
  const manifest = JSON.parse(await read("manifest.json"));
  assert.equal(manifest.version, EXTENSION_VERSION);
});

test("validateCommand returns the command and accepts only declared command types", () => {
  const valid = validateCommand(command());
  assert.equal(typeof valid, "object");
  assert.equal(valid.id, "cmd_123");
  assert.equal(valid.command_type, "read_active_tab");
  assert.deepEqual(valid.payload, {});
  assert.equal(commandKey(valid), "cmd_123:lease_abc");
  assert.equal(commandAuthority(valid), "read");

  // payload defaults to an object when absent
  assert.deepEqual(validateCommand(command({ payload: undefined })).payload, {});

  assert.throws(() => validateCommand(command({ command_type: "cdp_click_at" })), /Unsupported bridge command/);
  assert.throws(() => validateCommand(command({ payload: ["not-an-object"] })), /payload must be an object/);
  assert.throws(() => validateCommand(command({ id: "" })), /invalid identifier/);
  assert.throws(() => validateCommand(null), /must be an object/);
  assert.deepEqual(advertisedCapabilities(), COMMAND_TYPES);
});

test("verification rules are validated", () => {
  assert.equal(validateVerificationRule({ type: "url_matches" }).valid, true);
  assert.equal(validateVerificationRule({ type: "nope" }).valid, false);
  assert.equal(validateVerificationRule(null).valid, false);
  assert.throws(
    () => validateCommand(command({ payload: { verification: "not-an-array" } })),
    /Verification must be an array/,
  );
  assert.throws(
    () => validateCommand(command({ payload: { verification: [{ type: "bogus" }] } })),
    /Unsupported verification rule/,
  );
});

test("command catalog is consistent", () => {
  assert.equal(new Set(COMMAND_TYPES).size, COMMAND_TYPES.length, "no duplicate command types");
  for (const type of [...READ_COMMAND_TYPES, ...USER_GESTURE_COMMAND_TYPES]) {
    assert.ok(COMMAND_TYPES.includes(type), `${type} must be a declared command type`);
  }
  for (const state of ["completed", "failed", "cancelled", "rejected"]) {
    assert.ok(TERMINAL_COMMAND_STATES.has(state));
  }
  assert.equal(commandAuthority({ command_type: "click" }), "user_gesture");
  assert.equal(commandAuthority({ command_type: "observe" }), "read");
  assert.equal(commandAuthority({ command_type: "wait" }), "user_gesture");
});

test("legacy *_active_tab commands map onto the task-owned tab", () => {
  const mapped = normalizeLegacyCommand(command({ command_type: "click_active_tab" }), 42);
  assert.equal(mapped.command_type, "click");
  assert.equal(mapped.payload.tab_id, 42);
  assert.equal(mapped.payload._legacy_alias, "click_active_tab");
  const explicit = normalizeLegacyCommand(command({ command_type: "read_active_tab", payload: { tab_id: 7 } }), 42);
  assert.equal(explicit.payload.tab_id, 7);
  const untouched = command({ command_type: "navigate" });
  assert.equal(normalizeLegacyCommand(untouched, 42), untouched);
});

test("evidence and lifecycle snapshots preserve task and run correlation", () => {
  const item = command({ command_type: "click_active_tab" });
  const evidence = evidenceEnvelope(
    item,
    { page: { url: "https://example.test/a" } },
    { page: { url: "https://example.test/b" } },
    { status: "verified" },
  );
  assert.equal(evidence.protocol_version, PROTOCOL_VERSION);
  assert.equal(evidence.command_id, "cmd_123");
  assert.equal(evidence.task_id, "task_456");
  assert.equal(evidence.run_id, "run_789");
  assert.equal(evidence.authority, "user_gesture");
  assert.equal(evidence.verification.status, "verified");
  assert.equal(evidence.before.page.url, "https://example.test/a");
  assert.equal(evidence.after.page.url, "https://example.test/b");

  const unverified = evidenceEnvelope(item, null, null);
  assert.equal(unverified.verification.status, "unverified");

  const shaped = evidenceEnvelope({ command_id: "c1", observation_id: "o1", success: true });
  assert.equal(shaped.command_id, "c1");
  assert.equal(shaped.success, true);
  assert.equal(buildEvidenceEnvelope({ commandId: "c2", observationId: "o2", success: 0 }).success, false);

  const lifecycle = lifecycleSnapshot({ phase: "running", command: item, transport: "websocket" });
  assert.equal(lifecycle.protocol_version, PROTOCOL_VERSION);
  assert.equal(lifecycle.phase, "running");
  assert.equal(lifecycle.command_id, "cmd_123");
  assert.equal(lifecycle.task_id, "task_456");
  assert.equal(lifecycle.transport, "websocket");

  const workspace = lifecycleSnapshot({ status: "active", sessionId: "s1", tabGroupId: 9, primaryTabId: 3, tabIds: [3, 4] });
  assert.equal(workspace.status, "active");
  assert.equal(workspace.session_id, "s1");
  assert.deepEqual(workspace.tab_ids, [3, 4]);
});

test("session command key accepts ids or a command", () => {
  assert.equal(sessionCommandKey("s1", "c1"), "s1:c1");
  assert.equal(sessionCommandKey(), "nosession:nocommand");
  assert.equal(sessionCommandKey({ id: "c9", session_id: "s9" }), "s9:c9");
  assert.equal(sessionCommandKey({ id: "c9", payload: { session_id: "sp" } }), "sp:c9");
});

test("retry classifier remains narrow and terminal command failures do not retry", () => {
  assert.equal(isRetryableFailure({ status: 503 }), true);
  assert.equal(isRetryableFailure({ status: 429 }), true);
  assert.equal(isRetryableFailure(new Error("WebSocket transport disconnected")), true);
  assert.equal(isRetryableFailure(new Error("request timed out")), true);
  assert.equal(isRetryableFailure({ code: "network_error" }), true);
  assert.equal(isRetryableFailure({ status: 400, message: "Invalid selector" }), false);
  assert.equal(isRetryableFailure({ status: 409 }), false);
  assert.equal(isRetryableFailure({ status: 404, message: "network not found" }), false);
  assert.equal(isRetryableFailure(null), false);
  assert.equal(isRetryableFailure(new Error("Invalid selector")), false);
});

test("manifest declares the v3.1 task-workspace permission set", async () => {
  const manifest = JSON.parse(await read("manifest.json"));
  assert.equal(manifest.manifest_version, 3);
  for (const permission of ["storage", "tabs", "tabGroups", "scripting", "alarms", "downloads", "webNavigation"]) {
    assert.ok(manifest.permissions.includes(permission), `missing permission ${permission}`);
  }
  assert.equal(manifest.permissions.includes("debugger"), false, "no debugger permission in v3.1");
  assert.equal(manifest.permissions.includes("webRequest"), false);
  assert.deepEqual(manifest.host_permissions, ["<all_urls>"]);
  assert.equal(manifest.background.service_worker, "background.js");
  assert.equal(manifest.background.type, "module");
  assert.equal(manifest.action.default_popup, "popup/popup.html");
});

test("worker is receipt-aware and has no DevTools-protocol remnants", async () => {
  const background = await read("background.js");
  assert.match(background, /COMMAND_RECEIPTS_KEY/);
  assert.match(background, /recordTerminalReceipt/);
  assert.match(background, /processCommand\(command, transport, true\)/);
  assert.match(background, /captureTabScoped/);
  assert.match(background, /capture_unavailable/);
  assert.doesNotMatch(background, /CdpSession|cdp_click_at|cdp_type_at|inspect_ax_tree/);
});

test("worker uses validateCommand's return value as the command", async () => {
  const background = await read("background.js");
  assert.match(background, /command = validateCommand\(command\)/);
  // validateCommand must therefore return an object, not a boolean.
  assert.equal(typeof validateCommand(command()), "object");
});

test("overlay implements an actual resume action instead of a relabelled takeover action", async () => {
  const overlay = await read("operator-overlay.js");
  assert.match(overlay, /operatorResume/);
  assert.match(overlay, /takeover\.dataset\.action = paused \? "resume" : "takeover"/);
});

test("popup shares protocol metadata, stays within the backend capability cap, and does not fake readiness", async () => {
  const popup = await read("popup/popup.js");
  assert.match(popup, /import \{ EXTENSION_NAME, EXTENSION_VERSION, PROTOCOL_VERSION, advertisedCapabilities \}/);
  assert.match(popup, /phase: "transport_error"/);
  assert.doesNotMatch(popup, /Onsite48 Bridge Operator v0\.9\.0/);

  // Backend PairStart.requested_capabilities is capped at 20 entries; the popup must send a short coarse list,
  // never the full command catalog.
  const match = popup.match(/requested_capabilities:\s*\[([^\]]*)\]/);
  assert.ok(match, "popup must send an explicit coarse requested_capabilities list");
  const requested = [...match[1].matchAll(/"([^"]+)"/g)].map((m) => m[1]);
  assert.ok(requested.length > 0 && requested.length <= 20, `requested_capabilities has ${requested.length} entries`);
  assert.ok(COMMAND_TYPES.length > 20, "sanity: the full catalog would exceed the backend cap");
});

test("every named import between extension modules resolves to a real export", async () => {
  const modules = ["background.js", "popup/popup.js", "workspace.js", "session-state.js", "operator-overlay.js"];
  const exportsByFile = {};
  for (const file of ["protocol.js", "workspace.js", "session-state.js", "actions.js", "observation.js"]) {
    exportsByFile[file] = exportedNames(await read(file));
  }
  let checked = 0;
  for (const file of modules) {
    const source = await read(file);
    for (const { names, from } of importsOf(source)) {
      if (!from.startsWith("./")) continue;
      const target = from.slice(2);
      const available = exportsByFile[target];
      assert.ok(available, `${file} imports from unknown module ${from}`);
      for (const name of names) {
        checked += 1;
        assert.ok(available.has(name), `${file} imports '${name}' which ${from} does not export`);
      }
    }
  }
  assert.ok(checked > 10, "expected to verify a meaningful number of imports");
});

test("obsolete DevTools helper is removed from the extension package", async () => {
  await assert.rejects(readFile(new URL("cdp_session.js", root), "utf8"));
});
