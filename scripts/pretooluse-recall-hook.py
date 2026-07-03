"""PreToolUse hook: targeted recall before mid-turn autonomous actions (#332).

Complements ``scripts/memory-recall-hook.py`` (UserPromptSubmit). That hook
only fires at user turns, so autonomous actions that happen between turns —
dispatching a subagent, writing to a markdown doc, saving a memory,
recording a decision, filing a GitHub issue/PR — run without fresh recall.
Skills enforce recall at their entry, but impromptu work inside a task
doesn't trigger a skill.

This hook binds to a narrow set of tool matchers and, for each match,
derives a short query from the tool's args, runs a keyword recall, and
injects up to a few one-line memory hints as ``additionalContext`` so the
agent sees task-relevant rules *before* it executes the action.

Matched tools and query derivation
----------------------------------
- ``Task`` (agent launch) → ``"delegation " + description``
- ``Write`` / ``Edit`` / ``NotebookEdit`` on ``*.md`` → ``"state in docs " + filename-stem``
- ``mcp__memory__memory_store`` → ``"duplicate " + memory-name + " " + type``
- ``mcp__memory__record_decision`` → first sentence of the ``decision`` text
- ``Bash`` running ``gh issue create`` / ``gh pr create`` → ``"issue conventions milestone epic"``

Budget
------
- One ``keyword_search_memories`` RPC — no embedding, no LLM rewriter.
  Keeps the hook under ~500ms on a warm connection.
- Query-hash dedup: identical queries inside a ``DEDUP_TTL_SECONDS`` window
  skip the RPC entirely. Cache file sits under ``~/.claude/cache/``.
- Cap at ``MAX_BRIEF_ENTRIES`` one-line entries; the bigger recall context
  is already loaded by UserPromptSubmit, this is the mid-turn nudge on top.
- Token audit target: ``< 1K`` tokens added per typical session — measured
  by counting emits per session in smoke tests.

Fail-soft contract
------------------
Never blocks a tool call. Any parse error, missing credential, DB failure,
cache corruption → ``sys.exit(0)`` silently. Stderr is suppressed so a
transient failure doesn't clutter the agent's view. The hook's job is
*hint*, not *gate* — gating belongs in ``scripts/protected-files.py`` and
``scripts/secret-scanner.py``.
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

# ---------------------------------------------------------------------------
# Bootstrap: re-exec under venv if running under system Python
# ---------------------------------------------------------------------------
_ROOT = Path(__file__).resolve().parent.parent
_VENV_PY = _ROOT / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")

if (
    __name__ == "__main__"
    and _VENV_PY.exists()
    and Path(sys.executable).resolve() != _VENV_PY.resolve()
):
    sys.exit(subprocess.call([str(_VENV_PY), str(Path(__file__).resolve())]))

# ---------------------------------------------------------------------------
# Under venv — safe to import deps
# ---------------------------------------------------------------------------
try:
    from dotenv import load_dotenv

    for _env in [_ROOT / ".env", _ROOT.parent / ".env"]:
        if _env.exists():
            load_dotenv(_env, override=True)
            break
except ImportError:
    pass

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
MAX_BRIEF_ENTRIES = 3  # hard cap on emitted lines (mid-turn, terse)
FETCH_LIMIT = 15  # pull wider, we still only emit top-N
RECALL_TIMEOUT_SEC = 4.0  # hook runs inline before the tool call — keep tight
DEDUP_TTL_SECONDS = 60  # identical-query cache window
MIN_QUERY_CHARS = 8  # too-short query = too much noise from FTS
MIN_MATCH_SCORE = 0.08  # keyword rank threshold — drops tail noise (was 0.05; #434 audit)
ALLOWED_TYPES = {"feedback", "decision", "reference"}

# Stats file for per-session fire-rate audit (#434). Best-effort, never blocks.
# Keys: fired (total invocations that ran the RPC), emitted (yielded ≥1 row),
# deduped (skipped via cache). Reset by `--reset-stats` flag or manual delete.
STATS_FILE = None  # set lazily, depends on _CLAUDE_HOME below

# Projects Jarvis tracks — cwd basename must match to scope recall.
# Derived from config/repos.conf so the set is owner-agnostic; falls back to
# this checkout's own directory name when no repos are configured yet.
def _load_known_projects() -> set[str]:
    names: set[str] = set()
    try:
        for line in (_ROOT / "config" / "repos.conf").read_text(
            encoding="utf-8"
        ).splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "/" in line.split()[0]:
                names.add(line.split()[0].split("/", 1)[1])
    except OSError:
        pass
    return names or {_ROOT.name}


KNOWN_PROJECTS = _load_known_projects()

# Where to stash the dedup cache. Co-located with the rest of the user-level
# ephemera under ~/.claude/cache/ so install.ps1 --apply doesn't stomp it.
_CLAUDE_HOME_OVERRIDE = os.environ.get("JARVIS_CLAUDE_HOME")
_CLAUDE_HOME = (
    Path(_CLAUDE_HOME_OVERRIDE).expanduser() if _CLAUDE_HOME_OVERRIDE else Path.home() / ".claude"
)
CACHE_DIR = _CLAUDE_HOME / "cache"
CACHE_FILE = CACHE_DIR / "pretooluse-recall-dedup.json"
STATS_FILE = CACHE_DIR / "pretooluse-recall-stats.json"


def _bump_stat(key: str) -> None:
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
        data[key] = int(data.get(key, 0)) + 1
        # session_started_at is set on first write so /reflect can compute rate
        data.setdefault("session_started_at", time.time())
        tmp = STATS_FILE.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(data), encoding="utf-8")
        tmp.replace(STATS_FILE)
    except OSError:
        pass


def silent_exit() -> None:
    sys.exit(0)


# ---------------------------------------------------------------------------
# Query derivation — narrow, tool-specific signal extraction
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


def _is_markdown_path(path: str) -> bool:
    if not path:
        return False
    name = path.replace("\\", "/").rsplit("/", 1)[-1].lower()
    return name.endswith(".md") or name.endswith(".markdown")


_GH_CREATE_RE = re.compile(r"\bgh\s+(issue|pr)\s+create\b", re.IGNORECASE)


def _derive_query(tool_name: str, tool_input: dict) -> str | None:
    """Map (tool_name, tool_input) -> recall query, or None to skip.

    Returns None when the tool doesn't match any trigger, when the args
    are missing the fields we'd key on, or when the derived query would
    be too short to give FTS a useful signal.
    """
    if not isinstance(tool_input, dict):
        return None

    if tool_name == "Task":
        description = (tool_input.get("description") or "").strip()
        if not description:
            return None
        return f"delegation verify {description}"

    if tool_name in ("Write", "Edit", "NotebookEdit"):
        path = tool_input.get("file_path") or tool_input.get("notebook_path") or ""
        if not _is_markdown_path(path):
            return None
        stem = Path(path.replace("\\", "/")).stem
        return f"state in docs {stem}"

    if tool_name == "mcp__memory__memory_store":
        name = (tool_input.get("name") or "").strip()
        mtype = (tool_input.get("type") or "").strip()
        if not name:
            return None
        return f"duplicate memory {name} {mtype}".strip()

    if tool_name == "mcp__memory__record_decision":
        decision = _first_sentence(tool_input.get("decision") or "")
        if not decision:
            return None
        return f"decision {decision}"

    if tool_name == "Bash":
        command = tool_input.get("command") or ""
        if not _GH_CREATE_RE.search(command):
            return None
        return "issue conventions milestone epic pr hygiene"

    return None


# ---------------------------------------------------------------------------
# Dedup cache — identical queries inside DEDUP_TTL_SECONDS skip the RPC
# ---------------------------------------------------------------------------


def _query_hash(query: str, project: str | None) -> str:
    """Hash that keys the dedup cache. Includes project so different
    tracked repos don't alias to the same entry."""
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
        # Best-effort — on failure we'll just re-run the query next time.
        try:
            tmp.unlink()
        except OSError:
            pass


