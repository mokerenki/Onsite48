# Onsite48 remediation roadmap

This file is the authoritative plan for the current remediation work. It exists
because the plan previously lived only in conversation history, where it was one
compaction away from being lost — and where `docs/history/` already used a
*different* `P<number>` scheme (there, P4.4 means "add correction memory" and P5
means "safety and authority"). A future session reading `docs/history/` would
have executed the wrong work with total confidence.

**This roadmap's numbering is its own. It does not match `docs/history/`.**

## Invariants

These hold for every phase below. A phase that breaks one of them is not done,
no matter how green the suite is.

1. `python tools/release_gate.py` passes before a phase is committed.
2. `backend/app/agents/planner.py` stays behaviour-identical through P6. The
   release gate exists specifically to make that refactor safe; if the gate is
   not green, the refactor is the wrong thing to be doing.
3. P6 adds verticals through planner `DomainSpec`/`IntentSpec` manifests
   without modifying `planner.py` or `executor.py`.
4. No phase touches a real environment. Database-backed tests run against a
   uniquely named database, migrated from zero, dropped afterwards.
5. Existing lint findings are ratcheted, not re-baselined downward to make a
   stage pass.

## Sequencing rationale

P5 (the release gate) deliberately precedes P6. Moving roughly 800 lines out of
a 3,103-line planner without an automated behavioural check means silent
regressions — the kind that only surface in production, against a user's Gmail
account, weeks later. The gate is the precondition, not a follow-up.

## Status

### Complete

| Item | Commit | Note |
|---|---|---|
| P0 — browser authorization | `f9fa3b3` | Privilege escalation and telemetry leak |
| P1 — postcondition verification | `b89fcd7` | Verification now actually executes |
| P2a — stream idempotency | `88d184c` | One stream per thread, race-free sequence |
| P2b — task leases | `becb179` | Orphaned accepted work fails visibly |
| P3.1 — missing verification modes | `0e666c8` | Six declared modes now implemented |
| P3.2 — verification lattice | `2cfb0c9` | INCONCLUSIVE no longer counts as success |
| P3.3 — verification semantics | `30fa545` | Tautological checks on mutations blocked |
| P4.1 — separate memory types | `26e85d2` | Typed, enforced write policy |
| P4.2 — provenance and expiry | `26e85d2` | Source, evidence, expiry honoured at read time |
| P4.3 — context budgets by purpose | `df1b84c` | Entity ledger was unbounded |
| P4.4 — lint configuration | `0b8ea18` | Flat config plus ratcheting baseline |
| Disposable DB harness | `0b8ea18` | Migration-from-zero and sweeper coverage |
| P5 — release gate + CI | `b20d99f` | Five stages, cheapest first |
| Cost ledger | `32afc89` | Unmeasured spend is no longer a clean zero |
| `DATABASE_URL` fail-closed | `e9aabe0` | Fatal when deployed, not in development |
| Secret validation fail-closed | this commit | `prod`/`staging`/padded `production` accepted dev secrets |
| P6.0 precedence snapshot | `d9bb323` | The artefact that makes P6.1-P6.5 provable |
| P6.1 GitHub extraction | `8e03f57` | First domain out, with structural guards |
| P6.2 Time extraction | `60cf807` | Four scattered regions, closed dependency set |
| P6.3 Calendar extraction | `d023462` | Sliced not retyped; one misfiled constant left for P6.4 |
| P6.4 Gmail extraction | `1a5d9d4` | 671 lines, two slices; the largest domain |
| P6.5 Web extraction | this commit | Completes P6; found the corpus coverage gap |
| Alembic credential removal | `ee97452` | Dead config, deleted not rotated |

### Remaining

Nothing in the original scope. P6 is complete: `planner.py` is 1,982 lines
from 3,103, with the five domains in `app/agents/plans/`.

| Phase | Domain | Builders | Where |
| --- | --- | --- | --- |
| P6.1 GitHub | 2 | 35 lines | `plans/github.py` |
| P6.2 Time | 3 | 108 lines | `plans/time.py` |
| P6.3 Calendar | 6 | 178 lines | `plans/calendar.py` |
| P6.4 Gmail | 16 | 671 lines | `plans/gmail.py` |
| P6.5 Web/browser | 3 | 166 lines | `plans/web.py` |

`planner.py` kept every call site byte-identical throughout. Each domain module
exports its builders under the names they had in `planner.py`, so
`_deterministic_plan` and `_shortcut_for_intent` were never edited, only
imported into.

