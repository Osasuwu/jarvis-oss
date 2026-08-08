"""PreToolUse hook for ``mcp__memory__record_decision`` — sole owner of the
matcher (#1421). Combines three concerns in one process, one emitted
``hookSpecificOutput``:

1. **Gate (#524)** — block calls with empty ``memories_used``.
2. **Session-id stamping (#1269)** — stamp the harness session id into
   allowed calls via ``updatedInput`` so ``decision_list(session_id=...)``
   can recover the episode after context loss/compaction.
3. **Mid-turn recall (#332)** — inject up to a few relevant memories as
   ``additionalContext`` before the decision is recorded, ported from
   ``scripts/pretooluse-recall-hook.py``'s ``record_decision`` branch.

Why one process instead of two hooks on the same matcher
----------------------------------------------------------
Until #1421, (2) and (3) were two independent PreToolUse hooks registered
on the same ``mcp__memory__record_decision`` matcher. Claude Code's hooks
docs (code.claude.com/docs/en/hooks) confirm hooks on the same matcher run
in parallel and that multiple ``additionalContext`` values accumulate, but
do **not** document how two competing ``updatedInput`` payloads from
sibling processes merge. That undocumented gap let the stamping in (2)
go missing for an entire /grill session (4/4 decisions, not a flake) —
consistent with the recall hook's live Supabase RPC in (3) causing its
output to land after the gate hook's on every call. Consolidating into one
process removes the race outright: there is only ever one
``hookSpecificOutput`` for this matcher, carrying ``updatedInput`` and/or
``additionalContext`` together.

Contract
--------
- Fires on ``mcp__memory__record_decision``.
- Blocks (``permissionDecision=deny``) when ``memories_used`` is missing
  or empty AND ``intentionally_empty`` is not explicitly true. Recall (3)
  never runs for a blocked call — a deliberate tightening vs. the old
  two-process world, where the sibling recall hook fired independently of
  the gate's decision.
- Structural escape: pass ``intentionally_empty=true`` in the tool args
  to acknowledge no memory informed the decision. The server emits the
  flag into the episode payload so ``/learn`` (#526) can track the rate.
- Allowed calls: when stdin carries a valid ``session_id``, emit
  ``hookSpecificOutput.updatedInput`` = tool_input + ``session_id``. The
  harness stdin sid overrides any model-supplied ``session_id``. Missing/
  malformed stdin ``session_id`` → no stamping (fail-open; recovery via
  ``decision_list`` simply won't cover that episode).
- Allowed calls also get a best-effort mid-turn recall: derive a query
  from the ``decision`` text's first sentence, dedup against a
  query-hash cache and the shared (id, mode, generation) dedup
  (``lib.recall_dedup``), run one ``keyword_search_memories`` RPC, and
  fold up to ``MAX_BRIEF_ENTRIES`` hits into ``additionalContext``. Any
  failure here (missing credentials, network, parse) is fail-soft —
  falls back to no ``additionalContext``, never affects the gate or the
  session_id stamp.
- Any other tool name → silent exit 0 (defense-in-depth: this hook is
  also registered under a narrow matcher).
- Parse failure / malformed input → silent exit 0. Never block on
  hook-internal bugs; the rule must not become a footgun.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

TOOL_NAME = "mcp__memory__record_decision"

# ---------------------------------------------------------------------------
# Bootstrap: re-exec under venv if running under system Python (needed for
# the recall path's dotenv/supabase imports; the gate/stamp logic itself is
# pure stdlib and would work without this).
# ---------------------------------------------------------------------------
_ROOT = Path(__file__).resolve().parent.parent
_VENV_PY = _ROOT / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")

if (
    __name__ == "__main__"
    and _VENV_PY.exists()
    and Path(sys.executable).resolve() != _VENV_PY.resolve()
):
    sys.exit(subprocess.call([str(_VENV_PY), str(Path(__file__).resolve())]))

try:
    from dotenv import load_dotenv

    for _env in [_ROOT / ".env", _ROOT.parent / ".env"]:
        if _env.exists():
            load_dotenv(_env, override=True)
            break
except ImportError:
    pass

# Per-session (id, mode, generation) dedup state, shared with the
# UserPromptSubmit and other PreToolUse recall hooks (#1276).
if str(_ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(_ROOT / "scripts"))
from lib.recall_dedup import (  # noqa: E402
    MODE_PRETOOLUSE,
    filter_emittable,
    record_emitted,
)

# ---------------------------------------------------------------------------
# Gate (#524) / stamping (#1269) constants
# ---------------------------------------------------------------------------

# Matches _safe_session_id in session-context.py / pre-compact-backup.py.
_SESSION_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,128}$")

BLOCK_REASON = (
    "record_decision blocked: memories_used is empty.\n"
    "\n"
    "Per ~/.claude/CLAUDE.md (Memory & decision protocol, rule 2):\n"
    "  Every record_decision call passes UUIDs in memories_used, not names.\n"
    "  Empty list valid only when nothing in memory informed the choice.\n"
    "\n"
    "Fix one of:\n"
    "  1. Run memory_recall(brief=true), parse name→uuid, pass UUIDs.\n"
    "  2. If genuinely no memory informed this decision, pass\n"
    "     intentionally_empty=true to acknowledge it. The flag is recorded\n"
    "     on the episode payload for /learn rate tracking (#524, #526)."
)

# ---------------------------------------------------------------------------
# Recall (#332) constants — ported from pretooluse-recall-hook.py, scoped to
# the record_decision query only.
# ---------------------------------------------------------------------------
MAX_BRIEF_ENTRIES = 3  # hard cap on emitted lines (mid-turn, terse)
FETCH_LIMIT = 15  # pull wider, we still only emit top-N
DEDUP_TTL_SECONDS = 60  # identical-query cache window
MIN_QUERY_CHARS = 8  # too-short query = too much noise from FTS
MIN_MATCH_SCORE = 0.08  # keyword rank threshold — drops tail noise
ALLOWED_TYPES = {"feedback", "decision", "reference"}
KNOWN_PROJECTS = {"jarvis", "redrobot"}

# Shares the recall cache namespace with pretooluse-recall-hook.py — the
# dedup key already includes the derived query text + project, so a
# "decision ..." query naturally occupies its own slot regardless of which
# script computed it.
_CLAUDE_HOME_OVERRIDE = os.environ.get("JARVIS_CLAUDE_HOME")
_CLAUDE_HOME = (
    Path(_CLAUDE_HOME_OVERRIDE).expanduser() if _CLAUDE_HOME_OVERRIDE else Path.home() / ".claude"
)
CACHE_DIR = _CLAUDE_HOME / "cache"
CACHE_FILE = CACHE_DIR / "pretooluse-recall-dedup.json"
STATS_FILE = CACHE_DIR / "pretooluse-recall-stats.json"


def _bump_stat(key: str, delta: int = 1) -> None:
    """Increment a counter in the stats file. Best-effort; never raises."""
    try:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        if STATS_FILE.exists():
            try:
                data = json.loads(STATS_FILE.read_text(encoding="utf-8"))
                if not isinstance(data, dict):
                    data = {}
            except (OSError, json.JSONDecodeError):
                data = {}
        else:
            data = {}
        data[key] = int(data.get(key, 0)) + delta
        data.setdefault("session_started_at", time.time())
        tmp = STATS_FILE.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(data), encoding="utf-8")
        tmp.replace(STATS_FILE)
    except OSError:
        pass


def _emit_deny(reason: str) -> None:
    """Output deny JSON and exit 2 (PreToolUse deny convention)."""
    payload = {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": reason,
        }
    }
    json.dump(payload, sys.stdout)
    sys.exit(2)


def _silent_exit() -> None:
    sys.exit(0)


def sanitize_session_id(raw: object) -> str | None:
    """Return the session id iff it matches the harness sid shape, else None.

    Pure function — testable without stdin/stdout plumbing.
    """
    if not isinstance(raw, str):
        return None
    if not _SESSION_ID_RE.match(raw):
        return None
    return raw


def evaluate(tool_name: str, tool_input: dict) -> bool:
    """Return True iff this call should be blocked.

    Pure function — testable without stdin/stdout plumbing.
    """
    if tool_name != TOOL_NAME:
        return False
    if not isinstance(tool_input, dict):
        return False
    if bool(tool_input.get("intentionally_empty")):
        return False
    memories = tool_input.get("memories_used")
    if isinstance(memories, list) and len(memories) > 0:
        return False
    return True


# ---------------------------------------------------------------------------
# Recall query derivation
# ---------------------------------------------------------------------------


def _first_sentence(text: str, max_chars: int = 160) -> str:
    """Pull the first sentence (or the first `max_chars`) from `text`.

    Decision text often starts with the headline call ("implement #X",
    "defer until Y") followed by the paragraph-sized rationale — only
    the headline is useful for keyword recall.
    """
    if not text:
        return ""
    sentence = re.split(r"(?<=[.!?])\s+", text.strip(), maxsplit=1)[0]
    return sentence[:max_chars]


def _derive_recall_query(tool_input: dict) -> str | None:
    decision = _first_sentence(tool_input.get("decision") or "")
    if not decision:
        return None
    return f"decision {decision}"


# ---------------------------------------------------------------------------
# Dedup cache — identical queries inside DEDUP_TTL_SECONDS skip the RPC
# ---------------------------------------------------------------------------


def _query_hash(query: str, project: str | None) -> str:
    """Hash that keys the dedup cache. Includes project so jarvis vs.
    redrobot don't alias to the same entry."""
    digest = hashlib.sha256()
    digest.update((project or "").encode("utf-8"))
    digest.update(b"\x00")
    digest.update(query.encode("utf-8"))
    return digest.hexdigest()[:16]


