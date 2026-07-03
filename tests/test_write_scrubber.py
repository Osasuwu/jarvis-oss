"""Tests for the MCP write-path Tier-2 secret-scrubber gate (#555).

Covers the standalone ``write_scrubber`` helper module plus its integration
into the two write paths (``_handle_store``, ``_handle_record_decision``).

Privacy invariant under test: when a write is blocked, NO value from the
blocked payload appears in the error, the event row, or anywhere else —
only pattern names + fire counts.

Test secrets are constructed dynamically (prefix + entropy concatenation) so
GitHub's static secret scanner does not flag this file — same convention as
tests/test_secret_scrubber.py.
"""

from __future__ import annotations

import asyncio
import json
from unittest.mock import MagicMock

import pytest

import write_scrubber
from server import (
    _handle_store,
    _handle_record_decision,
    _handle_goal_set,
    _handle_goal_update,
    _handle_outcome_record,
    _handle_outcome_update,
)
import server as server_module

from test_record_decision_helpers import make_client


# A realistic OpenAI-style key: prefix + 26 entropy chars (matches
# sk-[A-Za-z0-9]{20,}). Split so the static scanner never sees a whole token.
FAKE_OPENAI_KEY = "sk-proj" + "AbCdEfGhIjKlMnOpQrStUvWxYz"

# One fake per remaining blocking pattern (same split-construction convention as
# tests/test_secret_scrubber.py) so a regression in any single scrubber pattern
# is caught at the write-gate layer, not just for api_key_openai.
# Anthropic key: sk-ant-api03-<entropy>. The `-` after `sk-ant` breaks the
# OpenAI pattern's run at 3 chars, so this is caught ONLY by the dedicated
# api_key_anthropic pattern — the regression this case guards.
FAKE_ANTHROPIC_KEY = "sk-ant-" + "api03-" + "0123456789abcdefghijABCDEFG"
FAKE_GITHUB_TOKEN = "ghp_" + "ABCDEFGHIJKLM" + "NOPQRSTUVWXYZabcdefghij123456"
FAKE_SLACK_TOKEN = "xoxb" + "-1111111111-aaaaaaaaaaaaaaaaaaaa"
FAKE_JWT = "eyJhbGciOiJIUzI1NiJ9" + "." + "eyJzdWIiOiIxMjM0NTY3ODkwIn0" + ".signature_part_here"
FAKE_AWS_KEY = "AKIA" + "IOSFODNN7EXAMPLE"
FAKE_VOYAGE_KEY = "pa-" + "0123456789abcdefghijABCDEFGHIJ0123456789"
FAKE_ENV_BLOCK = "```env\nDB_PASSWORD=supersecretpassword123\n```"

# (pattern name, text that fires exactly that blocking pattern)
# `path_username` is intentionally absent — it is a scrub-and-keep
# normalization, NOT a write-blocking leak (see SCRUB_ONLY_PATTERNS); a
# separate test (``test_user_path_does_not_block``) covers its non-blocking
# behavior. Listing it here would assert the opposite of the intended policy.
BLOCKING_PATTERN_CASES = [
    ("api_key_anthropic", FAKE_ANTHROPIC_KEY),
    ("api_key_openai", FAKE_OPENAI_KEY),
    ("api_key_github", FAKE_GITHUB_TOKEN),
    ("api_key_slack", FAKE_SLACK_TOKEN),
    ("api_key_jwt", FAKE_JWT),
    ("api_key_aws", FAKE_AWS_KEY),
    ("api_key_voyageai", FAKE_VOYAGE_KEY),
    ("env_block", FAKE_ENV_BLOCK),
]


@pytest.fixture(autouse=True)
async def _clear_pending():
    """Isolate the module-level ``_PENDING_BLOCK_LOGS`` set between tests.

    The set pins in-flight detached block-log tasks (GC-pinning, see
    write_scrubber). It is process-global, so without this fixture a task
    scheduled by one test bleeds into the next — making
    ``test_block_event_is_actually_logged_on_async_path`` able to pass
    *vacuously* off a stale task, and letting TestDecisionGate's blocking
    tests leak tasks forward.

    On teardown we DRAIN before clearing: a block-log task wraps
    ``asyncio.to_thread`` and an event-loop teardown cannot cancel the worker
    thread already running the insert. Just ``.clear()``-ing the pins would let
    that thread finish against the next test's mock client (a cross-test bleed).
    Awaiting the snapshot lets every in-flight insert complete first; only then
    do we drop the pins. ``async`` fixture runs under ``asyncio_mode=auto``.
    """
    write_scrubber._PENDING_BLOCK_LOGS.clear()
    yield
    pending = list(write_scrubber._PENDING_BLOCK_LOGS)
    if pending:
        await asyncio.gather(*pending, return_exceptions=True)
    write_scrubber._PENDING_BLOCK_LOGS.clear()


