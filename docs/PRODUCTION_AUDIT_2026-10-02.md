# Onsite48 Production Audit — 2026-10-02

## Executive assessment

The YouTube pause failure was not an isolated extension defect. The browser operator had a split authority model: the backend persisted task/selected-tab context while the extension maintained its own selected-tab state. Focused sessions could therefore fall through to legacy selected-tab checks when a command contained an old `target_tab`.

The audit also found a live product-path failure outside the extension: the frontend `SandboxWorkspace` called `/api/sandbox/*`, but `sandbox_api` was not included in `main.py`, so the workspace was unreachable. Those routes also accepted a caller-supplied `user_id`, which would have allowed cross-tenant sandbox selection if the router were enabled.

## Changes made

### Browser operator authority boundary

- Focused browser sessions now resolve the currently focused public tab at command execution time.
- Legacy `target_tab` payloads cannot force a focused session back into selected-tab behavior.
- Explicit `selected_only` mode remains available as a deliberate precision mode.
- Existing origin and permission checks remain in place; removing the tab lock does not remove site authorization.

### Persistent browser correctness

- Real Playwright persistent sessions now use `launch_persistent_context` rather than passing `user_data_dir` to `Browser.new_context`.
- Browser storage state is written using the sanitized, collision-resistant tenant slug rather than the raw user ID.
- Missing storage-state files are not passed to real Playwright on first launch.
- Compatibility fallback remains for lightweight test doubles and legacy embedded callers.

### Sandbox product path and tenant security

- The existing sandbox router is now mounted in FastAPI.
- Every sandbox route derives its owner from the authenticated account via `get_current_user`; request query/form `user_id` values are ignored.
- Sandbox statistics are scoped to the authenticated account instead of exposing all in-memory sandbox records.

## Verification

- Focused Python regression suite: **100 passed**.
- Python application/test compilation: **passed**.
- Extension JavaScript syntax checks: **passed**.
- Extension protocol suite: **7 passed**.

## Deliberate non-changes

- The explicit selected-tab mode was not removed; it is useful for precision workflows and is no longer the default focused-session authority.
- Origin authorization and Chrome site permission checks were not weakened.
- Arbitrary terminal execution remains an intentional sandbox capability, but it is now behind authenticated tenant ownership when exposed through the API.

## Launch risks to address before broad production exposure

1. Run a browser-instrumented smoke test against a real YouTube tab: pause, play, toggle, tab switch, and resume after a tab switch.
2. Add end-to-end API tests asserting unauthenticated sandbox requests return 401 and authenticated users cannot select another user ID.
3. Complete the competitive capability audit and convert only evidence-backed capabilities into roadmap work; do not widen permissions merely to match competitor marketing.
4. Add production metrics for focused-tab resolution failures, origin denials, media-control verification failures, bridge lease expiry, and user takeover transitions.

## Competitive synthesis and VC positioning

The reviewed products—Meta Muse, Manus Cue, xAI Grok Bot, OpenAI Cloud Browser, Perplexity Comet, and Opera Neon—converge on the same table stakes: browser execution, persistent work context, background work, connected services, visible progress, and human approval for consequential actions. Their public materials leave meaningful gaps around exact tab/session semantics, durable checkpoints, idempotent writes, recovery after ambiguous submissions, CAPTCHA/MFA handoff, and audit-grade evidence.

The strongest defensible positioning for Onsite48 is therefore a **trustworthy execution plane**: typed intent and policy outside the model, per-task browser profile and tab ownership, accessibility-first observation with stale-state detection, secure takeover with secret masking and verified resume, deterministic postconditions, action receipts, and reconciliation before retrying any external write. This is also the right VC narrative because it converts a common demo failure—“the agent clicked but did it actually happen?”—into an architectural advantage.

### Recommended staged build order

**Phase 0:** lock the execution contract: task, plan version, step, observation, action intent, approval grant, receipt, artifact, pause reason, and evidence bundle. Ship a timeline, plan preview, Stop, Take over, and narrow allowlisted demo origins.

**Phase 1:** make the browser loop safe and synchronous: accessibility snapshots, stable references, origin checks, before/after diffs, typed postconditions, and approval cards showing exact target, payload, destination, side effect, and reversibility.

**Phase 2:** productionize authentication and isolation: per-task profiles, session expiry and revocation, protected user takeover for login/MFA/CAPTCHA/payment/identity checks, secret masking, checkpoints, and verified resume.

**Phase 3:** add durable background execution only after recovery exists: leases, checkpoints, idempotency keys, bounded retries, reconciliation, crash restoration, partial completion, and a clear indeterminate state.

**Phase 4:** grow connectors, schedules, artifacts, and multi-agent roles behind the same policy and evidence plane. Prefer stable APIs over browser fallback, and measure completion, duplicate effects, recovery, approval latency, injection blocks, and user corrections by site/task class.

### VC demo recommendation

Use one controlled, reversible workflow—not a broad autonomy reel. Show planning, an exact approval, execution in a visible browser, a forced refresh or ambiguous submit, reconciliation finding the existing receipt rather than duplicating the action, a simulated MFA/CAPTCHA handoff with the model frozen and secrets hidden, then verified resume. Close with the capability matrix and internal reliability scorecard. This demonstrates engineering maturity instead of relying on an unmeasured happy path.

## Repository-wide verification update

The repository-root test configuration now enables `pytest-asyncio` automatically and points pytest at `backend/tests`, so a root-level test command behaves consistently with the backend-local configuration. The frontend dependency installation was repaired with optional platform packages restored; the source archive still excludes installed dependencies and generated build output.

Final verification: **1,096 backend tests passed, 65 environment-gated tests skipped, 38 frontend tests passed, TypeScript validation passed, and the Next.js production build passed**. The build emitted non-blocking ESLint warnings about existing React hook dependencies and explicit `any` types; these did not prevent compilation or type validation.