What remains in `planner.py` is the planner itself: the `Planner` class, the
precedence order, the plan validators, the quick-factual path, wide research,
cost accounting, and the model-facing prompt. The quick-factual and
wide-research builders were deliberately left, being the two domains the
roadmap did not scope and which are not interleaved with the others.

Two findings from P6.4 and P6.5 are worth more than the line counts.

P6 is the largest and is deliberately last. It moves roughly 800 lines out of a
3,103-line planner, and the release gate now exists to make each extraction step
checkable. It should be done as a sequence of small, individually reversible
extractions with the gate green between each, not as one large move.

P6.0 landed the artefact that makes the rest provable: a snapshot of what the
planner's domain precedence currently produces, over 32 goals. Reordering two
overlapping plan builders would otherwise pass every existing test.

P6.1 extracted the GitHub builders into `app/agents/plans/github.py`. It went
first because it is the only domain with no cross-domain references at all, so a
mistake in the pattern is cheapest to find there.

The remaining domains, in the order their dependencies allow, grounded by
reading `planner.py` rather than assumed:

| Phase | Domain | Builders | Why in this position |
| --- | --- | --- | --- |
| P6.2 Time | `_resolve_timezone_alias`, `_timezone_hint_from_goal`, `_current_time_plan` | Done: `plans/time.py` |
| P6.3 Calendar | 6 builders, 3 regions | Done: `plans/calendar.py` |
| P6.4 Gmail | 16 builders, 2 regions | Done: `plans/gmail.py`, 671 lines |
| P6.5 Web/browser | `_website_build_*`, `_direct_url_plan` | Interleaved with Gmail; last |

The structural fact that shapes all of these: **the domains are not contiguous
in `planner.py`.** Gmail's builders occupy lines 564-611, 647-724 and 842-1385,
with GitHub between the first two and website between the second and third. No
extraction is a matter of moving one line range, and P6.4 in particular has to be
done function by function.

P6.2 sharpened this. The time domain was scattered over *four* regions, not one:
two pattern constants at lines 99 and 111, the two alias tables at 142 and 172,
and the three builders at 313-386, with the quick-factual and web patterns
wedged between them. It was still safe to move as a unit, because each of the
four constants was read from exactly one place and all of those places moved
together. That closure was checked before moving anything, and a guard now
asserts no domain constant is still assigned in `planner.py`. A constant left
behind and still *read* would be a `NameError`; one left behind and still
*written* while the domain module keeps its own copy would be silent, and the
copies would drift the first time an alias was added to one of them.

P6.3 found the same shape in calendar, plus one misfiled constant.
`_WORD_NUMBER_PATTERN` sat inside the calendar region of `planner.py` but is
read only by `_GMAIL_INBOX_PATTERN`. Moving it out of line-order convenience
would have filed a Gmail constant under calendar, and the snapshot would not
have noticed, so it was left in place for P6.4 and is now pinned by a test that
asserts it is still *defined* in `planner.py`. Only the two calendar builders
that callers actually use are re-exported; the four parsing helpers moved with
the module and have no callers outside it, so re-exporting them would have been
dead imports.

Calendar was moved by slicing the file rather than retyping it, and the sliced
body was checked byte-identical against the pre-move source. A 178-line block
retyped by hand can drop a branch, and the snapshot is the only thing between
that and a commit. The first attempt at the slice was wrong: the tail index was
off by 147, which left every builder in *both* files, and only the expected
line count caught it. That count is now asserted in the slice itself.

P6.4 moved 671 lines, the largest domain, and produced the two findings that
justify the rest of the sequence:

- **The Planner's own control flow reads a domain's trigger patterns.**
  `Planner._deterministic_plan` tests `_GMAIL_DRAFT_PATTERN` and
  `_GMAIL_SEND_PATTERN` before it will build a draft for a compound goal. That
  is precedence logic living outside the domain it belongs to. The move removed
  the constants and the precedence snapshot failed immediately with a
  `NameError`, on exactly the two goals that reach the compound branch. The
  patterns are re-exported with a comment saying why, because "why is the
  planner importing two Gmail regexes" is not answerable from the code.
  This is the clearest argument for having pinned precedence before moving
  anything: the failure was a loud `NameError` at a known goal rather than a
  draft that silently stopped being built.