# ── scan_fields ───────────────────────────────────────────────────────────


class TestScanFields:
    def test_clean_fields_no_fires(self):
        fires = write_scrubber.scan_fields(
            {"name": "normal name", "content": "ordinary text", "tags": ["a", "b"]}
        )
        assert fires == {}

    def test_secret_in_str_field_fires(self):
        fires = write_scrubber.scan_fields({"content": f"key is {FAKE_OPENAI_KEY}"})
        assert fires.get("api_key_openai") == 1

    def test_secret_in_list_field_fires(self):
        fires = write_scrubber.scan_fields({"tags": ["fine", f"{FAKE_OPENAI_KEY}"]})
        assert fires.get("api_key_openai") == 1

    def test_counts_aggregate_across_fields(self):
        fires = write_scrubber.scan_fields({"name": FAKE_OPENAI_KEY, "content": FAKE_OPENAI_KEY})
        assert fires.get("api_key_openai") == 2

    def test_non_string_values_skipped(self):
        # ints / None / dicts must not raise — only str + list-of-str scanned.
        fires = write_scrubber.scan_fields(
            {"confidence": 0.9, "flag": None, "meta": {"x": 1}, "content": "clean"}
        )
        assert fires == {}

    def test_scrub_raising_is_contained_per_field(self, monkeypatch):
        """MAJOR-fix: a scrub() crash on one field must fail OPEN (skip that
        field, keep scanning the rest) — never propagate and crash the write.
        The surviving field still contributes its fires, proving containment
        skips only the crashing field and does not abort the whole scan."""

        def _boom(text):
            if "BOOM" in text:
                raise ValueError("scrubber exploded")
            n = text.count(FAKE_OPENAI_KEY)
            return text, ({"api_key_openai": n} if n else {})

        monkeypatch.setattr(write_scrubber, "scrub", _boom)
        # First field raises (skipped), second still fires → the secret in the
        # surviving field is reported despite the sibling crash.
        fires = write_scrubber.scan_fields(
            {"bad": "trigger BOOM here", "good": f"key {FAKE_OPENAI_KEY}"}
        )
        assert fires == {"api_key_openai": 1}

    def test_scrub_raising_does_not_leak_value_to_stderr(self, monkeypatch, capsys):
        """Privacy invariant: the per-field crash log carries the exception
        *type* + field name only — never the raised exception's str() (which
        could embed the input text)."""

        secret_marker = "S3CR3T_" + "PAYLOAD_VALUE"

        def _boom(text):
            raise ValueError(text)  # exception str() == the input text

        monkeypatch.setattr(write_scrubber, "scrub", _boom)
        write_scrubber.scan_fields({"content": secret_marker})
        err = capsys.readouterr().err
        assert "ValueError" in err
        assert "content" in err
        assert secret_marker not in err


# ── rejection_error ───────────────────────────────────────────────────────


class TestRejectionError:
    def test_shape(self):
        payload = json.loads(write_scrubber.rejection_error({"api_key_openai": 1}))
        assert payload["error"] == "secret_pattern_detected"
        assert payload["patterns"] == {"api_key_openai": 1}

    def test_carries_only_names_and_counts_no_value(self):
        body = write_scrubber.rejection_error({"api_key_openai": 1})
        # The literal secret never appears in the rejection string.
        assert FAKE_OPENAI_KEY not in body


# ── log_block_event ───────────────────────────────────────────────────────


