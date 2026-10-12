# Second-pass frontier architecture audit — Onsite48/Onsite48

## Bottom line

The repository is not failing because it lacks an agent loop. It has a planner, executor, verifier, LangGraph routing, durable task rows, queue primitives, checkpointing, connectors, browser paths, approvals, schedules, frontend streaming, and a large regression suite.

It is not yet world-class because the system still treats **execution state as a mutable dictionary whose meaning changes across plan revisions and presentation layers**. That one architectural choice creates several high-severity symptoms:

- recovery can execute against the wrong plan revision or declare a recovery complete because an old result has the same numeric index;
- executor presentation cleanup can remove the structured evidence that verification needs;
- deterministic shortcuts can bypass the same plan-quality and approval gates applied to model plans;
- all dependency-ready steps can run concurrently without resource-conflict or mutation-lane semantics;
- the production graph does not call the verifier's final-answer verification methods;
- accepted REST tasks are durably queued, but the consumer skeleton is not fully wired into application startup;
- frontend events are normalized, but the UI remains a projection of event shapes rather than a read model of a canonical execution journal;
- model routing remains provider- and role-oriented rather than requirement- and capability-oriented;
- memory is a useful retrieval/context layer, but not a typed, policy-governed memory system with provenance and write adjudication.

The result is a system that can look strong in a happy-path demo and pass many focused unit tests, while still being able to say “completed” when the intended recovery action was never attempted. That is the central reliability boundary to fix before adding more features.

## What was inspected

The second pass reopened the repository at `/home/ubuntu/work/P2026` and traced the real path through:

- `backend/app/api/product_tasks.py` and `backend/app/api/realtime.py` for accepted tasks and interactive execution;
- `backend/app/api/websocket_handler.py` for graph construction, checkpoint resume, event streaming, approval, takeover, and terminal events;
- `backend/app/graph.py` for planner → executor → verifier routing;
- `backend/app/agents/planner.py`, `executor.py`, `verifier.py`, and `intent_extractor.py`;
- `backend/app/core/agent_contracts.py`, `task_state_machine.py`, `checkpoint_manager.py`, `context_engine.py`, `model_router*.py`, `capability_model.py`, `control_plane_contracts.py`, and `step_registry.py`;
- `backend/app/tools/registry.py`, connector tools, MCP wrappers, browser tools, and bridge tools;
- queues, leases, workers, schedules, proactive operations, persistence, run ledger, outbox, and browser session services;
- `frontend/src/lib/streamEvent.ts`, `Chatinterface.tsx`, `ExecutionTimeline.tsx`, browser/session components, history, approvals, artifacts, and tests;
- `extension/onsite48-bridge/*` and its protocol tests;
- migrations, release gates, deployment files, and the full test suite.

The final repository validation after the previous production fixes was 1,096 backend tests passed, 65 environment-gated tests skipped, 38 frontend tests passed, TypeScript passed, Next.js production build passed, and 7 extension protocol tests passed. Passing tests are evidence of covered contracts; they do not prove that every cross-node contract is reachable in production. The concrete cross-node defects below were found by source tracing and direct reproductions.

## The actual execution path

### Interactive and REST entry points

An authenticated REST request enters `backend/app/api/product_tasks.py:run_task_rest`. It creates a `TaskThread` with `QUEUED` status and returns `202`. It intentionally does not use FastAPI `BackgroundTasks`, which is the correct durability direction. The row is expected to be claimed by `backend/app/services/task_queue_consumer.py`.

The interactive path enters `backend/app/api/websocket_handler.py:stream_task_events`. It creates or restores graph state, creates a graph with `create_graph`, writes checkpoint state, streams node outputs, handles confirmation and takeover exceptions, then derives a terminal event from the last graph state.

The REST helper `_run_rest_task` also calls `stream_task_events`, so the intended design is one canonical graph path with different submission/transport layers. The problem is that queue acceptance and queue execution are not one fully wired production lifecycle: `task_queue_consumer.py` accepts an injected executor callback, but the inspected application startup does not establish a dedicated consumer process that claims rows, marks them started, heartbeats them while running, and invokes `_run_rest_task`.

### Graph topology

`backend/app/graph.py:create_graph` wires:

`planner → executor → verifier → executor` for ordinary continuation, and `verifier → planner` when `needs_replan` is true. CodeAct is a parallel execution route for selected resources.

The graph is bounded by planner attempts, replan limits, and execution step limits. Those bounds are useful containment. They do not solve identity: a bounded loop can still complete the wrong step if old and new plans share numeric indices.

