"""Recurring obligations registry and evaluate() pure function (#1592, #1593).

Public interface:
    load_registry(path) -> list[ObligationEntry]
    evaluate(registry, acks, now, probes=None) -> list[ObligationStatus]
    render_section(statuses, ack_source_error=None) -> str
    ProbeResult(citation)  — evidence carrier; citation must be non-empty

The registry is a versioned YAML file in the repository. Entries are
added and removed exclusively by humans via PR — no code path mutates
the file. evaluate() is a pure function with zero I/O.

Catch-up policy (per entry):
    "single" — cap missed_periods at 1; high-frequency obligations (daily,
               weekly) should not accumulate a stack of copies when skipped.
    "all"    — track actual missed_periods; rare obligations (monthly,
               quarterly) must not disappear silently after a long gap.

Status rules:
    "unknown"  — no ack and no evidence trace; the system never invents history.
    "ok"       — last_done is observed AND now ≤ last_done + cadence.
    "overdue"  — last_done is observed AND now > last_done + cadence.
    "overdue" is IMPOSSIBLE without an observed last_done date.

Evidence-probe rules (#1593):
    A registry entry may carry an optional probe_name that names an injectable
    callable. When evaluate() receives a probes dict, entries with a matching
    probe_name call that callable instead of relying solely on acks.

    Fired probe (succeeds) → must return ProbeResult with non-empty citation.
        ProbeResult with empty citation raises ValueError at construction time —
        a verdict without evidence cannot be disputed and is therefore invalid.
        evaluate() propagates the ValueError as a probe failure.

    Failed probe (raises, returns None, or returns invalid result) → status
        degrades to "unknown", probe_failed=True. The probe failure is marked
        in the section provenance; the entry is never classified "overdue" due
        to probe failure alone.

    Entry without probe (or probe_name not in probes dict) → ack-only logic,
        unchanged from the baseline behaviour.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Literal, Optional

import yaml

# ceiling: monthly≈30d, quarterly≈91d — good enough for obligation tracking;
# switch to calendar-aware arithmetic if sub-week precision matters.
_CADENCE_DAYS: dict[str, int] = {
    "daily": 1,
    "weekly": 7,
    "monthly": 30,
    "quarterly": 91,
}

_VALID_UNITS = tuple(_CADENCE_DAYS)
_VALID_CATCH_UP = ("single", "all")


# ============================================================================
# Data types
# ============================================================================


@dataclass
class ProbeResult:
    """Evidence returned by a successful evidence-probe callable.

    citation is mandatory and must be non-empty — it is what a verdict can
    be disputed against. Constructing a ProbeResult with an empty citation
    raises ValueError immediately; there is no valid ProbeResult without evidence.
    """

    citation: str

    def __post_init__(self) -> None:
        if not isinstance(self.citation, str) or not self.citation.strip():
            raise ValueError(
                "ProbeResult.citation must be a non-empty string — "
                "a verdict without evidence cannot be disputed"
            )


@dataclass
class ObligationEntry:
    """Parsed registry entry."""

    id: str
    label: str
    cadence_unit: str
    cadence_every: int
    catch_up: Literal["single", "all"]
    probe_name: Optional[str] = None  # names the probe callable; None = ack-only


@dataclass
class AckEvent:
    """Explicit 'done' mark, giving a date to entries without machine traces."""

    obligation_id: str
    date: date


@dataclass
class ObligationStatus:
    """Result of evaluate() for a single registry entry."""

    id: str
    label: str
    status: Literal["ok", "overdue", "unknown"]
    last_done: Optional[date]
    next_due: Optional[date]
    missed_periods: int
    probe_citation: Optional[str] = None  # non-None when probe fired successfully
    probe_failed: bool = False  # True when probe ran but failed -> "unknown"


# ============================================================================
# Registry loading
# ============================================================================


def load_registry(path: Path | str) -> list[ObligationEntry]:
    """Parse obligations registry YAML; raise ValueError on any broken entry.

    Never adds or removes entries — returns a fresh list every call.
    """
    path = Path(path)
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))

    if not isinstance(raw, dict):
        raise ValueError(f"obligations registry must be a YAML mapping, got {type(raw).__name__}")

    entries_raw = raw.get("entries", [])
    if not isinstance(entries_raw, list):
        raise ValueError("'entries' must be a list")

    result: list[ObligationEntry] = []
    for idx, item in enumerate(entries_raw):
        _validate_entry(item, idx)
        result.append(
            ObligationEntry(
                id=item["id"],
                label=item["label"],
                cadence_unit=item["cadence"]["unit"],
                cadence_every=int(item["cadence"].get("every", 1)),
                catch_up=item.get("catch_up", "single"),
                probe_name=item.get("probe"),
            )
        )
    return result


def _validate_entry(item: object, idx: int) -> None:
    prefix = f"entry[{idx}]"
    if not isinstance(item, dict):
        raise ValueError(f"{prefix}: must be a mapping, got {type(item).__name__}")
    for req in ("id", "label", "cadence"):
        if req not in item:
            raise ValueError(f"{prefix}: missing required field '{req}'")
    cadence = item["cadence"]
    if not isinstance(cadence, dict):
        raise ValueError(f"{prefix}.cadence: must be a mapping, got {type(cadence).__name__}")
    if "unit" not in cadence:
        raise ValueError(f"{prefix}.cadence: missing 'unit'")
    unit = cadence["unit"]
    if unit not in _VALID_UNITS:
        raise ValueError(f"{prefix}.cadence.unit: must be one of {_VALID_UNITS}, got {unit!r}")
    if "every" in cadence:
        every = cadence["every"]
        if not isinstance(every, int) or isinstance(every, bool) or every < 1:
            raise ValueError(f"{prefix}.cadence.every: must be a positive integer, got {every!r}")
    catch_up = item.get("catch_up", "single")
    if catch_up not in _VALID_CATCH_UP:
        raise ValueError(f"{prefix}.catch_up: must be one of {_VALID_CATCH_UP}, got {catch_up!r}")
    probe = item.get("probe")
    if probe is not None and not isinstance(probe, str):
        raise ValueError(f"{prefix}.probe: must be a string if present, got {type(probe).__name__}")


# ============================================================================
# Pure evaluation
# ============================================================================


def _period_days(entry: ObligationEntry) -> int:
    return _CADENCE_DAYS[entry.cadence_unit] * entry.cadence_every


def evaluate(
    registry: list[ObligationEntry],
    acks: list[AckEvent],
    now: date,
    probes: dict[str, Callable[[], ProbeResult]] | None = None,
) -> list[ObligationStatus]:
    """Compute status for each obligation entry.

    Pure function — no I/O, no side effects. Returns one ObligationStatus per
    registry entry in the same order.

    Args:
        registry: parsed obligation entries (from load_registry or in-memory fixtures).
        acks: observed 'done' events; multiple acks for one obligation are allowed.
        now: the current date, injected so tests can control time.
        probes: optional dict mapping probe_name to callable. When an entry has a
            probe_name and the name is present in this dict, the callable is invoked
            instead of the ack-only logic. The callable must return a ProbeResult
            with a non-empty citation; any exception (including ValueError from an
            empty-citation ProbeResult) is caught and treated as a probe failure.

    Returns:
        List of ObligationStatus, one per entry.
    """
    _probes: dict[str, Callable[[], ProbeResult]] = probes or {}

    # Build ack index: obligation_id → sorted list of ack dates
    ack_index: dict[str, list[date]] = {}
    for ack in acks:
        ack_index.setdefault(ack.obligation_id, []).append(ack.date)
    for dates in ack_index.values():
        dates.sort()

    result: list[ObligationStatus] = []
    for entry in registry:
        # --- Evidence probe takes precedence when configured and provided ---
        # ceiling: probe_fn() runs synchronously with no timeout/cancellation, and
        # the broad `except Exception` below collapses every failure mode (a
        # hanging probe, a legitimate "not done yet" signal, a bug inside the
        # probe) into the same probe_failed=True/"unknown" result with no
        # diagnostic retained. Fine while probes are small local callables
        # (git log greps, file checks); if a probe ever does network I/O or can
        # run long, wrap the call with a timeout and log the caught exception
        # instead of discarding it.
        if entry.probe_name is not None and entry.probe_name in _probes:
            probe_fn = _probes[entry.probe_name]
            try:
                probe_result = probe_fn()
                if not isinstance(probe_result, ProbeResult):
                    raise TypeError(
                        f"probe '{entry.probe_name}' returned "
                        f"{type(probe_result).__name__!r}, expected ProbeResult"
                    )
                # Valid probe result (citation already validated in __post_init__)
                period = _period_days(entry)
                result.append(
                    ObligationStatus(
                        id=entry.id,
                        label=entry.label,
                        status="ok",
                        last_done=now,
                        next_due=now + timedelta(days=period),
                        missed_periods=0,
                        probe_citation=probe_result.citation,
                        probe_failed=False,
                    )
                )
            except Exception:
                # Any failure (including ValueError from empty-citation ProbeResult)
                # degrades to "unknown" — never "overdue" on a probe failure.
                result.append(
                    ObligationStatus(
                        id=entry.id,
                        label=entry.label,
                        status="unknown",
                        last_done=None,
                        next_due=None,
                        missed_periods=0,
                        probe_citation=None,
                        probe_failed=True,
                    )
                )
            continue

        # --- No probe (or probe_name not in probes dict) -> ack-only logic ---
        ack_dates = ack_index.get(entry.id, [])
        last_done: date | None = ack_dates[-1] if ack_dates else None

        if last_done is None:
            # No observed date → "unknown"; never claim "overdue" without evidence
            result.append(
                ObligationStatus(
                    id=entry.id,
                    label=entry.label,
                    status="unknown",
                    last_done=None,
                    next_due=None,
                    missed_periods=0,
                )
            )
            continue

        period = _period_days(entry)
        next_due = last_done + timedelta(days=period)

        if now <= next_due:
            result.append(
                ObligationStatus(
                    id=entry.id,
                    label=entry.label,
                    status="ok",
                    last_done=last_done,
                    next_due=next_due,
                    missed_periods=0,
                )
            )
        else:
            days_overdue = (now - next_due).days
            # How many full periods have elapsed since next_due
            missed = 1 + (days_overdue // period)
            if entry.catch_up == "single":
                missed = 1  # cap — high-frequency obligations must not stack
            result.append(
                ObligationStatus(
                    id=entry.id,
                    label=entry.label,
                    status="overdue",
                    last_done=last_done,
                    next_due=next_due,
                    missed_periods=missed,
                )
            )

    return result


# ============================================================================
# Section rendering
# ============================================================================


def render_section(
    statuses: list[ObligationStatus],
    ack_source_error: str | None = None,
) -> str:
    """Render the obligations section for the morning digest.

    Shows:
    - Overdue entries (with last-done date).
    - Probe-confirmed entries (ok with citation) — citation visible next to label.
    - Probe-failed entries (unknown, probe failed) — marked in provenance.
    - All-ok / empty fallback message when none of the above apply.

    Args:
        statuses: output of evaluate().
        ack_source_error: when set, the ack source could not be reached; print
            empty section with the reason instead of potentially misleading data.

    Returns:
        Section text string. Never empty — either lists entries needing attention,
        probe confirmations with citations, or explains source unavailability.
    """
    if ack_source_error is not None:
        return f"Обязательства: (недоступно — {ack_source_error})"

    overdue = [s for s in statuses if s.status == "overdue"]
    probe_confirmed = [s for s in statuses if s.probe_citation is not None]
    probe_failures = [s for s in statuses if s.probe_failed]

    if not overdue and not probe_failures:
        if probe_confirmed:
            lines = ["Обязательства (подтверждено пробой):"]
            for s in probe_confirmed:
                lines.append(f"  • {s.label} — {s.probe_citation}")
            return "\n".join(lines)
        return "Обязательства: всё своевременно"

    lines: list[str] = []

    if overdue:
        lines.append("Обязательства (просрочено):")
        for status in overdue:
            last = status.last_done.isoformat() if status.last_done else "неизвестно"
            lines.append(f"  • {status.label} — последнее: {last}")

    if probe_confirmed:
        if lines:
            lines.append("")
        lines.append("Обязательства (подтверждено пробой):")
        for s in probe_confirmed:
            lines.append(f"  • {s.label} — {s.probe_citation}")

    if probe_failures:
        if lines:
            lines.append("")
        lines.append("Обязательства (проба не удалась, статус неизвестен):")
        for status in probe_failures:
            lines.append(f"  • {status.label}")

    return "\n".join(lines)