class TestLogBlockEvent:
    def test_inserts_counter_event_no_values(self):
        client = MagicMock()
        write_scrubber.log_block_event(client, {"api_key_openai": 2}, write_path="memory_store")

        client.table.assert_called_with("events")
        row = client.table.return_value.insert.call_args.args[0]
        assert row["event_type"] == "mcp_write_scrubber_block"
        # An API-key fire is high-severity for triage indexing (see
        # _event_severity) — a live-key-shaped catch must not be buried at the
        # same priority as an env-block.
        assert row["severity"] == "high"
        assert row["repo"] == "Osasuwu/jarvis"
        assert row["payload"]["patterns"] == {"api_key_openai": 2}
        assert row["payload"]["write_path"] == "memory_store"
        # No payload value leaks into the event row.
        assert FAKE_OPENAI_KEY not in json.dumps(row)

    def test_severity_reflects_caught_pattern(self):
        """High-entropy credential patterns → "high"; everything else (e.g. an
        env block) → "medium". Pinned so the triage-index mapping is a
        deliberate change, not an accidental flatten back to one severity."""
        client = MagicMock()
        write_scrubber.log_block_event(client, {"api_key_anthropic": 1}, write_path="memory_store")
        assert client.table.return_value.insert.call_args.args[0]["severity"] == "high"

        client.reset_mock()
        write_scrubber.log_block_event(client, {"env_block": 1}, write_path="memory_store")
        assert client.table.return_value.insert.call_args.args[0]["severity"] == "medium"

        # A leaked JWT is a Supabase service-role token (full DB access) — it
        # must triage as "high" alongside the raw API keys, not as a medium
        # env-block. Pins the round-10 MINOR-3 fix.
        client.reset_mock()
        write_scrubber.log_block_event(client, {"api_key_jwt": 1}, write_path="memory_store")
        assert client.table.return_value.insert.call_args.args[0]["severity"] == "high"

    def test_swallows_db_errors(self, capsys):
        client = MagicMock()
        client.table.side_effect = Exception("DB down: secret-bearing context")
        # Must not raise — logging is fire-and-forget.
        write_scrubber.log_block_event(client, {"x": 1}, write_path="memory_store")
        # Privacy: the stderr diagnostic must carry the exception *type* only,
        # never str(exc) (which could echo request context). Pins the invariant
        # so a future `print(exc)` regression goes red (round-10 m3).
        err = capsys.readouterr().err
        assert "Exception" in err
        assert "secret-bearing context" not in err


# ── _dispatch_block_log fallback branches ─────────────────────────────────


class TestDispatchBlockLog:
    def test_no_loop_runs_inline(self, monkeypatch):
        """Called outside any running loop (direct unit-test / sync caller) →
        the audit insert runs inline rather than being lost to a create_task
        that has no loop to schedule on."""
        called: list = []
        monkeypatch.setattr(
            write_scrubber,
            "log_block_event",
            lambda c, p, *, write_path: called.append((p, write_path)),
        )
        write_scrubber._dispatch_block_log(
            MagicMock(), {"api_key_openai": 1}, write_path="memory_store"
        )
        assert called == [({"api_key_openai": 1}, "memory_store")]

    async def test_teardown_race_falls_back_to_inline(self, monkeypatch):
        """A loop IS running but create_task raises RuntimeError (loop closing
        during teardown). The audit event is non-negotiable, so the dispatcher
        must fall back to a synchronous inline insert rather than swallow it."""
        called: list = []
        monkeypatch.setattr(
            write_scrubber,
            "log_block_event",
            lambda c, p, *, write_path: called.append((p, write_path)),
        )

        def _raise(coro):
            coro.close()  # avoid "coroutine was never awaited" noise
            raise RuntimeError("loop is closing")

        monkeypatch.setattr(write_scrubber.asyncio, "create_task", _raise)
        write_scrubber._dispatch_block_log(
            MagicMock(), {"api_key_aws": 1}, write_path="record_decision"
        )
        assert called == [({"api_key_aws": 1}, "record_decision")]
        # Nothing was pinned — the task was never created.
        assert not write_scrubber._PENDING_BLOCK_LOGS


# ── check_write (combined gate) ───────────────────────────────────────────


class TestCheckWrite:
    def test_clean_returns_none_no_event(self):
        client = MagicMock()
        out = write_scrubber.check_write(
            client, {"content": "all clean"}, write_path="memory_store"
        )
        assert out is None
        client.table.assert_not_called()

    def test_fired_returns_error_and_logs_event(self):
        client = MagicMock()
        out = write_scrubber.check_write(
            client, {"content": FAKE_OPENAI_KEY}, write_path="memory_store"
        )
        assert out is not None
        payload = json.loads(out)
        assert payload["error"] == "secret_pattern_detected"
        client.table.assert_called_with("events")

    def test_user_path_does_not_block(self):
        """AC#4: ~26% of the live corpus carries absolute user paths. A path
        is a normalization concern (scrub-and-keep), not a write-blocking
        secret leak — it must NOT reject the write or emit a block event."""
        client = MagicMock()
        out = write_scrubber.check_write(
            client,
            {"content": r"config at C:\Users\alice\.claude\settings.json"},
            write_path="memory_store",
        )
        assert out is None
        client.table.assert_not_called()

    def test_secret_blocks_even_alongside_path(self):
        """A real secret still blocks even when a (non-blocking) path is also
        present; the block payload carries only the secret pattern."""
        client = MagicMock()
        out = write_scrubber.check_write(
            client,
            {"content": f"/home/bob/app uses {FAKE_OPENAI_KEY}"},
            write_path="memory_store",
        )
        payload = json.loads(out)
        assert payload["patterns"] == {"api_key_openai": 1}
        assert "path_username" not in payload["patterns"]


