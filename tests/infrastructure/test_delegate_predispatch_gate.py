"""Tests for scripts/delegate_predispatch_gate.py.

Reference implementation of the /delegate pre-dispatch gate (issue #642).
The gate refuses to dispatch a sandcastle subagent unless all four readiness
conditions hold for the target issue.
"""

from __future__ import annotations

import importlib
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
gate = importlib.import_module("delegate_predispatch_gate")
check_issue = gate.check_issue
check_repo = gate.check_repo
check_orchestrator_target = gate.check_orchestrator_target


# ── Fixtures ────────────────────────────────────────────────────────────────

VALID_UUID = "6b0a5bf7-8ca9-47cc-81cf-ebae39c81d08"
VALID_BODY = (
    f"## Acceptance criteria\n- [ ] do thing\n- [ ] do other thing\n\nDecisions: {VALID_UUID}\n"
)


def _issue(body: str = VALID_BODY, labels: tuple[str, ...] = ("sandcastle",), number: int = 999):
    return {
        "number": number,
        "body": body,
        "labels": [{"name": n} for n in labels],
    }


# ── Allow path ──────────────────────────────────────────────────────────────


def test_allows_when_all_four_conditions_present():
    result = check_issue(_issue())
    assert result.allow
    assert result.message == "OK"
    assert result.failures == ()


def test_acceptance_criteria_heading_is_case_insensitive():
    body = f"## ACCEPTANCE CRITERIA\n- [ ] x\n{VALID_UUID}"
    assert check_issue(_issue(body=body)).allow


def test_acceptance_criteria_heading_with_suffix_words_matches():
    body = f"## Acceptance criteria (brief)\n- [ ] x\n{VALID_UUID}"
    assert check_issue(_issue(body=body)).allow


def test_uuid_anywhere_in_body_satisfies():
    body = f"some prose {VALID_UUID} more prose\n## Acceptance criteria\n- [ ] x\n"
    assert check_issue(_issue(body=body)).allow


def test_no_decision_marker_satisfies_condition_four():
    """#1099 — pure-mechanical slices cite `[no-decision]` instead of a synthetic UUID."""
    body = "## Acceptance criteria\n- [ ] do thing\n\n## Decisions\n\n[no-decision]\n"
    assert check_issue(_issue(body=body)).allow


def test_no_decision_marker_is_case_insensitive():
    body = "## Acceptance criteria\n- [ ] x\n[NO-DECISION]\n"
    assert check_issue(_issue(body=body)).allow


# ── Refusal: missing sandcastle label ───────────────────────────────────────


def test_refuses_when_sandcastle_label_missing():
    result = check_issue(_issue(labels=()))
    assert not result.allow
    assert "sandcastle" in result.message


def test_refuses_when_only_unrelated_labels_present():
    result = check_issue(_issue(labels=("task", "area:skills")))
    assert not result.allow
    assert "sandcastle" in result.message


# ── Refusal: needs-* labels ────────────────────────────────────────────────


@pytest.mark.parametrize(
    "needs_label",
    ["needs-grill", "needs-research", "needs-prd", "needs-refactor"],
)
def test_refuses_when_any_needs_label_present(needs_label):
    result = check_issue(_issue(labels=("sandcastle", needs_label)))
    assert not result.allow
    assert needs_label in result.message


def test_refuses_on_unknown_needs_prefix_too():
    """Future-proof: any needs-* label, not only the enumerated four."""
    result = check_issue(_issue(labels=("sandcastle", "needs-design")))
    assert not result.allow
    assert "needs-design" in result.message


# ── Refusal: missing ## Acceptance criteria heading ─────────────────────────


def test_refuses_when_acceptance_criteria_section_missing():
    body = f"Some body without the heading. {VALID_UUID}"
    result = check_issue(_issue(body=body))
    assert not result.allow
    assert "Acceptance criteria" in result.message


def test_refuses_when_acceptance_criteria_only_inline_text_not_a_heading():
    body = f"acceptance criteria: do thing. {VALID_UUID}"
    result = check_issue(_issue(body=body))
    assert not result.allow


def test_refuses_when_heading_wrong_level():
    body = f"### Acceptance criteria\n- [ ] x\n{VALID_UUID}"
    result = check_issue(_issue(body=body))
    assert not result.allow


# ── Refusal: missing decision UUID ──────────────────────────────────────────


