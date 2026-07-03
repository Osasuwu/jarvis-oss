"""Principal detection — who is running the current Claude session?

Hooks (PreToolUse and friends) read this to apply principal-aware policy.
See ``docs/security/agent-boundaries.md`` for the full permission matrix.

Detection chain (#429 — simplified after the isatty fallback was found
broken for hook subprocesses):

1. Explicit env ``JARVIS_PRINCIPAL`` — primary signal.
   Accepted values: ``live``, ``autonomous``, ``subagent``, ``supervised``.
2. Claude Code headless / non-interactive env vars → ``autonomous``.
3. Default → ``live``.

**Why no isatty fallback**: hook subprocesses always receive piped JSON via
stdin, so ``sys.stdin.isatty()`` returns False even in fully interactive
sessions. The original chain mis-classified live owners as ``autonomous``
inside hooks — a hard regression for ``protected-files.py`` decisions.

**Contract for autonomous entry points**: launchers that run Claude headless
(the reactive-core executor, any cron/task wrapper) MUST set
``JARVIS_PRINCIPAL`` explicitly. The APScheduler scheduler service that
formerly demonstrated this via NSSM ``AppEnvironmentExtra=JARVIS_PRINCIPAL=autonomous``
was retired in #743 (replaced by the event-driven ``agents/wake_driver.py``);
the executor that spawns ``claude -p`` is the headless launcher now bound by
this rule.

Tier model is shared with ``agents.safety`` (T0=AUTO, T1=OWNER_QUEUE,
T2=BLOCKED). This module covers the orthogonal "principal" axis; concrete
permission decisions are ``(action_tier, principal)`` lookups documented
in the agent-boundaries doc.
"""

from __future__ import annotations

import os
from typing import Literal

Principal = Literal["live", "autonomous", "subagent", "supervised"]

VALID_PRINCIPALS: frozenset[str] = frozenset(
    {"live", "autonomous", "subagent", "supervised"}
)

# Claude Code env vars that signal headless / non-interactive execution. The
# exact name has shifted across releases; treat any of them being set (to a
# truthy value) as a "no human attached" signal. This is the suspenders that
# back up an explicit ``JARVIS_PRINCIPAL`` going missing.
_HEADLESS_ENV_VARS: tuple[str, ...] = (
    "CLAUDE_CODE_NON_INTERACTIVE",
    "CLAUDE_CODE_HEADLESS",
    "CLAUDE_HEADLESS",
)


def _explicit_env() -> str | None:
    """Return the explicit principal from ``JARVIS_PRINCIPAL`` if valid, else None."""
    raw = os.environ.get("JARVIS_PRINCIPAL")
    if not raw:
        return None
    val = raw.strip().lower()
    return val if val in VALID_PRINCIPALS else None


def _is_headless_env() -> bool:
    """True if any Claude Code headless / non-interactive env var is truthy."""
    for name in _HEADLESS_ENV_VARS:
        v = os.environ.get(name)
        if v and v.strip() and v.strip().lower() not in {"0", "false", "no"}:
            return True
    return False


def detect() -> Principal:
    """Return the active principal for the current process.

    Resolution order (#429):
    1. ``JARVIS_PRINCIPAL`` env (explicit, primary)
    2. Headless env var → ``autonomous``
    3. Default → ``live``

    Autonomous entry points must set ``JARVIS_PRINCIPAL`` explicitly. We
    cannot fall back to ``isatty()`` because hook subprocesses always have
    piped stdin, which would mis-classify interactive sessions as autonomous.
    """
    explicit = _explicit_env()
    if explicit is not None:
        return explicit  # type: ignore[return-value]
    if _is_headless_env():
        return "autonomous"
    return "live"


if __name__ == "__main__":
    # Ad-hoc inspection: ``python scripts/principal.py``
    print(detect())
