"""Tests for agents/sandcastle_result.py — the S2 completion reader (#1120).

The supervisor (.sandcastle/main.mts, via .sandcastle/completion.mts) writes
an unconditional result file at run end. This reader validates it,
reconstructs the completion-event payload, and mirrors the watchdog's failure
classifier + fail-loud scrubber. The shared fixture
tests/fixtures/sandcastle-result.json pins the contract on BOTH sides: the TS
writer's buildResultFile (.sandcastle/check-result-contract.mts) and this
reader's validate_result_file must both accept it.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from agents import sandcastle_result as sr

FIXTURE = Path(__file__).resolve().parent.parent / "fixtures" / "sandcastle-result.json"


# -- fixture / validation ----------------------------------------------------


def test_fixture_validates():
    data = sr.read_result_file(FIXTURE)
    assert data["schemaVersion"] == 2
    assert data["outcome"] == sr.OUTCOMES["SUCCESS"]
    assert data["prEvidence"] is True
    assert isinstance(data["commits"], list) and data["commits"]


def test_validate_accepts_every_required_field():
    data = json.loads(FIXTURE.read_text())
    sr.validate_result_file(data)
    assert set(sr.REQUIRED_FIELDS) <= set(data)


def test_missing_required_field_rejected():
    data = json.loads(FIXTURE.read_text())
    del data["branch"]
    with pytest.raises(sr.SandcastleResultError, match="missing required field.*branch"):
        sr.validate_result_file(data)


def test_wrong_schema_version_rejected():
    data = json.loads(FIXTURE.read_text())
    data["schemaVersion"] = 1
    with pytest.raises(sr.SandcastleResultError, match="unsupported schemaVersion"):
        sr.validate_result_file(data)


def test_non_dict_rejected():
    with pytest.raises(sr.SandcastleResultError, match="JSON object"):
        sr.validate_result_file([])


def test_unknown_outcome_rejected():
    data = json.loads(FIXTURE.read_text())
    data["outcome"] = "banana"
    with pytest.raises(sr.SandcastleResultError, match="unknown outcome"):
        sr.validate_result_file(data)


def test_bad_pr_evidence_rejected():
    data = json.loads(FIXTURE.read_text())
    data["prEvidence"] = "maybe"
    with pytest.raises(sr.SandcastleResultError, match="prEvidence"):
        sr.validate_result_file(data)


def test_non_json_file_rejected(tmp_path):
    bad = tmp_path / "result.json"
    bad.write_text("not json {", encoding="utf-8")
    with pytest.raises(sr.SandcastleResultError, match="not valid JSON"):
        sr.read_result_file(bad)


# -- classifier --------------------------------------------------------------


def _classify(**overrides):
    kwargs = {
        "run_completed": True,
        "exit_code": 0,
        "commits_count": 1,
        "pinned_branch_exists": True,
        "log_tail": "",
        "error_text": "",
    }
    kwargs.update(overrides)
    return sr.classify_completion(**kwargs)


def test_classify_success():
    c = _classify()
    assert c["outcome"] == sr.OUTCOMES["SUCCESS"]
    assert c["event_type"] == "task_done"
    assert c["exit"] == 0
    assert c["infra"] is False
    assert c["failure_class"] is None


def test_classify_zero_commits_is_infra_fault():
    # Upstream mattpocock/sandcastle#855: zero commits on the Windows host is
    # an infra fault — burns no agent attempt, never escalates tier.
    c = _classify(commits_count=0)
    assert c["outcome"] == sr.OUTCOMES["INFRA_FAULT"]
    assert c["failure_class"] == sr.FAILURE_CLASSES["ZERO_COMMITS"]
    assert c["event_type"] == "task_failed"
    assert c["infra"] is True


def test_classify_missing_branch_is_infra_fault():
    c = _classify(pinned_branch_exists=False)
    assert c["outcome"] == sr.OUTCOMES["INFRA_FAULT"]
    assert c["failure_class"] == sr.FAILURE_CLASSES["MISSING_BRANCH"]
    assert c["infra"] is True


def test_classify_oom_signature():
    c = _classify(run_completed=False, error_text="cuda out of memory at kernel launch")
    assert c["failure_class"] == sr.FAILURE_CLASSES["OOM"]
    assert c["outcome"] == sr.OUTCOMES["INFRA_FAULT"]
    assert c["infra"] is True


def test_classify_oom_exit_137():
    c = _classify(run_completed=False, exit_code=137, error_text="")
    assert c["failure_class"] == sr.FAILURE_CLASSES["OOM"]


def test_classify_provider_billing_signature():
    c = _classify(run_completed=False, error_text="api returned insufficient balance")
    assert c["failure_class"] == sr.FAILURE_CLASSES["PROVIDER_BILLING"]
    assert c["infra"] is True


def test_classify_provider_billing_http_402():
    c = _classify(run_completed=False, error_text="HTTP/1.1 402 Payment Required")
    assert c["failure_class"] == sr.FAILURE_CLASSES["PROVIDER_BILLING"]


def test_classify_infra_error_class():
    c = _classify(run_completed=False, error_text="ContainerStartTimeoutError: boot exceeded 300s")
    assert c["failure_class"] == "ContainerStartTimeoutError"
    assert c["outcome"] == sr.OUTCOMES["INFRA_FAULT"]
    assert c["infra"] is True


def test_classify_agent_error_class():
    c = _classify(run_completed=False, error_text="AgentError: the model returned a malformed tool call")
    assert c["failure_class"] == "AgentError"
    assert c["outcome"] == sr.OUTCOMES["AGENT_FAULT"]
    assert c["infra"] is False


def test_classify_unknown_throw_fails_toward_infra():
    c = _classify(run_completed=False, error_text="something entirely unexpected")
    assert c["outcome"] == sr.OUTCOMES["INFRA_FAULT"]
    assert c["failure_class"] is None
    assert c["infra"] is True


# -- fail-loud scrubber ------------------------------------------------------


def test_scrub_text_replaces_literals_and_patterns():
    out = sr.scrub_text(
        "key=ghp_0123456789abcdefghijklmnopqrstuvwx "
        "url=sk-ant-abcdefghijklmnopqrstuvwxyz012 token=my-secret-token",
        ["my-secret-token"],
    )
    assert "ghp_0123456789abcdefghijklmnopqrstuvwx" not in out
    assert "<GH-TOKEN-REDACTED>" in out
    assert "<ANTHROPIC-KEY-REDACTED>" in out
    assert "my-secret-token" not in out
    assert "<SECRET-REDACTED>" in out


def test_scrub_payload_fail_loud_on_residual_secret():
    # A secret hiding in a non-string value survives scrub_text (only string
    # values are scrubbed) — the residual check must abort emission (#1092).
    secret = "sk-secret-abcdefghijklmnopqrstuvwxyz0123456789"
    scrubbed, safe = sr.scrub_completion_payload(
        {"failure_reason": "boom", "nested": [secret]}, [secret]
    )
    assert safe is False
    assert secret in json.dumps(scrubbed)


def test_scrub_payload_safe_after_replacement():
    secret = "sk-secret-abcdefghijklmnopqrstuvwxyz0123456789"
    scrubbed, safe = sr.scrub_completion_payload(
        {"failure_reason": f"boom {secret}", "exit": 1}, [secret]
    )
    assert safe is True
    assert "<SECRET-REDACTED>" in json.dumps(scrubbed)
    assert secret not in json.dumps(scrubbed)


def test_short_secret_replaced_but_not_residual_checked():
    # Documented gap: secrets <8 chars are replaced but never flagged (a short
    # value would false-positive on ordinary prose). The supervisor only passes
    # long values (URLs, API keys, tokens).
    secret = "abc123"
    scrubbed, safe = sr.scrub_completion_payload({"failure_reason": f"token {secret} here"}, [secret])
    assert safe is True
    assert secret not in json.dumps(scrubbed)


# -- payload reconstruction --------------------------------------------------


def test_build_completion_payload_carries_contract():
    data = sr.read_result_file(FIXTURE)
    payload = sr.build_completion_payload(data)
    for field in (
        "task_id",
        "attempt",
        "lineage_key",
        "branch",
        "pr",
        "exit",
        "failure_class",
        "tier",
    ):
        assert field in payload
    assert payload["task_id"] == "task-fixture-1"
    assert payload["pr"] == 1234
    assert payload["pr_evidence"] is True
    assert payload["exit_confirmed"] is False  # task_done
    assert payload["closing_ref"] == 1234


def test_build_completion_payload_failed_event():
    data = sr.read_result_file(FIXTURE)
    data["outcome"] = sr.OUTCOMES["INFRA_FAULT"]
    data["failureClass"] = sr.FAILURE_CLASSES["ZERO_COMMITS"]
    data["failureReason"] = "no commits on windows host"
    payload = sr.build_completion_payload(data)
    assert payload["exit_confirmed"] is True
    assert payload["failure_reason"] == "no commits on windows host"


def test_completion_severity():
    assert sr.completion_severity("task_done", True) == "info"
    assert sr.completion_severity("task_done", False) == "medium"
    assert sr.completion_severity("task_failed", False) == "medium"


def test_fixture_round_trip_reader_and_payload_agree():
    data = sr.read_result_file(FIXTURE)
    classification = sr.classify_completion(
        run_completed=True,
        exit_code=0,
        commits_count=len(data["commits"]),
        pinned_branch_exists=True,
        log_tail="",
        error_text="",
    )
    assert classification["event_type"] == "task_done"
    payload = sr.build_completion_payload(data)
    assert payload["pr_evidence"] is True
    assert payload["exit"] == 0
