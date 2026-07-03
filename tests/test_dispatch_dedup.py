"""Tests for dispatch-dedup (issue #931).

Covers the shared in-flight predicate ``check_in_flight`` in
``scripts/delegate_predispatch_gate.py``, the strict-envelope CLI verdicts
(OK / REFUSE / SKIP, fail-closed), and the pre-spawn dedup in
``agents/task_dispatch.drain_tasks``. All fixtures are network-free.
"""

from __future__ import annotations

import importlib
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
gate = importlib.import_module("delegate_predispatch_gate")
check_in_flight = gate.check_in_flight


# ── Fixture builders ─────────────────────────────────────────────────────────


def _pr(number: int = 100, body: str = "", head: str = "task/abc123") -> dict:
    return {"number": number, "body": body, "headRefName": head}


# ── check_in_flight: live-PR via closing keyword ─────────────────────────────


def test_closing_keyword_in_pr_body_hits():
    result = check_in_flight(931, [_pr(body="Closes #931 for good")], [])
    assert result.verdict == "live_pr"
    assert "#100" in result.pointer


def test_all_closing_keyword_variants_hit():
    for kw in ("Closes", "closed", "close", "Fixes", "fix", "fixed", "Resolves", "resolve", "resolved"):
        result = check_in_flight(42, [_pr(body=f"{kw} #42")], [])
        assert result.verdict == "live_pr", kw


def test_issue_ref_without_closing_keyword_does_not_hit():
    result = check_in_flight(931, [_pr(body="relates to #931, see discussion")], [])
    assert result.verdict == "clear"


def test_number_is_right_anchored_93_does_not_match_931():
    # PR closing #931 must NOT read as in-flight evidence for issue #93 ...
    result = check_in_flight(93, [_pr(body="Closes #931")], [])
    assert result.verdict == "clear"


def test_number_is_right_anchored_931_not_matched_by_9310():
    result = check_in_flight(931, [_pr(body="Closes #9310")], [])
    assert result.verdict == "clear"


# ── check_in_flight: live-PR via head branch ─────────────────────────────────


def test_pr_head_branch_prefix_hits():
    result = check_in_flight(931, [_pr(head="feat/931-dispatch-dedup")], [])
    assert result.verdict == "live_pr"


def test_pr_head_branch_any_lowercase_prefix_hits():
    result = check_in_flight(931, [_pr(head="fix/931-hotfix")], [])
    assert result.verdict == "live_pr"


def test_pr_head_branch_wrong_number_does_not_hit():
    result = check_in_flight(931, [_pr(head="feat/9310-other")], [])
    assert result.verdict == "clear"


def test_pr_head_branch_needs_trailing_dash():
    result = check_in_flight(931, [_pr(head="feat/931")], [])
    assert result.verdict == "clear"


# ── check_in_flight: stale branch (no open PR) ───────────────────────────────


def test_branch_without_pr_is_stale_branch():
    result = check_in_flight(931, [], ["feat/931-dispatch-dedup"])
    assert result.verdict == "stale_branch"
    assert "feat/931-dispatch-dedup" in result.pointer


def test_branch_for_other_issue_is_clear():
    result = check_in_flight(931, [], ["feat/930-other", "feat/9310-other", "main"])
    assert result.verdict == "clear"


def test_non_feat_branch_does_not_count_as_claim():
    result = check_in_flight(931, [], ["task/931-something"])
    assert result.verdict == "clear"


# ── check_in_flight: precedence & message distinction ────────────────────────


def test_live_pr_beats_stale_branch():
    result = check_in_flight(
        931,
        [_pr(number=200, body="Closes #931")],
        ["feat/931-dispatch-dedup"],
    )
    assert result.verdict == "live_pr"
    assert "#200" in result.pointer


def test_stale_branch_and_live_pr_pointers_are_distinct():
    live = check_in_flight(931, [_pr(body="Closes #931")], [])
    stale = check_in_flight(931, [], ["feat/931-x"])
    assert live.pointer != stale.pointer


