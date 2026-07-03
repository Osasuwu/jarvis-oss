"""Meta-test for the `review-negative-claude-bot` parser in event-dispatch.yml.

Two-gate alignment (#992, milestone #52): the event-side `review_negative`
trigger must use the SAME merge-blocking predicate as the MERGE gate in
code-review.yml — emit iff the bot comment carries an all-caps
CRITICAL/MAJOR/BLOCKING severity heading (case-sensitive). The old parser keyed
on a bare `^Found N issues:` line, which was simultaneously:

  - too loose — it fired `review_negative` (→ a /rework round) on a minor-only
    advisory comment, even though that is a non-blocking PASS under the two-gate
    model, re-introducing on the event side the churn #988 removed; and
  - too tight — the plugin routinely emits severity-sectioned comments
    (`### MAJOR`, `### 🔴 BLOCKING`, #954/#956) with NO `Found N issues:` line,
    so a genuinely blocking review failed to raise `review_negative`.

This guard pins the corrected contract along the #326 two-dimension convention:
  - Config: the parser step contains the canonical block pattern (byte-identical
    to code-review.yml), is case-sensitive, drops MINOR, loosened the title
    selector off the literal `### Code review` first line, and no longer keys
    the emit decision on `Found N issues:`.
  - Logic: reimplement the emit/skip decision in Python and assert it fires on
    the blocking shapes and stays silent on the non-blocking ones.

event-dispatch.yml is event-triggered (not path-filtered), so #326 does not
strictly mandate this test — but PRD #41 calls this parser "the only fragile
string-match in the system", which earns a logic pin regardless.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
EVENT_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "event-dispatch.yml"
REVIEW_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "code-review.yml"


# -- Decision logic (mirror of the bash in the parser step) ------------------
#
# Title selector: a heading "(Claude )?Code Review" on any line, case-
# insensitive (grep -qiE). Mirrors the merge-gate selector; grep is line-
# anchored so a bare `^` is the line-start equivalent of the gate's `(^|\n)`.
TITLE_RE = re.compile(r"^#{1,6}[ \t]*(?:Claude[ \t]+)?Code[ \t]+Review", re.I | re.M)

# BLOCK signal: an all-caps CRITICAL/MAJOR/BLOCKING severity heading — the ONLY
# merge-blocking shape (two-gate, #988/#992). Byte-identical to BLOCK_RE in
# tests/ci/test_code_review_verdict_guard.py. Case-SENSITIVE (no re.I): real
# plugin severity sections are all-caps; title-case prose ("### Blocking issues
# — None", #962) must NOT match. MINOR dropped — minors never block.
#
# Locale faithfulness: Python's `[^A-Za-z0-9\n]` consumes a multibyte emoji
# rune-by-rune, so this mirror matches "### 🔴 BLOCKING". The bash grep only
# behaves the same once the step exports LC_ALL=C (test_severity_greps_run_
# under_c_locale pins that). Without LC_ALL=C the bash would diverge — match 0
# on the emoji while this mirror says 1 — so the locale pin is what keeps the
# `test_emoji_decorated_blocking_emits` expectation truthful at runtime.
BLOCK_RE = re.compile(r"^#{1,6}[^A-Za-z0-9\n]*(?:CRITICAL|MAJOR|BLOCKING)\b", re.M)

# Back-compat count derivation (blocking-heading counts).
CRIT_RE = re.compile(r"^#{1,6}[^A-Za-z0-9\n]*(?:CRITICAL|BLOCKING)\b", re.M)
MAJOR_RE = re.compile(r"^#{1,6}[^A-Za-z0-9\n]*MAJOR\b", re.M)
MINOR_RE = re.compile(
    r"^#{1,6}[^A-Za-z0-9\n]*(?:MINOR|NITPICK|LOW|INFO|MEDIUM)\b", re.M
)


def should_emit(comment_body: str) -> bool:
    """Reimplementation of the parser's emit decision.

    Returns True iff a `review_negative` event would be dispatched.
    """
    if not TITLE_RE.search(comment_body):
        return False  # not a code-review verdict comment
    if not BLOCK_RE.search(comment_body):
        return False  # no blocking severity heading → non-blocking, skip
    return True


def counts(comment_body: str) -> tuple[int, int, int, int]:
    """Returns (n_critical, n_major, n_minor, n_issues) as the step derives them."""
    n_critical = len(CRIT_RE.findall(comment_body))
    n_major = len(MAJOR_RE.findall(comment_body))
    n_minor = len(MINOR_RE.findall(comment_body))
    return n_critical, n_major, n_minor, n_critical + n_major


# -- Fixtures: real / representative comment shapes ---------------------------

PR_957_MAJOR = """\
## Claude Code Review — PR #957