# ── every blocking pattern is exercised, not just api_key_openai ──────────


class TestAllBlockingPatterns:
    @pytest.mark.parametrize("pattern_name,text", BLOCKING_PATTERN_CASES)
    def test_each_blocking_pattern_blocks(self, pattern_name, text):
        client = MagicMock()
        out = write_scrubber.check_write(client, {"content": text}, write_path="memory_store")
        assert out is not None, f"{pattern_name} did not block the write"
        assert pattern_name in json.loads(out)["patterns"]
        # The raw token never leaks into the rejection payload.
        assert text not in out


# ── fail-open when the scrubber lib is unavailable ────────────────────────


class TestScrubUnavailable:
    def test_scan_fields_fail_open_when_scrub_none(self, monkeypatch):
        """Security invariant: when scrub is unavailable the gate fails OPEN
        (availability > over-blocking) — documented + must stay covered."""
        monkeypatch.setattr(write_scrubber, "scrub", None)
        assert write_scrubber.scan_fields({"content": FAKE_OPENAI_KEY}) == {}

    def test_check_write_fail_open_when_scrub_none(self, monkeypatch):
        monkeypatch.setattr(write_scrubber, "scrub", None)
        client = MagicMock()
        out = write_scrubber.check_write(
            client, {"content": FAKE_OPENAI_KEY}, write_path="memory_store"
        )
        assert out is None
        client.table.assert_not_called()


# ── SCRUB_ONLY_PATTERNS coupling invariant ────────────────────────────────


class TestScrubOnlyCoupling:
    def test_scrub_only_names_are_real_pattern_names(self):
        """Drift guard, LIVE-FIRE: assert SCRUB_ONLY_PATTERNS names are names the
        real scrubber emits on real path input. A static subset check against
        ``_KNOWN_PATTERN_NAMES`` would pass vacuously — that set *also* hardcodes
        ``path_username``, so a rename in secret_scrubber.py would leave both
        stale in lockstep and never go red. Calling scrub() closes that loop:
        if path_username is renamed, the live fires key changes and this fails."""
        assert write_scrubber.scrub is not None, "only meaningful when scrubber is available"
        _, fires = write_scrubber.scrub(r"config at C:\Users\alice\.claude\settings.json")
        assert write_scrubber.SCRUB_ONLY_PATTERNS <= set(fires.keys()), (
            "SCRUB_ONLY_PATTERNS references names the live scrubber does not emit "
            f"on path input: {write_scrubber.SCRUB_ONLY_PATTERNS - set(fires.keys())}"
        )

    def test_env_block_pattern_name_is_live(self):
        """Companion live-fire guard for the non-path EXTRA_PATTERN_NAMES entry.
        ``env_block`` rides in _KNOWN_PATTERN_NAMES via EXTRA_PATTERN_NAMES but
        is not in SCRUB_ONLY_PATTERNS, so the subset guard above never exercises
        it. Fire the real scrubber on an env block and assert the emitted key is
        still ``env_block`` — catches a rename that would silently stop blocking
        env writes with no operator signal."""
        assert write_scrubber.scrub is not None, "only meaningful when scrubber is available"
        _, fires = write_scrubber.scrub(FAKE_ENV_BLOCK)
        assert "env_block" in fires
        assert "env_block" in write_scrubber._KNOWN_PATTERN_NAMES


# ── _handle_store integration ─────────────────────────────────────────────


