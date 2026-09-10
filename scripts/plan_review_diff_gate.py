"""PR-open CI diff-gate: re-classify the actual PR diff, fail closed (#1687).

The ex-post half of two-point plan-review classification (decision
`d34dd65a`): admission-time classification is best-effort, so this check
recomputes the ordinal-2 trigger from the real diff on PR open/push. A
trigger hit with no valid locked plan on the linked issue blocks — the PR
is converted to draft and labeled `status:owner-queue` (AC2) — rather than
merging having never seen a plan.

Reuses the shared classifier (`agents.plan_classifier.classify`) and the
shared lock-verification helper (`agents.plan_lock.verify_lock`) — no
second implementation of thresholds or hashing logic (AC5, decision
`fa9c5ab0`). This module does no network/subprocess I/O itself: it reads a
JSON envelope on stdin (paths, churn, the linked issue body if reachable)
and prints a decision, mirroring `delegate_predispatch_gate.py`'s
data-in/decision-out shape. The calling workflow does the `git diff` /
`gh` calls and acts on the exit code (0 = pass, 1 = block).

Fails closed (AC4): an indeterminate classification, an unreachable linked
issue, or an unverifiable/malformed lock all block rather than pass.
"""

from __future__ import annotations

import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path

from agents.plan_classifier import ChangeSet, classify, label_for, prod_areas_from_paths
from agents.plan_lock import MalformedPlanError, verify_lock
from agents.plan_review_config import PlanReviewConfig, load_plan_review_config

_DEFAULT_CONFIG_PATH = Path("config/plan_review.yaml")

# Mirrors the require-linked-issue bypasses in .github/workflows/pr-body-check.yml
# (#1710): a PR that doesn't need a linked issue at all has nowhere to hold a
# plan lock, so blocking it for "linked issue is unreachable" is a false
# failure, not fail-closed caution. Two separate JS/Python implementations
# (per-gate convention already in this repo — see AC5 comment above for why
# the *threshold/hash* logic is shared instead) rather than a cross-language
# shared module.
_BOT_AUTHORS = frozenset({"dependabot[bot]", "renovate[bot]"})
_NO_ISSUE_MARKER = re.compile(r"\[no-issue\]", re.IGNORECASE)
_REFACTOR_TITLE_PREFIX = re.compile(r"^refactor(\([^)]*\))?:", re.IGNORECASE)


def has_escape_hatch(*, author: str, title: str, body: str, labels: tuple[str, ...]) -> str | None:
    """Return the bypass reason if this PR doesn't need a linked issue, else None."""
    if author in _BOT_AUTHORS:
        return f"bot author ({author})"
    if "priority:critical" in labels:
        return "priority:critical label (hotfix)"
    if _NO_ISSUE_MARKER.search(body or "") or _NO_ISSUE_MARKER.search(title or ""):
        return "[no-issue] marker (fix-inline per #428)"
    if _REFACTOR_TITLE_PREFIX.match(title or ""):
        return "refactor: title prefix"
    return None


# prod_areas_from_paths re-exported from agents.plan_classifier (moved there
# in #1688 so the interactive lane can share it too) — kept importable from
# this module's own name for tests/callers already using
# `plan_review_diff_gate.prod_areas_from_paths`.
__all__ = ["prod_areas_from_paths"]


def compute_change_set(
    paths: tuple[str, ...],
    churn_lines: int,
    mechanical_criteria: tuple[str, ...] = (),
) -> ChangeSet:
    """Build a `ChangeSet` from raw diff facts — the gate's sole input shape."""
    return ChangeSet(
        paths=tuple(paths),
        churn_lines=churn_lines,
        prod_areas=prod_areas_from_paths(tuple(paths)),
        mechanical_criteria=tuple(mechanical_criteria),
    )


@dataclass(frozen=True)
class GateDecision:
    classification: int
    decision: str  # "pass" | "block"
    reason: str


def _class_2_trigger_reason(config: PlanReviewConfig, change: ChangeSet) -> str:
    """Describe which ordinal-2 condition(s) hold — for the PR comment (AC7).

    Informational readout only: reads the already-loaded config's own
    values back against the change-set to name what tripped. The
    pass/block *decision* always comes from `classify()`'s return value,
    never from this description re-deriving it.
    """
    reasons = []
    hit_globs = [g for g in config.class_2.shared_surface_globs if _matches_any(change.paths, g)]
    if hit_globs:
        reasons.append(f"touches shared-surface path(s) matching {hit_globs}")
    if change.churn_lines > config.class_2.churn_threshold:
        reasons.append(
            f"churn {change.churn_lines} lines exceeds threshold {config.class_2.churn_threshold}"
        )
    if change.prod_areas >= config.class_2.min_prod_areas:
        reasons.append(
            f"touches {change.prod_areas} production areas (>= min {config.class_2.min_prod_areas})"
        )
    return "; ".join(reasons) or "ordinal-2 trigger fired"