### Planner

`backend/app/agents/planner.py:Planner._run` resolves intent, selects deterministic shortcuts, or calls model planning. It normalizes plans, performs semantic/capability/coverage checks, calls `critique_plan`, stores the plan, resets `current_step`, and clears `needs_replan`.

The critical ordering defect is that deterministic shortcuts are selected before the `needs_replan` branch (`planner.py` around 1162–1182). A shortcut can clear `needs_replan` and reset `replan_count`, bypassing recovery-specific validation and repeat screening. The repository even contains a test asserting that a bridge shortcut precedes model replan; that is a policy smell, not a reliable recovery contract.

A second defect is the deterministic-plan exception in `planner.py` around 1261–1280. If the plan critic rejects a deterministic or quick plan, the planner logs `planner.critique_soft_accept` and adopts the plan. This makes fast paths less safe than model paths. A direct run with the real Gmail draft tool produced a `non_idempotent_write` plan with no postconditions; the planner logged that the plan was rejected because the mutation lacked explicit approval, then adopted it anyway.

### Executor

`backend/app/agents/executor.py:ExecutorNode._run` identifies completed work using `result.step_index` and the active plan's positional indices. It gathers every dependency-ready step through `asyncio.gather`, then mutates the shared results/tool-data state.

The first critical defect is revision aliasing. When the planner replaces `state["plan"]` and resets `current_step`, old results and `verified_step_indices` remain. The executor computes `completed_indices` from historical result indices. A result for old plan step 0 therefore counts as completion of new plan step 0. Direct reproduction: a one-step recovery plan plus an inherited old result at `step_index=0` returned completed without calling the recovery tool and with no final answer.

The second critical defect is evidence loss. The executor first appends result objects, then later runs `result.pop("tool_data", {})` while building the compact presentation state. The verifier receives the compacted `results` record and separately receives `state["tool_data"]`, but its postcondition path selects the latest result and reads top-level fields. A structured output such as `{id: "observed-123", status: "ok"}` can therefore become invisible to a `FIELD_EXISTS id` postcondition even though the tool returned it. The direct reproduction returned `postcondition_failed` and triggered replan for an actually successful tool call.

The third defect is concurrency semantics. The plan contract permits `depends_on: []`; executor gathers all ready steps. There is no scheduler model for tenant/account/resource/browser-session locks, mutation lanes, write ordering, conflict detection, or authoritative `concurrency_safe` metadata. A model can therefore emit two independent non-idempotent writes and cause both to begin together. Even if the model rarely does this, the architecture does not make the unsafe plan impossible.

### Verifier

`backend/app/agents/verifier.py` does deterministic step verification and contains offline methods for `verify_step_output`, `verify_plan`, and `verify_final_answer`.

The production graph calls `Verifier._run`, but repository search found no production caller for the final-answer verification methods. `websocket_handler.py` serializes `final_state.final_answer` and `verification` into `task_complete`. The synthesis prompt asks for evidence-grounded prose, but the final response has no reachable independent correctness gate.

The verifier also reports a PASS-shaped result for no-postcondition steps with `checked: false`. That is better than silently pretending the step was checked, but it still allows the surrounding task to be reported as completed unless the terminal gate interprets `checked: false` as partial or unverified.

`ENTITY_STATE` postconditions also treat `entity_id_ref` literally instead of resolving planner references such as `$steps.create-gmail-draft.output.draft_id`. The generated Gmail entity-state plans therefore declare a reference language that the evaluator does not implement.

### State, persistence, and transport

`backend/app/core/task_state_machine.py` gives the repository an explicit lifecycle vocabulary and transition validator. In production, however, the graph's execution truth is still the mutable LangGraph state dictionary. `results`, `plan`, `current_step`, `done`, `needs_replan`, `verification`, and `status` are written by different nodes and then projected into database events and frontend events.

The lifecycle state machine is a policy helper, not yet the sole authority for state transitions. A durable execution journal should be authoritative, while graph state should be a materialized working view with a revision and checkpoint cursor.

`backend/app/api/websocket_handler.py` correctly rejects duplicate live streams per thread in process memory. That is useful but not enough for multi-instance deployment: the lock is local to one process, while task claims, execution leases, and event sequence ownership must be shared across workers.

`backend/app/core/task_state_machine.py:should_apply_event` keeps only 500 seen event IDs in metadata. That is a bounded replay filter, not a durable event identity constraint. The durable event sequence and unique event identity must be enforced in the database/outbox layer, not only in a process-side projection.

