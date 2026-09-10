#!/usr/bin/env python3
"""Diff-scoped AST mutation probe (#1287 AC9).

Windows-compatible, stdlib-only (`ast` + `subprocess`, no mutmut/cosmic-ray)
mutation probe scoped to PR-changed Python lines. Mechanically flips a small,
fixed set of operators (comparisons, boolean ops, arithmetic ops, boolean
constants) on lines a diff actually touched, re-runs a caller-supplied test
command against each mutant, and reports which mutants survived (test still
passed — meaning nothing pins that line).

Report-only: the exit code reflects whether the scan itself ran cleanly, never
a mutation-score threshold or gate. This is the mechanical backend for the
manual, per-test mutation-probe ritual in `.claude-userlevel/skills/implement/SKILL.md`
§4-TDD step 3 — it does not replace that ritual's "no score/gate" discipline,
it just automates flipping the operator instead of doing it by hand.

Usage:
    # Diff-scoped: every changed Python line between `base` and HEAD
    python scripts/diff-mutation-probe.py --base main --test-cmd "pytest tests/foo -q"

    # Explicit single-line probe (e.g. AC8's stratified manual sampling)
    python scripts/diff-mutation-probe.py --file path/to/mod.py --line 42 \\
        --test-cmd "pytest -k test_bar -q"
"""

from __future__ import annotations

import argparse
import ast
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

_CMP_FLIP: dict[type, type] = {
    ast.Eq: ast.NotEq,
    ast.NotEq: ast.Eq,
    ast.Lt: ast.GtE,
    ast.GtE: ast.Lt,
    ast.Gt: ast.LtE,
    ast.LtE: ast.Gt,
    ast.Is: ast.IsNot,
    ast.IsNot: ast.Is,
}
_BOOL_FLIP: dict[type, type] = {ast.And: ast.Or, ast.Or: ast.And}
_ARITH_FLIP: dict[type, type] = {
    ast.Add: ast.Sub,
    ast.Sub: ast.Add,
    ast.Mult: ast.Div,
    ast.Div: ast.Mult,
}


@dataclass(frozen=True)
class MutationSite:
    lineno: int
    col_offset: int
    kind: str
    desc: str


@dataclass(frozen=True)
class MutationResult:
    file: str
    lineno: int
    desc: str
    survived: bool


def parse_diff_hunks(diff_text: str) -> dict[str, set[int]]:
    """Map changed file -> set of changed line numbers in the new version.

    Only the `+` side of each hunk counts. A pure-deletion hunk (`+n,0`)
    contributes no lines — nothing survives at that spot in the new file to
    mutate. A file diffed to `/dev/null` (deletion) is skipped entirely.
    """
    changed: dict[str, set[int]] = {}
    current_file: str | None = None
    for line in diff_text.splitlines():
        if line.startswith("+++ "):
            raw = line[4:].strip()
            if raw == "/dev/null":
                current_file = None
                continue
            current_file = raw[2:] if raw[:2] in ("a/", "b/") else raw
            changed.setdefault(current_file, set())
            continue
        if line.startswith("@@") and current_file is not None:
            m = re.search(r"\+(\d+)(?:,(\d+))?", line)
            if not m:
                continue
            start = int(m.group(1))
            count = int(m.group(2)) if m.group(2) is not None else 1
            if count == 0:
                continue
            changed[current_file].update(range(start, start + count))
    return {f: lines for f, lines in changed.items() if lines}


def find_mutation_sites(source: str, target_lines: set[int]) -> list[MutationSite]:
    """Enumerate mutable operator sites on `target_lines` in `source`."""
    tree = ast.parse(source)
    sites: list[MutationSite] = []
    for node in ast.walk(tree):
        lineno = getattr(node, "lineno", None)
        if lineno not in target_lines:
            continue
        if isinstance(node, ast.Compare) and len(node.ops) == 1 and type(node.ops[0]) in _CMP_FLIP:
            sites.append(
                MutationSite(lineno, node.col_offset, "cmp", f"flip {type(node.ops[0]).__name__}")
            )
        elif isinstance(node, ast.BoolOp) and type(node.op) in _BOOL_FLIP:
            sites.append(
                MutationSite(lineno, node.col_offset, "bool", f"flip {type(node.op).__name__}")
            )
        elif isinstance(node, ast.BinOp) and type(node.op) in _ARITH_FLIP:
            sites.append(
                MutationSite(lineno, node.col_offset, "arith", f"flip {type(node.op).__name__}")
            )
        elif isinstance(node, ast.Constant) and isinstance(node.value, bool):
            sites.append(MutationSite(lineno, node.col_offset, "bool_const", f"flip {node.value}"))
    return sites


