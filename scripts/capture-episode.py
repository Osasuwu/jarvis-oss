"""Capture a single episode into the `episodes` table.

Thin CLI around the Supabase client for hooks to call. Keeps episode
capture cheap: writes are fire-and-forget, non-blocking to the caller
(the extractor runs separately).

Usage:
  python scripts/capture-episode.py --actor session:2026-04-18 \
      --kind user_message --payload '{"text": "..."}'

  # Or read payload from stdin (remember --from-stdin — otherwise the
  # piped JSON is ignored and an empty {} payload is captured):
  echo '{"text": "..."}' | python scripts/capture-episode.py \
      --actor hook:user-prompt --kind user_message --from-stdin

Intended wiring from .claude/settings.json (UserPromptSubmit hook):
  {
    "matcher": "",
    "hooks": [
      {
        "type": "command",
        "command": "python scripts/capture-episode.py --actor hook:user-prompt --kind user_message --from-stdin"
      }
    ]
  }

Environment:
  SUPABASE_URL / SUPABASE_KEY — required.

Exit codes:
  0 on success
  1 on error (bad args, missing env, DB failure). Never blocks the hook —
  Claude Code treats a non-zero hook exit as informational.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent

# Load .env from repo root (parent of scripts/).
try:
    from dotenv import load_dotenv

    for _env_path in [_HERE.parent / ".env", _HERE.parent.parent / ".env"]:
        if _env_path.exists():
            load_dotenv(_env_path, override=True)
            break
except ImportError:
    pass


VALID_KINDS = ("tool_call", "decision", "user_message", "assistant_message", "observation")


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Write a single row to the episodes table.")
    parser.add_argument(
        "--actor",
        required=True,
        help="Who produced this episode. Conventions: 'session:<id>', "
        "'hook:<name>', 'skill:<name>', 'autonomous:<skill>'.",
    )
    parser.add_argument(
        "--kind",
        required=True,
        choices=VALID_KINDS,
        help="Shape of the payload (drives extractor prompting).",
    )
    parser.add_argument(
        "--payload", help="JSON payload. Omit with --from-stdin to read from stdin instead."
    )
    parser.add_argument(
        "--from-stdin",
        action="store_true",
        help="Read the payload JSON from stdin (for hook wiring).",
    )
    parser.add_argument(
        "--quiet", action="store_true", help="Suppress success output (hooks want silent success)."
    )
    return parser.parse_args(argv)


def _load_payload(args: argparse.Namespace) -> dict | list | str:
    if args.from_stdin:
        raw = sys.stdin.read()
    elif args.payload is not None:
        raw = args.payload
    else:
        return {}

    raw = raw.strip()
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        # Non-JSON stdin (e.g. a raw prompt) — wrap as {"text": ...} so
        # the schema constraint (jsonb) still holds and the extractor can
        # still read it.
        return {"text": raw}


def capture(actor: str, kind: str, payload) -> str | None:
    """Insert one episode. Returns the new row's id, or None on failure."""
    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_KEY")
    if not url or not key:
        print("ERROR: SUPABASE_URL / SUPABASE_KEY unset", file=sys.stderr)
        return None

    from supabase import create_client

    try:
        client = create_client(url, key)
        result = (
            client.table("episodes")
            .insert(
                {
                    "actor": actor,
                    "kind": kind,
                    "payload": payload,
                }
            )
            .execute()
        )
        if result.data:
            return result.data[0].get("id")
    except Exception as exc:
        print(f"ERROR: episode capture failed: {exc}", file=sys.stderr)
        return None
    return None


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    payload = _load_payload(args)
    episode_id = capture(args.actor, args.kind, payload)
    if episode_id is None:
        return 1
    if not args.quiet:
        print(f"captured episode {episode_id}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
