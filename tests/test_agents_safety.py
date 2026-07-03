"""Unit tests for ``agents.safety`` — S2-0 safety gate module (issue #295).

Semantics pinned here mirror ``action_agent_safety_gate_model_v1``:

1. Tier 0 = narrow whitelist (specific GitHub labels, Sprint-1 Supabase
   tables, memory with ``auto-generated`` tag).
2. Tier 2 = blocked outright (``.env*``, ``.claude/*``, destructive
   actions, impersonation, cross-repo, messaging area).
3. Tier 1 = everything else (owner queue).
4. Idempotency keys are deterministic and change with ``scope_hash``.
5. ``gate()`` audits every classification even when it refuses to fire.
6. Audit is best-effort — backend failure never raises from safety.
"""

from __future__ import annotations

from typing import Any

import pytest

from agents import safety


# ---------------------------------------------------------------------------
# classify — Tier 0 whitelist
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "label",
    [
        "priority:high",
        "priority:medium",
        "priority:low",
        "needs-research",
        "needs-triage",
        "status:ready",
    ],
)
def test_tier0_github_labels_on_whitelist(label: str) -> None:
    assert safety.classify("gh", "add_label", label, area="github") == safety.Tier.AUTO


def test_tier0_github_area_prefix() -> None:
    # area:* labels follow a prefix rule, not an exact-match list.
    assert safety.classify("gh", "add_label", "area:core-agent", area="github") == safety.Tier.AUTO
    assert (
        safety.classify("gh", "add_label", "area:infrastructure", area="github") == safety.Tier.AUTO
    )


@pytest.mark.parametrize(
    "table,action",
    [
        ("events", "insert"),
        ("events", "append"),
        ("audit_log", "insert"),
        ("goals", "progress_append"),
        ("goals", "update_progress"),
    ],
)
def test_tier0_supabase_sprint1_whitelist(table: str, action: str) -> None:
    assert safety.classify("sb", action, table, area="supabase") == safety.Tier.AUTO


def test_tier0_memory_store_requires_auto_generated_tag() -> None:
    tagged = safety.classify("mem", "store", "some_memory", area="memory", tags=["auto-generated"])
    untagged = safety.classify("mem", "store", "some_memory", area="memory", tags=["decision"])
    assert tagged == safety.Tier.AUTO
    assert untagged == safety.Tier.OWNER_QUEUE


# ---------------------------------------------------------------------------
# classify — Tier 2 blocklist
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "target",
    [
        ".env",
        ".env.local",
        "config/.env.production",
        ".claude/settings.json",
        "src/.claude/skills/my_skill.md",
    ],
)
def test_tier2_protected_file_patterns(target: str) -> None:
    assert safety.classify("fs", "write", target, area="filesystem") == safety.Tier.BLOCKED


def test_tier2_protected_paths_normalise_windows_separators() -> None:
    # A Windows-style path still must hit the blocklist.
    assert (
        safety.classify("fs", "write", r"C:\repo\.claude\settings.json", area="filesystem")
        == safety.Tier.BLOCKED
    )


@pytest.mark.parametrize(
    "action",
    ["delete", "destroy", "drop", "truncate", "force_push", "impersonate", "send_as_owner"],
)
def test_tier2_destructive_actions(action: str) -> None:
    assert safety.classify("anything", action, "any_target") == safety.Tier.BLOCKED


@pytest.mark.parametrize("tool_name", ["send_as_owner", "tg_impersonate_user", "EMAIL_IMPERSONATE"])
def test_tier2_tool_name_impersonation_substrings(tool_name: str) -> None:
    assert safety.classify(tool_name, "send", "anyone") == safety.Tier.BLOCKED


def test_tier2_messaging_area_blocked_wholesale() -> None:
    # Hard rule per no_sending_from_owner_name — messaging tools are not
    # available to agents until the digital-twin pillar ships.
    assert (
        safety.classify("tg_bot", "send_message", "chat-42", area="messaging")
        == safety.Tier.BLOCKED
    )


def test_tier2_cross_repo_write_blocked() -> None:
    # Writing to a repo outside the allowed scope is Tier 2.
    assert (
        safety.classify("gh", "comment", "OtherUser/otherrepo#10", area="github")
        == safety.Tier.BLOCKED
    )


