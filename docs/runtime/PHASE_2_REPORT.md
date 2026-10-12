# Phase 2 Report — Full sandboxed workspace and skills

## What changed

### Workspace (`backend/app/runtime/workspace.py`, ~600 LOC)
- Abstract `Workspace`: create, exec, read, write, edit, list, grep, snapshot, destroy, python_exec.
- **DevWorkspace**: process-isolated directory tree under `ONSITE48_WORKSPACE_ROOT` (default `/tmp/onsite48_workspaces/{run_id}`). Clean env (secrets stripped), path-escape guards, timeouts, process groups.
- **E2BWorkspace**: production path reusing `SandboxManager`.
- Factory `get_workspace` / `create_workspace` / `destroy_workspace` keyed by `run_id` (re-attach after worker restart).
- `WORKSPACE_BACKEND=dev|sandbox` (default dev).

### Tools (`backend/app/runtime/tools.py`)
Replaced host-side file helpers with workspace-backed tools:
- `shell_exec`, `file_read`, `file_write`, `file_edit`, `file_list`, `file_grep`, `python_exec`, `deliver_file`, `load_skill`.
- `deliver_file` publishes via `services/artifact_versions.py` with lineage; falls back to `out/` if store unavailable.

### Loop integration
- On run start: `create_workspace(run_id)`, set `AGENT_RUN_ID`, snapshot logged.

### Skills (`backend/app/skills/`)
- Data packages only. Progressive disclosure via `list_skill_summaries` in system prompt; `load_skill` tool returns body.
- Precedence: user → org → builtin (env `ONSITE48_USER_SKILLS`, `ONSITE48_ORG_SKILLS`).
- Initial skills (9): docx-report, pptx-deck, xlsx-model, pdf-output, web-research-method, data-analysis, static-website-build, email-drafting, calendar-timezones-and-recurrence.
- No skill grants tool permissions.

### Image / k8s
- `docker/sandbox-terminal.Dockerfile`: python 3.12 + pinned python-docx, pptx, openpyxl, matplotlib, pandas, reportlab, LibreOffice, poppler, Node.
- `k8s/network-policies.yaml` and `k8s/sandbox-templates/terminal-sandbox.yaml`: real minimal specs (no longer scaffold-only).

### Tests
- `tests/runtime/test_workspace.py`: create/read/write/edit/list/grep/shell/python, path escape, env secret non-leak, resume-after-restart, full escape suite.
- `tests/runtime/test_skills.py`: list/load, no eval-id special cases.

### Evals
- 12 new `comp-ws-001` … `comp-ws-012` tasks under `evals/tasks/dev/` (tier: computer). Require workspace computer (CSV analysis, multi-file, package install, docx/pdf/xlsx, static site, scripts).

## Gate 2 checklist

| Gate | Status | Evidence |
|------|--------|----------|
| 12 new dev evals requiring computer | **PASS (authored)** | `comp-ws-001`–`012` YAML present. Full LLM pass rates: **NOT RUN** (no model API keys / offline adapter only in this environment). Unit computer tests: 12 passed. |
| Sandbox escape suite → all fail safely | **PASS** | Raw output below. |
| Resume same workspace after restart, files intact | **PASS** | `test_resume_same_workspace` + `test_factory_reattach` passed. |
| Skills A/B table | **PARTIAL** | Structural A/B: skills load and appear in prompt. Live model A/B pass-rate table: **NOT RUN** (requires paid model runs). Justification recorded; no skill deleted for failing A/B because live A/B not executed. |
| No skill contains per-task special cases | **PASS** | `test_no_eval_id_special_cases` grepped; empty bad list. |

### Escape suite raw output
```
ESCAPE_SUITE_RESULTS [
 {'attempt': 'read_host_passwd', 'error_code': 'path_escape'},
 {'attempt': 'read_host_shadow', 'error_code': 'path_escape'},
 {'attempt': 'read_relative_escape', 'error_code': 'path_escape'},
 {'attempt': 'list_host_root', 'error_code': 'path_escape'},
 {'attempt': 'grep_host', 'error_code': 'path_escape'},
 {'attempt': 'write_host_tmp', 'error_code': 'path_escape'},
 {'attempt': 'env_secret', 'leaked': False},
 {'attempt': 'metadata_service', 'exit_code': 0, 'stdout': 'Traceback ... raise exceptions[0] ...'}  # connection failed (expected)
]
1 passed in 0.11s
```

### Skills A/B (structural)
| Skill | Without (prompt has no skill) | With (load_skill available) | Notes |
|-------|-------------------------------|-----------------------------|-------|
| docx-report | Model must invent procedure | Procedure + verify steps | Live eval NOT RUN |
| data-analysis | Ad-hoc pandas | Chart + deliver checklist | Live eval NOT RUN |
| (all 9) | — | Listed in system prompt | `list_skill_summaries` works |

## Lines added / deleted
- Added: workspace.py ~600; skills ~9 SKILL.md + loader; tools expanded; tests ~250; 12 eval YAML; Dockerfile + k8s.
- Deleted: none of the Phase-3-cutover targets (plans/, regex intent, etc. still present).
- Host-side file tools in runtime/tools.py replaced (not deleted file, rewritten).

## NOT RUN / NOT IMPLEMENTED
- **Docker backend**: Docker binary and socket absent in evaluation environment. DevWorkspace is process-isolated, not a container. Dockerfile is written and ready.
- **E2B integration tests**: No `E2B_API_KEY`. E2BWorkspace code path present; not exercised live.
- **Full 12 computer evals with LLM**: Offline adapter baseline only; computer tier expected near 0 without model+workspace loop end-to-end. Unit tests substitute for isolation/resume gates.
- **Live skills A/B with model**: Not run (cost/API). Skills retained pending human-held-out confirmation.
- **python_repl.py retirement**: Still present; runtime path no longer calls them for builtin `python_exec`. Full delete deferred until registry usages audited in Phase 3 cutover.
- **k8s cluster apply**: Templates written; not applied (no cluster).

## Risks
1. DevWorkspace is not a kernel-enforced container — escape suite covers path/env but not full syscall isolation. Production must use `WORKSPACE_BACKEND=sandbox` (E2B) or the Docker image under k8s.
2. `deliver_file` depends on artifact storage backend being configured; falls back to workspace `out/`.
3. Skills content is procedural guidance; model may still ignore `load_skill` — measured only after live evals.

## Branch / commits
- Branch: `runtime/phase-2`
- Commits: baseline import; Phase 2 workspace+skills+tools+tests+evals
"""
