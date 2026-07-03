"""Action-agent safety gate — S2-0 foundation (issue #295).

Every action agent in Pillar 7 Sprint 2+ that mutates external state
(GitHub, Supabase beyond Sprint-1 allowlist, filesystem) must route
its side effect through this module. Three responsibilities:

1. ``classify(tool_name, action, target, area=...)`` → ``Tier`` per
   ``action_agent_safety_gate_model_v1`` (see memory of the same
   name). Tier 0 = auto-allowed, Tier 1 = owner-approve queue, Tier 2
   = blocked outright.
2. ``idempotency_key(agent_id, action, target, scope_hash=...)`` →
   deterministic sha256 key so re-running an agent with the same
   inputs does not re-fire the action.
3. ``gate(..., fn=..., dry_run=...)`` → run ``fn`` only if Tier 0 and
   not dry-run; audit every classification/attempt via
   ``supabase_client.audit`` (best-effort).

The tiered whitelist/blocklist here mirrors the model memory exactly.
When the owner moves an action between tiers, update this module and
the memory in the same change.

``action_queue`` enqueueing for Tier 1 lives in S2-1 (``task_queue``
migration). Until that ships, ``gate()`` records Tier 1 attempts via
audit but does not persist to a dedicated queue; ``GateOutcome.queued``
still flips so callers can react.
"""

from __future__ import annotations

import hashlib
import logging
import os
from dataclasses import dataclass
from enum import IntEnum
from typing import Callable, Iterable, TypeVar

from agents import supabase_client

log = logging.getLogger(__name__)


class Tier(IntEnum):
    """Safety tier per ``action_agent_safety_gate_model_v1``."""

    AUTO = 0
    OWNER_QUEUE = 1
    BLOCKED = 2


# ---------------------------------------------------------------------------
# Tier 0 whitelist (auto-allowed)
# ---------------------------------------------------------------------------

# Narrow GitHub labels per model memory. ``priority:critical`` intentionally
# absent — big claims need a human.
_TIER0_GITHUB_LABELS: frozenset[str] = frozenset(
    {
        "priority:high",
        "priority:medium",
        "priority:low",
        "needs-research",
        "needs-triage",
        "status:ready",
    }
)

# area:* labels follow the same namespace-prefix rule (any area:* is Tier 0).
_TIER0_GITHUB_LABEL_PREFIXES: tuple[str, ...] = ("area:",)

# Supabase tables Sprint 1 already allows writing to. Tables outside this set
# are Tier 1 by default. Explicit ``action`` values prevent an UPDATE-all
# sneaking through under the same table name.
_TIER0_SUPABASE = {
    "events": {"insert", "append"},
    "audit_log": {"insert"},
    "goals": {"progress_append", "update_progress"},
}

# Memory store is Tier 0 only when tagged as auto-generated (dry-run flagging,
# not authoritative memory).
_TIER0_MEMORY_TAGS: frozenset[str] = frozenset({"auto-generated"})


# ---------------------------------------------------------------------------
# Tier 2 blocklist (never allowed)
# ---------------------------------------------------------------------------

# File patterns anywhere in the target string trip Tier 2 regardless of
# tool. ``.env`` catches ``.env``, ``.env.local``, etc. ``.claude/`` covers
# skills/settings/hooks per `claude_dir_edits_need_manual_confirm`.
_TIER2_FILE_PATTERNS: tuple[str, ...] = (".env", ".claude/")

# Destructive verbs. Match is exact action-string equality (not substring) to
# avoid false positives like ``delete_comment_draft``.
_TIER2_ACTIONS: frozenset[str] = frozenset(
    {
        "delete",
        "destroy",
        "drop",
        "truncate",
        "force_push",
        "impersonate",
        "send_as_owner",
    }
)

# Tool-name substrings that signal impersonation / owner-send paths. Matched
# case-insensitively. Hard rule per `no_sending_from_owner_name`.
_TIER2_TOOL_NAME_SUBSTRINGS: tuple[str, ...] = (
    "impersonate",
    "send_as_owner",
)

# Areas that are wholesale Tier 2 until explicitly re-tiered.
_TIER2_AREAS: frozenset[str] = frozenset({"messaging"})

# Single-repo scope for Sprint 2. Extend only when we have audit proof it
# works (per model memory).
DEFAULT_ALLOWED_REPO = os.environ.get("GITHUB_REPO", "your-username/your-repo")


# ---------------------------------------------------------------------------
# Classifier
# ---------------------------------------------------------------------------