### Tools, MCP, and connectors

`backend/app/tools/registry.py` is a good direction: tools have schemas, typed resources, operations, idempotency, approval flags, output schemas, postconditions, and evidence schemas. But legacy defaults are inferred by tool-name heuristics in `_default_contract_for_tool`. That means a missing authoritative contract is silently filled with a guessed capability model.

The registry does not yet make all critical execution properties first-class: resource identity, mutation conflict key, concurrency safety, authoritative verification provider, side-effect receipt type, compensation/rollback strategy, and ambiguity policy. Tool descriptions are not enough to prove those properties.

MCP wrappers validate input shape and call remote tools, but remote tool metadata is not equivalent to a server-authoritative safety contract. Every remote capability needs a normalized contract, an explicit trust tier, timeout/cancellation behavior, idempotency behavior, and postcondition/evidence strategy before it reaches the planner.

Connectors are broad, but breadth is not reliability. Gmail, Calendar, GitHub, Slack, WhatsApp, custom APIs, and MCP need a common action lifecycle: preflight, approval, claim, execute, receipt, reconcile, verify, and durable failure classification. A connector returning success without a source-of-truth read is not proof that the external state changed.

### Memory and context

`backend/app/core/context_engine.py` has real context engineering: stable prompt prefixes, tool catalog digests, result handles, task ledgers, and rehydration. That is stronger than naive transcript clipping.

The remaining problem is that the ledger is derived from plan/results and the memory system is primarily retrieval attached to context. It is not yet a canonical world-state model. A memory item needs type, provenance, confidence, owner, scope, time validity, sensitivity, source event, write reason, retention policy, and contradiction handling. Otherwise user memory, organization knowledge, task evidence, and untrusted page content can be mixed into one retrieval surface.

`backend/app/memory/*` provides schemas, semantic/graph retrieval, ingestion, and retention workers, but the agent architecture needs a hard distinction between:

- current execution evidence;
- durable task/episodic history;
- organization facts;
- user preferences;
- reusable procedures;
- untrusted observations.

Retrieval should not promote an old memory into an execution fact without a freshness/provenance check.

### Model routing

`backend/app/core/model_router_kimi_deepseek.py` is a useful adapter layer with retries, cooldowns, structured-output degradation, vision, usage, cost, and lanes. But core types still name Kimi/DeepSeek/NVIDIA, pricing constants are code-adjacent, and routing is primarily role-based (`planning`, `verification`, `fallback`) rather than requirements-based.

A stronger model would improve planning, ambiguity resolution, visual grounding, synthesis, and tool selection. It would not fix plan revision identity, evidence handoff, unsafe concurrency, missing final gates, queue wiring, or browser session authority. Architecture currently limits the benefit of model upgrades.

Conversely, a capability-aware router, structured goal contract, evidence ledger, and real terminal verifier would remove a large fraction of current failure modes even with the same model.

### Browser/operator layer

The previous pass corrected the major active-tab authority defect and sandbox ownership issue. The remaining architectural issue is broader: the bridge is a capable action surface, but the backend still needs a session-scoped world-state contract.

A browser step should carry browser session ID, target origin, tab identity policy, observation ID, DOM/accessibility snapshot hash, screenshot hash where used, action intent, precondition, postcondition, and receipt. Current browser and computer tools expose evidence and takeover states, but the universal contract is not strong enough to detect every stale observation or prove every side effect.

The extension's focused-session behavior now follows the current focused tab without restoring a stale lock. Explicit selected-only mode remains appropriate for precision. That should become an explicit session policy in the goal/execution contract rather than a legacy branch hidden in payload compatibility.

### Frontend and event state

`frontend/src/lib/streamEvent.ts` correctly validates event frames, bounds strings and lists, rejects unknown event types, and treats external content as text. That is good defensive parsing.

The weakness is the projection model. `Chatinterface.tsx` and `ExecutionTimeline.tsx` assemble user-visible state from event shapes and current React state. Reconnect and replay can therefore produce a UI that is internally consistent but not necessarily a canonical server read model. The frontend needs a versioned execution snapshot API and journal cursor. Events should be notifications that advance a cursor; snapshots should repair missed events.

The user should see the same durable concepts as the backend: goal contract, plan revision, step attempts, approval decision, evidence, verification verdict, pause reason, artifact version, and final claim status. A generic “completed” status is not sufficient for a partially verified task.