class TestStoreGate:
    @pytest.fixture(autouse=True)
    def _patch(self, monkeypatch):
        self.client = MagicMock()
        monkeypatch.setattr(server_module, "_get_client", lambda: self.client)

        self.embed_calls: list = []

        async def _spy_embed(_text):
            self.embed_calls.append(_text)
            return {}

        monkeypatch.setattr(server_module, "_compute_write_embeddings", _spy_embed)

    async def test_secret_in_content_blocks_no_embed_no_insert(self):
        result = await _handle_store(
            {
                "type": "project",
                "name": "leaky",
                "content": f"my key {FAKE_OPENAI_KEY}",
                "project": "jarvis",
                "source_provenance": "session:test",
            }
        )

        body = json.loads(result[0].text)
        assert body["error"] == "secret_pattern_detected"
        assert "api_key_openai" in body["patterns"]
        # No embedding computed.
        assert self.embed_calls == []
        # No memory row written — only the events insert is permitted.
        memory_writes = [
            c for c in self.client.table.call_args_list if c.args and c.args[0] == "memories"
        ]
        assert memory_writes == []
        # The secret never appears in the returned error text.
        assert FAKE_OPENAI_KEY not in result[0].text

    async def test_block_event_is_actually_logged_on_async_path(self):
        """MAJOR-fix: under asyncio_mode=auto the handler runs in a loop, so the
        block-event insert is dispatched as a detached task. Without draining,
        no test ever observes it. Drain with ``asyncio.sleep(0)`` and assert the
        ``events`` insert fired with the value-free counter row."""
        # Route each table() to its own mock so the events row is extracted from
        # the events table specifically — not from a shared insert.call_args that
        # grabs the last insert on ANY table (which would make the privacy check
        # vacuous if a future insert followed the block event).
        tables: dict = {}
        self.client.table.side_effect = lambda name: tables.setdefault(name, MagicMock())

        result = await _handle_store(
            {
                "type": "project",
                "name": "leaky",
                "content": f"my key {FAKE_OPENAI_KEY}",
                "project": "jarvis",
                "source_provenance": "session:test",
            }
        )
        assert json.loads(result[0].text)["error"] == "secret_pattern_detected"

        # Drain the detached _log_block_event_async task(s). The insert runs via
        # asyncio.to_thread, so awaiting the pending-task set (not bare sleep(0)
        # ticks) is what guarantees the executor thread finished before we assert.
        # Snapshot the set BEFORE awaiting: the done-callback discards completed
        # tasks, so reading it after a yield could race to empty even though a
        # task ran. The gate returns without yielding, so the task is scheduled
        # but not yet started here — the set is reliably populated.
        pending = list(write_scrubber._PENDING_BLOCK_LOGS)
        assert pending, "no block-log task was scheduled on the async path"
        await asyncio.gather(*pending)

        assert "events" in tables, "block event was never inserted on the async path"
        row = tables["events"].insert.call_args.args[0]
        assert row["event_type"] == "mcp_write_scrubber_block"
        assert row["payload"]["write_path"] == "memory_store"
        assert FAKE_OPENAI_KEY not in json.dumps(row)

    async def test_secret_in_name_blocks(self):
        result = await _handle_store(
            {
                "type": "project",
                "name": FAKE_OPENAI_KEY,
                "content": "clean content",
                "project": "jarvis",
                "source_provenance": "session:test",
            }
        )
        assert json.loads(result[0].text)["error"] == "secret_pattern_detected"

    @pytest.mark.parametrize(
        "field,value",
        [
            ("description", f"see {FAKE_OPENAI_KEY}"),
            ("tags", ["fine", FAKE_OPENAI_KEY]),
            ("source_provenance", f"external:{FAKE_OPENAI_KEY}"),
        ],
    )
    async def test_secret_in_other_scanned_fields_blocks(self, field, value):
        """Every field the store gate scans must block, not just name/content —
        otherwise a secret could slip in through description/tags/provenance."""
        args = {
            "type": "project",
            "name": "fine",
            "content": "clean content",
            "project": "jarvis",
            "source_provenance": "session:test",
        }
        args[field] = value
        result = await _handle_store(args)
        assert json.loads(result[0].text)["error"] == "secret_pattern_detected"
        assert self.embed_calls == []

    async def test_clean_store_passes_gate(self):
        tbl = MagicMock()
        tbl.upsert.return_value.execute.return_value = MagicMock(data=[{"id": "ok-1"}])
        self.client.table.return_value = tbl

        result = await _handle_store(
            {
                "type": "project",
                "name": "fine",
                "content": "nothing secret here",
                "project": "jarvis",
                "source_provenance": "session:test",
            }
        )
        # Reaches the normal store path → embedding spy was called AND the DB
        # upsert was actually reached (guards against an early return between
        # embed and insert that would still pass an embed-only assertion).
        assert self.embed_calls
        assert tbl.upsert.called, "clean write never reached the memories upsert"
        assert "ok-1" in result[0].text
        assert "error" not in result[0].text
        # A clean write must NOT emit a block event — the gate only logs on a
        # detected secret. Guards against an over-eager log_block_event firing
        # on every write (round-10 m2).
        events_calls = [
            c for c in self.client.table.call_args_list if c.args and c.args[0] == "events"
        ]
        assert not events_calls, "clean write spuriously emitted a block event"


