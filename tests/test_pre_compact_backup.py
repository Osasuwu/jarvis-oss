"""Unit tests for scripts/pre-compact-backup.py.

The Supabase-upsert path is exercised live (not here). This file covers the
deterministic parsing + composition pieces that don't need network, plus the
local fallback and the "never raises" guarantee.

The module filename uses a dash so importlib is required.
"""

from __future__ import annotations

import importlib.util
import io
import json
import sys
import types
from pathlib import Path

import pytest


# ---------------------------------------------------------------------------
# Stub optional deps so module import succeeds on minimal CI
# ---------------------------------------------------------------------------
for _stub in ("dotenv", "supabase"):
    if _stub not in sys.modules:
        try:
            __import__(_stub)
        except ImportError:
            mod = types.ModuleType(_stub)
            if _stub == "dotenv":
                mod.load_dotenv = lambda *a, **k: None
            if _stub == "supabase":
                mod.create_client = lambda *a, **k: None
            sys.modules[_stub] = mod


_PATH = Path(__file__).resolve().parent.parent / "scripts" / "pre-compact-backup.py"
_spec = importlib.util.spec_from_file_location("pre_compact_backup", _PATH)
pcb = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(pcb)


# ---------------------------------------------------------------------------
# Fixtures — synthetic transcript entries
# ---------------------------------------------------------------------------
def _user_entry(ts: str, text, isSidechain: bool = False) -> dict:
    return {
        "type": "user",
        "timestamp": ts,
        "isSidechain": isSidechain,
        "gitBranch": "feat/278-precompact-hook",
        "message": {"content": text},
    }


def _assistant_tool(ts: str, name: str, inp: dict) -> dict:
    return {
        "type": "assistant",
        "timestamp": ts,
        "gitBranch": "feat/278-precompact-hook",
        "message": {"content": [{"type": "tool_use", "id": "t1", "name": name, "input": inp}]},
    }


def _assistant_text(ts: str, text: str) -> dict:
    return {
        "type": "assistant",
        "timestamp": ts,
        "gitBranch": "feat/278-precompact-hook",
        "message": {"content": [{"type": "text", "text": text}]},
    }


# ---------------------------------------------------------------------------
# _detect_project
# ---------------------------------------------------------------------------
class TestDetectProject:
    def test_known_project(self):
        # Forward-slash form works on both Windows and POSIX; raw backslash
        # paths only resolve on Windows (Path on Linux treats them as part of
        # the name, so `.name` returns the entire string).
        assert pcb._detect_project("/Users/jdoe/GitHub/jarvis") == "jarvis"
        assert pcb._detect_project("/home/x/redrobot") == "redrobot"

    def test_unknown_returns_none(self):
        assert pcb._detect_project("/tmp/random") is None

    def test_none_or_empty(self):
        assert pcb._detect_project(None) is None
        assert pcb._detect_project("") is None


# ---------------------------------------------------------------------------
# _read_hook_input — tolerant JSON
# ---------------------------------------------------------------------------
class TestReadHookInput:
    def test_valid_payload(self):
        payload = {"session_id": "abc", "transcript_path": "/x"}
        r = pcb._read_hook_input(io.StringIO(json.dumps(payload)))
        assert r == payload

    def test_empty_string(self):
        assert pcb._read_hook_input(io.StringIO("")) == {}

    def test_invalid_json(self):
        assert pcb._read_hook_input(io.StringIO("not json")) == {}


# ---------------------------------------------------------------------------
# _parse_transcript — file reading + tail truncation
# ---------------------------------------------------------------------------
class TestParseTranscript:
    def test_small_file(self, tmp_path):
        p = tmp_path / "t.jsonl"
        p.write_text(
            "\n".join(json.dumps({"type": "user", "i": i}) for i in range(3)),
            encoding="utf-8",
        )
        entries, total, dropped = pcb._parse_transcript(p)
        assert total == 3
        assert dropped == 0
        assert [e["i"] for e in entries] == [0, 1, 2]

    def test_truncates_above_max(self, tmp_path, monkeypatch):
        monkeypatch.setattr(pcb, "MAX_TRANSCRIPT_LINES", 10)
        monkeypatch.setattr(pcb, "TAIL_KEEP", 6)
        p = tmp_path / "t.jsonl"
        p.write_text(
            "\n".join(json.dumps({"type": "user", "i": i}) for i in range(15)),
            encoding="utf-8",
        )
        entries, total, dropped = pcb._parse_transcript(p)
        assert total == 15
        assert dropped == 9
        assert len(entries) == 6
        assert entries[0]["i"] == 9  # kept the tail

    def test_skips_malformed_lines(self, tmp_path):
        p = tmp_path / "t.jsonl"
        p.write_text(
            '{"type":"user","i":0}\n<<garbage>>\n{"type":"user","i":1}\n',
            encoding="utf-8",
        )
        entries, total, _ = pcb._parse_transcript(p)
        assert total == 3
        assert [e["i"] for e in entries] == [0, 1]

    def test_missing_file_returns_empty(self, tmp_path):
        entries, total, dropped = pcb._parse_transcript(tmp_path / "missing.jsonl")
        assert entries == [] and total == 0 and dropped == 0