## Specific issue register

### Critical — plan revision identity is missing

**File → symbol:** `backend/app/agents/planner.py:Planner._run`; `backend/app/agents/executor.py:ExecutorNode._run`.

**Behavior:** planner replaces `state["plan"]` and resets `current_step`; executor uses historical positional `step_index` values to determine completion.

**Why it is a problem:** numeric positions are not stable identities across revisions.

**Consequence:** ghost completion, skipped recovery, incorrect verification attribution, and misleading terminal responses.

**Required change:** introduce immutable `plan_revision_id`, stable `step_id`, `attempt_id`, and `verification_id`. Active execution queries only attempts for the active plan revision. Carry-forward evidence is separate and can influence repeat screening without satisfying a new step automatically.

### Critical — evidence plane is coupled to presentation cleanup

**File → symbol:** `backend/app/agents/executor.py` result compaction; `backend/app/agents/verifier.py:_verify_postcondition`.

**Behavior:** `tool_data` is popped from the result and the verifier reads the compact record.

**Why it is a problem:** evidence needed for proof is destroyed while building a UI/model projection.

**Consequence:** false postcondition failure and recovery loops; potential false success for verifiers that see only partial output.

**Required change:** immutable `StepAttempt.evidence` or an evidence-store reference. Presentation summaries are derived views and cannot mutate evidence.

### Critical — no reachable final-answer correctness gate

**File → symbol:** `backend/app/agents/verifier.py:verify_final_answer`; `backend/app/api/websocket_handler.py:terminal_event_for_state`.

**Behavior:** final-answer helper is offline-only; terminal event trusts final state.

**Why it is a problem:** a model-generated claim can be delivered without claim-to-evidence verification.

**Consequence:** unsupported research conclusions, incorrect counts, and completion claims after partial work.

**Required change:** a terminal gate that maps material claims to evidence IDs, runs deterministic checks where possible, and emits `COMPLETED`, `PARTIAL`, or `UNVERIFIED` based on proof—not prose.

### High — deterministic shortcuts bypass safety parity

**File → symbol:** `backend/app/agents/planner.py` shortcut ordering and `planner.critique_soft_accept`; `backend/app/agents/plans/gmail.py` deterministic draft builder.

**Behavior:** deterministic plans can bypass replan gates and adopted plans can lack approval/postconditions.

**Consequence:** fast paths are less safe than model paths; external mutations can pass without proof.

**Required change:** all plans—deterministic, model, CodeAct, connector, and browser—must pass the same admission gate. Tool contracts must provide authoritative postconditions or the plan must be preview/draft/partial.

### High — unsafe parallel mutations

**File → symbol:** `backend/app/agents/executor.py:ExecutorNode._run`; `backend/app/core/agent_contracts.py` dependency normalization.

**Behavior:** all ready steps run through `asyncio.gather` without resource locks or mutation lanes.

**Consequence:** race conditions, nondeterministic ordering, duplicate writes, provider rate-limit bursts, and difficult recovery.

**Required change:** a durable scheduler with bounded read concurrency and serialized mutation lanes keyed by tenant, connector account, browser session, and external resource. Only authoritative tool metadata can mark a write concurrency-safe.

### High — queue consumer is not a complete production lifecycle

**File → symbol:** `backend/app/api/product_tasks.py:run_task_rest`; `backend/app/services/task_queue_consumer.py`; application startup.

**Behavior:** REST accepts a durable queued row, but the consumer is an injected callback skeleton and is not fully wired as a startup worker with `mark_started`, step/event lease heartbeats, and `_run_rest_task` execution.

**Consequence:** accepted tasks can remain queued indefinitely in a deployment that does not separately start the consumer.

**Required change:** dedicated worker entrypoint, explicit deployment service, claim → mark-started → execute → heartbeat → terminal transition adapter, poison-task quarantine, and queue-age metrics.

### High — lifecycle authority is split

**File → symbol:** `task_state_machine.py`, LangGraph state mutations, realtime persistence, frontend event projection.

**Behavior:** multiple layers can mutate status-like fields and serialize their own interpretation.

**Consequence:** stale/out-of-order events, reconnect divergence, and hard-to-prove terminal semantics.

**Required change:** append-only event journal plus server-authoritative state transition service. Graph state becomes a revisioned working set, not the durable source of truth.

### High — memory has no universal provenance and contradiction contract

**File → symbol:** `backend/app/memory/*`, `context_engine.py`, memory retrieval tools.