def _matches_any(paths: tuple[str, ...], glob: str) -> bool:
    import fnmatch

    return any(fnmatch.fnmatch(p, glob) for p in paths)


def evaluate(
    config: PlanReviewConfig,
    change: ChangeSet,
    issue_body: str | None,
    escape_hatch_reason: str | None = None,
) -> GateDecision:
    """Classify `change` and decide pass/block against the linked issue's plan lock.

    Ordinal 1 / 3 always pass silently (AC3, scoped to ordinal 2). For
    ordinal 2, an unreachable issue, a malformed plan, or a lock that does
    not verify all block (AC4, fail closed); only a verified lock passes —
    unless `escape_hatch_reason` is set (#1710), in which case this PR was
    never required to carry a linked issue in the first place (mirrors
    require-linked-issue's own bypasses), so there is no issue to hold a
    plan lock against and the gate passes.
    """
    classification = classify(config, change)

    if classification != 2:
        return GateDecision(classification=classification, decision="pass", reason="")

    trigger_reason = _class_2_trigger_reason(config, change)

    if escape_hatch_reason is not None:
        return GateDecision(classification=classification, decision="pass", reason="")

    label = label_for(classification)
    if issue_body is None:
        return GateDecision(
            classification=classification,
            decision="block",
            reason=f"{label} trigger ({trigger_reason}) but linked issue is unreachable",
        )

    try:
        locked = verify_lock(issue_body)
    except MalformedPlanError as exc:
        return GateDecision(
            classification=classification,
            decision="block",
            reason=f"{label} trigger ({trigger_reason}) but linked issue has no valid plan lock "
            f"({exc.reason})",
        )

    if not locked:
        return GateDecision(
            classification=classification,
            decision="block",
            reason=f"{label} trigger ({trigger_reason}) but linked issue's plan lock does not verify",
        )

    return GateDecision(classification=classification, decision="pass", reason="")


def _validate_envelope(payload: object) -> dict | str:
    if not isinstance(payload, dict):
        return "payload is not a JSON object"
    if not isinstance(payload.get("paths"), list):
        return "missing or malformed `paths` (must be a list of strings)"
    if not isinstance(payload.get("churn_lines"), int):
        return "missing or malformed `churn_lines` (must be an int)"
    issue_body = payload.get("issue_body")
    if issue_body is not None and not isinstance(issue_body, str):
        return "malformed `issue_body` (must be a string or null)"
    pr_labels = payload.get("pr_labels")
    if pr_labels is not None and not isinstance(pr_labels, list):
        return "malformed `pr_labels` (must be a list of strings or null)"
    return payload


def main(argv: list[str] | None = None) -> int:
    raw = sys.stdin.read()
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        print(f"BLOCK: unverifiable — stdin is not valid JSON ({exc.msg})")
        return 1

    validated = _validate_envelope(payload)
    if isinstance(validated, str):
        print(f"BLOCK: unverifiable — {validated}")
        return 1

    config_path = Path(validated.get("config_path") or _DEFAULT_CONFIG_PATH)
    try:
        config = load_plan_review_config(config_path)
    except (FileNotFoundError, ValueError) as exc:
        print(f"BLOCK: unverifiable — could not load plan-review config: {exc}")
        return 1

    change = compute_change_set(
        paths=tuple(validated["paths"]),
        churn_lines=validated["churn_lines"],
        mechanical_criteria=tuple(validated.get("mechanical_criteria") or ()),
    )
    escape_hatch_reason = has_escape_hatch(
        author=validated.get("pr_author") or "",
        title=validated.get("pr_title") or "",
        body=validated.get("pr_body") or "",
        labels=tuple(validated.get("pr_labels") or ()),
    )
    result = evaluate(
        config, change, validated.get("issue_body"), escape_hatch_reason=escape_hatch_reason
    )

    if result.decision == "block":
        print(f"BLOCK: {result.reason}")
        return 1

    print(f"PASS: classification={result.classification}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