# ---------------------------------------------------------------------------
# _extract_user_messages — filters + dedup
# ---------------------------------------------------------------------------
class TestExtractUserMessages:
    def test_string_content(self):
        entries = [_user_entry("2026-04-21T00:00:00Z", "what did I leave running?")]
        out = pcb._extract_user_messages(entries)
        assert out == [("2026-04-21T00:00:00Z", "what did I leave running?")]

    def test_list_text_blocks(self):
        entries = [
            _user_entry(
                "t",
                [
                    {"type": "text", "text": "hello"},
                    {"type": "tool_result", "tool_use_id": "x", "content": "ignored"},
                ],
            )
        ]
        assert pcb._extract_user_messages(entries) == [("t", "hello")]

    def test_filters_noise(self):
        entries = [
            _user_entry("t1", "<command-message>morning-brief</command-message>"),
            _user_entry("t2", "<command-name>/foo</command-name>"),
            _user_entry("t3", "Base directory for this skill: X"),
            _user_entry("t4", "<scheduled-task name='x'>bootstrap</scheduled-task>"),
            _user_entry("t5", "real question"),
        ]
        out = pcb._extract_user_messages(entries)
        assert [text for _, text in out] == ["real question"]

    def test_dedups_repeats(self):
        entries = [
            _user_entry("t1", "same question"),
            _user_entry("t2", "same question"),
            _user_entry("t3", "different"),
        ]
        out = pcb._extract_user_messages(entries)
        assert [text for _, text in out] == ["same question", "different"]

    def test_skips_sidechain(self):
        entries = [_user_entry("t", "subagent chatter", isSidechain=True)]
        assert pcb._extract_user_messages(entries) == []


# ---------------------------------------------------------------------------
# _summarize_tool + _extract_actions
# ---------------------------------------------------------------------------
class TestSummarizeTool:
    def test_bash_strips_newlines_and_caps(self):
        s = pcb._summarize_tool("Bash", {"command": "echo hi\nsleep 1"})
        assert "\n" not in s
        assert s == "echo hi sleep 1"

    def test_edit_returns_path(self):
        assert pcb._summarize_tool("Edit", {"file_path": "/a/b.py"}) == "/a/b.py"

    def test_todowrite_counts(self):
        s = pcb._summarize_tool(
            "TodoWrite",
            {
                "todos": [
                    {"status": "completed"},
                    {"status": "in_progress"},
                    {"status": "pending"},
                    {"status": "pending"},
                ]
            },
        )
        assert s == "4 todos (1 done, 1 in progress)"

    def test_memory_store(self):
        s = pcb._summarize_tool(
            "mcp__memory__memory_store",
            {"name": "working_state_jarvis", "type": "project"},
        )
        assert "name=working_state_jarvis" in s and "type=project" in s

    def test_record_decision_truncates(self):
        s = pcb._summarize_tool(
            "mcp__memory__record_decision",
            {"decision": "x" * 200},
        )
        assert len(s) <= 120

    def test_unknown_tool_fallback_description(self):
        s = pcb._summarize_tool("SomeNewTool", {"description": "does stuff"})
        assert s == "does stuff"

    def test_unknown_tool_no_useful_field(self):
        assert pcb._summarize_tool("SomeNewTool", {"foo": 1}) == ""


class TestExtractActions:
    def test_collects_tool_uses(self):
        entries = [
            _assistant_tool("t1", "Bash", {"command": "ls"}),
            _assistant_text("t2", "intermediate thought"),
            _assistant_tool("t3", "Edit", {"file_path": "a.py"}),
        ]
        out = pcb._extract_actions(entries)
        assert out == [("t1", "Bash: ls"), ("t3", "Edit: a.py")]