**Behavior:** memory retrieval is available to context but lacks a universal freshness, source, confidence, sensitivity, and contradiction envelope.

**Consequence:** stale or untrusted content can be treated as current task truth.

**Required change:** typed memory records with source event, scope, validity interval, confidence, sensitivity, and write policy; execution evidence remains separate from semantic memory.

### Medium — model routing is still model-name coupled

**File → symbol:** `model_router_kimi_deepseek.py`, `main.py`, planner/verifier/vision/memory callers.

**Behavior:** callers know roles and adapter names more than task requirements.

**Consequence:** swapping models requires code changes, and fallback may choose a technically available but unqualified model.

**Required change:** `TaskRequirements → ModelDescriptor → RoutingDecision`, with capability and cost evidence recorded per call.

### Medium — frontend needs a snapshot/journal read model

**File → symbol:** `frontend/src/lib/streamEvent.ts`, `Chatinterface.tsx`, `ExecutionTimeline.tsx`.

**Behavior:** events are normalized and rendered directly into local state.

**Consequence:** reconnection and multi-surface usage can show a plausible but incomplete execution story.

**Required change:** versioned execution snapshot with cursor, event replay, and durable IDs for plan revision, step attempt, approval, evidence, and verdict.

## Representative task walkthroughs

### 1. “What time is it in London?”

**Expected:** direct deterministic time tool, no unnecessary model planning.

**Likely actual:** the current-time shortcut is efficient and likely succeeds when the tool is registered. The architectural risk is that shortcut selection is embedded in planner heuristics rather than expressed as a capability/intent policy. A new time variant can fall through to intent/model planning or public web browsing.

**Fix:** typed `time.get` capability and deterministic intent compiler with an explicit proof that no external browsing is required.

### 2. “Research this company and produce a sourced report.”

**Expected:** source collection, source provenance, synthesis, claim/evidence links, artifact version, and report delivery.

**Likely actual:** wide research and browser/API tools can collect evidence, but final-answer verification is not reachable. A report may be delivered with citations or a polished synthesis without a terminal claim-evidence gate.

**Fix:** source objects and claim objects become durable; every material claim must link to source evidence or be labeled uncertain; artifact is `PARTIAL` if required sources are missing.

### 3. “Find 20 potential customers and put them in a spreadsheet.”

**Expected:** research, deduplication, structured rows, provenance, spreadsheet artifact, and count verification.

**Likely actual:** the model can chain research and document/chart tools, but context handles and memory may compress rows; there is no universal artifact schema and terminal claim check for exactly 20 qualified rows. A missing source can be hidden by synthesis.

**Fix:** schema-first row contract, dedupe key, source per row, deterministic count/quality gate, immutable artifact version.

### 4. “Open Gmail, find the latest email from X, summarize it and draft a response.”

**Expected:** read Gmail, select correct message, summarize, create draft, verify draft identity/content, stop before send.

**Likely actual:** Gmail deterministic paths exist, but the draft builder can create a non-idempotent write with no postcondition and the planner can soft-accept a rejected critique. Draft creation can be reported as completed without source-of-truth verification.

**Fix:** draft action requires approval classification, idempotency/duplicate policy, authoritative draft read-back, and exact target/body diff in the approval record.

### 5. “Check my calendar, identify conflicts and propose changes.”

**Expected:** read-only calendar retrieval, timezone-normalized conflict analysis, proposed changes without mutation.

**Likely actual:** calendar tools and intent routing exist. Model synthesis can still overstate a conflict if event windows or timezones are incomplete. The plan contract does not make the “proposal only” boundary a first-class side-effect policy.

**Fix:** typed interval normalization, conflict evidence, proposal artifact, and a policy that forbids mutation unless the user separately approves an exact change.

### 6. “Log in, find a document and download it.”

**Expected:** isolated session, user takeover for MFA/CAPTCHA, resume from the same browser state, verified download artifact.

**Likely actual:** browser sessions, takeover, bridge, and persistent-browser services exist. The remaining risk is session world-state proof: stale tabs/observations, missing download receipt, or resumptions that are not tied to a plan revision and browser observation ID.

**Fix:** session-scoped observation/action/receipt ledger and artifact checksum verification.

### 7. “Monitor this website and notify me when it changes.”

**Expected:** event-triggered monitor with baseline hash, change classification, deduplication, notification policy, and pause/disable controls.

**Likely actual:** heartbeat/proactive paths exist, but monitoring is a parallel subsystem rather than a routine attached to a project/run with a durable baseline and evidence lineage.

