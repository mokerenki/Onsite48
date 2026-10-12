# GAPS — Honest distance from Manus-1.0-class

This list is intentionally not flattering. It is ordered by expected impact on
eval pass rate if closed.

## Not yet Manus-class

1. **Live model competence on computer-use and deliverable tiers**
   - Why: Offline adapter baseline shows ~0 pass on general/deliverables/research
     computer tiers. Live frontier models close much of this, but this workspace
     has no credentials, so we cannot claim the numbers.
   - Evidence: EVAL_BASELINE_DEV.json; long-horizon and lane sweep are simulated.
   - Expected impact if closed: +0.3–0.5 absolute pass@1 on computer tiers.

2. **Long-horizon reliability under real wall-clock and real approvals**
   - Why: 8 lh-* tasks exist and the simulator exercises compaction / pause /
     restart paths, but real 20–60 minute runs with human approval gaps and
     worker process kills are NOT RUN.
   - Expected impact: exposes residual journal/lease bugs; currently one
     simulated failure mode (`worker_restart_state_loss`).

3. **Wide research quality at 50+ items with partial failure**
   - Why: `wide_map` and sa-wide-50-headline exist; live wall-time speedup and
     quality vs single-agent NOT measured.
   - Expected impact: +research tier pass and lower cost/success via parallelism.

4. **Browser takeover + login-wall robustness in production**
   - Why: protocol v4 and unit tests pass; live login-wall → takeover evals
     require real sites and extension.
   - Expected impact: browser tier from ~0.17 offline toward frontier rates.

5. **Held-out / GAIA external validity**
   - Why: held-out set and GAIA files are deliberately absent from the agent
     context (no overfitting). Until a human runs them, the
     dev-versus-held-out gap is unknown.
   - Expected impact: calibration of claimed pass rates; may reveal 5–15 pt gap.

6. **Prompt-cache hit rates on the real provider**
   - Why: metrics module records cache hits when the provider exposes them;
     efficiency before/after is simulated. Real cache stability needs production
     traffic.
   - Expected impact: 20–40% cost reduction on long runs if prefix stays stable.

7. **Nested child runs in the frontend timeline**
   - Why: journal supports parent_run_id; FE nesting not verified in this extract.
   - Expected impact: operability, not raw pass rate.

8. **Cost preflight / concurrency governor for large fan-out**
   - Why: depth_cap and budgets exist; preflight estimate + Redis governor are
     interface-level only.
   - Expected impact: fewer runaway-cost incidents under load.

## Three highest-leverage next steps (ranked by expected eval impact)

1. **Run the live frontier reference on the full dev + held-out sets** with
   real sandbox and connectors. Establish true pass@1, cost/success, and the
   held-out gap. (Unlocks honest investor numbers.)

2. **Close residual recovery bugs exposed by live long-horizon** (worker
   restart state loss, approval resume after hours, multi-compaction coherence).
   Fix runtime, not prompts.

3. **Ship browser login-wall → takeover + deliverable render-inspect-fix loop
   on live sites/files**, then re-measure browser and deliverable tiers.

## What we will not do

- Special-case task ids, fixtures, or grader behavior.
- Author, read, or request held-out / GAIA contents for tuning.
- Claim live numbers that were not run.
