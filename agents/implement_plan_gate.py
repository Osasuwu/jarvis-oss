"""Interactive `/implement` lane's plan-gate trigger evaluation (#1688).

Ex-ante half of two-point plan-review classification (decision `d34dd65a`):
runs off whatever files-to-touch and estimated churn are known before any
code edit exists, per `.github/workflows/plan-review-diff-gate.yml`'s own
framing — "admission-time classification (#1686) is best-effort ... runs
off whatever paths/churn were known when the PR/issue was opened." The CI
diff-gate (#1687) re-classifies from the real diff and is the fail-closed
backstop, so this evaluation does not need to be exact.

Reuses `agents.plan_classifier.classify_task_row` — the one named policy
entry point every consumer (interactive lane, drain, container pick, CI
diff-gate) calls per its own docstring — and `prod_areas_from_paths` for
deriving `prod_areas` from an estimated file list. No second
implementation of thresholds or classification logic here (AC1).
"""

from __future__ import annotations

from dataclasses import dataclass

from agents.plan_classifier import classify_task_row, prod_areas_from_paths
from agents.plan_review_config import PlanReviewConfig

PRIORITY_CRITICAL_LABEL = "priority:critical"


@dataclass(frozen=True)
class TriggerResult:
    classification: int
    requires_plan: bool
    carve_out: bool


def build_task_row(
    paths: tuple[str, ...],
    churn_lines: int = 0,
    mechanical_criteria: tuple[str, ...] = (),
) -> dict:
    """Build a task_queue-shaped row from an estimated file list — the
    input shape `classify_task_row` already expects (AC1)."""
    return {
        "scope_files": tuple(paths),
        "churn_lines": churn_lines,
        "prod_areas": prod_areas_from_paths(tuple(paths)),
        "mechanical_criteria": tuple(mechanical_criteria),
    }


def evaluate_trigger(
    config: PlanReviewConfig,
    paths: tuple[str, ...],
    labels: tuple[str, ...] = (),
    churn_lines: int = 0,
    mechanical_criteria: tuple[str, ...] = (),
) -> TriggerResult:
    """Classify the estimated change and decide whether the plan stage runs.

    The `priority:critical` carve-out (AC3) skips only `requires_plan` — it
    never changes `classification` itself, so a class-2 hotfix is still
    reported as ordinal 2 (visible for audit), just not gated on a plan.
    Ordinal 3 (true-HITL, #1707) never requires the class-2 plan gate
    either — it routes to a human, not the planner.
    """
    row = build_task_row(paths, churn_lines, mechanical_criteria)
    classification = classify_task_row(config, row)
    carve_out = PRIORITY_CRITICAL_LABEL in tuple(labels)
    requires_plan = classification == 2 and not carve_out
    return TriggerResult(
        classification=classification, requires_plan=requires_plan, carve_out=carve_out
    )
