# Blocking fixes report (Claude review items 1–6)

## 1. Import collision + create_graph
- **Deleted** shadowing packages `app/models/` and `app/db/` so `app/models.py` and `app/db.py` are the modules Python loads.
- **Restored** `create_graph()` in `app/graph.py` as a minimal LangGraph (single loop node + planner alias) delegating to `app.runtime.loop.run`. Shim fallback if langgraph missing.
- journal.py already prefers `from app.db import Base`.

## 2. Runtime ↔ ModelRouter
- `ModelRouter` (core): `ROLE_LOOP`, `ROLE_ALIASES` (`loop`→`planning`), `_resolve_role()`, `route(..., tools=, tool_choice=)`.
- kimi_deepseek router: alias resolution so `loop` does not raise Unknown role.
- `runtime/models.py`: `route()` called with tools/tool_choice.

## 3. Loop / journal / kill-resume tests
- `backend/tests/runtime/test_loop_journal_resume.py` (fake model, router role, create_graph import, models/db not shadowed).

## 4. Eval runner loop path
- `evals/runner.py`: `runtime == "loop"` calls `app.runtime.loop.run` instead of NOT IMPLEMENTED.

## 5. Simulated results / lane defaults
- `lane_defaults.py`: **only** Kimi / DeepSeek; removed frontier model production defaults.
- `_simulate_lane_run` / `_simulate_long_horizon` marked **NOT AUTHORITATIVE**.
- `docs/runtime/TARGETS.md`: honest targets; simulated numbers not claimed as measured.

## 6. Held-out / GAIA
- `evals/tasks/held_out/README.md`: empty by design until import + loop + non-simulated comparison pass.
- Live held-out / GAIA: **NOT RUN**.

## Verification in this environment
- Full `import app.main` needs product venv (pydantic_settings, etc.). Syntax checks passed on edited files.
- Live model calls / full pytest: **NOT RUN** without API keys and full deps.

## Remaining risks
- websocket still expects rich LangGraph stream event shapes; single-node loop may need stream-shape adapters for every UI event type.
- plan_policy.py still imported (legacy not fully deleted) — out of scope for import fix.
- Native tool-calling on Kimi/DeepSeek clients depends on client.chat supporting `tools=`; JSON fallback remains.
