"""Unit tests for scripts/pull-rate-report.py (#1275 telemetry AC group).

Numerator: transcript ``Read``/``Grep`` tool_use rows targeting the Glossary
source (``CONTEXT.md``), discovered via a recursive glob so subagent
transcripts (stored as separate files under
``<session_id>/subagents/agent-*.jsonl``) are included.

Denominator: rows from ``.claude/logs/session-context-size.jsonl`` (written by
``_self_log`` in session-context.py), joined on ``(session_id, project)`` and
windowed by ``ts`` per that function's docstring — a resumed session emits
several rows sharing the same key, so each row's window is
``[ts, next_row_ts)`` for that same key, not the whole session's lifetime.

Same import scaffolding convention as test_session_context_smoke.py, but this
module has no optional-dep imports of its own so no stubbing is needed.
"""

from __future__ import annotations

import importlib.util
import json
from datetime import datetime, timezone
from pathlib import Path

_PATH = Path(__file__).resolve().parent.parent.parent / "scripts" / "pull-rate-report.py"
_spec = importlib.util.spec_from_file_location("pull_rate_report", _PATH)
prr = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(prr)


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row) + "\n")


def _assistant_tool_use(name: str, tool_input: dict, ts: str) -> dict:
    return {
        "type": "assistant",
        "timestamp": ts,
        "message": {"content": [{"type": "tool_use", "name": name, "input": tool_input}]},
    }


# --- _targets_glossary_source ------------------------------------------------


def test_targets_glossary_source_matches_read_of_context_md() -> None:
    assert prr._targets_glossary_source("Read", {"file_path": "/repo/CONTEXT.md"})


def test_targets_glossary_source_matches_grep_of_context_md() -> None:
    """Grep is how the index is meant to be used, not just Read (AC)."""
    assert prr._targets_glossary_source("Grep", {"path": "/repo/CONTEXT.md"})


def test_targets_glossary_source_case_insensitive() -> None:
    assert prr._targets_glossary_source("Read", {"file_path": "/repo/context.md"})


def test_targets_glossary_source_rejects_other_tools() -> None:
    assert not prr._targets_glossary_source("Write", {"file_path": "/repo/CONTEXT.md"})
    assert not prr._targets_glossary_source("Bash", {"command": "cat CONTEXT.md"})


def test_targets_glossary_source_rejects_other_files() -> None:
    assert not prr._targets_glossary_source("Read", {"file_path": "/repo/CLAUDE.md"})
    assert not prr._targets_glossary_source("Grep", {"path": "/repo/README.md"})


# --- discover_transcripts_for_session (recursive glob incl. subagents) ------


def test_discover_transcripts_finds_main_transcript(tmp_path: Path) -> None:
    session_id = "sess-abc"
    main = tmp_path / "proj1" / f"{session_id}.jsonl"
    main.parent.mkdir(parents=True)
    main.write_text("")

    found = prr.discover_transcripts_for_session(tmp_path, session_id)

    assert main in found


def test_discover_transcripts_finds_subagent_transcripts_recursively(tmp_path: Path) -> None:
    """Subagent transcripts live at <session_id>/subagents/agent-*.jsonl, not
    as isSidechain rows in the parent file — the reader must glob for them."""
    session_id = "sess-abc"
    main = tmp_path / "proj1" / f"{session_id}.jsonl"
    main.parent.mkdir(parents=True)
    main.write_text("")
    sub = tmp_path / "proj1" / session_id / "subagents" / "agent-xyz.jsonl"
    sub.parent.mkdir(parents=True)
    sub.write_text("")

    found = prr.discover_transcripts_for_session(tmp_path, session_id)

    assert main in found
    assert sub in found


def test_discover_transcripts_empty_for_unknown_session(tmp_path: Path) -> None:
    assert prr.discover_transcripts_for_session(tmp_path, "no-such-session") == []


def test_discover_transcripts_empty_for_missing_session_id(tmp_path: Path) -> None:
    assert prr.discover_transcripts_for_session(tmp_path, "") == []
    assert prr.discover_transcripts_for_session(tmp_path, None) == []