# ── _handle_record_decision integration ───────────────────────────────────


class TestDecisionGate:
    async def test_secret_in_rationale_blocks_no_episode(self, monkeypatch):
        client = make_client("ep-blocked")
        monkeypatch.setattr(server_module, "_get_client", lambda: client)

        result = await _handle_record_decision(
            {
                "decision": "do the thing",
                "rationale": f"because {FAKE_OPENAI_KEY} is the key",
                "reversibility": "reversible",
                "actor": "session:test",
            }
        )

        body = json.loads(result[0].text)
        assert body["error"] == "secret_pattern_detected"
        # No episode written.
        episode_inserts = [
            c for c in client.table.call_args_list if c.args and c.args[0] == "episodes"
        ]
        assert episode_inserts == []
        assert FAKE_OPENAI_KEY not in result[0].text

    async def test_secret_in_decision_blocks(self, monkeypatch):
        client = make_client("ep-blocked")
        monkeypatch.setattr(server_module, "_get_client", lambda: client)

        result = await _handle_record_decision(
            {
                "decision": f"adopt {FAKE_OPENAI_KEY} as the key",
                "rationale": "clean rationale",
                "reversibility": "reversible",
            }
        )
        body = json.loads(result[0].text)
        assert body["error"] == "secret_pattern_detected"
        episode_inserts = [
            c for c in client.table.call_args_list if c.args and c.args[0] == "episodes"
        ]
        assert episode_inserts == []

    async def test_secret_in_alternatives_blocks(self, monkeypatch):
        client = make_client("ep-blocked")
        monkeypatch.setattr(server_module, "_get_client", lambda: client)

        result = await _handle_record_decision(
            {
                "decision": "clean decision",
                "rationale": "clean rationale",
                "alternatives_considered": [f"option using {FAKE_OPENAI_KEY}"],
                "reversibility": "reversible",
            }
        )
        assert json.loads(result[0].text)["error"] == "secret_pattern_detected"

    async def test_secret_in_outcomes_referenced_blocks(self, monkeypatch):
        """``outcomes_referenced`` is persisted raw into the episode payload with
        no UUID validation, so a secret smuggled there must block too."""
        client = make_client("ep-blocked")
        monkeypatch.setattr(server_module, "_get_client", lambda: client)

        result = await _handle_record_decision(
            {
                "decision": "clean decision",
                "rationale": "clean rationale",
                "reversibility": "reversible",
                "outcomes_referenced": [f"outcome {FAKE_OPENAI_KEY}"],
            }
        )
        assert json.loads(result[0].text)["error"] == "secret_pattern_detected"
        episode_inserts = [
            c for c in client.table.call_args_list if c.args and c.args[0] == "episodes"
        ]
        assert episode_inserts == []

    async def test_secret_in_actor_blocks(self, monkeypatch):
        """``actor`` is in the decision gate's scanned set — a secret there must
        block just like decision/rationale/alternatives."""
        client = make_client("ep-blocked")
        monkeypatch.setattr(server_module, "_get_client", lambda: client)

        result = await _handle_record_decision(
            {
                "decision": "clean decision",
                "rationale": "clean rationale",
                "reversibility": "reversible",
                "actor": f"session:{FAKE_OPENAI_KEY}",
            }
        )
        assert json.loads(result[0].text)["error"] == "secret_pattern_detected"
        episode_inserts = [
            c for c in client.table.call_args_list if c.args and c.args[0] == "episodes"
        ]
        assert episode_inserts == []

    async def test_clean_decision_passes_gate(self, monkeypatch):
        client = make_client("ep-ok")
        monkeypatch.setattr(server_module, "_get_client", lambda: client)

        result = await _handle_record_decision(
            {
                "decision": "clean decision",
                "rationale": "clean rationale",
                "reversibility": "reversible",
            }
        )
        # Reaches the normal path → episode id surfaced, no error.
        assert "ep-ok" in result[0].text
        assert "secret_pattern_detected" not in result[0].text


