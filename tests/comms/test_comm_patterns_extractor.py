"""Extractor + transcript-parser tests.

Covers the #581 acceptance criteria:
  * watermark idempotency — re-run on same session produces zero duplicates
  * sandcastle / headless skip — no rows written
  * scrubber wired into row build — redacted flag + placeholder propagate
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

from comm_patterns.extractor import extract_session
from comm_patterns.store import InMemoryStore
from comm_patterns.transcript import is_headless_cwd, parse_turns


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    with path.open("w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")


def _user_msg(text: str, ts: str = "2026-05-10T12:00:00Z") -> dict:
    return {
        "type": "user",
        "timestamp": ts,
        "message": {"content": text},
    }


def _asst_msg(text: str, ts: str = "2026-05-10T12:00:00Z") -> dict:
    return {
        "type": "assistant",
        "timestamp": ts,
        "message": {"content": [{"type": "text", "text": text}]},
    }


def _tool_result(ts: str = "2026-05-10T12:00:00Z") -> dict:
    return {
        "type": "user",
        "timestamp": ts,
        "message": {"content": [{"type": "tool_result", "content": "ok"}]},
    }


def _make_classifier(label="correction_wrong_direction", confidence=0.9, subtype=None):
    """Return a deterministic fake classifier."""

    def classify(user_text, prev_assistant_text):
        return {
            "primary_label": label,
            "subtype": subtype,
            "confidence": confidence,
            "anchor_quote": user_text[:200],
        }

    return classify


# ---------------------------------------------------------------------------
# transcript.parse_turns / is_headless_cwd
# ---------------------------------------------------------------------------


def test_is_headless_cwd_detects_sandcastle():
    assert is_headless_cwd("/repo/.sandcastle/main") is True
    assert is_headless_cwd("C:\\Users\\jdoe\\GitHub\\jarvis\\.sandcastle\\worktree") is True


def test_is_headless_cwd_detects_worktrees():
    assert is_headless_cwd("/repo/.claude-worktrees/keen-tesla-bb41d9") is True
    assert is_headless_cwd("C:\\Users\\jdoe\\GitHub\\jarvis\\worktrees\\foo") is True


def test_is_headless_cwd_passes_normal_repo():
    assert is_headless_cwd("/Users/jdoe/GitHub/jarvis") is False
    assert is_headless_cwd("C:\\Users\\jdoe\\GitHub\\jarvis") is False
    assert is_headless_cwd("") is False
    assert is_headless_cwd(None) is False


def test_parse_turns_filters_tool_results_and_command_echoes(tmp_path: Path):
    fp = tmp_path / "session.jsonl"
    _write_jsonl(
        fp,
        [
            _asst_msg("hi"),
            _user_msg("real question 1"),
            _tool_result(),
            _asst_msg("answer 1"),
            _user_msg("<command-name>/foo</command-name>"),  # command echo — filtered
            _user_msg("real question 2"),
        ],
    )
    turns = parse_turns(fp)
    assert [t.user_text for t in turns] == ["real question 1", "real question 2"]
    # message_idx is the raw row offset in the jsonl.
    assert turns[0].message_idx == 1
    assert turns[1].message_idx == 5
    # prev_assistant_text walks back to the most recent assistant text.
    assert turns[0].prev_assistant_text == "hi"
    assert turns[1].prev_assistant_text == "answer 1"


def test_parse_turns_returns_empty_for_missing_file(tmp_path: Path):
    assert parse_turns(tmp_path / "missing.jsonl") == []


# ---------------------------------------------------------------------------
# extract_session — happy path + idempotency
# ---------------------------------------------------------------------------


def test_extract_session_writes_rows_for_classified_turns(tmp_path: Path):
    fp = tmp_path / "s.jsonl"
    _write_jsonl(
        fp,
        [
            _asst_msg("did X"),
            _user_msg("не так, нужно было Y"),
            _asst_msg("ok did Y"),
            _user_msg("правильно"),
        ],
    )
    store = InMemoryStore()
    stats = extract_session(
        device="dev1",
        session_id="abc",
        transcript_path=fp,
        cwd="/Users/jdoe/GitHub/jarvis",
        store=store,
        classify_fn=_make_classifier(),
        source_provenance="extractor:stop-hook",
    )
    assert stats["skipped"] is None
    assert stats["rows_written"] == 2
    assert len(store.rows) == 2
    assert all(r["device"] == "dev1" and r["session_id"] == "abc" for r in store.rows)
    assert all(r["source_provenance"] == "extractor:stop-hook" for r in store.rows)
    assert store.get_watermark("dev1", "abc") == 3


def test_re_run_produces_zero_duplicate_rows(tmp_path: Path):
    fp = tmp_path / "s.jsonl"
    _write_jsonl(
        fp,
        [
            _asst_msg("did X"),
            _user_msg("не так"),
            _asst_msg("did Y"),
            _user_msg("опять не так"),
        ],
    )
    store = InMemoryStore()
    common = dict(
        device="dev1",
        session_id="s1",
        transcript_path=fp,
        cwd="/Users/jdoe/GitHub/jarvis",
        store=store,
        classify_fn=_make_classifier(),
        source_provenance="extractor:stop-hook",
    )
    stats1 = extract_session(**common)
    stats2 = extract_session(**common)
    assert stats1["rows_written"] == 2
    assert stats2["rows_written"] == 0
    assert stats2["turns_classified"] == 0  # watermark already covers all
    assert len(store.rows) == 2


def test_partial_run_advances_watermark_then_resumes(tmp_path: Path):
    """If a transcript grows after the first extraction, a re-run picks up
    only the new turns. Idempotency for new content."""
    fp = tmp_path / "s.jsonl"
    _write_jsonl(
        fp,
        [
            _asst_msg("a1"),
            _user_msg("u1"),
        ],
    )
    store = InMemoryStore()
    common = dict(
        device="dev1",
        session_id="growing",
        cwd="/Users/jdoe/GitHub/jarvis",
        store=store,
        classify_fn=_make_classifier(),
        source_provenance="extractor:stop-hook",
    )
    extract_session(transcript_path=fp, **common)
    assert len(store.rows) == 1

    # Append more turns.
    with fp.open("a", encoding="utf-8") as f:
        f.write(json.dumps(_asst_msg("a2")) + "\n")
        f.write(json.dumps(_user_msg("u2")) + "\n")

    extract_session(transcript_path=fp, **common)
    assert len(store.rows) == 2
    user_texts = sorted(r["anchor_quote"] for r in store.rows)
    assert user_texts == ["u1", "u2"]


# ---------------------------------------------------------------------------
# Headless / sandcastle skip
# ---------------------------------------------------------------------------


def test_extractor_skips_sandcastle_cwd(tmp_path: Path):
    fp = tmp_path / "s.jsonl"
    _write_jsonl(fp, [_asst_msg("a"), _user_msg("u")])
    store = InMemoryStore()
    stats = extract_session(
        device="dev1",
        session_id="sand",
        transcript_path=fp,
        cwd="C:\\Users\\jdoe\\GitHub\\jarvis\\.sandcastle\\foo",
        store=store,
        classify_fn=_make_classifier(),
        source_provenance="extractor:stop-hook",
    )
    assert stats["skipped"] == "headless_cwd"
    assert stats["rows_written"] == 0
    assert store.rows == []


def test_parse_turns_filters_sidechain_rows(tmp_path: Path):
    """Sidechain rows are subagent traffic — they must not contaminate
    pattern aggregates (review #584 finding 12). Highest data-corruption
    risk because subagent prompts often contain user-style strings."""
    fp = tmp_path / "session.jsonl"
    _write_jsonl(
        fp,
        [
            _asst_msg("hi"),
            {**_user_msg("subagent inner prompt"), "isSidechain": True},
            _user_msg("real user reply"),
        ],
    )
    turns = parse_turns(fp)
    assert [t.user_text for t in turns] == ["real user reply"]


def test_parse_turns_filters_filter_variants(tmp_path: Path):
    """Each filter variant in _is_real_user_message has its own bypass risk
    (review #584 finding 14). Cover all four shapes."""
    fp = tmp_path / "session.jsonl"
    _write_jsonl(
        fp,
        [
            _asst_msg("a"),
            _user_msg("<command-name>/foo</command-name>"),
            _user_msg("<scheduled-task>x</scheduled-task>"),
            _user_msg("[Request interrupted by user]"),
            _user_msg("Base directory for this skill: C:\\foo"),
            _user_msg("This session is being continued from a previous conversation"),
            _user_msg("real question"),
        ],
    )
    turns = parse_turns(fp)
    assert [t.user_text for t in turns] == ["real question"]


def test_extractor_skips_session_with_no_user_messages(tmp_path: Path):
    """Headless / scripted runs may have only assistant text + tool results."""
    fp = tmp_path / "s.jsonl"
    _write_jsonl(
        fp,
        [
            _asst_msg("hello"),
            _tool_result(),
            _asst_msg("done"),
        ],
    )
    store = InMemoryStore()
    stats = extract_session(
        device="dev1",
        session_id="no-user",
        transcript_path=fp,
        cwd="/Users/jdoe/GitHub/jarvis",
        store=store,
        classify_fn=_make_classifier(),
        source_provenance="extractor:stop-hook",
    )
    assert stats["skipped"] == "no_user_messages"
    assert store.rows == []


# ---------------------------------------------------------------------------
# Scrubber integration
# ---------------------------------------------------------------------------


def test_extractor_scrubs_anchor_and_sets_redacted_flag(tmp_path: Path):
    fp = tmp_path / "s.jsonl"
    _write_jsonl(
        fp,
        [
            _asst_msg("here's the key"),
            _user_msg("AKIAABCDEFGHIJKLMNOP is the AWS key — fix it"),
        ],
    )
    store = InMemoryStore()
    stats = extract_session(
        device="dev1",
        session_id="redact",
        transcript_path=fp,
        cwd="/Users/jdoe/GitHub/jarvis",
        store=store,
        classify_fn=_make_classifier(),
        source_provenance="extractor:stop-hook",
    )
    assert stats["rows_written"] == 1
    row = store.rows[0]
    assert row["redacted"] is True
    assert "AKIAABCDEFGHIJKLMNOP" not in row["anchor_quote"]
    assert "[REDACTED:secret:aws-key]" in row["anchor_quote"]


# ---------------------------------------------------------------------------
# Confidence / null-label handling
# ---------------------------------------------------------------------------


def test_low_confidence_is_skipped_but_watermark_advances(tmp_path: Path):
    fp = tmp_path / "s.jsonl"
    _write_jsonl(fp, [_asst_msg("a"), _user_msg("ну ладно")])
    store = InMemoryStore()
    stats = extract_session(
        device="dev1",
        session_id="low",
        transcript_path=fp,
        cwd="/Users/jdoe/GitHub/jarvis",
        store=store,
        classify_fn=_make_classifier(label="affirmation", confidence=0.3),
        source_provenance="extractor:stop-hook",
    )
    assert stats["rows_written"] == 0
    assert stats["low_confidence_skipped"] == 1
    assert store.get_watermark("dev1", "low") == 1


def test_confidence_boundary_at_threshold_writes_row(tmp_path: Path):
    """Boundary: confidence == 0.5 should pass — the threshold is strict <
    (review #584 finding 15). Without an explicit test, a refactor to ≤
    silently flips the contract."""
    fp = tmp_path / "s.jsonl"
    _write_jsonl(fp, [_asst_msg("a"), _user_msg("ладно")])
    store = InMemoryStore()
    stats = extract_session(
        device="dev1",
        session_id="boundary",
        transcript_path=fp,
        cwd="/Users/jdoe/GitHub/jarvis",
        store=store,
        classify_fn=_make_classifier(label="affirmation", confidence=0.5),
        source_provenance="extractor:stop-hook",
    )
    assert stats["rows_written"] == 1
    assert stats["low_confidence_skipped"] == 0


def test_null_label_is_skipped_but_watermark_advances(tmp_path: Path):
    fp = tmp_path / "s.jsonl"
    _write_jsonl(fp, [_asst_msg("a"), _user_msg("just a question")])
    store = InMemoryStore()

    def null_classifier(user_text, prev):
        return {"primary_label": None, "subtype": None, "confidence": 0.0, "anchor_quote": user_text}

    stats = extract_session(
        device="dev1",
        session_id="null",
        transcript_path=fp,
        cwd="/Users/jdoe/GitHub/jarvis",
        store=store,
        classify_fn=null_classifier,
        source_provenance="extractor:stop-hook",
    )
    assert stats["rows_written"] == 0
    assert stats["no_pattern_skipped"] == 1
    assert store.get_watermark("dev1", "null") == 1


def test_partial_failure_does_not_skip_failed_turn_on_next_run(tmp_path: Path):
    """Mid-pass classifier failure must not silently drop the failing turn.
    If turn N fails (None) and turn N+2 is next, watermark stays at the last
    contiguously-successful turn so the next run retries from N."""
    fp = tmp_path / "s.jsonl"
    _write_jsonl(
        fp,
        [
            _asst_msg("a"),
            _user_msg("u1"),  # idx 1 — succeeds
            _asst_msg("b"),
            _user_msg("u2"),  # idx 3 — flaky: first None, then success
            _asst_msg("c"),
            _user_msg("u3"),  # idx 5 — would-be skipped if loop didn't halt
        ],
    )
    store = InMemoryStore()
    flaky_calls = {"n": 0}

    def flaky(user_text, prev):
        if user_text == "u2":
            flaky_calls["n"] += 1
            if flaky_calls["n"] == 1:
                return None  # first attempt fails
        return {"primary_label": "affirmation", "subtype": None,
                "confidence": 0.9, "anchor_quote": user_text}

    common = dict(
        device="dev1",
        session_id="partial",
        transcript_path=fp,
        cwd="/Users/jdoe/GitHub/jarvis",
        store=store,
        classify_fn=flaky,
        source_provenance="extractor:stop-hook",
    )
    pass1 = extract_session(**common)
    # u1 succeeded; u2 failed → loop halted; u3 untouched.
    assert pass1["rows_written"] == 1
    assert pass1["classifier_errors"] == 1
    assert store.get_watermark("dev1", "partial") == 1  # only u1 confirmed

    # Second pass — flaky returns success this time.
    pass2 = extract_session(**common)
    assert pass2["rows_written"] == 2  # u2 and u3
    assert store.get_watermark("dev1", "partial") == 5
    # u2 anchor must appear — the bug we're guarding against would have
    # left it permanently dropped.
    anchors = sorted(r["anchor_quote"] for r in store.rows)
    assert anchors == ["u1", "u2", "u3"]


def test_wall_clock_budget_aborts_loop_and_preserves_watermark(tmp_path: Path):
    """If a session has too many turns to process within max_wall_seconds,
    the loop aborts and the watermark sits at the last successfully-
    processed turn. The next pass picks up from there."""
    fp = tmp_path / "s.jsonl"
    rows = []
    for i in range(5):
        rows.append(_asst_msg(f"a{i}"))
        rows.append(_user_msg(f"u{i}"))
    _write_jsonl(fp, rows)
    store = InMemoryStore()

    class _MockClock:
        def __init__(self, start: float = 0.0) -> None:
            self._now = start

        def monotonic(self) -> float:
            return self._now

        def sleep(self, secs: float) -> None:
            self._now += secs

    clock = _MockClock()

    def slow(user_text, prev):
        clock.sleep(0.05)
        return {"primary_label": "affirmation", "subtype": None,
                "confidence": 0.9, "anchor_quote": user_text}

    with patch("comm_patterns.extractor.time.monotonic", clock.monotonic):
        stats = extract_session(
            device="dev1",
            session_id="wall",
            transcript_path=fp,
            cwd="/Users/jdoe/GitHub/jarvis",
            store=store,
            classify_fn=slow,
            source_provenance="extractor:stop-hook",
            max_wall_seconds=0.1,  # cap forces early break
        )
    assert stats["wall_clock_aborted"] is True
    # Some rows written, but not all 5 — and the watermark covers exactly
    # what was written, so the next pass resumes cleanly.
    assert 0 < stats["rows_written"] < 5


def test_classifier_returning_none_does_not_advance_watermark(tmp_path: Path):
    """A None return from classify_fn (JSON parse failure) is
    treated as transient — the watermark stays put so the next run retries.
    Distinct from a result with primary_label=None, which is definitive."""
    fp = tmp_path / "s.jsonl"
    _write_jsonl(fp, [_asst_msg("a"), _user_msg("xx")])
    store = InMemoryStore()
    stats = extract_session(
        device="dev1",
        session_id="netfail",
        transcript_path=fp,
        cwd="/Users/jdoe/GitHub/jarvis",
        store=store,
        classify_fn=lambda u, p: None,
        source_provenance="extractor:stop-hook",
    )
    assert stats["rows_written"] == 0
    # Counters distinguish transient (classifier_errors) from definitive
    # (no_pattern_skipped) — same shape as backfill.
    assert stats["classifier_errors"] == 1
    assert stats["connection_errors"] == 0
    assert stats["no_pattern_skipped"] == 0
    # Watermark stays at -1 (the initial value) so the next run retries.
    assert store.get_watermark("dev1", "netfail") == -1


def test_ollama_unavailable_does_not_advance_watermark(tmp_path: Path):
    """OllamaUnavailable exception (connection failure) is treated as
    transient — the watermark stays put so the next run retries. Distinct
    from JSON-parse failures (None return) and definitive patterns."""
    from comm_patterns.classifier import OllamaUnavailable

    fp = tmp_path / "s.jsonl"
    _write_jsonl(fp, [_asst_msg("a"), _user_msg("xx")])
    store = InMemoryStore()

    def boom(u, p):
        raise OllamaUnavailable("connection refused")

    stats = extract_session(
        device="dev1",
        session_id="ollama-fail",
        transcript_path=fp,
        cwd="/Users/jdoe/GitHub/jarvis",
        store=store,
        classify_fn=boom,
        source_provenance="extractor:stop-hook",
    )
    assert stats["rows_written"] == 0
    # connection_errors distinct from classifier_errors (JSON parse)
    assert stats["connection_errors"] == 1
    assert stats["classifier_errors"] == 0
    assert stats["no_pattern_skipped"] == 0
    # Watermark stays at -1 (the initial value) so the next run retries.
    assert store.get_watermark("dev1", "ollama-fail") == -1
    # The returned stats dict is the observable contract for the Stop hook —
    # it must report the same un-advanced watermark, not just the store.
    assert stats["watermark_after"] == -1


def test_ollama_unavailable_mid_pass_preserves_completed_turns(tmp_path: Path):
    """OllamaUnavailable raised *after* a successful turn must not roll the
    watermark back to -1. Turns completed before the connection dropped stay
    confirmed; only the failing turn and those after it retry on the next pass.

    Distinct from test_ollama_unavailable_does_not_advance_watermark, where the
    very first turn fails so there is nothing to preserve. This guards the
    mid-pass case: a single dropped connection must not discard prior good work.
    """
    from comm_patterns.classifier import OllamaUnavailable

    fp = tmp_path / "s.jsonl"
    _write_jsonl(
        fp,
        [
            _asst_msg("a"),
            _user_msg("u1"),  # idx 1 — succeeds, row written
            _asst_msg("b"),
            _user_msg("u2"),  # idx 3 — Ollama drops here
            _asst_msg("c"),
            _user_msg("u3"),  # idx 5 — never reached this pass
        ],
    )
    store = InMemoryStore()

    def flaky(user_text, prev):
        if user_text == "u2":
            raise OllamaUnavailable("connection refused mid-pass")
        return {"primary_label": "affirmation", "subtype": None,
                "confidence": 0.9, "anchor_quote": user_text}

    common = dict(
        device="dev1",
        session_id="midpass",
        transcript_path=fp,
        cwd="/Users/jdoe/GitHub/jarvis",
        store=store,
        classify_fn=flaky,
        source_provenance="extractor:stop-hook",
    )
    stats = extract_session(**common)
    # u1 written before the drop; u2/u3 deferred to the next pass.
    assert stats["rows_written"] == 1
    assert stats["connection_errors"] == 1
    assert stats["classifier_errors"] == 0
    # Watermark covers the completed turn (idx 1), NOT rolled back to -1 —
    # the next run resumes at u2 instead of re-classifying u1.
    assert store.get_watermark("dev1", "midpass") == 1
    # Stats dict (the Stop hook's observable contract) reports the same
    # advanced-but-only-to-idx-1 watermark.
    assert stats["watermark_after"] == 1
    # The one confirmed row is u1.
    assert [r["anchor_quote"] for r in store.rows] == ["u1"]

    # --- Pass 2: Ollama is back — retry picks up u2 and u3 ---
    def healthy(user_text, prev):
        return {"primary_label": "affirmation", "subtype": None,
                "confidence": 0.9, "anchor_quote": user_text}

    common["classify_fn"] = healthy
    stats2 = extract_session(**common)
    assert stats2["rows_written"] == 2  # u2 and u3
    assert stats2["connection_errors"] == 0
    assert store.get_watermark("dev1", "midpass") == 5
    anchors = sorted(r["anchor_quote"] for r in store.rows)
    assert anchors == ["u1", "u2", "u3"]
