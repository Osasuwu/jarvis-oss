"""Tests for scripts/competence_scoring.py — issue #1249 AC2.

Every test traces to a specific behavior required by
docs/design/competence-measurement-protocol.md.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import competence_scoring as cs  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[1]
PROTOCOL_PATH = REPO_ROOT / "docs" / "design" / "competence-measurement-protocol.md"


# --- parse_verdict_comment ---------------------------------------------


def test_parse_clean_verdict():
    verdict = cs.parse_verdict_comment("вердикт: чисто")
    assert verdict is not None
    assert verdict.clean is True
    assert verdict.claims == []


def test_parse_single_defect_claim():
    verdict = cs.parse_verdict_comment("вердикт: дефект src/foo.py:42 — 80")
    assert verdict is not None
    assert verdict.clean is False
    assert len(verdict.claims) == 1
    claim = verdict.claims[0]
    assert claim.file == "src/foo.py"
    assert claim.line == 42
    assert claim.confidence == 80


def test_parse_multiple_defect_claims():
    body = "вердикт: дефект src/foo.py:42 — 80\nвердикт: дефект src/bar.py:7 — 30\n"
    verdict = cs.parse_verdict_comment(body)
    assert verdict is not None
    assert len(verdict.claims) == 2
    assert verdict.claims[1].file == "src/bar.py"
    assert verdict.claims[1].line == 7
    assert verdict.claims[1].confidence == 30


def test_parse_non_verdict_comment_returns_none():
    assert cs.parse_verdict_comment("looks good, nice PR!") is None


def test_parse_defect_confidence_out_of_range_raises():
    with pytest.raises(ValueError):
        cs.parse_verdict_comment("вердикт: дефект src/foo.py:1 — 150")


# --- draft-window gate ---------------------------------------------------


def test_comment_before_ready_for_review_is_in_window():
    assert cs.is_within_draft_window("2026-08-01T10:00:00Z", "2026-08-01T12:00:00Z")


def test_comment_after_ready_for_review_is_excluded():
    assert not cs.is_within_draft_window("2026-08-01T13:00:00Z", "2026-08-01T12:00:00Z")


def test_comment_exactly_at_ready_for_review_is_excluded():
    # strict "before" per protocol §2 — a tie does not count
    ts = "2026-08-01T12:00:00Z"
    assert not cs.is_within_draft_window(ts, ts)


# --- ground-truth matching ------------------------------------------------


def test_match_postmerge_fix_same_file_and_line():
    claim = cs.Claim(file="src/foo.py", line=42, confidence=80)
    fixes = [cs.FixCommit(message="fix: null check", files={"src/foo.py": [40, 41, 42]})]
    assert cs.match_postmerge_fix(claim, fixes)


def test_match_postmerge_fix_different_file_no_match():
    claim = cs.Claim(file="src/foo.py", line=42, confidence=80)
    fixes = [cs.FixCommit(message="fix: null check", files={"src/other.py": [42]})]
    assert not cs.match_postmerge_fix(claim, fixes)


def test_match_postmerge_fix_line_outside_hunk_no_match():
    claim = cs.Claim(file="src/foo.py", line=99, confidence=80)
    fixes = [cs.FixCommit(message="fix: null check", files={"src/foo.py": [40, 41, 42]})]
    assert not cs.match_postmerge_fix(claim, fixes)


def test_match_postmerge_fix_ignores_non_fix_commits():
    claim = cs.Claim(file="src/foo.py", line=42, confidence=80)
    fixes = [cs.FixCommit(message="refactor: rename var", files={"src/foo.py": [42]})]
    assert not cs.match_postmerge_fix(claim, fixes)


def test_bot_flagged_matches_file_and_line():
    claim = cs.Claim(file="src/foo.py", line=42, confidence=80)
    findings = [cs.BotFinding(file="src/foo.py", line=42)]
    assert cs.bot_flagged(claim, findings)


def test_bot_flagged_no_match():
    claim = cs.Claim(file="src/foo.py", line=42, confidence=80)
    findings = [cs.BotFinding(file="src/foo.py", line=41)]
    assert not cs.bot_flagged(claim, findings)


# --- classify_pr ------------------------------------------------------


def _pr(**overrides):
    base = dict(
        number=1,
        ready_for_review_at="2026-08-01T12:00:00Z",
        comments=[],
        bot_findings=[],
        fix_commits=[],
    )
    base.update(overrides)
    return cs.PRData(**base)


def test_classify_pr_excluded_when_bot_never_ran():
    pr = _pr(ready_for_review_at=None)
    score = cs.classify_pr(pr)
    assert score.excluded is True


def test_classify_pr_hit_when_claim_matches_postmerge_fix():
    pr = _pr(
        comments=[
            cs.Comment(created_at="2026-08-01T10:00:00Z", body="вердикт: дефект src/foo.py:42 — 80")
        ],
        fix_commits=[cs.FixCommit(message="fix: bug", files={"src/foo.py": [42]})],
    )
    score = cs.classify_pr(pr)
    assert score.excluded is False
    assert score.hits == 1
    assert score.bucket == 0


def test_classify_pr_hit_when_claim_matches_bot_finding():
    pr = _pr(
        comments=[
            cs.Comment(created_at="2026-08-01T10:00:00Z", body="вердикт: дефект src/foo.py:42 — 80")
        ],
        bot_findings=[cs.BotFinding(file="src/foo.py", line=42)],
    )
    score = cs.classify_pr(pr)
    assert score.hits == 1
    assert score.bucket == 0


def test_classify_pr_bucket_when_claim_unconfirmed():
    pr = _pr(
        comments=[
            cs.Comment(created_at="2026-08-01T10:00:00Z", body="вердикт: дефект src/foo.py:42 — 80")
        ],
    )
    score = cs.classify_pr(pr)
    assert score.hits == 0
    assert score.bucket == 1


def test_classify_pr_miss_when_postmerge_fix_line_unclaimed():
    pr = _pr(
        comments=[cs.Comment(created_at="2026-08-01T10:00:00Z", body="вердикт: чисто")],
        fix_commits=[cs.FixCommit(message="fix: bug", files={"src/foo.py": [42]})],
    )
    score = cs.classify_pr(pr)
    assert score.misses == 1
    assert score.correct_rejections == 0


def test_classify_pr_correct_rejection_when_clean_and_no_gt_defect():
    pr = _pr(comments=[cs.Comment(created_at="2026-08-01T10:00:00Z", body="вердикт: чисто")])
    score = cs.classify_pr(pr)
    assert score.correct_rejections == 1
    assert score.misses == 0


def test_classify_pr_comment_after_ready_for_review_excluded_from_scoring():
    pr = _pr(
        comments=[
            cs.Comment(created_at="2026-08-01T13:00:00Z", body="вердикт: дефект src/foo.py:42 — 80")
        ],
        bot_findings=[cs.BotFinding(file="src/foo.py", line=42)],
    )
    score = cs.classify_pr(pr)
    # comment landed after ready_for_review -> doesn't exist for scoring;
    # no verdict comment in-window means no claims and no clean verdict either
    assert score.hits == 0
    assert score.bucket == 0
    assert score.correct_rejections == 0
    assert score.misses == 0


# --- sensitivity / criterion (SDT) ----------------------------------------


def test_compute_sensitivity_basic():
    matrix = cs.Matrix(hits=8, misses=2, false_alarms=1, correct_rejections=9)
    assert cs.compute_sensitivity(matrix) == pytest.approx(0.8)


def test_compute_sensitivity_none_when_no_positive_trials():
    matrix = cs.Matrix(hits=0, misses=0, false_alarms=3, correct_rejections=7)
    assert cs.compute_sensitivity(matrix) is None


def test_compute_criterion_symmetric_case_is_zero():
    # hit rate == false alarm rate -> unbiased criterion ~ 0
    matrix = cs.Matrix(hits=5, misses=5, false_alarms=5, correct_rejections=5)
    assert cs.compute_criterion(matrix) == pytest.approx(0.0, abs=1e-9)


def test_compute_criterion_conservative_when_claims_rare():
    # hit rate at chance (0.5) but false-alarm rate well below chance (0.1) ->
    # positive criterion (conservative: reluctant to claim a defect at all)
    # per Macmillan & Creelman convention. Note hit_rate + fa_rate == 1 would
    # be the *unbiased* case regardless of magnitude (e.g. 0.9/0.1) — this
    # case is asymmetric, which is what conservative bias actually looks like.
    matrix = cs.Matrix(hits=5, misses=5, false_alarms=1, correct_rejections=9)
    assert cs.compute_criterion(matrix) > 0


# --- protocol constants loader --------------------------------------------


def test_load_protocol_constants_reads_real_doc():
    constants = cs.load_protocol_constants(PROTOCOL_PATH)
    assert constants.min_verdicts == 10
    assert constants.horizon_days == 60
    assert constants.horizon_prs == 100


# --- stop rules -------------------------------------------------------


def test_series_void_below_min_verdicts():
    constants = cs.ProtocolConstants(min_verdicts=10, horizon_days=60, horizon_prs=100)
    assert cs.check_series_validity(scored_verdicts=9, constants=constants) is False
    assert cs.check_series_validity(scored_verdicts=10, constants=constants) is True


def test_bucket_dominance_blocks_conclusion():
    assert cs.check_bucket_dominance(bucket=6, core=5) is True
    assert cs.check_bucket_dominance(bucket=4, core=5) is False


# --- CLI envelope -------------------------------------------------------


def test_cli_aggregates_prs_from_stdin_envelope():
    envelope = {
        "prs": [
            {
                "number": 1,
                "ready_for_review_at": "2026-08-01T12:00:00Z",
                "comments": [
                    {
                        "created_at": "2026-08-01T10:00:00Z",
                        "body": "вердикт: дефект src/foo.py:42 — 80",
                    }
                ],
                "bot_findings": [{"file": "src/foo.py", "line": 42}],
                "fix_commits": [],
            },
            {
                "number": 2,
                "ready_for_review_at": None,
                "comments": [],
                "bot_findings": [],
                "fix_commits": [],
            },
        ]
    }
    result = subprocess.run(
        [sys.executable, str(REPO_ROOT / "scripts" / "competence_scoring.py")],
        input=json.dumps(envelope),
        capture_output=True,
        text=True,
        check=True,
    )
    output = json.loads(result.stdout)
    assert output["matrix"]["hits"] == 1
    assert output["excluded_prs"] == [2]
    assert output["scored_prs"] == 1