def test_clear_when_nothing_matches():
    result = check_in_flight(931, [_pr(body="Closes #42", head="feat/42-x")], ["feat/42-x"])
    assert result.verdict == "clear"
    assert result.clear


# ── module purity: no network I/O imports ────────────────────────────────────


def test_module_has_no_network_imports():
    source = Path(gate.__file__).read_text(encoding="utf-8")
    for lib in ("httpx", "requests", "urllib", "socket", "http.client", "subprocess"):
        assert f"import {lib}" not in source, f"network/exec import {lib!r} found in gate module"


# ── CLI: strict envelope, exit 0/1/2 ─────────────────────────────────────────

VALID_UUID = "6b0a5bf7-8ca9-47cc-81cf-ebae39c81d08"
READY_BODY = f"## Acceptance criteria\n- [ ] do thing\n\nDecisions: {VALID_UUID}\n"


class _StringStream:
    def __init__(self, payload: str) -> None:
        self._payload = payload

    def read(self) -> str:
        return self._payload


def _envelope(
    body: str = READY_BODY,
    labels: tuple[str, ...] = ("sandcastle",),
    number: int = 931,
    open_prs: list | None = None,
    open_branches: list | None = None,
) -> dict:
    return {
        "issue": {
            "number": number,
            "body": body,
            "labels": [{"name": n} for n in labels],
        },
        "open_prs": open_prs if open_prs is not None else [],
        "open_branches": open_branches if open_branches is not None else [],
    }


def _run_main(monkeypatch, payload: str) -> int:
    import json as _json  # noqa: F401 — payload already serialized by callers

    monkeypatch.setattr("sys.stdin", _StringStream(payload))
    return gate.main([])


def test_cli_exit_zero_on_ready_and_clear(monkeypatch, capsys):
    import json

    rc = _run_main(monkeypatch, json.dumps(_envelope()))
    assert rc == 0
    assert "OK" in capsys.readouterr().out


def test_cli_exit_one_on_readiness_refuse(monkeypatch, capsys):
    import json

    rc = _run_main(monkeypatch, json.dumps(_envelope(body="", labels=())))
    assert rc == 1
    out = capsys.readouterr().out
    assert "REFUSE" in out
    assert "sandcastle" in out


def test_cli_exit_two_on_live_pr(monkeypatch, capsys):
    import json

    rc = _run_main(
        monkeypatch,
        json.dumps(_envelope(open_prs=[_pr(number=555, body="Closes #931")])),
    )
    assert rc == 2
    out = capsys.readouterr().out
    assert out.startswith("SKIP")
    assert "#555" in out


def test_cli_exit_two_on_stale_branch_with_distinct_message(monkeypatch, capsys):
    import json

    rc = _run_main(
        monkeypatch,
        json.dumps(_envelope(open_branches=["feat/931-dispatch-dedup"])),
    )
    assert rc == 2
    out = capsys.readouterr().out
    assert out.startswith("SKIP")
    assert "feat/931-dispatch-dedup" in out
    assert "owner attention" in out


def test_cli_refuse_wins_over_in_flight(monkeypatch, capsys):
    import json

    payload = _envelope(body="", labels=(), open_prs=[_pr(body="Closes #931")])
    rc = _run_main(monkeypatch, json.dumps(payload))
    assert rc == 1
    assert "REFUSE" in capsys.readouterr().out


# ── CLI: fail-closed on malformed payload ────────────────────────────────────


def _assert_skip(monkeypatch, capsys, payload: str):
    rc = _run_main(monkeypatch, payload)
    assert rc == 2
    assert capsys.readouterr().out.startswith("SKIP")


def test_cli_fails_closed_on_missing_open_prs(monkeypatch, capsys):
    import json

    env = _envelope()
    del env["open_prs"]
    _assert_skip(monkeypatch, capsys, json.dumps(env))


def test_cli_fails_closed_on_missing_open_branches(monkeypatch, capsys):
    import json

    env = _envelope()
    del env["open_branches"]
    _assert_skip(monkeypatch, capsys, json.dumps(env))