- **A two-line analysis mistake broke collection in eleven test files.** The
  scan deciding what to re-export looked at `def` lines, so it never saw the
  module-level pattern constants, and `app/fast_answer.py` imports
  `_GMAIL_INBOX_PATTERN` from `app.agents.planner`. The precedence snapshot was
  green, the structural guards were green, and 50 targeted tests were green; the
  release gate failed on collection errors after all of it. The fix is a test
  that asserts the entire import surface, rather than the part of it someone
  thought to look at.

Worth recording about the verification itself: a byte-identity check of the
sliced body against the pre-move source reported a difference that was not
there. `subprocess.run(..., text=True)` decodes with the Windows locale, so a
`…` in a comment arrived as `�`. The file was correct and the harness was
wrong. Comparing bytes requires saying which encoding is meant.

- **The Planner's control flow reads domain trigger patterns.** `_deterministic_plan`
  tests `_GMAIL_DRAFT_PATTERN` and `_GMAIL_SEND_PATTERN` for compound goals, and
  `_is_quick_factual` tests `_URL_PATTERN`, which moved to `plans/web.py` with
  `_direct_url_plan`. The domains are not independent of the planner: four
  patterns cross the boundary. They are re-exported, and a test now asserts the
  planner and the domain hold the *same object* for each. Two copies would both
  be correct alone and would drift, after which a builder matches a goal the
  planner's own guard rejects. This is a seam worth closing properly by moving
  the compound-goal rule into the Gmail module, which would be a behaviour
  change and is not part of P6.
- **The snapshot could not see two of P6.5's three builders.** The corpus
  registered `browser`, `computer_navigate` and `computer_read_page` but not
  `computer_inspect`, which `_direct_url_plan` emits. `_plan_tools_available`
  rejects a plan using an unregistered tool, so "open https://example.com/pricing"
  recorded as *no plan* rather than as a wrong one, and both web builders were
  invisible to the guard that was supposed to prove their move. This is the
  failure mode of a coverage-based safety net: absent coverage reads as correct
  behaviour. The registry now matches the tools the builders emit, the
  direct-URL branch has two goals, and changing `computer_inspect` to `browser`
  in the extracted module fails the snapshot.

Each item's exact scope is grounded in code before it is implemented, and
recorded here once grounded.

## Findings carried forward

Fixed:

- `alembic/env.py` overwrote `sqlalchemy.url` from app settings, so migrations
  could not be pointed at any other database. This hung the first disposable-DB
  run for ten minutes. `alembic.ini` carries a committed placeholder URL, so
  "is `sqlalchemy.url` set?" could never mean "was this deliberately targeted"
  without silently redirecting production migrations; hence the explicit
  `SYNTHAI_ALEMBIC_DATABASE_URL` opt-in.
- `alembic.ini` carried a committed credential. It was dead config (always
  overwritten), so it was deleted rather than rotated.
- Unbound `:params` in the disposable-DB teardown silently leaked every
  throwaway database. Found by an explicit leak check, not by inspection.
- `tools/release_gate.py --only` disabled the database it then advertised,
  turning a clean skip into a wall of connection errors.

Open:

- `app/core/memory_contracts.py` was dead code: `MemoryRecord`, `MemoryLayer`
  and `accept_memory` were referenced only inside their own module and one test
  file, while `CogneeMemory.ingest` wrote every event into one undifferentiated
  payload with no layer, no confidence and no expiry. Fixed in `26e85d2`.
- `build_entity_context` had no budget. Reproduced at 178,646 characters
  (~45k tokens) from step count alone, while `_MAX_TOTAL_CHARS` guarded only
  the conversation half. The module docstring claimed "Bounded. Never grows the
  prompt beyond a hard char budget", which was false for this function. Fixed
  in `df1b84c`, to 1,877 characters and now flat in step count.
- "Deployed" was decided twice with two different definitions, so the secret
  checks were open in exactly the environments the database check covered.
  `SecurityConfig.validate` used `environment == "production"` against a value
  that was lowercased but never stripped, while `_resolve_database_url` tested
  membership in `{"production", "prod", "staging"}` against a stripped value.
  Consequences, each reproduced: `ENVIRONMENT=" production "` started the app
  with the source-tree `change-me-in-development` signing key and no
  `CONNECTOR_ENCRYPTION_KEY`; `ENVIRONMENT=prod` and `ENVIRONMENT=staging`
  demanded a `DATABASE_URL` while accepting that same development key. Padding
  is trivial to produce and invisible in a log. Fixed by reducing the value once,
  in `normalize_environment`, and testing both call sites against one
  `DEPLOYED_ENVIRONMENTS` set.

