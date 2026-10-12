#!/usr/bin/env python
"""Run the trajectory corpus and print a compact summary.

    python scripts/run_trajectory_eval.py [--baseline PATH] [--out PATH]
                                         [--only FAMILY] [--fail-on-regression]
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

try:
    from evals.harness import run_sync  # noqa: E402
    from evals.report import (  # noqa: E402
        DEFAULT_BASELINE,
        DEFAULT_SNAPSHOT,
        load_jsonl,
        snapshot_delta,
        summarize,
        write_snapshot,
    )
    from evals.trajectories import FAMILIES, TRAJECTORIES, family_counts  # noqa: E402
except ImportError as exc:  # pragma: no cover - legacy tool
    raise SystemExit(
        "run_trajectory_eval.py is a legacy tool and cannot run: " + 'Legacy trajectory gate: it drives the Planner that was removed in Phase 3 (evals.harness.build_planner returns planner=None) and needs evals.report.{summarize,snapshot_delta,write_snapshot,load_jsonl,DEFAULT_BASELINE} which no longer exist. Replace with a loop-runtime eval gate before re-enabling.' + f" ({exc})"
    )


def _reconfigure_streams() -> None:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            try:
                reconfigure(encoding="utf-8", errors="replace")
            except (ValueError, OSError):
                pass


def _quiet_logs() -> None:
    """Keep structlog chatter out of the summary table (CLI-local cosmetic)."""
    import structlog

    structlog.configure(
        wrapper_class=structlog.make_filtering_bound_logger(logging.ERROR),
        cache_logger_on_first_use=True,
    )


def _print_table(rows: list[list[str]], headers: list[str]) -> None:
    widths = [len(header) for header in headers]
    for row in rows:
        for index, cell in enumerate(row):
            widths[index] = max(widths[index], len(cell))
    line = "  ".join(header.ljust(widths[index]) for index, header in enumerate(headers))
    print(line)
    print("  ".join("-" * width for width in widths))
    for row in rows:
        print("  ".join(cell.ljust(widths[index]) for index, cell in enumerate(row)))


def main(argv: list[str] | None = None) -> int:
    _reconfigure_streams()
    _quiet_logs()
    parser = argparse.ArgumentParser(description="Run the G2028 trajectory evaluation corpus.")
    parser.add_argument(
        "--baseline",
        type=Path,
        default=None,
        help=f"Baseline JSONL to diff against. Defaults to {DEFAULT_BASELINE} when it exists.",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=DEFAULT_SNAPSHOT,
        help=f"Where to write this run's snapshot (default: {DEFAULT_SNAPSHOT}).",
    )
    parser.add_argument("--only", choices=FAMILIES, default=None, help="Run a single trajectory family.")
    parser.add_argument(
        "--fail-on-regression",
        action="store_true",
        help="Exit 1 when any trajectory regresses against the baseline.",
    )
    parser.add_argument("--json", action="store_true", help="Print the machine-readable summary and delta.")
    args = parser.parse_args(argv)

    cases = TRAJECTORIES
    if args.only:
        cases = tuple(case for case in TRAJECTORIES if case.family == args.only)
    if not cases:
        print(f"No trajectories matched family '{args.only}'.", file=sys.stderr)
        return 2

    trajectories = run_sync(cases)
    snapshot_path = write_snapshot(trajectories, args.out)
    summary = summarize(trajectories)

    baseline_path = args.baseline
    if baseline_path is None and DEFAULT_BASELINE.exists():
        baseline_path = DEFAULT_BASELINE
    delta = snapshot_delta(
        [trajectory.to_jsonl() for trajectory in trajectories],
        load_jsonl(baseline_path) if baseline_path else [],
    )

    if args.json:
        print(
            json.dumps(
                {
                    "summary": summary.as_dict(),
                    "delta": delta.as_dict(),
                    "snapshot": str(snapshot_path),
                    "baseline": str(baseline_path) if baseline_path else None,
                },
                indent=2,
            )
        )
    else:
        counts = family_counts()
        print(f"trajectory eval  snapshot={snapshot_path}")
        print(f"baseline={baseline_path if baseline_path else '<none: everything is new>'}")
        print()
        _print_table(
            [
                [family, str(counts[family]), str(summary.by_family.get(family, {}).get("passed", 0)),
                 str(summary.by_family.get(family, {}).get("failed", 0))]
                for family in sorted(counts)
            ],
            ["family", "total", "passed", "failed"],
        )
        print()
        print(f"total={summary.total} passed={summary.passed} failed={summary.failed}")
        if summary.failures:
            print()
            for failure in summary.failures:
                print(f"FAIL {failure['id']} :: {failure['invariant']} :: {failure['detail']}")
        print()
        print(
            f"delta improved={len(delta.improved)} regressed={len(delta.regressed)} "
            f"unchanged={len(delta.by_status('unchanged'))} new={len(delta.new)} removed={len(delta.removed)}"
        )
        for entry in delta.regressed:
            print(f"  REGRESSED {entry.id}: {entry.detail}")
        for entry in delta.improved:
            print(f"  IMPROVED  {entry.id}: {entry.detail}")
        for entry in delta.removed:
            print(f"  REMOVED   {entry.id}")

    exit_code = 0
    if summary.failed:
        exit_code = 1
    if args.fail_on_regression and (delta.regressed or delta.removed):
        exit_code = 1
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())