### MAJOR findings

1. Regex too broad
2. OOM lane unprobed
"""

PR_956_BARE_MAJOR = """\
## Code Review — PR #956

**Verdict: APPROVE with action items.**

### MAJOR

1. Billing regex still matches non-billing 402s
"""

PR_954_BLOCKING = """\
## Code Review — PR #954

### 🔴 BLOCKING

1. SessionEnd hook drops state on crash
"""

CRITICAL_COMMENT = """\
### Code review

#### CRITICAL issues

1. Null deref on empty input
"""

# The #992 false-positive: bare "Found N issues:" with no severity heading. Old
# parser EMITTED; two-gate parser must SKIP (non-blocking).
FOUND_N_NO_SEVERITY = """\
### Code review

Found 3 issues:

1. Bug one
2. Bug two
3. Bug three
"""

MINOR_ONLY = """\
### Code review

Found 2 issues:

### MINOR

1. Naming drift
2. Stale comment
"""

CLEAN = """\
### Code review

No issues found. Checked for bugs and CLAUDE.md compliance.
"""

APPROVE_NO_BLOCKERS = """\
## Code Review — PR #962

### Verdict: APPROVE ✅

### Blocking issues — None
"""

SIMPLIFICATION = """\
### Simplification opportunities

1. Inline helper X
2. Collapse branch Y
"""

ORCHESTRATOR_DISPATCH = "Re-queued event evt_123 for the orchestrator.\n"


class TestEmitDecision:
    # --- blocking shapes EMIT ---
    def test_major_findings_section_emits(self):
        assert should_emit(PR_957_MAJOR) is True

    def test_bare_major_heading_emits(self):
        assert should_emit(PR_956_BARE_MAJOR) is True

    def test_emoji_decorated_blocking_emits(self):
        assert should_emit(PR_954_BLOCKING) is True

    def test_critical_section_emits(self):
        assert should_emit(CRITICAL_COMMENT) is True

    def test_deviant_title_with_major_emits(self):
        # Header-loosening (#992 consequence 2): a deviant title must not hide a
        # real blocker from the trigger.
        assert should_emit("## Claude Code Review — PR #960\n\n### MAJOR\n\n1. x\n") is True

    # --- non-blocking shapes SKIP ---
    def test_found_n_no_severity_skips(self):
        # The core #992 false-positive fix: bare Found-N no longer triggers.
        assert should_emit(FOUND_N_NO_SEVERITY) is False

    def test_minor_only_skips(self):
        assert should_emit(MINOR_ONLY) is False

    def test_clean_skips(self):
        assert should_emit(CLEAN) is False

    def test_approve_no_blockers_skips(self):
        assert should_emit(APPROVE_NO_BLOCKERS) is False

    def test_lowercase_severity_is_not_a_block(self):
        # Case-sensitive (mirrors #976): lowercase prose is not a severity head.
        assert should_emit("### Code review\n\n### major\n\n1. x\n") is False

    # --- non-review comments SKIP at the title gate ---
    def test_simplification_comment_skips(self):
        assert should_emit(SIMPLIFICATION) is False

    def test_orchestrator_dispatch_comment_skips(self):
        assert should_emit(ORCHESTRATOR_DISPATCH) is False

    def test_minor_section_under_review_title_does_not_block(self):
        # Even with a valid review title, MINOR alone is non-blocking.
        assert should_emit("### Code review\n\n### MINOR\n\n1. nit\n") is False


class TestCountDerivation:
    def test_major_count_set(self):
        nc, nm, nmi, ni = counts(PR_957_MAJOR)
        assert (nc, nm) == (0, 1) and ni == 1

    def test_blocking_maps_to_critical_bucket(self):
        nc, nm, nmi, ni = counts(PR_954_BLOCKING)
        assert nc == 1 and ni == 1

    def test_critical_count_set(self):
        nc, nm, nmi, ni = counts(CRITICAL_COMMENT)
        assert nc == 1 and ni == 1

    def test_blocking_branch_always_has_at_least_one_issue(self):
        # Every comment that emits must yield n_issues >= 1, else the dispatch
        # title and ISSUE_WORD logic underflow.
        for body in (PR_957_MAJOR, PR_956_BARE_MAJOR, PR_954_BLOCKING, CRITICAL_COMMENT):
            assert should_emit(body) and counts(body)[3] >= 1, body


# -- Workflow wiring ----------------------------------------------------------


@pytest.fixture(scope="module")
def parser_run() -> str:
    workflow = yaml.safe_load(EVENT_WORKFLOW.read_text(encoding="utf-8"))
    steps = workflow["jobs"]["review-negative-claude-bot"]["steps"]
    step = next(s for s in steps if s.get("name") == "Parse Claude verdict and dispatch event")
    return step["run"]


class TestParserWiring:
    def test_block_pattern_present_and_canonical(self, parser_run):
        assert r"^#{1,6}[^[:alnum:]]*(CRITICAL|MAJOR|BLOCKING)\b" in parser_run, (
            "Parser must key the emit decision on the all-caps CRITICAL/MAJOR/"
            "BLOCKING severity heading (two-gate, #992)."
        )

    def test_block_pattern_byte_identical_to_merge_gate(self, parser_run):
        # The whole point of #992: event trigger and merge gate share ONE
        # predicate. Pin them to the same literal so they cannot drift apart.
        review_run = REVIEW_WORKFLOW.read_text(encoding="utf-8")
        pattern = r"^#{1,6}[^[:alnum:]]*(CRITICAL|MAJOR|BLOCKING)\b"
        assert pattern in parser_run and pattern in review_run, (
            "Block pattern must be byte-identical in event-dispatch.yml and "
            "code-review.yml — divergence reopens the two-gate alignment gap."
        )

    def test_block_check_is_case_sensitive(self, parser_run):
        assert "grep -qE '^#{1,6}[^[:alnum:]]*(CRITICAL|MAJOR|BLOCKING)" in parser_run, (
            "Block check must be case-sensitive (grep -qE, not -qiE) so title-"
            "case prose like 'Blocking issues — None' does not false-trigger."
        )

    def test_minor_not_in_block_alternation(self, parser_run):
        assert "(CRITICAL|MAJOR|MINOR|BLOCKING)" not in parser_run, (
            "MINOR must NOT be in the blocking alternation — minors never block "
            "(two-gate, #988)."
        )

    def test_found_n_no_longer_drives_emit(self, parser_run):
        # The old emit trigger keyed on `^Found [0-9]+ issues?:` — that line is
        # the #992 false-positive source and must no longer gate the emit.
        assert "grep -oE '^Found [0-9]+ issues?:'" not in parser_run, (
            "Parser must not derive the emit decision from a bare 'Found N "
            "issues:' line (the #992 false-positive)."
        )

    def test_title_selector_is_loosened(self, parser_run):
        assert "^###[[:space:]]+Code review$" not in parser_run, (
            "Literal '### Code review' first-line selector misses deviant "
            "titles (### #957) — must be loosened to the merge-gate selector."
        )
        assert r"^#{1,6}[ \t]*(Claude[ \t]+)?Code[ \t]+Review" in parser_run, (
            "Title selector must tolerate heading level + optional 'Claude' "
            "prefix, mirroring the merge gate."
        )

    def test_block_check_precedes_count_derivation(self, parser_run):
        block_at = parser_run.index("(CRITICAL|MAJOR|BLOCKING)")
        count_at = parser_run.index("N_CRITICAL=$(grep")
        assert block_at < count_at, (
            "The blocking-heading gate must run before count derivation — "
            "counts are only meaningful once a block is confirmed."
        )

    def test_severity_greps_run_under_c_locale(self, parser_run):
        # The [^[:alnum:]] decoration class only consumes a multibyte emoji
        # ("### 🔴 BLOCKING", #954) per-byte under the C locale; under the
        # runner default LANG=C.UTF-8 it reads the emoji as one non-consumed
        # rune and the block check silently misses. `export LC_ALL=C` must be
        # set, AND it must precede the severity greps (after jq, so JSON stays
        # UTF-8 aware). Verified at runtime: LANG=C.UTF-8 → emoji match 0,
        # LC_ALL=C → emoji match 1.
        assert "export LC_ALL=C" in parser_run, (
            "Severity greps must run under LC_ALL=C — otherwise an emoji-"
            "decorated CRITICAL/MAJOR/BLOCKING heading (#954) escapes the "
            "block check under the runner's C.UTF-8 default."
        )
        assert parser_run.index("export LC_ALL=C") < parser_run.index(
            "(CRITICAL|MAJOR|BLOCKING)"
        ), "LC_ALL=C must be exported before the first severity grep."