def apply_mutation(source: str, site: MutationSite) -> str:
    """Return `source` with the operator at `site` flipped. Raises ValueError if not found."""
    tree = ast.parse(source)
    mutated = False
    for node in ast.walk(tree):
        if (
            getattr(node, "lineno", None) != site.lineno
            or getattr(node, "col_offset", None) != site.col_offset
        ):
            continue
        if site.kind == "cmp" and isinstance(node, ast.Compare) and len(node.ops) == 1:
            node.ops[0] = _CMP_FLIP[type(node.ops[0])]()
            mutated = True
        elif site.kind == "bool" and isinstance(node, ast.BoolOp):
            node.op = _BOOL_FLIP[type(node.op)]()
            mutated = True
        elif site.kind == "arith" and isinstance(node, ast.BinOp):
            node.op = _ARITH_FLIP[type(node.op)]()
            mutated = True
        elif site.kind == "bool_const" and isinstance(node, ast.Constant):
            node.value = not node.value
            mutated = True
        if mutated:
            break
    if not mutated:
        raise ValueError(f"mutation site not found in source: {site}")
    ast.fix_missing_locations(tree)
    return ast.unparse(tree)


def probe_file(file_path: str, target_lines: set[int], test_cmd: str) -> list[MutationResult]:
    """Mutate each site on `target_lines` in turn, run `test_cmd`, restore the file.

    Runs `test_cmd` once against the unmutated source first. A non-zero exit there
    means the command itself is broken (wrong path, missing fixture/dependency) —
    every subsequent mutant would also exit non-zero and get misreported `killed`,
    claiming full mutation coverage while validating nothing. Raise instead of
    silently proceeding into the mutation loop on a harness that isn't sound.
    """
    path = Path(file_path)
    original = path.read_text(encoding="utf-8")
    baseline = subprocess.run(test_cmd, shell=True, capture_output=True, text=True)
    if baseline.returncode != 0:
        raise RuntimeError(
            f"baseline test run failed before any mutation was applied "
            f"(exit {baseline.returncode}) for command: {test_cmd!r} — fix the "
            "test command; mutation results against a broken baseline are meaningless"
        )
    sites = find_mutation_sites(original, target_lines)
    results: list[MutationResult] = []
    for site in sites:
        mutated_source = apply_mutation(original, site)
        path.write_text(mutated_source, encoding="utf-8")
        try:
            proc = subprocess.run(test_cmd, shell=True, capture_output=True, text=True)
            survived = proc.returncode == 0
        finally:
            path.write_text(original, encoding="utf-8")
        results.append(MutationResult(file_path, site.lineno, site.desc, survived))
    return results


def _git_diff_hunks(base: str) -> dict[str, set[int]]:
    proc = subprocess.run(
        ["git", "diff", f"{base}...HEAD", "-U0", "--", "*.py"],
        capture_output=True,
        text=True,
        check=True,
    )
    return parse_diff_hunks(proc.stdout)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--base", help="git ref to diff against HEAD (diff-scoped mode)")
    parser.add_argument("--file", dest="file", help="explicit file to probe (single-file mode)")
    parser.add_argument(
        "--line",
        dest="line",
        type=int,
        action="append",
        help="explicit line to probe (repeatable; requires --file)",
    )
    parser.add_argument(
        "--test-cmd", required=True, help="shell command to run per mutant; exit 0 = survived"
    )
    args = parser.parse_args(argv)

    if args.file:
        if not args.line:
            print("ERROR: --file requires at least one --line", file=sys.stderr)
            return 1
        targets = {args.file: set(args.line)}
    elif args.base:
        targets = _git_diff_hunks(args.base)
    else:
        print("ERROR: pass --base (diff-scoped) or --file/--line (explicit)", file=sys.stderr)
        return 1

    total = 0
    survived = 0
    for file_path, lines in targets.items():
        if not Path(file_path).exists():
            continue
        try:
            file_results = probe_file(file_path, lines, args.test_cmd)
        except RuntimeError as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            return 1
        for result in file_results:
            total += 1
            if result.survived:
                survived += 1
                print(f"SURVIVED  {result.file}:{result.lineno}  {result.desc}")
            else:
                print(f"killed    {result.file}:{result.lineno}  {result.desc}")

    print(
        f"\n{total} mutants, {survived} survived, {total - survived} killed (report-only, no gate)"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
