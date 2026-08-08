"""Scoring script for the competence-measurement protocol (issue #1249, AC2).

Recomputes the 2x2 signal-detection matrix + the "claimed, not confirmed"
bucket from GitHub PR data on every run. There is no state file — the PR
(comments, timeline events, commits) is the journal; this script is a pure
function over a snapshot of that data.

Thresholds (minimum verdict count, horizon) are read from
docs/design/competence-measurement-protocol.md via `load_protocol_constants`
rather than hardcoded independently, per protocol §5: the script is
downstream of the protocol, never the reverse.

This module does no network I/O. The CLI entry point reads a JSON envelope
from stdin — {"prs": [...]} — so callers (a skill, a scheduled task) own the
`gh`/GitHub-API fetch and this module stays trivially testable:

  {"prs": [
    {"number": 1,
     "ready_for_review_at": "2026-08-01T12:00:00Z" | null,
     "comments": [{"created_at": "...", "body": "..."}],
     "bot_findings": [{"file": "...", "line": 1}],
     "fix_commits": [{"message": "fix: ...", "files": {"path": [1, 2]}}]},
    ...
  ]}
    | python scripts/competence_scoring.py

See protocol §2 for the verdict-comment format and §3 for ground-truth
ranking.
"""

from __future__ import annotations

import json
import re
import statistics
import sys
from dataclasses import dataclass, field
from pathlib import Path

_VERDICT_CLEAN_RE = re.compile(r"вердикт:\s*чисто", re.IGNORECASE)
_VERDICT_DEFECT_RE = re.compile(
    r"вердикт:\s*дефект\s+(?P<file>\S+):(?P<line>\d+)\s*[—-]\s*(?P<confidence>\d+)",
    re.IGNORECASE,
)
_FIX_COMMIT_RE = re.compile(r"^fix(\(.+\))?:", re.IGNORECASE)

_MIN_VERDICTS_RE = re.compile(r"fewer than\s+(\d+)\s+scored verdict", re.IGNORECASE)
_HORIZON_DAYS_RE = re.compile(r"Horizon:\*{0,2}\s*(\d+)\s*days", re.IGNORECASE)
_HORIZON_PRS_RE = re.compile(r"or\s+(\d+)\s*merged PRs", re.IGNORECASE)


@dataclass(frozen=True)
class Claim:
    file: str
    line: int
    confidence: int


@dataclass(frozen=True)
class Verdict:
    clean: bool
    claims: list[Claim] = field(default_factory=list)


@dataclass(frozen=True)
class Comment:
    created_at: str
    body: str


@dataclass(frozen=True)
class BotFinding:
    file: str
    line: int


@dataclass(frozen=True)
class FixCommit:
    message: str
    files: dict[str, list[int]]


@dataclass(frozen=True)
class PRData:
    number: int
    ready_for_review_at: str | None
    comments: list[Comment]
    bot_findings: list[BotFinding]
    fix_commits: list[FixCommit]


@dataclass
class PRScore:
    number: int
    excluded: bool
    hits: int = 0
    misses: int = 0
    bucket: int = 0
    correct_rejections: int = 0


@dataclass
class Matrix:
    hits: int = 0
    misses: int = 0
    false_alarms: int = 0
    correct_rejections: int = 0

    def __iadd__(self, other: "Matrix") -> "Matrix":
        self.hits += other.hits
        self.misses += other.misses
        self.false_alarms += other.false_alarms
        self.correct_rejections += other.correct_rejections
        return self


@dataclass(frozen=True)
class ProtocolConstants:
    min_verdicts: int
    horizon_days: int
    horizon_prs: int


def parse_verdict_comment(body: str) -> Verdict | None:
    """Parse a verdict comment per protocol §2. Returns None if `body` is
    not a verdict comment at all (e.g. an ordinary review remark)."""
    claims: list[Claim] = []
    for m in _VERDICT_DEFECT_RE.finditer(body):
        confidence = int(m.group("confidence"))
        if not 0 <= confidence <= 100:
            raise ValueError(f"confidence out of 0-100 range: {confidence}")
        claims.append(Claim(file=m.group("file"), line=int(m.group("line")), confidence=confidence))

    if claims:
        return Verdict(clean=False, claims=claims)
    if _VERDICT_CLEAN_RE.search(body):
        return Verdict(clean=True, claims=[])
    return None


def is_within_draft_window(comment_created_at: str, ready_for_review_at: str) -> bool:
    """Strict "before" per protocol §2 — a tie does not count."""
    return comment_created_at < ready_for_review_at


def match_postmerge_fix(claim: Claim, fix_commits: list[FixCommit]) -> bool:
    for commit in fix_commits:
        if not _FIX_COMMIT_RE.match(commit.message):
            continue
        lines = commit.files.get(claim.file)
        if lines and claim.line in lines:
            return True
    return False


def bot_flagged(claim: Claim, bot_findings: list[BotFinding]) -> bool:
    return any(f.file == claim.file and f.line == claim.line for f in bot_findings)


def _ground_truth_defect_lines(pr: PRData) -> set[tuple[str, int]]:
    """All (file, line) pairs with a ground-truth defect, ranked sources
    collapsed into a set — used to find misses (unclaimed GT defects)."""
    lines: set[tuple[str, int]] = set()
    for commit in pr.fix_commits:
        if not _FIX_COMMIT_RE.match(commit.message):
            continue
        for file, file_lines in commit.files.items():
            for line in file_lines:
                lines.add((file, line))
    for finding in pr.bot_findings:
        lines.add((finding.file, finding.line))
    return lines