def classify(
    tool_name: str,
    action: str,
    target: str | None = None,
    *,
    area: str | None = None,
    tags: Iterable[str] | None = None,
    allowed_repo: str = DEFAULT_ALLOWED_REPO,
) -> Tier:
    """Classify an attempted action into a safety tier.

    ``area`` is a coarse category — ``"github"``, ``"supabase"``,
    ``"filesystem"``, ``"memory"``, ``"messaging"`` — used by the
    classifier to choose the right allowlist. Unknown area → Tier 1.

    Resolution order:

    1. Tier 2 (blocked) — takes precedence; an unsafe pattern overrides
       any matching allowlist.
    2. Tier 0 (allowed) — explicit positive match against the narrow
       whitelist for the area.
    3. Tier 1 (owner queue) — default.
    """
    if _is_blocked(tool_name, action, target, area=area, allowed_repo=allowed_repo):
        return Tier.BLOCKED
    if _is_tier0(tool_name, action, target, area=area, tags=tags):
        return Tier.AUTO
    return Tier.OWNER_QUEUE


def _is_blocked(
    tool_name: str,
    action: str,
    target: str | None,
    *,
    area: str | None,
    allowed_repo: str,
) -> bool:
    if area in _TIER2_AREAS:
        return True
    if action in _TIER2_ACTIONS:
        return True
    lowered_tool = (tool_name or "").lower()
    for needle in _TIER2_TOOL_NAME_SUBSTRINGS:
        if needle in lowered_tool:
            return True
    if target:
        # Normalise path separators so Windows targets hit the same rules.
        normalised = target.replace("\\", "/")
        for pattern in _TIER2_FILE_PATTERNS:
            if pattern in normalised:
                return True
    if area == "github" and target:
        repo = _github_repo_of(target)
        if repo and repo != allowed_repo:
            return True
    return False


def _is_tier0(
    tool_name: str,
    action: str,
    target: str | None,
    *,
    area: str | None,
    tags: Iterable[str] | None,
) -> bool:
    if area == "github" and action == "add_label":
        if not target:
            return False
        if target in _TIER0_GITHUB_LABELS:
            return True
        return any(target.startswith(prefix) for prefix in _TIER0_GITHUB_LABEL_PREFIXES)
    if area == "supabase":
        allowed_actions = _TIER0_SUPABASE.get(target or "")
        if allowed_actions and action in allowed_actions:
            return True
    if area == "memory" and action == "store":
        tagset = set(tags or ())
        if tagset & _TIER0_MEMORY_TAGS:
            return True
    return False


def _github_repo_of(target: str) -> str | None:
    """Extract ``owner/repo`` prefix from a GitHub target string.

    Accepts shapes like ``owner/repo#123``, ``owner/repo/path/to/file``,
    or a bare ``owner/repo``. Returns ``None`` if the shape doesn't
    resemble a repo reference (e.g. a label string like ``priority:high``
    — those aren't repo-scoped and should not trip cross-repo checks).
    """
    if "/" not in target:
        return None
    # A label ``area:backend`` contains ``:``, never ``/``, so the guard
    # above rejects it. Repo refs always have a slash between owner and
    # repo segments.
    head, _, _ = target.partition("#")
    parts = head.split("/", 2)
    if len(parts) < 2 or not parts[0] or not parts[1]:
        return None
    return f"{parts[0]}/{parts[1]}"


# ---------------------------------------------------------------------------
# Idempotency
# ---------------------------------------------------------------------------


def idempotency_key(
    agent_id: str,
    action: str,
    target: str | None = None,
    scope_hash: str | None = None,
) -> str:
    """Deterministic sha256 over the action inputs.

    Same ``(agent_id, action, target, scope_hash)`` → same key. Callers
    store the key on the action row (audit, action queue, or queue_row)
    and skip a live-run if the key already exists.

    ``scope_hash`` changes with repo state; feeding it in means post-drift
    re-attempts produce a different key — no silent coalescing with the
    pre-drift attempt. (The ``approved_scope_hash`` column it once mirrored
    was dropped from ``task_queue`` in #740; this helper stays
    column-agnostic — it hashes whatever scope string the caller passes.)
    """
    raw = "|".join([agent_id or "", action or "", target or "", scope_hash or ""])
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Gate
# ---------------------------------------------------------------------------


class GateError(RuntimeError):
    """Raised when a Tier 2 action is attempted through ``gate``."""


F = TypeVar("F", bound=Callable[..., object])


@dataclass(frozen=True)
class GateOutcome:
    """Result of a ``gate`` call.

    - ``tier`` — classification that decided the outcome.
    - ``fired`` — ``fn`` actually ran (Tier 0, not dry-run).
    - ``queued`` — classified as Tier 1; caller should enqueue (when
      ``task_queue`` ships in S2-1, the gate itself will enqueue).
    - ``dry_run`` — caller passed ``dry_run=True`` and no mutation was
      attempted.
    - ``idempotency_key`` — key recorded on the audit row; callers can
      dedupe on re-run.
    """

    tier: Tier
    fired: bool
    queued: bool
    dry_run: bool
    idempotency_key: str


