# Phase 2 Discovery

## Verified from codebase (2026-10-03)

### Existing sandbox infrastructure
- `backend/app/sandbox/manager.py` (186 LOC): E2B-backed SandboxManager. Creates per-task_id sandboxes via `e2b_code_interpreter.Sandbox`. In-memory `_sandboxes` dict. Not persistent across process restarts; requires E2B_API_KEY.
- `backend/app/sandbox/persistent_manager.py` (526 LOC): User-scoped persistent E2B sandboxes with Redis + inactivity cleanup (30m sleep / 2h destroy). Still E2B.
- `backend/app/services/agent_computer.py` (642 LOC): Browser/computer-use session manager (Playwright routes, CAPTCHA handoff, visual grounding). Not a general shell/filesystem workspace.
- `k8s/sandbox-templates/*.yaml` and `k8s/network-policies.yaml`: **SCAFFOLD only** — comments say "Phase 2 implementation pending".
- `docker/sandbox-*.Dockerfile`: **SCAFFOLD only**.
- `docker-compose.yml`: defines volume `onsite48_sandbox` mounted at `/tmp/onsite48/sandbox` on backend; no dedicated sandbox service/container yet.

### Phase 1 runtime (present)
- `backend/app/runtime/`: loop.py, journal.py, context.py, models.py, policy.py, tools.py, prompts/.
- Workspace today: local host path `/tmp/onsite48_workspace/{run_id}` (or AGENT_WORKSPACE env). File tools (read/write/edit) are host-side Path operations with path-escape guard. `python_exec` falls back to `python_repl_secure` / `python_execute_tool`.
- Tools already exposed in loop: finish, ask_user, file_read, file_write, file_edit, python_exec (+ registry tools when present).
- No `shell_exec`, no `file_list`/`file_grep`, no `deliver_file`, no snapshot/destroy abstraction, no re-attach after worker restart via run_id.

### Python execution tools (to retire if fully replaced)
- `tools/python_repl.py`: thin E2B wrapper via SandboxManager.
- `tools/python_repl_secure.py`: present (used by runtime fallback).
- `tools/python_execute_tool.py` + chart_visualization path: local/safe-ish exec.
- Usage: runtime/tools.py `_builtin_python_exec` imports them; also registry registration elsewhere.

### Artifacts / deliverables
- `services/artifact_versions.py` (54 LOC): exists; lineage store to reuse for `deliver_file`.

### Skills
- No `backend/app/skills/` directory. No load_skill tool. No progressive disclosure in prompts.

### Evals / tests
- `backend/evals/tasks/dev/`: many YAML tasks (data-csv-*, br-*, cmp-*, etc.). Baseline overall pass@1 0.268 offline.
- `backend/tests/runtime/`: exists (Phase 1).
- No computer-tier evals that exercise a real isolated workspace yet (tiers near 0 as expected offline).

### Differences from Phase 2 prompt expectations
1. Prompt assumes k8s templates and agent_computer.py are production sandbox path; they are either scaffold or browser-only. Real production path today is E2B.
2. Phase 1 already shipped host-side file tools + local workspace dir; Phase 2 must replace with isolated backends (never execute on host).
3. No org/user skill directories yet.
4. Network policies and sandbox images are empty scaffolds — must implement.
5. docker-compose has a volume but no sandbox container service for the "dev-container" backend.

## Plan (what we will do)
1. Implement `backend/app/runtime/workspace.py`:
   - Abstract `Workspace` with create(run_id), exec, read, write, edit, list, grep, snapshot, destroy.
   - Backends: `DevContainerWorkspace` (docker exec into a long-lived sandbox container, volume per run_id) and `SandboxWorkspace` (E2B for prod, reusing manager/persistent_manager interfaces where possible; or k8s if templates filled).
   - Factory selects by `WORKSPACE_BACKEND=dev|sandbox` (default dev for local).
2. Flesh out docker sandbox image (python + pinned packages: python-docx, python-pptx, openpyxl, matplotlib, pandas, reportlab, LibreOffice headless, poppler, Node) and docker-compose sandbox service.
3. Wire new tools into runtime/tools.py: shell_exec, file_list, file_grep, deliver_file; re-implement file_* and python_exec via Workspace so nothing runs on host.
4. Skills package: `backend/app/skills/<name>/SKILL.md` + optional scripts/resources. `load_skill` tool. Progressive disclosure in system prompt. Org/user dirs with documented precedence. Initial skills written from verified procedures.
5. Sandbox escape suite (pytest) + resume-after-restart test + 12 computer-requiring dev evals + skills A/B.
6. Retire python_repl*.py only after workspace path fully replaces them and usage is checked (mark deprecated first if still referenced).
7. Update k8s scaffolds to real (minimal) templates + network policy.
8. Docs: this file, PHASE_2_REPORT.md, TARGETS.md update if needed. Branch commits per milestone.

## Risks / NOT RUN notes
- No E2B_API_KEY or k8s cluster in this environment → sandbox backend will be unit-tested with mocks; integration tests use dev-container backend only.
- Docker-in-Docker or privileged docker may be limited in the evaluation sandbox; if docker unavailable, DevContainerWorkspace falls back to a tightly constrained local process tree under a chroot-like temp dir with explicit "NOT FULLY ISOLATED — DEV ONLY" markers, and report will mark accordingly.
- Skills A/B requires running evals; will use offline/dev adapter where possible.
"""