def test_cli_fails_closed_on_non_list_open_prs(monkeypatch, capsys):
    import json

    env = _envelope()
    env["open_prs"] = "not-a-list"
    _assert_skip(monkeypatch, capsys, json.dumps(env))


def test_cli_fails_closed_on_non_dict_pr_entry(monkeypatch, capsys):
    import json

    _assert_skip(monkeypatch, capsys, json.dumps(_envelope(open_prs=["oops"])))


def test_cli_fails_closed_on_non_string_branch_entry(monkeypatch, capsys):
    import json

    _assert_skip(monkeypatch, capsys, json.dumps(_envelope(open_branches=[42])))


def test_cli_fails_closed_on_bare_issue_legacy_shape(monkeypatch, capsys):
    import json

    bare = {"number": 931, "body": READY_BODY, "labels": [{"name": "sandcastle"}]}
    _assert_skip(monkeypatch, capsys, json.dumps(bare))


def test_cli_fails_closed_on_invalid_json(monkeypatch, capsys):
    _assert_skip(monkeypatch, capsys, "{not json")


def test_cli_fails_closed_on_missing_issue_number(monkeypatch, capsys):
    import json

    env = _envelope()
    del env["issue"]["number"]
    _assert_skip(monkeypatch, capsys, json.dumps(env))


# ── drain_tasks pre-spawn dedup (agents/task_dispatch.py, #931) ──────────────

from agents.task_dispatch import DedupConfig, drain_tasks  # noqa: E402


class _Queue:
    """Minimal TaskQueuePort fake — mirrors tests/test_agents_task_dispatch.py."""

    def __init__(self, pending: list[dict]) -> None:
        self.pending = list(pending)
        self.transitions: list[tuple[str, str, str | None]] = []
        self.requeued: list[str] = []

    def claim_next(self, *, assignee: str) -> dict | None:
        return self.pending.pop(0) if self.pending else None

    def count_running(self, *, assignee: str) -> int:
        return 0

    def transition(self, task_id: str, to_status: str, *, reason: str | None = None) -> dict:
        self.transitions.append((task_id, to_status, reason))
        return {"id": task_id, "status": to_status}

    def reclaim_stale_claimed(self, *, assignee: str, older_than_seconds: float) -> int:
        return 0

    def list_stale_running(self, *, assignee: str, older_than_seconds: float) -> list[dict]:
        return []

    def requeue_running(self, task_id: str) -> bool:
        self.requeued.append(task_id)
        return True


def _task(task_id: str, goal: str) -> dict:
    return {"id": task_id, "goal": goal, "assignee": "sandcastle", "status": "pending"}


class _Spawned:
    """Healthy executor.SpawnResult stand-in: a proc was launched."""

    def __init__(self) -> None:
        self.proc = object()
        self.throttled = False


def _healthy_usage():
    class _Reading:
        near_exhaustion = False

    return _Reading()


def _drain(q: _Queue, dedup: DedupConfig | None):
    return drain_tasks(
        q,
        lambda goal, task_id=None: _Spawned(),
        cap=5,
        resolve_binary=lambda: "claude",
        read_usage=_healthy_usage,
        dedup=dedup,
    )


def test_drain_live_pr_skips_as_duplicate_and_records_outcome():
    q = _Queue([_task("t1", "Implement #931 dispatch dedup")])
    outcomes: list[dict] = []
    cfg = DedupConfig(
        fetch_in_flight=lambda: ([_pr(number=777, body="Closes #931")], []),
        list_active_rows=lambda: [],
        record_outcome=outcomes.append,
    )
    res = _drain(q, cfg)
    assert res.spawned == 0
    assert res.skipped_duplicate == 1
    skip = [(t, r) for t, s, r in q.transitions if s == "skipped_duplicate"]
    assert skip == [("t1", skip[0][1])]
    assert "#777" in skip[0][1]
    assert len(outcomes) == 1