# ---------------------------------------------------------------------------
# _extract_last_todos + _extract_last_assistant_text
# ---------------------------------------------------------------------------
class TestExtractLastTodos:
    def test_picks_last(self):
        first = [{"content": "old", "status": "pending"}]
        last = [{"content": "new", "status": "in_progress"}]
        entries = [
            _assistant_tool("t1", "TodoWrite", {"todos": first}),
            _assistant_tool("t2", "Bash", {"command": "ls"}),
            _assistant_tool("t3", "TodoWrite", {"todos": last}),
        ]
        assert pcb._extract_last_todos(entries) == last

    def test_no_todowrite(self):
        entries = [_assistant_tool("t", "Bash", {"command": "ls"})]
        assert pcb._extract_last_todos(entries) == []


class TestExtractLastAssistantText:
    def test_picks_most_recent(self):
        entries = [
            _assistant_text("t1", "first"),
            _assistant_tool("t2", "Bash", {"command": "ls"}),
            _assistant_text("t3", "second"),
        ]
        assert pcb._extract_last_assistant_text(entries) == "second"

    def test_no_text(self):
        entries = [_assistant_tool("t", "Bash", {"command": "ls"})]
        assert pcb._extract_last_assistant_text(entries) == ""


# ---------------------------------------------------------------------------
# _compose_markdown
# ---------------------------------------------------------------------------
class TestComposeMarkdown:
    def test_structure(self):
        entries = [
            _user_entry("t1", "what's next?"),
            _assistant_tool("t2", "Bash", {"command": "git status"}),
            _assistant_tool(
                "t3", "TodoWrite", {"todos": [{"content": "ship it", "status": "in_progress"}]}
            ),
            _assistant_text("t4", "Done."),
        ]
        md = pcb._compose_markdown("s-1", "manual", "/x/jarvis", entries, 4, 0)
        assert "# Session Snapshot — s-1" in md
        assert "**Trigger:** manual" in md
        assert "**cwd:** `/x/jarvis`" in md
        assert "## User messages (1)" in md
        assert "what's next?" in md
        assert "## Actions" in md
        assert "Bash: git status" in md
        assert "## Open loops / todos" in md
        assert "[~] ship it" in md
        assert "## Last assistant message" in md
        assert "Done." in md

    def test_reports_dropped_head(self):
        md = pcb._compose_markdown("s", "auto", "/x", [], total_seen=15000, dropped_head=7000)
        assert "dropped-head: 7000" in md

    def test_caps_actions_with_summary(self, monkeypatch):
        monkeypatch.setattr(pcb, "ACTIONS_CAP", 3)
        entries = [_assistant_tool(f"t{i}", "Bash", {"command": f"cmd{i}"}) for i in range(10)]
        md = pcb._compose_markdown("s", "auto", "/x", entries, 10, 0)
        assert "Earlier actions (summarized): Bash×7" in md
        # Only last 3 bash lines appear verbose
        assert md.count("`t7` — Bash: cmd7") == 1
        assert md.count("`t9` — Bash: cmd9") == 1
        assert "Bash: cmd0" not in md

    def test_enforces_size_budget(self, monkeypatch):
        monkeypatch.setattr(pcb, "SIZE_BUDGET", 500)
        # Pile of long bash commands — will bust 500B
        entries = [_assistant_tool(f"t{i}", "Bash", {"command": "x" * 100}) for i in range(20)]
        md = pcb._compose_markdown("s", "auto", "/x", entries, 20, 0)
        assert len(md.encode("utf-8")) <= 500
        assert "truncated at size budget" in md


# ---------------------------------------------------------------------------
# _persist_local — fallback file
# ---------------------------------------------------------------------------
class TestPersistLocal:
    def test_writes_snapshot_file(self, tmp_path, monkeypatch):
        monkeypatch.setattr(pcb, "_root", tmp_path)
        out = pcb._persist_local("session-xyz", "# Snapshot\n")
        assert out is not None
        assert out.exists()
        assert out.parent.name == "session-snapshots"
        assert out.read_text(encoding="utf-8") == "# Snapshot\n"

    def test_path_traversal_blocked(self, tmp_path, monkeypatch):
        monkeypatch.setattr(pcb, "_root", tmp_path)
        assert pcb._persist_local("../../etc/passwd", "evil") is None

    def test_prefix_collision_blocked(self, tmp_path, monkeypatch):
        # "session-snapshots-evil" starts with "session-snapshots" but is a
        # different directory — _is_within compares path components, so it
        # catches this where a string startswith() would not.
        monkeypatch.setattr(pcb, "_root", tmp_path)
        assert pcb._persist_local("../session-snapshots-evil/x", "evil") is None


