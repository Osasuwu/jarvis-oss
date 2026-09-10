"""Loader for the plan-review classification config (issue #1685, v2 vocabulary
per #1707).

Single classification artifact: class-2 trigger thresholds, the exempt
(mechanical, no-owner-review) criteria list, and the class-3 (true-HITL)
criteria list live in one YAML file (``config/plan_review.yaml``), loaded by
:func:`load_plan_review_config`. No threshold, glob, or criterion is
hardcoded on any code path that consumes this config — every value comes
from the file on disk.

Schema v2 (#1707) split what v1 called ``class_3`` into two distinct keys:
``exempt`` (the old class_3 — mechanical criteria needing no owner review)
and a new ``class_3`` (true HITL: the change cannot close without a human).
A v1 document is rejected with a rename-explaining error rather than
silently misread.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml

_SCHEMA_VERSION = "v2"


@dataclass(frozen=True)
class Class2Thresholds:
    shared_surface_globs: tuple[str, ...]
    churn_threshold: int
    min_prod_areas: int


@dataclass(frozen=True)
class ExemptCriteria:
    """Mechanical criteria exempt from owner review (the old ``class_3``)."""

    mechanical_criteria: tuple[str, ...]


@dataclass(frozen=True)
class Class3Criteria:
    """True-HITL criteria (#1573 AC2) — the change cannot close without a
    human: admin rights, credential provisioning, live third-party state
    verification, physical-device binding, or a whitelist-barred shared-
    surface DDL."""

    mechanical_criteria: tuple[str, ...]


@dataclass(frozen=True)
class ModelFloors:
    """Model floors for the plan-review stage (issue #1686 AC9): read from
    operator config, never hardcoded on a call path."""

    planner: str
    critic: str


@dataclass(frozen=True)
class PlanReviewConfig:
    class_2: Class2Thresholds
    exempt: ExemptCriteria
    class_3: Class3Criteria
    models: ModelFloors
    # Pick-time lock-age ceiling (#1691 AC8): a plan:locked issue whose lock
    # is older than this is refused at pick time, not blocked or re-planned
    # automatically — it falls to the same owner-attention path as any other
    # pick-time refusal. Optional key (default below) so existing config
    # files predating #1691 keep loading without an edit.
    lock_max_age_days: int = 14


_REQUIRED_CLASS_2_KEYS = ("shared_surface_globs", "churn_threshold", "min_prod_areas")
_REQUIRED_EXEMPT_KEYS = ("mechanical_criteria",)
_REQUIRED_CLASS_3_KEYS = ("mechanical_criteria",)
_REQUIRED_MODELS_KEYS = ("planner", "critic")
_DEFAULT_LOCK_MAX_AGE_DAYS = 14


def load_plan_review_config(path: Path) -> PlanReviewConfig:
    """Load and validate the plan-review classification config.

    Raises ``FileNotFoundError`` if ``path`` does not exist, ``ValueError``
    if the file is missing a required key or is on a stale schema version.
    """
    if not path.exists():
        raise FileNotFoundError(f"plan-review config not found: {path}")

    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}

    schema_version = raw.get("schema_version")
    if schema_version != _SCHEMA_VERSION:
        raise ValueError(
            f"plan-review config {path}: schema_version {schema_version!r} is stale "
            f"(expected {_SCHEMA_VERSION!r}) — #1707 renamed the old class_3: "
            "(mechanical, no-owner-review) key to exempt:, and freed class_3: for "
            "the true-HITL criteria; rename class_3: to exempt:, add a new class_3: "
            'section, and set schema_version: "v2"'
        )

    class_2_raw = raw.get("class_2") or {}
    missing_2 = [k for k in _REQUIRED_CLASS_2_KEYS if k not in class_2_raw]
    if missing_2:
        raise ValueError(f"plan-review config {path}: class_2 missing keys {missing_2}")

    exempt_raw = raw.get("exempt") or {}
    missing_exempt = [k for k in _REQUIRED_EXEMPT_KEYS if k not in exempt_raw]
    if missing_exempt:
        raise ValueError(f"plan-review config {path}: exempt missing keys {missing_exempt}")

    class_3_raw = raw.get("class_3") or {}
    missing_3 = [k for k in _REQUIRED_CLASS_3_KEYS if k not in class_3_raw]
    if missing_3:
        raise ValueError(f"plan-review config {path}: class_3 missing keys {missing_3}")

    models_raw = raw.get("models") or {}
    missing_models = [k for k in _REQUIRED_MODELS_KEYS if k not in models_raw]
    if missing_models:
        raise ValueError(f"plan-review config {path}: models missing keys {missing_models}")

    return PlanReviewConfig(
        class_2=Class2Thresholds(
            shared_surface_globs=tuple(class_2_raw["shared_surface_globs"]),
            churn_threshold=int(class_2_raw["churn_threshold"]),
            min_prod_areas=int(class_2_raw["min_prod_areas"]),
        ),
        exempt=ExemptCriteria(
            mechanical_criteria=tuple(exempt_raw["mechanical_criteria"]),
        ),
        class_3=Class3Criteria(
            mechanical_criteria=tuple(class_3_raw["mechanical_criteria"]),
        ),
        models=ModelFloors(
            planner=str(models_raw["planner"]),
            critic=str(models_raw["critic"]),
        ),
        lock_max_age_days=int(raw.get("lock_max_age_days", _DEFAULT_LOCK_MAX_AGE_DAYS)),
    )
