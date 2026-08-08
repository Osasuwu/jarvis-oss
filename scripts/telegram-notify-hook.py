"""Telegram notify hook — drain high-severity unprocessed events to owner's Telegram.

Part of flag-only escalation ladder (#327). Day-3 rung of the ladder:
`autonomous-loop` emits `severity=high` events when a flag-only finding has
been ignored N>=3 days; this hook reads those events and sends a one-line
message to the owner via Telegram Bot API, then marks them processed.

Can be invoked:
- At the tail of `autonomous-loop` (recommended — runs once daily)
- As a scheduled task (hourly drain)
- Manually to flush pending notifications

Usage:
    python scripts/telegram-notify-hook.py              # drain all pending high/critical events
    python scripts/telegram-notify-hook.py --dry-run    # print what would be sent
    python scripts/telegram-notify-hook.py --min-severity critical
    python scripts/telegram-notify-hook.py --limit 10

Env: SUPABASE_URL, SUPABASE_KEY, TELEGRAM_BOT_TOKEN, TELEGRAM_ALLOW_USER_ID. .env auto-loaded.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

try:
    from dotenv import load_dotenv

    here = Path(__file__).resolve().parent
    for candidate in (here.parent / ".env", here.parent.parent / ".env"):
        if candidate.exists():
            load_dotenv(candidate, override=True)
            break
except ImportError:
    pass

try:
    import httpx
except ImportError:
    httpx = None


TELEGRAM_API = "https://api.telegram.org"
SEVERITIES = ("critical", "high", "medium", "low", "info")
DEFAULT_LIMIT = 20


def fetch_pending_events(client, min_severity: str, limit: int) -> list[dict]:
    """Pending (unprocessed) events at or above `min_severity`, newest first.

    Filters on the FSM ``state='pending'`` column (#739). ``claim_next``
    flips state to ``'claimed'`` before ``mark_processed`` runs; filtering
    on ``state`` (not a boolean flag) means events the orchestrator already
    picked up are not re-notified.
    """
    idx = SEVERITIES.index(min_severity)
    allowed = list(SEVERITIES[: idx + 1])

    result = (
        client.table("events")
        .select("id, event_type, severity, repo, source, title, payload, created_at")
        .eq("state", "pending")
        .in_("severity", allowed)
        .order("created_at", desc=True)
        .limit(limit)
        .execute()
    )
    return result.data or []


def format_message(ev: dict) -> str:
    """One Telegram message per event. Keep it scannable on mobile."""
    severity = (ev.get("severity") or "").upper()
    title = ev.get("title") or "(no title)"
    repo = ev.get("repo") or "unknown"
    event_type = ev.get("event_type") or "unknown"

    payload = ev.get("payload") or {}
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except (ValueError, TypeError):
            payload = {}

    days = payload.get("days_flagged")
    url = payload.get("url")
    detail = payload.get("detail")

    lines = [f"[{severity}] {title}", f"Repo: {repo} | Type: {event_type}"]
    if days is not None:
        lines.append(f"Stale: {days}d unaddressed")
    if detail:
        lines.append(f"Detail: {detail}")
    if url:
        lines.append(url)
    lines.append(f"Event: {ev.get('id')}")
    return "\n".join(lines)


def send_telegram(token: str, chat_id: str, text: str) -> tuple[bool, str]:
    """Send one message via Bot API. Returns (ok, description)."""
    if httpx is None:
        return False, "httpx not available"

    url = f"{TELEGRAM_API}/bot{token}/sendMessage"
    try:
        with httpx.Client(timeout=15.0) as client:
            resp = client.post(
                url,
                json={
                    "chat_id": chat_id,
                    "text": text,
                    "disable_web_page_preview": True,
                },
            )
            body = (
                resp.json()
                if resp.headers.get("content-type", "").startswith("application/json")
                else {}
            )
            if resp.status_code == 200 and body.get("ok"):
                return True, "sent"
            return False, f"HTTP {resp.status_code}: {body.get('description', resp.text[:200])}"
    except Exception as e:
        return False, f"{type(e).__name__}: {e}"


def mark_processed(client, event_id: str, action: str) -> bool:
    """Mark event processed so it won't notify again.

    Sets the FSM ``state='processed'`` column. ``fetch_pending_events`` keys on
    ``state='pending'`` (#739), so before #649 this drain — which set only the
    legacy ``processed=True`` flag — left ``state='pending'`` and re-notified
    every sent event on the next run. Writing ``state='processed'`` closes that
    re-send loop. The legacy ``processed`` boolean itself was dropped in #1391.

    The update guards on ``.eq("state","pending")``: this batch drain is a
    side-channel that only claims events *still awaiting triage* and defers
    anything the orchestrator has already advanced to ``claimed`` (the
    single-event ``claim_next`` RPC path). Guarding on ``pending`` — not
    ``claimed`` — means a concurrent orchestrator claim is never clobbered.
    It deliberately does NOT delegate to the ``claim_next`` / ``mark_processed``
    RPCs: those transition ``pending -> claimed -> processed`` and would force
    this drain to first *claim* the row, stealing it from the orchestrator.

    Returns ``True`` when the write settled without error — either the pending
    row was flipped to ``processed`` (the normal path) or no pending row matched
    because the orchestrator/a concurrent drain already advanced it (a benign
    no-op; the row is no longer ``pending`` so it will not be re-fetched). The
    benign no-match is signalled by the rowcount INFO, NOT by an exception, so
    the ``except`` below is reached ONLY on a real DB/network failure. That case
    is systematic — the event stays ``state='pending'`` and re-notifies on the
    next run, exactly the #649 re-send loop — so it is surfaced as ERROR and
    returns ``False`` for the caller to fold into the run's exit status. Do not
    downgrade it back to a WARN-and-continue: a swallowed failure looks like a
    clean run while silently reintroducing the loop.
    """
    now = datetime.now(timezone.utc).isoformat()
    try:
        result = (
            client.table("events")
            .update(
                {
                    "processed_at": now,
                    "processed_by": "telegram-notify-hook",
                    "action_taken": action,
                    "state": "processed",
                }
            )
            .eq("id", event_id)
            .eq("state", "pending")
            .execute()
        )
        if not (getattr(result, "data", None) or []):
            # No row matched the pending guard — the event was already claimed
            # or processed (orchestrator or a concurrent drain got there first).
            # Not an error: the notification side-effect already fired above.
            print(
                f"INFO: {event_id} not marked processed "
                "(no pending row — already claimed/processed elsewhere)",
                file=sys.stderr,
            )
        return True
    except Exception as e:
        # Real DB/network failure — NOT the benign no-match (that returns above
        # with empty data). The event stays 'pending' and re-notifies next run
        # (#649 loop). Surface loudly and let the caller flag the run failed.
        print(
            f"ERROR: failed to mark {event_id} processed — event stays pending "
            f"and WILL re-notify next run: {e}",
            file=sys.stderr,
        )
        return False


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Drain high-severity events to owner's Telegram (#327 escalation ladder)"
    )
    parser.add_argument(
        "--min-severity",
        default="high",
        choices=SEVERITIES,
        help="Minimum severity to notify (default: high)",
    )
    parser.add_argument("--limit", type=int, default=DEFAULT_LIMIT, help="Max events per run")
    parser.add_argument("--dry-run", action="store_true", help="Print what would be sent")
    args = parser.parse_args()

    token = (os.environ.get("TELEGRAM_BOT_TOKEN") or "").strip()
    chat_id = (os.environ.get("TELEGRAM_ALLOW_USER_ID") or "").strip()

    if not args.dry_run and (not token or not chat_id):
        print(
            "Error: TELEGRAM_BOT_TOKEN and TELEGRAM_ALLOW_USER_ID must be set. "
            "(Use --dry-run to preview without sending.)",
            file=sys.stderr,
        )
        return 1

    try:
        from supabase import create_client
    except ImportError:
        print("Error: supabase library not available", file=sys.stderr)
        return 1

    supa_url = os.environ.get("SUPABASE_URL")
    supa_key = os.environ.get("SUPABASE_KEY")
    if not supa_url or not supa_key:
        print("Error: SUPABASE_URL and SUPABASE_KEY must be set", file=sys.stderr)
        return 1

    client = create_client(supa_url, supa_key)
    events = fetch_pending_events(client, args.min_severity, args.limit)

    if not events:
        print(f"No pending events at severity >= {args.min_severity}.")
        return 0

    print(f"Draining {len(events)} pending events (min severity: {args.min_severity})")
    sent = 0
    failed = 0
    mark_failed = 0

    for ev in events:
        msg = format_message(ev)
        if args.dry_run:
            print("--- [DRY RUN] would send ---")
            print(msg)
            print()
            continue

        ok, detail = send_telegram(token, chat_id, msg)
        if ok:
            sent += 1
            if mark_processed(client, ev["id"], f"telegram sent: {detail}"):
                print(f"[OK] {ev['id']} — sent")
            else:
                # Message went out but the processed-write failed: the event
                # will re-notify next run. Counted separately so a systematic
                # DB failure at the mark step doesn't read as a clean drain.
                mark_failed += 1
                print(f"[WARN] {ev['id']} — sent but NOT marked (will re-notify)", file=sys.stderr)
        else:
            failed += 1
            print(f"[FAIL] {ev['id']} — {detail}", file=sys.stderr)

    if args.dry_run:
        print(f"DRY RUN complete. Would have sent {len(events)} messages.")
    else:
        print(f"Done. sent={sent} failed={failed} mark_failed={mark_failed}")

    return 0 if (failed == 0 and mark_failed == 0) else 2


if __name__ == "__main__":
    sys.exit(main())
