"""Golden-file fixture tests for ``mcp-memory/review_render.py``.

Covers every row type the renderer accepts: classifier UPDATE (with diff),
classifier ADD / DELETE / NOOP, merge proposal, candidate, empty list,
and a previously-rejected item.

All tests are pure — no network, no DB, no mocks. Input data is inline.
Expected output is inline (golden strings) so fixture drift is visible in
the diff on every PR.
"""

from __future__ import annotations

import importlib.util
import uuid
from pathlib import Path

_THIS_DIR = Path(__file__).resolve().parent
_WORKSPACE_ROOT = _THIS_DIR.parent
_REVIEW_RENDER_PATH = _WORKSPACE_ROOT / "mcp-memory" / "review_render.py"

# Hyphen in parent directory name → use spec_from_file_location.
_spec = importlib.util.spec_from_file_location("review_render", _REVIEW_RENDER_PATH)
assert _spec and _spec.loader
review_render = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(review_render)


def _row(**overrides: str | list | None) -> dict:
    """Build a minimal ``memory_review_list``-shaped row."""
    base = {
        "id": str(uuid.uuid4()),
        "name": "test_memory",
        "type": "feedback",
        "project": "jarvis",
        "description": "Test description",
        "content": "Test content body",
        "tags": ["test", "fixture"],
        "source_provenance": "classifier:test",
        "requires_review": True,
        "merge_targets": None,
        "reject_reason": None,
        "created_at": "2026-05-20T12:00:00Z",
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# classifier UPDATE — with diff
# ---------------------------------------------------------------------------


class TestClassifierUpdate:
    def test_content_diff(self):
        row = _row(
            name="async_pref",
            description="User prefers async workflows",
            content="User strongly prefers async code review workflows and "
            "avoids synchronous meetings for code discussions.",
            tags=["workflow", "async", "code-review"],
        )
        before = {
            "description": "User likes async",
            "content": "User prefers async workflows for code review.",
            "tags": ["workflow", "async"],
        }
        ctx = {
            "decision": "UPDATE",
            "before_snapshot": before,
            "reasoning": "More specific preference detected",
        }
        result = review_render.render_proposal(row, ctx)
        assert "### async_pref (feedback) — UPDATE" in result
        assert "Description:" in result
        assert "Content:" in result
        assert "+ code-review" in result
        assert "More specific preference detected" in result
        assert "```diff" in result

    def test_no_diff_when_unchanged(self):
        """When before and after are identical, no diff block is emitted."""
        before = {"description": "Same", "content": "Same body", "tags": ["a"]}
        row = _row(description="Same", content="Same body", tags=["a"])
        ctx = {"decision": "UPDATE", "before_snapshot": before}
        result = review_render.render_proposal(row, ctx)
        assert "UPDATE" in result
        assert "```diff" not in result
        assert "Content:" not in result  # no section header when unchanged

    def test_tag_changes_only(self):
        before = {"description": "Desc", "content": "Body", "tags": ["old"]}
        row = _row(description="Desc", content="Body", tags=["old", "new"])
        ctx = {"decision": "UPDATE", "before_snapshot": before}
        result = review_render.render_proposal(row, ctx)
        assert "+ new" in result
        assert "```diff" not in result  # content unchanged, no diff block

    def test_missing_before_snapshot_falls_back_to_compact(self):
        """UPDATE without before_snapshot renders as compact card."""
        row = _row(source_provenance="classifier:update")
        ctx = {"decision": "UPDATE"}  # no before_snapshot
        result = review_render.render_proposal(row, ctx)
        # Falls back to compact card
        assert "UPDATE" in result or "CLASSIFIER" in result
        assert "Provenance:" in result


# ---------------------------------------------------------------------------
# classifier ADD / DELETE / NOOP
# ---------------------------------------------------------------------------


class TestClassifierCompact:
    def test_add(self):
        row = _row(
            name="new_insight",
            source_provenance="classifier:add:2026-05-20",
            description="A brand new insight",
            content="This is a new memory proposed by the classifier.",
            tags=["insight", "new"],
        )
        ctx = {"decision": "ADD", "reasoning": "New fact worth recording"}
        result = review_render.render_proposal(row, ctx)
        assert "new_insight" in result
        assert "ADD" in result
        assert "A brand new insight" in result
        assert "This is a new memory" in result
        assert "New fact worth recording" in result

    def test_delete(self):
        row = _row(
            name="stale_memory",
            source_provenance="classifier:delete:2026-05-20",
            description="Stale memory to remove",
        )
        ctx = {"decision": "DELETE", "reasoning": "Superseded by newer entry"}
        result = review_render.render_proposal(row, ctx)
        assert "stale_memory" in result
        assert "DELETE" in result
        assert "Superseded by newer entry" in result

    def test_noop(self):
        row = _row(
            name="already_captured",
            source_provenance="classifier:noop:2026-05-20",
        )
        ctx = {"decision": "NOOP", "reasoning": "Already captured in existing memory"}
        result = review_render.render_proposal(row, ctx)
        assert "already_captured" in result
        assert "NOOP" in result

    def test_missing_decision_label(self):
        """Classifier row without explicit decision in context still renders."""
        row = _row(source_provenance="classifier:unknown")
        result = review_render.render_proposal(row)
        assert "CLASSIFIER" in result or "unknown" not in result
        assert "Provenance:" in result


# ---------------------------------------------------------------------------
# Merge proposal
# ---------------------------------------------------------------------------


class TestMergeProposal:
    def test_basic_merge(self):
        row = _row(
            name="merged_feedback",
            description="Consolidated feedback about X",
            merge_targets=["uuid-a", "uuid-b"],
            source_provenance="dreamer:run-123",
        )
        result = review_render.render_proposal(row)
        assert "merged_feedback" in result
        assert "MERGE (2 targets)" in result
        assert "uuid-a" in result
        assert "uuid-b" in result
        assert "Consolidated feedback" in result

    def test_classifier_with_merge_targets_renders_as_merge(self):
        """Compound row (classifier:* provenance + non-empty merge_targets) must show targets.

        Regression: round-2 routing checked `is_classifier` before `has_merge_targets`,
        so a classifier:add:* row that also consolidates duplicates rendered as a
        plain classifier compact card and the merge target UUIDs were silently
        dropped from the reviewer's view.
        """
        row = _row(
            name="consolidated_add",
            description="New consolidated memory replacing duplicates",
            merge_targets=["uuid-dup-1", "uuid-dup-2", "uuid-dup-3"],
            source_provenance="classifier:add:2026-05-26",
        )
        ctx = {"decision": "ADD", "reasoning": "Consolidates 3 duplicates into one canonical"}
        result = review_render.render_proposal(row, ctx)
        assert "MERGE (3 targets)" in result
        assert "uuid-dup-1" in result
        assert "uuid-dup-2" in result
        assert "uuid-dup-3" in result


# ---------------------------------------------------------------------------
# Candidate
# ---------------------------------------------------------------------------


class TestCandidate:
    def test_basic_candidate(self):
        row = _row(
            name="derived_insight",
            source_provenance="dreamer:run-123",
            description="An insight derived from cross-corpus analysis",
            content="The agent should remember this important pattern.",
            tags=["dreamer", "insight"],
        )
        result = review_render.render_proposal(row)
        assert "derived_insight" in result
        assert "CANDIDATE" in result
        assert "An insight derived" in result
        assert "Provenance:" in result
        assert "dreamer:run-123" in result


# ---------------------------------------------------------------------------
# Previously rejected
# ---------------------------------------------------------------------------


class TestRejectedItem:
    def test_shows_reject_reason(self):
        row = _row(
            name="rejected_candidate",
            reject_reason="Not actionable, vague scope",
        )
        result = review_render.render_proposal(row)
        assert "rejected_candidate" in result
        assert "Previously rejected" in result
        assert "Not actionable" in result


# ---------------------------------------------------------------------------
# render_proposal_list
# ---------------------------------------------------------------------------


class TestRenderList:
    def test_empty_list(self):
        result = review_render.render_proposal_list([])
        assert "No pending proposals" in result

    def test_multiple_rows(self):
        rows = [
            _row(name="first", description="First item"),
            _row(name="second", description="Second item"),
        ]
        result = review_render.render_proposal_list(rows)
        assert "first" in result
        assert "second" in result
        assert "---" in result  # separator between items

    def test_contexts_length_mismatch_raises(self):
        import pytest

        rows = [_row(name="only")]
        with pytest.raises(ValueError, match="contexts length"):
            review_render.render_proposal_list(rows, contexts=[None, None])