# ---------------------------------------------------------------------------
# _is_within — shared containment guard (M2: version-safe, no is_relative_to)
# ---------------------------------------------------------------------------
class TestIsWithin:
    def test_nested_path_is_within(self, tmp_path):
        assert pcb._is_within(tmp_path / "a" / "b", tmp_path) is True

    def test_same_path_is_within(self, tmp_path):
        assert pcb._is_within(tmp_path, tmp_path) is True

    def test_sibling_not_within(self, tmp_path):
        assert pcb._is_within(tmp_path / "a", tmp_path / "b") is False

    def test_prefix_collision_not_within(self, tmp_path):
        # "snapshots-evil" shares a string prefix with "snapshots" but is a
        # different dir — component comparison (not startswith) must reject it.
        assert pcb._is_within(tmp_path / "snapshots-evil", tmp_path / "snapshots") is False

    def test_parent_not_within_child(self, tmp_path):
        # Containment is one-directional: root is not "within" its own subdir.
        assert pcb._is_within(tmp_path, tmp_path / "child") is False


# ---------------------------------------------------------------------------
# _persist_supabase — missing-env path must be loud, not silent
# ---------------------------------------------------------------------------
class TestPersistSupabaseMissingEnv:
    def test_missing_env_returns_false_and_warns(self, monkeypatch, capsys):
        monkeypatch.delenv("SUPABASE_URL", raising=False)
        monkeypatch.delenv("SUPABASE_KEY", raising=False)
        assert pcb._persist_supabase("s", "jarvis", "auto", "content") is False
        assert "SUPABASE_URL/SUPABASE_KEY not set" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# _append_hook_log — heartbeat
# ---------------------------------------------------------------------------
def _read_hook_log(root: Path) -> str:
    return (root / ".claude" / "session-snapshots" / "hook.log").read_text(encoding="utf-8")


class TestAppendHookLog:
    def test_appends_timestamped_line(self, tmp_path, monkeypatch):
        monkeypatch.setattr(pcb, "_root", tmp_path)
        pcb._append_hook_log("session=s1 trigger=auto outcome=supabase")
        pcb._append_hook_log("session=s1 trigger=auto outcome=local-fallback")
        lines = _read_hook_log(tmp_path).splitlines()
        assert len(lines) == 2
        assert lines[0].endswith("session=s1 trigger=auto outcome=supabase")
        assert lines[1].endswith("session=s1 trigger=auto outcome=local-fallback")
        # Each line starts with an ISO-8601 UTC stamp
        assert lines[0].split(" ")[0].startswith("20")

    def test_never_raises(self, tmp_path, monkeypatch):
        # _root pointing at a regular file makes mkdir fail — must be swallowed
        blocker = tmp_path / "blocker"
        blocker.write_text("x", encoding="utf-8")
        monkeypatch.setattr(pcb, "_root", blocker)
        pcb._append_hook_log("must not raise")

    def test_write_failure_reports_to_stderr(self, tmp_path, monkeypatch, capsys):
        # The observability log is itself observable on failure.
        blocker = tmp_path / "blocker"
        blocker.write_text("x", encoding="utf-8")
        monkeypatch.setattr(pcb, "_root", blocker)
        pcb._append_hook_log("disk full")
        assert "hook.log write failed" in capsys.readouterr().err

    def test_trims_when_oversized(self, tmp_path, monkeypatch):
        # Bounded growth: once past the byte cap the log keeps only its tail.
        # Shrink the constants so the test stays fast.
        monkeypatch.setattr(pcb, "_root", tmp_path)
        monkeypatch.setattr(pcb, "_HOOK_LOG_MAX_BYTES", 120)
        monkeypatch.setattr(pcb, "_HOOK_LOG_KEEP_LINES", 5)
        for i in range(50):
            pcb._append_hook_log(f"line-{i}")
        log = _read_hook_log(tmp_path)
        lines = log.splitlines()
        # Stays bounded at the keep-window rather than growing to 50 lines…
        assert len(lines) <= 5
        # …always retains the newest entry…
        assert lines[-1].endswith("line-49")
        # …and the oldest forged-off entries are gone.
        assert "line-0 " not in log and "line-0\n" not in log

    def test_trim_on_corrupted_log_does_not_raise(self, tmp_path, monkeypatch):
        # A partially-written/corrupted hook.log with invalid UTF-8 bytes must
        # not crash. The trim uses errors="replace" to handle corrupt bytes
        # gracefully, so it succeeds rather than raising UnicodeDecodeError.
        monkeypatch.setattr(pcb, "_root", tmp_path)
        monkeypatch.setattr(pcb, "_HOOK_LOG_MAX_BYTES", 8)
        out_dir = tmp_path / ".claude" / "session-snapshots"
        out_dir.mkdir(parents=True)
        (out_dir / "hook.log").write_bytes(b"\x80" * 64)
        # Must not raise despite the un-decodable existing content.
        pcb._append_hook_log("after-corruption")
        # Key invariant: heartbeat line is written regardless.
        log_bytes = (out_dir / "hook.log").read_bytes()
        assert b"after-corruption" in log_bytes


