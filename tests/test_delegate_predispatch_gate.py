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

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
gate = importlib.import_module("delegate_predispatch_gate")
check_issue = gate.check_issue


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
    envelope = {"issue": _issue(), "open_prs": [], "open_branches": []}
    monkeypatch.setattr("sys.stdin", _StringStream(json.dumps(envelope)))
    rc = gate.main([])
    assert rc == 0
    out = capsys.readouterr().out
    assert "OK" in out


def test_main_returns_nonzero_on_refuse(monkeypatch, capsys):
    envelope = {
        "issue": _issue(body="", labels=()),
        "open_prs": [],
        "open_branches": [],
    }
    monkeypatch.setattr("sys.stdin", _StringStream(json.dumps(envelope)))
    rc = gate.main([])
    assert rc == 1
    out = capsys.readouterr().out
    assert "REFUSE" in out
    assert "sandcastle" in out


class _StringStream:
    """Minimal sys.stdin stub for the CLI test."""

    def __init__(self, payload: str) -> None:
        self._payload = payload

    def read(self) -> str:
        return self._payload