# ── _handle_goal_set / _handle_goal_update integration ───────────────────


class TestGoalGate:
    @pytest.fixture(autouse=True)
    def _patch(self, monkeypatch):
        self.client = MagicMock()
        monkeypatch.setattr(server_module, "_get_client", lambda: self.client)

    async def test_secret_in_title_blocks_no_insert(self):
        """A secret in the goal title must block — no row written."""
        result = await _handle_goal_set(
            {
                "slug": "test-goal",
                "title": f"my key {FAKE_OPENAI_KEY}",
                "why": "clean why",
            }
        )
        body = json.loads(result[0].text)
        assert body["error"] == "secret_pattern_detected"
        assert "api_key_openai" in body["patterns"]
        # No goal insert/update reached.
        goals_writes = [
            c for c in self.client.table.call_args_list if c.args and c.args[0] == "goals"
        ]
        assert not goals_writes
        assert FAKE_OPENAI_KEY not in result[0].text

    async def test_secret_in_why_blocks(self):
        result = await _handle_goal_set(
            {
                "slug": "test-goal",
                "title": "clean title",
                "why": f"because {FAKE_GITHUB_TOKEN} is used",
            }
        )
        assert json.loads(result[0].text)["error"] == "secret_pattern_detected"

    async def test_secret_in_success_criteria_blocks(self):
        result = await _handle_goal_set(
            {
                "slug": "test-goal",
                "title": "clean title",
                "success_criteria": [f"need {FAKE_ANTHROPIC_KEY}"],
            }
        )
        assert json.loads(result[0].text)["error"] == "secret_pattern_detected"

    async def test_secret_in_risks_blocks(self):
        result = await _handle_goal_set(
            {
                "slug": "test-goal",
                "title": "clean title",
                "risks": [f"token {FAKE_AWS_KEY} exposed"],
            }
        )
        assert json.loads(result[0].text)["error"] == "secret_pattern_detected"

    async def test_secret_in_owner_focus_blocks(self):
        result = await _handle_goal_set(
            {
                "slug": "test-goal",
                "title": "clean title",
                "owner_focus": f"rotate {FAKE_SLACK_TOKEN}",
            }
        )
        assert json.loads(result[0].text)["error"] == "secret_pattern_detected"

    async def test_secret_in_jarvis_focus_blocks(self):
        result = await _handle_goal_set(
            {
                "slug": "test-goal",
                "title": "clean title",
                "jarvis_focus": f"use {FAKE_VOYAGE_KEY}",
            }
        )
        assert json.loads(result[0].text)["error"] == "secret_pattern_detected"

    async def test_secret_in_outcome_blocks(self):
        result = await _handle_goal_set(
            {
                "slug": "test-goal",
                "title": "clean title",
                "outcome": f"completed with {FAKE_JWT}",
            }
        )
        assert json.loads(result[0].text)["error"] == "secret_pattern_detected"

    async def test_secret_in_lessons_blocks(self):
        result = await _handle_goal_set(
            {
                "slug": "test-goal",
                "title": "clean title",
                "lessons": f"never hardcode {FAKE_OPENAI_KEY}",
            }
        )
        assert json.loads(result[0].text)["error"] == "secret_pattern_detected"

    async def test_user_path_does_not_block_goal(self):
        """AC#4: absolute user paths must not block a goal write."""
        result = await _handle_goal_set(
            {
                "slug": "test-goal",
                "title": "clean title",
                "why": r"config at C:\Users\alice\.claude\settings.json",
            }
        )
        assert "secret_pattern_detected" not in result[0].text

    async def test_clean_goal_set_passes_gate(self):
        """A clean goal_set must reach the normal handler path."""
        # The handler checks for existing slug — return empty so it creates.
        tbl = MagicMock()
        chain = MagicMock()
        chain.limit.return_value.execute.return_value = MagicMock(data=[])
        tbl.select.return_value.eq.return_value = chain
        self.client.table.return_value = tbl

        result = await _handle_goal_set(
            {
                "slug": "test-goal",
                "title": "clean title",
                "why": "clean why",
            }
        )
        # Reaches the normal path → goal created.
        assert "created" in result[0].text
        assert "secret_pattern_detected" not in result[0].text

    # ── goal_update ─────────────────────────────────────────────────

    async def test_update_secret_in_title_blocks(self):
        result = await _handle_goal_update(
            {
                "slug": "test-goal",
                "title": f"updated with {FAKE_OPENAI_KEY}",
            }
        )
        assert json.loads(result[0].text)["error"] == "secret_pattern_detected"

    async def test_update_clean_passes_gate(self):
        """A clean goal_update must reach the normal handler path."""
        tbl = MagicMock()
        tbl.update.return_value.eq.return_value.execute.return_value = MagicMock(
            data=[{"slug": "test-goal"}]
        )
        self.client.table.return_value = tbl

        result = await _handle_goal_update(
            {
                "slug": "test-goal",
                "title": "updated title",
                "why": "updated why",
            }
        )
        assert "updated" in result[0].text
        assert "secret_pattern_detected" not in result[0].text