def test_drain_live_sibling_row_skips_as_duplicate():
    q = _Queue([_task("t1", "Implement #931 dispatch dedup")])
    cfg = DedupConfig(
        fetch_in_flight=lambda: ([], []),
        list_active_rows=lambda: [
            {"id": "other", "goal": "Fix #931 from another angle", "status": "running"}
        ],
    )
    res = _drain(q, cfg)
    assert res.spawned == 0
    assert res.skipped_duplicate == 1
    reasons = [r for _, s, r in q.transitions if s == "skipped_duplicate"]
    assert reasons and "#931" in reasons[0]


def test_drain_own_row_is_not_a_sibling():
    q = _Queue([_task("t1", "Implement #931 dispatch dedup")])
    cfg = DedupConfig(
        fetch_in_flight=lambda: ([], []),
        list_active_rows=lambda: [
            {"id": "t1", "goal": "Implement #931 dispatch dedup", "status": "running"}
        ],
    )
    res = _drain(q, cfg)
    assert res.spawned == 1
    assert res.skipped_duplicate == 0


def test_drain_stale_branch_parks_for_owner_attention():
    q = _Queue([_task("t1", "Implement #931 dispatch dedup")])
    cfg = DedupConfig(
        fetch_in_flight=lambda: ([], ["feat/931-dispatch-dedup"]),
        list_active_rows=lambda: [],
    )
    res = _drain(q, cfg)
    assert res.spawned == 0
    parked = [(t, r) for t, s, r in q.transitions if s == "parked"]
    assert parked and parked[0][0] == "t1"
    assert "owner attention" in parked[0][1]


def test_drain_fetch_failure_requeues_and_stops():
    def boom() -> tuple[list, list]:
        raise RuntimeError("gh api down")

    q = _Queue([_task("t1", "Implement #931 x"), _task("t2", "Implement #932 y")])
    cfg = DedupConfig(fetch_in_flight=boom, list_active_rows=lambda: [])
    res = _drain(q, cfg)
    # Unverifiable is never terminal: the row goes back to pending ...
    assert q.requeued == ["t1"]
    assert res.spawned == 0
    assert res.skipped_duplicate == 0
    # ... and the drain stops instead of hammering a down API per task.
    assert q.pending and q.pending[0]["id"] == "t2"


def test_drain_fetches_github_evidence_once_per_drain():
    calls = {"n": 0}

    def fetch() -> tuple[list, list]:
        calls["n"] += 1
        return ([], [])

    q = _Queue([_task("t1", "Implement #931 x"), _task("t2", "Implement #932 y")])
    cfg = DedupConfig(fetch_in_flight=fetch, list_active_rows=lambda: [])
    res = _drain(q, cfg)
    assert res.spawned == 2
    assert calls["n"] == 1


def test_drain_rework_goal_bypasses_dedup():
    # A rework goal points at a live PR by design — dedup must not eat it.
    fetched = {"n": 0}

    def fetch() -> tuple[list, list]:
        fetched["n"] += 1
        return ([_pr(body="Closes #931")], [])

    q = _Queue([_task("t1", "/rework #931")])
    cfg = DedupConfig(fetch_in_flight=fetch, list_active_rows=lambda: [])
    res = _drain(q, cfg)
    assert res.spawned == 1
    assert fetched["n"] == 0


def test_drain_goal_without_issue_ref_proceeds():
    q = _Queue([_task("t1", "chore: tidy the docs")])
    cfg = DedupConfig(
        fetch_in_flight=lambda: ([_pr(body="Closes #1")], []),
        list_active_rows=lambda: [],
    )
    res = _drain(q, cfg)
    assert res.spawned == 1
    assert res.skipped_duplicate == 0


def test_drain_without_dedup_config_is_unchanged():
    q = _Queue([_task("t1", "Implement #931 x")])
    res = _drain(q, None)
    assert res.spawned == 1
    assert res.skipped_duplicate == 0
    assert [s for _, s, _ in q.transitions] == ["running"]