# ---------------------------------------------------------------------------
# main — never raises, honours missing/absent inputs, always heartbeats
# ---------------------------------------------------------------------------
class TestMain:
    @pytest.fixture(autouse=True)
    def _isolate_home(self, tmp_path, monkeypatch):
        # main() bumps the compaction counter under Path.home() on PreCompact-
        # shaped payloads (trigger auto/manual). Without this, the through-main
        # tests would write real files into the developer's ~/.claude — redirect
        # home into tmp_path so the side effect stays sandboxed.
        monkeypatch.setattr(pcb.Path, "home", lambda: tmp_path)
        yield

    def test_missing_transcript_path_exits_zero(self, tmp_path, monkeypatch):
        monkeypatch.setattr(pcb, "_root", tmp_path)
        monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps({"session_id": "x"})))
        # Missing transcript_path exits early with outcome=no-transcript-path;
        # Supabase state doesn't matter because we don't reach persistence code.
        assert pcb.main() == 0
        assert "session=x trigger=unknown outcome=no-transcript-path" in _read_hook_log(tmp_path)

    def test_missing_transcript_file(self, tmp_path, monkeypatch):
        monkeypatch.setattr(pcb, "_root", tmp_path)
        monkeypatch.setattr(
            sys,
            "stdin",
            io.StringIO(
                json.dumps(
                    {
                        "session_id": "x",
                        "transcript_path": str(tmp_path / "missing.jsonl"),
                        "cwd": str(tmp_path),
                    }
                )
            ),
        )
        assert pcb.main() == 0
        # Full triplet, not just `outcome=` — keeps the heartbeat format
        # contract auditable in line with every sibling test.
        assert "session=x trigger=unknown outcome=transcript-missing" in _read_hook_log(tmp_path)

    def test_supabase_fail_triggers_local_fallback(self, tmp_path, monkeypatch):
        # Build a tiny transcript
        t = tmp_path / "t.jsonl"
        t.write_text(json.dumps(_user_entry("ts", "hi")) + "\n", encoding="utf-8")
        monkeypatch.setattr(pcb, "_root", tmp_path)
        # Force supabase path to return False → fallback taken
        monkeypatch.setattr(pcb, "_persist_supabase", lambda *a, **k: False)
        monkeypatch.setattr(
            sys,
            "stdin",
            io.StringIO(
                json.dumps(
                    {
                        "session_id": "sess-fallback",
                        "transcript_path": str(t),
                        "cwd": str(tmp_path),
                        "trigger": "manual",
                    }
                )
            ),
        )
        assert pcb.main() == 0
        out = tmp_path / ".claude" / "session-snapshots" / "sess-fallback.md"
        assert out.exists()
        text = out.read_text(encoding="utf-8")
        assert "Session Snapshot — sess-fallback" in text
        assert "**Trigger:** manual" in text
        assert "session=sess-fallback trigger=manual outcome=local-fallback" in _read_hook_log(
            tmp_path
        )

    def test_supabase_success_heartbeats(self, tmp_path, monkeypatch):
        t = tmp_path / "t.jsonl"
        t.write_text(json.dumps(_user_entry("ts", "hi")) + "\n", encoding="utf-8")
        monkeypatch.setattr(pcb, "_root", tmp_path)
        monkeypatch.setattr(pcb, "_persist_supabase", lambda *a, **k: True)
        monkeypatch.setattr(
            sys,
            "stdin",
            io.StringIO(
                json.dumps(
                    {
                        "session_id": "sess-ok",
                        "transcript_path": str(t),
                        "cwd": str(tmp_path),
                        "trigger": "auto",
                    }
                )
            ),
        )
        assert pcb.main() == 0
        assert "session=sess-ok trigger=auto outcome=supabase" in _read_hook_log(tmp_path)
        # Supabase succeeded — no local fallback file
        assert not (tmp_path / ".claude" / "session-snapshots" / "sess-ok.md").exists()

    def test_bad_hook_input_does_not_raise(self, tmp_path, monkeypatch):
        monkeypatch.setattr(pcb, "_root", tmp_path)
        monkeypatch.setattr(sys, "stdin", io.StringIO("definitely not json"))
        assert pcb.main() == 0
        # Unparseable input → empty hook dict → no transcript_path
        assert "session=unknown-session trigger=unknown outcome=no-transcript-path" in (
            _read_hook_log(tmp_path)
        )

    def test_persist_failed_heartbeats(self, tmp_path, monkeypatch):
        # Both persist paths fail → `outcome=persist-failed`. This outcome value
        # previously had zero coverage.
        t = tmp_path / "t.jsonl"
        t.write_text(json.dumps(_user_entry("ts", "hi")) + "\n", encoding="utf-8")
        monkeypatch.setattr(pcb, "_root", tmp_path)
        monkeypatch.setattr(pcb, "_persist_supabase", lambda *a, **k: False)
        monkeypatch.setattr(pcb, "_persist_local", lambda *a, **k: None)
        monkeypatch.setattr(
            sys,
            "stdin",
            io.StringIO(
                json.dumps(
                    {
                        "session_id": "sess-pf",
                        "transcript_path": str(t),
                        "cwd": str(tmp_path),
                        "trigger": "auto",
                    }
                )
            ),
        )
        assert pcb.main() == 0
        assert "session=sess-pf trigger=auto outcome=persist-failed" in _read_hook_log(tmp_path)

    def test_transcript_path_outside_allowed_roots_rejected(self, tmp_path, monkeypatch):
        # transcript_path is untrusted hook stdin. A path to a real file that
        # lives outside ~/.claude and the repo root must be rejected *before* any
        # read or upsert — even though the file exists — so it can't be slurped
        # into a snapshot. The hook still exits 0 (never blocks compaction).
        repo = tmp_path / "repo"
        repo.mkdir()
        monkeypatch.setattr(pcb, "_root", repo)
        evil = tmp_path / "secret.jsonl"  # outside the repo and outside ~/.claude
        evil.write_text(json.dumps(_user_entry("ts", "secret")) + "\n", encoding="utf-8")
        called: list[int] = []
        monkeypatch.setattr(
            pcb, "_persist_supabase", lambda *a, **k: called.append(1) or True
        )
        monkeypatch.setattr(
            sys,
            "stdin",
            io.StringIO(
                json.dumps(
                    {
                        "session_id": "evil-sess",
                        "transcript_path": str(evil),
                        "cwd": str(repo),
                        "trigger": "auto",
                    }
                )
            ),
        )
        assert pcb.main() == 0
        assert called == []  # never reached persistence
        assert (
            "session=evil-sess trigger=auto outcome=transcript-path-rejected"
            in _read_hook_log(repo)
        )

    def test_unhandled_error_still_heartbeats(self, tmp_path, monkeypatch):
        # An exception mid-run still writes a heartbeat, re-stamped to record
        # that it failed after the "init" sentinel (i.e. before any stage set a
        # concrete outcome) — distinguishing an early crash from a mid-persist one.
        t = tmp_path / "t.jsonl"
        t.write_text(json.dumps(_user_entry("ts", "hi")) + "\n", encoding="utf-8")
        monkeypatch.setattr(pcb, "_root", tmp_path)

        def _boom(*a, **k):
            raise RuntimeError("compose blew up")

        monkeypatch.setattr(pcb, "_compose_markdown", _boom)
        monkeypatch.setattr(
            sys,
            "stdin",
            io.StringIO(
                json.dumps(
                    {
                        "session_id": "sess-err",
                        "transcript_path": str(t),
                        "cwd": str(tmp_path),
                        "trigger": "auto",
                    }
                )
            ),
        )
        assert pcb.main() == 0
        assert (
            "session=sess-err trigger=auto outcome=error-after:init"
            in _read_hook_log(tmp_path)
        )

    def test_session_id_newline_is_escaped(self, tmp_path, monkeypatch):
        # A `\n` in the stdin-sourced session_id must NOT split the heartbeat
        # into a forged second line.
        monkeypatch.setattr(pcb, "_root", tmp_path)
        monkeypatch.setattr(
            sys,
            "stdin",
            io.StringIO(json.dumps({"session_id": "real\ninjected outcome=supabase"})),
        )
        assert pcb.main() == 0
        log = _read_hook_log(tmp_path)
        # One physical line — the injected newline is escaped, not honoured.
        assert len(log.splitlines()) == 1
        assert (
            "session=real\\ninjected outcome=supabase "
            "trigger=unknown outcome=no-transcript-path"
        ) in log

    def test_trigger_newline_is_escaped(self, tmp_path, monkeypatch):
        # `trigger` shares session_id's untrusted-stdin provenance and the same
        # sanitizer — a `\n` here must not forge a second heartbeat line either.
        monkeypatch.setattr(pcb, "_root", tmp_path)
        monkeypatch.setattr(
            sys,
            "stdin",
            io.StringIO(
                json.dumps({"session_id": "s", "trigger": "auto\nforged outcome=supabase"})
            ),
        )
        assert pcb.main() == 0
        log = _read_hook_log(tmp_path)
        assert len(log.splitlines()) == 1
        assert "trigger=auto\\nforged outcome=supabase" in log