# --- _iter_glossary_pulls ----------------------------------------------------


def test_iter_glossary_pulls_yields_timestamp_for_matching_read(tmp_path: Path) -> None:
    path = tmp_path / "t.jsonl"
    _write_jsonl(
        path,
        [
            _assistant_tool_use(
                "Read", {"file_path": "/repo/CONTEXT.md"}, "2026-08-12T08:00:00.000Z"
            )
        ],
    )

    pulls = list(prr._iter_glossary_pulls(path))

    assert len(pulls) == 1
    assert pulls[0] == datetime(2026, 8, 12, 8, 0, 0, tzinfo=timezone.utc)


def test_iter_glossary_pulls_ignores_non_matching_tool_use(tmp_path: Path) -> None:
    path = tmp_path / "t.jsonl"
    _write_jsonl(
        path,
        [
            _assistant_tool_use(
                "Read", {"file_path": "/repo/CLAUDE.md"}, "2026-08-12T08:00:00.000Z"
            ),
            _assistant_tool_use("Bash", {"command": "ls"}, "2026-08-12T08:00:01.000Z"),
        ],
    )

    assert list(prr._iter_glossary_pulls(path)) == []


def test_iter_glossary_pulls_skips_malformed_lines(tmp_path: Path) -> None:
    """Same precedent as scripts/comm_patterns/transcript.py: skip, don't raise."""
    path = tmp_path / "t.jsonl"
    path.write_text(
        "not json at all\n"
        + json.dumps(
            _assistant_tool_use("Grep", {"path": "/repo/CONTEXT.md"}, "2026-08-12T09:00:00.000Z")
        )
        + "\n"
    )

    pulls = list(prr._iter_glossary_pulls(path))

    assert len(pulls) == 1


def test_iter_glossary_pulls_missing_file_yields_nothing(tmp_path: Path) -> None:
    assert list(prr._iter_glossary_pulls(tmp_path / "does-not-exist.jsonl")) == []


# --- _session_windows (join on session_id + project, ts-windowed) ----------


def test_session_windows_single_row_is_open_ended() -> None:
    rows = [{"session_id": "s1", "project": "jarvis", "ts": "2026-08-11T19:23:05.602793+00:00"}]

    windows = prr._session_windows(rows)

    assert len(windows) == 1
    assert windows[0]["session_id"] == "s1"
    assert windows[0]["project"] == "jarvis"
    assert windows[0]["end"] is None


def test_session_windows_splits_resumed_session_into_multiple_windows() -> None:
    """(session_id, project) is not unique per run (Claude Code reuses ids
    across --resume) — each row gets its own [ts, next_ts) window."""
    rows = [
        {"session_id": "s1", "project": "jarvis", "ts": "2026-08-11T19:00:00+00:00"},
        {"session_id": "s1", "project": "jarvis", "ts": "2026-08-11T20:00:00+00:00"},
    ]

    windows = prr._session_windows(rows)

    assert len(windows) == 2
    windows_sorted = sorted(windows, key=lambda w: w["start"])
    assert windows_sorted[0]["end"] == windows_sorted[1]["start"]
    assert windows_sorted[1]["end"] is None


def test_session_windows_skips_rows_without_session_id() -> None:
    """Old self-log rows (pre-#1275 join key) lack session_id entirely."""
    rows = [{"ts": "2026-08-03T07:04:04.523993+00:00", "emitted_chars": 5712}]

    assert prr._session_windows(rows) == []


def test_session_windows_distinguishes_by_project_too() -> None:
    rows = [
        {"session_id": "shared-id", "project": "jarvis", "ts": "2026-08-11T19:00:00+00:00"},
        {"session_id": "shared-id", "project": "redrobot", "ts": "2026-08-11T19:05:00+00:00"},
    ]

    windows = prr._session_windows(rows)

    projects = {w["project"] for w in windows}
    assert projects == {"jarvis", "redrobot"}


# --- compute_pull_rate (integration) ----------------------------------------