Worth knowing, found while testing the above:

- `AetherConfig.environment` is a dataclass field whose default calls
  `os.getenv` at class-definition time, so it is frozen when `app.core.config`
  is first imported. Setting the variable in a test afterwards cannot move it.
  A test asserting the old behaviour would have failed for that reason rather
  than because of the bug, so the process-startup cases reload the module inside
  a context manager that restores the environment afterwards.

Caught by the gate while building P4.1/P4.2:

- A numeric Qdrant range filter over an ISO expiry string is rejected at
  construction; range filters accept numbers only. Payloads now carry an
  `expires_at_epoch` companion. Worth remembering: the gate caught this, and a
  unit test that asserted only the happy path would not have.

## Verification discipline

Findings in this file were reproduced, not inferred. Specifically:

- The lint ratchet was confirmed to fail when a baseline is lowered.
- The orphan sweeper's `WHERE` clause was mutation-tested: dropping the expiry
  comparison, and reaping `running` work, both fail the suite.
- The release gate was confirmed to have teeth: one broken DB-backed assertion
  produced `1 failed, 514 passed` and `RELEASE GATE FAILED`.
- A green gate run leaves no container and no throwaway database behind.
- Both halves of the environment fix were mutation-tested separately: restoring
  the `== "production"` equality test fails 21 of the 66 config tests, and
  removing only the `.strip()` fails the padded-spelling cases. The
  `test_the_two_deployment_classifications_agree` guard is self-consistent by
  construction, so it detects divergence between the two checks rather than
  either one being wrong; it is not a substitute for the other assertions.
- P6.1's structural guards were mutation-tested. Re-defining
  `_github_repository_count_plan` in `planner.py` while leaving the import in
  place, so the local definition shadows the extracted one, fails both the
  identity and the defined-in-the-domain-module assertions. That is the failure
  mode the snapshot cannot see: the plan that comes out is unchanged, so P6.0
  stays green while the extraction is quietly undone.
- One test written during P6.1 was wrong and was corrected rather than
  accommodated: it asserted `planner` did not appear in the domain module's
  source, which the module docstring mentions in prose. It now inspects imports
  through the AST. A test that fails for a reason unrelated to the property it
  claims to check trains you to ignore it.
- The P6.2 constant-residue guard was caught being unable to fail. It collected
  `ast.Assign` and so missed `ast.AnnAssign`; appending
  `_TIMEZONE_ALIASES: Dict[str, str] = {...}` to `planner.py`, which is exactly
  the drift it exists to prevent, left the suite green. It now handles both
  forms, and the mutation fails it. The snapshot was green throughout, which is
  the point: the duplicated constant was never read, so no plan changed.
- A P6.3 guard that asserted `_GMAIL_INBOX_PATTERN` appears somewhere in
  `planner.py` passed while its definition had been deleted and one reference
  remained. It now asserts the module-level *assignment* exists. Both guards of
  that kind in `test_plans_extraction.py` are now built on one shared AST helper
  rather than each re-deriving the name set differently.

## Phase 0-6 audit against the original plan

Audited line by line against the code, not against a summary. Where an item is
recorded as outstanding, the evidence is named so the next session does not have
to rediscover it. Note that this plan's P-numbering is *not* the P-numbering of
the phase numbers used below: the earlier sections of this file use P2.1-P6.5 for
the extraction work, and the plan the user supplied numbers its phases 0-6. The
audit below uses the supplied plan's numbering.

### Phase 0 - security. Complete, all six.

| Item | Status | Evidence |
| --- | --- | --- |
| 0.1 client auth fields removed | done | `realtime.py:42 browser_scope_from_session(owned, ...)` builds every field from a persisted row |
| 0.2 `assert_runnable` fails closed | done | `browser_runtime.py:140-164`; each of origins, capabilities and `tab_ids` has an explicit empty-grant refusal |
| 0.3 `tab_ids` populated | done | `realtime.py:74` populates it, and `browser_runtime.py:160-164` enforces it |
| 0.4 ownership authoritative | done | scope is derived from the owned row; no client field survives |
| 0.5 `response_preview` gated | done | `model_router_kimi_deepseek.py:63-70` reads `SYNTHAI_TRACE_CONTENT`, default off |
| 0.6 exception text removed | done | `main.py:576-580` returns a message plus `request_id`; `main.py:446-451` documents why `str(exc)` is not exposed on the unauthenticated detailed health route |