def classify_pr(pr: PRData) -> PRScore:
    """Classify one PR's scored claims per protocol §3-4.

    Denominator rule (§7): a PR whose bot review never ran drops out
    entirely — `excluded=True`, no matrix contribution.
    """
    if pr.ready_for_review_at is None:
        return PRScore(number=pr.number, excluded=True)

    score = PRScore(number=pr.number, excluded=False)
    claimed_lines: set[tuple[str, int]] = set()
    any_scored_verdict = False

    for comment in pr.comments:
        if not is_within_draft_window(comment.created_at, pr.ready_for_review_at):
            continue
        verdict = parse_verdict_comment(comment.body)
        if verdict is None:
            continue
        any_scored_verdict = True

        for claim in verdict.claims:
            claimed_lines.add((claim.file, claim.line))
            if match_postmerge_fix(claim, pr.fix_commits) or bot_flagged(claim, pr.bot_findings):
                score.hits += 1
            else:
                score.bucket += 1

    if not any_scored_verdict:
        return score

    gt_defect_lines = _ground_truth_defect_lines(pr)
    unclaimed_gt_defects = gt_defect_lines - claimed_lines
    score.misses += len(unclaimed_gt_defects)
    if not gt_defect_lines and not claimed_lines:
        score.correct_rejections += 1

    return score


def aggregate(scores: list[PRScore]) -> tuple[Matrix, int, list[int]]:
    """Returns (matrix, scored_pr_count, excluded_pr_numbers). Bucket items
    are not folded into false_alarms — they stay separate per protocol §3
    until third-reader adjudication (out of this script's scope)."""
    matrix = Matrix()
    scored = 0
    excluded: list[int] = []
    bucket_total = 0
    for s in scores:
        if s.excluded:
            excluded.append(s.number)
            continue
        scored += 1
        matrix.hits += s.hits
        matrix.misses += s.misses
        matrix.correct_rejections += s.correct_rejections
        bucket_total += s.bucket
    return matrix, scored, excluded, bucket_total


def compute_sensitivity(matrix: Matrix) -> float | None:
    positives = matrix.hits + matrix.misses
    if positives == 0:
        return None
    return matrix.hits / positives


def _corrected_rate(x: int, n: int) -> float:
    """Log-linear correction (Hautus 1995) to keep rates off 0/1 so the
    inverse-normal z-score stays finite."""
    if n == 0:
        return 0.5
    return (x + 0.5) / (n + 1)


def compute_criterion(matrix: Matrix) -> float | None:
    """Standard SDT criterion c = -0.5 * (z(hit_rate) + z(false_alarm_rate)).
    Positive = conservative (reluctant to claim a defect) — the direct
    operationalization of the underestimation pattern (protocol §4)."""
    positives = matrix.hits + matrix.misses
    negatives = matrix.false_alarms + matrix.correct_rejections
    if positives == 0 or negatives == 0:
        return None
    hit_rate = _corrected_rate(matrix.hits, positives)
    fa_rate = _corrected_rate(matrix.false_alarms, negatives)
    dist = statistics.NormalDist()
    return -0.5 * (dist.inv_cdf(hit_rate) + dist.inv_cdf(fa_rate))


def load_protocol_constants(path: Path) -> ProtocolConstants:
    # ceiling: regex-parses the protocol doc's prose; a wording edit to the
    # Horizon/stop-rule sentences (not just the numbers) breaks this silently
    # into the raise below rather than a wrong value. Upgrade path: a small
    # frontmatter/YAML constants block in the doc if the prose keeps drifting.
    text = Path(path).read_text(encoding="utf-8")
    min_verdicts_m = _MIN_VERDICTS_RE.search(text)
    horizon_days_m = _HORIZON_DAYS_RE.search(text)
    horizon_prs_m = _HORIZON_PRS_RE.search(text)
    if not (min_verdicts_m and horizon_days_m and horizon_prs_m):
        raise ValueError(f"could not parse protocol constants from {path}")
    return ProtocolConstants(
        min_verdicts=int(min_verdicts_m.group(1)),
        horizon_days=int(horizon_days_m.group(1)),
        horizon_prs=int(horizon_prs_m.group(1)),
    )


def check_series_validity(scored_verdicts: int, constants: ProtocolConstants) -> bool:
    return scored_verdicts >= constants.min_verdicts


def check_bucket_dominance(bucket: int, core: int) -> bool:
    """True means the series draws no conclusion (protocol §6)."""
    return bucket > core


def _pr_from_json(raw: dict) -> PRData:
    return PRData(
        number=raw["number"],
        ready_for_review_at=raw.get("ready_for_review_at"),
        comments=[Comment(**c) for c in raw.get("comments", [])],
        bot_findings=[BotFinding(**f) for f in raw.get("bot_findings", [])],
        fix_commits=[FixCommit(**f) for f in raw.get("fix_commits", [])],
    )


def main() -> None:
    envelope = json.load(sys.stdin)
    prs = [_pr_from_json(raw) for raw in envelope.get("prs", [])]
    scores = [classify_pr(pr) for pr in prs]
    matrix, scored_prs, excluded_prs, bucket = aggregate(scores)
    core = matrix.hits + matrix.misses + matrix.correct_rejections
    result = {
        "matrix": {
            "hits": matrix.hits,
            "misses": matrix.misses,
            "false_alarms": matrix.false_alarms,
            "correct_rejections": matrix.correct_rejections,
        },
        "bucket": bucket,
        "scored_prs": scored_prs,
        "excluded_prs": excluded_prs,
        "sensitivity": compute_sensitivity(matrix),
        "criterion": compute_criterion(matrix),
        "bucket_dominant": check_bucket_dominance(bucket, core),
    }
    json.dump(result, sys.stdout)


if __name__ == "__main__":
    main()