def test_compute_pull_rate_counts_window_with_matching_pull(tmp_path: Path) -> None:
    session_id = "sess-hit"
    transcript = tmp_path / "proj1" / f"{session_id}.jsonl"
    transcript.parent.mkdir(parents=True)
    _write_jsonl(
        transcript,
        [
            _assistant_tool_use(
                "Read", {"file_path": "/repo/CONTEXT.md"}, "2026-08-12T08:05:00+00:00"
            )
        ],
    )
    self_log_rows = [
        {"session_id": session_id, "project": "jarvis", "ts": "2026-08-12T08:00:00+00:00"},
    ]

    result = prr.compute_pull_rate(self_log_rows, tmp_path)

    assert result == {"total_runs": 1, "runs_with_pull": 1, "pull_rate": 1.0}


def test_compute_pull_rate_excludes_pull_outside_window(tmp_path: Path) -> None:
    """A pull that happens after the NEXT row's ts belongs to a later window."""
    session_id = "sess-later"
    transcript = tmp_path / "proj1" / f"{session_id}.jsonl"
    transcript.parent.mkdir(parents=True)
    _write_jsonl(
        transcript,
        [_assistant_tool_use("Grep", {"path": "/repo/CONTEXT.md"}, "2026-08-12T09:30:00+00:00")],
    )
    self_log_rows = [
        {"session_id": session_id, "project": "jarvis", "ts": "2026-08-12T08:00:00+00:00"},
        {"session_id": session_id, "project": "jarvis", "ts": "2026-08-12T09:00:00+00:00"},
    ]

    result = prr.compute_pull_rate(self_log_rows, tmp_path)

    # pull at 09:30 falls in window 2 [09:00, None), not window 1 [08:00, 09:00)
    assert result == {"total_runs": 2, "runs_with_pull": 1, "pull_rate": 0.5}


def test_compute_pull_rate_counts_subagent_transcript_pull(tmp_path: Path) -> None:
    """The numerator must include pulls made by subagents, not just the main run."""
    session_id = "sess-sub"
    main = tmp_path / "proj1" / f"{session_id}.jsonl"
    main.parent.mkdir(parents=True)
    main.write_text("")
    sub = tmp_path / "proj1" / session_id / "subagents" / "agent-1.jsonl"
    sub.parent.mkdir(parents=True)
    _write_jsonl(
        sub,
        [
            _assistant_tool_use(
                "Read", {"file_path": "/repo/CONTEXT.md"}, "2026-08-12T08:10:00+00:00"
            )
        ],
    )
    self_log_rows = [
        {"session_id": session_id, "project": "jarvis", "ts": "2026-08-12T08:00:00+00:00"}
    ]

    result = prr.compute_pull_rate(self_log_rows, tmp_path)

    assert result["runs_with_pull"] == 1


def test_compute_pull_rate_zero_runs_is_zero_not_divide_error() -> None:
    assert prr.compute_pull_rate([], Path("/nonexistent"))["pull_rate"] == 0.0


# --- evaluate_escalation (two branches, explicit numeric thresholds) -------


def test_evaluate_escalation_insufficient_sample_takes_no_action() -> None:
    result = prr.evaluate_escalation(pull_rate=0.0, sample_size=prr.MIN_SAMPLE_SIZE - 1)

    assert result["action"] == "none"
    assert result["reason"] == "insufficient_sample"


def test_evaluate_escalation_low_pull_rate_with_enough_sample_escalates() -> None:
    result = prr.evaluate_escalation(pull_rate=prr.LOW_THRESHOLD, sample_size=prr.MIN_SAMPLE_SIZE)

    assert result["action"] == "escalate_to_level1"


def test_evaluate_escalation_healthy_pull_rate_takes_no_action() -> None:
    result = prr.evaluate_escalation(
        pull_rate=prr.LOW_THRESHOLD + 0.5, sample_size=prr.MIN_SAMPLE_SIZE
    )

    assert result["action"] == "none"
    assert result["reason"] == "healthy"