**Fix:** monitor object with baseline snapshot, detector version, event identity, delivery receipt, backoff, and owner policy.

### 8. “Every Monday, research X and send me a report.”

**Expected:** a versioned routine with schedule, permissions, source policy, report artifact history, and approval/delivery rules.

**Likely actual:** schedules and proactive tasks exist, but scheduled execution is not fully unified with the interactive execution journal and the same queue/worker lifecycle is not proven end-to-end.

**Fix:** schedule creates a `RoutineRun` that uses the same goal contract, execution journal, policy decision, evidence ledger, and artifact lineage as an interactive task.

### 9. “Find GitHub issues, determine duplicates, summarize.”

**Expected:** API pagination, stable issue IDs, dedupe explanation, source links, and no accidental writes.

**Likely actual:** GitHub capability coverage exists, but connector results can be compressed and memory/context can hide rows. Without a result-set identity and source ledger, duplicate analysis can be non-reproducible.

**Fix:** immutable source dataset snapshot, deterministic dedupe key, model-assisted similarity with saved rationale, and a report artifact referencing issue IDs.

### 10. Long-running task fails halfway and resumes hours later

**Expected:** durable checkpoint, active plan revision, attempt ledger, lease recovery, and no duplicate external writes.

**Likely actual:** checkpoint/resume and queue/reaper primitives exist, but old result indices can alias new plan indices. A task can resume into a wrong cursor or ghost-complete.

**Fix:** revisioned plan/step/attempt identities and reconciliation before retrying any ambiguous side effect.

### 11. Webpage changes unexpectedly

**Expected:** stale observation detected, re-observe, repair action, or request user intervention.

**Likely actual:** browser observation and visual grounding exist, but the universal action contract does not require snapshot hash/precondition validation for every action. A stale locator can fail, click the wrong element, or trigger replan without preserving exact browser evidence.

**Fix:** every browser action consumes an observation ID and precondition; stale observations produce a typed `STALE_WORLD_STATE` recovery path.

### 12. Approval required before a side effect

**Expected:** show exact action, target, arguments/diff, risk, reversibility, expiry, and resume the same step.

**Likely actual:** confirmation checkpoints and UI flows exist, but approval is not yet one durable policy decision model shared by connectors, browser, schedules, and frontend. Parallel ready steps can also produce multiple confirmations without a durable scheduling barrier.

**Fix:** approval grant bound to task, plan revision, step ID, attempt, exact argument digest, target resource, policy version, actor, expiry, and result receipt.

### 13. One tool returns incorrect/incomplete data

**Expected:** schema validation, confidence/quality flag, independent corroboration or explicit uncertainty.

**Likely actual:** tool output validation is shallow and final-answer gate is absent. The agent can synthesize from incomplete output and report success.

**Fix:** output schema plus evidence quality, source freshness, conflict detector, and a verifier that can downgrade rather than replan blindly.

### 14. Two tools conflict

**Expected:** preserve both observations, classify conflict, seek authoritative source or ask user.

**Likely actual:** results are accumulated, but there is no universal conflict object or resolution policy across connectors, memory, browser, and research.

**Fix:** `ConflictSet` with claims, source quality, freshness, scope, resolver strategy, and user-facing unresolved state.

### 15. Task requires 20–50 tool calls

**Expected:** long-horizon graph, bounded context, durable handles, parallel reads, serialized writes, checkpoints, compaction, and progress proof.

**Likely actual:** budgets, context handles, task ledgers, queues, and checkpoints help. The missing revision/evidence/journal substrate means long tasks amplify state aliasing, evidence loss, and frontend divergence.

**Fix:** execution journal and artifact/evidence handles become canonical; model context is a projection, not the source of truth.

## Frontier comparison: facts, inference, and unknowns

### Manus

Public materials describe Cascade as an agent harness that adds specialized capabilities as needed, keeps briefs/pages/videos/automations in one connected project, and claims lower token/time/cost in a tested configuration. Manus 2.0 also describes Cloud Computer for persistent project environments, Automations triggered by connected-service events, Studio creation tools, and visible Remote Control/Computer Use. These are public product claims, not proof of private internals. [1]

**Gap for Onsite48:** the repository has many corresponding primitives but not one project/artifact/run spine. The largest gap is not the existence of a browser tool; it is connected project state, durable artifact lineage, and human edits that can be handed back to the agent without losing context.

### Muse

