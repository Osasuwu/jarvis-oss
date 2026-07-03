"""Meta-test for .github/workflows/pr-body-check.yml.

Reimplements the workflow's decision rule in Python and asserts the
escape hatches behave as the workflow promises:

  - Closes #NNN in body                → allowed (linked)
  - priority:critical label             → allowed (hotfix bypass)
  - [no-issue] marker in body           → allowed (fix-inline per #428/#459)
  - refactor:/refactor(scope): title    → allowed (auto-bypass per #428)
  - none of the above                   → blocked

Convention from CLAUDE.md §326 (path-filtered guards need meta-tests).
PR Body Check isn't path-filtered, but the escape logic is non-trivial
enough that a sibling test is worth keeping in lockstep with the YAML.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest


WORKFLOW_PATH = (
    Path(__file__).resolve().parents[2]
    / ".github"
    / "workflows"
    / "pr-body-check.yml"
)


def evaluate(body: str, labels: list[str], title: str = "") -> tuple[bool, str]:
    """Mirror the workflow's decision rule. Returns (allowed, reason)."""
    if "priority:critical" in labels:
        return True, "hotfix"

    if re.search(r"\[no-issue\]", body, re.IGNORECASE):
        return True, "no-issue"

    if re.match(r"^refactor(\([^)]*\))?:", title, re.IGNORECASE):
        return True, "refactor"

    matches = re.findall(r"(?:closes|fixes|resolves)\s+#(\d+)", body, re.IGNORECASE)
    if matches:
        return True, "linked"

    return False, "no-link"


def test_closes_marker_allows():
    allowed, reason = evaluate("Closes #123\n\nSome body", [])
    assert allowed
    assert reason == "linked"


def test_alt_markers_allow():
    for marker in ("Closes", "Fixes", "Resolves", "closes", "FIXES"):
        body = f"{marker} #42"
        allowed, _ = evaluate(body, [])
        assert allowed, f"{marker} should be accepted"


def test_hotfix_label_bypasses():
    allowed, reason = evaluate("urgent prod fix, no time to file", ["priority:critical"])
    assert allowed
    assert reason == "hotfix"


def test_other_labels_do_not_bypass():
    allowed, _ = evaluate("trivial change", ["priority:medium", "documentation"])
    assert not allowed


def test_empty_body_no_label_blocked():
    allowed, _ = evaluate("", [])
    assert not allowed


def test_partial_keyword_blocked():
    # "close to" should not match "closes"
    allowed, _ = evaluate("This is close to done.", [])
    assert not allowed


def test_workflow_yaml_references_priority_critical():
    """Config dimension: workflow file must mention the label name we test against."""
    text = WORKFLOW_PATH.read_text(encoding="utf-8")
    assert "priority:critical" in text, (
        "PR Body Check workflow no longer references the priority:critical "
        "escape; this test (and CLAUDE.md hotfix rule) is now stale."
    )


def test_workflow_yaml_keeps_closes_regex():
    """Config dimension: the regex used in the YAML must still recognize Closes/Fixes/Resolves."""
    text = WORKFLOW_PATH.read_text(encoding="utf-8")
    assert "closes|fixes|resolves" in text.lower(), (
        "PR Body Check regex changed shape — update this test to match."
    )


def test_no_issue_marker_allows():
    allowed, reason = evaluate("Drive-by docs fix.\n\n[no-issue]", [])
    assert allowed
    assert reason == "no-issue"


def test_no_issue_marker_case_insensitive():
    for marker in ("[no-issue]", "[NO-ISSUE]", "[No-Issue]"):
        body = f"trivial fix\n\n{marker}"
        allowed, reason = evaluate(body, [])
        assert allowed, f"{marker} should be accepted"
        assert reason == "no-issue"


def test_no_issue_marker_with_unrelated_label():
    allowed, reason = evaluate("[no-issue] inline doc fix", ["documentation"])
    assert allowed
    assert reason == "no-issue"


def test_no_issue_phrase_without_brackets_blocked():
    # Plain prose "no issue" must not satisfy the escape — only the bracketed token.
    allowed, _ = evaluate("There is no issue with this change.", [])
    assert not allowed


def test_workflow_yaml_keeps_no_issue_escape():
    """Config dimension: the workflow must still honor the [no-issue] marker."""
    text = WORKFLOW_PATH.read_text(encoding="utf-8")
    assert "[no-issue]" in text, (
        "PR Body Check workflow no longer references the [no-issue] escape; "
        "this test (and CLAUDE.md fix>track rule #428) is now stale."
    )


def test_refactor_title_allows():
    allowed, reason = evaluate("just a refactor", [], title="refactor: split foo into bar")
    assert allowed
    assert reason == "refactor"


def test_refactor_with_scope_allows():
    allowed, reason = evaluate("", [], title="refactor(security): narrow protected-files")
    assert allowed
    assert reason == "refactor"


def test_refactor_case_insensitive():
    for title in ("refactor: x", "Refactor: x", "REFACTOR(api): x"):
        allowed, reason = evaluate("", [], title=title)
        assert allowed, f"{title!r} should bypass"
        assert reason == "refactor"


def test_refactoring_word_in_title_blocked():
    # Only the conventional-commit prefix qualifies — prose mentions don't.
    allowed, _ = evaluate("body", [], title="Refactoring the auth module")
    assert not allowed


def test_refactor_substring_blocked():
    # Mid-title "refactor" must not bypass.
    allowed, _ = evaluate("body", [], title="feat: include refactor: in description")
    assert not allowed


def test_workflow_yaml_keeps_refactor_escape():
    """Config dimension: the workflow must still honor the refactor: title prefix."""
    text = WORKFLOW_PATH.read_text(encoding="utf-8")
    assert "^refactor" in text, (
        "PR Body Check workflow no longer references the refactor: title escape; "
        "this test is now stale."
    )


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
