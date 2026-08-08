"""Budget-aware session-context assembly guard (#1271).

Runs via ci-meta.yml (``pytest tests/ci/``) on every PR. Asserts the #1271
assembly contract by importing the REAL assembler from
``scripts/session-context.py`` — never a reimplementation (that is the
anti-pattern the old byte-cap tests used to commit, #1271 AC5 / research E.1).

What it locks in:

- Clean-path and compact-path assembly each emit < 9,500 chars with every
  surviving section present (AC1).
- Compact path delivers the durable layer (always-load, user profile, working
  state, goals) even when the one-line reminders drop (AC2).
- Drop-priority order under induced overflow; dropped sections named on the
  ``dropped:`` line (AC3).
- Every run self-logs its emitted size (AC4); the log lives at
  ``.claude/logs/session-context-size.jsonl`` (gitignored — device-local).
- Every self-log row carries the ``session_id`` + ``project`` join key (#1275),
  without which the log is a denominator nothing can be joined against.

The former CONTEXT.md push (compressed Invariants + Glossary category index,
AC6/AC7) was retired by #1417 — the Invariants now ride `@import` from
`docs/context/invariants.md`, delivered outside this assembler entirely (never
enters the drop-priority ladder), and the Glossary category index was retired
outright by #1418 in favor of a pull pointer.
Coverage for that delivery path lives in
tests/ci/test_context_extraction_guard.py.
"""

from __future__ import annotations

import importlib.util
import json
import re
import sys
import types
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


def _stub_module(name: str, attrs: dict) -> None:
    mod = sys.modules.get(name)
    if mod is not None and all(hasattr(mod, a) for a in attrs):
        return
    new = types.ModuleType(name)
    for attr, value in attrs.items():
        setattr(new, attr, value)
    sys.modules[name] = new


_stub_module("supabase", {"create_client": lambda *a, **k: None})
_stub_module("dotenv", {"load_dotenv": lambda *a, **k: None})


