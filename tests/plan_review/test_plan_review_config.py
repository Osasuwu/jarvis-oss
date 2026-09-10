"""Tests for agents.plan_review_config — the single classification artifact
loader for issue #1685 (class-2 thresholds + class-3 criteria, one config).
"""

from __future__ import annotations

import fnmatch
from pathlib import Path

import pytest
import yaml

from agents.plan_review_config import load_plan_review_config

_REPO_CONFIG = Path(__file__).resolve().parents[2] / "config" / "plan_review.yaml"


def test_loads_repo_config() -> None:
    """The committed config/plan_review.yaml loads without error."""
    cfg = load_plan_review_config(_REPO_CONFIG)
    assert cfg.class_2.churn_threshold > 0
    assert cfg.class_2.min_prod_areas > 0
    assert cfg.class_2.shared_surface_globs
    assert cfg.exempt.mechanical_criteria
    assert cfg.class_3.mechanical_criteria
    assert cfg.models.planner
    assert cfg.models.critic


def test_repo_config_schema_v2_exempt_preserves_four_mechanical_criteria() -> None:
    """#1707: class_3 (the exemption list) renamed to exempt: — its four
    existing criteria must survive the rename untouched."""
    cfg = load_plan_review_config(_REPO_CONFIG)
    assert set(cfg.exempt.mechanical_criteria) == {
        "docs-only",
        "typo-fix",
        "dependency-bump",
        "lint-fix",
    }


def test_repo_config_schema_v2_class_3_is_real_hitl_criteria() -> None:
    """#1707 AC / #1573 AC2: the freed class_3: key now carries the five
    true-HITL criteria, not the old mechanical exemption list."""
    cfg = load_plan_review_config(_REPO_CONFIG)
    assert set(cfg.class_3.mechanical_criteria) == {
        "admin-rights-required",
        "secret-or-credential-provisioning",
        "live-third-party-state-verification",
        "physical-device-bound",
        "whitelist-barred-shared-surface-ddl",
    }


def test_repo_config_covers_supabase_as_a_shared_surface() -> None:
    """docs/reference/mcp-and-environment.md: 'mcp-memory/server.py, .mcp.json,
    and the Supabase schema are shared surfaces — consumers sit outside this
    repo ... breakage is invisible from inside it.' (moved here from
    docs/context/invariants.md by #1791's AGENTS.md rebuild — the invariant
    itself didn't survive the cut to exactly two invariants, but the fact it
    documents still governs this test's assertion.) A Supabase migration path
    must trip a shared_surface_globs match,
    same as mcp-memory/** (#1685 review finding: the glob list omitted
    supabase/** entirely, so a real supabase/migrations/*.sql change fell
    through class-2 classification unnoticed)."""
    cfg = load_plan_review_config(_REPO_CONFIG)
    sample_path = "supabase/migrations/20260415082814_create_credential_registry.sql"
    assert any(fnmatch.fnmatch(sample_path, glob) for glob in cfg.class_2.shared_surface_globs)


def test_thresholds_are_read_from_config_not_hardcoded(tmp_path: Path) -> None:
    """Changing a threshold in config changes the loaded verdict — no code edit.

    This is AC2's assertion: the loader must reflect whatever value is on
    disk, proving thresholds are not hardcoded on the loading path.
    """
    custom = tmp_path / "plan_review.yaml"
    custom.write_text(
        yaml.safe_dump(
            {
                "schema_version": "v2",
                "class_2": {
                    "shared_surface_globs": ["only/this/**"],
                    "churn_threshold": 12345,
                    "min_prod_areas": 7,
                },
                "exempt": {"mechanical_criteria": ["only-exempt-criterion"]},
                "class_3": {"mechanical_criteria": ["only-hitl-criterion"]},
                "models": {"planner": "custom-planner-model", "critic": "custom-critic-model"},
            }
        ),
        encoding="utf-8",
    )

    cfg = load_plan_review_config(custom)

    assert cfg.class_2.churn_threshold == 12345
    assert cfg.class_2.min_prod_areas == 7
    assert cfg.class_2.shared_surface_globs == ("only/this/**",)
    assert cfg.exempt.mechanical_criteria == ("only-exempt-criterion",)
    assert cfg.class_3.mechanical_criteria == ("only-hitl-criterion",)
    assert cfg.models.planner == "custom-planner-model"
    assert cfg.models.critic == "custom-critic-model"


def test_missing_models_key_raises(tmp_path: Path) -> None:
    """Issue #1686 AC9: planner/critic model floors must come from config —
    a config file without them is invalid, not silently defaulted."""
    bad = tmp_path / "plan_review.yaml"
    bad.write_text(
        yaml.safe_dump(
            {
                "schema_version": "v2",
                "class_2": {
                    "shared_surface_globs": ["x/**"],
                    "churn_threshold": 1,
                    "min_prod_areas": 1,
                },
                "exempt": {"mechanical_criteria": ["x"]},
                "class_3": {"mechanical_criteria": ["y"]},
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(ValueError):
        load_plan_review_config(bad)


def test_missing_file_raises() -> None:
    with pytest.raises(FileNotFoundError):
        load_plan_review_config(Path("does/not/exist.yaml"))


def test_missing_required_key_raises(tmp_path: Path) -> None:
    bad = tmp_path / "plan_review.yaml"
    bad.write_text(yaml.safe_dump({"schema_version": "v2", "class_2": {}}), encoding="utf-8")
    with pytest.raises(ValueError):
        load_plan_review_config(bad)


def test_stale_v1_schema_version_raises_with_rename_guidance(tmp_path: Path) -> None:
    """#1707 AC5: a v1 document (pre-rename class_3 = the exemption list)
    must be rejected with an error that names the class_3-to-exempt rename,
    not silently misread as the new schema."""
    stale = tmp_path / "plan_review.yaml"
    stale.write_text(
        yaml.safe_dump(
            {
                "schema_version": "v1",
                "class_2": {
                    "shared_surface_globs": ["x/**"],
                    "churn_threshold": 1,
                    "min_prod_areas": 1,
                },
                "class_3": {"mechanical_criteria": ["docs-only"]},
                "models": {"planner": "p", "critic": "c"},
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="exempt"):
        load_plan_review_config(stale)


def test_missing_schema_version_raises_with_rename_guidance(tmp_path: Path) -> None:
    """No schema_version at all is treated the same as stale v1 — the
    pre-#1685 shape never carried the key, so absence must not silently
    parse as v2."""
    bad = tmp_path / "plan_review.yaml"
    bad.write_text(
        yaml.safe_dump(
            {
                "class_2": {
                    "shared_surface_globs": ["x/**"],
                    "churn_threshold": 1,
                    "min_prod_areas": 1,
                },
                "class_3": {"mechanical_criteria": ["docs-only"]},
                "models": {"planner": "p", "critic": "c"},
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="exempt"):
        load_plan_review_config(bad)


def test_missing_exempt_key_raises(tmp_path: Path) -> None:
    """v2 requires the renamed exempt: section explicitly — a v2-labelled
    doc missing it is still invalid, not defaulted to empty."""
    bad = tmp_path / "plan_review.yaml"
    bad.write_text(
        yaml.safe_dump(
            {
                "schema_version": "v2",
                "class_2": {
                    "shared_surface_globs": ["x/**"],
                    "churn_threshold": 1,
                    "min_prod_areas": 1,
                },
                "class_3": {"mechanical_criteria": ["admin-rights-required"]},
                "models": {"planner": "p", "critic": "c"},
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(ValueError):
        load_plan_review_config(bad)
