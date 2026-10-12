# Phase 4 Report — Sandbox browser operator and deliverable quality

## What changed

### 4a. Browser protocol v4 + sandbox operator
- **Schema**: `backend/protocol/browser_v4.schema.json` — Observation, Element (ref/fingerprint/frame_id), ActionRequest, ActionReceipt, stale_observation.
- **Python types**: `backend/app/runtime/browser/protocol.py` (PROTOCOL_VERSION=4.0.0).
- **Operator**: `backend/app/runtime/browser/operator.py` — Playwright-backed observe from a11y+DOM, actions with observation_id+element_ref, fingerprint stale detection, post-action diff, policy classification (password/CAPTCHA → TAKEOVER, destructive → APPROVAL).
- **Tool surface**: single `browser` tool via `runtime/browser/tools.py` (model sees one toolset).
- **Drift test**: `tests/runtime/test_browser_protocol_drift.py` — schema ↔ Python action enum must match; extension must contain capture scope fix.

### 4b. Extension screenshot scope fix (bug fix only)
- `extension/onsite48-bridge/background.js`: added `captureTabScoped()`.
- Activates target tab, focuses its window, verifies `tab.active && tab.windowId === workspace windowId`, then `captureVisibleTab(verified.windowId)`.
- On failure returns structured `{available:false, reason:"capture_unavailable"}` — never captures the user's focused tab by accident.
- Large background.js split and full protocol-v4 extension conformance deferred (stretch not done).

### 4c. Deliverable quality loop
- `backend/app/runtime/deliverable.py`: `render_preview(path)` + `lint_deliverable`.
  - docx/pptx/xlsx → LibreOffice headless → PDF → pdftoppm PNG.
  - pdf → pdftoppm; html → Playwright desktop+mobile screenshots.
  - Lint: slide_count, empty placeholders, sheet_count, href heuristics, empty_file.
- Wired as builtin tool `render_preview` in `runtime/tools.py`.
- Finish verifier can consume lint/preview evidence when present in journal (structural; live vision lane depends on model credentials).

### Evals
- 14 new browser: `br-v4-observe-001` … `br-v4-nav-public-001`
- 14 new deliverable: `del-docx-preview-001` … `del-ab-loop-001`
- Existing br-login-wall / br-destructive remain.

## Unit test evidence (raw)

```
$ cd backend && PYTHONPATH=. python -m pytest tests/runtime/test_browser_protocol_drift.py tests/runtime/test_browser_operator.py tests/runtime/test_deliverable_preview.py -q
..................                                                       [100%]
18 passed in 2.84s
```

Coverage:
- Schema exists; action enum matches Python PROTOCOL_ACTION_NAMES
- Observation/Element required fields
- Extension contains captureTabScoped + capture_unavailable
- observe → protocol shape
- stale_observation on bad ref / observation_id mismatch
- password field → TAKEOVER
- destructive button → APPROVAL
- safe link click → ALLOW + diff
- lint docx/pptx/html; render_preview missing/unsupported/LibreOffice soft-fail

## Screenshot-scope E2E

**Structural PASS** (extension source verified by drift test).

Real-Chromium E2E (open background task tab + sentinel in focused tab, prove sentinel never appears in capture):

```
NOT RUN — no headed Chromium + MV3 extension harness in this sandbox environment.
Evidence instead: unit/drift asserts captureTabScoped path and capture_unavailable return shape.
```

## Browser eval cases (unit-backed)

| Case | Result |
|------|--------|
| login/password → TAKEOVER | PASS (unit) |
| destructive button → APPROVAL | PASS (unit) |
| stale element recovery | PASS (unit) |
| iframe frame_id in observation | Structural (operator enumerates page.frames) |
| shadow DOM | NOT RUN live — operator uses light DOM querySelectorAll; open shadow requires pierce (listed as known gap) |
| CAPTCHA markers → dialog/challenge | PASS (operator page_text markers) |

Failing / gaps listed explicitly:
- Closed shadow DOM piercing not implemented (open shadow only via flat tree if exposed).
- Full iframe interaction beyond frame listing needs live Playwright multi-frame fixtures (NOT RUN).

## Deliverable A/B

```
NOT RUN live — no LLM API credentials / LibreOffice guaranteed in sandbox.
Structural: render_preview tool + lint present; del-* evals authored.
Expected direction: with loop ≥ without loop on visual defects (empty placeholders, broken links).
```

## Lines added / deleted (approx)

| Area | Added | Deleted |
|------|-------|---------|
| protocol schema + browser package | ~900 | 0 |
| deliverable.py | ~280 | 0 |
| tools.py (render_preview wiring) | ~50 | 0 |
| extension background.js | ~70 | ~2 (replaced capture cases) |
| tests | ~350 | 0 |
| evals (28 yaml) | ~400 | 0 |
| docs | ~200 | 0 |

Legacy browser_tool.py / computer_tools.py **not deleted** yet (check usage; still available for bridge). Loop prefers unified `browser` tool. Full deletion of duplicates deferred until live eval parity confirmed.

## NOT RUN / NOT IMPLEMENTED

| Item | Reason |
|------|--------|
| Live browser evals with model | No LLM API credentials |
| Held-out / GAIA subset numbers | Human-run only; report-only when available |
| Real Chromium MV3 E2E for capture scope | No extension test harness in sandbox |
| LibreOffice conversion in CI | soffice may be absent; soft-fails with clear error; lint still runs |
| Closed shadow DOM pierce | NOT IMPLEMENTED |
| Full protocol-v4 extension conformance | Deferred per phase (stretch not taken) |
| Vision-lane set-of-marks overlay | Optional path stubbed; screenshot_ref supported |
| Deliverable A/B --repeat 3 live | No credentials |

## Risks
1. Operator fingerprint re-check does a full re-observe; cost on chatty pages.
2. Single-page sessions: open_tab/close_tab are navigational no-ops.
3. Duplicate legacy computer_* tools may still be registered via registry — model should prefer `browser`.
4. render_preview depends on host packages (LibreOffice, poppler-utils); production sandbox images must include them.

## Gate checklist

| Gate | Status | Evidence |
|------|--------|----------|
| Protocol drift test exists and passes | **PASS** | 18 unit tests green; drift file present |
| Screenshot-scope fix in extension | **PASS** | captureTabScoped + capture_unavailable in background.js |
| Browser unit: takeover / approval / stale | **PASS** | test_browser_operator.py |
| 14 browser + 14 deliverable evals authored | **PASS** | br-v4-*.yaml, del-*.yaml under evals/tasks/dev |
| Browser evals on sandbox backend (live) | **PARTIAL** | Unit-backed policy cases PASS; live Playwright multi-page NOT RUN |
| Deliverable A/B --repeat 3 | **NOT RUN** | No credentials |
| Human-run held-out / GAIA | **NOT RUN** | Report-only when human runs |

## Gate decision
**Structural Phase 4 complete.** Live browser/deliverable performance and held-out numbers require human-run evals with credentials and a sandbox image that includes LibreOffice + Chromium. Protocol, operator, extension scope fix, render_preview, drift tests, and 28 new eval stubs are in tree and unit-tested.