def _load_assembly_module():
    path = REPO_ROOT / "scripts" / "session-context.py"
    spec = importlib.util.spec_from_file_location("session_context_budget", path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["session_context_budget"] = mod
    spec.loader.exec_module(mod)
    return mod


sc = _load_assembly_module()


class TestCleanPath:
    """AC1 clean path: no compact resume → every section present, < 9,500."""

    def test_clean_path_fits_budget_with_all_sections(self):
        sections = [
            (sc._PRIORITY_ALWAYS_LOAD, "always_load", "## Always-Load Rules\n" + "a" * 1200, []),
            (sc._PRIORITY_USER_PROFILE, "user_profile", "## User Profile\n" + "u" * 500, []),
            (
                sc._PRIORITY_WORKING_STATE,
                "working_state",
                "## Working State (jarvis)\n" + "w" * 700,
                [],
            ),
            (sc._PRIORITY_GOALS, "goals", "## Active Goals (2)\n" + "g" * 250, []),
            (sc._PRIORITY_REMINDER, "pending_review", "**Pending memory candidates:** 3", []),
            (sc._PRIORITY_REMINDER, "milestone_sweep", "## Architecture Sweep\n- Milestone #1", []),
        ]
        output, dropped, emitted_ids, chars = sc.assemble_sections(sections)
        assert chars < sc.ASSEMBLY_BUDGET_CHARS
        assert dropped == []
        for name in (
            "always_load",
            "user_profile",
            "working_state",
            "goals",
            "pending_review",
            "milestone_sweep",
        ):
            assert name not in dropped
        # Delivery: every section's content is actually emitted.
        assert output.count("## Always-Load Rules") == 1
        assert output.count("## User Profile") == 1
        assert output.count("## Working State (jarvis)") == 1
        assert output.count("## Active Goals (2)") == 1


class TestCompactPath:
    """AC1 + AC2: compact resume delivers the durable layer under 9,500."""

    def test_compact_path_delivers_durable_layer(self):
        sections = [
            (sc._PRIORITY_RECOVERY, "recovery", "## Pre-Compact Recovery\n" + "s" * 2000, []),
            (sc._PRIORITY_ALWAYS_LOAD, "always_load", "## Always-Load Rules\n" + "a" * 1200, []),
            (sc._PRIORITY_USER_PROFILE, "user_profile", "## User Profile\n" + "u" * 500, []),
            (
                sc._PRIORITY_WORKING_STATE,
                "working_state",
                "## Working State (jarvis)\n" + "w" * 700,
                [],
            ),
            (sc._PRIORITY_GOALS, "goals", "## Active Goals (2)\n" + "g" * 250, []),
            (sc._PRIORITY_REMINDER, "pending_review", "**Pending memory candidates:** 3", []),
            (sc._PRIORITY_REMINDER, "milestone_sweep", "## Architecture Sweep\n- Milestone #1", []),
        ]
        output, dropped, emitted_ids, chars = sc.assemble_sections(sections)
        assert chars < sc.ASSEMBLY_BUDGET_CHARS
        # The durable layer + recovery survive; the reminders cut first.
        for name in ("recovery", "always_load", "user_profile", "working_state", "goals"):
            assert name not in dropped
        assert "Pre-Compact Recovery" in output
        assert "Always-Load Rules" in output
        assert "User Profile" in output
        assert "Working State (jarvis)" in output
        assert "Active Goals (2)" in output


class TestDropPriority:
    """AC3: the ranked drop order under induced overflow."""

    def test_drop_priority_order_under_overflow(self):
        sections = [
            (sc._PRIORITY_RECOVERY, "recovery", "## Pre-Compact Recovery\n" + "r" * 1000, []),
            (sc._PRIORITY_ALWAYS_LOAD, "always_load", "## Always-Load Rules\n" + "a" * 1000, []),
            (sc._PRIORITY_USER_PROFILE, "user_profile", "## User Profile\n" + "u" * 1000, []),
            (sc._PRIORITY_WORKING_STATE, "working_state", "## Working State\n" + "w" * 1000, []),
            (sc._PRIORITY_GOALS, "goals", "## Active Goals\n" + "g" * 1000, []),
            (sc._PRIORITY_REMINDER, "pending", "**pending**", []),
            (sc._PRIORITY_REMINDER, "sweep", "**sweep**", []),
            (sc._PRIORITY_REMINDER, "drift", "**drift**", []),
        ]
        output, dropped, emitted_ids, chars = sc.assemble_sections(sections, budget_chars=2000)
        assert dropped == [
            "drift",
            "sweep",
            "pending",  # reminders — lowest priority, later first
            "goals",
            "working_state",
            "user_profile",
            "always_load",
        ]
        # Recovery is priority 0 — the last survivor.
        assert "recovery" not in dropped
        assert "Pre-Compact Recovery" in output
        # Dropped sections are named on the dropped: line.
        dropped_line = next(line for line in output.splitlines() if line.startswith("dropped: "))
        for name in dropped:
            assert name in dropped_line

    def test_emitted_ids_exclude_dropped_sections(self):
        """Only KEPT sections' memory ids are returned — a dropped section is
        never fed to _touch_accessed (#1271 C.1 row 6)."""
        sections = [
            (0, "keep", "## K\n" + "k" * 500, ["id-keep"]),
            (5, "drop", "## D\n" + "d" * 500, ["id-drop"]),
        ]
        output, dropped, emitted_ids, chars = sc.assemble_sections(sections, budget_chars=800)
        assert "drop" in dropped
        assert emitted_ids == ["id-keep"]
        assert "id-drop" not in emitted_ids


class TestMainTriggerGating:
    """#1460: main() drops the duplicated block on the `compact` trigger.

    Recovery + always-load + working-state are the durable layer and stay
    unconditional (already covered elsewhere). User profile, active goals,
    and every one-line reminder duplicate what the just-compacted transcript
    summary + Pre-Compact Recovery pointer already carry, so on `compact`
    they must not be re-emitted. `startup` keeps the full block.
    """

    def _run_main(self, monkeypatch, capsys, *, trigger):
        monkeypatch.setattr(sys, "argv", ["session-context.py"])
        monkeypatch.setenv("SUPABASE_URL", "https://x.supabase.co")
        monkeypatch.setenv("SUPABASE_KEY", "anon-key")
        monkeypatch.setattr(sc, "create_client", lambda *a, **k: object())
        monkeypatch.setattr(
            sc,
            "_read_hook_input",
            lambda: {"source": trigger, "session_id": "sess-1", "cwd": "", "transcript_path": ""},
        )
        monkeypatch.setattr(sc, "_detect_project", lambda: ("jarvis", None))
        # Recovery's own snapshot lookups are out of scope here (covered by
        # test_session_context_recovery.py) — no snapshot found, section absent.
        monkeypatch.setattr(sc, "_load_snapshot_from_supabase", lambda *a, **k: None)
        monkeypatch.setattr(sc, "_load_snapshot_from_local", lambda *a, **k: None)

        monkeypatch.setattr(
            sc,
            "_query_memories",
            lambda client, *, mem_type, limit, extra_filter=None, compact=False: (
                (f"- stub {mem_type}\n", ["id-1"])
                if mem_type == "user"
                else ("- working\n", ["id-2"])
            ),
        )
        monkeypatch.setattr(
            sc, "_query_always_load", lambda client, *, compact=False: ("- always\n", [])
        )
        monkeypatch.setattr(sc, "_query_goals", lambda client: "## Active Goals (1)\n- goal\n")
        monkeypatch.setattr(sc, "_query_pending_review_count", lambda client, project: 3)
        monkeypatch.setattr(
            sc, "_check_milestone_sweep", lambda repo=None: "## Architecture Sweep\n- x"
        )
        monkeypatch.setattr(sc, "_check_mirror_drift", lambda: "## Mirror Drift\n- x")
        monkeypatch.setattr(sc, "_check_env_drift", lambda: "## Environment Drift\n- x")
        monkeypatch.setattr(sc, "_check_mcp_failures", lambda: "## MCP Failures\n- x")

        sc.main()
        return capsys.readouterr().out

    def test_startup_keeps_full_block(self, monkeypatch, capsys):
        out = self._run_main(monkeypatch, capsys, trigger="startup")
        assert "## User Profile" in out
        assert "## Active Goals" in out
        assert "Pending memory candidates" in out
        assert "## Architecture Sweep" in out
        assert "## Mirror Drift" in out
        assert "## Environment Drift" in out
        assert "## MCP Failures" in out
        # Durable layer present too.
        assert "Always-Load Rules" in out
        assert "Working State (jarvis)" in out

    def test_compact_drops_profile_goals_and_reminders(self, monkeypatch, capsys):
        out = self._run_main(monkeypatch, capsys, trigger="compact")
        assert "## User Profile" not in out
        assert "## Active Goals" not in out
        assert "Pending memory candidates" not in out
        assert "## Architecture Sweep" not in out
        assert "## Mirror Drift" not in out
        assert "## Environment Drift" not in out
        assert "## MCP Failures" not in out
        # Durable layer survives the slim-down.
        assert "Always-Load Rules" in out
        assert "Working State (jarvis)" in out

    def test_compact_output_under_6kb(self, monkeypatch, capsys):
        """AC2: measured compact-trigger output <= 6 KB with populated working state."""
        out = self._run_main(monkeypatch, capsys, trigger="compact")
        assert len(out.encode("utf-8")) <= 6 * 1024


class TestSelfLog:
    """AC4: every run appends its emitted size to the self-log."""

    def test_self_log_appends_json_line(self, tmp_path, monkeypatch):
        log_path = tmp_path / "session-context-size.jsonl"
        monkeypatch.setattr(sc, "_SELF_LOG_PATH", log_path)
        sc._self_log(1234, ["reminder"], True)
        lines = log_path.read_text(encoding="utf-8").splitlines()
        assert len(lines) == 1
        entry = json.loads(lines[0])
        assert entry["emitted_chars"] == 1234
        assert entry["budget_chars"] == sc.ASSEMBLY_BUDGET_CHARS
        assert entry["dropped"] == ["reminder"]
        assert entry["compact_resume"] is True

    def test_self_log_never_raises(self, tmp_path, monkeypatch):
        """A log write failure must not break session start."""
        blocked = tmp_path / "blocked"
        blocked.write_text("i am a file", encoding="utf-8")
        monkeypatch.setattr(sc, "_SELF_LOG_PATH", blocked / "x.jsonl")
        sc._self_log(0, [], False)  # must not raise

    def test_log_location_documented(self):
        """The self-log is under .claude/logs/ — gitignored, device-local."""
        assert ".claude" in str(sc._SELF_LOG_PATH)
        assert "logs" in str(sc._SELF_LOG_PATH)
        assert sc._SELF_LOG_PATH.name == "session-context-size.jsonl"


class TestSelfLogJoinKey:
    """#1275 enabling AC: every self-log row carries the pull-rate join key.

    The self-log is written per-WORKTREE, not per-device (797 rows in the
    worktree copy vs 956 in the main checkout on 2026-08-07). Without
    `session_id` and `project` on each row there is no key to join transcript
    pulls (the numerator) against context-assembly runs (the denominator), so
    the pull rate is uncomputable and #1275's numeric target cannot ship as a
    number anyone can produce. This is the enabling AC: the rest of that slice
    is unmeasurable until it lands.

    Both keys are always PRESENT, null when unknown -- a stable row schema
    means the reader can join on them without probing for their existence, and
    a null is honest about "this run could not be attributed" in a way a
    missing key is not.
    """

    def _row(self, tmp_path, monkeypatch, **kwargs):
        log_path = tmp_path / "session-context-size.jsonl"
        monkeypatch.setattr(sc, "_SELF_LOG_PATH", log_path)
        sc._self_log(1234, [], False, **kwargs)
        return json.loads(log_path.read_text(encoding="utf-8").splitlines()[0])

    def test_row_carries_session_id_and_project(self, tmp_path, monkeypatch):
        entry = self._row(tmp_path, monkeypatch, session_id="abc-123_XY", project="jarvis")
        assert entry["session_id"] == "abc-123_XY"
        assert entry["project"] == "jarvis"

    def test_keys_present_as_null_when_unknown(self, tmp_path, monkeypatch):
        # A run outside any known repo, or one whose hook input carried no
        # session_id. The reader must still see a well-formed row.
        entry = self._row(tmp_path, monkeypatch)
        assert "session_id" in entry and entry["session_id"] is None
        assert "project" in entry and entry["project"] is None

    def test_session_id_is_sanitised_not_trusted(self, tmp_path, monkeypatch):
        # session_id arrives over stdin from Claude Code. The module already
        # allowlists it everywhere it is used as a key (_safe_session_id); the
        # log is not the one place that trusts it raw. An id that fails the
        # allowlist cannot join against anything anyway, so it logs as null
        # rather than as garbage.
        entry = self._row(tmp_path, monkeypatch, session_id="../../etc/passwd", project="jarvis")
        assert entry["session_id"] is None
        assert entry["project"] == "jarvis"

    def test_empty_session_id_logs_as_null(self, tmp_path, monkeypatch):
        # main() defaults a missing id to "" rather than None -- that must not
        # reach the log as an empty-string key that silently joins nothing.
        entry = self._row(tmp_path, monkeypatch, session_id="", project="jarvis")
        assert entry["session_id"] is None

    def test_join_key_does_not_disturb_the_existing_row_shape(self, tmp_path, monkeypatch):
        # The size-trend fields are what #1271 AC4 shipped; adding the join key
        # must be additive.
        entry = self._row(tmp_path, monkeypatch, session_id="s1", project="jarvis")
        assert entry["emitted_chars"] == 1234
        assert entry["budget_chars"] == sc.ASSEMBLY_BUDGET_CHARS
        assert entry["dropped"] == []
        assert entry["compact_resume"] is False
        assert "ts" in entry

    def test_resumed_runs_share_the_pair_and_are_disambiguated_by_ts(self, tmp_path, monkeypatch):
        """`(session_id, project)` is not unique -- `ts` is the third component.

        Claude Code reuses session ids across `--resume` invocations (the same
        fact `PRE_COMPACT_FRESHNESS_MINUTES` exists to cope with), so one
        resumed session emits several rows sharing the pair. A pull-rate reader
        keyed on the pair alone would collapse them into one inflated
        denominator. Every row must therefore carry a parseable, timezone-aware
        `ts`, and rows must be appended in non-decreasing time order so the
        reader can window transcript pulls against them.
        """
        from datetime import datetime

        log_path = tmp_path / "session-context-size.jsonl"
        monkeypatch.setattr(sc, "_SELF_LOG_PATH", log_path)
        sc._self_log(1, [], False, session_id="resumed", project="jarvis")
        sc._self_log(2, [], True, session_id="resumed", project="jarvis")

        rows = [json.loads(ln) for ln in log_path.read_text(encoding="utf-8").splitlines()]
        assert len(rows) == 2
        assert {(r["session_id"], r["project"]) for r in rows} == {("resumed", "jarvis")}

        stamps = [datetime.fromisoformat(r["ts"]) for r in rows]
        assert all(s.tzinfo is not None for s in stamps), (
            "ts must be timezone-aware -- a naive stamp cannot be windowed "
            "against transcript pulls across devices"
        )
        assert stamps == sorted(stamps)

    def test_still_never_raises_with_the_new_args(self, tmp_path, monkeypatch):
        blocked = tmp_path / "blocked"
        blocked.write_text("i am a file", encoding="utf-8")
        monkeypatch.setattr(sc, "_SELF_LOG_PATH", blocked / "x.jsonl")
        sc._self_log(0, [], False, session_id="s1", project="jarvis")

    def test_every_call_site_passes_the_join_key(self):
        """Wiring: a call site that omits the key writes an unjoinable row.

        There are two `_self_log` calls in main() -- the no-credentials early
        return and the normal path. The early-return one is the easy one to
        forget, and it is exactly the run a `--resume` without Supabase creds
        produces, so its rows would silently dilute the denominator.
        """
        source = (REPO_ROOT / "scripts" / "session-context.py").read_text(encoding="utf-8")
        call_sites = []
        for match in re.finditer(r"(?<!def )_self_log\(", source):
            # Walk to the matching close paren -- the calls span several lines,
            # so a per-line scan would read the argument list as absent.
            depth, i = 1, match.end()
            while depth and i < len(source):
                depth += {"(": 1, ")": -1}.get(source[i], 0)
                i += 1
            call_sites.append(" ".join(source[match.start() : i].split()))
        assert call_sites, "no _self_log call sites found -- test is looking in the wrong place"
        for site in call_sites:
            assert "session_id=" in site and "project=" in site, (
                f"_self_log call site omits the join key: {site!r}. Rows without "
                "session_id + project cannot be joined against transcript pulls "
                "(#1275 enabling AC)."
            )