def gate(
    *,
    agent_id: str,
    tool_name: str,
    action: str,
    target: str | None = None,
    area: str | None = None,
    tags: Iterable[str] | None = None,
    scope_hash: str | None = None,
    dry_run: bool = False,
    fn: Callable[[], object] | None = None,
    allowed_repo: str = DEFAULT_ALLOWED_REPO,
) -> GateOutcome:
    """Run ``fn`` under the safety gate.

    Behaviour matrix:

    +----------+------------+------------------------------------------+
    | tier     | dry_run    | behaviour                                |
    +==========+============+==========================================+
    | BLOCKED  | *          | audit, raise :class:`GateError`          |
    +----------+------------+------------------------------------------+
    | OWNER_Q  | *          | audit (outcome=queued / dry_run_queued), |
    |          |            | return queued=True, do NOT call fn       |
    +----------+------------+------------------------------------------+
    | AUTO     | True       | audit (outcome=dry_run), do NOT call fn  |
    +----------+------------+------------------------------------------+
    | AUTO     | False      | call fn, audit with outcome=success |    |
    |          |            | ``failure:<ExceptionType>``              |
    +----------+------------+------------------------------------------+

    Audit is best-effort; a logging backend outage never blocks the
    action or obscures a genuine ``fn`` exception.
    """
    tier = classify(tool_name, action, target, area=area, tags=tags, allowed_repo=allowed_repo)
    key = idempotency_key(agent_id, action, target, scope_hash)

    if tier == Tier.BLOCKED:
        _audit_best_effort(
            agent_id=agent_id,
            tool_name=tool_name,
            action=action,
            target=target,
            tier=tier,
            outcome="blocked",
            idempotency_key=key,
        )
        raise GateError(
            f"Tier 2 action blocked: tool={tool_name!r} action={action!r} target={target!r}"
        )

    if tier == Tier.OWNER_QUEUE:
        _audit_best_effort(
            agent_id=agent_id,
            tool_name=tool_name,
            action=action,
            target=target,
            tier=tier,
            outcome="dry_run_queued" if dry_run else "queued",
            idempotency_key=key,
        )
        return GateOutcome(
            tier=tier, fired=False, queued=True, dry_run=dry_run, idempotency_key=key
        )

    # Tier.AUTO
    if dry_run:
        _audit_best_effort(
            agent_id=agent_id,
            tool_name=tool_name,
            action=action,
            target=target,
            tier=tier,
            outcome="dry_run",
            idempotency_key=key,
        )
        return GateOutcome(tier=tier, fired=False, queued=False, dry_run=True, idempotency_key=key)

    if fn is None:
        # Classification-only use: caller wants the tier decision and the
        # audit trail but has no side effect to perform here.
        _audit_best_effort(
            agent_id=agent_id,
            tool_name=tool_name,
            action=action,
            target=target,
            tier=tier,
            outcome="classified",
            idempotency_key=key,
        )
        return GateOutcome(tier=tier, fired=False, queued=False, dry_run=False, idempotency_key=key)

    try:
        fn()
    except Exception as exc:
        _audit_best_effort(
            agent_id=agent_id,
            tool_name=tool_name,
            action=action,
            target=target,
            tier=tier,
            outcome=f"failure:{type(exc).__name__}",
            idempotency_key=key,
            error=str(exc),
        )
        raise
    _audit_best_effort(
        agent_id=agent_id,
        tool_name=tool_name,
        action=action,
        target=target,
        tier=tier,
        outcome="success",
        idempotency_key=key,
    )
    return GateOutcome(tier=tier, fired=True, queued=False, dry_run=False, idempotency_key=key)


# ---------------------------------------------------------------------------
# Audit
# ---------------------------------------------------------------------------


def audit(
    *,
    agent_id: str,
    tool_name: str,
    action: str,
    target: str | None = None,
    tier: Tier = Tier.AUTO,
    outcome: str = "success",
    idempotency_key: str | None = None,
    error: str | None = None,
) -> None:
    """Standalone best-effort audit helper.

    Use when the caller has already classified/executed independently
    (e.g. legacy code paths, tests) and just needs to emit the audit
    row with tier + idempotency key in ``details``. ``gate()`` already
    calls this; callers that use ``gate`` should not also call ``audit``
    for the same event or the row count will double.
    """
    _audit_best_effort(
        agent_id=agent_id,
        tool_name=tool_name,
        action=action,
        target=target,
        tier=tier,
        outcome=outcome,
        idempotency_key=idempotency_key or "",
        error=error,
    )


def _audit_best_effort(
    *,
    agent_id: str,
    tool_name: str,
    action: str,
    target: str | None,
    tier: Tier,
    outcome: str,
    idempotency_key: str,
    error: str | None = None,
) -> None:
    details: dict[str, object] = {
        "tier": int(tier),
        "idempotency_key": idempotency_key,
    }
    if error is not None:
        details["error"] = error
    try:
        supabase_client.audit(
            agent_id=agent_id,
            tool_name=tool_name,
            action=action,
            target=target,
            details=details,
            outcome=outcome,
        )
    except Exception as exc:  # noqa: BLE001 — audit must never raise
        log.debug("safety audit swallowed exception: %s", exc)
