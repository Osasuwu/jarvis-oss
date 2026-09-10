"""Plan-review change-set classifier (issue #1685; ordinal vocabulary +
:func:`label_for` per #1707).

Deep module, narrow interface: :func:`classify` is the single entry point
consumed by four call sites (interactive lane, drain, container pick,
CI diff-gate) — classification logic lives here once, not re-derived
per caller. Every threshold, glob, and criterion comes from a
:class:`~agents.plan_review_config.PlanReviewConfig` — nothing here is
hardcoded.

Classification is two-point per decision `d34dd65a`: the same
:func:`classify` call is used ex-ante (at admission, from an estimated
change-set) and ex-post (on the actual diff after the CI diff-gate runs).

:func:`classify` returns an ordinal ``1 | 2 | 3`` — not a ``class:N``
string. :func:`label_for` is the single place that maps the ordinal to
its board label (``2 -> "afk:2-plan"``, ``3 -> "afk:3-human"``, ``1 ->
None`` — class 1 is labelless). Consumers must not re-derive label
strings themselves.
"""

from __future__ import annotations

import fnmatch
from dataclasses import dataclass
from typing import Any

from agents.plan_review_config import PlanReviewConfig


@dataclass(frozen=True)
class ChangeSet:
    """A change-set description — the classifier's sole input shape."""

    paths: tuple[str, ...]
    churn_lines: int
    prod_areas: int
    mechanical_criteria: tuple[str, ...] = ()


def _touches_shared_surface(paths: tuple[str, ...], globs: tuple[str, ...]) -> bool:
    return any(fnmatch.fnmatch(p, g) for p in paths for g in globs)


def prod_areas_from_paths(paths: tuple[str, ...]) -> int:
    """Distinct production areas touched, excluding `tests/` (AC per #1685).

    Area = the top-level path component (directory, or the bare filename
    for a top-level file like `.mcp.json`) — the same unit
    `config/plan_review.yaml` documents for `min_prod_areas`. Shared by
    every caller that needs to derive `prod_areas` from a path list (the CI
    diff-gate from a real diff, the interactive lane from an estimate) —
    one implementation, not a copy per caller.
    """
    areas = set()
    for path in paths:
        top = path.split("/", 1)[0]
        if top == "tests":
            continue
        areas.add(top)
    return len(areas)


_DOCS_ONLY_CRITERION = "docs-only"

# Root markdown that is executable process, not documentation about it: an
# agent reads these as rules, so a change to one is a behavior change.
_NON_DOC_ROOT_MARKDOWN = frozenset({"AGENTS.md", "CLAUDE.md"})


def _is_documentation_path(path: str) -> bool:
    """Deliberately narrow: `docs/**`, plus root-level markdown that is not a
    rules file. Everything else — `.claude/**` skills and hooks above all —
    is agent behavior or code, and exempting it would be a real hole.
    """
    if path.startswith("docs/"):
        return True
    if "/" in path:
        return False
    return path.endswith(".md") and path not in _NON_DOC_ROOT_MARKDOWN


def derive_mechanical_criteria(paths: tuple[str, ...]) -> tuple[str, ...]:
    """Criteria inferable from the paths alone (#1818).

    `mechanical_criteria` has no producer in either lane — the CI diff-gate's
    envelope carries no such key and `/implement` §3b passes none — so the
    exempt short-circuit in :func:`classify` was unreachable in production and
    a documentation-only change classified on raw thresholds. Deriving here,
    inside the one entry point that already holds both config and paths, fixes
    every call site at once without touching a single caller's signature.

    Naming a criterion is not the same as acting on it: the config still
    decides whether `docs-only` exempts (drop it from `exempt` and this
    derivation stops mattering).
    """
    if paths and all(_is_documentation_path(p) for p in paths):
        return (_DOCS_ONLY_CRITERION,)
    return ()


def classify(config: PlanReviewConfig, change: ChangeSet) -> int:
    """Return the ordinal classification 1, 2, or 3 for ``change``.

    Criteria are taken from ``change.mechanical_criteria`` when the caller
    supplied any, and otherwise derived from the paths (#1818) — an explicit
    criterion always wins over a derived one, so a caller naming a true-HITL
    criterion is never downgraded by documentation-shaped paths.

    Precedence (#1707): an exempt mechanical criterion short-circuits to 1
    — a docs-only/typo-fix/etc. change never escalates even if it happens
    to touch a shared-surface path (e.g. a doc file living under a
    shared-surface glob). Failing that, a true-HITL class_3 criterion wins
    at 3, even when a class_2 threshold also matches — a change that
    cannot close without a human is never downgraded to "just needs a
    plan". Otherwise class 2 triggers when any of the three thresholds
    trip (shared-surface glob hit, churn strictly above threshold, or
    prod-areas at or above the minimum). Default is 1.

    Use :func:`label_for` to map the ordinal to its board label.
    """
    criteria = change.mechanical_criteria or derive_mechanical_criteria(change.paths)

    if any(c in config.exempt.mechanical_criteria for c in criteria):
        return 1

    if any(c in config.class_3.mechanical_criteria for c in criteria):
        return 3

    if _touches_shared_surface(change.paths, config.class_2.shared_surface_globs):
        return 2
    if change.churn_lines > config.class_2.churn_threshold:
        return 2
    if change.prod_areas >= config.class_2.min_prod_areas:
        return 2

    return 1


def label_for(cls: int) -> str | None:
    """Map a :func:`classify` ordinal to its board label (#1707).

    Class 1 is labelless — ``sandcastle`` alone already conveys it, so no
    code path should write an ``afk:1-*`` label.
    """
    return {2: "afk:2-plan", 3: "afk:3-human"}.get(cls)


def classify_task_row(config: PlanReviewConfig, row: dict[str, Any]) -> int:
    """Named class-2 bundle policy, readable per task row (AC7).

    The single entry point every consumer (interactive lane, drain,
    container pick, CI diff-gate) calls against a ``task_queue``-shaped
    dict — same ``scope_files`` key convention as the reactive-core modules
    this classified for before they were demolished in #1802. Callers never
    re-derive the threshold conditions themselves; they read a task row and
    call this function.
    Missing fields default to the least-alarming values rather than
    raising, matching the ``row.get(...)`` convention used elsewhere in
    ``agents/``.
    """
    change = ChangeSet(
        paths=tuple(row.get("scope_files") or ()),
        churn_lines=int(row.get("churn_lines") or 0),
        prod_areas=int(row.get("prod_areas") or 0),
        mechanical_criteria=tuple(row.get("mechanical_criteria") or ()),
    )
    return classify(config, change)
