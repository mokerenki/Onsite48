# ARCHITECTURE — Onsite48 Runtime (as built through Phase 6)

## One-loop design

```
                    ┌─────────────────────────────────────┐
                    │           run(run_id, goal)         │
                    └─────────────────┬───────────────────┘
                                      │
                    ┌─────────────────▼───────────────────┐
                    │  ContextBuilder (stable system      │
                    │  prefix + journal + workspace FS)    │
                    └─────────────────┬───────────────────┘
                                      │
              ┌───────────────────────▼───────────────────────┐
              │              model_complete (lane)            │
              │   loop | worker | verifier | summarizer | vision│
              └───────────────────────┬───────────────────────┘
                                      │ tool calls
              ┌───────────────────────▼───────────────────────┐
              │  policy_evaluate → ToolSpec.execute           │
              │  (untrusted wrap, readback, locks, spawn)     │
              └───────────────────────┬───────────────────────┘
                                      │
              ┌───────────────────────▼───────────────────────┐
              │  Journal append · metrics · compaction check  │
              │  verifier on finish · recovery classifier     │
              └───────────────────────────────────────────────┘
```

Control flow is a single tool-calling loop. A plan is only a visible `todo.md`
the agent maintains in the workspace. No regex intent, no plan templates, no
planner/executor/verifier graph topology.

## Components (Phase 1–6)

| Module | Role |
|--------|------|
| `loop.py` | One tool-calling loop; budgets; spawn integration; cost rollup |
| `context.py` | Stable system prompt, untrusted wrapping, offload, compaction |
| `tools.py` | ToolSpec registry surface for the model |
| `policy.py` | Independent policy + provenance; tool output is data |
| `verifier.py` | Outcome verification against goal / filesystem evidence |
| `recovery.py` | Failure classification + auth refresh + recovery messages |
| `workspace.py` | Persistent sandbox filesystem abstraction |
| `journal.py` | Append-only event log; restorable state |
| `spawn.py` / `roles.py` / `locks.py` | Sub-agents, role configs, resource locks |
| `browser/` | Observe/action/receipt protocol + stale recovery |
| `deliverable.py` | Render/lint/inspect before handover |
| `metrics.py` | Cost, tokens, cache hit, steps (Phase 6) |
| `lane_defaults.py` | Lane→model defaults from committed sweep (Phase 6) |

## Reused from the old codebase (via interfaces)

- Auth / multi-tenancy, Postgres models + Alembic (migrations from 34 up)
- Transactional outbox, task leases, run ledger, idempotency
- Connector vault + OAuth
- Tool registry implementations (`tools/registry.py`, `tools/*.py`) when present
- Sandbox templates + network policies (`k8s/`)
- `services/agent_computer.py` concepts
- Policy / approval policy concepts
- Cost accounting, tracing, proactive worker
- Frontend shell + execution timeline/replay UI

## Deleted at Phase 3 cutover

`agents/plans/`, regex intent modules, `core/compound_intent.py`,
`core/intent_resolution.py`, replan machinery, `graph.py` planner/executor/
verifier topology, goal/capability-contract layers that only fed the planner.

## Phase 6 additions

- Long-horizon eval suite (`lh-*`) + simulated runner
- Lane sweep harness + committed defaults
- Metrics module + efficiency instrumentation
- Proof package under `docs/proof/`
- Static trajectory dashboard
