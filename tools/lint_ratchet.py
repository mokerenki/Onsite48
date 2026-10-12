"""Release-gate lint ratchet.

Two ESLint rules are configured as warnings with a reviewed baseline in
``frontend/eslint.config.mjs``. A warning threshold nobody enforces is just a
deleted threshold: the next person adds nineteen more ``any``s and the gate
still passes.

This fails when a ratcheted rule's count rises above its baseline, and prints
the exact per-file breakdown so the fix is obvious. Lowering a baseline is a
deliberate edit to ``BASELINES`` and shows up in review as a one-line diff.

Run directly (``python tools/lint_ratchet.py``) or via ``tools/release_gate.py``.
"""

from __future__ import annotations

import json
import subprocess
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FRONTEND = ROOT / "frontend"

# Reviewed 2026-09-27 against commit 0b1a70-era frontend. `tsc --noEmit` is
# clean, so no-explicit-any findings are API-design preference rather than type
# safety. exhaustive-deps findings are state setters (referentially stable) and
# per-render derived values (a render-cost smell), none of them live defects.
BASELINES: dict[str, int] = {
    "@typescript-eslint/no-explicit-any": 39,
    "react-hooks/exhaustive-deps": 13,
}


def _npx() -> list[str]:
    """npx is a .cmd shim on Windows and a plain binary elsewhere."""
    if sys.platform.startswith("win"):
        return ["npx.cmd"]
    return ["npx"]


def _eslint_messages() -> tuple[Counter, dict[str, Counter], int]:
    """Return (counts_by_rule, counts_by_file_and_rule, error_count)."""
    completed = subprocess.run(
        [*_npx(), "eslint", ".", "-f", "json"],
        cwd=FRONTEND,
        capture_output=True,
        text=True,
        shell=False,
        # eslint emits UTF-8; the Windows console codepage cannot decode it, and
        # a decode failure here would look like "eslint produced no output".
        encoding="utf-8",
        errors="replace",
    )
    stdout = completed.stdout or ""
    # eslint exits 1 when it reports problems, which is expected here.
    if not stdout.strip():
        raise SystemExit(
            f"eslint produced no JSON output (exit {completed.returncode}).\n"
            f"stderr: {(completed.stderr or '')[:2000]}"
        )
    try:
        report = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise SystemExit(f"could not parse eslint JSON: {exc}") from exc

    by_rule: Counter = Counter()
    by_file: dict[str, Counter] = {}
    errors = 0
    for entry in report:
        relative = Path(entry["filePath"]).name
        for message in entry.get("messages", []):
            rule = message.get("ruleId") or "(unknown)"
            by_rule[rule] += 1
            by_file.setdefault(relative, Counter())[rule] += 1
            if message.get("severity") == 2:
                errors += 1
    return by_rule, by_file, errors


def main() -> int:
    by_rule, by_file, errors = _eslint_messages()

    if errors:
        print(f"FAIL  {errors} lint error(s). These are not ratcheted; fix them.")
        for name, counts in sorted(by_file.items()):
            for rule, count in counts.items():
                if by_rule.get(rule) and rule not in BASELINES:
                    print(f"        {name}: {count} x {rule}")
        return 1

    regressions: list[str] = []
    for rule, baseline in BASELINES.items():
        actual = by_rule.get(rule, 0)
        if actual > baseline:
            regressions.append(f"        {rule}: {actual} (baseline {baseline})")
        status = "ok" if actual == baseline else ("improved" if actual < baseline else "OVER")
        print(f"  ratchet {rule}: {actual}/{baseline} [{status}]")

    if regressions:
        print("FAIL  lint ratchet regressed:")
        print("\n".join(regressions))
        print("        Either fix the new findings or deliberately lower the")
        print("        baseline in tools/lint_ratchet.py and say why in review.")
        return 1

    print("lint ratchet clean")
    return 0


if __name__ == "__main__":
    sys.exit(main())