# ── _handle_outcome_record / _handle_outcome_update integration ─────────


class TestOutcomeGate:
    @pytest.fixture(autouse=True)
    def _patch(self, monkeypatch):
        self.client = MagicMock()
        monkeypatch.setattr(server_module, "_get_client", lambda: self.client)

    async def test_secret_in_task_description_blocks_no_insert(self):
        result = await _handle_outcome_record(
            {
                "task_type": "fix",
                "task_description": f"rotate {FAKE_OPENAI_KEY}",
                "outcome_status": "success",
            }
        )
        body = json.loads(result[0].text)
        assert body["error"] == "secret_pattern_detected"
        assert FAKE_OPENAI_KEY not in result[0].text
        # No outcome insert reached.
        outcome_writes = [
            c for c in self.client.table.call_args_list if c.args and c.args[0] == "task_outcomes"
        ]
        assert not outcome_writes

    async def test_secret_in_outcome_summary_blocks(self):
        result = await _handle_outcome_record(
            {
                "task_type": "fix",
                "task_description": "clean task",
                "outcome_status": "success",
                "outcome_summary": f"used {FAKE_GITHUB_TOKEN}",
            }
        )
        assert json.loads(result[0].text)["error"] == "secret_pattern_detected"

    async def test_secret_in_lessons_blocks(self):
        result = await _handle_outcome_record(
            {
                "task_type": "fix",
                "task_description": "clean task",
                "outcome_status": "success",
                "lessons": f"never hardcode {FAKE_OPENAI_KEY}",
            }
        )
        assert json.loads(result[0].text)["error"] == "secret_pattern_detected"

    async def test_user_path_does_not_block_outcome(self):
        result = await _handle_outcome_record(
            {
                "task_type": "fix",
                "task_description": r"updated C:\Users\bob\.env",
                "outcome_status": "success",
            }
        )
        assert "secret_pattern_detected" not in result[0].text

    async def test_clean_outcome_record_passes_gate(self):
        tbl = MagicMock()
        tbl.insert.return_value.execute.return_value = MagicMock(data=[{"id": "out-1"}])
        self.client.table.return_value = tbl

        result = await _handle_outcome_record(
            {
                "task_type": "fix",
                "task_description": "clean task",
                "outcome_status": "success",
                "outcome_summary": "all good",
            }
        )
        assert "out-1" in result[0].text
        assert "secret_pattern_detected" not in result[0].text

    # ── outcome_update ──────────────────────────────────────────────

    async def test_update_secret_in_outcome_summary_blocks(self):
        result = await _handle_outcome_update(
            {
                "id": "out-1",
                "outcome_summary": f"breach with {FAKE_OPENAI_KEY}",
            }
        )
        assert json.loads(result[0].text)["error"] == "secret_pattern_detected"

    async def test_update_secret_in_lessons_blocks(self):
        result = await _handle_outcome_update(
            {
                "id": "out-1",
                "lessons": f"leaked {FAKE_AWS_KEY}",
            }
        )
        assert json.loads(result[0].text)["error"] == "secret_pattern_detected"

    async def test_update_clean_passes_gate(self):
        tbl = MagicMock()
        tbl.update.return_value.eq.return_value.execute.return_value = MagicMock(
            data=[{"id": "out-1"}]
        )
        self.client.table.return_value = tbl

        result = await _handle_outcome_update(
            {
                "id": "out-1",
                "outcome_summary": "clean update",
            }
        )
        assert "out-1" in result[0].text
        assert "secret_pattern_detected" not in result[0].text
