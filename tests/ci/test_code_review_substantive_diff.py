"""Meta-test: the "Check substantive diff" step in code-review.yml.

Two regressions pinned here:

1. **Zero-match crash.** `grep -c` prints "0" AND exits 1 when nothing matches.
   The old `... | grep -cE '^[+-][^+-]' || echo 0` then APPENDED a second "0",
   making LINES the two-line string "0\n0". `echo "lines=$LINES" >> $GITHUB_OUTPUT`
   wrote a malformed second line → `##[error]Invalid format '0'` → the step and
   the whole `review` check failed. Hit by any PR touching only extensions absent
   from the glob (first observed on an all-`.sql` migration-reconciliation PR).

2. **`.sql` coverage gap.** Schema/migration PRs are code and must be reviewed;
   `.sql` was missing from the substantive-diff glob, so a pure-.sql PR was
   classified as "no code" and skipped review entirely.

Config dimension asserts the YAML; logic dimension reimplements the count→output
formatting and proves zero matches yields a single clean "0" line.
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

WORKFLOW_PATH = (
    Path(__file__).resolve().parents[2]
    / ".github"
    / "workflows"
    / "code-review.yml"
)

DIFF_STEP_ID = "diff"


def _load_steps() -> list[dict]:
    spec = yaml.safe_load(WORKFLOW_PATH.read_text(encoding="utf-8"))
    return spec["jobs"]["review"]["steps"]


def _diff_step() -> dict:
    steps = _load_steps()
    step = next((s for s in steps if s.get("id") == DIFF_STEP_ID), None)
    assert step is not None, f"Step with id='{DIFF_STEP_ID}' not found in review job"
    return step


def _diff_globs() -> list[str]:
    """Extract the quoted pathspec globs from the git diff invocation."""
    run = _diff_step()["run"]
    # The globs are the single-quoted '*.ext' tokens fed to `git diff ... --`.
    return re.findall(r"'(\*\.[a-z]+)'", run)


# --- Config dimension: pin the YAML ---


def test_diff_step_exists():
    assert _diff_step() is not None


def test_sql_in_substantive_diff_glob():
    globs = _diff_globs()
    assert "*.sql" in globs, (
        "schema/migration (.sql) PRs must count as substantive code so they get "
        f"reviewed, not silently skipped; glob was: {globs}"
    )


def test_common_code_extensions_still_covered():
    globs = _diff_globs()
    for ext in ("*.py", "*.ts", "*.yml", "*.json", "*.sh", "*.md"):
        assert ext in globs, f"{ext} dropped from substantive-diff glob: {globs}"


def test_no_double_zero_echo_pattern():
    """The `|| echo 0` doubling bug must not come back.

    On zero matches grep already prints "0"; appending another via `echo 0`
    corrupts $GITHUB_OUTPUT. The fix uses `|| true` + a `${LINES:-0}` guard.
    """
    run = _diff_step()["run"]
    # Strip comment lines — the fix documents the old `|| echo 0` bug in prose.
    code = "\n".join(
        ln for ln in run.splitlines() if not ln.lstrip().startswith("#")
    )
    assert "|| echo 0" not in code, (
        "`grep -c ... || echo 0` reintroduces the 'Invalid format 0' crash on "
        "zero matches (grep -c already emits '0' and exits 1). Use `|| true`."
    )
    assert "grep -cE" in code and "|| true" in code, (
        "expected `grep -cE ... || true` to mask grep's exit-1-on-zero-matches"
    )


# --- Logic dimension: reimplement count → output formatting ---

CODE_EXT_RE = re.compile(r"\.(py|ts|tsx|js|jsx|yaml|yml|json|sh|md|sql)$")


def _is_code_path(path: str) -> bool:
    return CODE_EXT_RE.search(path) is not None


def _format_output(match_count: int) -> tuple[str, str]:
    """Model the fixed bash: LINES is always a single clean token, HAS derived.

    Mirrors:
        LINES=$(... | grep -cE ... || true); LINES=${LINES:-0}
        HAS=$( [ "$LINES" -gt 0 ] && echo true || echo false )
    """
    lines_value = str(match_count)  # grep -c always emits exactly one integer line
    assert "\n" not in lines_value, "LINES must be a single line for $GITHUB_OUTPUT"
    has = "true" if match_count > 0 else "false"
    return lines_value, has


def test_zero_matches_is_single_clean_line():
    lines_value, has = _format_output(0)
    assert lines_value == "0"
    assert has == "false"
    # The historical bug produced "0\n0"; assert we never emit an embedded newline.
    assert "\n" not in f"lines={lines_value}"


def test_positive_matches_flag_has_code():
    lines_value, has = _format_output(7)
    assert lines_value == "7"
    assert has == "true"


def test_sql_path_is_code():
    assert _is_code_path("supabase/migrations/20260415082814_create_credential_registry.sql")
    assert _is_code_path("mcp-memory/schema.sql")


def test_non_code_path_is_not_code():
    assert not _is_code_path("docs/notes.txt")
    assert not _is_code_path("Dockerfile")
