#!/usr/bin/env python3
"""Static audit for the submitted backend; does not import or execute project modules."""
from __future__ import annotations

import ast
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "backend"
APP = ROOT / "app"


def module_exists(module: str) -> bool:
    parts = module.split(".")
    base = ROOT.joinpath(*parts)
    return base.with_suffix(".py").exists() or (base / "__init__.py").exists()


def main() -> None:
    duplicate_definitions: dict[str, list[tuple[str, int]]] = defaultdict(list)
    unresolved_local_imports: list[tuple[str, int, str]] = []
    collected_root_tests: list[str] = []
    async_tests_without_marker: list[tuple[str, int, str]] = []
    source_files = sorted(APP.rglob("*.py"))

    for path in source_files:
        rel = path.relative_to(ROOT).as_posix()
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except SyntaxError as exc:
            print(f"SYNTAX_ERROR {rel}:{exc.lineno}: {exc.msg}")
            continue
        seen: dict[str, int] = {}
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                if node.name in seen:
                    duplicate_definitions[node.name].append((rel, seen[node.name]))
                    duplicate_definitions[node.name].append((rel, node.lineno))
                else:
                    seen[node.name] = node.lineno
            if isinstance(node, ast.ImportFrom) and node.module and node.module.startswith("app."):
                if not module_exists(node.module):
                    unresolved_local_imports.append((rel, node.lineno, node.module))

    for path in sorted(ROOT.glob("test_*.py")):
        collected_root_tests.append(path.name)

    for path in sorted((ROOT / "tests").rglob("test_*.py")):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except SyntaxError:
            continue
        rel = path.relative_to(ROOT).as_posix()
        for node in tree.body:
            if isinstance(node, ast.AsyncFunctionDef) and node.name.startswith("test_"):
                decorators = {ast.unparse(item) for item in node.decorator_list}
                if not any("asyncio" in decorator or "anyio" in decorator for decorator in decorators):
                    async_tests_without_marker.append((rel, node.lineno, node.name))

    print(f"BACKEND_SOURCE_FILES {len(source_files)}")
    print(f"ROOT_TEST_FILES {', '.join(collected_root_tests) or '-'}")
    print("DUPLICATE_TOP_LEVEL_DEFINITIONS")
    seen_entries: set[tuple[str, int]] = set()
    for name, entries in sorted(duplicate_definitions.items()):
        unique = sorted(set(entries))
        if tuple(unique) in seen_entries:
            continue
        seen_entries.add(tuple(unique))
        print(f"  {name}: " + ", ".join(f"{path}:{line}" for path, line in unique))
    print("UNRESOLVED_LOCAL_IMPORTS")
    for path, line, module in unresolved_local_imports:
        print(f"  {path}:{line}: {module}")
    print("ASYNC_TESTS_WITHOUT_ASYNC_MARKER")
    for path, line, name in async_tests_without_marker:
        print(f"  {path}:{line}: {name}")


if __name__ == "__main__":
    main()