"""Transcript reader — parse CCD jsonl and detect headless / sandcastle sessions.

Stop-hook gives us {session_id, transcript_path, cwd}. The extractor reads
that transcript, walks user→assistant turns in order, and yields one record
per real user message. ``message_idx`` is the row index in the raw jsonl
file (the same number written into ``comm_patterns.message_idx`` so the
``(device, session_id, message_idx)`` unique index dedups re-runs).

Headless / sandcastle skip — per #581 acceptance criterion. Two cheap
signals:
  1. cwd path containment (.sandcastle / worktrees / sandcastle in path)
  2. no real user messages in the transcript at all (purely scripted)

We bias toward false-positive *skip*: a missed write is fine; a write to a
sandcastle row pollutes cross-device aggregates.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

# Same scrubbers as extract_comms.py — strip noise that isn't user-typed.
_SYS_REMINDER_RE = re.compile(r"<system-reminder>.*?</system-reminder>", re.S)
_HOOK_RE = re.compile(r"<user-prompt-submit-hook>.*?</user-prompt-submit-hook>", re.S)
_COMMAND_TAG_RE = re.compile(r"<command-(name|message|args)>.*?</command-\1>", re.S)
_LOCAL_CMD_RE = re.compile(r"<local-command-stdout>.*?</local-command-stdout>", re.S)


@dataclass
class Turn:
    """One classified turn — a user message with its preceding assistant text."""

    message_idx: int
    timestamp: str
    user_text: str
    prev_assistant_text: str


def is_headless_cwd(cwd: str | None) -> bool:
    """Return True if the cwd looks like a sandcastle / worktree / headless run.

    These sessions don't contain real user-correctives — writing them would
    pollute the cross-device aggregate. Mirrored case-insensitive matching
    works on Windows + POSIX paths.
    """
    if not cwd:
        return False
    lowered = cwd.replace("\\", "/").lower()
    markers = (
        "/.sandcastle/",
        "/.sandcastle",
        "/sandcastle/",
        "/worktrees/",
        "/.claude-worktrees/",
    )
    return any(m in lowered or lowered.endswith(m.rstrip("/")) for m in markers)


def _clean_user_text(s: str) -> str:
    s = _SYS_REMINDER_RE.sub("", s)
    s = _HOOK_RE.sub("", s)
    s = _COMMAND_TAG_RE.sub("", s)
    s = _LOCAL_CMD_RE.sub("", s)
    return s.strip()


def _extract_text_from_content(content) -> str | None:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = [
            blk.get("text", "")
            for blk in content
            if isinstance(blk, dict) and blk.get("type") == "text"
        ]
        joined = "\n".join(p for p in parts if p)
        return joined or None
    return None


def _is_tool_result(content) -> bool:
    return isinstance(content, list) and any(
        isinstance(b, dict) and b.get("type") == "tool_result" for b in content
    )


def _is_real_user_message(text: str) -> bool:
    """Filter out noise: sidechain, command echoes, scheduled-task bootstrap,
    skill-body echoes, and base-directory injections.
    """
    if not text or len(text) < 2:
        return False
    if text.startswith("<command-") or text.startswith("[Request interrupted"):
        return False
    if text.startswith("<scheduled-task") or text.startswith("Base directory for this skill:"):
        return False
    if text.startswith("This session is being continued from"):
        return False
    return True


def parse_turns(transcript_path: Path) -> list[Turn]:
    """Walk the jsonl, return one Turn per real user message in order.

    ``prev_assistant_text`` is the most recent assistant text *block* before
    this user turn (skipping pure tool_use blocks). Empty string if the user
    spoke first.

    ``message_idx`` is the 0-based row offset in the source jsonl, so the
    unique index ``(device, session_id, message_idx)`` is stable across
    extractor re-runs even if rows are appended after a watermark write.
    """
    turns: list[Turn] = []
    last_assistant_text = ""

    try:
        with transcript_path.open("r", encoding="utf-8", errors="replace") as f:
            for idx, line in enumerate(f):
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except Exception:
                    continue
                if obj.get("isSidechain"):
                    continue
                t = obj.get("type")
                if t not in ("user", "assistant"):
                    continue
                msg = obj.get("message") or {}
                content = msg.get("content")
                if t == "assistant":
                    text = _extract_text_from_content(content)
                    if text and text.strip():
                        last_assistant_text = text.strip()
                    continue
                # user
                if _is_tool_result(content):
                    continue
                text = _extract_text_from_content(content)
                if text is None:
                    continue
                cleaned = _clean_user_text(text)
                if not _is_real_user_message(cleaned):
                    continue
                ts = obj.get("timestamp", "")
                turns.append(
                    Turn(
                        message_idx=idx,
                        timestamp=ts,
                        user_text=cleaned,
                        prev_assistant_text=last_assistant_text,
                    )
                )
    except FileNotFoundError:
        return []

    return turns


def is_interactive(turns: list[Turn], min_user_msgs: int = 1) -> bool:
    """Sandcastle / scripted sessions either don't have user messages at all
    or only have machine-generated bootstrap text. ``parse_turns`` already
    filters bootstrap; remaining count == real human turns.
    """
    return len(turns) >= min_user_msgs
