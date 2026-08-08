"""Shared (memory-id, mode, generation) dedup state for the two recall hooks.

``scripts/memory-recall-hook.py`` (UserPromptSubmit) and
``scripts/pretooluse-recall-hook.py`` (PreToolUse) both inject recalled
memories into context. Before #1276 neither deduped across turns, so the
same top memories were re-injected on every prompt (~16K tokens per
50-turn session). This module gives both hooks one per-session dedup state:
a memory injected in a given *mode* is skipped for the rest of the current
compaction generation.

Generation = the per-session compaction counter written by
``scripts/pre-compact-backup.py`` at
``~/.claude/compaction-counts/<session_id>.txt``. A compaction bumps the
counter, so a stored ``(id, mode)`` record from an earlier generation no
longer matches the current one and the memory is injected again —
post-compact re-injection is exactly what we want (the model genuinely sees
a fresh context after summarization).

Modes are intentionally disjoint so the two hooks don't collide:
``brief``/``full`` for the UserPromptSubmit hook (full = known-unknown
widened) and ``pretooluse`` for the mid-turn hook.

Best-effort contract: every function fails soft (returns defaults, never
raises) so the hooks' fail-soft guarantees hold. State writes are atomic
(tmp + replace) so concurrent hook processes can't corrupt the file.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

MODE_BRIEF = "brief"
MODE_FULL = "full"
MODE_PRETOOLUSE = "pretooluse"

_CLAUDE_HOME_OVERRIDE = os.environ.get("JARVIS_CLAUDE_HOME")
_CLAUDE_HOME = (
    Path(_CLAUDE_HOME_OVERRIDE).expanduser()
    if _CLAUDE_HOME_OVERRIDE
    else Path.home() / ".claude"
)
COMPACTION_DIR = _CLAUDE_HOME / "compaction-counts"
DEDUP_DIR = _CLAUDE_HOME / "cache" / "recall-dedup"


def sanitize_session_id(session_id: str) -> str:
    """Reduce a stdin-sourced session_id to a safe single filename component.

    Mirrors ``scripts/pre-compact-backup.py::_sanitize_session_id`` so the
    dedup state keys off the exact same per-session counter file name.
    """
    safe = "".join(c for c in str(session_id) if c.isalnum() or c in "-_")
    return safe or "unknown-session"


def current_generation(session_id: str) -> int:
    """Compaction generation for the session; 0 when absent or unreadable.

    The counter file lives at ``~/.claude/compaction-counts/<session_id>.txt``
    and is bumped by ``scripts/pre-compact-backup.py`` on every PreCompact
    event. A missing file (fresh session, no compaction yet) reads as 0.
    """
    try:
        f = COMPACTION_DIR / f"{sanitize_session_id(session_id)}.txt"
        return int(f.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return 0


def _dedup_path(session_id: str) -> Path:
    return DEDUP_DIR / f"{sanitize_session_id(session_id)}.json"


def load_state(session_id: str) -> dict:
    """Raw dedup state (``{ "<memory_id>:<mode>": <generation> }``)."""
    if not session_id:
        return {}
    try:
        with _dedup_path(session_id).open("r", encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _save_state(session_id: str, state: dict) -> None:
    try:
        DEDUP_DIR.mkdir(parents=True, exist_ok=True)
        path = _dedup_path(session_id)
        tmp = path.with_suffix(".tmp")
        with tmp.open("w", encoding="utf-8") as fh:
            json.dump(state, fh)
        tmp.replace(path)
    except OSError:
        pass


def _memory_key(memory: dict) -> str:
    """Stable key for a memory row: id when present, else name."""
    return str(memory.get("id") or memory.get("name") or "")


def _is_recorded(state: dict, memory: dict, mode: str, generation: int) -> bool:
    key = _memory_key(memory)
    return bool(key) and state.get(f"{key}:{mode}") == generation


def filter_emittable(
    session_id: str, memories: list[dict], mode: str, generation: int | None = None
) -> list[dict]:
    """Return the memories whose ``(id, mode)`` wasn't injected this generation.

    ``generation`` defaults to the session's current compaction counter. An
    empty ``session_id`` disables dedup (returns the input unchanged) so a
    missing hook field can never wrongly suppress injection.
    """
    if not session_id:
        return list(memories)
    gen = current_generation(session_id) if generation is None else generation
    state = load_state(session_id)
    return [m for m in memories if not _is_recorded(state, m, mode, gen)]


def record_emitted(
    session_id: str, memory_ids: list[str], mode: str, generation: int | None = None
) -> None:
    """Mark each memory id as injected at the current generation.

    Reads fresh state before writing so a concurrent write from the other
    hook isn't clobbered. Best-effort: any I/O failure is swallowed.
    """
    if not session_id:
        return
    ids = [str(mid) for mid in (memory_ids or []) if str(mid)]
    if not ids:
        return
    gen = current_generation(session_id) if generation is None else generation
    state = load_state(session_id)
    for mid in ids:
        state[f"{mid}:{mode}"] = gen
    _save_state(session_id, state)