def _load_cache() -> dict:
    try:
        with CACHE_FILE.open("r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return {}
    if not isinstance(data, dict):
        return {}
    return data


def _prune_cache(cache: dict, now: float) -> dict:
    """Drop expired entries. Keeps the file small under heavy use."""
    cutoff = now - DEDUP_TTL_SECONDS
    return {k: v for k, v in cache.items() if isinstance(v, (int, float)) and v > cutoff}


def is_duplicate(query: str, project: str | None, now: float | None = None) -> bool:
    """True when the same query (project-scoped) fired within DEDUP_TTL_SECONDS."""
    now = now if now is not None else time.time()
    cache = _load_cache()
    ts = cache.get(_query_hash(query, project))
    return isinstance(ts, (int, float)) and (now - ts) < DEDUP_TTL_SECONDS


def record_query(query: str, project: str | None, now: float | None = None) -> None:
    """Write the query's timestamp into the dedup cache. Best-effort."""
    now = now if now is not None else time.time()
    try:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
    except OSError:
        return
    cache = _prune_cache(_load_cache(), now)
    cache[_query_hash(query, project)] = now
    tmp = CACHE_FILE.with_suffix(".json.tmp")
    try:
        with tmp.open("w", encoding="utf-8") as fh:
            json.dump(cache, fh)
        tmp.replace(CACHE_FILE)
    except OSError:
        try:
            tmp.unlink()
        except OSError:
            pass


def detect_project(cwd: str | None) -> str | None:
    """Return the known project a path belongs to, scanning all components.

    Worktree cwds (`<repo>/.claude/worktrees/<name>`) and subdirectories
    resolve to the containing repo; rightmost match wins.
    """
    if not cwd:
        return None
    try:
        parts = Path(cwd).parts
    except (OSError, ValueError):
        return None
    for part in reversed(parts):
        if part.lower() in KNOWN_PROJECTS:
            return part.lower()
    return None


def format_brief(row: dict) -> str:
    name = row.get("name") or "?"
    mtype = row.get("type") or "?"
    proj = row.get("project") or "global"
    desc = (row.get("description") or "").strip()
    return f"- {name} [{mtype}/{proj}]: {desc}"


def _compute_recall_context(tool_input: dict, session_id: str, cwd: str | None) -> str | None:
    """Best-effort mid-turn recall for record_decision (#332).

    Returns the ``additionalContext`` body, or None when there is nothing
    to inject — no query, a dedup hit, missing credentials, or any error.
    Fail-soft: never raises, never affects the gate or session_id stamp.
    """
    try:
        query = _derive_recall_query(tool_input)
        if not query or len(query) < MIN_QUERY_CHARS:
            return None

        project = detect_project(cwd)

        if is_duplicate(query, project):
            _bump_stat("deduped")
            return None

        url = os.environ.get("SUPABASE_URL")
        key = os.environ.get("SUPABASE_KEY")
        if not url or not key:
            return None

        try:
            from supabase import create_client
        except ImportError:
            return None

        try:
            client = create_client(url, key)
        except Exception:
            return None

        # Record the query hash up-front so a failing/empty recall still
        # suppresses repeat RPC calls within the TTL window.
        record_query(query, project)

        try:
            resp = client.rpc(
                "keyword_search_memories",
                {
                    "search_query": query,
                    "match_limit": FETCH_LIMIT,
                    "filter_project": project,
                    "filter_type": None,
                },
            ).execute()
        except Exception:
            return None

        _bump_stat("fired")

        rows = resp.data or []
        rows = [r for r in rows if r.get("type") in ALLOWED_TYPES]
        rows = [r for r in rows if (r.get("rank") or 0) >= MIN_MATCH_SCORE]
        kept = filter_emittable(session_id, rows, MODE_PRETOOLUSE)
        if len(kept) < len(rows):
            _bump_stat("deduped_ids", delta=len(rows) - len(kept))
        rows = kept
        rows = rows[:MAX_BRIEF_ENTRIES]
        if not rows:
            return None

        _bump_stat("emitted")
        record_emitted(
            session_id,
            [r.get("id") or r.get("name") for r in rows],
            MODE_PRETOOLUSE,
        )

        header = (
            f"# Mid-turn recall for {TOOL_NAME}"
            + (f" (project: {project})" if project else "")
            + "\n\nBefore this action fires, the following memories may be relevant:\n\n"
        )
        body = "\n".join(format_brief(r) for r in rows)
        return header + body
    except Exception:
        return None


def _emit_allow(
    tool_input: dict, session_id: str | None, cwd: str | None, context: str | None
) -> None:
    """Emit a single combined ``hookSpecificOutput`` for an allowed call.

    ``updatedInput`` carries the ``session_id`` stamp (#1269, forensic
    grouping metadata only) and the ``cwd`` stamp (#1423, the actual
    recovery-key component alongside ``project``+``since``) as independent
    optional fields, plus ``additionalContext`` (mid-turn recall, #332) on
    the SAME payload — this is the one-process replacement for the two
    sibling hooks that used to race on this matcher (#1421). ``cwd`` has no
    sanitization failure mode (it falls back to ``os.getcwd()`` upstream),
    so it is stamped independently of whether ``session_id`` validated.
    Silent exit when nothing applies, matching the old per-hook silent-exit
    contract.
    """
    inner: dict = {"hookEventName": "PreToolUse"}
    updated: dict | None = None
    if session_id is not None:
        updated = dict(tool_input)
        updated["session_id"] = session_id
    if cwd:
        if updated is None:
            updated = dict(tool_input)
        updated["cwd"] = cwd
    if updated is not None:
        inner["updatedInput"] = updated
    if context:
        inner["additionalContext"] = context

    if len(inner) == 1:  # only hookEventName — nothing to report
        _silent_exit()

    json.dump({"hookSpecificOutput": inner}, sys.stdout)
    sys.exit(0)


def main() -> None:
    try:
        raw = sys.stdin.buffer.read().decode("utf-8", errors="replace")
    except Exception:
        _silent_exit()
    if not raw.strip():
        _silent_exit()

    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        _silent_exit()

    tool_name = data.get("tool_name") or ""
    tool_input = data.get("tool_input") or {}

    if evaluate(tool_name, tool_input):
        _emit_deny(BLOCK_REASON)

    if tool_name != TOOL_NAME or not isinstance(tool_input, dict):
        _silent_exit()

    sid = sanitize_session_id(data.get("session_id"))
    # Recall dedup keying is lenient (unsanitized) — matches the old
    # pretooluse-recall-hook.py behavior, independent of stamp validity.
    raw_session_id = data.get("session_id") or data.get("sessionId") or ""
    cwd = data.get("cwd") or os.getcwd()

    context = _compute_recall_context(tool_input, raw_session_id, cwd)
    _emit_allow(tool_input, sid, cwd, context)


if __name__ == "__main__":
    main()