def test_tier2_same_repo_write_not_blocked_by_cross_repo_rule() -> None:
    # A non-whitelisted action on the allowed repo should fall back to
    # Tier 1 (owner queue), not Tier 2.
    assert (
        safety.classify("gh", "comment", "Osasuwu/jarvis#10", area="github")
        == safety.Tier.OWNER_QUEUE
    )


# ---------------------------------------------------------------------------
# classify — Tier 2 wins over Tier 0
# ---------------------------------------------------------------------------


def test_tier2_wins_over_tier0_when_both_match() -> None:
    # Constructed clash: ``delete`` is Tier 2; even with a whitelisted
    # label target the action verb must dominate. Safety bias: deny-first.
    assert safety.classify("gh", "delete", "priority:low", area="github") == safety.Tier.BLOCKED


# ---------------------------------------------------------------------------
# classify — Tier 1 default
# ---------------------------------------------------------------------------


def test_tier1_default_for_unknown_area() -> None:
    assert safety.classify("unknown_tool", "do_thing", "somewhere") == safety.Tier.OWNER_QUEUE


def test_tier1_github_label_outside_whitelist() -> None:
    # ``priority:critical`` is intentionally NOT on the whitelist.
    assert (
        safety.classify("gh", "add_label", "priority:critical", area="github")
        == safety.Tier.OWNER_QUEUE
    )


def test_tier1_supabase_table_outside_whitelist() -> None:
    # ``memories`` insert is not Tier 0 — agents should not silently mint
    # authoritative memory without owner review.
    assert safety.classify("sb", "insert", "memories", area="supabase") == safety.Tier.OWNER_QUEUE


# ---------------------------------------------------------------------------
# idempotency_key
# ---------------------------------------------------------------------------


def test_idempotency_key_is_deterministic() -> None:
    k1 = safety.idempotency_key("agent-a", "label", "target-x")
    k2 = safety.idempotency_key("agent-a", "label", "target-x")
    assert k1 == k2


def test_idempotency_key_changes_with_scope_hash() -> None:
    k_no_scope = safety.idempotency_key("agent-a", "label", "target-x")
    k_with_scope = safety.idempotency_key("agent-a", "label", "target-x", scope_hash="sha-1")
    k_other_scope = safety.idempotency_key("agent-a", "label", "target-x", scope_hash="sha-2")
    assert k_no_scope != k_with_scope
    assert k_with_scope != k_other_scope


def test_idempotency_key_sensitive_to_every_input() -> None:
    base = safety.idempotency_key("a", "x", "t", "s")
    assert base != safety.idempotency_key("b", "x", "t", "s")
    assert base != safety.idempotency_key("a", "y", "t", "s")
    assert base != safety.idempotency_key("a", "x", "u", "s")
    assert base != safety.idempotency_key("a", "x", "t", "r")


def test_idempotency_key_is_hex_sha256() -> None:
    key = safety.idempotency_key("a", "x")
    assert len(key) == 64
    int(key, 16)  # raises if not hex


# ---------------------------------------------------------------------------
# gate() — audit capture helper
# ---------------------------------------------------------------------------


