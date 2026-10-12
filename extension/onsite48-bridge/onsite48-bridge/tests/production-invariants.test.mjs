
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const here = dirname(fileURLToPath(import.meta.url));
const root = join(here, "..");
const read = (rel) => readFileSync(join(root, rel), "utf8");
const background = read("background.js");
const manifest = read("manifest.json");

assert(!background.includes("const command = activeCommand;"), "invalidateActiveCommandLease must not read removed activeCommand");
assert(!background.includes("ensureActive(activeCommand"), "readActiveTabWithEvidence must not call ensureActive on removed activeCommand");
assert(!background.includes("activeCommand ||"), "streamLiveTabState must not guard on removed activeCommand");
assert(!background.includes("const active = activeCommands.get(sessionId);\n  if (active) active.tabId = tab.id;") || background.includes("const sessionId = commandSessionId(command);\n  const active = activeCommands.get(sessionId);"), "runCommand must define sessionId before use");
assert(background.includes('await chrome.storage.local.remove(COMMANDS_IN_FLIGHT_KEY);'), "in-flight transient state must be cleared on startup");
assert(background.includes('await chrome.storage.local.remove(COMMAND_RETRY_STATE_KEY);'), "retry transient state must be cleared on startup");
assert(background.includes('No task workspace is bound. Create or claim a task workspace before running browser commands.'), "selectedTabForCommand must fail closed");
assert(!background.includes("const tab = await activeTab();\n    return {\n      tab,"), "workspace creation must not bind focused tab");
assert(background.includes('Missing required browser capability'), "high-impact capabilities must be explicit");
assert(!background.includes("const { selectedTab, operatorMode } = await settings();"), "legacy focused/selected-tab fallback block must be removed");
assert(manifest.includes('"default_popup": "popup/popup.html"'), "manifest must use the canonical popup");
assert(!manifest.includes('"downloads"') || background.includes("HIGH_IMPACT_COMMAND_CAPABILITIES"), "downloads permission must be paired with explicit capability gating");

console.log("production invariant tests ok");
