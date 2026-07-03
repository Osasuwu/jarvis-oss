"""PreToolUse hook: block ``record_decision`` with empty ``memories_used`` (#524).

Tier 2 mechanical backstop for the Tier 1 prompt rule in
``~/.claude/CLAUDE.md`` — "Memory & decision protocol", brief-mode → UUID
map. Per #325 audit, 12 of 33 historical ``decision_made`` episodes stored
names not UUIDs (broken FK), and a separate audit found a non-trivial
fraction emitted with no memories at all. The Tier 1 rule alone hasn't
held the line.

Contract
--------
- Fires on ``mcp__memory__record_decision``.
- Blocks (``permissionDecision=deny``) when ``memories_used`` is missing
  or empty AND ``intentionally_empty`` is not explicitly true.
- Structural escape: pass ``intentionally_empty=true`` in the tool args
  to acknowledge no memory informed the decision. The server emits the
  flag into the episode payload so ``/learn`` (#526) can track the rate;
  sustained >10% is a flag for human review.
- Any other tool name → silent exit 0 (defense-in-depth: this hook is
  also registered under a narrow matcher).
- Parse failure / malformed input → silent exit 0. Never block on
  hook-internal bugs; the rule must not become a footgun.
"""

from __future__ import annotations

import json
import sys

TOOL_NAME = "mcp__memory__record_decision"

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
    _silent_exit()


if __name__ == "__main__":
    main()
