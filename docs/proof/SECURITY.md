# SECURITY — Threat model, injection evals, sandbox, policy

## Threat model

| Asset | Threat | Mitigation |
|-------|--------|------------|
| User secrets / connector tokens | Exfiltration via prompt injection in tool output | Untrusted-data wrappers; policy ignores directives inside tool results; connector vault never exposed to model |
| External side effects (send, delete, spend) | Unauthorized mutation | Policy engine + approval gates; idempotency keys; resource locks |
| Sandbox escape | Break out of workspace / network policy | k8s network policies; workspace path confinement; escape suite unit tests |
| Cross-tenant data | Worker or lead reading another org's state | Isolation: worker context = goal + input refs + schema only; org_id on journal |
| Prompt injection (web, email, files) | Model follows attacker instructions | `wrap_untrusted`; recovery classifier; verifier checks outcomes not plan text |
| Cost abuse | Runaway loop / fan-out | Step/token/cost/wall budgets; depth_cap on spawn; cost rollup |

## Injection defense

- All tool outputs pass through `wrap_untrusted(content, source=..., trust=...)`.
- Policy and loop look for `UNTRUSTED_DATA` markers; content inside cannot change
  tool permissions, approval state, or system prompt.
- Browser protocol treats page content as data; observe/action/receipt separates
  perception from actuation.
- Injection-oriented unit tests live under `tests/runtime/` (recovery, policy
  provenance). Live injection eval suite: structural PASS; adversarial live
  corpus NOT RUN without credentials.

## Sandbox escape suite

- Path confinement: writes outside workspace rejected (unit).
- Network policy: only allow-listed egress (k8s templates; structural).
- No privileged syscalls from tool surface (tool registry design).
- Results: unit tests PASS in prior phases; full k8s integration NOT RUN in
  this partial extract.

## Policy design

- Independent of the model: `policy.evaluate(spec, args, state)` runs before
  every side-effecting tool.
- Provenance: values originating from untrusted results cannot be used as
  recipients/URLs for mutating tools without explicit user confirmation path.
- Approval pauses are durable (journaled) and resumable hours later.
- Side effects are idempotency-aware (`client_request_id`).

## Known gaps

1. No continuous adversarial red-team corpus in CI (needs dedicated eval set).
2. Multi-tenant concurrency governor is in-process + lease interface; full
   Redis-backed rate limits depend on infra not present in this extract.
3. Vision/screenshot path must re-verify that image-derived text is wrapped
   as untrusted (covered structurally; live adversarial images NOT RUN).
4. Extension-side screenshot scope fix is present; end-to-end with real Chrome
   NOT RUN without browser credentials.
