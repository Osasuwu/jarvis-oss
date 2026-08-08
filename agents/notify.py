"""Telegram notifier for orchestrator escalations (#1385 AC-D).

Lifted from ``scripts/telegram-notify-hook.py``'s ``send_telegram`` helper,
but scoped to a single :class:`agents.orchestrator.Decision` rather than a
raw Supabase event row — ``dispatch``'s ``ESCALATE`` branch has only the
routing verdict, not the original event payload. Env-gated exactly like the
hook: unset ``TELEGRAM_BOT_TOKEN``/``TELEGRAM_ALLOW_USER_ID`` means
"notifications not configured yet", not an error.

This is called from inside a live ``wake_driver`` tick (via
``dispatch``/``build_production_orchestrator``), so it must never raise — a
notification failure would otherwise abort an event that was routed and
enqueued successfully. ``dispatch`` also wraps the call in a try/except as
defense-in-depth; the no-raise contract here is the primary guarantee.
"""

from __future__ import annotations

import logging
import os
from typing import TYPE_CHECKING

try:
    import httpx
except ImportError:  # pragma: no cover - exercised only when httpx is absent
    httpx = None  # type: ignore[assignment]

if TYPE_CHECKING:
    from agents.orchestrator import Decision

logger = logging.getLogger(__name__)

TELEGRAM_API = "https://api.telegram.org"


def _format_message(decision: "Decision") -> str:
    lines = [
        f"[{decision.severity.upper()}] {decision.event_type}",
        f"Target: {decision.target}",
    ]
    if decision.escalated_reason:
        lines.append(f"Reason: {decision.escalated_reason}")
    if decision.goal:
        lines.append(f"Goal: {decision.goal}")
    return "\n".join(lines)


def telegram_notifier(decision: "Decision") -> bool:
    """Send one Telegram message for an escalated :class:`Decision`.

    Returns ``True`` only on a confirmed send; every other case (missing
    env, missing ``httpx``, HTTP failure, network exception) logs a warning
    and returns ``False`` — never raises.
    """
    token = (os.environ.get("TELEGRAM_BOT_TOKEN") or "").strip()
    chat_id = (os.environ.get("TELEGRAM_ALLOW_USER_ID") or "").strip()
    if not token or not chat_id:
        logger.warning(
            "telegram_notifier: TELEGRAM_BOT_TOKEN/TELEGRAM_ALLOW_USER_ID not set "
            "— skipping notification for %s/%s",
            decision.event_type,
            decision.severity,
        )
        return False

    if httpx is None:
        logger.warning("telegram_notifier: httpx not installed — skipping notification")
        return False

    text = _format_message(decision)
    try:
        with httpx.Client(timeout=15.0) as client:
            resp = client.post(
                f"{TELEGRAM_API}/bot{token}/sendMessage",
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
                return True
            logger.warning(
                "telegram_notifier: send failed HTTP %s: %s",
                resp.status_code,
                body.get("description", resp.text[:200]),
            )
            return False
    except Exception as exc:
        logger.warning("telegram_notifier: %s: %s", type(exc).__name__, exc)
        return False
