"""Unit tests for scripts/recall-audit.py (#333).

Covers each detector with synthetic jsonl fixtures, plus markdown
rendering, aggregation, and CLI behavior. Uses a path-based importlib
load because the script's filename contains a dash.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path


_SCRIPT_PATH = Path(__file__).resolve().parent.parent / "scripts" / "recall-audit.py"
_spec = importlib.util.spec_from_file_location("recall_audit", _SCRIPT_PATH)
assert _spec and _spec.loader
recall_audit = importlib.util.module_from_spec(_spec)
# Python 3.14's @dataclass needs the module registered in sys.modules before
# exec_module so it can look up the class's module namespace.
sys.modules["recall_audit"] = recall_audit
_spec.loader.exec_module(recall_audit)


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------


def _assistant_record(session_id: str, blocks: list[dict]) -> dict:
    return {
        "type": "assistant",
        "sessionId": session_id,
        "message": {"role": "assistant", "content": blocks},
    }


def _tool_use(name: str, tool_input: dict, tool_id: str = "t1") -> dict:
    return {"type": "tool_use", "id": tool_id, "name": name, "input": tool_input}


def _text(body: str) -> dict:
    return {"type": "text", "text": body}


def _write_jsonl(path: Path, records: list[dict]) -> Path:
    with path.open("w", encoding="utf-8") as fh:
        for rec in records:
            fh.write(json.dumps(rec) + "\n")
    return path


# ---------------------------------------------------------------------------
# Detector 1 — empty memories_used on record_decision
# ---------------------------------------------------------------------------


class TestEmptyMemoriesUsed:
    def test_flags_missing_field(self, tmp_path):
        path = _write_jsonl(
            tmp_path / "s.jsonl",
            [
                _assistant_record(
                    "s1",
                    [
                        _tool_use(
                            "mcp__memory__record_decision",
                            {
                                "decision": "go with option A",
                                "rationale": "why A",
                                "reversibility": "reversible",
                            },
                        )
                    ],
                )
            ],
        )
        result = recall_audit.audit_session(path)
        assert len(result.flags) == 1
        assert result.flags[0].kind == "empty_memories_used"
        assert result.flags[0].summary.startswith("go with option A")

    def test_flags_empty_list(self, tmp_path):
        path = _write_jsonl(
            tmp_path / "s.jsonl",
            [
                _assistant_record(
                    "s1",
                    [
                        _tool_use(
                            "mcp__memory__record_decision",
                            {
                                "decision": "blank mem list",
                                "rationale": "r",
                                "reversibility": "reversible",
                                "memories_used": [],
                            },
                        )
                    ],
                )
            ],
        )
        result = recall_audit.audit_session(path)
        assert len(result.flags) == 1
        assert result.flags[0].kind == "empty_memories_used"

    def test_does_not_flag_when_populated(self, tmp_path):
        path = _write_jsonl(
            tmp_path / "s.jsonl",
            [
                _assistant_record(
                    "s1",
                    [
                        _tool_use(
                            "mcp__memory__record_decision",
                            {
                                "decision": "with basis",
                                "rationale": "r",
                                "reversibility": "reversible",
                                "memories_used": ["uuid-a", "uuid-b"],
                            },
                        )
                    ],
                )
            ],
        )
        result = recall_audit.audit_session(path)
        assert [f for f in result.flags if f.kind == "empty_memories_used"] == []


# ---------------------------------------------------------------------------
# Detector 2 — decision language without preceding recall
# ---------------------------------------------------------------------------


class TestDecisionTextNoRecall:
    def test_flags_decision_text_when_no_recall_in_window(self, tmp_path):
        path = _write_jsonl(
            tmp_path / "s.jsonl",
            [
                _assistant_record(
                    "s2",
                    [
                        _text(
                            "After looking at the options, I decided to go with Postgres FTS "
                            "because latency is under budget and it keeps the stack simple."
                        )
                    ],
                )
            ],
        )
        result = recall_audit.audit_session(path)
        kinds = [f.kind for f in result.flags]
        assert "decision_text_no_recall" in kinds

    def test_suppressed_when_memory_recall_preceded(self, tmp_path):
        path = _write_jsonl(
            tmp_path / "s.jsonl",
            [
                _assistant_record(
                    "s2",
                    [_tool_use("mcp__memory__memory_recall", {"query": "fts options"})],
                ),
                _assistant_record(
                    "s2",
                    [
                        _text(
                            "After looking at the options, I decided to go with Postgres FTS "
                            "because latency is under budget and it keeps the stack simple."
                        )
                    ],
                ),
            ],
        )
        result = recall_audit.audit_session(path)
        assert [f for f in result.flags if f.kind == "decision_text_no_recall"] == []

    def test_window_boundary_expires_recall(self, tmp_path):
        """A recall call further back than WINDOW_TOOLS no longer suppresses."""
        records = [
            _assistant_record("s2", [_tool_use("mcp__memory__memory_recall", {"query": "early"})])
        ]
        for i in range(recall_audit.WINDOW_TOOLS + 1):
            records.append(_assistant_record("s2", [_tool_use("Read", {"file_path": f"f{i}.py"})]))
        records.append(
            _assistant_record(
                "s2",
                [_text("I decided to use the second approach instead of rewriting from scratch.")],
            )
        )
        path = _write_jsonl(tmp_path / "s.jsonl", records)
        result = recall_audit.audit_session(path)
        assert any(f.kind == "decision_text_no_recall" for f in result.flags)

    def test_short_text_does_not_match(self, tmp_path):
        path = _write_jsonl(
            tmp_path / "s.jsonl",
            [_assistant_record("s2", [_text("Let's do X.")])],
        )
        result = recall_audit.audit_session(path)
        assert [f for f in result.flags if f.kind == "decision_text_no_recall"] == []

    def test_casual_language_not_flagged(self, tmp_path):
        path = _write_jsonl(
            tmp_path / "s.jsonl",
            [
                _assistant_record(
                    "s2",
                    [
                        _text(
                            "This function reads the config file and returns the parsed value. "
                            "It handles missing files gracefully by returning None."
                        )
                    ],
                )
            ],
        )
        result = recall_audit.audit_session(path)
        assert [f for f in result.flags if f.kind == "decision_text_no_recall"] == []


# ---------------------------------------------------------------------------
# Detector 3 — feedback/decision memory_store without preceding recall
# ---------------------------------------------------------------------------


class TestStoreNoRecall:
    def test_flags_feedback_store_without_recall(self, tmp_path):
        path = _write_jsonl(
            tmp_path / "s.jsonl",
            [
                _assistant_record(
                    "s3",
                    [
                        _tool_use(
                            "mcp__memory__memory_store",
                            {
                                "name": "feedback_xxx",
                                "type": "feedback",
                                "content": "rule",
                            },
                        )
                    ],
                )
            ],
        )
        result = recall_audit.audit_session(path)
        assert [f.kind for f in result.flags] == ["store_no_recall"]

    def test_flags_decision_store_without_recall(self, tmp_path):
        path = _write_jsonl(
            tmp_path / "s.jsonl",
            [
                _assistant_record(
                    "s3",
                    [
                        _tool_use(
                            "mcp__memory__memory_store",
                            {
                                "name": "decision_yyy",
                                "type": "decision",
                                "content": "chose X",
                            },
                        )
                    ],
                )
            ],
        )
        result = recall_audit.audit_session(path)
        assert any(f.kind == "store_no_recall" for f in result.flags)

    def test_skips_project_type_store(self, tmp_path):
        path = _write_jsonl(
            tmp_path / "s.jsonl",
            [
                _assistant_record(
                    "s3",
                    [
                        _tool_use(
                            "mcp__memory__memory_store",
                            {"name": "state", "type": "project", "content": "working"},
                        )
                    ],
                )
            ],
        )
        result = recall_audit.audit_session(path)
        assert [f for f in result.flags if f.kind == "store_no_recall"] == []

    def test_suppressed_by_recall_in_window(self, tmp_path):
        path = _write_jsonl(
            tmp_path / "s.jsonl",
            [
                _assistant_record(
                    "s3", [_tool_use("mcp__memory__memory_recall", {"query": "dup"})]
                ),
                _assistant_record(
                    "s3",
                    [
                        _tool_use(
                            "mcp__memory__memory_store",
                            {"name": "fb", "type": "feedback", "content": "r"},
                        )
                    ],
                ),
            ],
        )
        result = recall_audit.audit_session(path)
        assert [f for f in result.flags if f.kind == "store_no_recall"] == []


# ---------------------------------------------------------------------------
# Counters + aggregation + render
# ---------------------------------------------------------------------------


class TestCountersAndRender:
    def test_counters_track_tool_uses(self, tmp_path):
        path = _write_jsonl(
            tmp_path / "s.jsonl",
            [
                _assistant_record(
                    "s4",
                    [
                        _tool_use("mcp__memory__memory_recall", {"query": "x"}),
                        _tool_use(
                            "mcp__memory__record_decision",
                            {
                                "decision": "d",
                                "rationale": "r",
                                "reversibility": "reversible",
                                "memories_used": ["u"],
                            },
                        ),
                        _tool_use(
                            "mcp__memory__memory_store",
                            {"name": "x", "type": "feedback", "content": "c"},
                        ),
                    ],
                )
            ],
        )
        result = recall_audit.audit_session(path)
        c = result.counters
        assert c["memory_recall_calls"] == 1
        assert c["record_decision_calls"] == 1
        assert c["memory_store_calls"] == 1
        assert c["tool_uses"] >= 3

    def test_render_empty_when_no_flags(self, tmp_path):
        path = _write_jsonl(tmp_path / "s.jsonl", [])
        result = recall_audit.audit_session(path)
        assert recall_audit.render_markdown(result) == ""

    def test_render_with_flags(self, tmp_path):
        path = _write_jsonl(
            tmp_path / "s.jsonl",
            [
                _assistant_record(
                    "s5",
                    [
                        _tool_use(
                            "mcp__memory__record_decision",
                            {
                                "decision": "no basis",
                                "rationale": "r",
                                "reversibility": "reversible",
                            },
                        )
                    ],
                )
            ],
        )
        result = recall_audit.audit_session(path)
        md = recall_audit.render_markdown(result)
        assert "Recall audit" in md
        assert "empty memories_used" in md
        assert "L1" in md

    def test_aggregate_across_sessions(self, tmp_path):
        p1 = _write_jsonl(
            tmp_path / "a.jsonl",
            [
                _assistant_record(
                    "a",
                    [
                        _tool_use(
                            "mcp__memory__record_decision",
                            {"decision": "x", "rationale": "r", "reversibility": "reversible"},
                        )
                    ],
                )
            ],
        )
        p2 = _write_jsonl(
            tmp_path / "b.jsonl",
            [
                _assistant_record(
                    "b",
                    [
                        _tool_use(
                            "mcp__memory__record_decision",
                            {
                                "decision": "y",
                                "rationale": "r",
                                "reversibility": "reversible",
                                "memories_used": ["u"],
                            },
                        )
                    ],
                )
            ],
        )
        results = [recall_audit.audit_session(p) for p in (p1, p2)]
        agg = recall_audit.aggregate(results)
        assert agg["sessions"] == 2
        assert agg["record_decision_calls"] == 2
        assert agg["flags_by_kind"]["empty_memories_used"] == 1
        assert agg["empty_memories_used_pct"] == 50.0

    def test_aggregate_no_decisions_zeros(self, tmp_path):
        result = recall_audit.audit_session(_write_jsonl(tmp_path / "empty.jsonl", []))
        agg = recall_audit.aggregate([result])
        assert agg["record_decision_calls"] == 0
        assert agg["empty_memories_used_pct"] == 0.0


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


class TestCLI:
    def test_cli_json_single_session(self, tmp_path, monkeypatch, capsys):
        p = _write_jsonl(
            tmp_path / "s.jsonl",
            [
                _assistant_record(
                    "cli",
                    [
                        _tool_use(
                            "mcp__memory__record_decision",
                            {"decision": "x", "rationale": "r", "reversibility": "reversible"},
                        )
                    ],
                )
            ],
        )
        rc = recall_audit.main([str(p)])
        assert rc == 0
        out = capsys.readouterr().out
        payload = json.loads(out)
        assert payload["session_id"] == "cli"
        assert any(f["kind"] == "empty_memories_used" for f in payload["flags"])

    def test_cli_md_format_single_session(self, tmp_path, capsys):
        p = _write_jsonl(
            tmp_path / "s.jsonl",
            [
                _assistant_record(
                    "cli2",
                    [
                        _tool_use(
                            "mcp__memory__record_decision",
                            {"decision": "x", "rationale": "r", "reversibility": "reversible"},
                        )
                    ],
                )
            ],
        )
        rc = recall_audit.main([str(p), "--format", "md"])
        assert rc == 0
        out = capsys.readouterr().out
        assert "## cli2" in out
        assert "Recall audit" in out

    def test_cli_aggregate(self, tmp_path, capsys):
        p = _write_jsonl(
            tmp_path / "s.jsonl",
            [
                _assistant_record(
                    "agg",
                    [
                        _tool_use(
                            "mcp__memory__record_decision",
                            {"decision": "x", "rationale": "r", "reversibility": "reversible"},
                        )
                    ],
                )
            ],
        )
        rc = recall_audit.main([str(p), "--aggregate"])
        assert rc == 0
        out = capsys.readouterr().out
        payload = json.loads(out)
        assert payload["sessions"] == 1
        assert payload["flags_by_kind"]["empty_memories_used"] == 1

    def test_cli_missing_args_errors(self, capsys):
        try:
            recall_audit.main([])
        except SystemExit as e:
            assert e.code == 2
        captured = capsys.readouterr()
        assert "required" in captured.err.lower() or "usage" in captured.err.lower()

    def test_cli_missing_file(self, tmp_path, capsys):
        rc = recall_audit.main([str(tmp_path / "nope.jsonl")])
        assert rc == 1
        assert "error" in capsys.readouterr().err.lower()
