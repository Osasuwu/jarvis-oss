"""Eval runner — checks skill quality via Supabase memory inspection.

Reads eval YAML files, checks conditions against actual Supabase state,
and produces a pass/fail report. Designed to run from Claude Code:

    python evals/run_evals.py [--skill <name>]

Results are printed as a structured report. Save to Supabase via
the memory MCP server for trend tracking.
"""

from __future__ import annotations

import argparse
import os
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import yaml

# Supabase client (reuse from memory server pattern)
try:
    from supabase import create_client
except ImportError:
    print("Error: supabase-py not installed. Run: pip install supabase")
    sys.exit(1)


def get_supabase():
    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_KEY")
    if not url or not key:
        print("Error: SUPABASE_URL and SUPABASE_KEY must be set")
        sys.exit(1)
    return create_client(url, key)


def load_eval(path: Path) -> dict:
    with open(path) as f:
        return yaml.safe_load(f)


def check_memory_exists(sb, pattern: str, expect: dict, project: str | None = None) -> tuple[bool, str]:
    """Check if memories matching pattern exist."""
    # Use ILIKE for pattern matching
    like_pattern = pattern.replace("*", "%")
    query = sb.table("memories").select("name, type, project").ilike("name", like_pattern)

    # Filter by project if specified
    if project is not None:
        query = query.eq("project", project)

    result = query.execute()
    rows = result.data or []

    # Filter by type if specified
    if "type" in expect:
        rows = [r for r in rows if r.get("type") == expect["type"]]

    if "min_count" in expect:
        if len(rows) >= expect["min_count"]:
            return True, f"Found {len(rows)} memories (need >= {expect['min_count']})"
        return False, f"Found {len(rows)} memories (need >= {expect['min_count']})"

    if rows:
        return True, f"Found {len(rows)} memories matching {pattern}"
    return False, f"No memories matching {pattern}"


def check_memory_content(sb, pattern: str, expect: dict, project: str | None = None) -> tuple[bool, str]:
    """Check memory content for expected strings."""
    like_pattern = pattern.replace("*", "%")
    query = sb.table("memories").select("name, content, project").ilike("name", like_pattern)

    # Filter by project if specified
    if project is not None:
        query = query.eq("project", project)

    result = query.execute()
    rows = result.data or []

    if not rows:
        return False, f"No memories matching {pattern}"

    if "contains" in expect:
        for row in rows:
            if expect["contains"] in (row.get("content") or ""):
                return True, f"Found '{expect['contains']}' in {row['name']}"
        return False, f"'{expect['contains']}' not found in any matching memory"

    if "any_contains" in expect:
        for row in rows:
            if expect["any_contains"] in (row.get("content") or ""):
                return True, f"Found '{expect['any_contains']}' in {row['name']}"
        return False, f"'{expect['any_contains']}' not found in any matching memory"

    if "min_length" in expect:
        for row in rows:
            content = row.get("content") or ""
            if len(content) >= expect["min_length"]:
                return True, f"Memory {row['name']} has {len(content)} chars"
        return False, f"No memory with >= {expect['min_length']} chars"

    return True, "Content check passed (no specific criteria)"


def check_uniqueness(sb, pattern: str, project: str | None = None) -> tuple[bool, str]:
    """Check that no duplicate names exist."""
    like_pattern = pattern.replace("*", "%")
    query = sb.table("memories").select("name, project").ilike("name", like_pattern)

    # Filter by project if specified
    if project is not None:
        query = query.eq("project", project)

    result = query.execute()
    names = [r["name"] for r in (result.data or [])]
    counts = Counter(names)
    dupes = [name for name, count in counts.items() if count > 1]
    if dupes:
        return False, f"Duplicate names: {dupes}"
    return True, f"All {len(names)} names unique"


def run_eval(sb, eval_data: dict, project: str | None = None) -> dict:
    """Run all cases in an eval, return results."""
    skill = eval_data["skill"]
    results = []

    for case in eval_data.get("cases", []):
        case_id = case["id"]
        check_type = case.get("check_type", "manual")
        pattern = case.get("memory_pattern", "")
        expect = case.get("expect", {})

        if check_type == "manual":
            results.append({
                "id": case_id,
                "passed": None,
                "status": "SKIP",
                "reason": f"Manual check: {case.get('note', case['description'])}",
            })
        elif check_type == "memory_exists":
            passed, reason = check_memory_exists(sb, pattern, expect, project)
            results.append({"id": case_id, "passed": passed, "status": "PASS" if passed else "FAIL", "reason": reason})
        elif check_type == "memory_content":
            passed, reason = check_memory_content(sb, pattern, expect, project)
            results.append({"id": case_id, "passed": passed, "status": "PASS" if passed else "FAIL", "reason": reason})
        elif check_type == "uniqueness":
            passed, reason = check_uniqueness(sb, pattern, project)
            results.append({"id": case_id, "passed": passed, "status": "PASS" if passed else "FAIL", "reason": reason})
        else:
            results.append({"id": case_id, "passed": None, "status": "SKIP", "reason": f"Unknown check_type: {check_type}"})

    passed = sum(1 for r in results if r["passed"] is True)
    failed = sum(1 for r in results if r["passed"] is False)
    skipped = sum(1 for r in results if r["passed"] is None)

    return {
        "skill": skill,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "total": len(results),
        "passed": passed,
        "failed": failed,
        "skipped": skipped,
        "score": f"{passed}/{passed + failed}" if (passed + failed) > 0 else "N/A",
        "cases": results,
    }


def print_report(report: dict) -> None:
    """Print a human-readable eval report."""
    print(f"\n{'=' * 50}")
    print(f"EVAL: {report['skill']}  |  Score: {report['score']}  |  {report['timestamp'][:10]}")
    print(f"{'=' * 50}")

    for case in report["cases"]:
        icon = {"PASS": "+", "FAIL": "x", "SKIP": "-"}[case["status"]]
        print(f"  [{icon}] {case['id']}: {case['reason']}")

    print(f"\nTotal: {report['passed']} passed, {report['failed']} failed, {report['skipped']} skipped")


def main():
    parser = argparse.ArgumentParser(description="Run skill evals")
    parser.add_argument("--skill", help="Run eval for a specific skill only")
    parser.add_argument("--project", default=None, help="Filter checks to specific project (default: all projects)")
    args = parser.parse_args()

    evals_dir = Path(__file__).parent
    eval_files = sorted(evals_dir.glob("*.yaml"))

    if args.skill:
        eval_files = [f for f in eval_files if f.stem == args.skill]
        if not eval_files:
            print(f"No eval found for skill: {args.skill}")
            sys.exit(1)

    sb = get_supabase()
    all_reports = []

    for eval_file in eval_files:
        eval_data = load_eval(eval_file)
        report = run_eval(sb, eval_data, project=args.project)
        print_report(report)
        all_reports.append(report)

    # Summary
    if len(all_reports) > 1:
        total_p = sum(r["passed"] for r in all_reports)
        total_f = sum(r["failed"] for r in all_reports)
        total_s = sum(r["skipped"] for r in all_reports)
        print(f"\n{'=' * 50}")
        print(f"OVERALL: {total_p} passed, {total_f} failed, {total_s} skipped")
        print(f"{'=' * 50}")


if __name__ == "__main__":
    main()
