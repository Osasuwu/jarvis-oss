"""Pure-function status synthesis engine (#1013).

Zero I/O. Houses four deterministic detectors, provenance contract, and
top-N ranking for the status-synthesis pipeline.

Public interface:
    analyze(baseline, delta, decisions, contradiction_verdicts=()) -> Digest

Constants (reversible knobs, tuned post-launch):
    STALE_INPROGRESS_DAYS = 3
    TOP_N_CAP = 3
    FRESHNESS_AGE_SECONDS = 86400  # 24h
    DECISION_PREFILTER_DAYS = 14
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Sequence


# ============================================================================
# Constants (reversible knobs, tuned post-launch)
# ============================================================================

STALE_INPROGRESS_DAYS = 3
"""Days before an in-progress issue is considered stale."""

STALE_BACKLOG_DAYS = 30
"""Days an issue may sit in a ProjectV2 Backlog/Ready status before the
stale-backlog detector flags it (#1059)."""

DECISION_FOLLOWTHROUGH_STALE_DAYS = 14
"""Days a decision-referenced issue may sit without movement before the
decision-without-followthrough detector flags it (#1057)."""

TOP_N_CAP = 3
"""Absolute cap for "Куда смотреть" ranking."""

FRESHNESS_AGE_SECONDS = 86400  # 24 hours
"""Max age in seconds for a source to be considered fresh."""

DECISION_PREFILTER_DAYS = 14
"""Max age in days for decisions considered by the contradiction prefilter."""

MEMORY_GIT_CONTRADICTION = "memory-git-contradiction"
"""Detector name for the L1-only memory↔git contradiction detector (#1016).

The judgment itself is the native status-record cron Claude session — there is
no Anthropic API call here. This engine only folds the session's verdicts into
DetectorHits (see fold_contradiction_verdicts) and round-trips the cached
result (serialize/deserialize_contradiction_cache). The detector is L1-only:
analyze() never invokes it, so intraday (L2) recomputation never pays for the
LLM pass.
"""

CONTRADICTION_CACHE_SCHEMA = "contradiction-cache/v1"
"""Schema tag for the serialized contradiction cache (AC4)."""


# ============================================================================
# Data types
# ============================================================================


@dataclass
class Provenance:
    """Provenance stamp for a data source or detector."""

    ran: bool = True
    ok: bool = True
    input_rows: int = 0
    age: float = 0.0  # seconds since data was gathered


@dataclass
class IssueInfo:
    """Lightweight issue representation for engine consumption."""

    number: int
    title: str = ""
    state: str = "open"
    labels: list[str] = field(default_factory=list)
    milestone: str | None = None
    updated_at: str = ""  # ISO 8601
    is_blocked: bool = False
    blocks: list[int] = field(default_factory=list)


@dataclass
class RepoState:
    """State snapshot of a single repository."""

    repo: str
    open_issues: list[IssueInfo] = field(default_factory=list)
    open_prs: list[dict] = field(default_factory=list)
    provenance: Provenance | None = None


@dataclass
class DecisionInfo:
    """A recorded decision from episodes table."""

    decision_id: str
    decision: str = ""
    created_at: str = ""  # ISO 8601
    project: str | None = None


@dataclass
class ContradictionVerdict:
    """One LLM judgment over a (decision, issue) prefilter candidate (#1016).

    Emitted by the native status-record cron session, not by this engine.
    `verdict` is one of: 'contradiction' (memory and git disagree),
    'no_contradiction' (they agree / benign divergence), or 'uncertain'
    (judge could not decide). Per the false-negative-over-false-positive
    posture (research b72ea66c), only 'contradiction' surfaces — both
    'uncertain' and 'no_contradiction' are dropped on fold.
    """

    decision_id: str
    issue_number: int
    repo: str = ""
    verdict: str = "uncertain"
    rationale: str = ""


@dataclass
class Baseline:
    """L1 morning baseline snapshot."""

    repos: dict[str, RepoState] = field(default_factory=dict)
    gathered_at: str = ""
    provenance: dict[str, Provenance] = field(default_factory=dict)


@dataclass
class Delta:
    """Intraday delta — lightweight current state."""

    repos: dict[str, RepoState] = field(default_factory=dict)
    gathered_at: str = ""


@dataclass
class DetectorHit:
    """A single detector firing."""

    detector: str
    severity: str  # 'critical' | 'major' | 'minor' | 'info'
    repo: str
    issue_number: int | None = None
    title: str = ""
    description: str = ""
    provenance: Provenance | None = None


@dataclass
class RankedItem:
    """A ranked item for the 'Куда смотреть' list."""

    rank: int
    detector_hit: DetectorHit
    reason: str = ""


@dataclass
class HealthVerdict:
    """Overall health verdict."""

    ok: bool
    reason: str = ""


@dataclass
class Digest:
    """Output of analyze()."""

    health: HealthVerdict
    detector_hits: list[DetectorHit] = field(default_factory=list)
    ranking: list[RankedItem] = field(default_factory=list)
    provenance: dict[str, Provenance] = field(default_factory=dict)


# ============================================================================
# Helpers
# ============================================================================


def _parse_iso_age(iso_str: str, now: datetime | None = None) -> float:
    """Parse ISO 8601 string and return age in days from now."""
    if now is None:
        now = datetime.now(timezone.utc)
    if not iso_str:
        return 0.0
    try:
        dt = datetime.fromisoformat(iso_str)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return (now - dt).total_seconds() / 86400
    except (ValueError, TypeError):
        return 0.0


def _merge_repos(baseline: Baseline, delta: Delta) -> dict[str, RepoState]:
    """Merge repos from delta over baseline for the freshest view."""
    merged: dict[str, RepoState] = {}
    for name, state in baseline.repos.items():
        merged[name] = state
    for name, state in delta.repos.items():
        merged[name] = state
    return merged


# ============================================================================
# Detector: stale-in-progress
# ============================================================================


def detect_stale_in_progress(
    baseline: Baseline,
    delta: Delta,
    decisions: list[DecisionInfo],
) -> list[DetectorHit]:
    """Detect issues labeled status:in-progress that are stale.

    An issue is stale if it has the status:in-progress label and its
    updated_at is more than STALE_INPROGRESS_DAYS in the past.
    """
    hits: list[DetectorHit] = []
    now = datetime.now(timezone.utc)

    for repo_name, repo_state in _merge_repos(baseline, delta).items():
        for issue in repo_state.open_issues:
            if "status:in-progress" not in issue.labels:
                continue
            age_days = _parse_iso_age(issue.updated_at, now)
            if age_days > STALE_INPROGRESS_DAYS:
                hits.append(
                    DetectorHit(
                        detector="stale-in-progress",
                        severity="major",
                        repo=repo_name,
                        issue_number=issue.number,
                        title=issue.title,
                        description=(
                            f"Issue #{issue.number} has been in-progress for "
                            f"{age_days:.1f} days (threshold: {STALE_INPROGRESS_DAYS}d)"
                        ),
                    )
                )

    return hits


# ============================================================================
# Detector: priority-inversion
# ============================================================================


_PRIORITY_ORDER = {
    "priority:critical": 0,
    "priority:P0": 0,
    "priority:P1": 1,
    "priority:P2": 2,
}


def detect_priority_inversion(
    baseline: Baseline,
    delta: Delta,
    decisions: list[DecisionInfo],
) -> list[DetectorHit]:
    """Detect priority inversion: lower-priority work moving while higher stalls.

    A priority inversion occurs when a P0/P1 issue has no recent activity
    while a lower-priority issue in the same repo has recent activity.
    """
    hits: list[DetectorHit] = []
    now = datetime.now(timezone.utc)

    for repo_name, repo_state in _merge_repos(baseline, delta).items():
        stalled_high: list[IssueInfo] = []
        active_low: list[IssueInfo] = []

        for issue in repo_state.open_issues:
            prio = _issue_priority(issue)
            if prio is None:
                continue

            age_days = _parse_iso_age(issue.updated_at, now)

            if prio <= 1 and age_days > STALE_INPROGRESS_DAYS:
                stalled_high.append(issue)
            elif prio > 1 and age_days <= STALE_INPROGRESS_DAYS:
                active_low.append(issue)

        if stalled_high and active_low:
            for high_issue in stalled_high:
                low_titles = ", ".join(f"#{i.number}" for i in active_low[:3])
                hits.append(
                    DetectorHit(
                        detector="priority-inversion",
                        severity="critical",
                        repo=repo_name,
                        issue_number=high_issue.number,
                        title=high_issue.title,
                        description=(
                            f"P0/P1 issue #{high_issue.number} stalled while "
                            f"lower-priority work ({low_titles}) has recent activity"
                        ),
                    )
                )

    return hits


def _issue_priority(issue: IssueInfo) -> int | None:
    """Return numeric priority (0=highest) or None if no priority label."""
    for label in issue.labels:
        if label in _PRIORITY_ORDER:
            return _PRIORITY_ORDER[label]
    return None


# ============================================================================
# Detector: decision-without-followthrough
# ============================================================================


_ISSUE_REF_RE = re.compile(r"#(\d+)")


def detect_decision_without_followthrough(
    baseline: Baseline,
    delta: Delta,
    decisions: list[DecisionInfo],
) -> list[DetectorHit]:
    """Flag decision-referenced open issues that have had NO movement (#1057).

    For each open issue referenced by ≥1 decision, take the LATEST referencing
    decision's ``created_at`` as ``t_dec``. The issue is flagged iff BOTH:

    - **No movement since the decision** — ``issue.updated_at <= t_dec``,
      computed via ``_parse_iso_age`` so both timestamps are tz-normalized
      (naive→UTC) before comparison. If the issue was touched after the
      decision, that is real follow-through and it is not flagged. GitHub's
      ``updated_at`` is bumped by comments, labels, milestone/assignee/state
      changes and commit ``referenced`` events, but NOT by passive
      "cross-referenced" timeline events — so it is a sound movement signal.
    - **Age-gate** — ``now - t_dec > DECISION_FOLLOWTHROUGH_STALE_DAYS`` (14d).
      A fresh decision has not had time to stall.

    Matching is **project-scoped**: ``#NNN`` is matched only against the open
    issues of ``dec.project``. A decision with a falsy ``project`` falls back
    to any-repo matching (legacy decisions predating project provenance).

    At most one hit per ``(repo, issue_number)``; the description lists every
    referencing decision ID. Blank/malformed ``updated_at`` parses to age 0
    (treated as freshly-touched) and therefore fails silent rather than
    false-flagging — the false-negative-over-false-positive posture.

    NOTE (accepted limitation): a housekeeping-only touch (label churn with no
    real work) resets the movement clock and masks a genuine stall. The
    2026-07-01 milestone-backfill root cause is fixed, so residual risk is
    reduced; a backlog-issue-with-no-decision stall is tracked separately in
    #1059 and out of scope here.
    """
    hits: list[DetectorHit] = []
    now = datetime.now(timezone.utc)

    # AC3: per-repo open-issue index carries the full IssueInfo (for updated_at),
    # not a bare set of numbers — the movement check depends on it.
    open_index: dict[str, dict[int, IssueInfo]] = {}
    for name, state in _merge_repos(baseline, delta).items():
        idx = open_index.setdefault(name, {})
        for issue in state.open_issues:
            idx[issue.number] = issue

    # Collect referencing decisions per (repo, issue), tracking the latest one.
    refs: dict[tuple[str, int], dict] = {}
    for dec in decisions:
        ref_nums = {int(m) for m in _ISSUE_REF_RE.findall(dec.decision)}
        if not ref_nums:
            continue
        # AC5: project-scoped matching; falsy project → any-repo fallback.
        if dec.project:
            target_repos = [dec.project] if dec.project in open_index else []
        else:
            target_repos = list(open_index.keys())

        for repo_name in target_repos:
            idx = open_index[repo_name]
            for ref_num in ref_nums:
                issue = idx.get(ref_num)
                if issue is None:
                    continue
                key = (repo_name, ref_num)
                entry = refs.get(key)
                if entry is None:
                    entry = {"issue": issue, "dec_ids": [], "latest": None}
                    refs[key] = entry
                if dec.decision_id not in entry["dec_ids"]:
                    entry["dec_ids"].append(dec.decision_id)
                # Latest = smallest age (most recent created_at).
                if entry["latest"] is None or _parse_iso_age(dec.created_at, now) < _parse_iso_age(
                    entry["latest"], now
                ):
                    entry["latest"] = dec.created_at

    for (repo_name, ref_num), entry in refs.items():
        issue: IssueInfo = entry["issue"]
        t_dec_age = _parse_iso_age(entry["latest"], now)
        # AC2: age-gate — decision must be older than the stale window.
        if t_dec_age <= DECISION_FOLLOWTHROUGH_STALE_DAYS:
            continue
        # AC1: movement check — flag only if the issue was NOT touched after
        # the decision (issue is at least as old as the decision).
        issue_age = _parse_iso_age(issue.updated_at, now)
        if issue_age < t_dec_age:
            continue
        dec_list = ", ".join(entry["dec_ids"])
        hits.append(
            DetectorHit(
                detector="decision-without-followthrough",
                severity="major",
                repo=repo_name,
                issue_number=ref_num,
                title=issue.title or f"Decision references #{ref_num}",
                description=(
                    f"#{ref_num} referenced by decision(s) {dec_list} but had "
                    f"no movement for {issue_age:.1f}d "
                    f"(latest decision {t_dec_age:.1f}d ago, threshold "
                    f"{DECISION_FOLLOWTHROUGH_STALE_DAYS}d)"
                ),
            )
        )

    return hits


# ============================================================================
# Detector: stale-backlog (#1059)
# ============================================================================


# Vocabulary — case-normalized ProjectV2 Status strings the detector reacts to.
# Backlog is time-only; Ready additionally requires no referencing decision.
# Every other status (In Progress / In review / Done) and None (not on board)
# is silent — the false-negative-over-false-positive posture.
_READY_LABEL = "status:ready"
_IN_PROGRESS_LABEL = "status:in-progress"
# Labels that pull an issue OUT of the "backlog" bucket. status:in-progress is
# owned by detect_stale_in_progress; status:ready has its own
# (decision-suppressed) branch below.
_BACKLOG_EXCLUDING_LABELS = frozenset({_IN_PROGRESS_LABEL, _READY_LABEL})


def _decision_refs_by_repo(decisions: list[DecisionInfo], repos: list[str]) -> dict[str, set[int]]:
    """Map repo → set of issue numbers referenced by any project decision.

    Matching is robust to the decision's ``project`` being either the full
    ``owner/repo`` or the short repo slug (``jarvis`` for ``your-username/jarvis``).
    A decision with a falsy project is skipped (cannot be scoped to a repo).
    """
    refs: dict[str, set[int]] = {r: set() for r in repos}
    for dec in decisions:
        if not dec.project:
            continue
        ref_nums = {int(m) for m in _ISSUE_REF_RE.findall(dec.decision)}
        if not ref_nums:
            continue
        for repo in repos:
            if repo == dec.project or repo.endswith(f"/{dec.project}"):
                refs[repo].update(ref_nums)
    return refs


def detect_stale_backlog(
    baseline: Baseline,
    delta: Delta,
    decisions: list[DecisionInfo],
) -> list[DetectorHit]:
    """Flag issues idle ≥STALE_BACKLOG_DAYS in a Backlog/Ready state.

    Label-driven (grill #1065, decision ``0a02d3ee``): ``status:*`` labels are
    the single source of truth; the ProjectV2 board is a read-only downstream
    projection and is never read here.

    - **Backlog** = open ∧ carries none of ``status:{in-progress, ready}``
      + idle ≥30d → candidate (time-only). status:in-progress is owned by
      detect_stale_in_progress.
    - **Ready** = carries ``status:ready`` + idle ≥30d + no project-scoped
      decision referencing ``#<n>`` → candidate. A decision means the issue is
      intentionally staged, so it is suppressed.

    Aggregation (AC3): one ``info``-severity hit PER REPO listing all candidate
    issue numbers oldest-first (largest ``updated_at`` age first). Zero
    candidates in a repo ⇒ no hit for that repo. ``info`` severity does not flip
    health and is excluded from ranking (see compute_health_verdict /
    rank_detector_hits).
    """
    now = datetime.now(timezone.utc)
    merged = _merge_repos(baseline, delta)
    decision_refs = _decision_refs_by_repo(decisions, list(merged.keys()))

    hits: list[DetectorHit] = []
    for repo_name, state in merged.items():
        referenced = decision_refs.get(repo_name, set())
        # (age, number) candidates so we can order oldest-first deterministically.
        candidates: list[tuple[float, int]] = []
        for issue in state.open_issues:
            labels = set(issue.labels)
            is_ready = _READY_LABEL in labels
            is_backlog = not (labels & _BACKLOG_EXCLUDING_LABELS)
            if not (is_ready or is_backlog):
                continue
            age = _parse_iso_age(issue.updated_at, now)
            if age < STALE_BACKLOG_DAYS:
                continue
            if is_ready and issue.number in referenced:
                continue
            candidates.append((age, issue.number))

        if not candidates:
            continue

        candidates.sort(key=lambda c: c[0], reverse=True)  # oldest (largest age) first
        numbers = [num for _, num in candidates]
        refs = ", ".join(f"#{n}" for n in numbers)
        hits.append(
            DetectorHit(
                detector="stale-backlog",
                severity="info",
                repo=repo_name,
                issue_number=None,
                title=f"{len(numbers)} issue(s) idle ≥{STALE_BACKLOG_DAYS}d in Backlog/Ready",
                description=(
                    f"{len(numbers)} issue(s) sitting in Backlog/Ready for "
                    f"≥{STALE_BACKLOG_DAYS}d with no movement (oldest first): {refs}"
                ),
            )
        )

    return hits


# ============================================================================
# Detector: blocker-cascade
# ============================================================================


def detect_blocker_cascade(
    baseline: Baseline,
    delta: Delta,
    decisions: list[DecisionInfo],
) -> list[DetectorHit]:
    """Detect blocking chains; surface the root blocker.

    A root blocker is an issue that blocks others but is not itself
    blocked by any other issue.
    """
    hits: list[DetectorHit] = []

    for repo_name, repo_state in _merge_repos(baseline, delta).items():
        # Build block graph
        blocked_by: dict[int, int] = {}
        blockers: dict[int, list[int]] = {}

        for issue in repo_state.open_issues:
            for blocked_num in issue.blocks:
                blockers.setdefault(issue.number, []).append(blocked_num)
                blocked_by[blocked_num] = issue.number

        # Find root blockers: issues that block others but aren't blocked
        for issue in repo_state.open_issues:
            blocked_list = blockers.get(issue.number, [])
            if blocked_list and issue.number not in blocked_by:
                hits.append(
                    DetectorHit(
                        detector="blocker-cascade",
                        severity="critical",
                        repo=repo_name,
                        issue_number=issue.number,
                        title=issue.title,
                        description=(
                            f"Issue #{issue.number} is a root blocker "
                            f"blocking {len(blocked_list)} other issue(s): "
                            f"{', '.join(f'#{n}' for n in blocked_list)}"
                        ),
                    )
                )

    return hits


# ============================================================================
# Contradiction-detector prefilter
# ============================================================================


def build_contradiction_prefilter(
    decisions: list[DecisionInfo],
    baseline: Baseline,
) -> list[tuple[DecisionInfo, int]]:
    """Build ≤14-day decision↔issue shortlist for LLM contradiction detector.

    Filters decisions to those within DECISION_PREFILTER_DAYS,
    extracts #NNN references, returns (decision, issue_number) pairs.
    """
    now = datetime.now(timezone.utc)
    result: list[tuple[DecisionInfo, int]] = []

    for dec in decisions:
        if dec.created_at:
            age_days = _parse_iso_age(dec.created_at, now)
            if age_days > DECISION_PREFILTER_DAYS:
                continue

        refs = _ISSUE_REF_RE.findall(dec.decision)
        seen: set[int] = set()
        for ref_str in refs:
            ref_num = int(ref_str)
            if ref_num not in seen:
                seen.add(ref_num)
                result.append((dec, ref_num))

    return result


# ============================================================================
# Contradiction-detector fold (L1-only — see analyze() omission)
# ============================================================================


def fold_contradiction_verdicts(
    verdicts: Sequence[ContradictionVerdict],
) -> list[DetectorHit]:
    """Fold LLM contradiction verdicts into DetectorHits (#1016, AC1/AC6).

    Only verdicts with ``verdict == "contradiction"`` surface. Both
    ``"uncertain"`` and ``"no_contradiction"`` (and any unrecognized value)
    are dropped — the false-negative-over-false-positive posture (research
    b72ea66c): at solo-dev volume a base-rate flood of weak positives causes
    habituation, so an undecided candidate is dropped, not surfaced.

    The per-candidate rationale is carried into the hit's ``description`` so
    the provenance of every surfaced contradiction is visible to the reader
    (the actionability/provenance mitigation from the same research).
    """
    hits: list[DetectorHit] = []
    for v in verdicts:
        if v.verdict != "contradiction":
            continue
        hits.append(
            DetectorHit(
                detector=MEMORY_GIT_CONTRADICTION,
                severity="major",
                repo=v.repo,
                issue_number=v.issue_number,
                title=f"Decision {v.decision_id} contradicts issue #{v.issue_number}",
                description=v.rationale,
            )
        )
    return hits


def serialize_contradiction_cache(
    verdicts: Sequence[ContradictionVerdict],
    generated_at: str = "",
) -> dict:
    """Serialize verdicts to a JSON-able cache dict (#1016, AC4).

    The L1 morning pass writes this under the ``status-snapshot`` memory tag so
    L2/L3 can re-fold the same contradictions without re-running the LLM. The
    full verdict set is stored (not just the surfaced contradictions) so the
    drop decision stays auditable from the cache alone.
    """
    return {
        "schema": CONTRADICTION_CACHE_SCHEMA,
        "generated_at": generated_at,
        "verdicts": [
            {
                "decision_id": v.decision_id,
                "issue_number": v.issue_number,
                "repo": v.repo,
                "verdict": v.verdict,
                "rationale": v.rationale,
            }
            for v in verdicts
        ],
    }


def deserialize_contradiction_cache(
    data: dict,
) -> list[ContradictionVerdict]:
    """Rebuild verdicts from a cache dict (#1016, AC4).

    Tolerant of a malformed/empty cache: a missing ``verdicts`` key yields an
    empty list rather than raising, so a corrupt snapshot degrades to "no
    cached contradictions" instead of breaking the render path. Individual
    rows missing mandatory keys degrade per-field (not KeyError) for the same
    reason (C2).

    A cache stamped with an unrecognized ``schema`` version deserializes to
    empty — a future v2 layout must not be silently misread as v1 (M2). A
    cache with no ``schema`` key at all is accepted (legacy / hand-written).
    """
    schema = data.get("schema")
    if schema is not None and schema != CONTRADICTION_CACHE_SCHEMA:
        return []
    rows = data.get("verdicts") or []
    return [
        ContradictionVerdict(
            decision_id=row.get("decision_id", ""),
            # YAML may emit issue_number as a float (42.0); coerce to int so it
            # renders as #42, not #42.0.
            issue_number=int(row.get("issue_number", 0) or 0),
            repo=row.get("repo", ""),
            verdict=row.get("verdict", "uncertain"),
            rationale=row.get("rationale", ""),
        )
        for row in rows
    ]


# ============================================================================
# Ranking
# ============================================================================


_SEVERITY_SORT = {"critical": 0, "major": 1, "minor": 2}


def rank_detector_hits(hits: list[DetectorHit]) -> list[RankedItem]:
    """Rank detector hits by severity, return at most TOP_N_CAP items.

    Critical first, then major, then minor. Stable sort within severity.
    ``info`` hits (e.g. stale-backlog, #1059) are advisory — excluded from
    "Куда смотреть" entirely.
    """
    rankable = [h for h in hits if h.severity != "info"]
    sorted_hits = sorted(
        rankable,
        key=lambda h: _SEVERITY_SORT.get(h.severity, 99),
    )

    ranked: list[RankedItem] = []
    for i, hit in enumerate(sorted_hits[:TOP_N_CAP]):
        ranked.append(
            RankedItem(
                rank=i + 1,
                detector_hit=hit,
                reason=f"[{hit.severity.upper()}] {hit.detector}"
                f" — {hit.repo}" + (f" — #{hit.issue_number}" if hit.issue_number else ""),
            )
        )
    return ranked


# ============================================================================
# Health verdict
# ============================================================================


def compute_health_verdict(
    baseline: Baseline,
    hits: list[DetectorHit],
) -> HealthVerdict:
    """Compute overall health verdict.

    Health is GREEN (ok=True) ONLY if:
    - Every source has ran=True, ok=True, age <= FRESHNESS_AGE_SECONDS
    - No detector hits exist
    """
    stale_or_failed: list[str] = []
    for source_name, prov in baseline.provenance.items():
        if not prov.ran:
            stale_or_failed.append(f"{source_name}: did not run")
        elif not prov.ok:
            stale_or_failed.append(f"{source_name}: failed (ok=False)")
        elif prov.age is not None and prov.age > FRESHNESS_AGE_SECONDS:
            # age is None ⇒ data age unknown (e.g. a status snapshot written
            # without a parseable generated_at). Treat unknown age as not-stale
            # rather than crashing the comparison (C1).
            stale_or_failed.append(
                f"{source_name}: stale ({prov.age:.0f}s > {FRESHNESS_AGE_SECONDS}s)"
            )

    if stale_or_failed:
        return HealthVerdict(
            ok=False,
            reason="Unhealthy: " + "; ".join(stale_or_failed),
        )

    # ``info`` hits (stale-backlog, #1059) are advisory — they surface only in
    # --deep and must NOT flip health red. Gate on the blocking subset.
    blocking = [h for h in hits if h.severity != "info"]
    if blocking:
        critical_count = sum(1 for h in blocking if h.severity == "critical")
        major_count = sum(1 for h in blocking if h.severity == "major")
        return HealthVerdict(
            ok=False,
            reason=(
                f"Unhealthy: {len(blocking)} detector hit(s)"
                f" ({critical_count} critical, {major_count} major)"
            ),
        )

    return HealthVerdict(ok=True, reason="All sources fresh, no anomalies detected")


# ============================================================================
# Main entry point
# ============================================================================


def analyze(
    baseline: Baseline,
    delta: Delta,
    decisions: list[DecisionInfo],
    contradiction_verdicts: Sequence[ContradictionVerdict] = (),
) -> Digest:
    """Synthesize status digest from baseline, delta, and decisions.

    Pure function — zero I/O, fully deterministic given the same inputs.
    This is the sole public interface of status_engine.

    ``contradiction_verdicts`` carries the L1 memory↔git verdicts (already
    judged upstream by the status-record cron, then read from the cached
    status-snapshot — see deserialize_contradiction_cache). analyze() only
    *folds* them; it never runs the LLM judgment itself. The default empty
    tuple is the intraday/L2 path: no verdicts → no contradiction hit → no
    LLM cost, satisfying the L1-only contract (#1016 AC2). Folding the cached
    verdicts here (rather than after analyze) lets contradiction hits take
    part in rank_detector_hits + compute_health_verdict, so they appear in
    "Куда смотреть" and flip health red like any other detector hit (AC4).
    """
    hits: list[DetectorHit] = []
    hits.extend(detect_stale_in_progress(baseline, delta, decisions))
    hits.extend(detect_priority_inversion(baseline, delta, decisions))
    hits.extend(detect_decision_without_followthrough(baseline, delta, decisions))
    hits.extend(detect_stale_backlog(baseline, delta, decisions))
    hits.extend(detect_blocker_cascade(baseline, delta, decisions))
    hits.extend(fold_contradiction_verdicts(contradiction_verdicts))

    ranking = rank_detector_hits(hits)
    health = compute_health_verdict(baseline, hits)

    provenance: dict[str, Provenance] = dict(baseline.provenance)
    for repo_name, repo_state in delta.repos.items():
        if repo_state.provenance:
            provenance[f"delta:{repo_name}"] = repo_state.provenance

    return Digest(
        health=health,
        detector_hits=hits,
        ranking=ranking,
        provenance=provenance,
    )
