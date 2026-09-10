"""Tests for scripts/plan_review_diff_gate.py (#1687).

The ex-post half of two-point plan-review classification: a CI check that
recomputes the class-2 trigger from the actual PR diff and, on a trigger
hit with no valid locked plan, blocks (fail closed) rather than merges.

Reuses agents.plan_classifier.classify and agents.plan_lock.verify_lock —
no second implementation of thresholds or hashing (AC5); this file asserts
that by grep, not just by behavior.
"""

from __future__ import annotations

import importlib
import re
import sys
from pathlib import Path

from agents.plan_classifier import ChangeSet
from agents.plan_lock import hash_plan
from agents.plan_review_config import (
    Class2Thresholds,
    Class3Criteria,
    ExemptCriteria,
    ModelFloors,
    PlanReviewConfig,
)

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
gate = importlib.import_module("plan_review_diff_gate")

_CONFIG = PlanReviewConfig(
    class_2=Class2Thresholds(
        shared_surface_globs=("mcp-memory/**",),
        churn_threshold=400,
        min_prod_areas=2,
    ),
    exempt=ExemptCriteria(mechanical_criteria=("typo-fix",)),
    class_3=Class3Criteria(mechanical_criteria=("docs-only",)),
    models=ModelFloors(planner="claude-opus-5", critic="claude-sonnet-5"),
)


def _locked_body(steps: tuple[str, ...] = ("do a thing",)) -> str:
    steps_text = "\n".join(f"- {s}" for s in steps)
    lock = hash_plan(steps_text)
    return f"## Acceptance Criteria\n- [ ] x\n\n## Plan\n\n{steps_text}\n\nlock: {lock}\n"


_UNLOCKED_BODY = "## Plan\n\n- do a thing\n\nlock: not-the-real-hash\n"
_NO_PLAN_BODY = "## Acceptance Criteria\n- [ ] x\n"


# ── prod_areas_from_paths ────────────────────────────────────────────────────


def test_prod_areas_counts_distinct_top_level_dirs():
    assert gate.prod_areas_from_paths(("agents/foo.py", "scripts/bar.py")) == 2


def test_prod_areas_excludes_tests():
    assert gate.prod_areas_from_paths(("agents/foo.py", "tests/test_foo.py")) == 1


def test_prod_areas_dedupes_same_top_level_dir():
    assert gate.prod_areas_from_paths(("agents/a.py", "agents/b.py")) == 1


def test_prod_areas_top_level_file_counts_as_its_own_area():
    assert gate.prod_areas_from_paths((".mcp.json",)) == 1


# ── evaluate: classification pass-through for class:1 / class:3 ────────────


def test_evaluate_passes_silently_for_class_1():
    change = ChangeSet(paths=("agents/x.py",), churn_lines=1, prod_areas=1)
    result = gate.evaluate(_CONFIG, change, issue_body=None)
    assert result.classification == 1
    assert result.decision == "pass"


def test_evaluate_passes_silently_for_class_3_even_without_issue():
    change = ChangeSet(
        paths=("docs/x.md",), churn_lines=1, prod_areas=1, mechanical_criteria=("docs-only",)
    )
    result = gate.evaluate(_CONFIG, change, issue_body=None)
    assert result.classification == 3
    assert result.decision == "pass"


# ── evaluate: class:2 trigger + lock verification ───────────────────────────


def test_evaluate_passes_for_class_2_with_valid_lock():
    change = ChangeSet(paths=("mcp-memory/x.py",), churn_lines=1, prod_areas=1)
    result = gate.evaluate(_CONFIG, change, issue_body=_locked_body())
    assert result.classification == 2
    assert result.decision == "pass"


def test_evaluate_blocks_for_class_2_with_stale_lock():
    change = ChangeSet(paths=("mcp-memory/x.py",), churn_lines=1, prod_areas=1)
    result = gate.evaluate(_CONFIG, change, issue_body=_UNLOCKED_BODY)
    assert result.classification == 2
    assert result.decision == "block"
    assert "lock" in result.reason.lower()


def test_evaluate_blocks_for_class_2_with_no_plan_section():
    change = ChangeSet(paths=("mcp-memory/x.py",), churn_lines=1, prod_areas=1)
    result = gate.evaluate(_CONFIG, change, issue_body=_NO_PLAN_BODY)
    assert result.decision == "block"


