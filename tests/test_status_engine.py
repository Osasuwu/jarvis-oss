"""Tests for status_engine pure-function module (#1013).

Verifies four deterministic detectors, ranking, provenance contract, and
the contradiction-detector prefilter. Follows the fixture pattern from
test_rework_policy.py: in-memory fixtures, no I/O.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from status_engine import (
    DECISION_FOLLOWTHROUGH_STALE_DAYS,
    DECISION_PREFILTER_DAYS,
    FRESHNESS_AGE_SECONDS,
    MEMORY_GIT_CONTRADICTION,
    STALE_BACKLOG_DAYS,
    STALE_INPROGRESS_DAYS,
    TOP_N_CAP,
    Baseline,
    ContradictionVerdict,
    DecisionInfo,
    Delta,
    DetectorHit,
    Digest,
    HealthVerdict,
    IssueInfo,
    Provenance,
    RepoState,
    analyze,
    build_contradiction_prefilter,
    compute_health_verdict,
    deserialize_contradiction_cache,
    detect_blocker_cascade,
    detect_decision_without_followthrough,
    detect_priority_inversion,
    detect_stale_backlog,
    detect_stale_in_progress,
    fold_contradiction_verdicts,
    rank_detector_hits,
    serialize_contradiction_cache,
)

# ============================================================================
# Fixture helpers
# ============================================================================


def _days_ago(days: float) -> str:
    """Return ISO 8601 string for `days` ago in UTC."""
    return (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()


def make_issue(
    number: int,
    title: str = "",
    state: str = "open",
    labels: list[str] | None = None,
    updated_days_ago: float = 0,
    is_blocked: bool = False,
    blocks: list[int] | None = None,
) -> IssueInfo:
    """Helper to construct an IssueInfo fixture."""
    return IssueInfo(
        number=number,
        title=title or f"Issue #{number}",
        state=state,
        labels=labels or [],
        updated_at=_days_ago(updated_days_ago),
        is_blocked=is_blocked,
        blocks=blocks or [],
    )


def make_state(
    repo: str,
    issues: list[IssueInfo] | None = None,
    provenance: Provenance | None = None,
) -> RepoState:
    """Helper to construct a RepoState fixture."""
    return RepoState(
        repo=repo,
        open_issues=issues or [],
        provenance=provenance or Provenance(ran=True, ok=True, age=0),
    )


def make_baseline(
    repos: dict[str, RepoState] | None = None,
    provenance: dict[str, Provenance] | None = None,
) -> Baseline:
    """Helper to construct a Baseline fixture."""
    return Baseline(
        repos=repos or {},
        provenance=provenance or {},
    )


def make_delta(
    repos: dict[str, RepoState] | None = None,
) -> Delta:
    """Helper to construct a Delta fixture."""
    return Delta(repos=repos or {})


def make_decision(
    decision_id: str = "dec-1",
    decision: str = "",
    created_days_ago: float = 0,
    project: str | None = None,
) -> DecisionInfo:
    """Helper to construct a DecisionInfo fixture."""
    return DecisionInfo(
        decision_id=decision_id,
        decision=decision,
        created_at=_days_ago(created_days_ago),
        project=project,
    )


_NOW = datetime.now(timezone.utc)


# ============================================================================
# AC: Pure function — module-level smoke
# ============================================================================


class TestPureFunctionContract:
    """analyze() is a pure function — no I/O, deterministic with same inputs."""

    def test_analyze_returns_digest(self):
        """analyze with empty inputs returns a green Digest."""
        baseline = make_baseline()
        delta = make_delta()
        result = analyze(baseline, delta, [])
        assert isinstance(result, Digest)
        assert isinstance(result.health, HealthVerdict)
        assert isinstance(result.detector_hits, list)
        assert isinstance(result.ranking, list)
        assert isinstance(result.provenance, dict)

    def test_digest_has_required_fields(self):
        """Digest carries health, detector_hits, ranking, provenance."""
        result = analyze(make_baseline(), make_delta(), [])
        assert hasattr(result, "health")
        assert hasattr(result, "detector_hits")
        assert hasattr(result, "ranking")
        assert hasattr(result, "provenance")


# ============================================================================
# AC: Stale-in-progress detector
# ============================================================================


class TestStaleInProgressDetector:
    """stale-in-progress: issues with status:in-progress past threshold."""

    def test_stale_issue_detected(self):
        """AC: issue in-progress >3 days without update → hit."""
        baseline = make_baseline(
            {
                "jarvis": make_state(
                    "jarvis",
                    issues=[
                        make_issue(
                            1,
                            labels=["status:in-progress"],
                            updated_days_ago=STALE_INPROGRESS_DAYS + 1,
                        ),
                    ],
                ),
            }
        )
        hits = detect_stale_in_progress(baseline, make_delta(), [])
        assert len(hits) == 1
        assert hits[0].detector == "stale-in-progress"
        assert hits[0].issue_number == 1
        assert hits[0].severity == "major"

    def test_recent_in_progress_not_stale(self):
        """AC: in-progress issue updated today → no hit."""
        baseline = make_baseline(
            {
                "jarvis": make_state(
                    "jarvis",
                    issues=[
                        make_issue(1, labels=["status:in-progress"], updated_days_ago=0.1),
                    ],
                ),
            }
        )
        hits = detect_stale_in_progress(baseline, make_delta(), [])
        assert len(hits) == 0

    def test_no_label_not_stale(self):
        """Issue without status:in-progress label → no hit regardless of age."""
        baseline = make_baseline(
            {
                "jarvis": make_state(
                    "jarvis",
                    issues=[
                        make_issue(1, labels=["bug"], updated_days_ago=10),
                    ],
                ),
            }
        )
        hits = detect_stale_in_progress(baseline, make_delta(), [])
        assert len(hits) == 0

    def test_delta_overrides_baseline(self):
        """Delta repo state takes priority over baseline."""
        baseline = make_baseline(
            {
                "jarvis": make_state(
                    "jarvis",
                    issues=[
                        make_issue(
                            1, labels=["status:in-progress"], updated_days_ago=10
                        ),  # stale in baseline
                    ],
                ),
            }
        )
        delta = make_delta(
            {
                "jarvis": make_state(
                    "jarvis",
                    issues=[
                        make_issue(
                            1, labels=["status:in-progress"], updated_days_ago=0.1
                        ),  # fresh in delta
                    ],
                ),
            }
        )
        hits = detect_stale_in_progress(baseline, delta, [])
        assert len(hits) == 0

    def test_boundary_below_threshold_not_stale(self):
        """Edge: just below STALE_INPROGRESS_DAYS → not stale (> not >=)."""
        baseline = make_baseline(
            {
                "jarvis": make_state(
                    "jarvis",
                    issues=[
                        make_issue(
                            1,
                            labels=["status:in-progress"],
                            updated_days_ago=STALE_INPROGRESS_DAYS - 0.01,
                        ),
                    ],
                ),
            }
        )
        hits = detect_stale_in_progress(baseline, make_delta(), [])
        assert len(hits) == 0


# ============================================================================
# AC: Priority-inversion detector
# ============================================================================


class TestPriorityInversionDetector:
    """priority-inversion: high-priority stalled while low-priority active."""

    def test_priority_inversion_detected(self):
        """AC: P0 issue stale, P2 issue active → hit."""
        baseline = make_baseline(
            {
                "jarvis": make_state(
                    "jarvis",
                    issues=[
                        make_issue(
                            1, labels=["priority:P0"], updated_days_ago=STALE_INPROGRESS_DAYS + 2
                        ),
                        make_issue(2, labels=["priority:P2"], updated_days_ago=0.1),
                    ],
                ),
            }
        )
        hits = detect_priority_inversion(baseline, make_delta(), [])
        assert len(hits) == 1
        assert hits[0].detector == "priority-inversion"
        assert hits[0].issue_number == 1
        assert hits[0].severity == "critical"

    def test_no_inversion_when_all_active(self):
        """All priorities active → no inversion."""
        baseline = make_baseline(
            {
                "jarvis": make_state(
                    "jarvis",
                    issues=[
                        make_issue(1, labels=["priority:P0"], updated_days_ago=0.1),
                        make_issue(2, labels=["priority:P2"], updated_days_ago=0.1),
                    ],
                ),
            }
        )
        hits = detect_priority_inversion(baseline, make_delta(), [])
        assert len(hits) == 0

    def test_no_inversion_when_low_priority_inactive(self):
        """All issues stale → no inversion (no active low-pri work)."""
        baseline = make_baseline(
            {
                "jarvis": make_state(
                    "jarvis",
                    issues=[
                        make_issue(
                            1, labels=["priority:P0"], updated_days_ago=STALE_INPROGRESS_DAYS + 5
                        ),
                        make_issue(
                            2, labels=["priority:P2"], updated_days_ago=STALE_INPROGRESS_DAYS + 5
                        ),
                    ],
                ),
            }
        )
        hits = detect_priority_inversion(baseline, make_delta(), [])
        assert len(hits) == 0

    def test_no_inversion_without_priority_labels(self):
        """Issues without priority labels → no inversion hits."""
        baseline = make_baseline(
            {
                "jarvis": make_state(
                    "jarvis",
                    issues=[
                        make_issue(
                            1,
                            labels=["status:in-progress"],
                            updated_days_ago=STALE_INPROGRESS_DAYS + 2,
                        ),
                        make_issue(2, labels=["bug"], updated_days_ago=0.1),
                    ],
                ),
            }
        )
        hits = detect_priority_inversion(baseline, make_delta(), [])
        assert len(hits) == 0

    def test_priority_critical_treated_as_P0(self):
        """priority:critical label treated same as P0."""
        baseline = make_baseline(
            {
                "jarvis": make_state(
                    "jarvis",
                    issues=[
                        make_issue(
                            1,
                            labels=["priority:critical"],
                            updated_days_ago=STALE_INPROGRESS_DAYS + 2,
                        ),
                        make_issue(2, labels=["priority:P2"], updated_days_ago=0.1),
                    ],
                ),
            }
        )
        hits = detect_priority_inversion(baseline, make_delta(), [])
        assert len(hits) == 1


# ============================================================================
# AC: Decision-without-followthrough detector
# ============================================================================


class TestDecisionWithoutFollowthrough:
    """decision-without-followthrough (#1057): flag a decision-referenced open
    issue only when it has had NO movement since the latest referencing
    decision AND that decision is older than the age-gate. Real no-movement
    check via issue.updated_at vs decision.created_at — not "any open ref"."""

    _STALE = DECISION_FOLLOWTHROUGH_STALE_DAYS

    def test_untouched_since_decision_flagged(self):
        """AC1+AC2: decision older than the gate, issue untouched since the
        decision → hit."""
        baseline = make_baseline(
            {
                "jarvis": make_state(
                    "jarvis",
                    issues=[make_issue(42, updated_days_ago=self._STALE + 20)],
                ),
            }
        )
        decisions = [
            make_decision(
                "dec-1",
                decision="We should fix #42 via a new PR",
                created_days_ago=self._STALE + 6,
                project="jarvis",
            ),
        ]
        hits = detect_decision_without_followthrough(baseline, make_delta(), decisions)
        assert len(hits) == 1
        assert hits[0].detector == "decision-without-followthrough"
        assert hits[0].issue_number == 42
        assert hits[0].severity == "major"

    def test_moved_after_decision_not_flagged(self):
        """AC1: issue was updated AFTER the decision → real movement, no hit."""
        baseline = make_baseline(
            {
                "jarvis": make_state(
                    "jarvis",
                    issues=[make_issue(42, updated_days_ago=1)],
                ),
            }
        )
        decisions = [
            make_decision(
                "dec-1",
                decision="Fix #42",
                created_days_ago=self._STALE + 6,
                project="jarvis",
            ),
        ]
        hits = detect_decision_without_followthrough(baseline, make_delta(), decisions)
        assert len(hits) == 0

    def test_untouched_but_within_age_gate_not_flagged(self):
        """AC2: no movement since decision, but decision is younger than the
        age-gate → not yet stale, no hit."""
        baseline = make_baseline(
            {
                "jarvis": make_state(
                    "jarvis",
                    issues=[make_issue(42, updated_days_ago=self._STALE - 2)],
                ),
            }
        )
        decisions = [
            make_decision(
                "dec-1",
                decision="Fix #42",
                created_days_ago=self._STALE - 4,
                project="jarvis",
            ),
        ]
        hits = detect_decision_without_followthrough(baseline, make_delta(), decisions)
        assert len(hits) == 0

    def test_dedup_one_issue_many_decisions(self):
        """AC4: one issue referenced by 3 stale decisions → exactly 1 hit whose
        description lists all 3 referencing decision IDs."""
        baseline = make_baseline(
            {
                "jarvis": make_state(
                    "jarvis",
                    issues=[make_issue(42, updated_days_ago=self._STALE + 30)],
                ),
            }
        )
        decisions = [
            make_decision(
                "dec-a",
                decision="Plan #42",
                created_days_ago=self._STALE + 10,
                project="jarvis",
            ),
            make_decision(
                "dec-b",
                decision="Revisit #42",
                created_days_ago=self._STALE + 5,
                project="jarvis",
            ),
            make_decision(
                "dec-c",
                decision="Still #42",
                created_days_ago=self._STALE + 2,
                project="jarvis",
            ),
        ]
        hits = detect_decision_without_followthrough(baseline, make_delta(), decisions)
        assert len(hits) == 1
        assert hits[0].issue_number == 42
        for dec_id in ("dec-a", "dec-b", "dec-c"):
            assert dec_id in hits[0].description

    def test_project_scoped_no_cross_repo_collision(self):
        """AC5: a jarvis decision referencing #50 must NOT flag redrobot#50 —
        matching is scoped to the decision's own project."""
        baseline = make_baseline(
            {
                "jarvis": make_state(
                    "jarvis",
                    issues=[make_issue(50, updated_days_ago=self._STALE + 30)],
                ),
                "redrobot": make_state(
                    "redrobot",
                    issues=[make_issue(50, updated_days_ago=self._STALE + 30)],
                ),
            }
        )
        decisions = [
            make_decision(
                "dec-1",
                decision="Fix #50",
                created_days_ago=self._STALE + 5,
                project="jarvis",
            ),
        ]
        hits = detect_decision_without_followthrough(baseline, make_delta(), decisions)
        assert len(hits) == 1
        assert hits[0].repo == "jarvis"
        assert hits[0].issue_number == 50

    def test_empty_project_any_repo_fallback(self):
        """AC5: a decision with no project falls back to any-repo matching."""
        baseline = make_baseline(
            {
                "jarvis": make_state(
                    "jarvis",
                    issues=[make_issue(50, updated_days_ago=self._STALE + 30)],
                ),
            }
        )
        decisions = [
            make_decision(
                "dec-1",
                decision="Fix #50",
                created_days_ago=self._STALE + 5,
                project=None,
            ),
        ]
        hits = detect_decision_without_followthrough(baseline, make_delta(), decisions)
        assert len(hits) == 1
        assert hits[0].issue_number == 50

    def test_decision_no_refs_no_hit(self):
        """Decision without #NNN ref → no hit."""
        baseline = make_baseline(
            {
                "jarvis": make_state(
                    "jarvis",
                    issues=[make_issue(42, updated_days_ago=self._STALE + 30)],
                ),
            }
        )
        decisions = [
            make_decision(
                "dec-1",
                decision="Refactor the auth module",
                created_days_ago=self._STALE + 5,
                project="jarvis",
            ),
        ]
        hits = detect_decision_without_followthrough(baseline, make_delta(), decisions)
        assert len(hits) == 0

    def test_decision_refers_to_closed_issue(self):
        """AC8: decision referencing an issue NOT in open_issues → no hit."""
        baseline = make_baseline(
            {
                "jarvis": make_state(
                    "jarvis",
                    issues=[make_issue(1, state="closed")],
                ),
            }
        )
        decisions = [
            make_decision(
                "dec-1",
                decision="See #999",
                created_days_ago=self._STALE + 5,
                project="jarvis",
            ),
        ]
        hits = detect_decision_without_followthrough(baseline, make_delta(), decisions)
        assert len(hits) == 0

    def test_blank_updated_at_fail_silent(self):
        """AC8: a blank/malformed issue timestamp parses to age 0 (fresh), so
        the movement check treats it as recently-touched and does NOT flag —
        documented fail-silent behavior (false-negative over false-positive)."""
        baseline = make_baseline(
            {
                "jarvis": make_state(
                    "jarvis",
                    issues=[IssueInfo(number=42, updated_at="")],
                ),
            }
        )
        decisions = [
            make_decision(
                "dec-1",
                decision="Fix #42",
                created_days_ago=self._STALE + 5,
                project="jarvis",
            ),
        ]
        hits = detect_decision_without_followthrough(baseline, make_delta(), decisions)
        assert len(hits) == 0


# ============================================================================
# AC: Blocker-cascade detector
# ============================================================================


class TestBlockerCascadeDetector:
    """blocker-cascade: surface root blockers."""

    def test_root_blocker_surfaced(self):
        """AC: issue blocking others but not blocked → root blocker hit."""
        baseline = make_baseline(
            {
                "jarvis": make_state(
                    "jarvis",
                    issues=[
                        make_issue(1, blocks=[2, 3]),  # root blocker
                        make_issue(2, is_blocked=True),  # transitively blocked
                        make_issue(3, is_blocked=True),  # transitively blocked
                    ],
                ),
            }
        )
        hits = detect_blocker_cascade(baseline, make_delta(), [])
        assert len(hits) == 1
        assert hits[0].detector == "blocker-cascade"
        assert hits[0].issue_number == 1
        assert hits[0].severity == "critical"

    def test_no_blockers_no_hits(self):
        """No blocking relationships → no hits."""
        baseline = make_baseline(
            {
                "jarvis": make_state(
                    "jarvis",
                    issues=[
                        make_issue(1),
                        make_issue(2),
                    ],
                ),
            }
        )
        hits = detect_blocker_cascade(baseline, make_delta(), [])
        assert len(hits) == 0

    def test_transitive_chain_surfaces_root(self):
        """AC: A→B→C chain surfaces A (root), not B or C."""
        baseline = make_baseline(
            {
                "jarvis": make_state(
                    "jarvis",
                    issues=[
                        make_issue(1, blocks=[2]),
                        make_issue(2, is_blocked=True, blocks=[3]),
                        make_issue(3, is_blocked=True),
                    ],
                ),
            }
        )
        hits = detect_blocker_cascade(baseline, make_delta(), [])
        assert len(hits) == 1
        assert hits[0].issue_number == 1


# ============================================================================
# AC: Contradiction-detector prefilter
# ============================================================================


class TestContradictionPrefilter:
    """build_contradiction_prefilter returns ≤14-day decision↔issue pairs."""

    def test_recent_decision_with_ref_included(self):
        """Recent decision (<14d) with #NNN → included in prefilter."""
        decisions = [
            make_decision(
                "dec-1", decision="Fix #42", created_days_ago=DECISION_PREFILTER_DAYS - 1
            ),
        ]
        result = build_contradiction_prefilter(decisions, make_baseline())
        assert len(result) == 1
        assert result[0][1] == 42

    def test_old_decision_excluded(self):
        """Old decision (>14d) → excluded from prefilter."""
        decisions = [
            make_decision(
                "dec-1", decision="Fix #42", created_days_ago=DECISION_PREFILTER_DAYS + 1
            ),
        ]
        result = build_contradiction_prefilter(decisions, make_baseline())
        assert len(result) == 0

    def test_decision_without_ref_excluded(self):
        """Decision without #NNN ref → excluded."""
        decisions = [
            make_decision("dec-1", decision="Refactor auth", created_days_ago=1),
        ]
        result = build_contradiction_prefilter(decisions, make_baseline())
        assert len(result) == 0

    def test_multiple_refs_in_one_decision(self):
        """One decision referencing multiple issues → multiple pairs."""
        decisions = [
            make_decision("dec-1", decision="Fix #42 and #99", created_days_ago=1),
        ]
        result = build_contradiction_prefilter(decisions, make_baseline())
        assert len(result) == 2
        refs = {r[1] for r in result}
        assert refs == {42, 99}


# ============================================================================
# AC: memory↔git contradiction — verdict folding (#1016)
# ============================================================================


def make_verdict(
    decision_id: str = "dec-1",
    issue_number: int = 42,
    repo: str = "Osasuwu/jarvis",
    verdict: str = "contradiction",
    rationale: str = "memory says shipped; issue still open",
) -> ContradictionVerdict:
    """Helper to construct a ContradictionVerdict fixture."""
    return ContradictionVerdict(
        decision_id=decision_id,
        issue_number=issue_number,
        repo=repo,
        verdict=verdict,
        rationale=rationale,
    )


class TestContradictionFolding:
    """fold_contradiction_verdicts maps verdicts → DetectorHits (AC1, AC6).

    The LLM judgment itself is the native status-record cron session — not
    tested here. This covers only the deterministic fold of its output.
    """

    def test_contradiction_verdict_becomes_hit(self):
        """A 'contradiction' verdict folds into one DetectorHit."""
        hits = fold_contradiction_verdicts([make_verdict()])
        assert len(hits) == 1
        assert hits[0].detector == MEMORY_GIT_CONTRADICTION
        assert hits[0].issue_number == 42
        assert hits[0].repo == "Osasuwu/jarvis"

    def test_rationale_carried_into_hit(self):
        """Per-candidate rationale is preserved into the hit (AC1)."""
        hits = fold_contradiction_verdicts(
            [make_verdict(rationale="decision 6f11 claims merged; PR never opened")]
        )
        assert "6f11" in hits[0].description

    def test_uncertain_verdict_dropped(self):
        """An 'uncertain' candidate is dropped, not surfaced (AC6)."""
        hits = fold_contradiction_verdicts([make_verdict(verdict="uncertain")])
        assert hits == []

    def test_no_contradiction_verdict_dropped(self):
        """A 'no_contradiction' candidate produces no hit."""
        hits = fold_contradiction_verdicts([make_verdict(verdict="no_contradiction")])
        assert hits == []

    def test_mixed_batch_only_contradictions_surface(self):
        """Only the 'contradiction' verdicts in a mixed batch fold to hits."""
        verdicts = [
            make_verdict("d1", 1, verdict="contradiction"),
            make_verdict("d2", 2, verdict="uncertain"),
            make_verdict("d3", 3, verdict="no_contradiction"),
            make_verdict("d4", 4, verdict="contradiction"),
        ]
        hits = fold_contradiction_verdicts(verdicts)
        assert {h.issue_number for h in hits} == {1, 4}

    def test_empty_verdicts_returns_empty(self):
        assert fold_contradiction_verdicts([]) == []

    def test_unknown_verdict_string_dropped(self):
        """An unrecognized verdict value is treated as uncertain → dropped."""
        hits = fold_contradiction_verdicts([make_verdict(verdict="maybe?")])
        assert hits == []


class TestContradictionCache:
    """Cached contradiction result round-trips without re-running the LLM (AC4)."""

    def test_serialize_produces_jsonable_dict(self):
        data = serialize_contradiction_cache([make_verdict()])
        # round-trips through JSON unchanged
        import json

        assert json.loads(json.dumps(data)) == data

    def test_roundtrip_preserves_verdicts(self):
        verdicts = [
            make_verdict("d1", 1, verdict="contradiction"),
            make_verdict("d2", 2, verdict="uncertain"),
        ]
        restored = deserialize_contradiction_cache(serialize_contradiction_cache(verdicts))
        assert restored == verdicts

    def test_renderer_folds_from_cache_without_llm(self):
        """Deserialized cache folds to the same hits as the live verdicts (AC4)."""
        verdicts = [
            make_verdict("d1", 1, verdict="contradiction"),
            make_verdict("d2", 2, verdict="uncertain"),
        ]
        cached = serialize_contradiction_cache(verdicts)
        from_cache = fold_contradiction_verdicts(deserialize_contradiction_cache(cached))
        from_live = fold_contradiction_verdicts(verdicts)
        assert from_cache == from_live
        assert len(from_cache) == 1  # only the contradiction survives

    def test_empty_cache_deserializes_to_empty(self):
        assert deserialize_contradiction_cache({"verdicts": []}) == []

    def test_missing_verdicts_key_tolerated(self):
        """A malformed cache (no verdicts key) deserializes to empty, not raises."""
        assert deserialize_contradiction_cache({}) == []

    def test_row_missing_mandatory_keys_tolerated(self):
        """A verdict row missing decision_id/issue_number degrades gracefully —
        the 'tolerant' contract must not raise KeyError on a partial row (C2)."""
        cache = {
            "verdicts": [
                {"repo": "o/r", "verdict": "contradiction", "rationale": "x"},
            ]
        }
        restored = deserialize_contradiction_cache(cache)
        assert len(restored) == 1
        assert restored[0].decision_id == ""
        assert restored[0].issue_number == 0

    def test_float_issue_number_coerced_to_int(self):
        """YAML can emit issue_number as a float (42.0); it must render as #42,
        not #42.0 (C2)."""
        cache = {
            "verdicts": [
                {
                    "decision_id": "d1",
                    "issue_number": 42.0,
                    "repo": "o/r",
                    "verdict": "contradiction",
                    "rationale": "x",
                },
            ]
        }
        restored = deserialize_contradiction_cache(cache)
        assert restored[0].issue_number == 42
        assert isinstance(restored[0].issue_number, int)

    def test_schema_mismatch_returns_empty(self):
        """A cache stamped with an unrecognized schema version deserializes to
        empty rather than silently producing wrong verdicts (M2)."""
        cache = {
            "schema": "contradiction-cache/v2",
            "verdicts": [
                {
                    "decision_id": "d1",
                    "issue_number": 1,
                    "repo": "o/r",
                    "verdict": "contradiction",
                    "rationale": "x",
                },
            ],
        }
        assert deserialize_contradiction_cache(cache) == []

    def test_absent_schema_key_tolerated(self):
        """A cache with no schema key (legacy/hand-written) still deserializes —
        only an explicit *mismatched* schema is rejected (M2)."""
        cache = {
            "verdicts": [
                {
                    "decision_id": "d1",
                    "issue_number": 1,
                    "repo": "o/r",
                    "verdict": "contradiction",
                    "rationale": "x",
                },
            ]
        }
        assert len(deserialize_contradiction_cache(cache)) == 1


class TestL1OnlyGuard:
    """The contradiction detector runs L1-only; analyze() never invokes the
    LLM judgment on its own (AC2).

    Behavioral guard (replaces an earlier source-grep): the meaningful L1-only
    contract is that ``analyze`` never *generates* contradiction verdicts — it
    only folds verdicts handed to it explicitly. The LLM judgment lives in the
    upstream L1 status-record cron; the intraday (L2) path calls ``analyze``
    with the default empty ``contradiction_verdicts`` and therefore pays for no
    LLM and surfaces no contradiction. Folding a *cached* verdict (already
    computed in the morning) is deterministic and free, so it is allowed.
    """

    def test_analyze_default_param_folds_nothing(self):
        """Calling analyze without the verdicts arg surfaces no contradiction
        hit — the intraday/L2 signature is unchanged and LLM-free."""
        base = make_baseline(
            provenance={"jarvis": Provenance(ran=True, ok=True, age=0)},
        )
        digest = analyze(base, make_delta(), [])
        assert all(h.detector != MEMORY_GIT_CONTRADICTION for h in digest.detector_hits)

    def test_analyze_does_not_autodetect_contradictions(self):
        """Even when a decision references an open issue, analyze with no
        verdicts produces no contradiction hit — it never runs the detector
        itself (the LLM judgment must arrive pre-computed from L1)."""
        issue = make_issue(42, labels=["status:in-progress"])
        state = make_state("Osasuwu/jarvis", issues=[issue])
        base = make_baseline(
            provenance={"jarvis": Provenance(ran=True, ok=True, age=0)},
        )
        delta = make_delta(repos={"Osasuwu/jarvis": state})
        decisions = [make_decision("d1", decision="Shipped #42", created_days_ago=1)]
        digest = analyze(base, delta, decisions)
        assert all(h.detector != MEMORY_GIT_CONTRADICTION for h in digest.detector_hits)


class TestAnalyzeFoldsContradictions:
    """analyze() folds explicitly-passed cached verdicts into the digest so
    they participate in ranking + health (AC4)."""

    def _green_baseline(self) -> Baseline:
        return make_baseline(
            provenance={"jarvis": Provenance(ran=True, ok=True, age=0)},
        )

    def test_contradiction_verdict_surfaces_as_hit(self):
        digest = analyze(
            self._green_baseline(),
            make_delta(),
            [],
            contradiction_verdicts=[make_verdict()],
        )
        hits = [h for h in digest.detector_hits if h.detector == MEMORY_GIT_CONTRADICTION]
        assert len(hits) == 1
        assert hits[0].issue_number == 42

    def test_uncertain_verdict_not_folded(self):
        digest = analyze(
            self._green_baseline(),
            make_delta(),
            [],
            contradiction_verdicts=[make_verdict(verdict="uncertain")],
        )
        assert all(h.detector != MEMORY_GIT_CONTRADICTION for h in digest.detector_hits)

    def test_folded_hit_participates_in_ranking(self):
        digest = analyze(
            self._green_baseline(),
            make_delta(),
            [],
            contradiction_verdicts=[make_verdict()],
        )
        ranked_detectors = {item.detector_hit.detector for item in digest.ranking}
        assert MEMORY_GIT_CONTRADICTION in ranked_detectors

    def test_folded_hit_flips_health(self):
        """A folded contradiction on an otherwise-green baseline makes the
        health verdict unhealthy — it is a real anomaly, not just metadata."""
        green = analyze(self._green_baseline(), make_delta(), [])
        assert green.health.ok is True
        unhealthy = analyze(
            self._green_baseline(),
            make_delta(),
            [],
            contradiction_verdicts=[make_verdict()],
        )
        assert unhealthy.health.ok is False

    def test_fold_is_deterministic(self):
        args = (self._green_baseline(), make_delta(), [])
        kw = {"contradiction_verdicts": [make_verdict()]}
        assert analyze(*args, **kw).detector_hits == analyze(*args, **kw).detector_hits

    def test_cached_cache_folds_through_analyze_without_llm(self):
        """End-to-end AC4: serialize → deserialize → analyze surfaces the same
        contradiction hit the live verdicts would, with no LLM in the path."""
        verdicts = [
            make_verdict("d1", 1, verdict="contradiction"),
            make_verdict("d2", 2, verdict="uncertain"),
        ]
        cache = serialize_contradiction_cache(verdicts)
        restored = deserialize_contradiction_cache(cache)
        digest = analyze(
            self._green_baseline(),
            make_delta(),
            [],
            contradiction_verdicts=restored,
        )
        hits = [h for h in digest.detector_hits if h.detector == MEMORY_GIT_CONTRADICTION]
        assert len(hits) == 1  # only the contradiction survives
        assert hits[0].issue_number == 1

    # NOTE: the "analyze never autodetects contradictions" assertion lives in
    # TestL1OnlyGuard.test_analyze_does_not_autodetect_contradictions — not
    # duplicated here (N4).


# ============================================================================
# AC: Top-N ranking
# ============================================================================


class TestRanking:
    """rank_detector_hits returns at most TOP_N_CAP items, sorted by severity."""

    def test_at_most_top_n_returned(self):
        """AC: ranking caps at TOP_N_CAP items."""
        hits = [
            DetectorHit(detector=f"test-{i}", severity="minor", repo="jarvis")
            for i in range(TOP_N_CAP + 5)
        ]
        ranked = rank_detector_hits(hits)
        assert len(ranked) <= TOP_N_CAP

    def test_critical_before_major_before_minor(self):
        """AC: critical items ranked before major before minor."""
        hits = [
            DetectorHit(detector="minor-hit", severity="minor", repo="jarvis"),
            DetectorHit(detector="critical-hit", severity="critical", repo="jarvis"),
            DetectorHit(detector="major-hit", severity="major", repo="jarvis"),
        ]
        ranked = rank_detector_hits(hits)
        assert ranked[0].detector_hit.detector == "critical-hit"
        assert ranked[1].detector_hit.detector == "major-hit"
        assert ranked[2].detector_hit.detector == "minor-hit"

    def test_rank_numbers_are_one_indexed(self):
        """Rank starts at 1 and increments."""
        hits = [
            DetectorHit(detector="a", severity="critical", repo="jarvis"),
            DetectorHit(detector="b", severity="major", repo="jarvis"),
        ]
        ranked = rank_detector_hits(hits)
        assert ranked[0].rank == 1
        assert ranked[1].rank == 2

    def test_empty_hits_returns_empty(self):
        """No hits → empty ranking."""
        assert rank_detector_hits([]) == []

    def test_top_n_is_documented_constant(self):
        """AC: TOP_N_CAP is a module-level documented constant."""
        assert isinstance(TOP_N_CAP, int)
        assert TOP_N_CAP > 0


# ============================================================================
# AC: Health verdict — provenance contract
# ============================================================================


class TestHealthVerdict:
    """Health is GREEN only if all sources fresh/ok AND no detector hits."""

    def test_green_when_all_sources_fresh_no_hits(self):
        """AC: all sources ok + fresh, no hits → health ok=True."""
        health = compute_health_verdict(
            make_baseline(
                provenance={"gh:jarvis": Provenance(ran=True, ok=True, age=0)},
            ),
            [],
        )
        assert health.ok is True

    def test_not_green_when_source_did_not_run(self):
        """AC: source with ran=False → health not green."""
        health = compute_health_verdict(
            make_baseline(
                provenance={"gh:jarvis": Provenance(ran=False, ok=False, age=0)},
            ),
            [],
        )
        assert health.ok is False
        assert "did not run" in health.reason

    def test_not_green_when_source_failed(self):
        """AC: source with ok=False → health not green."""
        health = compute_health_verdict(
            make_baseline(
                provenance={"gh:jarvis": Provenance(ran=True, ok=False, age=0)},
            ),
            [],
        )
        assert health.ok is False
        assert "ok=False" in health.reason

    def test_not_green_when_source_stale(self):
        """AC: source with age > FRESHNESS_AGE_SECONDS → health not green."""
        health = compute_health_verdict(
            make_baseline(
                provenance={
                    "gh:jarvis": Provenance(
                        ran=True,
                        ok=True,
                        age=FRESHNESS_AGE_SECONDS + 1,
                    )
                },
            ),
            [],
        )
        assert health.ok is False
        assert "stale" in health.reason

    def test_not_green_with_detector_hits(self):
        """AC: fresh sources but with detector hits → health not green."""
        health = compute_health_verdict(
            make_baseline(
                provenance={"gh:jarvis": Provenance(ran=True, ok=True, age=0)},
            ),
            [
                DetectorHit(detector="test", severity="major", repo="jarvis"),
            ],
        )
        assert health.ok is False
        assert "detector hit" in health.reason

    def test_green_requires_all_sources_fresh(self):
        """Multiple sources: one stale → not green."""
        health = compute_health_verdict(
            make_baseline(
                provenance={
                    "gh:jarvis": Provenance(ran=True, ok=True, age=0),
                    "gh:redrobot": Provenance(
                        ran=True,
                        ok=True,
                        age=FRESHNESS_AGE_SECONDS + 100,
                    ),
                },
            ),
            [],
        )
        assert health.ok is False

    def test_age_none_does_not_crash(self):
        """A source with ran=True, ok=True, age=None (unknown data age — e.g. a
        status snapshot written without a parseable generated_at) must not crash
        the freshness comparison. age=None means 'age unknown', not stale (C1)."""
        health = compute_health_verdict(
            make_baseline(
                provenance={
                    "status_snapshot": Provenance(ran=True, ok=True, age=None),
                },
            ),
            [],
        )
        # Does not raise; unknown age is treated as not-stale (lenient, non-crash).
        assert health.ok is True


# ============================================================================
# AC2/AC3 (#1059): stale-backlog detector
# ============================================================================


def _backlog_state(repo="Osasuwu/jarvis", issues=None):
    return make_state(repo, issues=issues or [])


class TestStaleBacklog:
    """AC2: label-driven candidate selection; AC3: per-repo info aggregation.

    Source of truth is ``status:*`` labels (grill #1065, decision 0a02d3ee):
    - ``ready``   = issue carries ``status:ready``.
    - ``backlog`` = open ∧ carries none of ``status:{in-progress, ready}``.
    """

    def test_backlog_idle_flagged(self):
        """Backlog (no status label) + idle ≥30d → candidate (time-only)."""
        baseline = make_baseline(
            repos={
                "Osasuwu/jarvis": _backlog_state(
                    issues=[make_issue(42, updated_days_ago=40, labels=[])],
                )
            }
        )
        hits = detect_stale_backlog(baseline, make_delta(), [])
        assert len(hits) == 1
        assert hits[0].severity == "info"
        assert hits[0].issue_number is None
        assert "#42" in hits[0].description

    def test_backlog_fresh_not_flagged(self):
        """Backlog but idle <30d → not a candidate."""
        baseline = make_baseline(
            repos={
                "Osasuwu/jarvis": _backlog_state(
                    issues=[make_issue(42, updated_days_ago=5, labels=[])],
                )
            }
        )
        assert detect_stale_backlog(baseline, make_delta(), []) == []

    def test_backlog_with_unrelated_label_flagged(self):
        """A non-status label doesn't exclude an issue from backlog."""
        baseline = make_baseline(
            repos={
                "Osasuwu/jarvis": _backlog_state(
                    issues=[make_issue(42, updated_days_ago=40, labels=["enhancement"])],
                )
            }
        )
        assert len(detect_stale_backlog(baseline, make_delta(), [])) == 1

    def test_ready_idle_no_decision_flagged(self):
        """status:ready + idle ≥30d + no referencing decision → candidate."""
        baseline = make_baseline(
            repos={
                "Osasuwu/jarvis": _backlog_state(
                    issues=[make_issue(7, updated_days_ago=40, labels=["status:ready"])],
                )
            }
        )
        hits = detect_stale_backlog(baseline, make_delta(), [])
        assert len(hits) == 1
        assert "#7" in hits[0].description

    def test_ready_with_decision_not_flagged(self):
        """status:ready + idle ≥30d but a project decision references it → suppressed."""
        baseline = make_baseline(
            repos={
                "Osasuwu/jarvis": _backlog_state(
                    issues=[make_issue(7, updated_days_ago=40, labels=["status:ready"])],
                )
            }
        )
        decisions = [make_decision(decision="Ship #7 next sprint", project="Osasuwu/jarvis")]
        assert detect_stale_backlog(baseline, make_delta(), decisions) == []

    def test_ready_decision_short_project_slug_matches(self):
        """Decision.project may be the short slug (`jarvis`) — still matches."""
        baseline = make_baseline(
            repos={
                "Osasuwu/jarvis": _backlog_state(
                    issues=[make_issue(7, updated_days_ago=40, labels=["status:ready"])],
                )
            }
        )
        decisions = [make_decision(decision="Do #7", project="jarvis")]
        assert detect_stale_backlog(baseline, make_delta(), decisions) == []

    def test_in_progress_not_flagged(self):
        """status:in-progress excludes an issue from backlog (owned by stale-in-progress)."""
        baseline = make_baseline(
            repos={
                "Osasuwu/jarvis": _backlog_state(
                    issues=[
                        make_issue(
                            9,
                            updated_days_ago=99,
                            labels=["status:in-progress"],
                        )
                    ],
                )
            }
        )
        assert detect_stale_backlog(baseline, make_delta(), []) == []

    def test_zero_candidates_no_hit(self):
        assert detect_stale_backlog(make_baseline(), make_delta(), []) == []

    def test_aggregate_one_hit_per_repo_oldest_first(self):
        """One info hit per repo; numbers ordered oldest-first by updated_at."""
        baseline = make_baseline(
            repos={
                "Osasuwu/jarvis": _backlog_state(
                    issues=[
                        make_issue(3, updated_days_ago=35, labels=[]),
                        make_issue(1, updated_days_ago=90, labels=[]),
                        make_issue(2, updated_days_ago=60, labels=["status:ready"]),
                    ],
                )
            }
        )
        hits = detect_stale_backlog(baseline, make_delta(), [])
        assert len(hits) == 1
        # oldest (largest age) first: #1 (90d), #2 (60d), #3 (35d)
        assert hits[0].description.index("#1") < hits[0].description.index("#2")
        assert hits[0].description.index("#2") < hits[0].description.index("#3")

    def test_info_does_not_flip_health(self):
        """AC3: an info-only hit set leaves health green."""
        verdict = compute_health_verdict(
            make_baseline(),
            [
                DetectorHit(
                    detector="stale-backlog",
                    severity="info",
                    repo="Osasuwu/jarvis",
                    description="#42",
                )
            ],
        )
        assert verdict.ok is True

    def test_info_excluded_from_ranking(self):
        """AC3: info hits never appear in 'Куда смотреть'."""
        ranked = rank_detector_hits(
            [
                DetectorHit(
                    detector="stale-backlog",
                    severity="info",
                    repo="Osasuwu/jarvis",
                    description="#42",
                )
            ]
        )
        assert ranked == []


# ============================================================================
# AC: Constants are module-level
# ============================================================================


class TestModuleConstants:
    """AC: Thresholds are module-level constants with documented defaults."""

    def test_stale_inprogress_days_constant(self):
        assert isinstance(STALE_INPROGRESS_DAYS, int)
        assert STALE_INPROGRESS_DAYS > 0

    def test_freshness_age_seconds_constant(self):
        assert isinstance(FRESHNESS_AGE_SECONDS, (int, float))
        assert FRESHNESS_AGE_SECONDS > 0

    def test_decision_prefilter_days_constant(self):
        assert isinstance(DECISION_PREFILTER_DAYS, int)
        assert DECISION_PREFILTER_DAYS > 0

    def test_stale_backlog_days_constant(self):
        assert isinstance(STALE_BACKLOG_DAYS, int)
        assert STALE_BACKLOG_DAYS == 30


# ============================================================================
# Integration tests
# ============================================================================


class TestIntegration:
    """End-to-end analyze() scenarios."""

    def test_empty_state_returns_green(self):
        """No repos, no decisions → green health, empty lists."""
        result = analyze(make_baseline(), make_delta(), [])
        assert result.health.ok is True
        assert result.detector_hits == []
        assert result.ranking == []

    def test_stale_issue_makes_health_red(self):
        """Single stale in-progress issue → hits + non-green health."""
        baseline = make_baseline(
            repos={
                "jarvis": make_state(
                    "jarvis",
                    issues=[
                        make_issue(
                            1,
                            labels=["status:in-progress"],
                            updated_days_ago=STALE_INPROGRESS_DAYS + 5,
                        ),
                    ],
                ),
            },
            provenance={"gh:jarvis": Provenance(ran=True, ok=True, age=0)},
        )
        result = analyze(baseline, make_delta(), [])
        assert len(result.detector_hits) >= 1
        assert result.health.ok is False

    def test_provenance_in_digest(self):
        """Baseline provenance is carried through to digest."""
        baseline = make_baseline(
            repos={},
            provenance={
                "gh:jarvis": Provenance(ran=True, ok=True, age=0, input_rows=10),
            },
        )
        result = analyze(baseline, make_delta(), [])
        assert "gh:jarvis" in result.provenance
        assert result.provenance["gh:jarvis"].input_rows == 10

    def test_delta_provenance_merged(self):
        """Delta repo provenance is added to digest."""
        baseline = make_baseline(
            provenance={"gh:jarvis": Provenance(ran=True, ok=True, age=0)},
        )
        delta = make_delta(
            {
                "redrobot": make_state(
                    "redrobot",
                    provenance=Provenance(ran=True, ok=True, age=100),
                ),
            }
        )
        result = analyze(baseline, delta, [])
        assert "delta:redrobot" in result.provenance
