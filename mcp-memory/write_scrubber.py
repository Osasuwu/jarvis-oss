"""MCP write-path Tier-2 secret-scrubber gate (#555).

The slice-3 scrubber (``scripts/lib/secret_scrubber.py``) is applied at the
MCP write boundary. This is the Tier-2 backstop in the two-layer privacy
model (decision ``eb62980e``, ADR-0003): even if the SessionEnd hook scrubber
(slice 6) leaks, MCP writes still cannot land secrets.

When any pattern fires on user-supplied text, the write is **rejected** — not
silently scrubbed. The write is intent-bearing, so the sender must know the
payload was blocked rather than silently rewritten.

Privacy invariant: no value from a blocked payload ever leaves this module.
Only pattern names + fire counts appear in the rejection error, the
``mcp_write_scrubber_block`` counter event, or any log line.

Scope (#555 AC + #999 follow-up): this gate covers ``memory_store``,
``record_decision``, ``goal_set``, ``goal_update``, ``outcome_record``, and
``outcome_update``. ``credential_add`` is deliberately NOT a candidate — it is the one write path
whose entire *domain* is credentials. It records credential metadata (env var
names, provider, expiry) plus free-text note fields (``notes`` /
``rotation_notes``) that legitimately discuss key-shaped values; a
secret-reject gate there would fight the handler's own purpose (it exists to
catalogue credentials, so naming one is expected, not a leak). The structured
value columns reject raw secrets at the schema level; the notes fields rely on
the ``credential_registry`` access model, not scrubbing.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import threading
from collections.abc import Iterator
from pathlib import Path

# The scrubber lib lives under scripts/lib. The live server runtime launches
# server.py from mcp-memory/ (via run-memory-server.py), so scripts/ is NOT on
# sys.path by default — add it here so jarvis always loads the real gate
# instead of silently degrading to a no-op. Tests already put scripts/ on the
# path (conftest), so the insert is idempotent there.
_SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
# Gate on the target FILE existing, not merely a scripts/ dir: a host that has
# an unrelated scripts/ directory (or a redrobot layout) must not get scripts/
# prepended to sys.path, where it could shadow a stdlib/site module named `lib`.
if (_SCRIPTS / "lib" / "secret_scrubber.py").is_file() and str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

try:
    from lib.secret_scrubber import scrub, API_KEY_PATTERNS, EXTRA_PATTERN_NAMES  # type: ignore
except (ImportError, ModuleNotFoundError):
    # redrobot path: scripts/lib genuinely absent. Fail-open is intentional
    # (availability > over-blocking) but MUST be loud: a silent no-op would
    # erase the Tier-2 layer with zero operator signal. Suppressible via
    # WRITE_SCRUBBER_QUIET so repos that legitimately lack scripts/lib don't
    # print this on every cold start forever.
    scrub = None  # type: ignore
    API_KEY_PATTERNS = []  # type: ignore
    EXTRA_PATTERN_NAMES = frozenset()  # type: ignore
    if not os.environ.get("WRITE_SCRUBBER_QUIET"):
        print(
            "[write_scrubber] WARNING: secret_scrubber unavailable (module absent) "
            "— the Tier-2 MCP write-path gate is DISABLED; writes will NOT be "
            "scanned for secrets. Set WRITE_SCRUBBER_QUIET=1 to silence (e.g. on "
            "redrobot).",
            file=sys.stderr,
        )
except Exception as exc:  # noqa: BLE001 — module FOUND but broken (syntax error, etc.)
    # On jarvis scripts/lib exists, so this branch means secret_scrubber.py
    # itself failed to import (merge-conflict marker, syntax error, bad ref).
    # Surfacing the actual error type — not the misleading "unavailable" —
    # is what tells the developer to fix the module rather than hunt a phantom
    # missing dependency. Type only (no str(exc)) to honour the privacy stance.
    scrub = None  # type: ignore
    API_KEY_PATTERNS = []  # type: ignore
    EXTRA_PATTERN_NAMES = frozenset()  # type: ignore
    print(
        "[write_scrubber] ERROR: secret_scrubber import failed — the module was "
        f"found but is broken ({type(exc).__name__}); the Tier-2 gate is DISABLED. "
        "Fix scripts/lib/secret_scrubber.py.",
        file=sys.stderr,
    )


# Patterns the scrubber detects but that must NOT hard-block an MCP write.
# `path_username` is a privacy *normalization* (scrub-and-keep), not a secret
# leak: ~26% of the live memory corpus (214/832) legitimately contains absolute
# user paths (`C:\Users\<name>\…`, `/Users/<name>/…`). Hard-rejecting those
# would violate AC#4 ("no false-positive blocks on real-world content") and
# break a quarter of all memory writes that reference a file path. Path
# normalization is the SessionEnd/Deriver lane's job (slice 6), not this
# Tier-2 secret-reject backstop. The genuine-secret patterns (API keys, env
# blocks) — 0 false positives in the corpus — remain blocking.
SCRUB_ONLY_PATTERNS = frozenset({"path_username"})

# Guard against silent string-coupling drift: SCRUB_ONLY_PATTERNS names must be
# real pattern names emitted by secret_scrubber.py. If a pattern is renamed
# there, this warns loudly at import instead of letting the frozenset become a
# no-op that starts hard-blocking every path-containing write. We warn rather
# than raise: a startup crash would take down the whole (shared) MCP server —
# disproportionate for a drift whose worst case is a functional regression
# (path writes hard-blocked), not a secret leak (fail-open). Loud-but-alive
# beats dead.
_KNOWN_PATTERN_NAMES = {name for name, _ in API_KEY_PATTERNS} | set(EXTRA_PATTERN_NAMES)
if scrub is not None and not SCRUB_ONLY_PATTERNS <= _KNOWN_PATTERN_NAMES:
    print(
        "[write_scrubber] WARNING: SCRUB_ONLY_PATTERNS references pattern name(s) "
        f"not produced by secret_scrubber: {SCRUB_ONLY_PATTERNS - _KNOWN_PATTERN_NAMES}. "
        "A rename in secret_scrubber.py disables path exclusion — those writes "
        "will now be hard-blocked. Update SCRUB_ONLY_PATTERNS in lockstep.",
        file=sys.stderr,
    )


def _iter_strings(value: object) -> Iterator[str]:
    """Yield the str values worth scanning out of a field value.

    str → itself; list/tuple → each str element; everything else (ints,
    None, dicts, floats) is skipped so non-text fields never raise.

    Depth is one level: nested lists (``[["a"]]``) are NOT descended — the
    inner list is not a str so it yields nothing. All current callers pass
    flat ``str`` / ``list[str]`` fields, so this is safe today; if a future
    caller passes nested structure it must flatten first or scanning silently
    skips the nested text.
    """
    if isinstance(value, str):
        yield value
    elif isinstance(value, (list, tuple)):
        for item in value:
            if isinstance(item, str):
                yield item


def scan_fields(fields: dict[str, object]) -> dict[str, int]:
    """Run the scrubber over each text field, return aggregate fire counts.

    *fields* maps a logical field name → value. Returns a dict of pattern
    name → total fire count across all fields. Empty when nothing fires (or
    when the scrubber lib is unavailable — see module docstring).

    A ``scrub()`` crash is contained per-field (logged + skipped) so a bug in
    the scrubber cannot take down every MCP write; this is fail-open, matching
    the unavailable-scrubber stance above.
    """
    if scrub is None:
        return {}
    totals: dict[str, int] = {}
    for field_name, value in fields.items():
        for text in _iter_strings(value):
            try:
                _, fires = scrub(text)
            except Exception as exc:  # noqa: BLE001 — scrubber bug must not crash writes
                # Privacy invariant: log only the exception *type* and field
                # name — never `{exc}`, whose str() could embed the input text
                # (e.g. a regex-engine error carrying match context).
                print(
                    f"[write_scrubber] scrub() raised on field {field_name!r}, "
                    f"skipped (fail-open): {type(exc).__name__}",
                    file=sys.stderr,
                )
                continue
            for name, count in fires.items():
                totals[name] = totals.get(name, 0) + count
    return totals


def rejection_error(patterns: dict[str, int]) -> str:
    """Build the structured rejection payload as a JSON string.

    Carries ONLY pattern names + counts — never any payload value.

    Returns a JSON *string* (handlers wrap it in a TextContent). #1000 will
    migrate the MCP write paths to a structured ``dict`` error return; when that
    lands, this returns the dict directly and the json.dumps moves to the edge.
    """
    return json.dumps({"error": "secret_pattern_detected", "patterns": patterns})


# High-entropy credential patterns — a fire here means a real, live-key-shaped
# secret was caught, which the orchestrator should triage above an env-block or
# (already non-blocking) path. Anything not listed maps to "medium". The block
# itself prevented the leak, so these are never "critical" (no incident
# occurred), but severity must reflect *what* was caught for triage indexing.
_HIGH_SEVERITY_PATTERNS = frozenset(
    {
        "api_key_anthropic",
        "api_key_openai",
        "api_key_aws",
        "api_key_github",
        "api_key_slack",
        # A leaked JWT is typically a Supabase service-role token — full DB
        # access, so high-sensitivity alongside the raw API keys.
        "api_key_jwt",
        # VoyageAI embedding key — a real live credential for this codebase.
        "api_key_voyageai",
    }
)


def _event_severity(patterns: dict[str, int]) -> str:
    """Map fired patterns → event severity for orchestrator triage indexing."""
    return "high" if any(p in _HIGH_SEVERITY_PATTERNS for p in patterns) else "medium"


# Serialize the audit insert: concurrent blocked writes dispatch their block
# events via asyncio.to_thread, which runs them on separate executor threads
# against the *same* Supabase client singleton. httpx.Client is generally
# thread-safe for concurrent requests, but the surrounding supabase-py
# query-builder is not documented as such, and a corrupted/dropped audit event
# is a security-signal loss. The insert is a single short round-trip on a rare
# path (only fires on a detected secret), so serializing it is cheap insurance.
_BLOCK_LOG_LOCK = threading.Lock()


def log_block_event(client, patterns: dict[str, int], *, write_path: str) -> None:
    """Best-effort: write an ``mcp_write_scrubber_block`` counter event.

    Records pattern names + counts only (privacy invariant). *write_path*
    identifies which handler blocked (``memory_store`` / ``record_decision``)
    so ``/learn`` can surface eager-pattern false positives. Repo slug is
    env-overridable so cross-repo (redrobot) blocks are attributed correctly.
    Severity reflects which pattern fired (see ``_event_severity``) so an
    ``sk-ant-*`` catch is not buried at the same priority as an env-block.

    Serialized via ``_BLOCK_LOG_LOCK`` so concurrent ``to_thread`` dispatches
    don't drive the shared client singleton from two threads at once.
    """
    try:
        with _BLOCK_LOG_LOCK:
            client.table("events").insert(
                {
                    "event_type": "mcp_write_scrubber_block",
                    "severity": _event_severity(patterns),
                    "repo": os.environ.get("JARVIS_REPO_SLUG", "your-username/your-repo"),
                    "source": "mcp_memory",
                    "title": f"Write blocked by secret scrubber ({write_path})",
                    "payload": {"write_path": write_path, "patterns": patterns},
                }
            ).execute()
    except Exception as exc:  # noqa: BLE001 — logging must never block the rejection
        # Loud-but-non-fatal: a silent pass hides "why are there no block
        # events in the table?" during debugging. Log only the exception type —
        # the row we tried to insert carries pattern names + counts, but a
        # client-layer error str() could still echo request context, so stay
        # value-free here too.
        print(
            f"[write_scrubber] block-event log failed: {type(exc).__name__}",
            file=sys.stderr,
        )


async def _log_block_event_async(client, patterns: dict[str, int], *, write_path: str) -> None:
    """Run the blocking insert off the event-loop thread.

    ``log_block_event`` does synchronous Supabase HTTP I/O. Scheduling it via
    ``create_task`` alone only defers *when* it starts — it would still run the
    50–200 ms round-trip on the loop thread and stall every other coroutine.
    ``asyncio.to_thread`` hands it to the default executor so the loop stays
    free. (The codebase's older fire-and-forget helpers — ``_emit_recall_event``
    — block the loop directly; this path is the corrected pattern.)"""
    await asyncio.to_thread(log_block_event, client, patterns, write_path=write_path)


# Strong references to in-flight block-log tasks. CPython holds only a *weak*
# reference to a task returned by asyncio.create_task; if nothing else keeps it
# alive the GC can collect it mid-run, silently dropping the insert. For a
# security audit event that is unacceptable — so we pin each task here and drop
# it on completion. (See https://docs.python.org/3/library/asyncio-task.html
# #asyncio.create_task — "Save a reference to the result of this function".)
_PENDING_BLOCK_LOGS: set[asyncio.Task] = set()


def _on_block_log_done(task: asyncio.Task) -> None:
    """Unpin a finished block-log task and surface any exception eagerly.

    Without retrieving ``task.exception()`` here, a failure inside the detached
    insert would surface only as a late, value-bearing "Task exception was never
    retrieved" warning at GC time. We log the exception *type* (privacy) and
    drop the pin.
    """
    _PENDING_BLOCK_LOGS.discard(task)
    if not task.cancelled():
        exc = task.exception()
        if exc is not None:
            print(
                f"[write_scrubber] block-log task failed: {type(exc).__name__}",
                file=sys.stderr,
            )


def _dispatch_block_log(client, patterns: dict[str, int], *, write_path: str) -> None:
    """Emit the block event off the hot path.

    Inside an async handler (a loop is running) the insert is scheduled as a
    detached task so the rejection returns immediately — the MCP event loop is
    never stalled on a 50–200 ms Supabase round-trip. The task is held in
    ``_PENDING_BLOCK_LOGS`` until done so it cannot be GC-dropped mid-insert.
    Two paths fall back to a synchronous inline insert: (1) no loop is running
    (direct unit-test calls), and (2) a loop *was* running but is now closing,
    so ``create_task`` itself raises ``RuntimeError`` during teardown. In both
    cases the audit event must still be written, so we insert inline.
    """
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        log_block_event(client, patterns, write_path=write_path)
        return
    try:
        task = asyncio.create_task(_log_block_event_async(client, patterns, write_path=write_path))
    except RuntimeError:
        # Loop is closing (teardown race) — create_task rejects. The audit event
        # is non-negotiable, so fall back to a blocking inline insert.
        log_block_event(client, patterns, write_path=write_path)
        return
    _PENDING_BLOCK_LOGS.add(task)
    task.add_done_callback(_on_block_log_done)


def check_write(client, fields: dict[str, object], *, write_path: str) -> str | None:
    """Tier-2 gate. Scan *fields*; on any **blocking** secret fire, emit the
    block event (off the event loop when one is running) and return the JSON
    rejection string. Return ``None`` to allow the write. Scrub-only patterns
    (see ``SCRUB_ONLY_PATTERNS``) are ignored — they are normalization
    concerns, not write-blocking leaks.
    """
    fires = scan_fields(fields)
    blocking = {k: v for k, v in fires.items() if k not in SCRUB_ONLY_PATTERNS}
    if not blocking:
        return None
    _dispatch_block_log(client, blocking, write_path=write_path)
    return rejection_error(blocking)