### Phase 1 - real verification. Complete.

The plan offered a choice for the dual model. The branch taken was deletion
rather than reconciliation: `Postcondition` is the single contract with 78
references, and the executor's second, independent implementation built on a
`VerificationRule.from_dict()` that never existed was removed. `executor.py:827-834`
records why it must not be reintroduced, because the dead code read like an
active safety net. 29 tests in `test_phase1_postcondition_verification.py`,
including that every generated postcondition parses.

### Phase 2 - idempotency and durability. Three of five.

| Item | Status | Evidence |
| --- | --- | --- |
| duplicate stream rejected | done | `websocket_handler.py:270-279` refuses and returns a message |
| sequence allocation safe | done | `UniqueConstraint("task_id", "sequence")` on `TaskEvent`, and `realtime.py:160-169` takes `pg_advisory_xact_lock` before allocating |
| reaper for orphaned work | done | `services/task_lease.sweep_orphaned_tasks`, driven by `proactive_worker.py:165`; 14 tests |
| resume path atomic | **outstanding** | `realtime.py:295-311` reads `task.status` then assigns. No `with_for_update`, no version-guarded `UPDATE ... WHERE status = :expected`. The `preserve_paused_status` check at line 295 mitigates one specific race, a late event repainting a paused task, and is not atomicity |
| idempotency on every mutating call | **partial** | `executor.py:1593-1594` derives idempotency from the tool schema and only retries `READ_ONLY` tools, so retry is safe. There is no idempotency-key store, so a duplicate *submission* is prevented by the stream check rather than deduplicated by key |

### Phase 3 - operational control. One of five.

| Item | Status | Evidence |
| --- | --- | --- |
| durable cancel | done | `product_tasks.py:452-474` is authenticated, writes `CANCELLED` durably, and also cancels bridge commands |
| readiness split | **outstanding** | only `/api/health` and `/api/health/detailed` exist (`main.py:437,455`). No per-feature readiness, no Qdrant probe |
| correlation IDs | **partial** | `main.py:351-371` mints `request_id`, stores it on request state, logs it, sets `X-Request-ID`, and returns it in error bodies. It is *not* threaded into tracing spans or task events, and the frontend never displays it |
| alert set | **outstanding** | nothing. The only workflow is `.github/workflows/release-gate.yml`; no alert rules, no SLOs |
| operator tooling | **partial** | `operator.py` has feed, replay, control and (new) costs. No search-by-request-id and no trace-inspection endpoint; `retry_task` exists as a realtime action in `realtime.py:644` |

### Phase 4 - production invariants. One of four.

| Item | Status | Evidence |
| --- | --- | --- |
| noninteractive lint | done | flat config plus lint ratchet, `0b8ea18` |
| boot guard: TLS is an invariant | **outstanding** | `config.py:97` still defaults `cookie_secure=False` and nothing refuses to start when `ENVIRONMENT=production` and `COOKIE_SECURE` is not true, or when a CORS origin is not https. The `DATABASE_URL` and secret guards landed; this one did not |
| encrypted off-host backups | **outstanding** | no backup or restore tooling anywhere in the repo, and no stated RPO/RTO. Losing `CONNECTOR_ENCRYPTION_KEY` forces every user to re-authorise every connector |
| reproducible build | **partial** | `backend/Dockerfile:19` runs as `USER onsite48`, so non-root is real. Base images are tags, not digests (`python:3.12-slim-bookworm`, `node:20-alpine`), and the frontend builder does not use a frozen lockfile |

### Phase 5 - the release gate. Five of twelve stages.

Present: alembic single head, backend tests, frontend typecheck, frontend tests,
lint ratchet, all behind a disposable database that migrates from zero.

Absent: extension tests (a suite exists at
`extension/onsite48-bridge/tests/protocol.test.mjs` but there is no `package.json`
and the gate never runs it), config and security checks, compose startup,
public-origin smoke, two-organization isolation, authenticated WebSocket,
worker/browser failure injection, rollback exercise. "frontend build" is also
served by `typecheck` rather than an actual build.

The gate is real and it has teeth, but it covers a minority of what the plan
requires. A green gate is currently evidence about backend correctness and
migrations, and is not evidence about the deployment.

### Phase 6 - the scaling plan. Two of four.

