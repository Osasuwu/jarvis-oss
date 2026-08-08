"""Unit tests for event emission at task completion (#953).

Tests cover:
- AC1: Event emission on terminal transitions (task_done, task_failed)
- AC2: PR-evidence check for fresh-shape and rework-shape goals
- AC3: Secondary evidence channel from executor stdout JSON
- AC4: Evidence checks via github_client (no gh CLI)
- AC5: Branch contract in task_dispatch
- AC6: Orchestrator routing table for all (event_type, pr_evidence, exit_confirmed) combos
- AC7: Re-drive mechanics with idempotency keys and MAX_ATTEMPTS
- AC8: No cron, no periodic scan — event-driven only
- AC10: Event-first ordering and dedup on re-observation
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from typing import Any
from unittest import mock

import pytest

from agents.github_client import (
    GitHubClient,
    HttpxGitHubClient,
    check_pr_closing_ref_fresh_shape,
    check_pr_evidence_fresh_shape,
    check_pr_evidence_rework_shape,
    parse_executor_stdout,
)
from agents.orchestrator import (
    Route,
    _as_bool,
    _attempt_of,
    _redrive_goal,
    handle_event,
)
from agents.task_dispatch import (
    TrackedProc,
    _augment_branch_directive,
    _augment_closes_mandate,
    _compute_pr_evidence,
    default_spawn,
    default_stdout_reader,
    format_lineage_key,
    parse_lineage,
    poll_completions,
)


# =============================================================================
# Fakes and Mocks
# =============================================================================


class _FakeProc:
    """A process handle whose ``poll()`` reports a fixed exit code (exited)."""

    def __init__(self, rc: int) -> None:
        self._rc = rc

    def poll(self) -> int:
        return self._rc


class _RecordingPort:
    """Minimal task-queue port stand-in that records transition order.

    ``poll_completions`` only ever calls ``transition`` on the port; the other
    Protocol methods are unused here, so they raise to catch accidental use.
    """

    def __init__(self, order_log: list[tuple[str, ...]]) -> None:
        self._order = order_log
        self.transitions: list[tuple[str, str, str | None]] = []

    def transition(
        self, task_id: str, to_status: str, *, reason: str | None = None
    ) -> dict[str, Any]:
        self._order.append(("transition", task_id, to_status))
        self.transitions.append((task_id, to_status, reason))
        return {"id": task_id, "status": to_status}


def _recording_emit(order_log: list[tuple[str, ...]], sink: list[dict[str, Any]]) -> Any:
    """Build an ``event_emit`` callback that records emit order and payloads."""

    def emit(
        event_type: str,
        severity: str,
        payload: dict[str, Any],
        *,
        dedup_key: str | None = None,
    ) -> dict[str, Any]:
        order_log.append(("event", event_type, dedup_key))
        row = {
            "event_type": event_type,
            "severity": severity,
            "payload": payload,
            "dedup_key": dedup_key,
        }
        sink.append(row)
        return row

    return emit


# =============================================================================
# AC2: PR-Evidence Check — Fresh Shape
# =============================================================================


class TestPREvidenceFreshShape:
    """Test PR-evidence check for fresh-shape goals (create your working branch as `task/<task_id>`)."""

    def test_pr_exists_with_task_branch_convention(self) -> None:
        """Fresh shape: PR exists with head branch `task/abc123`."""
        mock_client = mock.MagicMock()
        mock_client.get_pull_by_head_branch.return_value = {"id": 1, "number": 42}

        evidence = check_pr_evidence_fresh_shape(
            task_id="abc123",
            goal="implement feature X",
            spawned_at=datetime.now(UTC),
            client=mock_client,
        )
        assert evidence is True

    def test_pr_missing_task_branch_convention(self) -> None:
        """Fresh shape: no PR with head branch `task/abc123`."""
        mock_client = mock.MagicMock()
        mock_client.get_pull_by_head_branch.return_value = None

        evidence = check_pr_evidence_fresh_shape(
            task_id="abc123",
            goal="implement feature X",
            spawned_at=datetime.now(UTC),
            client=mock_client,
        )
        assert evidence is False

    def test_goal_with_explicit_branch_directive(self) -> None:
        """Fresh shape: goal carries explicit branch directive (non-convention)."""
        mock_client = mock.MagicMock()
        mock_client.get_pull_by_head_branch.return_value = {"id": 1, "number": 42}

        # Goal has explicit "branch=feature-xyz" directive
        evidence = check_pr_evidence_fresh_shape(
            task_id="abc123",
            goal="implement feature X (branch=feature-xyz)",
            spawned_at=datetime.now(UTC),
            client=mock_client,
        )
        # Should look up the explicit branch, not the convention
        mock_client.get_pull_by_head_branch.assert_called_with("feature-xyz")
        assert evidence is True

    def test_pr_created_after_spawn_is_evidence(self) -> None:
        """Fresh shape: PR on the branch created AFTER spawn → fresh evidence (True)."""
        mock_client = mock.MagicMock()
        spawned_at = datetime(2026, 6, 21, 10, 0, 0, tzinfo=UTC)
        mock_client.get_pull_by_head_branch.return_value = {
            "id": 1,
            "number": 42,
            "created_at": "2026-06-21T10:30:00Z",  # after spawn
        }
        evidence = check_pr_evidence_fresh_shape(
            task_id="abc123",
            goal="implement feature X",
            spawned_at=spawned_at,
            client=mock_client,
        )
        assert evidence is True

    def test_stale_pr_predating_spawn_is_not_evidence(self) -> None:
        """Fresh shape: a PR reusing the branch but created BEFORE spawn → not evidence (False).

        Guards the MAJOR finding (PR #1011): without the spawned_at gate a stale
        pre-existing PR on the same branch name would falsely read as evidence
        that *this* spawn produced work.
        """
        mock_client = mock.MagicMock()
        spawned_at = datetime(2026, 6, 21, 10, 0, 0, tzinfo=UTC)
        mock_client.get_pull_by_head_branch.return_value = {
            "id": 1,
            "number": 7,
            "created_at": "2026-06-20T09:00:00Z",  # predates spawn
        }
        evidence = check_pr_evidence_fresh_shape(
            task_id="abc123",
            goal="implement feature X",
            spawned_at=spawned_at,
            client=mock_client,
        )
        assert evidence is False

    def test_present_but_unparseable_created_at_escalates(self) -> None:
        """A PR whose ``created_at`` is present but garbage → None, not True (MEDIUM #1011).

        Freshness cannot be verified against a malformed timestamp, so the
        evidence is genuinely *unknown* and must escalate (tri-state None) rather
        than silently read as fresh evidence. This is distinct from an ABSENT
        ``created_at`` (next test), where the per-task-unique branch name is the
        fallback signal.
        """
        mock_client = mock.MagicMock()
        spawned_at = datetime(2026, 6, 21, 10, 0, 0, tzinfo=UTC)
        mock_client.get_pull_by_head_branch.return_value = {
            "id": 1,
            "number": 42,
            "created_at": "not-a-timestamp",  # present but unparseable
        }
        evidence = check_pr_evidence_fresh_shape(
            task_id="abc123",
            goal="implement feature X",
            spawned_at=spawned_at,
            client=mock_client,
        )
        assert evidence is None

    def test_absent_created_at_falls_back_to_branch_existence(self) -> None:
        """A PR with NO ``created_at`` field → branch existence is the fallback (True).

        Pins the deliberate asymmetry against the unparseable case above: an
        absent timestamp is not a freshness *failure*, it is a field the API
        simply did not return, so the per-task-unique branch match still stands
        as evidence.
        """
        mock_client = mock.MagicMock()
        spawned_at = datetime(2026, 6, 21, 10, 0, 0, tzinfo=UTC)
        mock_client.get_pull_by_head_branch.return_value = {
            "id": 1,
            "number": 42,
        }  # no created_at key
        evidence = check_pr_evidence_fresh_shape(
            task_id="abc123",
            goal="implement feature X",
            spawned_at=spawned_at,
            client=mock_client,
        )
        assert evidence is True


def _real_closing_ref_matcher() -> Any:
    """Load the /delegate gate's closing-ref regex factory (source of truth, #1136 AC4).

    ``check_pr_closing_ref_fresh_shape`` takes the matcher by injection so the
    HTTP client never path-loads a ``scripts/`` module. The test exercises the
    *real* regex (not a re-implementation) to stay in lockstep with the gate.
    """
    import importlib
    import sys
    from pathlib import Path

    scripts_dir = str(Path(__file__).resolve().parents[2] / "scripts")
    if scripts_dir not in sys.path:
        sys.path.insert(0, scripts_dir)
    return importlib.import_module("delegate_predispatch_gate")._closing_ref_re


class TestClosingRefChannel:
    """AC4 (#1136): a SEPARATE return channel reporting whether the fetched PR
    body carries a *closing* ref for the task's issue.

    Distinct from the freshness tri-state — this reuses the gate's
    ``_closing_ref_re`` (via injection) so ``Refs #N``-only bodies (which link
    without closing) report False, feeding the AC5 advisory WARNING.
    """

    def test_body_with_closes_keyword_reports_true(self) -> None:
        mock_client = mock.MagicMock()
        mock_client.get_pull_by_head_branch.return_value = {
            "number": 42,
            "body": "Some summary.\n\nCloses #1136\n",
        }
        result = check_pr_closing_ref_fresh_shape(
            task_id="abc123",
            goal="implement the guard for #1136",
            issue_number=1136,
            client=mock_client,
            closing_ref_matcher=_real_closing_ref_matcher(),
        )
        assert result is True

    def test_body_with_fixes_variant_reports_true(self) -> None:
        mock_client = mock.MagicMock()
        mock_client.get_pull_by_head_branch.return_value = {
            "number": 42,
            "body": "Fixes #1136",
        }
        result = check_pr_closing_ref_fresh_shape(
            task_id="abc123",
            goal="implement the guard for #1136",
            issue_number=1136,
            client=mock_client,
            closing_ref_matcher=_real_closing_ref_matcher(),
        )
        assert result is True

    def test_body_with_only_refs_reports_false(self) -> None:
        """``Refs #N`` links without closing → NOT a closing ref (drives the advisory)."""
        mock_client = mock.MagicMock()
        mock_client.get_pull_by_head_branch.return_value = {
            "number": 42,
            "body": "Refs #1136 — partial work, issue stays open.",
        }
        result = check_pr_closing_ref_fresh_shape(
            task_id="abc123",
            goal="implement the guard for #1136",
            issue_number=1136,
            client=mock_client,
            closing_ref_matcher=_real_closing_ref_matcher(),
        )
        assert result is False

    def test_body_without_any_ref_reports_false(self) -> None:
        mock_client = mock.MagicMock()
        mock_client.get_pull_by_head_branch.return_value = {
            "number": 42,
            "body": "No linkage keyword at all.",
        }
        result = check_pr_closing_ref_fresh_shape(
            task_id="abc123",
            goal="implement the guard for #1136",
            issue_number=1136,
            client=mock_client,
            closing_ref_matcher=_real_closing_ref_matcher(),
        )
        assert result is False

    def test_closing_ref_for_other_issue_reports_false(self) -> None:
        """A close of a *different* issue is not a close of this one."""
        mock_client = mock.MagicMock()
        mock_client.get_pull_by_head_branch.return_value = {
            "number": 42,
            "body": "Closes #999",
        }
        result = check_pr_closing_ref_fresh_shape(
            task_id="abc123",
            goal="implement the guard for #1136",
            issue_number=1136,
            client=mock_client,
            closing_ref_matcher=_real_closing_ref_matcher(),
        )
        assert result is False

    def test_no_pr_on_branch_reports_none(self) -> None:
        """No PR found → None (unknown), distinct from False (PR exists, no close)."""
        mock_client = mock.MagicMock()
        mock_client.get_pull_by_head_branch.return_value = None
        result = check_pr_closing_ref_fresh_shape(
            task_id="abc123",
            goal="implement the guard for #1136",
            issue_number=1136,
            client=mock_client,
            closing_ref_matcher=_real_closing_ref_matcher(),
        )
        assert result is None

    def test_explicit_branch_directive_is_honored(self) -> None:
        mock_client = mock.MagicMock()
        mock_client.get_pull_by_head_branch.return_value = {
            "number": 42,
            "body": "Closes #1136",
        }
        check_pr_closing_ref_fresh_shape(
            task_id="abc123",
            goal="implement feature X (branch=feat/1136-x)",
            issue_number=1136,
            client=mock_client,
            closing_ref_matcher=_real_closing_ref_matcher(),
        )
        mock_client.get_pull_by_head_branch.assert_called_with("feat/1136-x")

    def test_no_client_reports_none(self) -> None:
        result = check_pr_closing_ref_fresh_shape(
            task_id="abc123",
            goal="implement the guard for #1136",
            issue_number=1136,
            client=None,
            closing_ref_matcher=_real_closing_ref_matcher(),
        )
        assert result is None


# =============================================================================
# AC2: PR-Evidence Check — Rework Shape
# =============================================================================


class TestPREvidenceReworkShape:
    """Test PR-evidence check for rework-shape goals (continue on PR #N)."""

    def test_pr_has_activity_after_spawned_at(self) -> None:
        """Rework shape: PR #42 has activity (commits/updatedAt) after spawned_at."""
        mock_client = mock.MagicMock()
        spawned_at = datetime(2026, 6, 21, 10, 0, 0, tzinfo=UTC)
        # PR updated after spawn
        mock_client.get_pull_by_number.return_value = {
            "number": 42,
            "updated_at": "2026-06-21T11:00:00Z",  # 1 hour after spawn
            "head": {"sha": "abc123"},
        }
        mock_client.list_commits_for_pull.return_value = [
            {"sha": "abc123", "commit": {"author": {"date": "2026-06-21T10:30:00Z"}}}
        ]

        evidence = check_pr_evidence_rework_shape(
            task_id="abc123",
            goal="/rework #42",
            pr_number=42,
            spawned_at=spawned_at,
            client=mock_client,
        )
        assert evidence is True

    def test_pr_no_activity_after_spawned_at(self) -> None:
        """Rework shape: PR #42 unchanged since spawned_at."""
        mock_client = mock.MagicMock()
        spawned_at = datetime(2026, 6, 21, 10, 0, 0, tzinfo=UTC)
        # PR updated BEFORE spawn — no new activity
        mock_client.get_pull_by_number.return_value = {
            "number": 42,
            "updated_at": "2026-06-21T09:00:00Z",
        }
        mock_client.list_commits_for_pull.return_value = []

        evidence = check_pr_evidence_rework_shape(
            task_id="abc123",
            goal="/rework #42",
            pr_number=42,
            spawned_at=spawned_at,
            client=mock_client,
        )
        assert evidence is False

    def test_pr_not_found(self) -> None:
        """Rework shape: PR #42 does not exist."""
        mock_client = mock.MagicMock()
        mock_client.get_pull_by_number.return_value = None

        evidence = check_pr_evidence_rework_shape(
            task_id="abc123",
            goal="/rework #42",
            pr_number=42,
            spawned_at=datetime.now(UTC),
            client=mock_client,
        )
        assert evidence is False

    def test_commit_fetch_error_escalates_not_false(self) -> None:
        """Commit-listing failure ⇒ None (escalate), never False (MAJOR #1011 r2).

        ``updated_at`` is present but pre-spawn (no signal there), so the commit
        list is the only remaining evidence channel. If that fetch raises, we do
        NOT know whether new commits exist — returning False would be a confident
        "no activity ⇒ re-drive" verdict built on an unknown. Tri-state demands
        None so the orchestrator escalates instead of spuriously re-driving.
        """
        mock_client = mock.MagicMock()
        spawned_at = datetime(2026, 6, 21, 10, 0, 0, tzinfo=UTC)
        mock_client.get_pull_by_number.return_value = {
            "number": 42,
            "updated_at": "2026-06-21T09:00:00Z",  # pre-spawn → no signal
        }
        mock_client.list_commits_for_pull.side_effect = RuntimeError("network down")

        evidence = check_pr_evidence_rework_shape(
            task_id="abc123",
            goal="/rework #42",
            pr_number=42,
            spawned_at=spawned_at,
            client=mock_client,
        )
        assert evidence is None

    def test_freshness_anchors_on_committer_date_not_author_date(self) -> None:
        """Commit freshness reads ``committer.date``, not ``author.date`` (M2 #1029).

        A rebased/cherry-picked rework commit keeps the ORIGINAL author.date
        (pre-spawn) while the committer.date reflects the actual rework push
        (post-spawn). Anchoring on author.date would read that commit as stale
        and spuriously re-drive a PR that in fact has fresh rework activity.
        ``updated_at`` is pre-spawn so the commit date is the only signal.
        """
        mock_client = mock.MagicMock()
        spawned_at = datetime(2026, 6, 21, 10, 0, 0, tzinfo=UTC)
        mock_client.get_pull_by_number.return_value = {
            "number": 42,
            "updated_at": "2026-06-21T09:00:00Z",  # pre-spawn → no signal here
            "head": {"sha": "abc123"},
        }
        mock_client.list_commits_for_pull.return_value = [
            {
                "sha": "abc123",
                "commit": {
                    "author": {"date": "2026-06-20T08:00:00Z"},  # pre-spawn (original)
                    "committer": {"date": "2026-06-21T10:30:00Z"},  # post-spawn (rework)
                },
            }
        ]

        evidence = check_pr_evidence_rework_shape(
            task_id="abc123",
            goal="/rework #42",
            pr_number=42,
            spawned_at=spawned_at,
            client=mock_client,
        )
        assert evidence is True


class TestListCommitsForPullPagination:
    """``HttpxGitHubClient.list_commits_for_pull`` walks ALL pages (CRITICAL, PR #1011 r2).

    The PR commits endpoint returns commits OLDEST-first, paginated at
    per_page<=100, capped at 250 total (<=3 pages), and does NOT support a
    ``?since=`` filter. The round-1 "page 1 + last page" fix silently dropped
    every intermediate page — on a >200-commit PR the freshly-pushed commits in
    the middle pages vanished from the freshness gate. The only correct fix is
    to accumulate every page.
    """

    @staticmethod
    def _resp(body: object, status: int = 200) -> mock.MagicMock:
        r = mock.MagicMock()
        r.status_code = status
        r.json.return_value = body
        r.raise_for_status.return_value = None
        return r

    def _client_with(self, responses: list) -> "HttpxGitHubClient":
        client = HttpxGitHubClient("o/r", token="t")
        client._client = mock.MagicMock()
        client._client.get.side_effect = responses
        return client

    def test_walks_all_pages_and_accumulates(self) -> None:
        page1 = [{"sha": f"a{i}"} for i in range(100)]
        page2 = [{"sha": f"b{i}"} for i in range(100)]
        page3 = [{"sha": f"c{i}"} for i in range(50)]  # short → terminal page
        client = self._client_with([self._resp(page1), self._resp(page2), self._resp(page3)])
        commits = client.list_commits_for_pull(1)
        assert len(commits) == 250
        # Newest commit (last element, oldest-first ordering) MUST survive — the
        # round-1 bug kept only the final page; the page-2 commits were the ones
        # silently dropped.
        assert commits[-1]["sha"] == "c49"
        assert commits[100]["sha"] == "b0"  # page-2 content present
        pages = [c.kwargs["params"]["page"] for c in client._client.get.call_args_list]
        assert pages == [1, 2, 3]

    def test_single_short_page_stops_after_one_get(self) -> None:
        client = self._client_with([self._resp([{"sha": "x"}])])
        assert client.list_commits_for_pull(1) == [{"sha": "x"}]
        assert client._client.get.call_count == 1

    def test_full_page_then_empty_page_terminates(self) -> None:
        # Total a multiple of 100 → an empty trailing page must end the walk
        # without an extra request or an infinite loop.
        page1 = [{"sha": f"a{i}"} for i in range(100)]
        client = self._client_with([self._resp(page1), self._resp([])])
        commits = client.list_commits_for_pull(1)
        assert len(commits) == 100
        assert client._client.get.call_count == 2

    def test_404_on_first_page_returns_empty(self) -> None:
        client = self._client_with([self._resp(None, status=404)])
        assert client.list_commits_for_pull(1) == []
        assert client._client.get.call_count == 1


class TestGetPullByHeadBranch404Guard:
    """``get_pull_by_head_branch`` normalizes a 404 to ``None`` (L3, #1029).

    A branch that never had a PR (or a deleted branch) makes the pulls
    endpoint 404. That is "no evidence the work landed", not a transport
    error — the caller expects the same ``None`` sentinel a found-but-empty
    result yields, not a raised ``HTTPStatusError``.
    """

    @staticmethod
    def _resp(body: object, status: int = 200) -> mock.MagicMock:
        r = mock.MagicMock()
        r.status_code = status
        r.json.return_value = body
        # raise_for_status must actually raise on 4xx so a missing 404 guard
        # would surface as an error instead of silently passing this test.
        if status >= 400:
            r.raise_for_status.side_effect = RuntimeError(f"HTTP {status}")
        else:
            r.raise_for_status.return_value = None
        return r

    def _client_with(self, response: mock.MagicMock) -> HttpxGitHubClient:
        client = HttpxGitHubClient("o/r", token="t")
        client._client = mock.MagicMock()
        client._client.get.return_value = response
        return client

    def test_404_returns_none(self) -> None:
        client = self._client_with(self._resp(None, status=404))
        assert client.get_pull_by_head_branch("feat/x") is None

    def test_found_pr_returned(self) -> None:
        client = self._client_with(self._resp([{"number": 7}]))
        assert client.get_pull_by_head_branch("feat/x") == {"number": 7}

    def test_empty_list_returns_none(self) -> None:
        client = self._client_with(self._resp([]))
        assert client.get_pull_by_head_branch("feat/x") is None


class TestGitHubClientProtocolClose:
    """The ``GitHubClient`` Protocol declares ``close()`` (L2, #1029).

    Declaring it on the Protocol makes a type-checker flag any alternate
    implementation that forgets to release its pooled connections — the
    lifecycle contract lives on the interface, not just the one concrete impl.
    """

    def test_close_declared_on_protocol(self) -> None:
        assert hasattr(GitHubClient, "close")

    def test_concrete_client_close_is_idempotent(self) -> None:
        client = HttpxGitHubClient("o/r", token="t")
        client._client = mock.MagicMock()
        client.close()
        client.close()
        assert client._client.close.call_count == 2


# =============================================================================
# AC3: Secondary Evidence Channel — Executor Stdout Parsing
# =============================================================================


class TestExecutorStdoutParsing:
    """Test parsing executor stdout JSON for PR-evidence fallback."""

    def test_parse_executor_stdout_with_pr_url(self) -> None:
        """Executor stdout contains PR URL — extract and verify."""
        stdout_json = {
            "status": "completed",
            "pr_url": "https://github.com/Osasuwu/jarvis/pull/999",
            "message": "Opened PR #999",
        }
        pr_info = parse_executor_stdout(json.dumps(stdout_json))
        assert pr_info is not None
        assert pr_info["number"] == 999

    def test_parse_executor_stdout_missing_pr_url(self) -> None:
        """Executor stdout has no PR URL."""
        stdout_json = {
            "status": "completed",
            "message": "No PR was opened",
        }
        pr_info = parse_executor_stdout(json.dumps(stdout_json))
        assert pr_info is None

    def test_parse_executor_stdout_malformed(self) -> None:
        """Executor stdout is not valid JSON."""
        pr_info = parse_executor_stdout("garbage data")
        assert pr_info is None

    def test_parse_executor_stdout_claude_cli_envelope(self) -> None:
        """Real ``claude --output-format json`` envelope — the PR URL lives in
        the ``result`` text field, not a top-level key (MAJOR, PR #1011 round-4).

        ``spawn()`` adds ``--output-format json`` when a ``task_id`` is set; the
        CLI then emits ``{"type":"result","result":"<agent text>", ...}`` and the
        agent's PR URL is *inside* that text. Scanning only top-level
        ``pr_url``/``pull_request_url``/``url`` made the AC3 secondary channel
        dead in production while flat-JSON fixtures kept CI green.
        """
        envelope = {
            "type": "result",
            "subtype": "success",
            "result": "Done. Opened PR #888 at https://github.com/Osasuwu/jarvis/pull/888",
            "session_id": "abc",
            "usage": {"input_tokens": 10},
        }
        pr_info = parse_executor_stdout(json.dumps(envelope))
        assert pr_info is not None
        assert pr_info["number"] == 888

    def test_parse_executor_stdout_envelope_without_pull_link(self) -> None:
        """A result envelope whose text has no /pull/ link yields no PR number."""
        envelope = {"type": "result", "result": "Could not open a PR; tests failed."}
        assert parse_executor_stdout(json.dumps(envelope)) is None

    def test_parse_executor_stdout_non_dict_json(self) -> None:
        """Top-level JSON that is not an object (list/scalar) → None, no crash.

        ``data.get(...)`` would ``AttributeError`` on a list; the guard degrades
        to the best-effort ``None`` instead of raising into the poll loop.
        """
        assert parse_executor_stdout("[1, 2, 3]") is None
        assert parse_executor_stdout("42") is None


class TestStdoutReaderPathSafety:
    """``default_stdout_reader`` must not let a crafted task_id escape the log dir (LOW #1011)."""

    @pytest.mark.parametrize(
        "evil_id",
        [
            "../../etc/passwd",
            "..\\..\\windows\\system32\\config",
            "/etc/shadow",
            "foo/bar",
            "a/../../b",
            "with space",
            "semi;colon",
        ],
    )
    def test_traversal_task_id_returns_none_without_opening(self, evil_id: str) -> None:
        """A task_id containing path separators / traversal / unsafe chars → None, no open().

        ``task_id`` is interpolated into a filesystem path; a value carrying
        ``..`` or a separator could read an arbitrary file. The reader rejects
        anything outside the safe ``[A-Za-z0-9_-]`` charset and degrades to its
        best-effort ``None`` rather than touching the filesystem.
        """
        with mock.patch("builtins.open", side_effect=AssertionError("must not open")):
            assert default_stdout_reader(evil_id) is None

    @pytest.mark.parametrize("safe_id", ["t123", "abc123", "a1b2c3d4-e5f6-7890-abcd-ef0123456789"])
    def test_safe_task_id_is_accepted(self, safe_id: str) -> None:
        """A normal UUID or alnum id passes the guard and is read (returns None only if absent)."""
        # No such file exists in the test sandbox → OSError path → None, but the
        # guard did NOT short-circuit it (open was attempted). Assert via a spy.
        opened: list[str] = []
        real_open = open

        def spy_open(path: str, *a: Any, **k: Any) -> Any:
            opened.append(path)
            return real_open(path, *a, **k)

        with mock.patch("builtins.open", side_effect=spy_open):
            default_stdout_reader(safe_id)
        assert opened, f"safe id {safe_id!r} should have reached open()"

    def test_parse_executor_stdout_pull_request_url_field(self) -> None:
        """The ``pull_request_url`` field variant is recognised (MAJOR #1011).

        ``parse_executor_stdout`` scans three field names — ``pr_url``,
        ``pull_request_url``, ``url`` — but only the first was under test.
        An agent that writes ``pull_request_url`` must still be parsed, else
        the AC3 secondary channel silently misses real PRs.
        """
        stdout_json = {
            "status": "completed",
            "pull_request_url": "https://github.com/Osasuwu/jarvis/pull/777",
        }
        pr_info = parse_executor_stdout(json.dumps(stdout_json))
        assert pr_info is not None
        assert pr_info["number"] == 777

    def test_parse_executor_stdout_bare_url_field(self) -> None:
        """The generic ``url`` field variant is recognised (MAJOR #1011)."""
        stdout_json = {
            "status": "completed",
            "url": "https://github.com/Osasuwu/jarvis/pull/555",
        }
        pr_info = parse_executor_stdout(json.dumps(stdout_json))
        assert pr_info is not None
        assert pr_info["number"] == 555

    def test_parse_executor_stdout_non_pull_url_ignored(self) -> None:
        """A ``url`` that is not a /pull/ link yields no PR number (no false match)."""
        stdout_json = {
            "status": "completed",
            "url": "https://github.com/Osasuwu/jarvis/issues/123",
        }
        pr_info = parse_executor_stdout(json.dumps(stdout_json))
        assert pr_info is None


# =============================================================================
# AC3: Secondary Evidence Channel — _compute_pr_evidence integration
# =============================================================================


class TestComputePrEvidenceStdoutFallback:
    """The fresh-shape ``False`` → stdout-fallback → verified ``True`` path (MAJOR #1011).

    ``_compute_pr_evidence`` glues three pieces: the fresh-shape head-branch
    check, the AC3 stdout secondary channel, and the direct PR-number
    verification. Only the leaf parsers were under test; the glue itself —
    "head-branch lookup found nothing BUT the agent's stdout named a real PR" —
    had no coverage, so a regression that dropped the fallback would pass CI.
    """

    def test_head_branch_miss_then_stdout_pr_verified_is_true(self) -> None:
        spawned_at = datetime(2026, 6, 21, 10, 0, 0, tzinfo=UTC)
        client = mock.MagicMock()
        # Primary fresh-shape lookup misses (non-convention branch).
        client.get_pull_by_head_branch.return_value = None
        # The claimed PR number resolves to a real, existing PR.
        client.get_pull_by_number.return_value = {"number": 888, "state": "open"}

        def stdout_reader(task_id: str) -> str:
            return json.dumps({"status": "completed", "pr_url": "https://github.com/o/r/pull/888"})

        evidence, _ = _compute_pr_evidence(
            "abc123",
            "implement feature X",
            spawned_at,
            client=client,
            stdout_reader=stdout_reader,
        )
        assert evidence is True
        client.get_pull_by_number.assert_called_once_with(888)

    def test_head_branch_miss_and_stdout_pr_unverified_is_false(self) -> None:
        """Agent claimed a PR number, but the direct lookup finds nothing → stays False.

        The fallback verifies the claim against the API — a hallucinated PR
        number in stdout must NOT be trusted as evidence.
        """
        spawned_at = datetime(2026, 6, 21, 10, 0, 0, tzinfo=UTC)
        client = mock.MagicMock()
        client.get_pull_by_head_branch.return_value = None
        client.get_pull_by_number.return_value = None  # claimed PR does not exist

        def stdout_reader(task_id: str) -> str:
            return json.dumps({"pr_url": "https://github.com/o/r/pull/404"})

        evidence, _ = _compute_pr_evidence(
            "abc123",
            "implement feature X",
            spawned_at,
            client=client,
            stdout_reader=stdout_reader,
        )
        assert evidence is False

    def test_head_branch_miss_and_no_stdout_claim_is_false(self) -> None:
        """Fresh-shape miss with a stdout log that names no PR → False (no fallback fires)."""
        spawned_at = datetime(2026, 6, 21, 10, 0, 0, tzinfo=UTC)
        client = mock.MagicMock()
        client.get_pull_by_head_branch.return_value = None

        def stdout_reader(task_id: str) -> str:
            return json.dumps({"status": "completed", "message": "no PR opened"})

        evidence, _ = _compute_pr_evidence(
            "abc123",
            "implement feature X",
            spawned_at,
            client=client,
            stdout_reader=stdout_reader,
        )
        assert evidence is False
        client.get_pull_by_number.assert_not_called()


# =============================================================================
# AC5/AC6 (#1136): Closing-ref advisory at the evidence boundary
# =============================================================================


class TestClosingRefAdvisory:
    """``_compute_pr_evidence`` emits an advisory WARNING (no blocking, no edit)
    when a fresh-shape task naming an issue produces a PR whose body links but
    does not *close* that issue (#1136 AC5/AC6).

    Native auto-close is ``closingIssuesReferences``-driven and suppressed for
    bot/App merges (memory 21866efc), so a ``Refs #N``-only body silently leaves
    the issue open. The advisory surfaces that at the boundary; it does not
    change the returned evidence tri-state.
    """

    _WARN_TOKEN = "pr_closing_ref_missing"

    def _fresh_pr(self, body: str) -> dict[str, Any]:
        return {
            "number": 42,
            "created_at": "2026-06-21T10:30:00Z",  # after the spawn below
            "body": body,
        }

    def test_advisory_fires_for_refs_only_body(self, caplog: pytest.LogCaptureFixture) -> None:
        spawned_at = datetime(2026, 6, 21, 10, 0, 0, tzinfo=UTC)
        client = mock.MagicMock()
        client.get_pull_by_head_branch.return_value = self._fresh_pr("Refs #1136 — partial work.")
        with caplog.at_level(logging.WARNING, logger="agents.task_dispatch"):
            _compute_pr_evidence(
                "abc123",
                "implement the guard for #1136",
                spawned_at,
                client=client,
            )
        assert any(self._WARN_TOKEN in r.message for r in caplog.records), (
            "expected a closing-ref advisory for a Refs-only PR body"
        )

    def test_advisory_fires_for_no_ref_body(self, caplog: pytest.LogCaptureFixture) -> None:
        spawned_at = datetime(2026, 6, 21, 10, 0, 0, tzinfo=UTC)
        client = mock.MagicMock()
        client.get_pull_by_head_branch.return_value = self._fresh_pr("No linkage keyword.")
        with caplog.at_level(logging.WARNING, logger="agents.task_dispatch"):
            _compute_pr_evidence(
                "abc123",
                "implement the guard for #1136",
                spawned_at,
                client=client,
            )
        assert any(self._WARN_TOKEN in r.message for r in caplog.records), (
            "expected a closing-ref advisory for a PR body with no linkage"
        )

    def test_no_advisory_when_body_closes_issue(self, caplog: pytest.LogCaptureFixture) -> None:
        spawned_at = datetime(2026, 6, 21, 10, 0, 0, tzinfo=UTC)
        client = mock.MagicMock()
        client.get_pull_by_head_branch.return_value = self._fresh_pr("Closes #1136")
        with caplog.at_level(logging.WARNING, logger="agents.task_dispatch"):
            _compute_pr_evidence(
                "abc123",
                "implement the guard for #1136",
                spawned_at,
                client=client,
            )
        assert not any(self._WARN_TOKEN in r.message for r in caplog.records), (
            "a Closes #N body must not trigger the advisory"
        )

    def test_no_advisory_when_goal_names_no_issue(self, caplog: pytest.LogCaptureFixture) -> None:
        spawned_at = datetime(2026, 6, 21, 10, 0, 0, tzinfo=UTC)
        client = mock.MagicMock()
        client.get_pull_by_head_branch.return_value = self._fresh_pr("no ref")
        with caplog.at_level(logging.WARNING, logger="agents.task_dispatch"):
            _compute_pr_evidence(
                "abc123",
                "refactor the usage probe module",  # names no issue
                spawned_at,
                client=client,
            )
        assert not any(self._WARN_TOKEN in r.message for r in caplog.records), (
            "a goal naming no issue must not trigger the advisory"
        )

    def test_no_advisory_for_rework_shape(self, caplog: pytest.LogCaptureFixture) -> None:
        """Rework-shape tasks return before the fresh-path advisory (AC5 scope)."""
        spawned_at = datetime(2026, 6, 21, 10, 0, 0, tzinfo=UTC)
        client = mock.MagicMock()
        client.get_pull_by_number.return_value = {"number": 7, "state": "open"}
        client.list_commits_for_pull.return_value = []
        with caplog.at_level(logging.WARNING, logger="agents.task_dispatch"):
            _compute_pr_evidence(
                "abc123",
                "/rework #7 address review on #1136",
                spawned_at,
                client=client,
            )
        assert not any(self._WARN_TOKEN in r.message for r in caplog.records), (
            "rework-shape must not reach the fresh-path advisory"
        )


# =============================================================================
# AC6: Orchestrator Routing Table — task_done / task_failed Combinations
# =============================================================================


class TestOrchestratorRoutingTable:
    """Test routing table for all (event_type, pr_evidence, exit_confirmed) combos."""

    def test_task_done_pr_evidence_true(self) -> None:
        """task_done + pr_evidence=true → inline no-op (never re-drive, never escalate)."""
        event = {
            "event_type": "task_done",
            "severity": "info",
            "payload": {
                "task_id": "t123",
                "pr_evidence": True,
                "goal": "implement feature",
            },
        }
        decision = handle_event(event)
        assert decision.route == Route.HANDLE_INLINE
        assert decision.noop is True

    def test_task_failed_pr_evidence_true(self) -> None:
        """task_failed + pr_evidence=true → inline no-op (work landed; exit code lost)."""
        event = {
            "event_type": "task_failed",
            "severity": "info",
            "payload": {
                "task_id": "t123",
                "pr_evidence": True,
                "exit_confirmed": True,
                "goal": "implement feature",
            },
        }
        decision = handle_event(event)
        assert decision.route == Route.HANDLE_INLINE
        assert decision.noop is True

    def test_task_done_pr_evidence_false_attempt_1(self) -> None:
        """task_done + pr_evidence=false + attempt=1 → EMIT_TASK re-drive."""
        event = {
            "event_type": "task_done",
            "severity": "medium",
            "payload": {
                "task_id": "t123",
                "attempt": 1,
                "pr_evidence": False,
                "goal": "implement feature",
            },
        }
        decision = handle_event(event)
        assert decision.route == Route.EMIT_TASK
        assert "re-drive" in decision.goal.lower() or "retry" in decision.goal.lower()

    def test_task_failed_pr_evidence_false_confirmed_attempt_1(self) -> None:
        """task_failed + exit_confirmed=true + pr_evidence=false + attempt=1 → EMIT_TASK."""
        event = {
            "event_type": "task_failed",
            "severity": "medium",
            "payload": {
                "task_id": "t123",
                "attempt": 1,
                "exit_confirmed": True,
                "pr_evidence": False,
                "failure_reason": "exit 1",
                "goal": "implement feature",
            },
        }
        decision = handle_event(event)
        assert decision.route == Route.EMIT_TASK

    def test_task_done_pr_evidence_false_attempt_2_escalate(self) -> None:
        """task_done + pr_evidence=false + attempt≥2 → ESCALATE."""
        event = {
            "event_type": "task_done",
            "severity": "medium",
            "payload": {
                "task_id": "t123",
                "attempt": 2,
                "pr_evidence": False,
                "goal": "implement feature",
            },
        }
        decision = handle_event(event)
        assert decision.route == Route.ESCALATE
        assert decision.assignee == "owner"

    def test_task_failed_unconfirmed_exit_escalate(self) -> None:
        """task_failed + exit_confirmed=false → ESCALATE (never re-drive unconfirmed death)."""
        event = {
            "event_type": "task_failed",
            "severity": "medium",
            "payload": {
                "task_id": "t123",
                "exit_confirmed": False,
                "failure_reason": "reaped",
                "goal": "implement feature",
            },
        }
        decision = handle_event(event)
        assert decision.route == Route.ESCALATE
        assert decision.assignee == "owner"

    def test_task_failed_exit_confirmed_false_string_escalates(self) -> None:
        """``exit_confirmed="false"`` (a string) must ESCALATE, not re-drive (L4 #1029).

        Emitters that serialize the flag as text send the *string* ``"false"``,
        which is truthy under a bare ``if exit_confirmed:`` check — flipping an
        unconfirmed death into a confident re-drive. Normalizing at the emit
        boundary coerces it to the real bool ``False`` so the unconfirmed-exit
        branch fires and the event escalates to the owner.
        """
        event = {
            "event_type": "task_failed",
            "severity": "medium",
            "payload": {
                "task_id": "t123",
                "exit_confirmed": "false",  # string, not bool
                "pr_evidence": False,
                "failure_reason": "reaped",
                "goal": "implement feature",
            },
        }
        decision = handle_event(event)
        assert decision.route == Route.ESCALATE
        assert decision.assignee == "owner"

    def test_as_bool_coerces_flag_strings(self) -> None:
        """``_as_bool`` maps flag-like strings to strict bools (L4 #1029)."""
        assert _as_bool("false") is False
        assert _as_bool("False") is False
        assert _as_bool("0") is False
        assert _as_bool("") is False
        assert _as_bool("no") is False
        assert _as_bool("true") is True
        assert _as_bool("1") is True
        assert _as_bool(True) is True
        assert _as_bool(False) is False
        assert _as_bool(None) is False

    def test_task_failed_pr_evidence_null_escalate(self) -> None:
        """task_failed + confirmed exit + pr_evidence=null (unparseable goal) → ESCALATE.

        ``exit_confirmed=True`` is load-bearing: without it the event hits the
        unconfirmed-exit branch *before* the pr_evidence=null branch, so the test
        would assert ESCALATE for the wrong reason and silently stop covering the
        null-evidence path (MAJOR finding, PR #1011).
        """
        event = {
            "event_type": "task_failed",
            "severity": "medium",
            "payload": {
                "task_id": "t123",
                "exit_confirmed": True,
                "pr_evidence": None,  # unparseable goal
                "failure_reason": "exit 1",
                "goal": "some unparseable text",
            },
        }
        decision = handle_event(event)
        assert decision.route == Route.ESCALATE
        assert decision.escalated_reason is not None
        assert "pr_evidence=null" in decision.escalated_reason

    def test_task_done_non_bool_pr_evidence_escalates(self) -> None:
        """task_done + non-bool/non-None pr_evidence → ESCALATE naming the type fault.

        A malformed emitter sending ``pr_evidence="true"`` (string) or ``1`` must
        not fall through every ``is True / is False / is None`` arm to the generic
        Step-4 "no deterministic route" fail-safe — that reason misattributes a
        data-shape bug to an unknown event type (CRITICAL finding, PR #1011).
        """
        for bad in ("true", 1, {}, [], 0.0):
            event = {
                "event_type": "task_done",
                "severity": "medium",
                "payload": {
                    "task_id": "t123",
                    "attempt": 1,
                    "pr_evidence": bad,
                    "goal": "implement feature",
                },
            }
            decision = handle_event(event)
            assert decision.route == Route.ESCALATE, f"pr_evidence={bad!r}"
            assert decision.escalated_reason is not None
            assert "pr_evidence" in decision.escalated_reason
            assert "no deterministic route" not in decision.escalated_reason

    def test_task_failed_non_bool_pr_evidence_escalates(self) -> None:
        """task_failed + confirmed exit + non-bool/non-None pr_evidence → ESCALATE naming the fault."""
        for bad in ("false", 1, {}, [], 0.0):
            event = {
                "event_type": "task_failed",
                "severity": "medium",
                "payload": {
                    "task_id": "t123",
                    "attempt": 1,
                    "exit_confirmed": True,
                    "pr_evidence": bad,
                    "failure_reason": "exit 1",
                    "goal": "implement feature",
                },
            }
            decision = handle_event(event)
            assert decision.route == Route.ESCALATE, f"pr_evidence={bad!r}"
            assert decision.escalated_reason is not None
            assert "pr_evidence" in decision.escalated_reason
            assert "no deterministic route" not in decision.escalated_reason


# =============================================================================
# AC7: Re-drive Mechanics — Idempotency Keys and MAX_ATTEMPTS
# =============================================================================


class TestParseLineage:
    """``parse_lineage`` recovers the STABLE root key + outermost attempt (MAJOR #1011 r2).

    The greedy ``^(.*):r(\\d+)$`` round-1 regex split only the LAST ``:rN``,
    so a doubly-suffixed key (which the orchestrator can produce if it folds an
    already-suffixed key back into ``lineage_key``) kept the inner ``:rN`` in the
    root. Root keys must be identical across every attempt of one task, else
    dedup_key/MAX_ATTEMPTS lineage breaks.
    """

    def test_bare_key_is_root_attempt_1(self) -> None:
        assert parse_lineage("abc") == ("abc", 1)

    def test_empty_key_is_empty_root_attempt_1(self) -> None:
        assert parse_lineage("") == ("", 1)

    def test_single_suffix(self) -> None:
        assert parse_lineage("abc:r2") == ("abc", 2)

    def test_nested_suffix_strips_to_stable_root(self) -> None:
        # outermost attempt wins; root has ALL :rN peeled off.
        assert parse_lineage("abc:r2:r3") == ("abc", 3)

    def test_triple_nested_suffix(self) -> None:
        assert parse_lineage("task_done:abc123:r2:r3:r4") == ("task_done:abc123", 4)

    def test_root_with_colon_but_no_attempt_suffix(self) -> None:
        # a colon that is not a ``:rN`` attempt marker is part of the root.
        assert parse_lineage("task_done:abc123") == ("task_done:abc123", 1)

    def test_colon_root_with_single_attempt_suffix(self) -> None:
        # a colon-containing root with EXACTLY ONE ``:rN`` suffix: peel only the
        # outermost ``:r2`` and keep ``task_done:abc123`` intact — the colon in
        # the root must not be mistaken for an attempt separator (LOW #1011 r3;
        # the no-suffix and multi-suffix colon-root cases were covered, this
        # single-depth one was the gap).
        assert parse_lineage("task_done:abc123:r2") == ("task_done:abc123", 2)

    def test_format_lineage_key_round_trips_through_parse(self) -> None:
        """``format_lineage_key`` and ``parse_lineage`` share the separator (MEDIUM #1011).

        The builder is the single mint-point for re-drive keys; the parser is
        the single read-point. Both derive from ``_LINEAGE_SEP``, so a key built
        by one must parse back to its inputs through the other — this pins them
        symmetric so a future change to the separator can't desync the two.
        """
        for lineage, attempt in [("abc", 2), ("task_done:abc123", 3), ("t999", 1)]:
            built = format_lineage_key(lineage, attempt)
            assert built == f"{lineage}:r{attempt}"
            assert parse_lineage(built) == (lineage, attempt)


class TestRedriveIdempotency:
    """Test re-drive key structure and idempotency collision handling."""

    def test_redrive_key_structure(self) -> None:
        """Re-drive Decision carries idempotency key ``<lineage_key>:r<next_attempt>``.

        Drives the real ``handle_event`` re-drive branch (task_done + no PR
        evidence + attempt < MAX_ATTEMPTS) and asserts on the product key, not a
        re-derived string. ``lineage_key`` on the payload is what the key keys
        off — incrementing the attempt by one each re-drive.
        """
        event = {
            "event_type": "task_done",
            "severity": "medium",
            "payload": {
                "task_id": "t123",
                "lineage_key": "t123",
                "attempt": 1,
                "pr_evidence": False,
                "goal": "implement feature",
            },
        }
        decision = handle_event(event)
        assert decision.route == Route.EMIT_TASK
        # attempt 1 → next attempt 2 → key suffix :r2
        assert decision.idempotency_key == "t123:r2"

    def test_redrive_key_falls_back_to_task_id_without_lineage_key(self) -> None:
        """Older emitters omit ``lineage_key`` → re-drive keys off ``task_id``."""
        event = {
            "event_type": "task_failed",
            "severity": "medium",
            "payload": {
                "task_id": "t999",
                "attempt": 1,
                "exit_confirmed": True,
                "pr_evidence": False,
                "goal": "implement feature",
            },
        }
        decision = handle_event(event)
        assert decision.route == Route.EMIT_TASK
        assert decision.idempotency_key == "t999:r2"

    def test_explicit_attempt_zero_is_preserved_not_coerced(self) -> None:
        """An explicit ``attempt=0`` must NOT be coerced to 1 (MAJOR #1011 r2).

        The round-1 ``int(payload.get("attempt", 1) or 1)`` mapped a falsy-but-
        valid 0 to 1, silently advancing the attempt counter one step toward
        MAX_ATTEMPTS and mis-numbering the re-drive lineage. With attempt=0
        preserved, next_attempt is 1 → key suffix ``:r1`` (the bug produced
        ``:r2``).
        """
        event = {
            "event_type": "task_done",
            "severity": "medium",
            "payload": {
                "task_id": "t123",
                "lineage_key": "t123",
                "attempt": 0,
                "pr_evidence": False,
                "goal": "implement feature",
            },
        }
        decision = handle_event(event)
        assert decision.route == Route.EMIT_TASK
        assert decision.idempotency_key == "t123:r1"

    def test_missing_attempt_defaults_to_one(self) -> None:
        """Absent ``attempt`` still defaults to 1 → next attempt 2 → ``:r2``."""
        event = {
            "event_type": "task_done",
            "severity": "medium",
            "payload": {
                "task_id": "t123",
                "lineage_key": "t123",
                "pr_evidence": False,
                "goal": "implement feature",
            },
        }
        decision = handle_event(event)
        assert decision.route == Route.EMIT_TASK
        assert decision.idempotency_key == "t123:r2"

    def test_malformed_attempt_falls_back_to_zero(self) -> None:
        """A non-int ``attempt`` (malformed payload) falls back to 0, not a crash (M1 #1029).

        ``int("abc")`` raises ``ValueError``; without the guard the whole event
        handler would blow up on a garbage attempt suffix instead of degrading
        to the root attempt. attempt→0 ⇒ next_attempt 1 ⇒ key suffix ``:r1``.
        """
        event = {
            "event_type": "task_done",
            "severity": "medium",
            "payload": {
                "task_id": "t123",
                "lineage_key": "t123",
                "attempt": "abc",  # non-int → guard returns 0
                "pr_evidence": False,
                "goal": "implement feature",
            },
        }
        decision = handle_event(event)
        assert decision.route == Route.EMIT_TASK
        assert decision.idempotency_key == "t123:r1"

    def test_attempt_of_guards_non_int(self) -> None:
        """``_attempt_of`` returns 0 on a non-int suffix, 1 on absent (M1 #1029)."""
        assert _attempt_of({"attempt": "abc"}) == 0
        assert _attempt_of({"attempt": None}) == 1
        assert _attempt_of({}) == 1
        assert _attempt_of({"attempt": 0}) == 0
        assert _attempt_of({"attempt": 3}) == 3
        assert _attempt_of({"attempt": "2"}) == 2

    def test_max_attempts_exhausted(self) -> None:
        """Attempt ≥ 2 (MAX_ATTEMPTS=2) and pr_evidence=false → escalate, no re-drive."""
        event = {
            "event_type": "task_done",
            "severity": "medium",
            "payload": {
                "task_id": "t123",
                "attempt": 2,
                "pr_evidence": False,
                "goal": "implement feature",
            },
        }
        decision = handle_event(event)
        # Should escalate, not re-drive (attempt >= MAX_ATTEMPTS)
        assert decision.route == Route.ESCALATE


class TestRedriveGoalShape:
    """``_redrive_goal`` augments fresh goals with a branch pin but never rework goals."""

    def test_rework_goal_has_no_branch_directive(self) -> None:
        """A rework re-drive re-runs ``/rework #N`` with NO ``(branch=...)`` pin (AC7).

        The rework's evidence is *new activity on the existing PR #N*, not a
        fresh branch — embedding ``(branch=task/<id>)`` would point the next
        evidence check at a branch that does not exist for a rework. The previous
        round dropped this branch entirely, so the goal text had no coverage.
        """
        goal = _redrive_goal("/rework #42", "abc123", 2)
        assert goal == "Re-drive (attempt 2): /rework #42"
        assert "(branch=" not in goal

    def test_fresh_goal_embeds_root_task_branch(self) -> None:
        """A fresh re-drive pins ``(branch=task/<root_task_id>)`` so evidence looks right.

        The re-driven task never creates its own ``task/<new_id>`` branch; the
        terminal-boundary evidence check looks at the ROOT task's branch, so the
        pin must name ``root_task_id``, not the new attempt's id.
        """
        goal = _redrive_goal("implement feature X", "abc123", 2)
        assert goal.startswith("Re-drive (attempt 2): implement feature X")
        assert "(branch=task/abc123)" in goal

    def test_fresh_goal_with_explicit_branch_not_re_pinned(self) -> None:
        """A fresh goal already naming a branch is not given a second pin."""
        goal = _redrive_goal("implement X (branch=custom)", "abc123", 2)
        assert goal.count("(branch=") == 1
        assert "(branch=custom)" in goal
        assert "task/abc123" not in goal

    def test_rework_redrive_decision_goal_carries_no_branch(self) -> None:
        """End-to-end: a rework re-drive Decision's goal carries no branch pin.

        Drives the real ``handle_event`` re-drive branch with a ``/rework #42``
        goal so the regression is locked at the Decision boundary, not just the
        helper — this is what the executor actually receives.
        """
        event = {
            "event_type": "task_done",
            "severity": "medium",
            "payload": {
                "task_id": "t123",
                "lineage_key": "t123",
                "attempt": 1,
                "pr_evidence": False,
                "goal": "/rework #42",
            },
        }
        decision = handle_event(event)
        assert decision.route == Route.EMIT_TASK
        assert decision.goal == "Re-drive (attempt 2): /rework #42"
        assert "(branch=" not in decision.goal


# =============================================================================
# AC10: Event Emission — Event-First Ordering and Dedup
# =============================================================================


class TestEventEmission:
    """Test event-first emission with dedup on re-observation."""

    def test_event_emitted_before_transition(self) -> None:
        """``poll_completions`` emits the terminal event BEFORE the FSM transition.

        Event-first ordering is the crash-safety contract: if the driver dies in
        the window, re-observation re-emits and the ``dedup_key`` absorbs the
        duplicate. Verified on a real ``poll_completions`` call by recording the
        interleaving of emit vs transition into one ordered log.
        """
        order: list[tuple[str, ...]] = []
        emitted: list[dict[str, Any]] = []
        port = _RecordingPort(order)
        procs = {
            "t123": TrackedProc(
                proc=_FakeProc(0),
                started_at=0.0,
                goal="implement feature X",
                idempotency_key="t123",
                spawned_at=datetime.now(UTC),
            )
        }
        client = mock.MagicMock()
        client.get_pull_by_head_branch.return_value = {"id": 1, "number": 42}

        result = poll_completions(
            port,
            procs,
            event_emit=_recording_emit(order, emitted),
            evidence_client=client,
        )

        assert result.done == 1
        # exactly one event then one transition, event first
        assert order == [
            ("event", "task_done", "task_done:t123:a1"),
            ("transition", "t123", "done"),
        ]

    def test_dedup_key_collision_idempotent(self) -> None:
        """Re-observation of the same terminal task emits an identical dedup_key.

        The key is ``<event_type>:<task_id>:a<attempt>`` — derived from the
        task's identity and attempt, not from observation time — so two
        independent ``poll_completions`` passes over the same exited task produce
        byte-identical keys. The DB unique index then collapses the duplicate.
        """
        client = mock.MagicMock()
        client.get_pull_by_head_branch.return_value = None  # no PR → pr_evidence False

        def observe() -> str | None:
            emitted: list[dict[str, Any]] = []
            procs = {
                "t123": TrackedProc(
                    proc=_FakeProc(0),
                    started_at=0.0,
                    goal="implement feature X",
                    idempotency_key="t123",  # root → attempt 1
                    spawned_at=datetime.now(UTC),
                )
            }
            poll_completions(
                _RecordingPort([]),
                procs,
                event_emit=_recording_emit([], emitted),
                evidence_client=client,
            )
            return emitted[0]["dedup_key"]

        first = observe()
        second = observe()
        assert first == "task_done:t123:a1"
        assert first == second

    def test_emit_failure_still_transitions(self) -> None:
        """A raising ``event_emit`` must NOT block the FSM transition (MAJOR #1011).

        Emit and transition are decoupled: a dropped event self-heals on
        re-observation (the dedup_key absorbs it), but a transition skipped
        because emit raised leaves the row stuck in ``running`` until the 6h
        reaper. So a Supabase-down / network-blip emit failure has to fall
        through to ``transition(done)`` and still count the completion.
        """
        order: list[tuple[str, ...]] = []
        port = _RecordingPort(order)
        procs = {
            "t123": TrackedProc(
                proc=_FakeProc(0),
                started_at=0.0,
                goal="implement feature X",
                idempotency_key="t123",
                spawned_at=datetime.now(UTC),
            )
        }
        client = mock.MagicMock()
        client.get_pull_by_head_branch.return_value = {"id": 1, "number": 42}

        def exploding_emit(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
            order.append(("event", "raised", None))
            raise RuntimeError("supabase down")

        result = poll_completions(
            port,
            procs,
            event_emit=exploding_emit,
            evidence_client=client,
        )

        # The completion still counts and the transition still fired.
        assert result.done == 1
        assert port.transitions == [("t123", "done", None)]
        # Emit was attempted (and raised) before the transition fired.
        assert order == [
            ("event", "raised", None),
            ("transition", "t123", "done"),
        ]

    def test_spawned_at_none_yields_null_evidence(self) -> None:
        """A proc with no ``spawned_at`` (adopted after restart) emits null pr_evidence.

        ``_compute_pr_evidence`` returns ``None`` when ``spawned_at`` is absent —
        there is no spawn boundary to date PR activity against, so evidence is
        genuinely unknown (tri-state), not ``False``. The orchestrator routes a
        null on a re-observed completion to escalation, never to a blind re-drive.
        """
        emitted: list[dict[str, Any]] = []
        procs = {
            "t123": TrackedProc(
                proc=_FakeProc(0),
                started_at=0.0,
                goal="implement feature X",
                idempotency_key="t123",
                spawned_at=None,
            )
        }
        client = mock.MagicMock()
        # Even with a client wired, a missing spawn boundary short-circuits to None.
        client.get_pull_by_head_branch.return_value = {"id": 1, "number": 42}

        result = poll_completions(
            _RecordingPort([]),
            procs,
            event_emit=_recording_emit([], emitted),
            evidence_client=client,
        )

        assert result.done == 1
        assert emitted[0]["payload"]["pr_evidence"] is None
        # No branch lookup happened — the None short-circuit precedes the query.
        client.get_pull_by_head_branch.assert_not_called()


# =============================================================================
# AC5: Branch Contract — Goal Augmentation in task_dispatch
# =============================================================================


class TestBranchContract:
    """Test branch directive in goal for fresh-shape tasks."""

    def test_fresh_shape_augmented_goal_convention(self) -> None:
        """Real ``_augment_branch_directive`` pins a fresh-shape goal to ``task/<task_id>``."""
        augmented = _augment_branch_directive("implement feature X", "abc123")
        assert augmented != "implement feature X"  # it WAS augmented
        assert "(branch=task/abc123)" in augmented

    def test_fresh_shape_with_explicit_branch_not_re_augmented(self) -> None:
        """A goal already naming a branch is left exactly as the author wrote it (AC5)."""
        goal = "implement feature X (branch=feature-xyz)"
        assert _augment_branch_directive(goal, "abc123") == goal

    def test_rework_shape_no_augmentation(self) -> None:
        """Real ``_augment_branch_directive`` never touches a rework-shape goal (AC5)."""
        goal = "/rework #42"
        # Rework targets an existing PR's branch — augmenting it would be wrong.
        assert _augment_branch_directive(goal, "abc123") == goal


# =============================================================================
# #1136 AC1/AC3: Executor-Lane Closes #N Mandate — Goal Augmentation
# =============================================================================


class TestClosesMandate:
    """`_augment_closes_mandate` injects a `Closes #N` PR-body mandate (#1136).

    The executor lane's spawned ``claude -p`` sessions may run
    ``Bash(gh pr create:*)`` with no directive to link the issue their PR
    closes — so a merged PR can silently fail to auto-close its issue (#948).
    A fresh-shape goal naming issue #N gets an explicit mandate appended.
    """

    def test_fresh_shape_with_issue_gets_closes_mandate(self) -> None:
        """Fresh goal naming #N → mandate appended carrying `Closes #<N>` (AC1)."""
        goal = "implement the executor guard for #1136"
        augmented = _augment_closes_mandate(goal, "abc123")
        assert augmented != goal  # it WAS augmented
        assert augmented.startswith(goal)  # additive — original goal preserved verbatim
        assert "Closes #1136" in augmented

    def test_closes_mandate_permits_refs_escape(self) -> None:
        """The mandate offers the partial-work `Refs #<N>` escape (AC3)."""
        goal = "implement feature for #1136"
        augmented = _augment_closes_mandate(goal, "abc123")
        assert "Refs #1136" in augmented

    def test_rework_shape_no_closes_mandate(self) -> None:
        """Rework goal (`/rework #N`) targets an existing PR → never augmented (AC1)."""
        goal = "/rework #42"
        assert _augment_closes_mandate(goal, "abc123") == goal

    def test_fresh_shape_without_issue_no_mandate(self) -> None:
        """Fresh goal with no `#N` has no close target → unchanged (AC1)."""
        goal = "refactor the usage probe module"
        assert _augment_closes_mandate(goal, "abc123") == goal

    def test_empty_goal_no_mandate(self) -> None:
        """Empty/whitespace goal → unchanged (AC1)."""
        assert _augment_closes_mandate("", "abc123") == ""
        assert _augment_closes_mandate("   ", "abc123") == "   "

    def test_default_spawn_composes_branch_and_closes_mandate(
        self, tmp_path: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """`default_spawn` applies BOTH augmenters before the executor hand-off (AC1)."""
        import subprocess

        import agents.executor as executor_mod
        import agents.task_dispatch as task_dispatch_mod

        repo = tmp_path / "repo"
        repo.mkdir()
        subprocess.run(["git", "init", "-q", "--initial-branch=main"], cwd=repo, check=True)
        (repo / "README.md").write_text("stub\n", encoding="utf-8")
        subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
        subprocess.run(
            [
                "git",
                "-c",
                "user.email=t@t",
                "-c",
                "user.name=t",
                "-c",
                "commit.gpgsign=false",
                "commit",
                "-qm",
                "init",
            ],
            cwd=repo,
            check=True,
        )
        monkeypatch.setattr(task_dispatch_mod, "_REPO_ROOT", str(repo))

        captured: dict[str, Any] = {}

        def fake_spawn(goal: str, *, task_id: str | None = None, cwd: str | None = None) -> Any:
            captured["goal"] = goal
            captured["task_id"] = task_id
            captured["cwd"] = cwd
            return object()

        monkeypatch.setattr(executor_mod, "spawn", fake_spawn)

        default_spawn("implement the guard for #1136", task_id="t1")

        assert "(branch=task/t1)" in captured["goal"]  # branch directive applied
        assert "Closes #1136" in captured["goal"]  # closes mandate applied
        assert captured["task_id"] == "t1"


# =============================================================================
# Integration: Event Emission in wake_driver.tick()
# =============================================================================


class TestEventEmissionInTick:
    """Integration test: events are emitted during completion polling in tick()."""

    def test_completion_poll_emits_task_done_event(self) -> None:
        """A clean exit emits ``task_done`` carrying the COMPUTED pr_evidence.

        Full integration through real ``poll_completions``: a fresh-shape goal
        whose injected evidence client finds the PR yields ``pr_evidence=True``
        (computed at the boundary, not handed in), plus the lineage/attempt
        parsed from the idempotency key and the AC1 dedup_key.
        """
        emitted: list[dict[str, Any]] = []
        procs = {
            "abc123": TrackedProc(
                proc=_FakeProc(0),
                started_at=0.0,
                goal="implement feature X",
                idempotency_key="abc123:r2",  # lineage abc123, attempt 2
                spawned_at=datetime.now(UTC),
            )
        }
        client = mock.MagicMock()
        client.get_pull_by_head_branch.return_value = {"id": 1, "number": 7}

        result = poll_completions(
            _RecordingPort([]),
            procs,
            event_emit=_recording_emit([], emitted),
            evidence_client=client,
        )

        assert result.done == 1
        assert len(emitted) == 1
        event = emitted[0]
        assert event["event_type"] == "task_done"
        # PR evidence was COMPUTED from the injected client, not passed in.
        assert event["payload"]["pr_evidence"] is True
        assert event["payload"]["lineage_key"] == "abc123"
        assert event["payload"]["attempt"] == 2
        assert event["dedup_key"] == "task_done:abc123:a2"
        # The evidence check used the task/<task_id> convention branch.
        client.get_pull_by_head_branch.assert_called_with("task/abc123")

    def test_completion_poll_emits_task_failed_on_nonzero_exit(self) -> None:
        """A non-zero exit emits ``task_failed`` with exit_confirmed + exit_code."""
        emitted: list[dict[str, Any]] = []
        procs = {
            "abc123": TrackedProc(
                proc=_FakeProc(1),
                started_at=0.0,
                goal="implement feature X",
                idempotency_key="abc123",
                spawned_at=datetime.now(UTC),
            )
        }
        client = mock.MagicMock()
        client.get_pull_by_head_branch.return_value = None  # no PR → pr_evidence False

        result = poll_completions(
            _RecordingPort([]),
            procs,
            event_emit=_recording_emit([], emitted),
            evidence_client=client,
        )

        assert result.failed_exit == 1
        event = emitted[0]
        assert event["event_type"] == "task_failed"
        assert event["payload"]["exit_confirmed"] is True
        assert event["payload"]["exit_code"] == 1
        assert event["payload"]["pr_evidence"] is False


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