class _AuditSpy:
    """Replace ``supabase_client.audit`` to capture calls without a backend."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.raise_with: Exception | None = None

    def __call__(self, **kwargs: Any) -> None:
        if self.raise_with is not None:
            raise self.raise_with
        self.calls.append(kwargs)


@pytest.fixture
def audit_spy(monkeypatch: pytest.MonkeyPatch) -> _AuditSpy:
    spy = _AuditSpy()
    monkeypatch.setattr("agents.safety.supabase_client.audit", spy)
    return spy


# ---------------------------------------------------------------------------
# gate() — Tier 2 raises + audits
# ---------------------------------------------------------------------------


def test_gate_tier2_raises_gate_error(audit_spy: _AuditSpy) -> None:
    calls: list[str] = []

    def fn() -> None:
        calls.append("fired")

    with pytest.raises(safety.GateError):
        safety.gate(
            agent_id="test-agent",
            tool_name="fs",
            action="delete",
            target="some.txt",
            area="filesystem",
            fn=fn,
        )

    assert calls == []  # fn must not run
    assert len(audit_spy.calls) == 1
    row = audit_spy.calls[0]
    assert row["outcome"] == "blocked"
    assert row["details"]["tier"] == int(safety.Tier.BLOCKED)
    assert len(row["details"]["idempotency_key"]) == 64


# ---------------------------------------------------------------------------
# gate() — Tier 1 queues, no fn call
# ---------------------------------------------------------------------------


def test_gate_tier1_queues_and_skips_fn(audit_spy: _AuditSpy) -> None:
    calls: list[str] = []

    result = safety.gate(
        agent_id="test-agent",
        tool_name="gh",
        action="comment",
        target="Osasuwu/jarvis#42",
        area="github",
        fn=lambda: calls.append("fired"),
    )

    assert calls == []
    assert result.tier == safety.Tier.OWNER_QUEUE
    assert result.queued is True
    assert result.fired is False
    assert audit_spy.calls[-1]["outcome"] == "queued"


def test_gate_tier1_dry_run_marks_outcome_dry_run_queued(audit_spy: _AuditSpy) -> None:
    safety.gate(
        agent_id="test-agent",
        tool_name="gh",
        action="comment",
        target="Osasuwu/jarvis#42",
        area="github",
        dry_run=True,
    )
    assert audit_spy.calls[-1]["outcome"] == "dry_run_queued"


# ---------------------------------------------------------------------------
# gate() — Tier 0 dry-run
# ---------------------------------------------------------------------------


def test_gate_tier0_dry_run_skips_fn_and_audits_dry_run(audit_spy: _AuditSpy) -> None:
    calls: list[str] = []

    result = safety.gate(
        agent_id="test-agent",
        tool_name="gh",
        action="add_label",
        target="priority:high",
        area="github",
        dry_run=True,
        fn=lambda: calls.append("fired"),
    )

    assert calls == []
    assert result.tier == safety.Tier.AUTO
    assert result.fired is False
    assert result.dry_run is True
    assert audit_spy.calls[-1]["outcome"] == "dry_run"
    assert audit_spy.calls[-1]["details"]["tier"] == int(safety.Tier.AUTO)


# ---------------------------------------------------------------------------
# gate() — Tier 0 live happy path
# ---------------------------------------------------------------------------


def test_gate_tier0_live_runs_fn_and_audits_success(audit_spy: _AuditSpy) -> None:
    calls: list[str] = []

    result = safety.gate(
        agent_id="test-agent",
        tool_name="gh",
        action="add_label",
        target="priority:high",
        area="github",
        fn=lambda: calls.append("fired"),
    )

    assert calls == ["fired"]
    assert result.fired is True
    assert result.tier == safety.Tier.AUTO
    assert audit_spy.calls[-1]["outcome"] == "success"


def test_gate_tier0_fn_none_records_classified(audit_spy: _AuditSpy) -> None:
    # When the caller only wants classification + audit, not a side
    # effect, gate still audits with an explicit outcome.
    result = safety.gate(
        agent_id="test-agent",
        tool_name="gh",
        action="add_label",
        target="priority:high",
        area="github",
    )
    assert result.fired is False
    assert audit_spy.calls[-1]["outcome"] == "classified"


# ---------------------------------------------------------------------------
# gate() — Tier 0 live failure reraises + audits
# ---------------------------------------------------------------------------


def test_gate_tier0_failure_audits_and_reraises(audit_spy: _AuditSpy) -> None:
    def boom() -> None:
        raise ValueError("kaboom")

    with pytest.raises(ValueError, match="kaboom"):
        safety.gate(
            agent_id="test-agent",
            tool_name="gh",
            action="add_label",
            target="priority:high",
            area="github",
            fn=boom,
        )

    row = audit_spy.calls[-1]
    assert row["outcome"] == "failure:ValueError"
    assert row["details"]["error"] == "kaboom"


# ---------------------------------------------------------------------------
# Audit is best-effort
# ---------------------------------------------------------------------------


def test_audit_swallows_backend_errors(
    audit_spy: _AuditSpy, caplog: pytest.LogCaptureFixture
) -> None:
    audit_spy.raise_with = RuntimeError("supabase down")
    # Should not raise, and fn-less classification should still return
    # its outcome so agents can carry on.
    result = safety.gate(
        agent_id="test-agent",
        tool_name="gh",
        action="add_label",
        target="priority:high",
        area="github",
    )
    assert result.tier == safety.Tier.AUTO


def test_audit_standalone_helper_best_effort(audit_spy: _AuditSpy) -> None:
    audit_spy.raise_with = RuntimeError("supabase down")
    # Must not raise.
    safety.audit(
        agent_id="test-agent",
        tool_name="gh",
        action="add_label",
        target="priority:high",
        tier=safety.Tier.AUTO,
        outcome="success",
        idempotency_key="abc",
    )