| Item | Status | Evidence |
| --- | --- | --- |
| domain manifests | done | `app/agents/plans/{github,time,calendar,gmail,web}.py`; `planner.py` 3,103 -> 1,992 lines |
| cost ledger and price table | done | `services/cost_ledger.py`, exposed at `GET /api/operator/costs` in `38aa9e6`. Cost *per configured model* is honoured; there is no separate managed price table, which is arguably what "price table" meant |
| queue + externalised state + object storage | **outstanding** | no queue library, no object storage. State is in-process plus Postgres, which is exactly the failure mode the Phase 2 reaper was built to paper over |
| persona / narrow-workload work | **outstanding** | no `DomainSpec` or `IntentSpec` manifest type exists. The extraction produced per-domain builder modules, which is the structural half of that idea, but the manifest contract that would let a vertical be added without touching the planner is not written |

### The single largest gap

Phase 2's reaper exists because work is queued in-process. Phase 3's cancel has to
write durably for the same reason. Phase 6's queue and externalised state remove
the cause of both. Taken together those three are the same piece of work, and the
reaper and the durable cancel are the two mitigations standing in for it.

## Forward plan: finishing Phases 0-6

This section is the plan of record. The audit above says what is true; this
says what happens next, in an order chosen so that each step is verifiable
before the step that could break it.

### Design facts that constrain the order

Three facts, established by reading the code rather than by inference, decide
most of the sequencing.

**1. There is no queue, and Postgres is already half of one.** `TaskStatus.QUEUED`
is *set* in four places (`product_tasks.py:196`, `realtime.py:500,607,655`) and
never *claimed* by anything. Execution is handed to
`background_tasks.add_task(_run_rest_task, ...)`, which runs in the API process
and dies with it. The Phase 2 sweeper closes the resulting hole by reaping a
lapsed `QUEUED` task to `FAILED` (`task_lease.py:52 REAPABLE_STATUSES`). So the
durable row, the lease and the reaper already exist; what is missing is a
consumer. That makes Phase 6's queue much smaller than "add a queue" implies,
and it means the reaper should end up a *retry*, not a *failure*.

**2. The gate's database wiring has a scaling trap.** `release_gate.py:326`
computes `needs_db = not args.skip_db and (should_run("backend"))` from a single
hardcoded stage name. Every stage is selected by string (`--only migrations`,
`--only typecheck`, ...). Adding any new DB-backed stage and then running it in
isolation would advertise no database and fail on connection errors, which is
the exact failure the roadmap records as already fixed once. The stage list and
the infrastructure decision must be derived from one declaration, not two.

**3. Metrics exist but are too thin to alert on.** `/api/metrics`
(`main.py:515-559`) exposes six series: `onsite48_up`, `onsite48_browser_ready`,
and four proactive-worker counters, with the worker heartbeat read from Redis.
There is no request counter, no latency histogram, no queue-age gauge, no
provider 429 counter and no saturation gauge. Phase 3's alert set is therefore
blocked behind a metrics expansion; it is not blocked behind Prometheus.

### Sequenced work

Each step is independently reversible and leaves the gate green. Steps 1-4 are
small and code-only; step 5 is the first that changes a protocol.

**Step 1 - make the gate able to grow.** Derive the stage list and the
`needs_db` decision from one declaration carrying each stage's name, function and
whether it needs the database. Add the missing *config and security checks*
stage: `DATABASE_URL` and secret fail-closed behaviour, the browser
authorization denial, and the no-content-in-spans default. Add the
*two-organization isolation* and *authenticated WebSocket* stages, which are
cheap as tests and are the gate's only real coverage of tenant boundaries.
This is first deliberately: every later step is verified by this gate, and the
plan's own words are that the gate is "the prerequisite for everything above
shipping safely".

**Step 2 - TLS as an invariant (Phase 4.1).** `SecurityConfig.validate` already
receives the environment, so the guard is a few lines: in a deployed environment
`cookie_secure` must be true, and `frontend_url` and every CORS origin must be
https. Reuse the single `is_deployed` definition from the `config.py` fix rather
than reintroducing an equality test. Tests must cover the padded and aliased
environment spellings, because that is precisely how the previous two guards in
this file were bypassed.

**Step 3 - atomic status transitions (Phase 2.3).** `realtime.py:295-311` reads
`task.status` and then assigns. Take the row with `with_for_update()` on the
status-application path, or use a version-guarded
`UPDATE ... WHERE status = :expected`. Keep the existing `preserve_paused_status`
rule; it is a correct product decision, it just is not atomicity. Test with two
concurrent transitions against one task and assert exactly one wins.

