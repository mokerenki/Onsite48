# Discovery Phase 4 — Sandbox browser operator and deliverable quality

## Verified

### Browser stack (sandbox)
- `services/agent_computer.py` (642 LOC): Playwright-based `AgentComputerManager` with isolated per-thread contexts, public-URL guard, CAPTCHA/challenge markers → user takeover, click/type/scroll/inspect/navigate/screenshot, coordinate click, post-action screenshot diff.
- Observation is **selector/index based**, not stable observation_id + element_ref + fingerprint + revision.
- No shared JSON Schema protocol for browser actions.
- Duplicate/legacy tools still present:
  - `tools/browser_tool.py` (145)
  - `tools/browser_control_tool.py` (243)
  - `tools/computer_tools.py` (225) — computer_navigate, computer_back, computer_scroll, computer_inspect
  - `tools/browser_bridge_tools.py` (481) — extension bridge
- Runtime tools.py wraps registry tools; computer tools already classified as untrusted/read.

### Extension (User-Chrome)
- `extension/onsite48-bridge/background.js` (2747 LOC).
- `capture_active_tab` and `snapshot_active_tab` call `chrome.tabs.captureVisibleTab(tab.windowId, …)` after selecting a tab, but **do not verify** `tab.active && tab.windowId === workspace.windowId` after activation. A background task tab can yield a capture of the user's focused tab if activation fails or race occurs.
- Protocol version constant exists; full protocol-v4 conformance of extension deferred per phase (stretch).

### Deliverables
- Skills exist: docx-report, pptx-deck, xlsx-model, pdf-output, static-website-build.
- No `render_preview` tool converting docx/pptx/xlsx/pdf/html → PNG pages.
- Verifier (Phase 3) does not yet receive preview images / lint results.
- LibreOffice / pdftoppm availability: NOT verified in this sandbox (will mark NOT RUN where missing).

### Policy
- `runtime/policy.py` already has TAKEOVER / APPROVAL / DENY / ALLOW.
- Login/password, CAPTCHA, payment, send/publish/delete → need explicit browser action classification wired to policy.

### Evals
- Existing browser-ish evals: br-login-wall-001, br-destructive-001, br-form-001, br-pages-001, br-misc-005..012.
- Phase gate requires **14 new** browser + **14 new** deliverable dev evals on top of Phase 0's.
- No protocol drift test yet.
- No screenshot-scope E2E for extension capture isolation.

### Differs from phase prompt
1. agent_computer already has solid Playwright base — extend to protocol v4 rather than rewrite from zero.
2. Some br-* evals exist; still need 14 new dedicated protocol/stale/iframe/shadow cases.
3. Extension is large; only bug-fix capture scope in this phase (as specified).
4. No render_preview path yet.

## Plan
1. Define `protocol/browser_v4.schema.json` + drift test (backend + extension surface).
2. Implement observe/action protocol on `agent_computer.py` and expose a single `browser_*` toolset via runtime tools; deprecate duplicate computer/browser tools behind the same surface.
3. Policy boundaries for login/password, CAPTCHA, payment, destructive, unknown downloads.
4. Fix extension capture: activate target tab in workspace window, verify active+windowId, else `capture_unavailable`.
5. Chromium E2E test for screenshot scope (or structural equivalent if headless Chromium unavailable).
6. `render_preview` tool + deliverable lint; wire finish verifier to accept previews.
7. Skills for layout/typography only if they improve evals (minimal).
8. 14 browser + 14 deliverable dev evals; A/B harness for render-inspect-fix.
9. PHASE_4_REPORT.md with gates, NOT RUN list, line counts.

## Risks / blockers
- Full live browser evals need Playwright + model credentials.
- LibreOffice headless may be absent in CI sandbox → render_preview will soft-fail with clear error and unit tests with fixtures.
- Extension E2E on real Chromium may need playwright/chromium install.