Meta publicly describes Muse Secure VM, a dedicated browser/computer, background work, app permissions, approval before sensitive actions, credential isolation, a separate Sentinel that mediates internet access, audit trails, revocation, and user-editable memory. [2]

**Gap for Onsite48:** current approvals and connector vault are useful but are not yet a separate trust plane with secret brokering, per-app scopes, network egress policy, immutable policy decisions, and independent reconciliation. Muse’s public claims support the need for these boundaries; they do not reveal its private implementation.

### Grok Bot

xAI publicly describes always-on AI teammates with their own computers, work across apps and tools, continue after the user steps away, hand off work among multiple Bots, retain context, and learn routines by watching a workflow. [3]

**Gap for Onsite48:** schedules/proactive work and subagent capabilities exist, but there is no equally coherent concept of an agent identity with isolated resources, a versioned routine learned from a successful run, and a multi-agent handoff contract. Do not copy the marketing surface without per-agent identity, credential isolation, and action receipts.

### GPT-6 Astra

OpenAI publicly describes computer use for forms, CRMs, calendars, research, documents, sites, frontend QA, and software troubleshooting, with better handling of ambiguity and evolving requirements. The public safety documentation describes confirmation policies, tool-use monitoring, jailbreak testing, realistic workplace evaluations, and explicit concern about misrepresentation of completed work. [4] [5]

**Gap for Onsite48:** the repository’s model/tool prompt rules address some of these behaviors, but the architecture does not yet enforce the final claim/evidence boundary independently. A stronger model could reduce planning and grounding errors, but it cannot be trusted to supply the system’s missing proof ledger or authorization boundary.

**Unknowns:** private harnesses, training data, internal orchestration, exact model routing, and unreleased implementation details for all four products are not knowable from public material and are not inferred here.

## The five foundational primitives that are missing

### 1. Revisioned execution journal

This is the canonical append-only record of goal version, plan revision, step identity, attempt, observation, action intent, approval, receipt, verification, artifact, pause, and terminal verdict.

The repository currently has many pieces—task events, run ledger, step registry, checkpoints—but not one identity system that all pieces share. This primitive solves revision aliasing, replay ambiguity, recovery attribution, UI repair, and auditability.

Introduce it incrementally: add IDs and revision fields to existing state/results/events, then make the journal authoritative for completion and terminal status before migrating storage.

### 2. Evidence and world-state ledger

A structured evidence record must be immutable and separate from display/model summaries. It stores source, observation, freshness, schema, hashes, confidence, and claim references.

This solves evidence loss, stale browser actions, unsupported final answers, conflict handling, and source-quality reasoning. It can be added beside current `results` and `tool_data`, then replace destructive compaction.

### 3. Capability and policy plane

Every tool needs an authoritative contract for inputs, outputs, resources, mutation class, idempotency, concurrency safety, approval policy, side-effect receipt, verification provider, and compensation strategy.

This solves deterministic/model safety divergence, unsafe MCP imports, parallel mutations, and connector inconsistency. Existing `ToolSchema` and `CapabilityContract` are the migration seam.

### 4. Durable execution scheduler

The scheduler decides which attempts can run, with read-only concurrency, mutation lanes, resource locks, leases, cancellation, and checkpoint barriers.

This solves unsafe `asyncio.gather`, queue/worker integration, long-running recovery, and multi-agent collision. Start with a single worker and resource keys; add parallel read pools later.

### 5. Terminal proof gate and read model

The terminal gate determines completion from required conditions and evidence. The frontend consumes a versioned execution snapshot/read model and journal cursor, not raw event interpretation.

This solves false success, partial completion ambiguity, reconnect divergence, and unsupported final claims. Implement the gate before adding more surface features.

## Target architecture

The target should be:

`User request → Intent contract → Goal contract → Policy preflight → Plan revision → Capability admission → Durable scheduler → Observation → Action attempt → Receipt/evidence → State transition → Deterministic verification → Recovery/reconciliation → Claim verification → Artifact/version → Terminal verdict → Read model/UI`

A **Goal Contract** should include normalized goal, constraints, scope, success conditions, required evidence, allowed side effects, approval policy, time budget, cost budget, workspace/project, and ambiguity policy.

A **Plan Revision** should contain stable step IDs, dependencies, expected outcomes, preconditions, postconditions, evidence selectors, resource keys, concurrency class, retry/recovery policy, and plan parent.

A **Capability Registry** should expose normalized contracts for built-in tools, APIs, MCP, browser, computer use, and CodeAct. Natural-language descriptions remain for model selection but never substitute for machine policy.

