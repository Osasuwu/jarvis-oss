"""Unit tests for scripts/lib/recall_dedup.py (#1276).

Covers the per-session (memory-id, mode, generation) dedup state shared by
memory-recall-hook.py and pretooluse-recall-hook.py:
  - generation reads the per-session compaction counter (pre-compact-backup)
  - a (id, mode) record suppresses re-injection within the same generation
  - a compaction bump (generation changes) resets dedup state
  - modes are disjoint namespaces (brief/full/pretooluse don't collide)
  - a missing session_id disables dedup (fail-open, never wrongly suppress)
  - best-effort I/O never raises
"""

from __future__ import annotations

import json

import pytest
from lib import recall_dedup as rd


@pytest.fixture(autouse=True)
def _isolated_dirs(tmp_path, monkeypatch):
    """Redirect both state dirs into a pytest-managed tmp dir."""
    monkeypatch.setattr(rd, "COMPACTION_DIR", tmp_path / "compaction-counts")
    monkeypatch.setattr(rd, "DEDUP_DIR", tmp_path / "cache" / "recall-dedup")
    yield


def _write_generation(session_id: str, gen: int) -> None:
    rd.COMPACTION_DIR.mkdir(parents=True, exist_ok=True)
    f = rd.COMPACTION_DIR / f"{rd.sanitize_session_id(session_id)}.txt"
    f.write_text(str(gen), encoding="utf-8")


class TestSanitizeSessionId:
    def test_keeps_identifier_chars(self):
        assert rd.sanitize_session_id("abc-123_xyz") == "abc-123_xyz"

    def test_strips_traversal_chars(self):
        # Path separators and dots are dropped; only id chars survive. Mirrors
        # pre-compact-backup._sanitize_session_id so both hooks key off the
        # exact same per-session counter file name.
        assert rd.sanitize_session_id("../evil/id.txt") == "evilidtxt"

    def test_empty_falls_back(self):
        assert rd.sanitize_session_id("") == "unknown-session"

    def test_non_string_coerced(self):
        # str(None) == "None" — deterministic id-char output, never a crash.
        assert rd.sanitize_session_id(None) == "None"


class TestCurrentGeneration:
    def test_missing_counter_is_zero(self):
        assert rd.current_generation("sess-1") == 0

    def test_reads_counter_file(self):
        _write_generation("sess-1", 3)
        assert rd.current_generation("sess-1") == 3

    def test_corrupt_counter_is_zero(self):
        _write_generation("sess-1", 1)
        f = rd.COMPACTION_DIR / f"{rd.sanitize_session_id('sess-1')}.txt"
        f.write_text("not-a-number", encoding="utf-8")
        assert rd.current_generation("sess-1") == 0


class TestFilterEmittable:
    def test_empty_session_disables_dedup(self):
        rows = [{"id": "a", "name": "A"}]
        assert rd.filter_emittable("", rows, rd.MODE_BRIEF) == rows

    def test_fresh_memory_is_emittable(self):
        rows = [{"id": "a", "name": "A"}, {"id": "b", "name": "B"}]
        assert rd.filter_emittable("sess-1", rows, rd.MODE_BRIEF) == rows

    def test_recorded_memory_filtered(self):
        rd.record_emitted("sess-1", ["a"], rd.MODE_BRIEF)
        rows = [{"id": "a", "name": "A"}, {"id": "b", "name": "B"}]
        kept = rd.filter_emittable("sess-1", rows, rd.MODE_BRIEF)
        assert [r["id"] for r in kept] == ["b"]

    def test_generation_bump_resets(self):
        _write_generation("sess-1", 1)
        rd.record_emitted("sess-1", ["a"], rd.MODE_BRIEF)
        _write_generation("sess-1", 2)  # compaction happened
        rows = [{"id": "a", "name": "A"}]
        assert rd.filter_emittable("sess-1", rows, rd.MODE_BRIEF) == rows

    def test_modes_are_disjoint(self):
        rd.record_emitted("sess-1", ["a"], rd.MODE_BRIEF)
        rows = [{"id": "a", "name": "A"}]
        assert rd.filter_emittable("sess-1", rows, rd.MODE_FULL) == rows
        assert rd.filter_emittable("sess-1", rows, rd.MODE_PRETOOLUSE) == rows

    def test_name_fallback_when_no_id(self):
        rd.record_emitted("sess-1", ["mem_by_name"], rd.MODE_BRIEF)
        rows = [{"name": "mem_by_name"}]
        assert rd.filter_emittable("sess-1", rows, rd.MODE_BRIEF) == []

    def test_best_effort_on_corrupt_state(self):
        rd.DEDUP_DIR.mkdir(parents=True, exist_ok=True)
        (rd.DEDUP_DIR / "sess-1.json").write_text("{broken", encoding="utf-8")
        rows = [{"id": "a", "name": "A"}]
        assert rd.filter_emittable("sess-1", rows, rd.MODE_BRIEF) == rows


class TestRecordEmitted:
    def test_writes_state_file(self):
        rd.record_emitted("sess-1", ["a", "b"], rd.MODE_BRIEF)
        state = json.loads((rd.DEDUP_DIR / "sess-1.json").read_text(encoding="utf-8"))
        assert state == {"a:brief": 0, "b:brief": 0}

    def test_empty_session_noop(self):
        rd.record_emitted("", ["a"], rd.MODE_BRIEF)
        assert not rd.DEDUP_DIR.exists()

    def test_empty_ids_noop(self):
        rd.record_emitted("sess-1", [], rd.MODE_BRIEF)
        assert not rd.DEDUP_DIR.exists()

    def test_preserves_other_modes(self):
        rd.record_emitted("sess-1", ["a"], rd.MODE_BRIEF)
        rd.record_emitted("sess-1", ["a"], rd.MODE_FULL)
        state = json.loads((rd.DEDUP_DIR / "sess-1.json").read_text(encoding="utf-8"))
        assert state == {"a:brief": 0, "a:full": 0}

    def test_generation_recorded_from_counter(self):
        _write_generation("sess-1", 5)
        rd.record_emitted("sess-1", ["a"], rd.MODE_BRIEF)
        state = json.loads((rd.DEDUP_DIR / "sess-1.json").read_text(encoding="utf-8"))
        assert state == {"a:brief": 5}