**Step 4 - readiness split (Phase 3.1).** Leave `/api/health` as the L1
DB/Redis gate that gates restarts. Add `/api/health/ready` returning per-feature
sections for browser, worker and memory, with a Qdrant probe, and 200 only when
every *required* feature is ready. The split exists because folding browser into
L1 causes restart loops, which the plan called out and which the current design
correctly avoids.

**Step 5 - idempotency keys (Phase 2.5).** The executor already derives
idempotency from the tool schema and refuses to retry anything that is not
`read_only`, so *retry* is safe. What is missing is deduplication across
duplicate submissions. Add a key store with a TTL, checked immediately before a
mutating tool executes, recording the outcome so a repeat returns the first
result rather than acting again. Redis, since it is already a dependency and
already used for worker state.

**Step 6 - correlation IDs end to end (Phase 3.2).** `request_id` is minted in
`main.py:351-371`, logged, returned in error bodies and set as `X-Request-ID`.
Thread it into tracing spans and `TaskEvent` payloads, then surface it in the
frontend error surface. Without the last two steps the ID is useful only to
whoever already has server logs, which is not the point.

**Step 7 - the remaining gate stages (Phase 5).** Extension tests: a suite
exists at `extension/onsite48-bridge/tests/protocol.test.mjs` with no `package.json`
and no runner, so it has never run in CI. Then compose startup, public-origin
smoke, worker/browser failure injection, rollback exercise, and a real frontend
`build` in place of relying on `typecheck`.

**Step 8 - the queue consumer (Phase 6.2).** A worker that claims with
`SELECT ... FOR UPDATE SKIP LOCKED` over `QUEUED` rows, transitions to `RUNNING`
under the existing lease, and runs the same `_run_rest_task` path. Postgres is
already durable and already the source of truth, so this is a consumer, not a
new system. Once work survives a restart, the sweeper's `QUEUED -> FAILED` becomes
a retry with backoff, and its test must change to assert retry rather than
failure. This is the highest-risk step and is deliberately after Step 7.

**Step 9 - externalised state and object storage (Phase 6.2).** LangGraph
checkpoint state out of process, attachments to object storage, and the
`TaskThread` JSON columns narrowed. Depends on Step 8, because externalising
state before work is durable buys nothing.

**Step 10 - metrics, then alerts (Phase 3.3).** Extend `/api/metrics` with what
the alert set needs: request count by status, latency histogram, task queue age,
worker heartbeat *age* rather than a boolean, provider latency and 429 counts,
DB/Redis/Qdrant saturation, browser failure rate, and cost per task so the
p95-cost-per-task-over-40%-week-over-week rule has a series to evaluate. Only
then write the alert rules.

**Step 11 - backups and a restore rehearsal (Phase 4.2).** Encrypted off-host
backups for Postgres, Qdrant, Redis, attachments and the connector encryption
key; a stated RPO and RTO; and a dated full restore rehearsal before anyone is
invited. The key is the urgent part of this: losing `CONNECTOR_ENCRYPTION_KEY`
forces every user to re-authorise every connector. This is operational rather
than code, and it is gated on hosting.

**Step 12 - reproducible build (Phase 4.3).** Pin base images by digest, frozen
lockfile installs, fail the build if the browser is not actually usable, and
immutable tagged artifacts. Non-root is already real (`backend/Dockerfile:19`).

**Step 13 - persona and vertical manifests (Phase 6.3).** The
`DomainSpec`/`IntentSpec` contract, so a vertical is added as a manifest instead
of by editing the planner. The extraction produced per-domain builder modules,
which is the structural half of this; the manifest type does not exist yet. This
is product work and belongs last.

### What is deliberately not being done yet

- **No new phase numbering.** `docs/history/` uses a different scheme again, and
  this file's own P2.1-P6.5 refers to the extraction work. The audit and this
  plan use the supplied plan's Phase 0-6 numbering and say so.
- **No behaviour change to the planner.** It is still behaviour-identical to
  baseline `80b1a70` apart from relocation, and the precedence snapshot enforces
  that. Steps 2-9 do not touch it.
- **No alerting before metrics.** Writing alert rules against six gauges that do
  not include latency or queue age produces alerts that cannot fire correctly.