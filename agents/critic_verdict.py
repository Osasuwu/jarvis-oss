"""Critic verdict schema, fail-closed resolution, and consensus (#1686).

Each objection carries either a ``resolution`` or ``blocking: True`` plus a
``rationale`` — an objection that is neither resolved nor a rationalized
blocker is malformed input, not a valid disposition (AC6). An absent or
schema-invalid verdict, after exactly one re-run, is treated as an
unresolved blocking objection: fail-closed, never fail-open (AC7).
Consensus requires zero unresolved objections after at most one revision
cycle (AC8).
"""

from __future__ import annotations

from dataclasses import dataclass, field


class InvalidVerdictError(Exception):
    """Raised when a raw verdict/objection payload does not match the schema."""


@dataclass(frozen=True)
class Objection:
    description: str
    resolution: str | None = None
    blocking: bool = False
    rationale: str | None = None

    def is_unresolved(self) -> bool:
        return self.blocking and self.resolution is None


@dataclass(frozen=True)
class Verdict:
    critic: str
    objections: tuple[Objection, ...] = field(default_factory=tuple)

    def has_unresolved_blocking(self) -> bool:
        return any(o.is_unresolved() for o in self.objections)


def validate_objection(raw: dict) -> Objection:
    description = raw.get("description")
    if not description:
        raise InvalidVerdictError("objection missing 'description'")

    resolution = raw.get("resolution")
    blocking = bool(raw.get("blocking", False))
    rationale = raw.get("rationale")

    if resolution is None and not blocking:
        raise InvalidVerdictError(
            "objection must carry either 'resolution' or 'blocking'+'rationale'"
        )
    if blocking and resolution is None and not rationale:
        raise InvalidVerdictError("blocking objection missing 'rationale'")

    return Objection(
        description=description, resolution=resolution, blocking=blocking, rationale=rationale
    )


def validate_verdict(raw: dict) -> Verdict:
    critic = raw.get("critic")
    if not critic:
        raise InvalidVerdictError("verdict missing 'critic'")

    objections_raw = raw.get("objections", [])
    objections = tuple(validate_objection(o) for o in objections_raw)
    return Verdict(critic=critic, objections=objections)


def _forced_unresolved_verdict(reason: str) -> Verdict:
    return Verdict(
        critic="unknown",
        objections=(
            Objection(
                description=f"verdict resolution failed: {reason}",
                blocking=True,
                rationale=reason,
            ),
        ),
    )


def resolve_verdict(raw: dict | None, *, retried: bool) -> Verdict | None:
    """Resolve a raw verdict payload per AC7's fail-closed rule.

    Returns ``None`` when the payload is missing/invalid and a re-run has
    not yet been attempted — the caller should re-run once. After a retry,
    a still-missing or still-invalid payload is forced into an unresolved
    blocking verdict rather than silently passing.
    """
    if raw is None:
        return _forced_unresolved_verdict("no verdict returned") if retried else None

    try:
        return validate_verdict(raw)
    except InvalidVerdictError as exc:
        return _forced_unresolved_verdict(f"schema-invalid verdict: {exc}") if retried else None


def consensus_reached(verdicts: list[Verdict], *, revisions: int) -> bool:
    if revisions > 1:
        return False
    return not any(v.has_unresolved_blocking() for v in verdicts)


def planner_actor(run_id: str) -> str:
    """Actor stamp for planner-role recorded decisions (AC3)."""
    return f"planner:{run_id}"
