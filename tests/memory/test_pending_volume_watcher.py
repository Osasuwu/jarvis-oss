"""Unit tests for ``scripts/pending-volume-watcher.py``.

Tests the hysteresis logic and debounce behavior with a mocked Supabase
client. See ``test_dreamer.py`` for same-pattern mock chain construction.
"""

from __future__ import annotations

import importlib.util
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock

_SCRIPT_PATH = Path(__file__).resolve().parent.parent.parent / "scripts" / "pending-volume-watcher.py"
_spec = importlib.util.spec_from_file_location("pending_volume_watcher", _SCRIPT_PATH)
assert _spec and _spec.loader
watcher = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(watcher)

FROZEN_NOW = datetime(2026, 5, 24, 12, 0, 0, tzinfo=timezone.utc)


def _mock_pending_count(client: MagicMock, count: int):
    """Set up the client stub to return ``count`` pending items."""
    exec_resp = MagicMock()
    exec_resp.count = count

    execute_mock = MagicMock()
    execute_mock.execute.return_value = exec_resp

    is_superseded_mock = MagicMock()
    is_superseded_mock.is_.return_value = execute_mock

    is_deleted_mock = MagicMock()
    is_deleted_mock.is_.return_value = is_superseded_mock

    eq_mock = MagicMock()
    eq_mock.eq.return_value = is_deleted_mock

    select_mock = MagicMock()
    select_mock.select.return_value = eq_mock

    client.table.return_value = select_mock


def _mock_events(client: MagicMock, events_by_type: dict[str, list[dict]]):
    """Set up the client stub to return event rows keyed by event_type.

    Each key maps to the rows that queries with
    ``.eq("event_type", key)`` should return.  Unknown event_types get an
    empty result.  Call this *after* ``_mock_pending_count`` so the memories
    mock (stored in ``client.table.return_value``) is already in place.
    """

    def _make_chain(rows: list[dict]) -> MagicMock:
        exec_resp = MagicMock()
        exec_resp.data = rows
        limit_mock = MagicMock()
        limit_mock.limit.return_value.execute.return_value = exec_resp
        order_mock = MagicMock()
        order_mock.order.return_value = limit_mock
        return order_mock

    def _eq_side_effect(field: str, value):
        if field == "event_type" and value in events_by_type:
            return _make_chain(events_by_type[value])
        return _make_chain([])

    eq_mock = MagicMock()
    eq_mock.eq.side_effect = _eq_side_effect

    select_mock = MagicMock()
    select_mock.select.return_value = eq_mock

    def _table_side_effect(name: str):
        if name == "memories":
            return client.table.return_value
        elif name == "events":
            return select_mock
        return MagicMock()

    client.table.side_effect = _table_side_effect