def test_refuses_when_no_uuid_anywhere():
    body = "## Acceptance criteria\n- [ ] do thing"
    result = check_issue(_issue(body=body))
    assert not result.allow
    assert "decision UUID" in result.message


def test_refuses_on_non_canonical_uuid_shape():
    body = "## Acceptance criteria\n- [ ] x\nsee abc12345-not-real-shape\n"
    result = check_issue(_issue(body=body))
    assert not result.allow
    assert "decision UUID" in result.message


# ── Multiple failures: all are reported ─────────────────────────────────────


def test_refuses_lists_all_failures_when_everything_missing():
    result = check_issue(_issue(body="", labels=()))
    assert not result.allow
    # All four readiness gaps should be present in the message
    assert "sandcastle" in result.message
    assert "Acceptance criteria" in result.message
    assert "decision UUID" in result.message
    assert len(result.failures) == 3  # no needs-* label here, so only 3


def test_refuses_with_three_simultaneous_gaps_and_needs_label():
    result = check_issue(_issue(body="", labels=("needs-grill",)))
    assert not result.allow
    assert len(result.failures) == 4
    assert "sandcastle" in result.message
    assert "needs-grill" in result.message
    assert "Acceptance criteria" in result.message
    assert "decision UUID" in result.message


# ── Edge cases ──────────────────────────────────────────────────────────────


def test_handles_null_body():
    issue = {"number": 1, "labels": [{"name": "sandcastle"}], "body": None}
    result = check_issue(issue)
    assert not result.allow


def test_handles_missing_body_key():
    issue = {"number": 1, "labels": [{"name": "sandcastle"}]}
    result = check_issue(issue)
    assert not result.allow


def test_handles_missing_labels_key():
    issue = {"number": 1, "body": VALID_BODY}
    result = check_issue(issue)
    assert not result.allow  # no sandcastle label


# ── CLI smoke (stdin JSON → exit code) ──────────────────────────────────────


# Since #931 the CLI takes a strict envelope: {issue, open_prs, open_branches}.
# Bare-issue stdin now fails closed (exit 2) — see tests/test_dispatch_dedup.py.


def test_main_returns_zero_on_allow(monkeypatch, capsys):
    envelope = {
        "issue": _issue(),
        "repo": "your-username/jarvis",
        "open_prs": [],
        "open_branches": [],
    }
    monkeypatch.setattr("sys.stdin", _StringStream(json.dumps(envelope)))
    rc = gate.main([])
    assert rc == 0
    out = capsys.readouterr().out
    assert "OK" in out


def test_main_returns_nonzero_on_refuse(monkeypatch, capsys):
    envelope = {
        "issue": _issue(body="", labels=()),
        "repo": "your-username/jarvis",
        "open_prs": [],
        "open_branches": [],
    }
    monkeypatch.setattr("sys.stdin", _StringStream(json.dumps(envelope)))
    rc = gate.main([])
    assert rc == 1
    out = capsys.readouterr().out
    assert "REFUSE" in out
    assert "sandcastle" in out


def test_main_returns_one_on_repo_mismatch_before_readiness_check(monkeypatch, capsys):
    """A foreign-repo issue is refused even if it also fails readiness (#1651)."""
    envelope = {
        "issue": _issue(body="", labels=()),
        "repo": "SergazyNarynov/redrobot",
        "open_prs": [],
        "open_branches": [],
    }
    monkeypatch.setattr("sys.stdin", _StringStream(json.dumps(envelope)))
    rc = gate.main([])
    assert rc == 1
    out = capsys.readouterr().out
    assert "REFUSE" in out
    assert "SergazyNarynov/redrobot" in out
    assert "sandcastle" not in out  # repo check short-circuits before readiness


def test_main_skips_on_missing_repo_key(monkeypatch, capsys):
    envelope = {"issue": _issue(), "open_prs": [], "open_branches": []}
    monkeypatch.setattr("sys.stdin", _StringStream(json.dumps(envelope)))
    rc = gate.main([])
    assert rc == 2
    out = capsys.readouterr().out
    assert "SKIP" in out
    assert "repo" in out


# ── check_repo (#1651) ───────────────────────────────────────────────────────


def test_check_repo_allows_matching_default():
    result = check_repo("your-username/jarvis", default_repo="your-username/jarvis")
    assert result.allow