# ---------------------------------------------------------------------------
# Project detection — same helper as memory-recall-hook.py
# ---------------------------------------------------------------------------


def detect_project(cwd: str | None) -> str | None:
    if not cwd:
        return None
    try:
        name = Path(cwd).name.lower()
    except (OSError, ValueError):
        return None
    return name if name in KNOWN_PROJECTS else None


# ---------------------------------------------------------------------------
# Emit — PreToolUse additionalContext
# ---------------------------------------------------------------------------


def format_brief(row: dict) -> str:
    name = row.get("name") or "?"
    mtype = row.get("type") or "?"
    proj = row.get("project") or "global"
    desc = (row.get("description") or "").strip()
    return f"- {name} [{mtype}/{proj}]: {desc}"


def emit_context(context: str) -> None:
    out = {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "additionalContext": context,
        }
    }
    json.dump(out, sys.stdout)
    sys.exit(0)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> None:
    try:
        raw = sys.stdin.buffer.read().decode("utf-8", errors="replace")
    except Exception:
        silent_exit()
    if not raw.strip():
        silent_exit()

    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        silent_exit()

    tool_name = data.get("tool_name") or ""
    tool_input = data.get("tool_input") or {}
    if not tool_name:
        silent_exit()

    query = _derive_query(tool_name, tool_input)
    if not query or len(query) < MIN_QUERY_CHARS:
        silent_exit()

    cwd = data.get("cwd") or os.getcwd()
    project = detect_project(cwd)

    if is_duplicate(query, project):
        _bump_stat("deduped")
        silent_exit()

    # Deferred imports — keep non-matching tool calls cheap (no supabase)
    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_KEY")
    if not url or not key:
        silent_exit()

    try:
        from supabase import create_client
    except ImportError:
        silent_exit()

    try:
        client = create_client(url, key)
    except Exception:
        silent_exit()

    # Record the query hash up-front so a failing / empty recall still
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
        silent_exit()

    _bump_stat("fired")

    rows = resp.data or []
    rows = [r for r in rows if r.get("type") in ALLOWED_TYPES]
    rows = [r for r in rows if (r.get("rank") or 0) >= MIN_MATCH_SCORE]
    rows = rows[:MAX_BRIEF_ENTRIES]
    if not rows:
        silent_exit()

    _bump_stat("emitted")

    header = (
        f"# Mid-turn recall for {tool_name}"
        + (f" (project: {project})" if project else "")
        + "\n\nBefore this action fires, the following memories may be relevant:\n\n"
    )
    body = "\n".join(format_brief(r) for r in rows)
    emit_context(header + body)


if __name__ == "__main__":
    main()
