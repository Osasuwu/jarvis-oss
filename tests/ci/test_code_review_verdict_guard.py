"""Meta-test for the `Verify review verdict` step in code-review.yml.

Two-gate model (#988, milestone #52): the MERGE gate blocks ONLY on a real
merge-blocking finding — an all-caps CRITICAL / MAJOR / BLOCKING / MEDIUM
severity heading (MEDIUM promoted into the blocking set, #1385 follow-up: a
MEDIUM finding can genuinely corrupt state in the moment). MINOR / NITPICK /
LOW / INFO never block merge, and a bare "Found N issues:" line (no blocking
heading) no longer blocks — in practice the plugin emits severity sections for
real bugs (#963/#964/#965/#966) and the canonical "Found N issues:" has
carried only advisory LOW (#956). This unjams the #976 deadlock where
clean-but-minor PRs were rejected by the gate while the rework loop considered
them done.

#976 fold: the block check is case-SENSITIVE all-caps (`grep -qE`, not `-qiE`)
so title-case prose like "Blocking issues — None" (#962) is no longer a false
block. The real plugin severity sections are always all-caps.

Incident still guarded (PR #957): the bot posted findings under a deviant title
with "### MAJOR findings" / "### MINOR findings" sections; the old literal
`### Code review` selector treated that as "no comment → pass" and the PR
auto-merged. The selector still tolerates title variants, and a `### MAJOR`
section still fails the gate.

Two halves, per the #326 guard-test convention:
  - Config: the workflow's verdict step contains the hardened selector and
    verdict patterns, block-before-pass order, ending fail-closed.
  - Logic: reimplement the verdict decision rule in Python and assert it
    blocks/allows the scenarios the workflow claims to handle.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
REVIEW_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "code-review.yml"


# -- Decision logic (mirror of the bash in the verdict step) -----------------
#
# Selector: any heading line (1-6 #'s) that reads "[Claude] Code Review",
# case-insensitive, anywhere in the body (jq/Oniguruma `^` is line-anchored).
TITLE_RE = re.compile(r"^#{1,6}[ \t]*(?:Claude[ \t]+)?Code[ \t]+Review", re.I | re.M)

# BLOCK signal: an all-caps CRITICAL / MAJOR / BLOCKING / MEDIUM severity
# heading. This is the ONLY merge-blocking shape (two-gate model, #988).
# MEDIUM was promoted into the blocking set (#1385 follow-up) — a MEDIUM
# finding can genuinely corrupt state in the moment, unlike the
# MINOR/LOW/INFO/NITPICK advisories that stay non-blocking. MINOR is
# dropped — minors never block merge. Case-SENSITIVE (no re.I, #976): the
# real plugin severity sections are all-caps ("### MAJOR", "### 🔴
# BLOCKING"); title-case prose ("### Blocking issues — None", #962, or a
# title-case "### Medium" advisory) must NOT match. Decoration between
# the #'s and the keyword is tolerated; the class excludes \n so the decoration
# run cannot span lines under re.M (grep matches per-line).
#
# Locale faithfulness: Python's `[^A-Za-z0-9\n]` consumes a multibyte emoji
# rune-by-rune, so this mirror matches "### 🔴 BLOCKING". The bash grep only
# behaves the same once the verdict step exports LC_ALL=C — pinned by
# test_severity_greps_run_under_c_locale. Without that export the bash diverges
# (emoji match 0 while this mirror says 1), which is exactly the merge-gate hole
# the locale fix closes; the pin keeps this mirror truthful at runtime.
BLOCK_RE = re.compile(r"^#{1,6}[^A-Za-z0-9\n]*(?:CRITICAL|MAJOR|BLOCKING|MEDIUM)\b", re.M)

# Clean signal: line-start anchored, NO end anchor — the plugin spec's clean
# example is "No issues found. Checked for bugs and CLAUDE.md compliance."
# (trailing prose on the same line; an end-anchored regex fails-closed on it).
CLEAN_RE = re.compile(r"^No issues found\.", re.M)

# "No blockers" prose: the 8-reviewer APPROVE summary "Blocking issues — None"
# (#962). Case-insensitive, matched within a single line (the class excludes
# \n). Only consulted AFTER the block check, so it can never shadow a real
# severity heading.
NO_BLOCKERS_RE = re.compile(r"blocking issues\b[^A-Za-z0-9\n]*none\b", re.I)

# Findings present but none blocking: the plugin's canonical "Found N issues:"
# line (#956 carried only MEDIUM/LOW advisories). No longer a fail signal.
FOUND_RE = re.compile(r"Found [0-9]+ issues?:")

# Non-blocking severity sections: MINOR-only (#963), or MEDIUM/LOW/INFO/
# NITPICK advisories with no "Found N" line. MEDIUM STAYS in this alternation
# (#1385 follow-up) — a real all-caps "### MEDIUM" finding is already caught
# by the case-sensitive BLOCK_RE above and never reaches here; only a
# title-case "Medium" heading (decorative prose, not a real severity signal,
# same as title-case "Critical"/"Blocking") lands in this case-insensitive
# branch and stays non-blocking. Case-INSENSITIVE (#1050, re.I): the plugin
# sometimes emits title-case advisory headings ("#### Low" on PR #1049),
# which the old all-caps-only match dropped → fail-closed. Only consulted
# AFTER the case-sensitive block check, so leniency here can never shadow a
# real all-caps CRITICAL/MAJOR/BLOCKING/MEDIUM section. \b after the keyword
# keeps "### Information" / "### Lower" from matching INFO/LOW.
NONBLOCK_SEV_RE = re.compile(
    r"^#{1,6}[^A-Za-z0-9\n]*(?:MINOR|NITPICK|LOW|INFO|MEDIUM)\b", re.M | re.I
)

# Positive verdict line with no blocking severity heading: bare "LGTM" or a
# "Verdict: … APPROVE/APPROVED" summary (#1050 — PR #1049 posted
# "**Verdict: LGTM with one minor note**"). Case-insensitive; consulted only
# after the block check, so it can never green-light a real all-caps severity
# section. Covers the clean-LGTM-with-no-findings shape that carries no
# severity heading at all (which the non-block grep above would miss).
LGTM_RE = re.compile(r"\bLGTM\b|Verdict:[^\n]*\bAPPROVED?\b", re.I)

# Structured findings block (#1456). The plugin emits an HTML comment whose
# JSON gives every finding an explicit "severity". Consulted BEFORE the prose
# ladder, because it is machine-emitted: severity does not have to be inferred
# from heading shape. The prose ladder only sees line-start markdown headings,
# so when the plugin uses the numbered-list shape ("Found N issues:" + prose
# bullets) the JSON is the ONLY place severity lives — and the FOUND_RE branch
# then passes the whole comment as advisory. PR #1452 auto-merged that way with
# two MEDIUM findings.
FINDINGS_MARKER_RE = re.compile(r"<!-- *code-review-findings")
FINDINGS_BLOCK_RE = re.compile(
    r"<!-- *code-review-findings[^\n]*\n(.*?)\n-->", re.S
)
BLOCKING_SEVERITIES = frozenset({"CRITICAL", "MAJOR", "BLOCKING", "MEDIUM"})


def structured_verdict(body: str) -> str | None:
    """Mirror of the verdict step's structured-findings check (#1456).

    Returns ``'fail'`` when the block carries a blocking severity or is present
    but unparseable, and ``None`` when the block is absent or carries only
    non-blocking severities — ``None`` meaning "fall through to the prose
    ladder", which is what keeps this additive over every older comment shape.
    """
    if not FINDINGS_MARKER_RE.search(body):
        return None
    m = FINDINGS_BLOCK_RE.search(body)
    if not m:
        return "fail"  # marker present, block malformed → fail closed
    try:
        payload = json.loads(m.group(1))
    except (ValueError, TypeError):
        return "fail"
    if not isinstance(payload, dict):
        return "fail"
    severities = {
        str(f.get("severity", "")).upper()
        for f in payload.get("findings", [])
        if isinstance(f, dict)
    }
    return "fail" if severities & BLOCKING_SEVERITIES else None


def lineage_failed_runs(
    runs: list[tuple[str, str | None, str]],
    lineage_shas: list[str],
    current_run_id: str | None = None,
) -> int:
    """Count code-review workflow runs over the PR's head lineage that FAILED.

    Mirror of the #1228 head-lineage probe. Comment-absence alone cannot tell
    "the plugin legitimately declined" from "every review attempt died before it
    could post" — both look like zero comments. A failed run whose head SHA is
    one of the PR's own commits is positive evidence of the latter.

    Args:
        runs: ``(head_sha, conclusion, run_id)`` triples from
            ``actions/workflows/code-review.yml/runs``. ``conclusion`` is None
            for a still-running run.
        lineage_shas: SHAs of the PR's commits (``pulls/<n>/commits``).
        current_run_id: the run executing this check. Excluded — its own
            conclusion is not yet decided and must never self-block.
    """
    lineage = set(lineage_shas)
    return sum(
        1
        for head_sha, conclusion, run_id in runs
        if conclusion == "failure" and head_sha in lineage and run_id != current_run_id
    )


def lineage_in_flight_runs(
    runs: list[tuple[str, str | None, str]],
    lineage_shas: list[str],
    current_run_id: str | None = None,
) -> int:
    """Count code-review runs over the PR's head lineage that have NOT completed.

    Mirror of the #1434 in-flight probe. ``lineage_failed_runs`` above sees only
    runs that already CONCLUDED in failure; a review still executing has no
    conclusion at all and is therefore invisible to it. That blind spot is what
    let an auto-rebase push pass the gate while the real review was mid-flight,
    twice on 2026-08-07 (PRs #1429 and #1435 both merged unreviewed).

    Keyed on ``status != "completed"`` rather than an enumeration of in-flight
    status names (``queued`` / ``waiting`` / ``in_progress`` / ``pending``), so a
    status GitHub adds later fails closed instead of silently reading as done.

    Args:
        runs: ``(head_sha, status, run_id)`` triples from
            ``actions/workflows/code-review.yml/runs``.
        lineage_shas: SHAs of the PR's commits (``pulls/<n>/commits``).
        current_run_id: the run executing this check — always in flight by
            definition, so counting it would make every run block on itself.
    """
    lineage = set(lineage_shas)
    return sum(
        1
        for head_sha, status, run_id in runs
        if status != "completed" and head_sha in lineage and run_id != current_run_id
    )


def verdict(
    comment_bodies: list[str],
    *,
    ran: bool = False,
    lineage_failed: int = 0,
    lineage_in_flight: int = 0,
    has_code: bool | None = None,
    is_draft: bool = False,
    pr_state: str = "OPEN",
) -> str:
    """Reimplementation of the verdict step's decision rule.

    Args:
        comment_bodies: /code-review-titled comment bodies (any age).
        ran: whether the review step's execution_file was present (#1182) —
            i.e. the step was eligible and actually ran (and, per the prior
            "Verify review ran cleanly" step, ran cleanly: is_error=false, 0
            denials — otherwise the job would have already failed before this
            check runs). Only consulted when there is no selected comment: it
            disambiguates "legitimate skip" (ran=False → pass) from
            "ran-but-silent" (ran=True → fail closed, #1182).
        lineage_failed: number of FAILED code-review runs over the PR's head
            lineage (#1228). Non-zero means every review attempt died before
            posting, so "no comment" is evidence of never-reviewed, not of a
            legitimate skip. Only consulted when there is no selected comment.
        lineage_in_flight: number of code-review runs over the PR's head lineage
            that have not completed (#1434). The autobase carve-out's premise —
            "no new code, so the existing review stands" — needs an existing
            review that actually finished; a running one has verified nothing
            yet. Only consulted when there is no selected comment.
        has_code: whether the PR carries a substantive diff (the
            ``Check substantive diff`` step's ``has_code``), or None when the
            event never computed it (``workflow_dispatch``). ``True`` with no
            verdict comment means the PR was never reviewed — a completed review
            always posts one — so it fails closed (#1434 AC2). ``False`` is the
            positive evidence that a skip was legitimate; ``None`` is unknown and
            must not fail closed, or post-factum dispatch breaks.
        is_draft: whether the PR is a draft. Drafts are declined by the plugin by
            design and cannot merge, so there is no merge to gate — carved out of
            the ``has_code`` check only (#1434 AC2).
        pr_state: ``OPEN`` / ``MERGED`` / ``CLOSED``. A non-OPEN PR gates
            nothing — a post-factum ``workflow_dispatch`` re-run must not fail
            closed merely because the plugin declined to review a merged PR
            (#1228).

    Returns 'pass' (exit 0) or 'fail' (exit 1).
    """
    selected = [b for b in comment_bodies if TITLE_RE.search(b)]
    if not selected:
        # No review comment at all — three disambiguating signals, in order:
        #   1. PR not OPEN → post-factum run, nothing to gate → pass (#1228).
        #   2. lineage_failed > 0 → every review attempt over this PR's own
        #      commits errored, so the PR was NEVER reviewed → fail closed
        #      (#1228; PR #1226 auto-merged unreviewed through this hole).
        #   3. lineage_in_flight > 0 → a review over this PR's own commits is
        #      still running, so nothing has verified this head yet → fail
        #      closed (#1434; PRs #1429 and #1435 both merged through this hole).
        #   4. ran=True → eligible + ran cleanly yet posted nothing →
        #      ran-but-silent → fail closed (#1182 — PR #1179).
        #   5. has_code=True on a non-draft → the PR carries reviewable code and
        #      no verdict comment exists, with nothing failed and nothing in
        #      flight to explain it → it was never reviewed → fail closed
        #      (#1434 AC2: pass needs positive evidence, not absent failure).
        # Otherwise there was genuinely nothing to review (has_code=false /
        # draft / unknown) → legitimate skip → pass.
        if pr_state != "OPEN":
            return "pass"
        if lineage_failed > 0:
            return "fail"
        if lineage_in_flight > 0:
            return "fail"
        if ran:
            return "fail"
        if has_code and not is_draft:
            return "fail"
        return "pass"
    body = selected[-1]  # latest review comment wins
    # Structured findings block first (#1456) — authoritative severity, and the
    # only signal present at all when the plugin uses the numbered-list shape.
    if structured_verdict(body) == "fail":
        return "fail"
    # BLOCK check runs first: a pass signal must never shadow a CRITICAL/MAJOR/
    # BLOCKING heading.
    if BLOCK_RE.search(body):
        return "fail"
    # No blocking severity heading. Any recognized non-blocking shape passes.
    if CLEAN_RE.search(body):
        return "pass"
    if NO_BLOCKERS_RE.search(body):
        return "pass"
    if FOUND_RE.search(body):
        return "pass"
    if NONBLOCK_SEV_RE.search(body):
        return "pass"
    if LGTM_RE.search(body):  # #1050: explicit LGTM/APPROVE verdict
        return "pass"
    return "fail"  # unrecognized review comment — fail closed


def verdict_fresh(
    comments: list[tuple[str, str]],
    head_time: str,
    *,
    ran: bool = False,
    lineage_failed: int = 0,
    lineage_in_flight: int = 0,
    has_code: bool | None = None,
    is_draft: bool = False,
    pr_state: str = "OPEN",
) -> str:
    """Mirror of the verdict step INCLUDING the #993 freshness gate.

    A /code-review comment is a verdict only for the CURRENT head commit. The
    review action sometimes errors on turn 1 and posts nothing for the head SHA;
    a stale comment from a PRIOR head SHA must not be consumed as this head's
    verdict (it would wrongly block a clean head or wrongly pass a dirty one).

    Args:
        comments: ``(body, created_at)`` pairs, oldest→newest (issue-comments API
            order). ``created_at`` is an ISO-8601 UTC string (…Z), comparable as
            a plain string against ``head_time``.
        head_time: committer time of the current head commit (ISO-8601 UTC).
        ran: see ``verdict()`` — only consulted when there is no review comment
            at all (disambiguates skip vs. ran-but-silent, #1182).
        lineage_failed, pr_state: see ``verdict()`` — the #1228 never-reviewed
            signals, likewise only consulted when there is no review comment.
        lineage_in_flight, has_code, is_draft: see ``verdict()`` — the #1434
            in-flight and positive-evidence signals, likewise only consulted when
            there is no review comment.

    Returns 'pass' (exit 0) or 'fail' (exit 1). Disambiguation:
      - no code-review comment at all → the #1228/#1182 total==0 decision table
        (see ``verdict()``): non-OPEN PR → pass; failed head-lineage runs →
        fail; ran-but-silent → fail; otherwise legitimate skip → pass;
      - code-review comment(s) exist but ALL predate the head commit →
        'fail' (latest review errored / never posted for this SHA — #993);
      - a fresh code-review comment exists → existing two-gate content verdict.
    """
    review = [(b, t) for (b, t) in comments if TITLE_RE.search(b)]
    if not review:
        return verdict(
            [],
            ran=ran,
            lineage_failed=lineage_failed,
            lineage_in_flight=lineage_in_flight,
            has_code=has_code,
            is_draft=is_draft,
            pr_state=pr_state,
        )
    fresh = [b for (b, t) in review if t >= head_time]
    if not fresh:
        return "fail"  # only stale prior-SHA comment(s) — fail closed (#993)
    return verdict([fresh[-1]])  # latest fresh comment → content verdict


# -- Autobase carry-forward anchor (mirror of the #1134 autobase branch) -------
#
# On the jarvis-ci[bot] auto-rebase (update-branch) push the verdict step now
# RUNS — its if: no longer short-circuits on steps.autobase.outputs.skip — but
# HEAD is the bot's 2-parent merge commit. Anchoring freshness on that commit's
# committer time is an anchor-trap: no review comment is newer than a just-made
# merge commit, so a clean PR would fail-closed forever. Instead the autobase
# branch anchors on the LAST NON-BOT HEAD — the committer date of the most
# recent PR commit whose author is not jarvis-ci[bot] — so the last REAL review
# verdict is re-enforced. The walk is newest→oldest so a run of consecutive
# auto-rebases (PR #963 had 28) is skipped, not just parents[0]. A null/unlinked
# commit author counts as real (safe default). Normal path unchanged: anchor ==
# head_time.
AUTOBASE_BOT = "jarvis-ci[bot]"


def anchor_time(commits: list[tuple[str, str | None]], head_time: str, *, autobase: bool) -> str:
    """Freshness anchor for the verdict step (mirror of the #1134 bash).

    Args:
        commits: ``(committer_date, author_login)`` pairs for the PR commits,
            oldest→newest (REST ``pulls/<n>/commits`` order). ``author_login`` is
            the GitHub login or ``None`` when the commit email is unlinked.
        head_time: committer date of the current head commit.
        autobase: whether ``steps.autobase.outputs.skip == 'true'`` (the push was
            an jarvis-ci[bot] auto-rebase).

    Returns the ISO-8601 UTC string to anchor freshness on. Normal path →
    ``head_time``. Autobase path → committer date of the most recent non-bot
    commit, or ``head_time`` if the PR is (impossibly) all-bot.
    """
    if not autobase:
        return head_time
    non_bot = [c for c in commits if (c[1] or "") != AUTOBASE_BOT]
    if not non_bot:
        # No real commit at all (should not happen — every PR has a first human
        # commit). Fall back to HEAD → strict freshness → fail-closed on this
        # bizarre all-bot PR rather than pass unverified.
        return head_time
    return non_bot[-1][0]


def verdict_autobase(
    comments: list[tuple[str, str]],
    commits: list[tuple[str, str | None]],
    head_time: str,
    *,
    autobase: bool,
    ran: bool = False,
    lineage_failed: int = 0,
    lineage_in_flight: int = 0,
    has_code: bool | None = None,
    is_draft: bool = False,
    pr_state: str = "OPEN",
) -> str:
    """Mirror of the full verdict step INCLUDING the #1134 autobase branch.

    Computes the freshness anchor (``head_time`` on the normal path, the last
    non-bot head on the autobase path) then applies the SAME freshness + two-gate
    content verdict as ``verdict_fresh``. AC3 (total==0→pass/fail per ``ran``,
    #1182) and AC4 (comments exist but none fresh-for-anchor→fail-closed) fall
    out of ``verdict_fresh`` unchanged — only the anchor differs.
    """
    return verdict_fresh(
        comments,
        anchor_time(commits, head_time, autobase=autobase),
        ran=ran,
        lineage_failed=lineage_failed,
        lineage_in_flight=lineage_in_flight,
        has_code=has_code,
        is_draft=is_draft,
        pr_state=pr_state,
    )


# The literal shape that false-passed the gate on PR #957: MAJOR + MINOR
# sections. MAJOR still blocks.
PR_957_COMMENT = """\
## Claude Code Review — PR #957

Reviewed the diff against CLAUDE.md and the plugin rubric.

### MAJOR findings

1. Regex too broad in billing classifier
2. OOM lane unprobed
3. Silent npm failure
4. Missing test for alert path

### MINOR findings

1. Stale comment
2. Naming drift
3. Unused import
4. Doc typo
5. Log level
6. Magic number
"""

# Real deviant shape from PR #956: APPROVE verdict line, bare "### MAJOR"
# section heading (no "findings" suffix) — yet carried genuine major findings.
PR_956_COMMENT = """\
## Code Review — PR #956 `fix(sandcastle): address PR #956 round-2 review`

**Verdict: APPROVE with action items.**

### MAJOR

1. Billing regex still matches non-billing 402s (confidence 85)
2. OOM lane untested (confidence 82)
"""

# Real deviant shape from PR #954: emoji-decorated severity headings.
PR_954_COMMENT = """\
## Code Review — PR #954

### 🔴 BLOCKING

1. SessionEnd hook drops state on crash

### 🟡 IMPORTANT (non-blocking)

1. Model tiering table drifts from settings.json
"""

# Real shape from PR #962 (#976 deadlock): an explicit APPROVE summary whose
# "### Blocking issues — None" header is title-case. The old case-insensitive
# regex matched the title-case word "Blocking" and FALSE-BLOCKED a clean PR.
PR_962_COMMENT = """\
## Code Review — PR #962

### Verdict: APPROVE ✅

### Blocking issues — None

All findings below are explicitly non-blocking.

### Notes (non-blocking)

1. Consider renaming `foo` for clarity
"""

# Canonical findings, no severity heading — under the two-gate model this is
# NON-blocking (the bot emits severity sections for real bugs; bare Found-N has
# been advisory, #956).
CANONICAL_FINDINGS = """\
### Code review

Found 3 issues:

1. Bug one
2. Bug two
3. Bug three
"""

# A findings comment whose only severity sections are MINOR (#963 shape) — must
# PASS (AC fixture (a)/(d)).
CANONICAL_MINOR_ONLY = """\
### Code review

Found 2 issues:

### MINOR

1. Naming drift
2. Stale comment
"""

# MINOR-only deviant sections with no "Found N" line — must PASS.
MINOR_ONLY_SECTIONS = """\
## Code Review — PR #963

### MINOR

1. Prefer f-string here
2. Docstring wording
"""

# A real blocking comment used to prove latest-wins / selection (MAJOR blocks).
BLOCKING_COMMENT = """\
### Code review

### MAJOR

1. Null deref on empty input
"""

CANONICAL_CLEAN_SPEC = """\
### Code review

No issues found. Checked for bugs and CLAUDE.md compliance.

🤖 Generated with [Claude Code](https://claude.ai/code)
"""

# Real shape from PR #1049 (#1050 false-fail): an LGTM verdict line plus a
# single advisory under a TITLE-CASE "#### Low" heading. The old all-caps-only
# non-block grep dropped "#### Low" and there was no LGTM recognizer, so the
# gate fell through to fail-closed despite a clean verdict.
PR_1049_COMMENT = """\
## Code Review

**Verdict: LGTM with one minor note**

#### Low — missing `src.is_dir()` guard (line 1143)

`src.iterdir()` is called without first asserting `src.is_dir()`. Not a
blocker (full CI catches it), but easy fix if you want isolation-safe tests.

Everything else looks good.
"""

# Clean LGTM with no severity heading and no "No issues found." line — the
# verdict line is the only positive signal. Must PASS via LGTM_RE (#1050).
BARE_LGTM_COMMENT = """\
## Code Review

**Verdict: LGTM.** Clean diff, no concerns.
"""

SIMPLIFICATION_COMMENT = """\
### Simplification opportunities

1. Inline helper X
2. Collapse branch Y
"""

# --- #1456: the structured findings block ---------------------------------
# The verbatim shape that auto-merged PR #1452: numbered-list prose, no severity
# heading anywhere, and both MEDIUMs living ONLY inside the machine-readable
# block. Under the prose-only ladder this hit the "Found N issues:" branch and
# exited 0 — a required check passing a PR it was meant to block. Kept verbatim
# (permalinks trimmed) as the regression fixture: it must now FAIL.
PR_1452_COMMENT = """\
### Code review

Found 2 issues:

1. **`.claude-userlevel/settings.json:9`** — the blanket `Read/Edit(**/.env.*)`
   deny reverts the `.env.example` carve-out and blocks the `/wizard` skill's
   documented Phase 1 read.
2. **`tests/ci/test_context_extraction_guard.py:3`** — the PR body names files
   under "Files Changed" that carry no diff hunk.

<!-- code-review-findings
{
  "schema_version": 1,
  "findings": [
    {"severity": "MEDIUM", "rule": "git-blame-context",
     "file": ".claude-userlevel/settings.json", "line": 9,
     "description": "Blanket dotenv deny reverts the .env.example carve-out"},
    {"severity": "MEDIUM", "rule": "diff-coherence",
     "file": "tests/ci/test_context_extraction_guard.py", "line": 3,
     "description": "PR body names files with no diff hunk"}
  ]
}
-->
"""

# Same prose, but the block carries only advisory severities. Must fall through
# to the prose ladder — where "Found N issues:" passes it, as before.
FINDINGS_BLOCK_MINOR_ONLY = """\
### Code review

Found 2 issues:

1. Naming drift
2. Stale comment

<!-- code-review-findings
{
  "schema_version": 1,
  "findings": [
    {"severity": "MINOR", "rule": "naming", "file": "a.py", "line": 1},
    {"severity": "LOW", "rule": "comment", "file": "b.py", "line": 2}
  ]
}
-->
"""

# Marker present, payload truncated mid-object. Fails closed — same floor as the
# unrecognized-format exit at the end of the verdict step.
FINDINGS_BLOCK_MALFORMED = """\
### Code review

No issues found.

<!-- code-review-findings
{
  "schema_version": 1,
  "findings": [
    {"severity": "MEDIUM", "rule": "truncated
-->
"""

RETRY_EXHAUSTED_COMMENT = (
    "WARNING: Claude code-review auto-retry exhausted after 4 attempts.\n"
    "Re-run manually: gh workflow run code-review.yml -f pr_number=957\n"
)


class TestVerdictLogic:
    # --- blocking: CRITICAL / MAJOR / BLOCKING all-caps headings fail ---
    def test_pr_957_major_section_fails(self):
        assert verdict([PR_957_COMMENT]) == "fail"

    def test_major_findings_heading_alone_fails(self):
        assert verdict(["## Code Review\n\n### MAJOR findings\n\n1. x\n"]) == "fail"

    def test_critical_issues_heading_variant_fails(self):
        assert verdict(["## Code Review\n\n#### CRITICAL issues\n\n1. x\n"]) == "fail"

    def test_pr_956_bare_major_heading_fails(self):
        assert verdict([PR_956_COMMENT]) == "fail"

    def test_pr_954_emoji_decorated_blocking_heading_fails(self):
        assert verdict([PR_954_COMMENT]) == "fail"

    def test_bare_major_heading_with_stray_clean_line_fails(self):
        # A stray "No issues found." line must not shadow a "### MAJOR" section.
        body = "## Code Review\n\nNo issues found.\n\n### MAJOR\n\n1. x\n"
        assert verdict([body]) == "fail"

    def test_major_heading_with_stray_no_blockers_line_fails(self):
        # "Blocking issues — None" prose must not shadow a real "### MAJOR".
        body = "## Code Review\n\n### Blocking issues — None\n\n### MAJOR\n\n1. real bug\n"
        assert verdict([body]) == "fail"

    def test_severity_decoration_does_not_span_lines(self):
        # "###" alone on a line followed by prose mentioning MAJOR is not a
        # severity heading (grep matches per-line; the mirror must agree).
        body = "## Code Review\n\n###\nMAJOR refactor suggested someday.\n\nNo issues found.\n"
        assert verdict([body]) == "pass"

    # --- minors / advisories never block (two-gate, #988) ---
    def test_minor_findings_heading_alone_passes(self):
        # WAS fail under the old gate — the core #976 unjam.
        assert verdict(["## Code Review\n\n### MINOR findings\n\n1. x\n"]) == "pass"

    def test_minor_only_sections_no_found_line_passes(self):
        assert verdict([MINOR_ONLY_SECTIONS]) == "pass"

    def test_canonical_minor_only_passes(self):
        assert verdict([CANONICAL_MINOR_ONLY]) == "pass"

    def test_low_info_nitpick_headings_pass(self):
        for sev in ("LOW", "INFO", "NITPICK"):
            body = f"## Code Review\n\n### {sev}\n\n1. advisory note\n"
            assert verdict([body]) == "pass", sev

    # --- MEDIUM now blocks (#1385 follow-up: a MEDIUM finding can genuinely
    # corrupt state in the moment, unlike MINOR/LOW/INFO/NITPICK) ---
    def test_allcaps_medium_heading_fails(self):
        assert verdict(["## Code Review\n\n### MEDIUM\n\n1. real issue\n"]) == "fail"

    def test_titlecase_nonblocking_severity_headings_pass(self):
        # #1050: the plugin sometimes title-cases advisory headings ("#### Low"
        # on PR #1049). These are non-blocking and must PASS, not fail-closed.
        for sev in ("Low", "Minor", "Info", "Nitpick", "Medium"):
            body = f"## Code Review\n\n#### {sev} — advisory\n\n1. note\n"
            assert verdict([body]) == "pass", sev

    def test_pr_1049_titlecase_low_plus_lgtm_passes(self):
        # The exact #1049 false-fail shape: "Verdict: LGTM" + "#### Low".
        assert verdict([PR_1049_COMMENT]) == "pass"

    def test_bare_lgtm_verdict_passes(self):
        # LGTM with no severity heading and no "No issues found." line.
        assert verdict([BARE_LGTM_COMMENT]) == "pass"

    def test_lgtm_does_not_shadow_a_real_major(self):
        # A "Verdict: LGTM" line must NOT green-light a coexisting all-caps
        # "### MAJOR" — the case-sensitive block check runs first.
        body = "## Code Review\n\n**Verdict: LGTM**\n\n### MAJOR\n\n1. real bug\n"
        assert verdict([body]) == "fail"

    def test_lowercase_severity_keyword_is_not_a_block(self):
        # Case-sensitive block keyword (#976): lowercase prose is not a real
        # severity section. Falls through to fail-closed (no recognized shape),
        # which is the conservative outcome — not a block masquerading as one.
        body = "## Code Review\n\n### blocking issues — none\n"
        # "blocking issues — none" matches the (case-insensitive) no-blockers
        # prose signal, so this clean APPROVE passes.
        assert verdict([body]) == "pass"

    # --- "Blocking issues — None" / APPROVE (#962, #976) ---
    def test_pr_962_approve_no_blockers_passes(self):
        assert verdict([PR_962_COMMENT]) == "pass"

    def test_titlecase_blocking_none_header_passes(self):
        assert verdict(["## Code Review\n\n### Blocking issues — None\n"]) == "pass"

    # --- canonical findings with no severity heading: non-blocking ---
    def test_canonical_findings_no_severity_passes(self):
        # Two-gate decision: a bare "Found N issues:" with no CRITICAL/MAJOR/
        # BLOCKING heading does NOT block (residual risk accepted per #988).
        assert verdict([CANONICAL_FINDINGS]) == "pass"

    # --- clean ---
    def test_canonical_clean_with_spec_trailing_prose_passes(self):
        assert verdict([CANONICAL_CLEAN_SPEC]) == "pass"

    def test_bare_no_issues_found_passes(self):
        assert verdict(["### Code review\n\nNo issues found.\n"]) == "pass"

    # --- selector behavior ---
    def test_no_comments_passes(self):
        assert verdict([]) == "pass"

    # --- #1182: ran-but-silent vs. legitimate skip (total==0 disambiguation) ---
    def test_no_comments_and_ran_fails_closed(self):
        # Review step was eligible, ran cleanly (execution_file present, prior
        # step already confirmed is_error=false / 0 denials), yet posted zero
        # comments. Silent no-review — must fail closed (PR #1179 incident).
        assert verdict([], ran=True) == "fail"

    def test_no_comments_and_not_ran_passes(self):
        # Review step never ran (ineligible: draft / has_code=false / autobase
        # skip) — no execution_file at all. Legitimate skip — pass.
        assert verdict([], ran=False) == "pass"

    def test_unrelated_comments_only_passes(self):
        assert verdict(["LGTM!", RETRY_EXHAUSTED_COMMENT, "merge train queued"]) == "pass"

    def test_simplification_comment_is_not_selected(self):
        # Informational-only by design (see event-dispatch.yml) — must never
        # gate the merge, even though it lists numbered opportunities.
        assert verdict([SIMPLIFICATION_COMMENT]) == "pass"

    def test_deviant_title_clean_body_passes(self):
        assert verdict(["## Claude Code Review — PR #960\n\nNo issues found.\n"]) == "pass"

    def test_title_selector_is_case_insensitive(self):
        # A no-severity findings comment now PASSES (non-blocking).
        assert verdict(["### code review\n\nFound 1 issue:\n\n1. x\n"]) == "pass"
        assert verdict(["## CODE REVIEW\n\nNo issues found.\n"]) == "pass"

    def test_title_heading_mid_body_is_selected(self):
        # Deviant bots may prepend preamble — the heading need not be line 1.
        # Prove selection with a blocking comment (selection → fail).
        body = "Review complete, summary below.\n\n## Code Review\n\n### MAJOR\n\n1. x\n"
        assert verdict([body]) == "fail"

    def test_latest_review_comment_wins(self):
        # latest blocking → fail; latest clean → pass.
        assert verdict([BLOCKING_COMMENT, CANONICAL_CLEAN_SPEC]) == "pass"
        assert verdict([CANONICAL_CLEAN_SPEC, BLOCKING_COMMENT]) == "fail"
        # Non-review comments in between don't affect selection.
        assert verdict([BLOCKING_COMMENT, "thanks, reworking", CANONICAL_CLEAN_SPEC]) == "pass"

    # --- #1456: structured findings block is authoritative -------------------
    def test_pr_1452_structured_medium_findings_fail(self):
        # The regression fixture. Prose says only "Found 2 issues:", so the old
        # ladder passed it and the PR auto-merged; the JSON says MEDIUM twice.
        assert verdict([PR_1452_COMMENT]) == "fail"

    def test_structured_block_blocks_every_blocking_severity(self):
        for sev in ("CRITICAL", "MAJOR", "BLOCKING", "MEDIUM", "medium", "Major"):
            body = (
                "### Code review\n\nFound 1 issue:\n\n1. x\n\n"
                "<!-- code-review-findings\n"
                '{"findings": [{"severity": "%s"}]}\n'
                "-->\n" % sev
            )
            assert verdict([body]) == "fail", sev

    def test_structured_block_with_only_advisories_falls_through(self):
        # Non-blocking severities do not block; the prose ladder still decides.
        assert verdict([FINDINGS_BLOCK_MINOR_ONLY]) == "pass"

    def test_structured_block_advisory_does_not_shadow_prose_block(self):
        # A MINOR-only JSON block must not green-light a real "### MAJOR".
        body = (
            "### Code review\n\n### MAJOR\n\n1. real bug\n\n"
            "<!-- code-review-findings\n"
            '{"findings": [{"severity": "MINOR"}]}\n'
            "-->\n"
        )
        assert verdict([body]) == "fail"

    def test_structured_block_malformed_fails_closed(self):
        # Marker present but unparseable — never infer "clean" from a broken
        # machine block, even when the prose says "No issues found."
        assert verdict([FINDINGS_BLOCK_MALFORMED]) == "fail"

    def test_absent_block_leaves_every_prose_branch_unchanged(self):
        # The additive guarantee: no marker ⇒ identical to pre-#1456 behavior.
        for body, want in (
            (CANONICAL_FINDINGS, "pass"),
            (CANONICAL_CLEAN_SPEC, "pass"),
            (CANONICAL_MINOR_ONLY, "pass"),
            (PR_1049_COMMENT, "pass"),
            (BARE_LGTM_COMMENT, "pass"),
            (PR_962_COMMENT, "pass"),
            (PR_954_COMMENT, "fail"),
            (PR_956_COMMENT, "fail"),
            (PR_957_COMMENT, "fail"),
            ("## Code Review\n\nShip it.\n", "fail"),
        ):
            assert "code-review-findings" not in body
            assert verdict([body]) == want, body[:40]

    def test_empty_findings_array_does_not_block(self):
        body = (
            "### Code review\n\nNo issues found.\n\n"
            "<!-- code-review-findings\n"
            '{"schema_version": 1, "findings": []}\n'
            "-->\n"
        )
        assert verdict([body]) == "pass"

    # --- fail-closed ---
    def test_unrecognized_review_comment_fails_closed(self):
        assert verdict(["## Code Review\n\nEverything looks great! Ship it.\n"]) == "fail"

    def test_empty_verdict_section_fails_closed(self):
        assert verdict(["### Code review\n"]) == "fail"


class TestFreshnessLogic:
    """#993: a comment is a verdict only for the current head SHA.

    The review action errors on turn 1 sometimes (is_error, ~$0, num_turns=1)
    and posts NO comment for the head SHA. Without anchoring on the head commit
    time the verdict step consumes a STALE prior-SHA comment — false-blocking a
    clean head (stale MAJOR) or false-PASSING a dirty head (stale clean). The
    second direction is the dangerous one: it auto-merges code the plugin never
    reviewed. The gate fails closed when no comment is fresh for the head.
    """

    HEAD = "2026-06-18T12:00:00Z"  # current head commit's committer time
    OLD = "2026-06-17T00:00:00Z"  # a comment from a prior head SHA
    NEW = "2026-06-18T12:30:00Z"  # a comment posted for the current head SHA

    def test_stale_major_only_fails_closed(self):
        # False-BLOCK direction: a stale MAJOR from a prior SHA must NOT be
        # consumed as this head's verdict. With no fresh comment → fail closed.
        assert verdict_fresh([(BLOCKING_COMMENT, self.OLD)], self.HEAD) == "fail"

    def test_stale_clean_only_fails_closed(self):
        # False-PASS direction (the dangerous one, #993): a stale "No issues
        # found." from a prior SHA must NOT pass the current (possibly dirty)
        # head. Pre-fix this returned a PASS and auto-merged unreviewed code.
        assert verdict_fresh([(CANONICAL_CLEAN_SPEC, self.OLD)], self.HEAD) == "fail"

    def test_fresh_clean_passes(self):
        assert verdict_fresh([(CANONICAL_CLEAN_SPEC, self.NEW)], self.HEAD) == "pass"

    def test_fresh_major_fails(self):
        assert verdict_fresh([(BLOCKING_COMMENT, self.NEW)], self.HEAD) == "fail"

    def test_no_review_comment_at_all_passes(self):
        # Plugin legitimately skipped — no regression vs. the pre-#993 behavior.
        assert verdict_fresh([("LGTM, merging", self.NEW)], self.HEAD) == "pass"
        assert verdict_fresh([], self.HEAD) == "pass"

    # --- #1182: ran-but-silent vs. legitimate skip (total==0 disambiguation) ---
    def test_no_review_comment_and_ran_fails_closed(self):
        assert verdict_fresh([], self.HEAD, ran=True) == "fail"

    def test_no_review_comment_and_not_ran_passes(self):
        assert verdict_fresh([], self.HEAD, ran=False) == "pass"

    def test_comment_at_exactly_head_time_is_fresh(self):
        # created_at == head_time is treated as fresh (>=), not stale.
        assert verdict_fresh([(CANONICAL_CLEAN_SPEC, self.HEAD)], self.HEAD) == "pass"

    def test_fresh_wins_over_stale(self):
        # Stale MAJOR + fresh clean → use the fresh comment → pass (the stale
        # MAJOR from the prior SHA is correctly ignored).
        assert (
            verdict_fresh(
                [(BLOCKING_COMMENT, self.OLD), (CANONICAL_CLEAN_SPEC, self.NEW)],
                self.HEAD,
            )
            == "pass"
        )
        # Stale clean + fresh MAJOR → use the fresh comment → fail.
        assert (
            verdict_fresh(
                [(CANONICAL_CLEAN_SPEC, self.OLD), (BLOCKING_COMMENT, self.NEW)],
                self.HEAD,
            )
            == "fail"
        )

    def test_latest_fresh_comment_wins_among_fresh(self):
        # Two fresh comments: the newest (last) decides, matching the bash
        # `map(select(.created_at >= $head)) | .[-1].body`.
        early_fresh = "2026-06-18T12:10:00Z"
        late_fresh = "2026-06-18T12:40:00Z"
        assert (
            verdict_fresh(
                [(BLOCKING_COMMENT, early_fresh), (CANONICAL_CLEAN_SPEC, late_fresh)],
                self.HEAD,
            )
            == "pass"
        )


class TestAutobaseAnchorLogic:
    """#1134: on the autobase (jarvis-ci[bot] update-branch) push the verdict
    step now RUNS and anchors freshness on the last non-bot head — not the bot's
    2-parent merge commit. Anchoring on the merge commit is an anchor-trap (no
    review comment is newer than a just-made merge commit → a clean PR would
    fail-closed forever; and #1131's live CRITICAL, posted for the last real
    head, would be dropped as stale). AC2-AC6.
    """

    # --- #1131 timeline (AC6), all 2026-07-08 UTC ---
    CRITICAL_COMMENT = "## Code Review — PR #1131\n\n### CRITICAL\n\n1. Data-loss on empty batch\n"
    # commits oldest→newest: last real head, then the bot 2-parent merge (HEAD).
    PR_1131_COMMITS = [
        ("2026-07-08T06:00:52Z", "Osasuwu"),  # 45e17d61b — last real head
        ("2026-07-08T06:14:50Z", AUTOBASE_BOT),  # ef39b0a86 — bot merge (HEAD)
    ]
    PR_1131_HEAD = "2026-07-08T06:14:50Z"  # bot merge committer time
    PR_1131_COMMENTS = [
        (CRITICAL_COMMENT, "2026-07-08T05:51:50Z"),  # stale (< 06:00:52 anchor)
        (CRITICAL_COMMENT, "2026-07-08T06:14:38Z"),  # fresh (>= anchor) — selected
    ]

    def test_pr_1131_autobase_anchors_on_last_real_head_and_blocks(self):
        # AC6: the 06:14:57 autobase run must BLOCK. Carry-forward anchor =
        # 06:00:52 → the 06:14:38 CRITICAL is fresh → block-first grep → fail.
        assert (
            verdict_autobase(
                self.PR_1131_COMMENTS,
                self.PR_1131_COMMITS,
                self.PR_1131_HEAD,
                autobase=True,
            )
            == "fail"
        )

    def test_pr_1131_head_anchor_would_have_masked_the_critical(self):
        # Why not anchor on HEAD: the bot merge commit (06:14:50) is newer than
        # BOTH CRITICAL comments, so a naive head anchor finds nothing fresh.
        # (Combined with the OLD if-gate the step was skipped entirely — either
        # way the live CRITICAL went un-enforced and #1131 auto-merged.)
        assert verdict_fresh(self.PR_1131_COMMENTS, self.PR_1131_HEAD) == "fail"

    # --- AC3: autobase, zero review comments → pass (do not fail-close) ---
    def test_autobase_no_review_comment_passes(self):
        commits = [
            ("2026-07-01T10:00:00Z", "Osasuwu"),
            ("2026-07-01T11:00:00Z", AUTOBASE_BOT),
        ]
        head = "2026-07-01T11:00:00Z"
        assert verdict_autobase([], commits, head, autobase=True) == "pass"

    # --- #1182: ran-but-silent must fail closed on the autobase path too ---
    def test_autobase_no_review_comment_and_ran_fails_closed(self):
        commits = [
            ("2026-07-01T10:00:00Z", "Osasuwu"),
            ("2026-07-01T11:00:00Z", AUTOBASE_BOT),
        ]
        head = "2026-07-01T11:00:00Z"
        assert verdict_autobase([], commits, head, autobase=True, ran=True) == "fail"
        # non-review comments only (no code-review title) also pass
        assert (
            verdict_autobase(
                [("merge-train queued", "2026-07-01T11:30:00Z")],
                commits,
                head,
                autobase=True,
            )
            == "pass"
        )

    # --- AC4 / CRITIC Risk #1: stale clean before the last real head → fail ---
    def test_autobase_stale_clean_before_last_real_head_fails_closed(self):
        commits = [
            ("2026-07-01T10:00:00Z", "Osasuwu"),  # older real commit
            ("2026-07-01T12:00:00Z", "Osasuwu"),  # LAST real head → anchor
            ("2026-07-01T13:00:00Z", AUTOBASE_BOT),  # autobase (HEAD)
        ]
        # Clean verdict from 11:00 — after the OLDER real commit but BEFORE the
        # last real head (12:00). Must not be resurrected → fail closed. Pins
        # "anchor on the LAST real head", not the first.
        stale = [(CANONICAL_CLEAN_SPEC, "2026-07-01T11:00:00Z")]
        assert verdict_autobase(stale, commits, "2026-07-01T13:00:00Z", autobase=True) == "fail"

    # --- AC5 + autobase-clean-fresh: walk past consecutive autobases ---
    def test_autobase_consecutive_rebases_anchor_on_last_real_head(self):
        # PR #963 shape: a run of consecutive bot rebases. The anchor must walk
        # back past ALL of them (not parents[0]) to the last real head, so a
        # clean review of that head still passes.
        commits = [
            ("2026-07-01T10:00:00Z", "Osasuwu"),  # real head, reviewed clean
            ("2026-07-01T11:00:00Z", AUTOBASE_BOT),  # autobase 1
            ("2026-07-01T12:00:00Z", AUTOBASE_BOT),  # autobase 2
            ("2026-07-01T13:00:00Z", AUTOBASE_BOT),  # autobase 3 (HEAD)
        ]
        clean_for_real_head = [(CANONICAL_CLEAN_SPEC, "2026-07-01T10:30:00Z")]
        assert (
            verdict_autobase(clean_for_real_head, commits, "2026-07-01T13:00:00Z", autobase=True)
            == "pass"
        )
        # Anchor-trap proof: naive head anchoring (13:00) drops the 10:30 clean
        # comment → fail-closed on a legitimately clean PR.
        assert verdict_fresh(clean_for_real_head, "2026-07-01T13:00:00Z") == "fail"

    # --- anchor_time unit behaviour ---
    def test_anchor_time_normal_path_is_head(self):
        assert (
            anchor_time(
                [("2026-07-01T10:00:00Z", "Osasuwu")],
                "2026-07-01T12:00:00Z",
                autobase=False,
            )
            == "2026-07-01T12:00:00Z"
        )

    def test_anchor_time_autobase_walks_back_past_bots(self):
        commits = [
            ("2026-07-01T10:00:00Z", "Osasuwu"),
            ("2026-07-01T11:00:00Z", AUTOBASE_BOT),
            ("2026-07-01T12:00:00Z", AUTOBASE_BOT),
        ]
        assert anchor_time(commits, "2026-07-01T12:00:00Z", autobase=True) == "2026-07-01T10:00:00Z"

    def test_anchor_time_null_author_counts_as_real(self):
        # An unlinked/null GitHub author is treated as non-bot (safe default).
        commits = [
            ("2026-07-01T10:00:00Z", None),
            ("2026-07-01T11:00:00Z", AUTOBASE_BOT),
        ]
        assert anchor_time(commits, "2026-07-01T11:00:00Z", autobase=True) == "2026-07-01T10:00:00Z"

    def test_anchor_time_all_bot_falls_back_to_head(self):
        assert (
            anchor_time(
                [("2026-07-01T11:00:00Z", AUTOBASE_BOT)],
                "2026-07-01T11:00:00Z",
                autobase=True,
            )
            == "2026-07-01T11:00:00Z"
        )

    # --- AC1 sanity: normal path via the mirror == verdict_fresh (unchanged) ---
    def test_normal_path_matches_verdict_fresh(self):
        commits = [
            ("2026-07-01T10:00:00Z", "Osasuwu"),
            ("2026-07-01T12:00:00Z", "Osasuwu"),
        ]
        head = "2026-07-01T12:00:00Z"
        for comments in (
            [(CANONICAL_CLEAN_SPEC, head)],
            [(BLOCKING_COMMENT, head)],
            [(CANONICAL_CLEAN_SPEC, "2026-07-01T09:00:00Z")],  # stale → fail
            [],
        ):
            assert verdict_autobase(comments, commits, head, autobase=False) == verdict_fresh(
                comments, head
            )


class TestNeverReviewedLineageLogic:
    """#1228: "no verdict comment" must not be read as "plugin skipped".

    Both of these produce zero /code-review comments for the current SHA:
      (a) the plugin legitimately declined (draft / no substantive code /
          autobase push / already-merged PR) — nothing to gate, pass;
      (b) every real review attempt DIED before it could post (is_error,
          permission denials) — the PR was never reviewed, block.

    PR #1226 (2026-07-21) is case (b): all three plugin runs failed on
    permission_denials, then an jarvis-ci[bot] auto-rebase push skipped review
    as designed, the gate found no comment, concluded "likely skipped → pass",
    went green, and auto-merge shipped the PR un-reviewed. No admin bypass —
    the gate genuinely passed on a PR nothing had ever reviewed.

    The discriminator is the PR's HEAD LINEAGE: did any code-review run over
    this PR's own commits conclude in failure?
    """

    HEAD = "2026-07-21T12:00:00Z"
    SHAS = ["sha-a", "sha-b", "sha-head"]

    # --- lineage_failed_runs() probe ---
    def test_failed_run_on_a_pr_commit_is_counted(self):
        runs = [("sha-b", "failure", "1001")]
        assert lineage_failed_runs(runs, self.SHAS) == 1

    def test_failed_run_outside_the_lineage_is_ignored(self):
        # Another PR's failing review must never block this one.
        runs = [("sha-of-other-pr", "failure", "1001")]
        assert lineage_failed_runs(runs, self.SHAS) == 0

    def test_successful_and_in_progress_runs_are_not_failures(self):
        # This probe answers "did an attempt die?", not "has this head been
        # verified?". An in-flight run is not a dead attempt — but it is not a
        # completed review either, which is the blind spot #1434 closes with a
        # separate probe (see TestInFlightLineageLogic). Do not widen this one.
        runs = [("sha-a", "success", "1001"), ("sha-head", None, "1002")]
        assert lineage_failed_runs(runs, self.SHAS) == 0

    def test_current_run_is_excluded(self):
        # The run executing this check has no conclusion yet; excluding it by id
        # keeps a re-run from ever self-blocking on its own prior attempt row.
        runs = [("sha-head", "failure", "9999")]
        assert lineage_failed_runs(runs, self.SHAS, current_run_id="9999") == 0
        assert lineage_failed_runs(runs, self.SHAS, current_run_id="1234") == 1

    def test_multiple_failures_counted(self):
        runs = [
            ("sha-a", "failure", "1001"),
            ("sha-b", "failure", "1002"),
            ("sha-head", "failure", "1003"),
        ]
        assert lineage_failed_runs(runs, self.SHAS) == 3

    # --- AC2: the #1226 scenario blocks ---
    def test_pr_1226_all_review_runs_failed_zero_comments_blocks(self):
        # Autobase push, no comment ever posted, three dead review runs on the
        # PR's own commits. This is the exact state that shipped #1226.
        assert (
            verdict_autobase(
                [],
                [(self.HEAD, "Osasuwu"), ("2026-07-21T12:30:00Z", AUTOBASE_BOT)],
                "2026-07-21T12:30:00Z",
                autobase=True,
                ran=False,
                lineage_failed=3,
            )
            == "fail"
        )

    def test_pr_1226_would_have_passed_without_the_lineage_signal(self):
        # Same state minus the lineage evidence — the pre-#1228 gate. Proves
        # the fix is load-bearing, not decorative.
        assert (
            verdict_autobase(
                [],
                [(self.HEAD, "Osasuwu"), ("2026-07-21T12:30:00Z", AUTOBASE_BOT)],
                "2026-07-21T12:30:00Z",
                autobase=True,
                ran=False,
                lineage_failed=0,
            )
            == "pass"
        )

    # --- AC3: genuine skips still pass ---
    def test_genuine_skip_clean_lineage_passes(self):
        assert verdict([], ran=False, lineage_failed=0) == "pass"

    def test_autobase_push_clean_lineage_passes(self):
        assert (
            verdict_autobase(
                [],
                [(self.HEAD, "Osasuwu"), ("2026-07-21T12:30:00Z", AUTOBASE_BOT)],
                "2026-07-21T12:30:00Z",
                autobase=True,
                lineage_failed=0,
            )
            == "pass"
        )

    # --- AC4: post-factum dispatch on a merged/closed PR ---
    @pytest.mark.parametrize("state", ["MERGED", "CLOSED"])
    def test_non_open_pr_with_zero_comments_passes(self, state):
        # workflow_dispatch re-run on a merged PR: the plugin correctly declines
        # to review it and posts nothing. Failing closed there is a false fail —
        # there is no merge left to gate (reproduced on run 29986227927).
        assert verdict([], ran=True, pr_state=state) == "pass"

    def test_non_open_pr_passes_even_with_a_dirty_lineage(self):
        # #1226 itself: merged, and its lineage is full of failed runs. The
        # post-factum re-run still must not go red — the block is only
        # meaningful while the PR can still be merged.
        assert verdict([], ran=True, lineage_failed=3, pr_state="MERGED") == "pass"

    def test_open_pr_is_the_only_gated_state(self):
        assert verdict([], ran=True, lineage_failed=3, pr_state="OPEN") == "fail"

    # --- #1182 preserved ---
    def test_ran_but_silent_still_fails_on_a_clean_lineage(self):
        assert verdict([], ran=True, lineage_failed=0, pr_state="OPEN") == "fail"

    # --- the four-case decision table (AC5) ---
    @pytest.mark.parametrize(
        "case,pr_state,lineage_failed,ran,expected",
        [
            ("every review run failed, zero comments", "OPEN", 3, False, "fail"),
            ("genuine skip (draft/no-code/autobase)", "OPEN", 0, False, "pass"),
            ("ran cleanly but posted nothing (#1182)", "OPEN", 0, True, "fail"),
            ("post-factum dispatch on a merged PR", "MERGED", 3, True, "pass"),
        ],
    )
    def test_no_comment_decision_table(self, case, pr_state, lineage_failed, ran, expected):
        assert verdict([], ran=ran, lineage_failed=lineage_failed, pr_state=pr_state) == expected, (
            case
        )

    # --- no regression on #993 / #1134: a real comment still decides ---
    @pytest.mark.parametrize("lineage_failed", [0, 3])
    def test_fresh_comment_verdict_ignores_the_lineage_signal(self, lineage_failed):
        # A fresh verdict comment is direct evidence a review happened; the
        # lineage probe is consulted ONLY when there is no comment at all.
        assert (
            verdict_fresh(
                [(CANONICAL_CLEAN_SPEC, self.HEAD)], self.HEAD, lineage_failed=lineage_failed
            )
            == "pass"
        )
        assert (
            verdict_fresh([(BLOCKING_COMMENT, self.HEAD)], self.HEAD, lineage_failed=lineage_failed)
            == "fail"
        )

    def test_stale_only_still_fails_closed_regardless_of_lineage(self):
        # #993 anchoring is upstream of the total==0 branch and unaffected.
        assert (
            verdict_fresh(
                [(CANONICAL_CLEAN_SPEC, "2026-07-20T00:00:00Z")], self.HEAD, lineage_failed=0
            )
            == "fail"
        )


# -- Workflow wiring ----------------------------------------------------------


@pytest.fixture(scope="module")
def workflow_text() -> str:
    return REVIEW_WORKFLOW.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def verdict_step(workflow_text) -> dict:
    workflow = yaml.safe_load(workflow_text)
    steps = workflow["jobs"]["review"]["steps"]
    return next(s for s in steps if s.get("name") == "Verify review verdict")


@pytest.fixture(scope="module")
def review_step(workflow_text) -> dict:
    workflow = yaml.safe_load(workflow_text)
    steps = workflow["jobs"]["review"]["steps"]
    return next(s for s in steps if s.get("name") == "Run /code-review")


@pytest.fixture(scope="module")
def review_job(workflow_text) -> dict:
    return yaml.safe_load(workflow_text)["jobs"]["review"]


def branch_slice(run: str, marker: str) -> str:
    """Extract the whole `if <marker> ... fi` block from a bash script.

    Slicing a fixed character window instead makes every test that inspects a
    branch silently start covering less (or more) of it as the branch grows —
    the assertions keep passing while the thing they claim to pin drifts out of
    the window. Anchor on the closing `fi` at the `if`'s own indentation.
    """
    start = run.index(marker)
    line_start = run.rfind("\n", 0, start) + 1
    indent = run[line_start:start]
    end_token = f"\n{indent}fi"
    end = run.index(end_token, start)
    return run[start : end + len(end_token)]


class TestReviewStepBotGate:
    def test_allowed_bots_wildcard_is_set(self, review_step):
        # claude-code-action@v1's `allowed_bots` gate hard-fails on a Bot
        # actor. Two legitimate bot triggers feed the `review` check:
        # code-review-retry.yml (workflow_dispatch as github-actions[bot]) and
        # merge-train.yml's update-branch (synchronize as the App token). With
        # this unset, every retried / merge-train-updated PR jams the gate.
        assert review_step["with"].get("allowed_bots") == "*", (
            "Run /code-review must set allowed_bots: '*' — otherwise bot-"
            "triggered runs (retry dispatch, merge-train synchronize) fail at "
            "the action level and the `review` check goes permanently red."
        )


class TestVerdictStepWiring:
    def test_step_gates_on_pull_request_and_dispatch(self, verdict_step):
        # MUST also run on workflow_dispatch: the retry path
        # (code-review-retry.yml) dispatches with --ref <head_branch>, so its
        # check attaches to the PR head SHA and feeds the `review` gate.
        gate = verdict_step["if"]
        assert "github.event_name == 'pull_request'" in gate
        assert "github.event_name == 'workflow_dispatch'" in gate, (
            "Verdict must enforce on the retry (workflow_dispatch) path, not "
            "just pull_request — else the retry path is an auto-merge bypass."
        )

    def test_selector_matches_title_variants_not_literal_prefix(self, verdict_step):
        run = verdict_step["run"]
        assert 'startswith("### Code review")' not in run, (
            "Literal-prefix selector is the #957 false-pass hole."
        )
        assert r"(^|\\n)#{1,6}[ \\t]*(Claude[ \\t]+)?Code[ \\t]+Review" in run, (
            "Selector must tolerate title variants (any heading level, "
            "optional 'Claude' prefix) and anchor with (^|\\n), not ^."
        )
        assert '"i"' in run, "Title selector must be case-insensitive."

    def test_selector_slurps_pagination_via_standalone_jq(self, verdict_step):
        run = verdict_step["run"]
        assert "--paginate" in run
        # Pages must be slurped (-s) and flattened (add) in standalone jq so the
        # latest review comment is the global-latest, not per-page. The freshness
        # gate (#993) threads `--arg head` between `-rs` and the program, so the
        # `add` flatten is no longer adjacent to `jq -rs` — match it tolerantly.
        assert re.search(r"jq -rs\b.*'add", run), (
            "Pages must be slurped (-s) and flattened (add) in standalone jq "
            "so the latest review comment is the global-latest, not per-page."
        )
        code_lines = "\n".join(
            line for line in run.splitlines() if not line.lstrip().startswith("#")
        )
        assert "--slurp" not in code_lines, (
            "gh rejects --slurp combined with --jq at runtime (exit 1)."
        )

    def test_block_headings_are_critical_major_blocking_medium(self, verdict_step):
        run = verdict_step["run"]
        assert r"^#{1,6}[^[:alnum:]]*(CRITICAL|MAJOR|BLOCKING|MEDIUM)\b" in run, (
            "Block pattern must cover the real plugin severity sections: "
            "'### MAJOR' (#956), '### MAJOR findings' (#957), "
            "'### 🔴 BLOCKING' (#954) — decoration tolerated, suffix optional. "
            "MEDIUM is included (#1385 follow-up: a MEDIUM finding can "
            "genuinely corrupt state in the moment, so it now blocks)."
        )
        assert "(CRITICAL|MAJOR|MINOR|BLOCKING)" not in run, (
            "MINOR must be DROPPED from the block alternation (two-gate, #988) "
            "— minors never block merge."
        )

    # --- #1456: the structured-findings check must exist in the bash ---------
    # Without these the Python mirror above can stay green while the workflow
    # loses the check entirely — the mirror is only evidence if it mirrors
    # something. Same reason the prose-ladder wiring tests exist.
    def test_structured_findings_block_is_extracted(self, verdict_step):
        run = verdict_step["run"]
        assert "code-review-findings" in run, (
            "The verdict step must read the plugin's machine-emitted "
            "<!-- code-review-findings --> block. Prose shape alone missed two "
            "MEDIUM findings on PR #1452 and auto-merged it (#1456)."
        )
        assert re.search(r"sed -n '/<!-- \*code-review-findings/,/-->/p'", run), (
            "Extraction must slice from the marker to the closing --> so the "
            "payload can be handed to jq."
        )

    def test_structured_check_blocks_on_blocking_severities(self, verdict_step):
        run = verdict_step["run"]
        for sev in ("CRITICAL", "MAJOR", "BLOCKING", "MEDIUM"):
            assert f'== "{sev}"' in run, (
                f"The structured severity filter must treat {sev} as blocking "
                "— it is the same blocking set the prose ladder uses (#1385)."
            )
        assert "ascii_upcase" in run, (
            "Severity comparison must be case-normalized; the plugin has "
            "emitted title-case severities before (#1050)."
        )
        assert '== "MINOR"' not in run and '== "LOW"' not in run, (
            "Advisory severities must NOT block — they fall through to the "
            "prose ladder, preserving the two-gate model (#988)."
        )

    def test_structured_check_fails_closed_on_bad_json(self, verdict_step):
        run = verdict_step["run"]
        assert "not valid JSON" in run, (
            "A present-but-unparseable findings block must fail closed, same "
            "floor as the unrecognized-format exit (#957)."
        )

    def test_structured_check_precedes_the_locale_export(self, verdict_step):
        # `export LC_ALL=C` makes grep byte-oriented for the emoji-decorated
        # heading case (#954), but the workflow sets it AFTER the jq body
        # extraction precisely so JSON decoding stays UTF-8 aware. The
        # structured check is jq work, so it belongs on the UTF-8 side of that
        # line — and ahead of the prose ladder it overrides.
        run = verdict_step["run"]
        assert run.index("code-review-findings") < run.index("export LC_ALL=C"), (
            "The structured-findings jq must run before `export LC_ALL=C` so "
            "the JSON payload is decoded UTF-8 aware."
        )

    def test_severity_greps_run_under_c_locale(self, verdict_step):
        # The block/nonblock greps classify heading decoration with POSIX
        # [^[:alnum:]]. Under the runner default LANG=C.UTF-8 a multibyte emoji
        # ("### 🔴 BLOCKING", #954) is one rune that [^[:alnum:]] won't consume,
        # so the block check misses and a coexisting "### MINOR" can flip the
        # MERGE gate to PASS — auto-merging a PR the bot blocked. `export
        # LC_ALL=C` (set after the jq body extraction so JSON stays UTF-8 aware)
        # makes grep byte-oriented and restores the emoji match. Verified at
        # runtime: LANG=C.UTF-8 → emoji match 0, LC_ALL=C → emoji match 1.
        run = verdict_step["run"]
        assert "export LC_ALL=C" in run, (
            "Severity greps must run under LC_ALL=C — otherwise an emoji-"
            "decorated CRITICAL/MAJOR/BLOCKING heading (#954) escapes the block "
            "check under the runner's C.UTF-8 default and a coexisting MINOR "
            "section flips the merge gate to a false PASS."
        )
        assert run.index("export LC_ALL=C") < run.index("(CRITICAL|MAJOR|BLOCKING|MEDIUM)"), (
            "LC_ALL=C must be exported before the first severity grep."
        )

    def test_block_check_is_case_sensitive_allcaps(self, verdict_step):
        # #976: the block grep must be `-qE` (case-sensitive), NOT `-qiE` —
        # else title-case prose like "Blocking issues — None" false-blocks.
        run = verdict_step["run"]
        assert "grep -qE '^#{1,6}[^[:alnum:]]*(CRITICAL|MAJOR|BLOCKING|MEDIUM)" in run, (
            "Block check must be case-sensitive (grep -qE, no -i) so all-caps "
            "is the discriminator between real severity sections and title-"
            "case prose (#976)."
        )
        assert "grep -qiE '^#{1,6}[^[:alnum:]]*(CRITICAL" not in run, (
            "Block check must not use -i (case-insensitive) — that is the #962 false-block bug."
        )

    def test_found_n_issues_is_a_nonblocking_pass(self, verdict_step):
        run = verdict_step["run"]
        assert "Found [0-9]+ issues?:" in run, (
            "Found-N pattern must still be recognized — as a NON-blocking "
            "pass signal now (two-gate, #988)."
        )
        block_at = run.index("(CRITICAL|MAJOR|BLOCKING|MEDIUM)")
        found_at = run.index("Found [0-9]+ issues?:")
        assert block_at < found_at, "Block check must precede the Found-N pass branch."
        # The Found-N branch must lead to a pass (exit 0), not a block. The
        # branch spans the if-guard, a count-extraction line, the notice, and
        # exit 0 — widen the slice to cover it without reaching the next branch.
        found_branch = run[found_at : found_at + 380]
        assert "exit 0" in found_branch and "exit 1" not in found_branch, (
            "A 'Found N issues:' comment with no blocking heading must PASS "
            "(exit 0), not block (#956 advisory-only false-block)."
        )

    def test_no_blockers_prose_is_a_pass(self, verdict_step):
        # "Blocking issues — None" (#962) must be a positive pass, matched
        # case-insensitively (it is title-case prose).
        run = verdict_step["run"]
        assert "blocking issues" in run.lower(), (
            "A 'Blocking issues — None' APPROVE summary (#962) must be recognized as a pass."
        )

    def test_nonblocking_severity_headings_pass(self, verdict_step):
        run = verdict_step["run"]
        assert r"^#{1,6}[^[:alnum:]]*(MINOR|NITPICK|LOW|INFO|MEDIUM)\b" in run, (
            "Minor-only / advisory-only severity sections must be a positive "
            "pass signal (#963). MEDIUM stays here for title-case matches "
            "only — a real all-caps MEDIUM finding is already caught by the "
            "case-sensitive block check above (#1385 follow-up)."
        )

    def test_nonblocking_severity_grep_is_case_insensitive(self, verdict_step):
        # #1050: the non-block pass grep must be -qiE so a title-case advisory
        # heading ("#### Low" on PR #1049) passes instead of fail-closing. Safe
        # because it runs after the case-sensitive block check.
        run = verdict_step["run"]
        assert "grep -qiE '^#{1,6}[^[:alnum:]]*(MINOR|NITPICK|LOW|INFO|MEDIUM)" in run, (
            "Non-blocking severity grep must be case-insensitive (-qiE) so a "
            "title-case advisory heading (#### Low, PR #1049) is a pass, not a "
            "fail-closed (#1050)."
        )

    def test_lgtm_verdict_is_a_pass(self, verdict_step):
        # #1050: an explicit LGTM / "Verdict: APPROVE" summary with no blocking
        # heading must be a positive pass. Must run after the block check.
        run = verdict_step["run"]
        assert r"\bLGTM\b" in run and "APPROVED?" in run, (
            "A positive LGTM/APPROVE verdict (PR #1049) must be recognized as "
            "a pass signal (#1050)."
        )
        block_at = run.index("(CRITICAL|MAJOR|BLOCKING|MEDIUM)")
        lgtm_at = run.index(r"\bLGTM\b")
        assert block_at < lgtm_at, (
            "LGTM pass branch must run after the block check so it can never "
            "shadow a real all-caps severity section."
        )

    def test_clean_pattern_is_not_end_anchored(self, verdict_step):
        run = verdict_step["run"]
        assert r"^No issues found\." in run
        assert r"^No issues found\.?\s*$" not in run, (
            "End-anchored clean regex rejects the plugin spec's own clean "
            "example and would fail-closed every spec-compliant clean review."
        )

    def test_block_check_runs_before_all_pass_checks(self, verdict_step):
        run = verdict_step["run"]
        block_at = run.index("(CRITICAL|MAJOR|BLOCKING|MEDIUM)")
        clean_at = run.index(r"^No issues found\.")
        found_at = run.index("Found [0-9]+ issues?:")
        nonblock_at = run.index("(MINOR|NITPICK|LOW|INFO|MEDIUM)")
        assert block_at < clean_at, "Clean line must not shadow a block heading."
        assert block_at < found_at, "Found-N must not shadow a block heading."
        assert block_at < nonblock_at, "Non-blocking severity check must run after the block check."

    def test_step_ends_fail_closed(self, verdict_step):
        assert verdict_step["run"].strip().endswith("exit 1"), (
            "Unrecognized verdict format must fail closed (exit 1), not fall "
            "through to success — that fall-through is how #957 auto-merged."
        )


class TestFreshnessGateWiring:
    """#993: the verdict step must anchor on the head commit and reject stale
    prior-SHA comments. A SHA-agnostic selector consumes a leftover comment when
    the latest review errors and posts nothing — false-block or false-pass."""

    def test_resolves_head_commit_and_committer_time(self, verdict_step):
        run = verdict_step["run"]
        assert "headRefOid" in run, (
            "Must resolve the PR head SHA (gh pr view --json headRefOid) to "
            "anchor comment freshness."
        )
        assert ".commit.committer.date" in run, (
            "Must read the head commit's committer time as the freshness "
            "anchor (a comment older than the head commit is not its verdict)."
        )

    def test_selection_filters_comments_to_fresh_only(self, verdict_step):
        run = verdict_step["run"]
        assert ".created_at >= $head" in run, (
            "The jq selection must drop comments created before the head "
            "commit (stale prior-SHA comments are not this head's verdict)."
        )

    def test_no_comment_at_all_branch_disambiguates_skip_vs_ran(self, verdict_step):
        # total == 0 is ambiguous between legitimate skip and ran-but-silent
        # (#1182) — the branch must consult EXEC_FILE to tell them apart rather
        # than passing unconditionally.
        run = verdict_step["run"]
        marker = 'if [ "$total" -eq 0 ]; then'
        assert marker in run, (
            "Must distinguish 'no review comment at all' (total 0) from "
            "'comment(s) exist but all stale' (→ fail closed)."
        )
        total_zero_branch = branch_slice(run, marker)
        assert "EXEC_FILE" in total_zero_branch, (
            "The total==0 branch must consult EXEC_FILE (steps.review.outputs."
            "execution_file) to distinguish a legitimate skip (never ran, no "
            "execution log) from a ran-but-silent run (#1182)."
        )
        assert "exit 1" in total_zero_branch and "exit 0" in total_zero_branch, (
            "total==0 must branch: EXEC_FILE present → fail closed (exit 1, "
            "ran-but-silent, #1182); EXEC_FILE absent → pass (exit 0, "
            "legitimate skip)."
        )

    def test_exec_file_threaded_into_verdict_step_env(self, verdict_step):
        # Without EXEC_FILE in this step's own env, the shell variable used by
        # the total==0 branch would always be unset — silently degrading back
        # to "always pass" regardless of the bash logic (#1182).
        env = verdict_step.get("env", {})
        assert "EXEC_FILE" in env, (
            "Verify review verdict must read steps.review.outputs."
            "execution_file into EXEC_FILE — the sibling 'Verify review ran "
            "cleanly' step already proves that output exists when the review "
            "step ran (#1182)."
        )
        assert "steps.review.outputs.execution_file" in str(env["EXEC_FILE"])

    def test_genuine_skip_without_exec_file_still_passes(self, verdict_step):
        # Pure logic check on the extracted sub-branch: when EXEC_FILE is
        # empty/absent the step must fall through to exit 0 — no regression
        # for draft / has_code=false / autobase-skip PRs.
        run = verdict_step["run"]
        marker = 'if [ "$total" -eq 0 ]; then'
        total_zero_branch = branch_slice(run, marker)
        exec_check_idx = total_zero_branch.index("EXEC_FILE")
        after_exec_check = total_zero_branch[exec_check_idx:]
        assert "exit 0" in after_exec_check, (
            "The fall-through path after the EXEC_FILE check (i.e. no "
            "execution log — review never ran) must still exit 0."
        )

    def test_ran_but_silent_fails_before_genuine_skip_exit(self, verdict_step):
        # The exit 1 (ran-but-silent) must appear textually before the
        # fall-through exit 0 (genuine skip) — otherwise the EXEC_FILE check
        # is dead code and every total==0 case would hit exit 0 first.
        run = verdict_step["run"]
        marker = 'if [ "$total" -eq 0 ]; then'
        total_zero_branch = branch_slice(run, marker)
        assert total_zero_branch.index("exit 1") < total_zero_branch.rindex("exit 0"), (
            "Ran-but-silent's exit 1 must be reached before the genuine-skip "
            "exit 0 fall-through, or the fail-closed branch is unreachable."
        )

    def test_stale_only_fails_closed(self, verdict_step):
        # total > 0 but no fresh body ($body empty) → fail closed (#993).
        run = verdict_step["run"]
        marker = 'if [ -z "$body" ]; then'
        assert marker in run, (
            "Must have a stale-only branch: review comment(s) exist but none "
            "is newer than the head commit."
        )
        stale_branch = run[run.index(marker) : run.index(marker) + 600]
        assert "exit 1" in stale_branch and "exit 0" not in stale_branch, (
            "Stale-only (no comment fresh for the head SHA) must FAIL CLOSED "
            "(exit 1) — the latest review errored; do not consume a prior-SHA "
            "verdict (#993)."
        )


class TestAutobaseAnchorWiring:
    """#1134: the verdict step must RUN on the jarvis-ci[bot] auto-rebase push
    (drop the skip gate from its `if:`) and, on that branch, anchor freshness on
    the last non-bot head instead of the bot's 2-parent merge commit. #1131
    auto-merged past a live CRITICAL because the step was skipped there and the
    `review` check went green with nothing evaluated."""

    def test_step_no_longer_if_gated_on_autobase_skip(self, verdict_step):
        assert "steps.autobase.outputs.skip" not in str(verdict_step.get("if", "")), (
            "Verify review verdict must RUN on the autobase push — the "
            "steps.autobase.outputs.skip gate must not be in its if: (#1134). "
            "Skipping there is what let #1131 auto-merge past a CRITICAL."
        )

    def test_step_imports_autobase_skip_for_internal_branch(self, verdict_step):
        # The flag no longer gates the step; it must still reach the body (via
        # env or the run script) so the step can branch the anchor internally.
        env = verdict_step.get("env", {}) or {}
        in_env = any("steps.autobase.outputs.skip" in str(v) for v in env.values())
        in_run = "steps.autobase.outputs.skip" in verdict_step.get("run", "")
        assert in_env or in_run, (
            "Verdict step must still reference steps.autobase.outputs.skip (via "
            "env or run body) to branch the freshness anchor internally (#1134)."
        )

    def test_autobase_branch_walks_pr_commits_filtering_the_bot(self, verdict_step):
        run = verdict_step["run"]
        assert "pulls/" in run and "/commits" in run, (
            "Autobase branch must list the PR commits (pulls/<n>/commits) to "
            "find the last non-bot head."
        )
        assert ".author.login" in run, (
            "Autobase anchor must key off commit author.login (null/unlinked "
            "author counts as real — safe default, #1134)."
        )
        assert "jarvis-ci[bot]" in run, (
            "Autobase anchor must filter out jarvis-ci[bot] commits so it walks "
            "back to the last real head (#963: 28 consecutive rebases; "
            "parents[0] alone breaks)."
        )

    def test_autobase_anchor_feeds_the_shared_fresh_selection(self, verdict_step):
        # The computed anchor must feed the SAME selection/grep chain — the
        # autobase path is not a parallel copy of the two-gate verdict.
        run = verdict_step["run"]
        assert '--arg head "$ANCHOR_TIME"' in run, (
            "The freshness selection must consume the computed ANCHOR_TIME "
            "(head_time on the normal path, last-non-bot-head on the autobase "
            "path), not a raw HEAD_TIME — so both paths share one verdict chain."
        )
        assert ".created_at >= $head" in run, (
            "Autobase path must reuse the shared freshness filter, not a "
            "separate selection (#1134)."
        )


class TestNeverReviewedLineageWiring:
    """#1228: the total==0 branch must probe the PR's head lineage for FAILED
    code-review runs instead of reading comment-absence as "plugin skipped".

    PR #1226 (2026-07-21): three plugin runs died on permission_denials, an
    jarvis-ci[bot] auto-rebase push then skipped review as designed, the gate
    found no comment, passed, and auto-merge shipped an un-reviewed PR. The
    mirror bug is run 29986227927 — a workflow_dispatch on the already-merged
    #1226 where the plugin correctly declined and the guard failed closed.
    """

    MARKER = 'if [ "$total" -eq 0 ]; then'

    @pytest.fixture(scope="class")
    def zero_branch(self, verdict_step) -> str:
        # Comment lines stripped: this class asserts on the ORDER of the
        # decision steps, and the explanatory comment block names every signal
        # up front — matching prose would make the ordering assertions vacuous.
        branch = branch_slice(verdict_step["run"], self.MARKER)
        return "\n".join(line for line in branch.splitlines() if not line.lstrip().startswith("#"))

    def test_job_can_read_workflow_runs(self, review_job):
        # Listing workflow runs via GITHUB_TOKEN needs `actions: read`. Without
        # it the probe 404s and — under `set -euo pipefail` — the step dies,
        # which is safe but wrong: every PR would block.
        perms = review_job.get("permissions", {}) or {}
        assert perms.get("actions") == "read", (
            "The review job must grant `actions: read` — the head-lineage probe "
            "lists code-review workflow runs via the Actions API (#1228)."
        )

    def test_probe_queries_the_code_review_workflow_runs(self, zero_branch):
        assert "actions/workflows/code-review.yml/runs" in zero_branch, (
            "The total==0 branch must list this workflow's own runs to tell a "
            "legitimate skip from 'every review attempt died' (#1228)."
        )

    def test_probe_counts_only_failed_runs(self, zero_branch):
        assert 'conclusion == "failure"' in zero_branch, (
            "Only runs that CONCLUDED IN FAILURE are evidence that every review "
            "attempt died before posting; success and cancelled must not block "
            "through THIS probe. An in-flight run blocks through the separate "
            "#1434 probe (see TestInFlightWiring), not by widening this one."
        )

    def test_probe_is_scoped_to_this_prs_commits(self, zero_branch):
        assert "pulls/" in zero_branch and "/commits" in zero_branch, (
            "The failed-run set must be intersected with the PR's own commit "
            "lineage — another PR's failing review must never block this one."
        )
        assert "head_sha" in zero_branch, (
            "Intersection keys on run.head_sha ∈ the PR's commit SHAs (#1228)."
        )

    def test_probe_excludes_the_current_run(self, zero_branch):
        assert "GITHUB_RUN_ID" in zero_branch, (
            "The currently-executing run must be excluded by id, or a re-run "
            "could self-block on its own row once it concludes (#1228)."
        )

    def test_non_open_pr_short_circuits_to_pass(self, zero_branch, verdict_step):
        # AC4: a workflow_dispatch re-run on a merged/closed PR posts zero
        # comments by design. There is no merge left to gate — failing closed
        # there is pure noise (observed on run 29986227927 against #1226).
        run = verdict_step["run"]
        assert "PR_STATE" in run and "state" in run, (
            "The verdict step must resolve the PR state (gh pr view --json "
            "state) so post-factum dispatch on a non-OPEN PR is not gated."
        )
        assert 'PR_STATE" != "OPEN"' in zero_branch or 'PR_STATE" = "OPEN"' in zero_branch, (
            "The total==0 branch must carve out non-OPEN PRs before the "
            "lineage/EXEC_FILE fail-closed checks (#1228 AC4)."
        )

    def test_decision_order_state_then_lineage_then_exec_file(self, zero_branch):
        # Ordering IS the fix: state carve-out must precede the fail-closed
        # checks (else merged PRs go red), and the lineage block must precede
        # the fall-through pass (else it is dead code).
        state_at = zero_branch.index("PR_STATE")
        lineage_at = zero_branch.index("LINEAGE_FAILED")
        exec_at = zero_branch.index("EXEC_FILE")
        assert state_at < lineage_at < exec_at, (
            "total==0 must decide in order: non-OPEN → pass; lineage failures → "
            "fail closed (never reviewed, #1228); EXEC_FILE present → fail "
            "closed (ran-but-silent, #1182); otherwise pass (genuine skip)."
        )
        assert lineage_at < zero_branch.rindex("exit 0"), (
            "The lineage check must run before the genuine-skip fall-through, "
            "or #1226 passes again."
        )

    def test_lineage_failure_blocks(self, zero_branch):
        lineage_at = zero_branch.index("LINEAGE_FAILED")
        after = zero_branch[lineage_at:]
        assert "exit 1" in after[: after.index("EXEC_FILE")], (
            "A non-zero lineage failure count must exit 1 — the PR was never "
            "reviewed, so it must not be mergeable (#1228 AC2)."
        )

    def test_genuine_skip_still_falls_through_to_pass(self, zero_branch):
        # AC3: draft / no-substantive-code / autobase push with a CLEAN lineage
        # must still pass. The branch's last statement is the skip exit 0.
        assert "exit 0" in zero_branch, "Genuine skip must still pass (exit 0)."
        assert zero_branch.rindex("exit 0") > zero_branch.rindex("exit 1"), (
            "The genuine-skip exit 0 is the fall-through — it must come after "
            "every fail-closed branch (#993/#1134 regression guard)."
        )


class TestInFlightLineageLogic:
    """#1434: a still-RUNNING sibling review is not evidence of a clean skip.

    The autobase carve-out's premise is "this push added no code, so the
    existing review still stands". That premise only holds when a review has
    actually *completed*. The #1228 lineage probe counts runs that CONCLUDED IN
    FAILURE; a run that has not concluded at all is invisible to it, so a race
    between an auto-rebase push and an in-flight review passes the gate.

    Fired twice on 2026-08-07:
      - PR #1429: real run 31181563574 started 13:11:35Z; autobase run
        31182030912 passed 13:18:47Z; auto-merge fired 13:19:44Z; the real run
        failed closed 13:25:46Z -- six minutes after the merge.
      - PR #1435: real run 31183547249 over e8fdd701 was still in_progress while
        three consecutive autobase runs passed and auto-merge shipped it.

    Both PRs merged having never been reviewed.
    """

    SHAS = ["sha-a", "sha-b", "sha-head"]

    # --- lineage_in_flight_runs() probe ---
    def test_in_progress_run_on_a_pr_commit_is_counted(self):
        runs = [("sha-head", "in_progress", "1001")]
        assert lineage_in_flight_runs(runs, self.SHAS) == 1

    def test_queued_run_is_counted(self):
        # `queued` and `waiting` are equally not-yet-verified. The probe keys on
        # "not completed" rather than enumerating in-flight status names, so a
        # future GitHub status can never silently read as done.
        runs = [("sha-head", "queued", "1001"), ("sha-a", "waiting", "1002")]
        assert lineage_in_flight_runs(runs, self.SHAS) == 2

    def test_completed_run_is_not_in_flight(self):
        runs = [("sha-a", "completed", "1001"), ("sha-b", "completed", "1002")]
        assert lineage_in_flight_runs(runs, self.SHAS) == 0

    def test_in_flight_run_outside_the_lineage_is_ignored(self):
        # Another PR's running review must never block this one -- the same
        # scoping rule the #1228 failure probe already applies.
        runs = [("sha-of-other-pr", "in_progress", "1001")]
        assert lineage_in_flight_runs(runs, self.SHAS) == 0

    def test_current_run_is_excluded(self):
        # This very run is in_progress by definition; counting it would make
        # every autobase run block on itself.
        runs = [("sha-head", "in_progress", "9999")]
        assert lineage_in_flight_runs(runs, self.SHAS, current_run_id="9999") == 0
        assert lineage_in_flight_runs(runs, self.SHAS, current_run_id="1234") == 1

    # --- AC1: an in-flight sibling blocks the total==0 pass ---
    def test_in_flight_sibling_blocks(self):
        assert verdict([], lineage_in_flight=1) == "fail"

    def test_in_flight_blocks_even_with_a_clean_failure_probe(self):
        # The two probes are independent: zero failures is exactly the state
        # that passed on #1429/#1435. It must no longer be sufficient.
        assert verdict([], lineage_failed=0, lineage_in_flight=1) == "fail"

    def test_in_flight_on_a_non_open_pr_still_passes(self):
        # A merged/closed PR gates nothing; the #1228 post-factum carve-out
        # keeps precedence over the new check (else workflow_dispatch re-runs
        # against merged PRs go red again -- run 29986227927).
        assert verdict([], lineage_in_flight=1, pr_state="MERGED") == "pass"


class TestPositiveEvidenceLogic:
    """#1434 AC2: pass requires evidence a review happened, not absence of failure.

    With zero verdict comments and a clean lineage the old rule passed by
    default. But a PR carrying substantive code and no verdict comment has, by
    construction, not been reviewed -- a completed review always posts one. So
    `has_code` is the discriminator between "nothing to review" (legitimate
    skip) and "something to review that nobody reviewed" (fail closed).

    `has_code` is already computed for every pull_request event by the
    "Check substantive diff" step, and it measures the PR's FULL diff
    (`base...HEAD`), not the push diff -- on PR #1435's autobase run it
    correctly reported 299 lines. No new probe is needed.
    """

    def test_no_substantive_code_still_passes(self):
        # EOL-only / non-code PRs: the review step is skipped by design and
        # there is genuinely nothing to gate.
        assert verdict([], has_code=False) == "pass"

    def test_unknown_has_code_still_passes(self):
        # workflow_dispatch does not run the diff step, so has_code is unset.
        # Unknown must not fail closed -- that would break post-factum dispatch.
        assert verdict([], has_code=None) == "pass"

    def test_code_present_with_no_verdict_comment_blocks(self):
        assert verdict([], has_code=True) == "fail"

    def test_draft_with_code_still_passes(self):
        # The plugin declines drafts at its eligibility check, and a draft
        # cannot be merged -- there is no merge to gate, so failing closed is
        # pure noise (same reasoning as the non-OPEN carve-out).
        assert verdict([], has_code=True, is_draft=True) == "pass"

    def test_non_open_pr_outranks_the_code_check(self):
        assert verdict([], has_code=True, pr_state="MERGED") == "pass"

    def test_a_fresh_verdict_comment_is_the_evidence(self):
        # has_code is consulted only when total==0. Once a verdict comment
        # exists, the content gates decide and has_code is irrelevant.
        assert (
            verdict_fresh(
                [("## Code Review\n\nNo issues found.", "2026-08-07T13:40:00Z")],
                "2026-08-07T13:36:46Z",
                has_code=True,
            )
            == "pass"
        )


class TestPR1435Timeline:
    """#1434 AC4: the reproducing fixture -- autobase push + in-flight review.

    Replays PR #1435's actual state at 13:39:01Z, when autobase run 31183641339
    evaluated the gate: the real review over `e8fdd701` had been running since
    13:37:50Z and had posted nothing; an earlier autobase run had already
    concluded success; the PR carried 299 substantive diff lines. The gate
    passed, and auto-merge shipped it at 13:41:38Z.
    """

    HUMAN_SHA = "e8fdd701"
    LINEAGE = [HUMAN_SHA, "a335a42d", "3ac2731c"]
    COMMITS = [
        ("2026-08-07T13:36:46Z", "Osasuwu"),
        ("2026-08-07T13:38:56Z", AUTOBASE_BOT),
    ]
    HEAD_TIME = "2026-08-07T13:38:56Z"
    # (head_sha, status, run_id) -- as the Actions API reported them.
    RUNS = [
        (HUMAN_SHA, "in_progress", "31183547249"),
        ("a335a42d", "completed", "31183593287"),
    ]
    CURRENT = "31183641339"

    def test_the_old_rule_saw_nothing_wrong(self):
        # Guards the diagnosis: the #1228 failure probe genuinely reads clean
        # here, so the fix must come from a different signal, not a tweak to it.
        failed = [(sha, None, rid) for sha, _status, rid in self.RUNS]
        assert lineage_failed_runs(failed, self.LINEAGE, self.CURRENT) == 0

    def test_the_in_flight_probe_sees_the_running_review(self):
        assert lineage_in_flight_runs(self.RUNS, self.LINEAGE, self.CURRENT) == 1

    def test_gate_does_not_pass_the_pr_1435_state(self):
        assert (
            verdict_autobase(
                [],
                self.COMMITS,
                self.HEAD_TIME,
                autobase=True,
                lineage_in_flight=lineage_in_flight_runs(self.RUNS, self.LINEAGE, self.CURRENT),
                has_code=True,
            )
            == "fail"
        )

    def test_gate_still_blocks_once_the_review_finishes_silently(self):
        # The in-flight run concludes success but posts no verdict comment.
        # AC2 catches what AC1 no longer can: code present, nothing reviewed it.
        finished = [(self.HUMAN_SHA, "completed", "31183547249")]
        assert (
            verdict_autobase(
                [],
                self.COMMITS,
                self.HEAD_TIME,
                autobase=True,
                lineage_in_flight=lineage_in_flight_runs(finished, self.LINEAGE, self.CURRENT),
                has_code=True,
            )
            == "fail"
        )

    def test_gate_passes_once_a_real_verdict_lands(self):
        # The self-healing path: code-review-retry re-dispatches the failed run
        # as a workflow_dispatch (which bypasses the autobase skip and performs
        # a genuine review), a verdict comment lands, and the next evaluation
        # carries it forward on the last-non-bot anchor (#1134).
        assert (
            verdict_autobase(
                [("## Code Review -- PR #1435\n\nNo issues found.", "2026-08-07T13:50:00Z")],
                self.COMMITS,
                self.HEAD_TIME,
                autobase=True,
                has_code=True,
            )
            == "pass"
        )


class TestInFlightWiring:
    """#1434: the bash must carry the in-flight probe and the has_code gate."""

    MARKER = 'if [ "$total" -eq 0 ]; then'

    @pytest.fixture(scope="class")
    def zero_branch(self, verdict_step) -> str:
        branch = branch_slice(verdict_step["run"], self.MARKER)
        return "\n".join(line for line in branch.splitlines() if not line.lstrip().startswith("#"))

    def test_probe_counts_not_completed_runs(self, zero_branch):
        assert 'status != "completed"' in zero_branch, (
            "The total==0 branch must count code-review runs over this PR's "
            "commits that have NOT completed. Keying on 'not completed' rather "
            "than listing in-flight status names means a future GitHub status "
            "can never silently read as done (#1434 AC1)."
        )

    def test_in_flight_count_blocks(self, zero_branch):
        after = zero_branch[zero_branch.index("LINEAGE_INFLIGHT") :]
        assert "exit 1" in after, (
            "A non-zero in-flight count must exit 1 -- the review that would "
            "verify this PR has not finished, so the autobase carry-forward has "
            "nothing to carry (#1434 AC1)."
        )

    def test_in_flight_check_precedes_the_fall_through_pass(self, zero_branch):
        assert zero_branch.index("LINEAGE_INFLIGHT") < zero_branch.rindex("exit 0"), (
            "The in-flight check must run before the genuine-skip pass, or it "
            "is dead code and #1429/#1435 pass again."
        )

    def test_non_open_carve_out_still_precedes_it(self, zero_branch):
        assert zero_branch.index("PR_STATE") < zero_branch.index("LINEAGE_INFLIGHT"), (
            "A merged/closed PR gates nothing; the #1228 post-factum carve-out "
            "must keep precedence over the new fail-closed checks."
        )

    def test_has_code_gates_the_fall_through_pass(self, zero_branch):
        assert "HAS_CODE" in zero_branch, (
            "The fall-through pass must require positive evidence there was "
            "nothing to review (has_code=false), not merely the absence of a "
            "failed run (#1434 AC2)."
        )
        assert zero_branch.index("HAS_CODE") < zero_branch.rindex("exit 0"), (
            "The has_code gate must precede the genuine-skip pass."
        )

    def test_draft_is_carved_out_of_the_has_code_gate(self, zero_branch):
        assert "IS_DRAFT" in zero_branch, (
            "A draft PR cannot be merged and the plugin declines it by design "
            "-- failing closed there is noise, not safety (#1434 AC2)."
        )

    def test_has_code_is_wired_from_the_diff_step(self, verdict_step):
        # has_code is a step OUTPUT, so it must cross into the verdict step via
        # env; unlike isDraft it cannot be re-derived from the API here.
        env = verdict_step.get("env", {}) or {}
        assert "HAS_CODE" in env, (
            "HAS_CODE must be wired from steps.diff.outputs.has_code -- it "
            "measures the PR's full base...HEAD diff, so it is meaningful even "
            "on an autobase push whose own diff is empty."
        )
        assert "steps.diff.outputs.has_code" in env["HAS_CODE"]

    def test_pr_query_resolves_draft_state(self, verdict_step):
        # isDraft comes from the API, resolved in-script beside PR_STATE off the
        # same `gh pr view` call rather than round-tripping through env.
        run = verdict_step["run"]
        assert "isDraft" in run, (
            "The verdict step must resolve isDraft alongside state and "
            "headRefOid (gh pr view --json ...,isDraft)."
        )
        assert "IS_DRAFT=" in run, "isDraft must be bound to IS_DRAFT for the carve-out."

    def test_pass_notice_names_the_evidence_it_relied_on(self, zero_branch):
        tail = zero_branch[zero_branch.rindex("::notice::") :]
        assert "HAS_CODE" in tail or "LINEAGE" in tail, (
            "The fall-through pass notice must state which signal it relied on, "
            "so a pass is auditable from the log without re-deriving it "
            "(#1434 AC3)."
        )