def test_check_repo_refuses_mismatch():
    result = check_repo("SergazyNarynov/redrobot", default_repo="your-username/jarvis")
    assert not result.allow
    assert "SergazyNarynov/redrobot" in result.message
    assert "M58" in result.message or "#1651" in result.message


def test_check_repo_falls_back_to_github_repo_env(monkeypatch):
    monkeypatch.setenv("GITHUB_REPO", "your-username/other-repo")
    assert check_repo("your-username/other-repo").allow
    assert not check_repo("your-username/jarvis").allow


def test_check_repo_falls_back_to_hardcoded_default_when_env_unset(monkeypatch):
    monkeypatch.delenv("GITHUB_REPO", raising=False)
    assert check_repo("your-username/jarvis").allow


# ── check_orchestrator_target (#1617) ───────────────────────────────────────


def _row(target_type=None, target_repo=None, target_number=None):
    return {
        "target_type": target_type,
        "target_repo": target_repo,
        "target_number": target_number,
    }


def test_orchestrator_target_passes_when_type_none():
    result = check_orchestrator_target(_row(target_type="none"))
    assert result.allow


def test_orchestrator_target_passes_when_pr_open_and_same_repo():
    fetch_pull = lambda n: {"state": "open"}  # noqa: E731
    result = check_orchestrator_target(
        _row(target_type="pr", target_repo="your-username/jarvis", target_number=42),
        fetch_pull=fetch_pull,
        default_repo="your-username/jarvis",
    )
    assert result.allow


def test_orchestrator_target_parks_when_pr_foreign_repo():
    fetch_pull = lambda n: {"state": "open"}  # noqa: E731
    result = check_orchestrator_target(
        _row(target_type="pr", target_repo="SergazyNarynov/redrobot", target_number=42),
        fetch_pull=fetch_pull,
        default_repo="your-username/jarvis",
    )
    assert not result.allow
    assert "SergazyNarynov/redrobot" in result.message


def test_orchestrator_target_parks_when_pr_closed_or_merged():
    fetch_pull = lambda n: {"state": "closed", "merged": True}  # noqa: E731
    result = check_orchestrator_target(
        _row(target_type="pr", target_repo="your-username/jarvis", target_number=42),
        fetch_pull=fetch_pull,
        default_repo="your-username/jarvis",
    )
    assert not result.allow
    assert "42" in result.message


def test_orchestrator_target_parks_when_pr_number_missing():
    result_no_number = check_orchestrator_target(
        _row(target_type="pr", target_repo="your-username/jarvis", target_number=None),
        fetch_pull=lambda n: {"state": "open"},
        default_repo="your-username/jarvis",
    )
    assert not result_no_number.allow


def test_orchestrator_target_disables_pr_gate_when_fetch_pull_unwired():
    """fetch_pull=None (the default) must DISABLE the PR-state re-check, not
    park the row — same DI contract as fetch_issue (#1617 AC, DedupConfig
    .fetch_pull docstring: "None here (the default) disables that re-check
    entirely"). A caller that never wires fetch_pull must not have every
    PR-pinned row parked by omission (#1758 review finding 2)."""
    result_no_fetch = check_orchestrator_target(
        _row(target_type="pr", target_repo="your-username/jarvis", target_number=42),
        fetch_pull=None,
        default_repo="your-username/jarvis",
    )
    assert result_no_fetch.allow


def test_orchestrator_target_parks_when_type_null():
    result = check_orchestrator_target(_row(target_type=None))
    assert not result.allow
    assert "unclassified" in result.message


def test_orchestrator_target_parks_when_issue_unverifiable():
    result = check_orchestrator_target(
        _row(target_type="issue", target_number=7), fetched_issue=None
    )
    assert not result.allow
    assert "unverifiable" in result.message


def test_orchestrator_target_passes_when_issue_mechanical_subset_satisfied():
    fetched_issue = {"labels": [{"name": "sandcastle"}]}
    result = check_orchestrator_target(
        _row(target_type="issue", target_number=7), fetched_issue=fetched_issue
    )
    assert result.allow


def test_orchestrator_target_parks_on_unrecognized_type():
    result = check_orchestrator_target(_row(target_type="bogus"))
    assert not result.allow


class _StringStream:
    """Minimal sys.stdin stub for the CLI test."""

    def __init__(self, payload: str) -> None:
        self._payload = payload

    def read(self) -> str:
        return self._payload