A **Policy Engine** should produce a durable decision before side effects. The decision includes actor, scope, exact argument digest, target, risk, expiry, approval status, and policy version.

A **Scheduler** should claim attempts with leases and resource locks. It should serialize writes per account/resource/session and allow parallelism only where authoritative metadata says it is safe.

An **Evidence Store** should be append-only and content-addressed. The model sees bounded projections and handles; verifiers see the original structured evidence.

A **Recovery Manager** should classify tool failure, stale world state, authentication, policy denial, timeout, provider rate limit, ambiguous side effect, and goal failure separately. It should reconcile before retrying an ambiguous write.

A **Terminal Gate** should verify required goal conditions and map claims to evidence. It should be impossible for a model string alone to transition a task to verified completion.

A **Read Model** should expose snapshot version, journal cursor, active plan revision, attempts, approvals, evidence, verdict, artifacts, and pause reason. WebSockets deliver updates; snapshot endpoints repair dropped events.

## Migration order

1. Add `plan_revision_id`, `step_id`, `attempt_id`, `observation_id`, `approval_id`, `receipt_id`, `verification_id`, `evidence_id`, and `artifact_version_id` to state and events. Add regression tests for shorter and longer replans with inherited results.
2. Stop destructive mutation of `results`. Preserve structured evidence and create a presentation projection. Implement `$steps.<id>.output.<path>` resolution with schema validation.
3. Remove the deterministic-plan soft accept for safety-critical blockers. Require mutation postconditions/receipts or downgrade to preview/partial.
4. Implement the terminal proof gate and make `task_complete` impossible without a terminal verdict. Add claim-to-evidence links to final state.
5. Add mutation resource keys and a scheduler. Serialize Gmail/Calendar/GitHub/browser-session writes; bound read concurrency.
6. Wire the durable queue consumer as a real deployment worker. Add mark-started, heartbeats, terminal persistence, poison-task handling, and queue metrics.
7. Make browser actions consume observation IDs and produce receipts. Keep focused-tab mode but encode it in the session policy contract.
8. Split memory into task evidence, episodic history, semantic organization/user memory, procedures, and untrusted observations with provenance and retention.
9. Introduce capability-based model routing and record routing decisions/costs on every run.
10. Add snapshot/cursor APIs and make the frontend a read-model consumer. Add end-to-end reconnect and replay tests.
11. Add release gates for extension, frontend production build, queue worker, Compose startup, public smoke, failure injection, rollback, backups, restore rehearsal, and image digest promotion.
12. Only then expand proactive routines, multi-agent groups, project surfaces, and broad connector breadth.

## Model dependence

The current weakness is mixed. Roughly speaking, model quality affects intent ambiguity, plan quality, visual grounding, synthesis, and conflict resolution. The orchestration architecture determines whether a correct model action is safely attributed, verified, resumed, and represented to the user.

Replacing the current model with a stronger frontier model would likely improve first-pass task success and reduce unnecessary replanning. It would not fix revision aliasing, evidence loss, unsafe parallel writes, un-wired REST queue execution, or final-answer proof. It could even increase blast radius by producing more confident and more parallel side effects.

Keeping the current model while implementing the five primitives would remove many catastrophic failures and make improvements measurable. The correct order is therefore execution correctness first, then model upgrades and model-specific optimizations.

## VC-level diagnosis

The defensible story is not “we have the most autonomous model.” It is “we built the execution plane that can tell the difference between an action attempted, an action acknowledged, an action verified, an action ambiguous, and a goal actually completed.”

A credible demo should show one reversible workflow with: plan preview, exact approval, visible execution, a forced ambiguous outcome, reconciliation without duplicate side effect, a takeover flow with hidden secrets, a resumed checkpoint, evidence-backed artifact, and a terminal verdict that refuses to claim more than it can prove.

## References

[1]: https://manus.im/blog/introducing-manus-2-0 "Introducing Manus 2.0"
[2]: https://about.fb.com/news/2026/09/introducing-muse-personal-ai-agent/ "Introducing Muse: The World’s First Personal AI Agent Built for Everyone"
[3]: https://x.ai/news/introducing-grok-bot "Introducing Grok Bot"
[4]: https://openai.com/index/gpt-6-astra/ "GPT-6 Astra: A new generation of intelligence"
[5]: https://deploymentsafety.openai.com/gpt-6-astra "GPT-6 Astra Deployment Safety Hub"