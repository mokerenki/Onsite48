"""The release gate: everything that must pass before this code ships.

Run it directly::

    python tools/release_gate.py

It is the gate that makes the planner refactor (Phase 6) safe. Moving roughly
800 lines out of a 2,812-line planner without an automated check means silent
behavioural regressions -- the kind that only surface in production, on a user's
Gmail account, weeks later. So the gate comes first, deliberately.

Stages, in cheapest-first order so a cheap failure is not reported after an
expensive one. Each is declared once in ``STAGES``, which is also where it
declares whether it needs a database, so selecting stages cannot silently
disable the infrastructure a stage depends on:

1. Alembic has a single head (a branch here silently skips migrations).
2. Config and security checks -- fast, and deliberately run without a database
   so a security regression is still reported under ``--skip-db``.
3. Backend tests, against a disposable database migrated from zero.
4. Model/schema parity: every mapped column exists in the migrated schema.
5. Two-organization tenant isolation, with a member who belongs to both.
6. Trace correlation: one identifier per request, carried onto every event.
7. Task status atomicity: a transition only applies to a task still in the
   status the caller saw, so a stale resume cannot revive a cancelled task.
8. Tool call idempotency: a non-idempotent write is claimed before it is made,
   so a replay returns the recorded result instead of sending a second email.
9. Authenticated WebSocket handshake: closed, never accepted, when unauthenticated.
10. Frontend typecheck.
11. Frontend tests.
12. Lint ratchet (lint findings must not increase).

The database is disposable by construction: a uniquely named database per test,
migrated from zero and dropped afterwards. No stage touches a real environment.

If a database is already supplied through ``SYNTHAI_TEST_DATABASE_URL`` it is
used as-is. Otherwise a throwaway Postgres container is started and removed
afterwards, so the gate behaves identically on a laptop and in CI.

Exit codes:

* 0 -- every selected stage passed, none skipped.
* 1 -- at least one stage failed.
* 2 -- the gate could not run (bad arguments, no database, no npx).
* 3 -- every stage that ran passed, but at least one was skipped. This is
  deliberately distinct from 0: a run with the tenant-isolation stage skipped
  has not verified tenant isolation, and reporting that as a plain pass is the
  false confidence this gate exists to prevent.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Sequence

ROOT = Path(__file__).resolve().parent.parent
BACKEND = ROOT / "backend"
FRONTEND = ROOT / "frontend"

CONTAINER_NAME = "onsite48-release-gate-db"
CONTAINER_PORT = 55433
CONTAINER_PASSWORD = "gateonly"
ADMIN_DATABASE = "postgres"

# Passed to the backend; distinct from the Alembic override, which each test
# points at its own throwaway database.
TEST_DATABASE_URL_ENV = "SYNTHAI_TEST_DATABASE_URL"


@dataclass
class StageResult:
    name: str
    passed: bool
    duration: float
    detail: str = ""
    skipped: bool = False


@dataclass
class GateReport:
    results: list[StageResult] = field(default_factory=list)

    def add(self, result: StageResult) -> None:
        self.results.append(result)

    @property
    def failed(self) -> list[StageResult]:
        return [r for r in self.results if not r.passed and not r.skipped]


class GateError(RuntimeError):
    pass


@dataclass(frozen=True)
class Stage:
    """One gate stage, declared once.

    ``needs_db`` lives here rather than in a separate expression because the two
    drifted apart once already: the database was started based on a literal
    ``"backend"`` string while stages were selected by key, so adding a
    DB-backed stage meant it could run with no database and fail on a wall of
    connection errors instead of a clear skip.
    """

    key: str
    name: str
    run: Callable[["GateReport", bool], None]
    needs_db: bool = False



def _npx() -> list[str]:
    """npx is a .cmd on Windows, and is not resolvable without the extension
    when shell=False."""
    if os.name == "nt":
        npx = shutil.which("npx.cmd") or shutil.which("npx")
        if npx is None:
            raise GateError("npx not found; install Node.js to run the frontend stages")
        return [npx]
    npx = shutil.which("npx")
    if npx is None:
        raise GateError("npx not found; install Node.js to run the frontend stages")
    return [npx]


def _docker() -> list[str]:
    docker = shutil.which("docker")
    if docker is None:
        raise GateError("docker not found; supply SYNTHAI_TEST_DATABASE_URL or --skip-db")
    return [docker]


def _run(
    command: Sequence[str],
    *,
    cwd: Path,
    env: dict[str, str] | None = None,
) -> tuple[int, str]:
    merged = {**os.environ, **(env or {})}
    completed = subprocess.run(
        list(command),
        cwd=str(cwd),
        env=merged,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        shell=False,
    )
    return completed.returncode, (completed.stdout or "") + (completed.stderr or "")


# ---------------------------------------------------------------------------
# Disposable database lifecycle
# ---------------------------------------------------------------------------


class DisposableDatabase:
    """A Postgres that exists only for the duration of the gate."""

    def __init__(self) -> None:
        self.started = False

    @property
    def url(self) -> str:
        return (
            f"postgresql+asyncpg://postgres:{CONTAINER_PASSWORD}"
            f"@127.0.0.1:{CONTAINER_PORT}/{ADMIN_DATABASE}"
        )

    def start(self) -> None:
        docker = _docker()
        code, output = _run([*docker, "rm", "-f", CONTAINER_NAME], cwd=ROOT)
        del code, output  # a missing container is the normal case

        code, output = _run(
            [
                *docker, "run", "-d",
                "--name", CONTAINER_NAME,
                "-e", "POSTGRES_USER=postgres",
                "-e", f"POSTGRES_PASSWORD={CONTAINER_PASSWORD}",
                "-e", f"POSTGRES_DB={ADMIN_DATABASE}",
                "-p", f"{CONTAINER_PORT}:5432",
                "--health-cmd", "pg_isready -U postgres",
                "--health-interval", "2s",
                "--health-retries", "30",
                "postgres:16-alpine",
            ],
            cwd=ROOT,
        )
        if code != 0:
            raise GateError(f"could not start the disposable database:\n{output}")

        self.started = True
        if not self._await_healthy(docker):
            raise GateError("the disposable database never became healthy")

    def _await_healthy(self, docker: Sequence[str], *, attempts: int = 30) -> bool:
        for _ in range(attempts):
            code, output = _run(
                [*docker, "inspect", "-f", "{{.State.Health.Status}}", CONTAINER_NAME],
                cwd=ROOT,
            )
            if code == 0 and output.strip() == "healthy":
                return True
            time.sleep(1)
        return False

    def stop(self) -> None:
        if not self.started:
            return
        docker = _docker()
        _run([*docker, "rm", "-f", CONTAINER_NAME], cwd=ROOT)
        self.started = False


# ---------------------------------------------------------------------------
# Stages
# ---------------------------------------------------------------------------


def _assert_database_reachable(url: str) -> None:
    """Fail with one clear message rather than a wall of connection errors."""
    started = time.monotonic()
    probe = (
        "import asyncio,sys\n"
        "from sqlalchemy.ext.asyncio import create_async_engine\n"
        "async def main():\n"
        "    e = create_async_engine(sys.argv[1])\n"
        "    async with e.connect() as c:\n"
        "        await c.exec_driver_sql('SELECT 1')\n"
        "    await e.dispose()\n"
        "asyncio.run(main())\n"
    )
    code, output = _run([sys.executable, "-c", probe, url], cwd=BACKEND)
    if code != 0:
        raise GateError(
            f"the database at the configured {TEST_DATABASE_URL_ENV} is not reachable:\n"
            f"{_tail(output, 8)}"
        )
    del started


def stage_single_alembic_head(report: GateReport, with_db: bool = False) -> None:
    del with_db
    started = time.monotonic()
    from alembic.config import Config as AlembicConfig
    from alembic.script import ScriptDirectory

    config = AlembicConfig(str(BACKEND / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND / "alembic"))
    script = ScriptDirectory.from_config(config)
    heads = script.get_heads()

    if len(heads) != 1:
        report.add(StageResult(
            "alembic single head", False, time.monotonic() - started,
            f"expected exactly 1 head, found {len(heads)}: {heads}",
        ))
        return

    report.add(StageResult(
        "alembic single head", True, time.monotonic() - started, f"head {heads[0]}",
    ))


def stage_backend_tests(report: GateReport, *, with_db: bool) -> None:
    started = time.monotonic()
    env: dict[str, str] = {}
    if with_db:
        env[TEST_DATABASE_URL_ENV] = os.environ[TEST_DATABASE_URL_ENV]

    code, output = _run(
        [sys.executable, "-m", "pytest", "-q"], cwd=BACKEND, env=env,
    )
    tail = _tail(output)
    report.add(StageResult(
        "backend tests", code == 0, time.monotonic() - started, tail,
    ))


def stage_backend_suite(report: GateReport, with_db: bool) -> None:
    stage_backend_tests(report, with_db=with_db)



def _pytest_stage(
    report: GateReport,
    name: str,
    files: Sequence[str],
    *,
    with_db: bool,
) -> None:
    """Run one named slice of the backend suite as its own stage.

    These slices are already covered by the full backend run, so this is not
    extra coverage. What it buys is *ordering* and *isolation*: a security
    regression is reported before a slow typecheck rather than after it, and a
    tenant-isolation failure names itself in the summary instead of hiding
    among several hundred dots.
    """
    started = time.monotonic()
    env: dict[str, str] = {}
    if with_db:
        env[TEST_DATABASE_URL_ENV] = os.environ[TEST_DATABASE_URL_ENV]
    code, output = _run(
        [sys.executable, "-m", "pytest", "-q", *files], cwd=BACKEND, env=env,
    )
    report.add(StageResult(
        name, code == 0, time.monotonic() - started, _tail(output),
    ))


def stage_config_security(report: GateReport, with_db: bool) -> None:
    del with_db  # deliberately runnable without a database
    _pytest_stage(
        report,
        "config and security checks",
        [
            "tests/test_config_database_url.py",
            "tests/test_config_environment_fail_closed.py",
            "tests/test_config_tls_transport.py",
            "tests/test_readiness_classification.py",
            "tests/test_readiness_qdrant_probe.py",
            "tests/test_phase0_security_remediation.py",
        ],
        with_db=False,
    )


def stage_model_schema_parity(report: GateReport, with_db: bool) -> None:
    _skipped_without_db(report, "model/schema parity", with_db)
    if with_db:
        _pytest_stage(
            report,
            "model/schema parity",
            ["tests/test_model_schema_parity.py"],
            with_db=True,
        )


def stage_two_organization_isolation(report: GateReport, with_db: bool) -> None:
    _skipped_without_db(report, "two-organization isolation", with_db)
    if with_db:
        _pytest_stage(
            report,
            "two-organization isolation",
            ["tests/test_tenant_isolation_two_orgs.py"],
            with_db=True,
        )


def stage_trace_correlation(report: GateReport, with_db: bool) -> None:
    _skipped_without_db(report, "trace correlation", with_db)
    if with_db:
        _pytest_stage(
            report, "trace correlation", ["tests/test_trace_correlation.py"], with_db=True
        )


def stage_task_status_atomicity(report: GateReport, with_db: bool) -> None:
    _skipped_without_db(report, "task status atomicity", with_db)
    if with_db:
        _pytest_stage(
            report,
            "task status atomicity",
            ["tests/test_task_status_atomicity.py"],
            with_db=True,
        )


def stage_tool_idempotency(report: GateReport, with_db: bool) -> None:
    _skipped_without_db(report, "tool call idempotency", with_db)
    if with_db:
        _pytest_stage(
            report,
            "tool call idempotency",
            [
                "tests/test_tool_idempotency.py",
                "tests/test_executor_idempotency_wiring.py",
            ],
            with_db=True,
        )


def stage_authenticated_websocket(report: GateReport, with_db: bool) -> None:
    _skipped_without_db(report, "authenticated websocket", with_db)
    if with_db:
        _pytest_stage(
            report,
            "authenticated websocket",
            ["tests/test_websocket_authentication.py"],
            with_db=True,
        )


def _skipped_without_db(report: GateReport, name: str, with_db: bool) -> None:
    """Report a DB-backed stage as skipped rather than quietly passing it."""
    if with_db:
        return
    report.add(StageResult(
        name, True, 0.0, f"skipped: no {TEST_DATABASE_URL_ENV}", skipped=True,
    ))


def stage_typecheck(report: GateReport, with_db: bool = False) -> None:
    del with_db
    started = time.monotonic()
    try:
        code, output = _run([*_npx(), "tsc", "--noEmit"], cwd=FRONTEND)
    except GateError as error:
        report.add(StageResult("frontend typecheck", False, time.monotonic() - started, str(error)))
        return
    report.add(StageResult(
        "frontend typecheck", code == 0, time.monotonic() - started,
        "" if code == 0 else _tail(output),
    ))


def stage_frontend_tests(report: GateReport, with_db: bool = False) -> None:
    del with_db
    started = time.monotonic()
    try:
        code, output = _run([*_npx(), "vitest", "run"], cwd=FRONTEND)
    except GateError as error:
        report.add(StageResult("frontend tests", False, time.monotonic() - started, str(error)))
        return
    report.add(StageResult(
        "frontend tests", code == 0, time.monotonic() - started,
        "" if code == 0 else _tail(output),
    ))


def stage_frontend_build(report: GateReport, with_db: bool = False) -> None:
    del with_db
    started = time.monotonic()
    try:
        code, output = _run(["pnpm", "run", "build"], cwd=FRONTEND)
    except FileNotFoundError:
        report.add(StageResult("frontend production build", False, time.monotonic() - started, "pnpm not found"))
        return
    report.add(StageResult(
        "frontend production build", code == 0, time.monotonic() - started,
        "" if code == 0 else _tail(output, 20),
    ))


def stage_extension_tests(report: GateReport, with_db: bool = False) -> None:
    del with_db
    started = time.monotonic()
    tests = ROOT / "extension" / "onsite48-bridge" / "tests"
    if not tests.exists():
        report.add(StageResult("extension protocol tests", False, time.monotonic() - started, "extension tests directory is missing"))
        return
    code, output = _run(["node", "--test", *sorted(str(path) for path in tests.glob("*.test.mjs"))], cwd=ROOT)
    report.add(StageResult(
        "extension protocol tests", code == 0, time.monotonic() - started,
        "" if code == 0 else _tail(output, 20),
    ))


def stage_smoke(report: GateReport, with_db: bool = False) -> None:
    del with_db
    started = time.monotonic()
    code, output = _run(
        [sys.executable, "scripts/smoke_audit.py"],
        cwd=ROOT,
        env={"PYTHONPATH": str(BACKEND)},
    )
    report.add(StageResult("backend smoke", code == 0, time.monotonic() - started, _tail(output, 20)))


def stage_compose_config(report: GateReport, with_db: bool = False) -> None:
    del with_db
    started = time.monotonic()
    compose = shutil.which("docker")
    if compose is None:
        report.add(StageResult("compose validation", False, time.monotonic() - started, "docker is required for compose validation"))
        return
    code, output = _run([compose, "compose", "config", "--quiet"], cwd=ROOT)
    report.add(StageResult("compose validation", code == 0, time.monotonic() - started, _tail(output, 20)))


def stage_failure_injection(report: GateReport, with_db: bool = False) -> None:
    del with_db
    started = time.monotonic()
    test_files = [
        BACKEND / "tests" / "test_task_queue.py",
        BACKEND / "tests" / "test_phase2b_task_lease.py",
        BACKEND / "tests" / "test_executor_reliability_regressions.py",
    ]
    existing = [str(path.relative_to(BACKEND)) for path in test_files if path.exists()]
    if not existing:
        report.add(StageResult("failure injection and retry", False, time.monotonic() - started, "retry/failure-injection tests are missing"))
        return
    code, output = _run([sys.executable, "-m", "pytest", "-q", *existing], cwd=BACKEND)
    report.add(StageResult("failure injection and retry", code == 0, time.monotonic() - started, _tail(output, 20)))


def stage_reproducible_digest(report: GateReport, with_db: bool = False) -> None:
    del with_db
    started = time.monotonic()
    code, output = _run([sys.executable, "scripts/reproducible_digest.py"], cwd=ROOT)
    report.add(StageResult("reproducible source digest", code == 0, time.monotonic() - started, _tail(output, 4)))


def stage_lint_ratchet(report: GateReport, with_db: bool = False) -> None:
    del with_db
    started = time.monotonic()
    code, output = _run([sys.executable, "tools/lint_ratchet.py"], cwd=ROOT)
    report.add(StageResult(
        "lint ratchet", code == 0, time.monotonic() - started, _tail(output),
    ))


STAGES: tuple[Stage, ...] = (
    Stage("migrations", "alembic single head", stage_single_alembic_head),
    Stage("security", "config and security checks", stage_config_security),
    Stage("backend", "backend tests", stage_backend_suite, needs_db=True),
    Stage("schema", "model/schema parity", stage_model_schema_parity, needs_db=True),
    Stage(
        "isolation", "two-organization isolation",
        stage_two_organization_isolation, needs_db=True,
    ),
    Stage(
        "trace", "trace correlation",
        stage_trace_correlation, needs_db=True,
    ),
    Stage(
        "atomicity", "task status atomicity",
        stage_task_status_atomicity, needs_db=True,
    ),
    Stage(
        "idempotency", "tool call idempotency",
        stage_tool_idempotency, needs_db=True,
    ),
    Stage(
        "websocket", "authenticated websocket",
        stage_authenticated_websocket, needs_db=True,
    ),
    Stage("typecheck", "frontend typecheck", stage_typecheck),
    Stage("frontend", "frontend tests", stage_frontend_tests),
    Stage("frontend-build", "frontend production build", stage_frontend_build),
    Stage("extension", "extension protocol tests", stage_extension_tests),
    Stage("smoke", "backend smoke", stage_smoke),
    Stage("compose", "compose validation", stage_compose_config),
    Stage("failure-injection", "failure injection and retry", stage_failure_injection, needs_db=True),
    Stage("digest", "reproducible source digest", stage_reproducible_digest),
    Stage("lint", "lint ratchet", stage_lint_ratchet),
)


def _tail(output: str, lines: int = 12) -> str:
    cleaned = [
        line for line in output.splitlines()
        if line.strip() and "alembic.runtime.migration" not in line
    ]
    return "\n".join(cleaned[-lines:])


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the Onsite48 release gate.")
    parser.add_argument(
        "--skip-db", action="store_true",
        help="run without a database; the DB-backed tests will be skipped",
    )
    parser.add_argument(
        "--keep-db", action="store_true",
        help="leave the disposable database container running afterwards",
    )
    parser.add_argument(
        "--only", action="append", default=[], metavar="STAGE",
        help=(
            "run only the named stage (repeatable); one of: "
            + ", ".join(stage.key for stage in STAGES)
        ),
    )
    args = parser.parse_args(argv)

    report = GateReport()
    database = DisposableDatabase()

    known = {stage.key for stage in STAGES}
    unknown = sorted(set(args.only) - known)
    if unknown:
        # A typo used to select nothing and then report "PASSED: 0 stages",
        # which reads exactly like a green gate.
        print(
            f"unknown stage(s): {', '.join(unknown)}\n"
            f"known stages: {', '.join(stage.key for stage in STAGES)}",
            file=sys.stderr,
        )
        return 2

    selected = [stage for stage in STAGES if not args.only or stage.key in set(args.only)]

    # The database is needed only if a stage that uses it will actually run.
    # This is derived from the same declaration the stages are run from, so a
    # newly added DB-backed stage cannot be selected without its infrastructure:
    # advertising a URL for a database that was never started turns a clean skip
    # into a wall of connection errors.
    needs_db = not args.skip_db and any(stage.needs_db for stage in selected)
    use_db = False

    try:
        if not args.skip_db and needs_db and os.environ.get(TEST_DATABASE_URL_ENV):
            print(f"using the database supplied via {TEST_DATABASE_URL_ENV}")
            _assert_database_reachable(os.environ[TEST_DATABASE_URL_ENV])
            use_db = True
        elif needs_db:
            print("starting a disposable database...")
            database.start()
            os.environ[TEST_DATABASE_URL_ENV] = database.url
            _assert_database_reachable(database.url)
            use_db = True
        elif args.skip_db:
            print("--skip-db: database-backed stages will be reported as skipped")

        for stage in selected:
            stage.run(report, use_db)
    except GateError as error:
        print(f"release gate could not run: {error}", file=sys.stderr)
        return 2
    finally:
        if args.keep_db and database.started:
            print(f"leaving {CONTAINER_NAME} running")
        else:
            database.stop()
        os.environ.pop(TEST_DATABASE_URL_ENV, None)

    print()
    width = max(len(r.name) for r in report.results)
    for result in report.results:
        mark = "SKIP" if result.skipped else ("PASS" if result.passed else "FAIL")
        print(f"  {mark}  {result.name.ljust(width)}  {result.duration:6.1f}s")
        if not result.passed and result.detail:
            for line in result.detail.splitlines():
                print(f"          {line}")
        elif result.skipped and result.detail:
            print(f"          {result.detail}")

    print()
    if report.failed:
        print(f"RELEASE GATE FAILED: {len(report.failed)} stage(s) failed")
        return 1

    skipped = len([r for r in report.results if r.skipped])
    if skipped:
        print(
            f"RELEASE GATE PASSED: {len(report.results) - skipped} stages "
            f"({skipped} skipped, not verified)"
        )
        return 3

    print(f"RELEASE GATE PASSED: {len(report.results)} stages")
    return 0


if __name__ == "__main__":
    sys.exit(main())