def test_drain_record_outcome_failure_does_not_break_the_skip():
    def bad_outcome(payload: dict) -> None:
        raise RuntimeError("supabase down")

    q = _Queue([_task("t1", "Implement #931 x"), _task("t2", "Implement #932 y")])
    cfg = DedupConfig(
        fetch_in_flight=lambda: ([_pr(body="Closes #931")], []),
        list_active_rows=lambda: [],
        record_outcome=bad_outcome,
    )
    res = _drain(q, cfg)
    assert res.skipped_duplicate == 1
    assert res.spawned == 1  # t2 still spawned — the drain continued


# ── HttpxGitHubClient.list_open_pulls / list_branch_names (production fetch) ──
#
# These back the drain-time evidence fetch (cycle 5). They mirror the pooled-
# client / paginated-GET conventions of the sibling evidence methods and the
# MagicMock side_effect test pattern in tests/test_agents_953_event_emission.py.

from unittest import mock  # noqa: E402

from agents.github_client import HttpxGitHubClient  # noqa: E402
from agents.task_dispatch import default_task_dedup  # noqa: E402


def _resp(body, status: int = 200):
    r = mock.MagicMock()
    r.status_code = status
    r.json.return_value = body
    r.raise_for_status.return_value = None
    return r


def _client_with(responses):
    c = HttpxGitHubClient("Osasuwu/jarvis", token="x")
    c._client = mock.MagicMock()
    c._client.get.side_effect = responses
    return c


def test_list_open_pulls_maps_rest_shape_to_gate_keys():
    # REST returns head.ref; check_in_flight expects headRefName.
    c = _client_with(
        [
            _resp(
                [
                    {"number": 12, "body": "Closes #931", "head": {"ref": "feat/931-x"}},
                    {"number": 13, "body": "unrelated", "head": {"ref": "chore/y"}},
                ]
            ),
            _resp([]),
        ]
    )
    pulls = c.list_open_pulls()
    assert pulls == [
        {"number": 12, "body": "Closes #931", "headRefName": "feat/931-x"},
        {"number": 13, "body": "unrelated", "headRefName": "chore/y"},
    ]
    # state=open must be requested.
    assert c._client.get.call_args_list[0].kwargs["params"]["state"] == "open"


def test_list_open_pulls_paginates_until_short_page():
    page1 = [{"number": n, "body": "", "head": {"ref": f"b/{n}"}} for n in range(100)]
    page2 = [{"number": 100, "body": "", "head": {"ref": "b/100"}}]
    c = _client_with([_resp(page1), _resp(page2)])
    pulls = c.list_open_pulls()
    assert len(pulls) == 101
    pages = [call.kwargs["params"]["page"] for call in c._client.get.call_args_list]
    assert pages == [1, 2]


def test_list_branch_names_paginates_and_extracts_names():
    page1 = [{"name": f"feat/{n}"} for n in range(100)]
    page2 = [{"name": "main"}]
    c = _client_with([_resp(page1), _resp(page2)])
    names = c.list_branch_names()
    assert names[0] == "feat/0"
    assert names[-1] == "main"
    assert len(names) == 101
    pages = [call.kwargs["params"]["page"] for call in c._client.get.call_args_list]
    assert pages == [1, 2]


# ── default_task_dedup factory ───────────────────────────────────────────────


def test_default_task_dedup_wires_github_and_active_rows():
    gh = mock.MagicMock()
    gh.list_open_pulls.return_value = [_pr(body="Closes #931")]
    gh.list_branch_names.return_value = ["feat/931-x"]
    cfg = default_task_dedup(gh, list_active=lambda: [{"id": "r1", "goal": "Implement #931"}])
    prs, branches = cfg.fetch_in_flight()
    assert prs == [_pr(body="Closes #931")]
    assert branches == ["feat/931-x"]
    assert cfg.list_active_rows() == [{"id": "r1", "goal": "Implement #931"}]
    assert callable(cfg.record_outcome)