class TestHysteresis:
    """Tests the fire/re-arm hysteresis logic."""

    def test_below_threshold_no_action(self):
        client = MagicMock()
        _mock_pending_count(client, 5)
        _mock_events(client, {"candidates_pending": []})

        result = watcher.check_and_fire(client, dry_run=True)
        assert result["action"] == "none"
        assert "pending=5 < fire=10" in result["reason"]

    def test_above_threshold_fires(self):
        client = MagicMock()
        _mock_pending_count(client, 12)
        _mock_events(client, {"candidates_pending": []})

        result = watcher.check_and_fire(client, dry_run=True)
        assert result["action"] == "would_fire"
        assert result["pending_count"] == 12

    def test_already_fired_no_rearm_skips(self):
        """Still in fired state and count >= rearm threshold → skip."""
        client = MagicMock()
        _mock_pending_count(client, 10)
        _mock_events(
            client,
            {
                "candidates_pending": [
                    {
                        "created_at": "2026-05-20T10:00:00Z",
                        "payload": {"state": "fired", "pending_count": 15},
                    }
                ]
            },
        )

        result = watcher.check_and_fire(client, dry_run=True)
        assert result["action"] == "none"
        assert "already fired" in result["reason"]

    def test_rearmed_then_crosses_threshold_fires_again(self):
        """After re-arm (<8), crossing >=10 fires again."""
        client = MagicMock()
        _mock_pending_count(client, 12)
        _mock_events(
            client,
            {
                "candidates_pending": [
                    {
                        "created_at": "2026-05-19T10:00:00Z",
                        "payload": {"state": "rearmed", "pending_count": 7},
                    }
                ]
            },
        )

        result = watcher.check_and_fire(client, dry_run=True)
        assert result["action"] == "would_fire"
        assert result["pending_count"] == 12

    def test_pending_below_fire_with_prior_fired_no_action(self):
        """pending=9 + prior fired → 'none' via the below-fire early-exit (step 4).

        Note: this does NOT exercise the step-5 hysteresis guard — pending < FIRE
        returns early at step 4. The reason-string assertion pins the path so a
        reorder of steps 4 and 5 cannot pass silently.
        See test_hysteresis_guard_blocks_for_already_fired for the step-5 case.
        """
        client = MagicMock()
        _mock_pending_count(client, 9)
        _mock_events(
            client,
            {
                "candidates_pending": [
                    {
                        "created_at": "2026-05-20T10:00:00Z",
                        "payload": {"state": "fired", "pending_count": 15},
                    }
                ]
            },
        )

        result = watcher.check_and_fire(client, dry_run=True)
        assert result["action"] == "none"
        assert "pending=9 < fire=10" in result["reason"]

    def test_hysteresis_guard_blocks_for_already_fired(self):
        """pending well above FIRE + prior fired → step-5 hysteresis guard fires."""
        client = MagicMock()
        _mock_pending_count(client, 15)  # well above FIRE_THRESHOLD=10
        _mock_events(
            client,
            {
                "candidates_pending": [
                    {
                        "created_at": "2026-05-24T10:00:00Z",
                        "payload": {"state": "fired", "pending_count": 12},
                    }
                ],
                "learn_run": [],
            },
        )

        result = watcher.check_and_fire(client, dry_run=True, _now=lambda: FROZEN_NOW)
        assert result["action"] == "none"
        assert "already fired" in result["reason"]
        assert "hysteresis band" in result["reason"]

    def test_missing_state_in_payload_fails_open(self):
        """A `candidates_pending` event without 'state' in payload must not lock the guard.

        Regression: round-2 default of 'fired' caused a permanent hysteresis lock
        whenever a payload was missing the state key (manually inserted, alt
        emitter, future schema). Default must be 'rearmed' (fail open).
        """
        client = MagicMock()
        _mock_pending_count(client, 12)
        _mock_events(
            client,
            {
                "candidates_pending": [
                    {
                        "created_at": "2026-05-20T10:00:00Z",
                        "payload": {"pending_count": 12},  # state missing
                    }
                ],
                "learn_run": [],
            },
        )

        result = watcher.check_and_fire(client, dry_run=True, _now=lambda: FROZEN_NOW)
        assert result["action"] == "would_fire"

    def test_fire_drop_rerise_refires(self):
        """fire → drop below REARM_THRESHOLD → rise above FIRE_THRESHOLD → re-fires."""
        # Phase 1: no prior event, pending=12 → fires
        c1 = MagicMock()
        _mock_pending_count(c1, 12)
        _mock_events(c1, {"candidates_pending": [], "learn_run": []})
        r1 = watcher.check_and_fire(c1, dry_run=True, _now=lambda: FROZEN_NOW)
        assert r1["action"] == "would_fire"

        # Phase 2: pending=6, last state=fired → re-arms, returns none (below FIRE)
        c2 = MagicMock()
        _mock_pending_count(c2, 6)
        _mock_events(
            c2,
            {
                "candidates_pending": [
                    {
                        "created_at": "2026-05-24T10:00:00Z",
                        "payload": {"state": "fired", "pending_count": 12},
                    }
                ],
                "learn_run": [],
            },
        )
        r2 = watcher.check_and_fire(c2, dry_run=True, _now=lambda: FROZEN_NOW)
        assert r2["action"] == "none"
        assert r2.get("rearmed") is True

        # Phase 3: pending=11, last state=rearmed → fires again
        c3 = MagicMock()
        _mock_pending_count(c3, 11)
        _mock_events(
            c3,
            {
                "candidates_pending": [
                    {
                        "created_at": "2026-05-24T11:00:00Z",
                        "payload": {"state": "rearmed", "pending_count": 6},
                    }
                ],
                "learn_run": [],
            },
        )
        r3 = watcher.check_and_fire(c3, dry_run=True, _now=lambda: FROZEN_NOW)
        assert r3["action"] == "would_fire"


class TestDebounce:
    """Tests the 24h debounce after /learn runs."""

    def test_recent_learn_skips_fire(self):
        client = MagicMock()
        _mock_pending_count(client, 15)
        _mock_events(
            client,
            {
                "candidates_pending": [
                    {
                        "created_at": "2026-05-23T12:00:00Z",
                        "payload": {"state": "rearmed", "pending_count": 5},
                    }
                ],
                "learn_run": [
                    {"created_at": "2026-05-24T11:00:00Z"},  # 1h before FROZEN_NOW
                ],
            },
        )

        result = watcher.check_and_fire(client, dry_run=True, _now=lambda: FROZEN_NOW)
        assert result["action"] == "none"
        assert "debounce" in result["reason"]

    def test_old_learn_allows_fire(self):
        client = MagicMock()
        _mock_pending_count(client, 15)
        _mock_events(
            client,
            {
                "candidates_pending": [
                    {
                        "created_at": "2026-05-22T12:00:00Z",
                        "payload": {"state": "rearmed", "pending_count": 5},
                    }
                ],
                "learn_run": [
                    {"created_at": "2026-05-22T12:00:00Z"},  # 48h before FROZEN_NOW
                ],
            },
        )

        result = watcher.check_and_fire(client, dry_run=True, _now=lambda: FROZEN_NOW)
        assert result["action"] == "would_fire"

    def test_second_invocation_within_debounce_suppressed(self):
        """After watcher fires, re-invocation within 24h is suppressed via learn_run marker."""
        client = MagicMock()
        _mock_pending_count(client, 15)
        _mock_events(
            client,
            {
                "candidates_pending": [
                    {"created_at": "2026-05-24T11:55:00Z", "payload": {"state": "rearmed"}}
                ],
                "learn_run": [
                    {"created_at": "2026-05-24T11:59:00Z"},  # 1 min before FROZEN_NOW
                ],
            },
        )

        result = watcher.check_and_fire(client, dry_run=True, _now=lambda: FROZEN_NOW)
        assert result["action"] == "none"
        assert "debounce" in result["reason"]


