"""Canon↔live drift guard for the code-review verdict logic.

The repo-baseline canon (`scripts/repo_baseline/canon/code-review.yml`) is the
PROPAGATION template pushed to every owned repo. Its `verify-verdict` job must
encode the SAME merge-gate decision logic as the live reference workflow
(`.github/workflows/code-review.yml`), or propagated repos silently get an
outdated gate that mis-merges PRs.

This is exactly what happened pre-this-test: the canon verdict step lagged a
full generation behind live —

  - still `grep -qiE` (case-INsensitive) on the block check vs live `-qE` (#976);
  - still blocked on MINOR (pre-two-gate) vs live's CRITICAL/MAJOR/BLOCKING
    alternation (#988);
  - lacked `export LC_ALL=C` so an emoji severity heading escaped the block
    check (#996);
  - lacked the #993 freshness anchor.

Nothing compared the two, so the drift was invisible. This guard pins parity on
the load-bearing verdict patterns. When the live workflow's verdict logic
evolves, re-snapshot the canon (`scripts/repo_baseline/canon/code-review.yml`)
and this test goes green again — that's the guard working.

Structural note: the canon and live workflows are intentionally NOT byte-equal —
canon is a 3-job retry-wrapper (attempt-1 → attempt-2 → verify-verdict) with
`{{ axis }}` placeholders, live is a single `review` job. So this test compares
the *verdict-decision patterns*, not the whole file.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

from scripts.repo_baseline import Manifest, Renderer
from scripts.repo_baseline.canon import load_canon_template

REPO_ROOT = Path(__file__).resolve().parents[2]
LIVE_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "code-review.yml"

# Patterns that carry the merge-gate decision. Each must appear, verbatim, in
# BOTH the live verdict step and the rendered canon verdict step. Drift on any
# one is the class of bug this guard exists to catch.
VERDICT_INVARIANTS = [
    # case-SENSITIVE all-caps block, MINOR dropped (#976/#988)
    r"grep -qE '^#{1,6}[^[:alnum:]]*(CRITICAL|MAJOR|BLOCKING)",
    # locale fix so emoji headings are consumed byte-wise (#996)
    "export LC_ALL=C",
    # non-blocking severity pass branch (two-gate, #963)
    r"^#{1,6}[^[:alnum:]]*(MINOR|NITPICK|LOW|INFO|MEDIUM)\b",
    # "Found N issues:" recognized (non-blocking pass under two-gate, #956)
    "Found [0-9]+ issues?:",
    # "Blocking issues — None" APPROVE pass (#962)
    "blocking issues",
    # clean signal, not end-anchored
    r"^No issues found\.",
    # freshness anchor (#993)
    "headRefOid",
    ".commit.committer.date",
    ".created_at >= $head",
]

# Anti-patterns: the OLD/buggy shapes. Must appear in NEITHER verdict step.
VERDICT_ANTIPATTERNS = [
    # case-INsensitive block — the #962 false-block bug
    r"grep -qiE '^#{1,6}[^[:alnum:]]*(CRITICAL",
    # MINOR in the block alternation — pre-two-gate (#988)
    "(CRITICAL|MAJOR|MINOR|BLOCKING)",
]


def _verdict_run(run_steps: list[dict]) -> str:
    step = next(s for s in run_steps if s.get("name") == "Verify review verdict")
    return step["run"]


@pytest.fixture(scope="module")
def live_verdict_run() -> str:
    doc = yaml.safe_load(LIVE_WORKFLOW.read_text(encoding="utf-8"))
    return _verdict_run(doc["jobs"]["review"]["steps"])


@pytest.fixture(scope="module")
def canon_verdict_run() -> str:
    template = load_canon_template(".github/workflows/code-review.yml")
    assert template is not None, "canon code-review.yml template must exist"
    manifest = Manifest.from_dict(
        {
            "repo": "Osasuwu/jarvis",
            "profile": "full",
            "required_check_contexts": ["verify-verdict"],
        }
    )
    rendered = Renderer().render(template, manifest)
    doc = yaml.safe_load(rendered)
    return _verdict_run(doc["jobs"]["verify-verdict"]["steps"])


class TestCanonVerdictParity:
    @pytest.mark.parametrize("pattern", VERDICT_INVARIANTS)
    def test_invariant_present_in_canon(self, canon_verdict_run, pattern):
        assert pattern in canon_verdict_run, (
            f"Canon verdict step is missing the load-bearing pattern {pattern!r}. "
            f"Re-snapshot scripts/repo_baseline/canon/code-review.yml from the live "
            f".github/workflows/code-review.yml verdict step."
        )

    @pytest.mark.parametrize("pattern", VERDICT_INVARIANTS)
    def test_invariant_present_in_live(self, live_verdict_run, pattern):
        # If live drops a pattern, the invariant list is stale — update both.
        assert pattern in live_verdict_run, (
            f"Live verdict step no longer contains {pattern!r}. If the live "
            f"verdict logic changed intentionally, update VERDICT_INVARIANTS and "
            f"re-snapshot the canon to match."
        )

    @pytest.mark.parametrize("pattern", VERDICT_ANTIPATTERNS)
    def test_antipattern_absent_from_canon(self, canon_verdict_run, pattern):
        assert pattern not in canon_verdict_run, (
            f"Canon verdict step contains the buggy/outdated shape {pattern!r}."
        )

    @pytest.mark.parametrize("pattern", VERDICT_ANTIPATTERNS)
    def test_antipattern_absent_from_live(self, live_verdict_run, pattern):
        assert pattern not in live_verdict_run, (
            f"Live verdict step contains the buggy/outdated shape {pattern!r}."
        )

    def test_canon_block_check_runs_before_pass_checks(self, canon_verdict_run):
        run = canon_verdict_run
        block_at = run.index("(CRITICAL|MAJOR|BLOCKING)")
        for later in (r"^No issues found\.", "Found [0-9]+ issues?:",
                      "(MINOR|NITPICK|LOW|INFO|MEDIUM)"):
            assert block_at < run.index(later), (
                f"Block check must precede pass signal {later!r} so no pass can "
                f"shadow a CRITICAL/MAJOR/BLOCKING heading."
            )

    def test_canon_locale_exported_before_block(self, canon_verdict_run):
        run = canon_verdict_run
        assert run.index("export LC_ALL=C") < run.index("(CRITICAL|MAJOR|BLOCKING)"), (
            "LC_ALL=C must be exported before the first severity grep (#996)."
        )

    def test_canon_verdict_fails_closed(self, canon_verdict_run):
        assert canon_verdict_run.strip().endswith("exit 1"), (
            "Unrecognized verdict format must fail closed (exit 1), not fall "
            "through to success (cf. #957)."
        )