class TestSanitizeLogField:
    """Direct unit coverage for the security-adjacent `_sanitize_log_field`."""

    def test_newline_escaped(self):
        assert pcb._sanitize_log_field("a\nb") == "a\\nb"

    def test_carriage_return_escaped(self):
        # CR alone (no LF) overwrites the line on legacy terminals — a distinct
        # escape path from `\n` that the through-main tests don't exercise.
        assert pcb._sanitize_log_field("a\rb") == "a\\rb"

    def test_crlf_escaped(self):
        assert pcb._sanitize_log_field("a\r\nb") == "a\\r\\nb"

    def test_clean_value_untouched(self):
        assert pcb._sanitize_log_field("plain-value") == "plain-value"

    def test_non_str_is_coerced(self):
        # stdin JSON can yield a non-str (null/number); the annotation is widened
        # to object precisely so the str() coerce is the documented contract.
        assert pcb._sanitize_log_field(None) == "None"
        assert pcb._sanitize_log_field(42) == "42"


# ---------------------------------------------------------------------------
# _sanitize_session_id + _bump_compaction_count (compaction-generation counter)
# ---------------------------------------------------------------------------
class TestSanitizeSessionId:
    def test_strips_path_separators_and_traversal(self):
        assert pcb._sanitize_session_id("../../etc/passwd") == "etcpasswd"
        assert pcb._sanitize_session_id(r"a/b\c") == "abc"

    def test_keeps_id_chars(self):
        assert pcb._sanitize_session_id("sess-ABC_123") == "sess-ABC_123"

    def test_empty_falls_back(self):
        assert pcb._sanitize_session_id("") == "unknown-session"
        assert pcb._sanitize_session_id("///") == "unknown-session"


class TestBumpCompactionCount:
    def test_increments_from_zero(self, tmp_path, monkeypatch):
        monkeypatch.setattr(pcb.Path, "home", lambda: tmp_path)
        assert pcb._bump_compaction_count("sess-1") == 1
        assert pcb._bump_compaction_count("sess-1") == 2
        assert pcb._bump_compaction_count("sess-1") == 3

    def test_per_session_isolation(self, tmp_path, monkeypatch):
        monkeypatch.setattr(pcb.Path, "home", lambda: tmp_path)
        assert pcb._bump_compaction_count("sess-a") == 1
        assert pcb._bump_compaction_count("sess-b") == 1
        assert pcb._bump_compaction_count("sess-a") == 2

    def test_corrupt_counter_resets(self, tmp_path, monkeypatch):
        monkeypatch.setattr(pcb.Path, "home", lambda: tmp_path)
        d = tmp_path / ".claude" / "compaction-counts"
        d.mkdir(parents=True)
        (d / "sess-x.txt").write_text("garbage", encoding="utf-8")
        # Unparseable prior value -> treated as 0, so next bump yields 1.
        assert pcb._bump_compaction_count("sess-x") == 1