class TestEmitLearnRunGuard:
    """Tests the guard: emit_learn_run must not fire when emit_event fails (#2)."""

    def test_no_debounce_marker_when_emit_event_fails(self):
        """emit_learn_run must NOT be called when emit_event returns None (DB error)."""
        from unittest.mock import patch

        client = MagicMock()
        _mock_pending_count(client, 15)
        _mock_events(client, {"candidates_pending": [], "learn_run": []})

        with (
            patch.object(watcher, "emit_event", return_value=None),
            patch.object(watcher, "emit_learn_run") as mock_learn_run,
        ):
            result = watcher.check_and_fire(client, dry_run=False)

        mock_learn_run.assert_not_called()
        assert result.get("event_id") is None

    def test_debounce_marker_written_when_emit_event_succeeds(self):
        """emit_learn_run IS called when emit_event returns a valid event_id."""
        from unittest.mock import patch

        client = MagicMock()
        _mock_pending_count(client, 15)
        _mock_events(client, {"candidates_pending": [], "learn_run": []})

        with (
            patch.object(watcher, "emit_event", return_value="event-uuid-123"),
            patch.object(watcher, "emit_learn_run", return_value=True) as mock_learn_run,
        ):
            result = watcher.check_and_fire(client, dry_run=False)

        mock_learn_run.assert_called_once_with(client)
        assert result["action"] == "fired"
        assert result["debounce_marker_written"] is True

    def test_debounce_marker_written_when_emit_event_returns_no_id_sentinel(self):
        """emit_learn_run IS called even when emit_event returns the NO_ID_SENTINEL.

        Regression: round-2 conflated 'INSERT succeeded with empty resp.data'
        (PostgREST return=minimal, RLS hiding the row) with 'INSERT failed',
        so the debounce marker was never written and the watcher double-fired
        on the next queue oscillation.
        """
        from unittest.mock import patch

        client = MagicMock()
        _mock_pending_count(client, 15)
        _mock_events(client, {"candidates_pending": [], "learn_run": []})

        with (
            patch.object(watcher, "emit_event", return_value=watcher.NO_ID_SENTINEL),
            patch.object(watcher, "emit_learn_run", return_value=True) as mock_learn_run,
        ):
            result = watcher.check_and_fire(client, dry_run=False)

        mock_learn_run.assert_called_once_with(client)
        assert result["event_id"] == watcher.NO_ID_SENTINEL
        assert result["debounce_marker_written"] is True

    def test_emit_learn_run_failure_surfaced_in_result(self):
        """emit_learn_run insert failure (returns False) must appear in result.

        Regression: round-2 swallowed the exception and returned action='fired'
        with no signal that the debounce marker was lost — the watcher would
        double-fire on the next oscillation without any visible error.
        """
        from unittest.mock import patch

        client = MagicMock()
        _mock_pending_count(client, 15)
        _mock_events(client, {"candidates_pending": [], "learn_run": []})

        with (
            patch.object(watcher, "emit_event", return_value="event-uuid-123"),
            patch.object(watcher, "emit_learn_run", return_value=False),
        ):
            result = watcher.check_and_fire(client, dry_run=False)

        assert result["debounce_marker_written"] is False


class TestLearnRunContract:
    """Verify SKILL.md closes the /learn → learn_run event loop (#1)."""

    def test_skill_md_contains_learn_run_emit_instruction(self):
        """SKILL.md must instruct the skill to emit a learn_run event."""
        skill_path = (
            Path(__file__).resolve().parent.parent.parent
            / ".claude-userlevel"
            / "skills"
            / "learn"
            / "SKILL.md"
        )
        content = skill_path.read_text(encoding="utf-8")
        assert "learn_run" in content, "SKILL.md must instruct emitting learn_run event"
        assert "learn_skill" in content, "SKILL.md must identify the source as learn_skill"


class TestDryRun:
    def test_dry_run_no_event_emitted(self):
        client = MagicMock()
        _mock_pending_count(client, 15)
        _mock_events(client, {"candidates_pending": []})

        result = watcher.check_and_fire(client, dry_run=True)
        assert result["action"] == "would_fire"
        assert result["dry_run"] is True