def test_evaluate_blocks_for_class_2_with_unreachable_issue():
    change = ChangeSet(paths=("mcp-memory/x.py",), churn_lines=1, prod_areas=1)
    result = gate.evaluate(_CONFIG, change, issue_body=None)
    assert result.decision == "block"
    assert "issue" in result.reason.lower()


# ── evaluate: escape-hatch bypass (#1710) ───────────────────────────────────


def test_evaluate_passes_for_class_2_no_issue_when_escape_hatch_set():
    """The actual #1710 regression: a class:2 PR with no linked issue at all
    (uses [no-issue] instead of Closes #N) must pass, not fail-closed-block."""
    change = ChangeSet(paths=("mcp-memory/x.py",), churn_lines=1, prod_areas=1)
    result = gate.evaluate(
        _CONFIG,
        change,
        issue_body=None,
        escape_hatch_reason="[no-issue] marker (fix-inline per #428)",
    )
    assert result.classification == 2
    assert result.decision == "pass"


# ── has_escape_hatch (#1710) ─────────────────────────────────────────────────


def test_has_escape_hatch_bot_author():
    reason = gate.has_escape_hatch(author="dependabot[bot]", title="bump x", body="", labels=())
    assert reason is not None and "bot" in reason.lower()


def test_has_escape_hatch_priority_critical_label():
    reason = gate.has_escape_hatch(
        author="petrk", title="fix", body="", labels=("priority:critical",)
    )
    assert reason is not None and "priority:critical" in reason.lower()


def test_has_escape_hatch_no_issue_marker_in_body():
    reason = gate.has_escape_hatch(author="petrk", title="fix", body="stuff [no-issue]", labels=())
    assert reason is not None and "no-issue" in reason.lower()


def test_has_escape_hatch_no_issue_marker_in_title():
    reason = gate.has_escape_hatch(author="petrk", title="fix [no-issue]", body="", labels=())
    assert reason is not None and "no-issue" in reason.lower()


def test_has_escape_hatch_refactor_title_prefix():
    reason = gate.has_escape_hatch(
        author="petrk", title="refactor(scripts): tidy up", body="", labels=()
    )
    assert reason is not None and "refactor" in reason.lower()


def test_has_escape_hatch_none_when_no_bypass_applies():
    reason = gate.has_escape_hatch(
        author="petrk", title="feat: add thing", body="Closes #1", labels=()
    )
    assert reason is None


def test_evaluate_class_2_via_churn_threshold():
    change = ChangeSet(paths=("agents/x.py",), churn_lines=401, prod_areas=1)
    result = gate.evaluate(_CONFIG, change, issue_body=None)
    assert result.classification == 2
    assert result.decision == "block"


def test_evaluate_class_2_via_prod_areas():
    change = ChangeSet(paths=("agents/x.py", "scripts/y.py"), churn_lines=1, prod_areas=2)
    result = gate.evaluate(_CONFIG, change, issue_body=None)
    assert result.classification == 2
    assert result.decision == "block"


# ── AC5: no duplicated thresholds/hashing logic ─────────────────────────────


def test_no_reimplemented_sha256_hashing_in_gate_script():
    src = Path(gate.__file__).read_text(encoding="utf-8")
    assert "hashlib" not in src, (
        "gate script must call agents.plan_lock.verify_lock, not hash directly"
    )


def test_no_reimplemented_threshold_constants_in_gate_script():
    src = Path(gate.__file__).read_text(encoding="utf-8")
    # the literal threshold values from config/plan_review.yaml must not be
    # hardcoded anywhere in the gate script
    assert not re.search(r"\b400\b", src)
    assert "churn_threshold" not in src or "config" in src.lower()


# ── meta-test (AC6): fires on trigger-glob diff, silent on non-trigger diff ─


def test_meta_fires_on_trigger_glob_diff():
    change = gate.compute_change_set(paths=("mcp-memory/server.py",), churn_lines=5)
    result = gate.evaluate(_CONFIG, change, issue_body=None)
    assert result.decision == "block"


def test_meta_silent_on_non_trigger_diff():
    change = gate.compute_change_set(paths=("agents/foo.py",), churn_lines=5)
    result = gate.evaluate(_CONFIG, change, issue_body=None)
    assert result.decision == "pass"


# ── comment content (AC7) ───────────────────────────────────────────────────


def test_block_reason_names_the_trigger_condition():
    change = ChangeSet(paths=("mcp-memory/x.py",), churn_lines=1, prod_areas=1)
    result = gate.evaluate(_CONFIG, change, issue_body=None)
    assert "shared" in result.reason.lower() or "surface" in result.reason.lower()
