"""task_dispatch — close the reactive forward path (#909).

The reactive forward path is now closed end-to-end (#741/#744 → #909 → #921)::

    event → wake_driver → orchestrator.handle_event
          → task_queue.enqueue(row) → drain_tasks → executor.spawn
          → poll_completions → running → done | failed

:func:`drain_tasks` claims pending ``sandcastle`` rows, transitions each to
``running``, and fires ``executor.spawn(goal)`` — symmetric to how
:func:`wake_driver.drain_pending` drains *events*. The spawned processes are
handed back as :attr:`DrainResult.procs`; :func:`poll_completions` (#921)
closes each row when its process exits — exit 0 → ``done``, non-zero →
``failed``. **Model P semantics: ``done`` means the spawned process exited
cleanly, nothing more** — not task success, not PR-merged. Outcome truth
re-enters externally via GitHub Path-A workflows as fresh *events*.

**Restart limitation (#921):** the proc map lives only in the driver process.
A restart forgets every live process — those rows age out and the orphan
reaper (:func:`reclaim_stale_tasks`) fails them as a backstop, which Path A
then self-heals. A PID sidecar that survives restarts is #952.

Design mirrors :mod:`agents.wake_driver`: the pure logic
(:func:`drain_tasks` / :func:`reclaim_stale_tasks`) runs over a
:class:`TaskQueuePort` Protocol, so it is unit-testable with an in-memory fake
(fake queue + fake spawn + fake running-count) — no live DB, no real
``claude -p``. :class:`SupabaseTaskQueue` is the thin real adapter over
:mod:`agents.task_queue` (supabase-py / PostgREST). Events ride raw psycopg
(they need ``LISTEN``); tasks ride supabase-py — the split is deliberate (AC10).

Crash-safety follows **Ordering B** (grill decision ``2489782f``): per task,
``claim → transition(running) → spawn``. Transitioning to ``running`` *before*
the spawn means a crash in the window leaves the row ``running`` (swept by the
generous reaper, AC6) rather than ``claimed`` with a live process — the latter
would let the claimed-reclaimer (AC5) hand the same task to a second spawn.
``claimed`` therefore strictly means *claimed-but-not-yet-spawned*.
"""

from __future__ import annotations

import logging
import os
import re
import subprocess
import sys
import time
from collections.abc import Callable, Collection
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

if TYPE_CHECKING:
    from supabase import Client

from agents import task_queue
from agents.github_client import (
    GitHubClient,
    check_pr_closing_ref_fresh_shape,
    check_pr_evidence_fresh_shape,
    check_pr_evidence_rework_shape,
    parse_executor_stdout,
    parse_goal_shape,
)
from agents.pid_sidecar import Sidecar, poll_exit

logger = logging.getLogger(__name__)

# Only sandcastle rows are auto-spawned; assignee='owner' escalation rows are
# never claimed by the drain (AC2).
DEFAULT_ASSIGNEE = "sandcastle"


def _resolve_concurrency_cap() -> int:
    """Read the sandcastle concurrency cap from REACTIVE_CONCURRENCY_CAP (#1390
    AC8), falling back to 5. register-wake-driver.ps1 sets this to 2 in the
    launched process's environment before the module ever imports, so the
    module-level read below picks it up at process start."""
    return int(os.environ.get("REACTIVE_CONCURRENCY_CAP", "5"))


# Max concurrent running sandcastle tasks (AC3). Measures compute concurrency:
# slots free as soon as poll_completions observes the process exit (#921).
DEFAULT_CONCURRENCY_CAP = _resolve_concurrency_cap()

# A row stuck in ``claimed`` past this long means the drainer died between the
# claim and the running transition — no process exists, so it is safe to return
# to ``pending`` (AC5). Matches the wake_driver event watchdog default.
DEFAULT_CLAIMED_STALE_SECONDS = 300

# One 6h knob, two consumers (#921): rows whose process the driver no longer
# tracks (orphans — e.g. after a restart) are reaped to ``failed`` past this
# age, and *tracked* processes still alive past it are tree-killed as runaways.
# Deliberately generous (≫ normal task runtime); live tracked rows under the
# threshold are never time-reaped (AC5).
DEFAULT_RUNNING_REAP_SECONDS = 6 * 60 * 60

# A retained-failure worktree (#1390 AC6) is kept this long, measured from its
# ``_WORKTREE_FAILED_AT_MARKER`` timestamp, before the sweep TTL-prunes it —
# generous enough to cover a same-day post-mortem without accumulating stale
# trees indefinitely.
DEFAULT_WORKTREE_RETENTION_TTL_SECONDS = 24 * 60 * 60

# Beyond this many retained-failure worktrees, the sweep evicts the oldest
# (by ``_WORKTREE_FAILED_AT_MARKER``) first — a backstop against disk growth
# when failures outpace the TTL, independent of it (#1390 AC6).
DEFAULT_WORKTREE_RETENTION_CAP = 20

# Spawn a task's goal, fire-and-forget. Raises on a hard launch failure (AC7b).
# Called as ``spawn(goal, task_id=<id>)`` — the executor needs the id to write
# the per-task stdout JSON the #953 AC3 evidence channel reads, so the contract
# carries the keyword (``Callable[..., Any]`` to keep the kwarg in the type).
Spawn = Callable[..., Any]
# Resolve the claude binary; raises FileNotFoundError when unresolved (AC7a).
ResolveBinary = Callable[[], str]
# Quota probe — returns a UsageReading-shaped object with .near_exhaustion
# (#921 AC4). The production default is false-safe: it never raises, a probe
# error reads as near-exhaustion, so a broken probe pauses dispatch.
ReadUsage = Callable[[], Any]

# Repo root, mirroring executor._REPO_ROOT — anchors per-task worktree creation
# (#1390 AC3) to the main checkout regardless of the daemon's CWD. Tests
# monkeypatch this attribute to point at a temporary repo.
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Directory the executor writes per-task stdout JSON to (#953 AC3). Mirrors
# executor._STDERR_LOG_DIR; kept local so the reader has no executor import.
# Anchored to the repo root (this module lives in ``agents/``) so reader and
# writer resolve to the SAME absolute dir regardless of the daemon's CWD — a
# CWD-relative default would silently break the AC3 channel when the wake_driver
# and executor run from different directories (LOW, PR #1011 round 3 —
# sibling-anchored with executor._STDERR_LOG_DIR).
_EXECUTOR_LOG_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "logs", "executor"
)

# An idempotency key carries lineage as ``<lineage_key>:r<attempt>``. A root
# task (first spawn) has no ``:rN`` suffix and is attempt 1. ``_LINEAGE_SEP`` is
# the single source of truth for the separator: both the builder
# (:func:`format_lineage_key`) and the parser (:func:`parse_lineage` via
# ``_LINEAGE_RE``) derive from it so the two can never drift (MEDIUM, PR #1011 —
# previously the orchestrator hard-coded ``f"{key}:r{n}"`` while the parser owned
# its own regex; a change to one would silently desync the other).
_LINEAGE_SEP = ":r"
_LINEAGE_RE = re.compile(rf"^(.*){re.escape(_LINEAGE_SEP)}(\d+)$")


def format_lineage_key(lineage_key: str, attempt: int) -> str:
    """Build an idempotency key for a re-drive: ``<lineage_key>:r<attempt>`` (#953 AC7).

    Inverse of :func:`parse_lineage` — they share ``_LINEAGE_SEP`` so the wire
    format stays symmetric. Use this anywhere a re-drive key is minted instead of
    interpolating the separator by hand.
    """
    return f"{lineage_key}{_LINEAGE_SEP}{attempt}"


def parse_lineage(idempotency_key: str) -> tuple[str, int]:
    """Split an idempotency key into ``(lineage_key, attempt)`` (#953 AC7).

    ``"abc:r2"`` → ``("abc", 2)``; a bare key or empty string is the root
    attempt → ``(key, 1)``. The lineage key is stable across re-drives so
    every attempt of one task shares it; the attempt number gates MAX_ATTEMPTS.

    The attempt is the OUTERMOST ``:rN`` suffix (the most recent re-drive), and
    the root has *every* ``:rN`` suffix peeled off — so a doubly-suffixed key
    like ``"abc:r2:r3"`` resolves to ``("abc", 3)``, not ``("abc:r2", 3)``. A
    non-greedy single-strip would leave an inner ``:rN`` in the root and split
    one task's lineage across distinct root keys (MAJOR, #1011).
    """
    if not idempotency_key:
        return ("", 1)
    m = _LINEAGE_RE.match(idempotency_key)
    if not m:
        return (idempotency_key, 1)
    attempt = int(m.group(2))
    root = m.group(1)
    inner = _LINEAGE_RE.match(root)
    while inner:
        root = inner.group(1)
        inner = _LINEAGE_RE.match(root)
    return (root, attempt)


def _augment_branch_directive(goal: str, task_id: str) -> str:
    """Append the ``task/<task_id>`` branch directive to a fresh-shape goal (AC5).

    Only fresh-shape goals lacking an explicit ``(branch=...)`` directive are
    augmented — a rework goal (``/rework #N``) already targets an existing PR's
    branch and must NEVER be augmented (AC5), and a goal that already names a
    branch is left as the author wrote it. The directive embeds the convention
    the evidence check (:func:`check_pr_evidence_fresh_shape`) looks for, so
    spawn-side and evidence-side agree on where the PR should be.
    """
    if "(branch=" in goal:
        return goal
    shape, _ = parse_goal_shape(goal)
    if shape != "fresh":
        return goal
    return f"{goal}\n\n(branch=task/{task_id})"


def _augment_closes_mandate(goal: str, task_id: str) -> str:
    """Append a ``Closes #<N>`` PR-body mandate to a fresh-shape goal (#1136 AC1).

    The executor lane's spawned ``claude -p`` sessions are permitted to run
    ``Bash(gh pr create:*)`` (``executor._SPAWN_ALLOWED_TOOLS``) with no directive
    to link the issue their PR closes — so a merged PR can silently fail to
    auto-close its issue (the #948 failure mode; native linked-issue auto-close is
    suppressed for bot/App-attributed merges, so the closing keyword in the PR body
    is what the ``pr-merged.yml`` close path keys on). Fresh-shape goals naming an
    issue ``#N`` get an explicit mandate appended so the child's PR body carries
    that keyword.

    Fires **iff** the goal is fresh-shape AND names an issue (``#N``). Rework goals
    (``/rework #N``) target an existing PR and are left untouched; a fresh goal with
    no ``#N`` has no close target; an empty goal is a no-op. AC3 escape: the mandate
    permits the child to emit ``Refs #N`` instead when the PR only partially
    addresses the issue — both satisfy the ``require-linked-issue`` merge gate, but
    only ``Closes`` triggers auto-close. Additive like
    :func:`_augment_branch_directive` — the original goal is preserved verbatim as a
    prefix. ``task_id`` is unused (sibling-parity with the branch augmenter); kept in
    the signature for a uniform augmenter shape.
    """
    shape, _ = parse_goal_shape(goal)
    if shape != "fresh":
        return goal
    issue_number = _goal_issue_number(goal)
    if issue_number is None:
        return goal
    return (
        f"{goal}\n\n(PR-body requirement: when you open the PR, put "
        f"`Closes #{issue_number}` on its own line in the body so the merge "
        f"auto-closes the issue. If this PR only partially addresses "
        f"#{issue_number}, use `Refs #{issue_number}` instead — it still satisfies "
        f"the linked-issue merge gate but leaves the issue open.)"
    )


# A task_id is interpolated into the executor-log path, so it must be confined
# to a charset that cannot escape the directory — no ``/``, ``\``, ``.`` (hence
# no ``..``), or other path-significant characters. UUIDs and the alnum ids used
# elsewhere both satisfy this; a crafted ``../../etc/passwd`` does not (LOW,
# PR #1011 — path-traversal hardening on the AC3 secondary channel).
_SAFE_TASK_ID_RE = re.compile(r"\A[A-Za-z0-9_-]+\Z")


def default_stdout_reader(task_id: str) -> str | None:
    """Read the executor's stdout JSON for ``task_id`` (#953 AC3 secondary).

    Returns the file text, or ``None`` when it is absent/unreadable — the
    secondary-evidence path is best-effort, so a missing log degrades the
    check to its primary verdict rather than raising.

    A ``task_id`` outside the safe ``[A-Za-z0-9_-]`` charset (e.g. one carrying
    ``..`` or a path separator) is rejected up front and returns ``None`` — it
    never reaches the filesystem, closing the path-traversal vector (LOW #1011).
    """
    if not _SAFE_TASK_ID_RE.match(task_id):
        logger.warning(
            "default_stdout_reader: refusing unsafe task_id %r (path-traversal guard)",
            task_id,
        )
        return None
    path = os.path.join(_EXECUTOR_LOG_DIR, f"{task_id}.stdout.json")
    try:
        with open(path, encoding="utf-8") as fh:
            return fh.read()
    except OSError:
        return None


def _compute_pr_evidence(
    task_id: str,
    goal: str,
    spawned_at: datetime | None,
    *,
    client: GitHubClient | None,
    stdout_reader: Callable[[str], str | None] | None = None,
) -> tuple[bool | None, bool | None]:
    """Compute PR evidence AND closing-ref status for one completed task.

    Returns ``(pr_evidence, closing_ref)`` — the first element is the PR
    existence tri-state (legacy flow), the second is whether the PR body
    carries a closing ref for the task's issue (#1169). For rework-shape
    goals and goals with no issue reference, ``closing_ref`` is ``None``.

    The closing-ref channel is separate from the evidence tri-state per
    grill decision ``ec66db74`` — the two questions have independent
    None/False/True semantics.
    """
    if client is None or spawned_at is None:
        return (None, None)
    shape, pr_number = parse_goal_shape(goal)
    if shape == "empty":
        return (None, None)
    if shape == "rework":
        assert pr_number is not None  # noqa: S101
        evidence = check_pr_evidence_rework_shape(
            task_id, goal, pr_number, spawned_at, client=client
        )
        return (evidence, None)

    evidence = check_pr_evidence_fresh_shape(task_id, goal, spawned_at, client=client)
    # #1136 AC5: advisory-only — surface a fresh-shape PR that links but does not
    # *close* its named issue. Runs at this evidence boundary regardless of the
    # freshness verdict; never blocks and never edits the PR.
    _warn_if_pr_lacks_closing_ref(task_id, goal, client=client)
    closing_ref = _compute_closing_ref_fresh_shape(task_id, goal, client=client)
    if evidence is False and stdout_reader is not None:
        # AC3 — the head-branch lookup found nothing; fall back to whatever PR
        # the agent claimed in its stdout, then verify it actually exists.
        try:
            text = stdout_reader(task_id)
        except Exception:  # noqa: BLE001 — secondary channel is best-effort
            text = None
        claimed = parse_executor_stdout(text) if text else None
        if claimed and claimed.get("number"):
            try:
                pr = client.get_pull_by_number(int(claimed["number"]))
            except Exception:  # noqa: BLE001 — a claimed-PR lookup error is non-fatal
                pr = None
            if pr:
                return (True, closing_ref)
    return (evidence, closing_ref)


def _compute_closing_ref_fresh_shape(
    task_id: str,
    goal: str,
    *,
    client: GitHubClient | None = None,
) -> bool | None:
    """Compute closing-ref status for a fresh-shape task (#1169).

    Calls ``check_pr_closing_ref_fresh_shape`` through the gate module.
    Returns ``True`` if the PR carries a closing ref, ``False`` if it
    doesn't, ``None`` if it can't be computed.
    """
    issue_number = _goal_issue_number(goal)
    if issue_number is None:
        return None
    if client is None:
        return None
    try:
        gate = _load_gate_module()
    except Exception:  # noqa: BLE001
        return None
    return check_pr_closing_ref_fresh_shape(
        task_id,
        goal,
        issue_number,
        client=client,
        closing_ref_matcher=gate._closing_ref_re,
    )


def _warn_if_pr_lacks_closing_ref(
    task_id: str,
    goal: str,
    *,
    client: GitHubClient,
) -> None:
    """Log an advisory WARNING if a fresh-shape task's PR does not close its issue (#1136 AC5).

    Advisory-only: this neither blocks the pipeline nor edits the PR. It is a
    SEPARATE, deliberate second fetch of the PR (via
    :func:`check_pr_closing_ref_fresh_shape`) — the freshness evidence and the
    closing-ref question are orthogonal (grill decision ``ec66db74``), so they
    are not folded into one call. The closing-ref matcher is the /delegate gate's
    ``_closing_ref_re`` (recognizing ``closes/fixes/resolves`` only, NOT
    ``Refs``), reused by injection so this module keeps its single path-load in
    :func:`_load_gate_module` rather than importing gate internals directly.

    Fires only when the goal names an issue AND a PR exists on the branch whose
    body carries no closing ref for that issue (``check_...`` returns ``False``).
    A missing issue reference, an absent PR (``None``), or a genuine ``Closes #N``
    (``True``) are all silent. The AC7 follow-up (#1169) turns this signal into a
    disposition; here it is observation only.
    """
    issue_number = _goal_issue_number(goal)
    if issue_number is None:
        return
    try:
        gate = _load_gate_module()
    except Exception:  # noqa: BLE001 — advisory must never break the evidence path
        logger.debug("closing-ref advisory: gate module unavailable; skipping")
        return
    closes = check_pr_closing_ref_fresh_shape(
        task_id,
        goal,
        issue_number,
        client=client,
        closing_ref_matcher=gate._closing_ref_re,
    )
    if closes is False:
        logger.warning(
            "pr_closing_ref_missing: task=%s issue=#%s — the PR links but carries "
            "no `Closes #%s` keyword; this merge will NOT auto-close the issue "
            "(native auto-close is suppressed for bot/App merges). Use `Closes #%s` "
            "for a full close; `Refs #%s` is correct only for partial work. "
            "Advisory only — see #1169 for enforcement.",
            task_id,
            issue_number,
            issue_number,
            issue_number,
            issue_number,
        )


def _ensure_pr_closing_ref(
    task_id: str,
    goal: str,
    *,
    client: GitHubClient | None = None,
) -> bool | None:
    """Ensure a fresh-shape task's PR body carries a closing ref (#1169 item 1).

    Structural enforcement: if the PR exists on the task's branch but its body
    lacks a ``Closes/Fixes/Resolves #<N>`` for the referenced issue, the
    supervisor auto-edits the PR body to add it. This makes the requirement
    structural (not advisory) — even if the spawned agent fails to include the
    closing keyword, the merge gate still fires.

    Returns the same tri-state as :func:`check_pr_closing_ref_fresh_shape`:
    - ``True`` — PR has (or now has) a closing ref
    - ``False`` — no PR to fix, or goal has no issue reference
    - ``None`` — unparseable or client unavailable
    """
    shape, _ = parse_goal_shape(goal)
    if shape != "fresh":
        return False
    issue_number = _goal_issue_number(goal)
    if issue_number is None:
        return False
    if client is None:
        return None

    try:
        gate = _load_gate_module()
    except Exception:  # noqa: BLE001 — enforcement must not crash the poll
        logger.debug("ensure-closing-ref: gate module unavailable; skipping")
        return None

    current = check_pr_closing_ref_fresh_shape(
        task_id,
        goal,
        issue_number,
        client=client,
        closing_ref_matcher=gate._closing_ref_re,
    )
    if current is True:
        return True

    if current is False:
        branch_match = re.search(r"\(branch=([^)]+)\)", goal)
        branch = branch_match.group(1).strip() if branch_match else f"task/{task_id}"
        pr = client.get_pull_by_head_branch(branch)
        if pr is None:
            return None
        pr_number = pr.get("number")
        existing_body = pr.get("body") or ""
        closing_line = f"\nCloses #{issue_number}\n"
        if not existing_body.endswith("\n"):
            closing_line = "\n" + closing_line
        new_body = existing_body + closing_line
        try:
            result = client.update_pull(pr_number, body=new_body)
            if result is not None:
                logger.info(
                    "[task_dispatch] auto-fixed missing closing ref: "
                    "PR #%s for issue #%s (task %s)",
                    pr_number,
                    issue_number,
                    task_id,
                )
                return True
        except Exception:  # noqa: BLE001 — enforcement failure must not crash the poll
            logger.exception(
                "[task_dispatch] auto-fix of PR #%s closing ref failed for task %s",
                pr_number,
                task_id,
            )
        return None

    return None


def _severity_for(
    event_type: str, pr_evidence: bool | None, *, closing_ref: bool | None = None
) -> str:
    """Severity for a terminal event, satisfying the events CHECK constraint.

    A clean ``task_done`` with PR evidence is ``info`` (pure-pipeline no-op);
    every other terminal outcome is ``medium`` so it outranks noise but is not
    treated as an incident.

    When ``closing_ref`` is ``False`` (PR exists but body lacks the closing
    keyword), a ``task_done`` is promoted to ``medium`` — the supervisor
    auto-fixes the body but the miss is still noteworthy. (#1169 item 3)
    """
    if event_type == "task_done" and pr_evidence is True and closing_ref is not False:
        return "info"
    return "medium"


@runtime_checkable
class TaskQueuePort(Protocol):
    """The slice of the task FSM the dispatch loop depends on (AC10).

    Implemented for real by :class:`SupabaseTaskQueue` over
    :mod:`agents.task_queue`, and by an in-memory fake in the tests.

    ``runtime_checkable`` makes ``isinstance(x, TaskQueuePort)`` check only that
    the seven method *names* are present — not their signatures — so the
    ``isinstance`` assertion in the tests is a structural smoke check, not a
    full conformance proof.
    """

    def claim_next(self, *, assignee: str) -> dict[str, Any] | None:
        """Claim the highest-priority pending row for ``assignee`` (pending→claimed)."""

    def count_running(self, *, assignee: str) -> int:
        """Count rows currently ``running`` for ``assignee`` (concurrency cap)."""

    def transition(
        self, task_id: str, to_status: str, *, reason: str | None = None
    ) -> dict[str, Any]:
        """Advance a task through the FSM (validated in the real adapter)."""

    def reclaim_stale_claimed(self, *, assignee: str, older_than_seconds: float) -> int:
        """Return stale ``claimed`` rows to ``pending`` (direct UPDATE, FSM-bypassing)."""

    def list_stale_running(
        self, *, assignee: str, older_than_seconds: float
    ) -> list[dict[str, Any]]:
        """List ``running`` rows older than the reaper threshold for ``assignee``."""

    def requeue_running(self, task_id: str) -> bool:
        """Return one process-less ``running`` row to ``pending`` (direct UPDATE, #921 AC4)."""

    def get_status(self, task_id: str) -> str | None:
        """Look up one task's current FSM status, or ``None`` if the row is absent.

        Backs the #1390 AC6 worktree sweep: each on-disk worktree is keyed by
        ``task_id``, and the sweep needs a single-row status check — no
        existing method here lists all rows or looks up one by id.
        """


# First "#N" reference in a goal string — the issue a fresh-shape task targets.
# Right-anchored like the gate's closing-keyword regex so "#93" never reads as
# "#931" (the (?!\d) lookahead).
_GOAL_ISSUE_RE = re.compile(r"#(\d+)(?!\d)")


def _goal_issue_number(goal: str) -> int | None:
    """Issue number a goal references, or ``None`` when it references none."""
    m = _GOAL_ISSUE_RE.search(goal)
    return int(m.group(1)) if m else None


def _load_gate_module() -> Any:
    """Load ``scripts/delegate_predispatch_gate.py`` for its shared predicate (#931).

    The gate module lives outside the ``agents`` package (it is the /delegate
    CLI reference implementation), so import it by path, anchored to the repo
    root the same way :data:`_EXECUTOR_LOG_DIR` is. Cached in ``sys.modules``
    under its plain name — the tests import it the same way, so the two share
    one module object.
    """
    mod = sys.modules.get("delegate_predispatch_gate")
    if mod is not None:
        return mod
    import importlib.util

    path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "scripts",
        "delegate_predispatch_gate.py",
    )
    spec = importlib.util.spec_from_file_location("delegate_predispatch_gate", path)
    if spec is None or spec.loader is None:  # pragma: no cover — repo layout broken
        raise ImportError(f"cannot load dispatch gate module from {path}")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["delegate_predispatch_gate"] = mod
    spec.loader.exec_module(mod)
    return mod


@dataclass(frozen=True)
class DedupConfig:
    """Pre-spawn dispatch-dedup wiring for :func:`drain_tasks` (#931).

    ``fetch_in_flight`` returns ``(open_prs, open_branches)`` in the shapes the
    gate's ``check_in_flight`` predicate takes — PR dicts with ``number`` /
    ``body`` / ``headRefName``, branch names as plain strings. It is called
    lazily, at most once per drain (the first fresh-shape task with an issue
    reference triggers it), and a raise means *unverifiable*: the in-flight row
    is requeued to ``pending`` and the drain stops — never a terminal state on
    evidence we could not read.

    ``list_active_rows`` returns live (``claimed``/``running``) task_queue rows
    (``id``/``goal``/``status``) for the sibling check; queried fresh per task
    so a row spawned earlier in this same drain is seen by later tasks.

    ``record_outcome`` (optional) is called best-effort with a small payload
    dict on each ``skipped_duplicate`` — a raise is logged and swallowed.
    """

    fetch_in_flight: Callable[[], tuple[list[dict[str, Any]], list[str]]]
    list_active_rows: Callable[[], list[dict[str, Any]]]
    record_outcome: Callable[[dict[str, Any]], None] | None = None


def _record_skip_outcome(payload: dict[str, Any]) -> None:
    """Best-effort ``task_outcomes`` write for a skipped-duplicate (#931).

    Sandcastle-anon insert, so ``source_provenance`` carries the required
    ``sandcastle:`` prefix (mcp-memory/schema.sql RLS, #542). Any failure is the
    caller's to swallow — this never raises on the happy path but the caller
    still guards it. ``GITHUB_REPO`` builds the issue URL for the ``issue_url``
    link column.
    """
    from agents.supabase_client import get_client

    repo = os.environ.get("GITHUB_REPO", "your-username/your-repo")
    issue_number = payload.get("issue_number")
    get_client().table("task_outcomes").insert(
        {
            "task_type": "autonomous",
            "task_description": f"dispatch-dedup skip: {payload.get('goal')}",
            "outcome_status": "unknown",
            "outcome_summary": (
                f"Skipped duplicate dispatch for #{issue_number}: {payload.get('pointer')}"
            ),
            "project": "jarvis",
            "issue_url": (
                f"https://github.com/{repo}/issues/{issue_number}"
                if issue_number is not None
                else None
            ),
            "pattern_tags": ["dispatch-dedup", "skip", "autonomous"],
            "source_provenance": "sandcastle:task_dispatch-dedup",
        }
    ).execute()


def default_task_dedup(
    github: GitHubClient,
    *,
    list_active: Callable[[], list[dict[str, Any]]] | None = None,
) -> DedupConfig:
    """Build the production :class:`DedupConfig` from a live GitHub client (#931).

    ``fetch_in_flight`` maps the client's ``list_open_pulls`` / ``list_branch_names``
    into the ``(open_prs, open_branches)`` shape the gate predicate takes;
    ``list_active_rows`` defaults to :func:`task_queue.list_active`; ``record_outcome``
    is the best-effort ``task_outcomes`` writer above. Wired from
    :func:`wake_driver.main`; unit tests inject fakes into :class:`DedupConfig` directly.
    """
    active = list_active if list_active is not None else task_queue.list_active

    def fetch_in_flight() -> tuple[list[dict[str, Any]], list[str]]:
        return github.list_open_pulls(), github.list_branch_names()

    return DedupConfig(
        fetch_in_flight=fetch_in_flight,
        list_active_rows=active,
        record_outcome=_record_skip_outcome,
    )


@dataclass(frozen=True)
class DrainResult:
    """What one :func:`drain_tasks` did."""

    spawned: int = 0
    failed: int = 0
    # Tasks terminated as ``skipped_duplicate`` by the #931 pre-spawn dedup:
    # a live PR (or a live sibling queue row) already covers their issue.
    skipped_duplicate: int = 0
    # True iff the whole drain was skipped because the claude binary did not
    # resolve (AC7a) — distinct from "ran, claimed nothing".
    skipped_no_binary: bool = False
    # True iff the drain skipped/stopped on quota near-exhaustion — either the
    # AC4 pre-flight (nothing claimed) or a mid-drain throttled spawn (the one
    # in-flight row is requeued to ``pending``; on requeue failure the AC6
    # reaper is the backstop). Remaining rows stay ``pending`` and self-heal.
    throttled: bool = False
    # (task_id, proc) per *successful* spawn that yielded a pollable process
    # handle (#921 AC1). A raising spawn never reaches the append; a throttled
    # spawn returns early (the whole drain stops) before it; a result without
    # a ``proc`` attribute counts as spawned but is skipped here. The
    # wake_driver folds these pairs into its {task_id: TrackedProc} liveness
    # map to close running→done on process exit.
    procs: tuple[tuple[str, Any], ...] = ()
    # Per-task spawn metadata keyed by task_id (#953): the original ``goal``,
    # the ``idempotency_key`` (lineage + attempt), and a tz-aware ``spawned_at``
    # stamp. The wake_driver folds these into each :class:`TrackedProc` so the
    # completion poll can compute PR evidence at the terminal boundary. Kept
    # separate from ``procs`` so the existing (task_id, proc) contract — and the
    # drain tests that assert on it — stay byte-for-byte unchanged.
    spawned_meta: dict[str, dict[str, Any]] = field(default_factory=dict)


@dataclass(frozen=True)
class ReclaimResult:
    """What one :func:`reclaim_stale_tasks` did."""

    reclaimed_claimed: int = 0
    reaped_running: int = 0


@dataclass(frozen=True)
class WorktreeSweepResult:
    """What one :func:`sweep_task_worktrees` did (#1390 AC6)."""

    # Worktrees removed immediately: task row absent, or terminal-non-failure
    # (``done``, ``parked``, ``skipped_duplicate``).
    pruned: int = 0
    # Retained-failure worktrees still on disk after TTL + cap eviction.
    retained: int = 0
    # Retained-failure worktrees removed for exceeding the TTL.
    ttl_pruned: int = 0
    # Retained-failure worktrees removed for exceeding the count cap
    # (oldest-first), independent of TTL.
    cap_evicted: int = 0


@dataclass(frozen=True)
class TrackedProc:
    """A live spawn under liveness tracking (#921 AC2).

    ``proc`` is the ``Popen``-shaped handle from :class:`executor.SpawnResult`;
    ``started_at`` is a monotonic-clock stamp the runaway check measures age
    against (AC6). It is a **per-batch** stamp, not per-task: the wake_driver
    samples ``task_clock()`` **once, before** the drain and assigns that same
    value to every proc folded into the map on that tick (wake_driver.tick Step
    4). The pre-drain single sample is deliberate — stamping after the drain
    would discard the just-spawned handles if the clock raised mid-fold,
    orphaning live children onto the 6h reaper (the exact regression
    ``test_tick_stamps_the_clock_before_spawning`` /
    ``test_tick_with_a_broken_clock_spawns_nothing`` guard against). The
    resulting age skew across a batch is bounded by the drain's own duration
    (≤ ``cap`` spawns) and is negligible against the multi-hour
    ``task_running_reap_after_seconds`` threshold, so per-task accuracy buys
    nothing and would reintroduce the orphan risk. Pinned by
    ``test_tick_batch_shares_one_started_at_stamp``.

    The #953 fields carry the spawn context the completion poll needs to compute
    PR evidence at the terminal boundary: the original ``goal`` (shape + branch),
    the ``idempotency_key`` (lineage + attempt for re-drive keying), and a
    tz-aware ``spawned_at`` (the rework evidence check compares PR activity
    against it — a naive datetime would raise on the aware/naive compare). They
    default empty/``None`` so an adopted-after-restart proc with no recovered
    metadata yields ``pr_evidence=null`` → escalate (documented #921 limitation).
    """

    proc: Any
    started_at: float
    goal: str = ""
    idempotency_key: str = ""
    spawned_at: datetime | None = None


@dataclass(frozen=True)
class CompletionResult:
    """What one :func:`poll_completions` did (#921 AC2)."""

    done: int = 0
    failed_exit: int = 0


# An ``event_emit`` callback: (event_type, severity, payload, *, dedup_key).
# Mirrors wake_driver's production emitter and the tests' FakeEventQueue —
# severity is explicit (events CHECK constraint), dedup_key absorbs a
# re-observed terminal event at the DB unique index (#953 AC1/AC9).
EventEmit = Callable[..., Any]


def poll_completions(
    port: TaskQueuePort,
    procs: dict[str, TrackedProc],
    *,
    sidecar: Sidecar | None = None,
    event_emit: EventEmit | None = None,
    evidence_client: GitHubClient | None = None,
    stdout_reader: Callable[[str], str | None] | None = None,
) -> CompletionResult:
    """Close ``running`` rows whose process has exited (#921 AC2, Model P).

    For each tracked pair: ``poll() is None`` → still running, kept;
    ``poll() == 0`` → ``transition(done)``; ``poll() != 0`` →
    ``transition(failed, reason="exit <rc>")``. The DB transition is what frees
    the cap slot (``count_running`` drops) for the same tick's drain; dropping
    the closed entry from ``procs`` (mutated in place) just stops it from being
    re-polled and shields the row from the watchdogs.

    **``done`` means the process exited 0 — nothing more.** Not task success,
    not PR merged; the child may have produced garbage and exited cleanly.
    Outcome truth re-enters externally via Path-A GitHub events.

    AC1/AC2/AC3 (#953): at the terminal boundary the poll computes **PR
    evidence** for the task — parsing the goal shape carried on the
    :class:`TrackedProc` and querying ``evidence_client`` (a real PR exists for
    a fresh task / PR #N got new activity for a rework), with the executor
    stdout (``stdout_reader``) as the AC3 secondary channel. The resulting
    tri-state plus the task's ``lineage_key``/``attempt`` (parsed from its
    idempotency key) go into the payload, and the event is emitted **before**
    the FSM transition so a crash in the window self-heals on re-observation —
    the ``dedup_key`` (``<event_type>:<task_id>:a<attempt>``) absorbs the
    duplicate. With no ``event_emit`` wired, no events are emitted (the #921
    completion behavior is unchanged).

    Per-row isolation: a ``transition`` raising logs, drops the entry, and
    continues — the row stays ``running`` in the store with no live handle, so
    the AC5/AC6 orphan reaper is the backstop. No counter is incremented for it.
    """
    done = 0
    failed_exit = 0
    for task_id, tracked in list(procs.items()):
        # poll_exit handles both handle kinds: a freshly-spawned Popen (real exit
        # code) and an adopted psutil.Process (no poll()/returncode — exited maps
        # to a non-zero sentinel → failed, #952). A bare .poll() here would
        # AttributeError on every adopted handle and wedge the row in running.
        rc = poll_exit(tracked.proc)
        if rc is None:
            continue

        # AC1/AC2/AC3 (#953) — compute evidence and lineage at the boundary, then
        # emit the event BEFORE the transition (event-first ordering). spawned_at
        # rides on the TrackedProc as tz-aware (folded in by the wake_driver);
        # an adopted-after-restart proc has no goal/spawned_at → evidence is null.
        goal = tracked.goal
        lineage_key, attempt = parse_lineage(tracked.idempotency_key)

        # #1169 item 1: for a done fresh-shape task, ensure the PR body carries
        # a closing ref. The supervisor auto-fixes if the agent omitted it.
        if rc == 0 and evidence_client is not None:
            _ensure_pr_closing_ref(task_id, goal, client=evidence_client)

        # #1169 item 3: unpack the closing-ref status alongside the PR evidence.
        pr_evidence, closing_ref = _compute_pr_evidence(
            task_id,
            goal,
            tracked.spawned_at,
            client=evidence_client,
            stdout_reader=stdout_reader,
        )

        # Event emission and the FSM transition are DECOUPLED (MAJOR, PR #1011).
        # event-first ordering is the happy path, but if the emit raises (Supabase
        # down, network blip) the transition MUST still fire — otherwise the task
        # is stuck in ``running`` until the 6h reaper sweeps it. A dropped event
        # self-heals on re-observation; a stuck transition does not. So the emit
        # gets its own try/except and never blocks the transition below.
        if event_emit:
            try:
                if rc == 0:
                    event_emit(
                        "task_done",
                        _severity_for("task_done", pr_evidence, closing_ref=closing_ref),
                        {
                            "task_id": task_id,
                            "lineage_key": lineage_key,
                            "attempt": attempt,
                            "pr_evidence": pr_evidence,
                            "goal": goal,
                            "closing_ref": closing_ref,
                        },
                        dedup_key=f"task_done:{task_id}:a{attempt}",
                    )
                else:
                    event_emit(
                        "task_failed",
                        _severity_for("task_failed", pr_evidence),
                        {
                            "task_id": task_id,
                            "lineage_key": lineage_key,
                            "attempt": attempt,
                            "exit_code": rc,
                            "exit_confirmed": True,
                            "pr_evidence": pr_evidence,
                            "failure_reason": f"exit {rc}",
                            "goal": goal,
                        },
                        dedup_key=f"task_failed:{task_id}:a{attempt}",
                    )
            except Exception:  # noqa: BLE001 — emit failure must not block transition
                logger.exception(
                    "[task_dispatch] event emit for task %s failed; "
                    "proceeding with transition (event self-heals on re-observation)",
                    task_id,
                )

        try:
            if rc == 0:
                port.transition(task_id, "done")
                done += 1
            else:
                port.transition(task_id, "failed", reason=f"exit {rc}")
                failed_exit += 1
        except Exception:  # noqa: BLE001 — isolate one bad row, reaper backstops it
            logger.exception(
                "[task_dispatch] completion transition for task %s failed; "
                "dropped from tracking (reaper backstop)",
                task_id,
            )
        finally:
            # AC6 (#952) — delete sidecar on terminal transition.
            if sidecar is not None:
                try:
                    sidecar.delete_sidecar_file(task_id)
                except Exception:  # noqa: BLE001 — sidecar delete is best-effort
                    logger.exception(
                        "[task_dispatch] sidecar delete failed for task %s",
                        task_id,
                    )
            # AC5 (#1390) — remove the worktree on success; detach HEAD on
            # failure so the branch ref is free for `_redrive_goal`'s retry.
            try:
                _finalize_task_worktree(task_id, success=(rc == 0))
            except Exception:  # noqa: BLE001 — worktree finalize is best-effort
                logger.exception(
                    "[task_dispatch] worktree finalize failed for task %s",
                    task_id,
                )
            procs.pop(task_id, None)
    return CompletionResult(done=done, failed_exit=failed_exit)


def kill_process_tree(proc: Any, *, platform: str = sys.platform) -> None:
    """Kill a spawned process AND its children (#921 AC6).

    On Windows ``Popen.kill()`` is an alias for ``terminate()`` — it kills only
    the direct process, and a ``claude -p`` child's own subprocesses (git, gh,
    tools) survive as orphans. ``taskkill /PID <pid> /T /F`` walks the tree; if
    taskkill itself can't launch (stripped PATH), degrade to ``proc.kill()`` —
    direct child only, better than leaving the runaway alive.

    POSIX gets plain ``proc.kill()`` — direct child only. ``os.killpg`` would
    be WRONG here: :func:`executor.spawn` does not pass
    ``start_new_session=True``, so the child shares the driver's process group
    and ``killpg`` would kill the driver itself. A POSIX tree-kill needs the
    spawn-side change first; production runs on Windows, so this is deferred.

    Best-effort ``wait`` afterwards reaps the handle so ``poll()`` reflects the
    death immediately; a hung wait is swallowed (the next tick's poll re-checks).
    """
    if platform == "win32":
        try:
            subprocess.run(
                ["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                capture_output=True,
                check=False,
            )
        except OSError:  # taskkill missing/unlaunchable — degrade to direct kill
            logger.exception(
                "[task_dispatch] taskkill unavailable for pid %s; "
                "falling back to Popen.kill() (children may survive)",
                proc.pid,
            )
            proc.kill()
    else:
        proc.kill()
    try:
        proc.wait(timeout=10)
    except Exception:  # noqa: BLE001 — reap is best-effort; poll re-checks next tick
        pass


def kill_runaways(
    port: TaskQueuePort,
    procs: dict[str, TrackedProc],
    *,
    max_runtime_seconds: float = DEFAULT_RUNNING_REAP_SECONDS,
    now: Callable[[], float] = time.monotonic,
    kill: Callable[[Any], None] = kill_process_tree,
    sidecar: Sidecar | None = None,
    event_emit: EventEmit | None = None,
) -> int:
    """Tree-kill live processes that exceeded the max runtime (#921 AC6).

    The orphan reaper (:func:`reclaim_stale_tasks`) deliberately skips rows
    with a live tracked process — this is the counterpart that bounds those:
    a process still alive past ``max_runtime_seconds`` (same one 6h knob as
    the reaper) is killed with its whole tree, its row transitioned
    ``running → failed`` (``reason="killed: exceeded max runtime"``), and the
    entry dropped. Killed runaways fold into the tick's failed-exit counter.

    Already-exited processes are skipped — :func:`poll_completions` owns those
    (their real exit code decides done vs failed). Per-row isolation: a *kill*
    raising keeps the entry (the process may still be alive; failing the row
    would lie — retried next tick); a *transition* raising after a successful
    kill drops the entry to the reaper backstop, like ``poll_completions``.

    AC1 (#953): a runaway-reaped task is the worst-case stuck class the
    reconciliation exists to catch, so — like :func:`poll_completions` — it
    emits ``task_failed`` (``exit_confirmed=True``, ``pr_evidence=None``,
    ``failure_reason="killed: exceeded max runtime"``) when ``event_emit`` is
    wired, so the orchestrator can re-drive or escalate. Without the emit the
    killed task vanishes silently (MAJOR, PR #1011). The emit is DECOUPLED from
    the transition (its own try/except): an emit blowup must not strand the kill
    in ``running`` — a dropped event self-heals on re-observation via the
    ``dedup_key``. With no ``event_emit`` the #921 behavior is unchanged.
    """
    killed = 0
    for task_id, tracked in list(procs.items()):
        if poll_exit(tracked.proc) is not None:
            continue  # exited — poll_completions closes it (real rc or sentinel)
        if now() - tracked.started_at <= max_runtime_seconds:
            continue
        try:
            kill(tracked.proc)
        except Exception:  # noqa: BLE001 — possibly still alive; retry next tick
            logger.exception(
                "[task_dispatch] tree-kill of runaway task %s failed; will retry",
                task_id,
            )
            continue
        # AC1 (#953) — emit task_failed BEFORE the transition (event-first), and
        # DECOUPLED from it: a runaway is the worst-case stuck class, so the
        # orchestrator must hear about it even if the transition later fails. The
        # emit's own try/except keeps an emit blowup (Supabase down) from
        # stranding the kill in ``running`` — a dropped event self-heals on
        # re-observation via the dedup_key.
        lineage_key, attempt = parse_lineage(tracked.idempotency_key)
        if event_emit:
            try:
                event_emit(
                    "task_failed",
                    _severity_for("task_failed", None),
                    {
                        "task_id": task_id,
                        "lineage_key": lineage_key,
                        "attempt": attempt,
                        "exit_confirmed": True,
                        "pr_evidence": None,
                        "failure_reason": "killed: exceeded max runtime",
                        "goal": tracked.goal,
                    },
                    dedup_key=f"task_failed:{task_id}:a{attempt}",
                )
            except Exception:  # noqa: BLE001 — emit failure must not block transition
                logger.exception(
                    "[task_dispatch] runaway task %s event emit failed; "
                    "proceeding with transition (event self-heals on re-observation)",
                    task_id,
                )
        try:
            port.transition(task_id, "failed", reason="killed: exceeded max runtime")
            killed += 1
        except Exception:  # noqa: BLE001 — killed but row not closed; reaper backstops
            logger.exception(
                "[task_dispatch] runaway task %s killed but transition failed; "
                "dropped from tracking (reaper backstop)",
                task_id,
            )
        finally:
            # AC4 (#952) — delete sidecar when tree-killing orphan.
            if sidecar is not None:
                try:
                    sidecar.delete_sidecar_file(task_id)
                except Exception:  # noqa: BLE001 — sidecar delete is best-effort
                    logger.exception(
                        "[task_dispatch] sidecar delete failed for runaway task %s",
                        task_id,
                    )
            procs.pop(task_id, None)
    return killed


def _create_task_worktree(task_id: str, goal: str = "") -> str:
    """Create a per-task git worktree at ``.reactive/worktrees/<task_id>``
    (#1390 AC3) — isolates concurrent spawned workers from each other and
    from the main checkout's working tree.

    By default the worktree is created on a fresh branch ``task/<task_id>``
    (``git worktree add -b``). If ``goal`` carries an explicit
    ``(branch=<name>)`` directive naming a *different* branch, the worktree
    instead **attaches** to that existing branch (``git worktree add`` with
    no ``-b``) rather than creating ``task/<task_id>``.

    This distinction matters for a fresh-shape re-drive: :func:`orchestrator._redrive_goal`
    pins the retry to ``(branch=task/<root_task_id>)`` specifically *because*
    the re-driven task's own ``task/<task_id>`` branch is never meant to be
    created — the retry needs to land back on the root attempt's branch,
    which :func:`_finalize_task_worktree` leaves detached-but-intact after a
    failure precisely so a later attach can succeed. Creating a new branch
    unconditionally here would silently violate that pin (MEDIUM, PR #1450
    review) and leave the retry's evidence check looking at a branch that was
    never populated.

    ``task_id`` is validated via ``_SAFE_TASK_ID_RE`` before interpolation into
    a filesystem path — same path-traversal guard as
    :func:`default_stdout_reader`.
    """
    if not _SAFE_TASK_ID_RE.match(task_id):
        raise ValueError(f"unsafe task_id for worktree path: {task_id!r}")
    worktree_path = os.path.join(_REPO_ROOT, ".reactive", "worktrees", task_id)
    own_branch = f"task/{task_id}"
    branch_match = re.search(r"\(branch=([^)]+)\)", goal)
    target_branch = branch_match.group(1).strip() if branch_match else own_branch
    if target_branch == own_branch:
        cmd = ["git", "worktree", "add", "-b", own_branch, worktree_path]
    else:
        cmd = ["git", "worktree", "add", worktree_path, target_branch]
    subprocess.run(cmd, cwd=_REPO_ROOT, check=True, capture_output=True, text=True)
    return worktree_path


# Marker file written into a retained-failure worktree at detach time, holding
# the epoch timestamp of finalization. The AC6 sweep TTLs retained failures
# against this file's content rather than git-internal mtimes — ``git
# checkout --detach`` gives no reliable "when did this fail" signal on its
# own (LOW, #1390 AC6 design).
_WORKTREE_FAILED_AT_MARKER = ".reactive-failed-at"


def _finalize_task_worktree(task_id: str, *, success: bool) -> None:
    """Finalize the per-task worktree at the terminal boundary (#1390 AC5).

    Success removes the worktree outright. Failure detaches HEAD first so the
    branch ``task/<task_id>`` is free for ``_redrive_goal``'s retry to attach
    in a fresh worktree, writes the ``_WORKTREE_FAILED_AT_MARKER`` timestamp
    file, then leaves the tree on disk for post-mortem — the AC6 sweep
    TTL/count-caps genuinely retained failures later, keyed on that marker.

    No-op (not an error) when the worktree was never created — e.g. an
    adopted-after-restart proc, or a task spawned before #1390 shipped.
    """
    if not _SAFE_TASK_ID_RE.match(task_id):
        return
    worktree_path = os.path.join(_REPO_ROOT, ".reactive", "worktrees", task_id)
    if not os.path.isdir(worktree_path):
        return
    if success:
        subprocess.run(
            ["git", "worktree", "remove", "--force", worktree_path],
            cwd=_REPO_ROOT,
            check=True,
            capture_output=True,
            text=True,
        )
    else:
        subprocess.run(
            ["git", "checkout", "--detach"],
            cwd=worktree_path,
            check=True,
            capture_output=True,
            text=True,
        )
        with open(
            os.path.join(worktree_path, _WORKTREE_FAILED_AT_MARKER), "w", encoding="utf-8"
        ) as fh:
            fh.write(str(time.time()))


def default_spawn(goal: str, *, task_id: str | None = None) -> Any:
    """Production spawn adapter — fire-and-forget ``claude -p`` via the executor.

    Returns the :class:`executor.SpawnResult`. A throttled result (quota
    near-exhaustion) means no process launched; :func:`drain_tasks` inspects the
    ``throttled`` flag and stops the drain rather than counting a phantom spawn.
    Imported lazily so the tested drain logic (which injects its own spawn) need
    not pull executor's subprocess/usage-probe dependencies.

    AC3 (#953): passes task_id to the executor for stdout JSON capture.

    AC5 (#953): a **fresh-shape** goal with no explicit ``(branch=...)`` directive
    gets ``(branch=task/<task_id>)`` appended before spawn, so the child opens its
    PR on a deterministic head branch the terminal-boundary evidence check can find.
    Rework-shape goals (``/rework #N``) and goals that already pin a branch are left
    untouched — augmentation is purely additive and never rewrites an operator's
    branch choice. The un-augmented goal is what ``drain_tasks`` records in
    ``spawned_meta`` for evidence (the default head ``task/<task_id>`` matches).

    AC1 (#1136): a fresh-shape goal naming an issue ``#N`` additionally gets a
    ``Closes #<N>`` PR-body mandate appended (:func:`_augment_closes_mandate`), so a
    PR the executor lane opens links its issue and auto-closes on merge (#948).
    """
    from agents.executor import spawn as executor_spawn

    spawn_goal = _augment_branch_directive(goal, task_id) if task_id else goal
    # #1136 AC1: also inject the Closes #<N> PR-body mandate for a fresh-shape goal
    # naming an issue. Order-independent of the branch directive above — the branch
    # suffix carries no ``#N`` and is not a ``/rework`` marker, so it neither adds a
    # spurious close target nor flips the goal's shape.
    spawn_goal = _augment_closes_mandate(spawn_goal, task_id) if task_id else spawn_goal
    # AC3 (#1390): isolate each spawned worker in its own git worktree so
    # concurrent workers never share a working tree. Pass spawn_goal (not the
    # raw goal) so a fresh-shape redrive's (branch=task/<root_task_id>) pin
    # (added by _redrive_goal, threaded through by _augment_branch_directive)
    # is honored — see _create_task_worktree docstring.
    cwd = _create_task_worktree(task_id, spawn_goal) if task_id else None
    return executor_spawn(spawn_goal, task_id=task_id, cwd=cwd)


def default_resolve_binary() -> str:
    """Production binary-resolution adapter (lazy import; see :func:`default_spawn`)."""
    from agents.executor import _resolve_claude_binary

    return _resolve_claude_binary()


def default_read_usage() -> Any:
    """Production quota-probe adapter (lazy import; see :func:`default_spawn`).

    :func:`agents.usage_probe.read_usage` is false-safe — it never raises, a
    probe failure returns ``near_exhaustion=True`` — so the AC4 pre-flight
    pauses dispatch rather than flooding it when the probe is broken.
    """
    from agents.usage_probe import read_usage

    return read_usage()


def drain_tasks(
    port: TaskQueuePort,
    spawn: Spawn = default_spawn,
    *,
    assignee: str = DEFAULT_ASSIGNEE,
    cap: int = DEFAULT_CONCURRENCY_CAP,
    resolve_binary: ResolveBinary = default_resolve_binary,
    read_usage: ReadUsage = default_read_usage,
    sidecar: Sidecar | None = None,
    dedup: DedupConfig | None = None,
) -> DrainResult:
    """Claim pending ``assignee`` tasks up to the cap and spawn each (AC2–AC4, AC7–AC9).

    Order of operations:

    1. **Pre-flight binary resolution, once (AC7a).** If the claude binary does
       not resolve — missing, not executable, or the executor import is broken —
       skip the *entire* drain: zero claims, nothing marked ``failed``, every
       row stays ``pending`` so the next drain self-heals once the env is fixed.
       No internal retry.
    2. **Budget, sampled once (AC3).** ``budget = cap − count_running(assignee)``.
       Nothing exits ``running`` mid-drain, so the snapshot is exact; the loop
       spawns at most ``budget`` tasks and leaves the rest ``pending``.
    3. **Per task, Ordering B (AC4).** ``claim_next`` (pending→claimed) →
       ``transition(running)`` → ``spawn(goal)``. The running transition
       precedes the spawn so a crash in the window can only strand a ``running``
       row (reaped, AC6), never a ``claimed`` row with a live process (which
       would double-spawn under the AC5 reclaimer).

    A ``claim_next`` returning ``None`` (empty queue or lost race, AC9) breaks
    the loop cleanly. A ``transition(running)`` raising leaves the row
    ``claimed`` (no process launched) for the AC5 reclaimer and skips to the
    next slot. A ``spawn`` raising (AC7b) marks *that* task ``running→failed``
    (terminal — the external event loop re-drives) and the drain continues. A
    ``spawn`` returning a *throttled* result (quota near-exhaustion: no process
    launched) stops the drain — the one in-flight row is requeued to
    ``pending`` (#921 AC4; reaper backstop if the requeue fails), the rest stay
    ``pending``; quota will not recover mid-drain.

    With ``dedup`` wired (#931), each *fresh-shape* task that references an
    issue is checked after the running transition and before the spawn: a live
    PR for the issue or a live sibling queue row → ``running →
    skipped_duplicate`` (terminal, best-effort outcome record) and the drain
    continues; a stale claim branch with no PR → ``running → parked`` for
    owner attention; evidence fetch failure → the row is requeued to
    ``pending`` and the drain stops (unverifiable is never terminal).
    Rework-shape goals bypass the check — they target a live PR by design.
    """
    # AC7a — pre-flight once; an unusable binary skips the whole drain. Widened
    # past FileNotFoundError to the other no-usable-binary failures (not
    # executable → PermissionError; broken executor import → ImportError): all
    # mean "cannot spawn", so skip-and-self-heal beats claim-and-strand.
    try:
        resolve_binary()
    except (FileNotFoundError, PermissionError, ImportError):
        logger.warning(
            "[task_dispatch] claude binary unresolved; skipping drain "
            "(no claims, rows stay pending, self-heals when env is fixed)"
        )
        return DrainResult(skipped_no_binary=True)

    # AC4 (#921) — quota pre-flight, once per drain. Near-exhaustion skips the
    # *entire* drain: zero claims, zero churn, rows stay visibly ``pending``
    # until quota recovers. The default probe is false-safe (a probe error
    # reads as near-exhaustion), so a broken probe pauses dispatch too.
    # executor.spawn re-checks per spawn — that per-spawn gate remains the
    # backstop for a quota flip mid-drain.
    reading = read_usage()
    if getattr(reading, "near_exhaustion", False):
        logger.warning(
            "[task_dispatch] quota near-exhaustion at drain start; skipping drain "
            "(no claims, rows stay pending until quota recovers)"
        )
        return DrainResult(throttled=True)

    # AC3 — budget sampled once at drain start.
    budget = cap - port.count_running(assignee=assignee)
    if budget <= 0:
        return DrainResult()

    spawned = 0
    failed = 0
    skipped_duplicate = 0
    # #931 — GitHub in-flight evidence, fetched lazily at most once per drain.
    in_flight_evidence: tuple[list[dict[str, Any]], list[str]] | None = None
    procs: list[tuple[str, Any]] = []
    # AC1/AC2 (#953) — carry each spawned task's goal + idempotency key + tz-aware
    # spawn time out to the wake_driver, which folds them onto the TrackedProc so
    # the terminal-boundary poll can compute PR evidence and lineage. spawned_at
    # MUST be tz-aware (datetime.now(UTC)) — the rework-shape evidence check
    # compares it against a tz-aware PR ``updated_at`` and a naive value raises.
    spawned_meta: dict[str, dict[str, Any]] = {}
    for _ in range(budget):
        row = port.claim_next(assignee=assignee)  # AC2 routing; AC9 lost-race → None
        if row is None:
            break
        task_id = str(row["id"])

        # AC4 Ordering B — running BEFORE spawn. Guard it: a transient store
        # error here leaves the row ``claimed`` with no process, so the AC5
        # reclaimer returns it to ``pending``. Skip to the next slot rather than
        # spawn against a row we failed to mark running.
        try:
            port.transition(task_id, "running")
        except Exception:  # noqa: BLE001 — isolate a transient transition error
            logger.exception(
                "[task_dispatch] could not mark task %s running; left claimed for the reclaimer",
                task_id,
            )
            continue

        # #931 — pre-spawn dispatch-dedup. Placed AFTER the running transition
        # (the row is ours under the optimistic lock — a skip verdict can only
        # terminate a row this drain owns) and BEFORE the spawn (the whole
        # point: no duplicate process). Fresh-shape goals only; a rework goal
        # targets a live PR by design and must not be eaten by the live-PR rule.
        if dedup is not None:
            shape, _ = parse_goal_shape(row["goal"])
            issue_number = _goal_issue_number(str(row["goal"])) if shape == "fresh" else None
            if issue_number is not None:
                try:
                    if in_flight_evidence is None:
                        in_flight_evidence = dedup.fetch_in_flight()
                    active_rows = dedup.list_active_rows()
                except Exception:  # noqa: BLE001 — unverifiable is never terminal
                    try:
                        requeued = port.requeue_running(task_id)
                    except Exception:  # noqa: BLE001 — requeue is best-effort
                        requeued = False
                    logger.exception(
                        "[task_dispatch] dedup evidence fetch failed; stopping drain — task %s %s",
                        task_id,
                        "requeued to pending" if requeued else "left running for the reaper",
                    )
                    return DrainResult(
                        spawned=spawned,
                        failed=failed,
                        skipped_duplicate=skipped_duplicate,
                        procs=tuple(procs),
                        spawned_meta=spawned_meta,
                    )

                open_prs, open_branches = in_flight_evidence
                in_flight = _load_gate_module().check_in_flight(
                    issue_number, open_prs, open_branches
                )
                sibling = next(
                    (
                        r
                        for r in active_rows
                        if str(r.get("id")) != task_id
                        and _goal_issue_number(str(r.get("goal") or "")) == issue_number
                    ),
                    None,
                )
                if in_flight.verdict == "live_pr" or sibling is not None:
                    pointer = (
                        in_flight.pointer
                        if in_flight.verdict == "live_pr"
                        else f"live task_queue row {sibling['id']} already targets #{issue_number}"
                    )
                    try:
                        port.transition(task_id, "skipped_duplicate", reason=pointer)
                    except Exception:  # noqa: BLE001 — row stays running; reaper backstops
                        logger.exception(
                            "[task_dispatch] could not mark task %s skipped_duplicate; "
                            "row left running for the reaper",
                            task_id,
                        )
                        continue
                    skipped_duplicate += 1
                    logger.info(
                        "[task_dispatch] task %s skipped as duplicate: %s", task_id, pointer
                    )
                    if dedup.record_outcome is not None:
                        try:
                            dedup.record_outcome(
                                {
                                    "task_id": task_id,
                                    "issue_number": issue_number,
                                    "goal": row["goal"],
                                    "pointer": pointer,
                                }
                            )
                        except Exception:  # noqa: BLE001 — outcome record is best-effort
                            logger.exception(
                                "[task_dispatch] outcome record for skipped task %s raised",
                                task_id,
                            )
                    continue
                if in_flight.verdict == "stale_branch":
                    # A claim branch with no open PR is owner-attention territory:
                    # someone (or some run) claimed the issue and went dark. Park —
                    # don't spawn over it, don't silently drop it.
                    try:
                        port.transition(task_id, "parked", reason=in_flight.pointer)
                    except Exception:  # noqa: BLE001 — row stays running; reaper backstops
                        logger.exception(
                            "[task_dispatch] could not park task %s on stale branch; "
                            "row left running for the reaper",
                            task_id,
                        )
                    continue

        # Capture spawn time BEFORE launching (MAJOR, PR #1011). The terminal
        # evidence check counts PR/commit activity with timestamp > spawned_at;
        # recording it AFTER spawn() returns would let any commit the child makes
        # in that window read as older-than-spawn and be missed as evidence. Take
        # the lower bound: the instant just before the process starts.
        spawn_started_at = datetime.now(UTC)
        try:
            result = spawn(row["goal"], task_id=task_id)  # AC3 (#953) — capture stdout JSON
        except Exception as exc:  # noqa: BLE001 — AC7b: isolate one bad spawn
            # AC7b — terminal failure; no internal retry, external loop re-drives.
            try:
                port.transition(task_id, "failed", reason=f"spawn raised: {exc}")
            except Exception:  # noqa: BLE001 — the failed-mark itself can raise; an
                # escape here would discard the already-spawned handles in ``procs``
                # (orphans for the 6h reaper). Row stays running; reaper backstops.
                logger.exception(
                    "[task_dispatch] could not mark task %s failed after spawn raise; "
                    "row left running for the reaper",
                    task_id,
                )
            failed += 1
            continue

        # The executor declined to launch (quota near-exhaustion): no process
        # exists, but the row is already ``running`` (Ordering B). Requeue it to
        # ``pending`` so the next drain retries as soon as quota recovers
        # (#921 AC4) — without this it would strand 6h until the reaper failed
        # a task that never ran. Quota won't recover mid-drain, so stop
        # claiming. Not a spawn failure → not counted.
        if getattr(result, "throttled", False):
            try:
                requeued = port.requeue_running(task_id)
            except Exception:  # noqa: BLE001 — requeue is best-effort
                requeued = False
                logger.exception("[task_dispatch] requeue of throttled task %s raised", task_id)
            logger.warning(
                "[task_dispatch] spawn throttled (quota near-exhaustion); "
                "stopping drain — task %s %s",
                task_id,
                "requeued to pending" if requeued else "left running for the reaper",
            )
            return DrainResult(
                spawned=spawned,
                failed=failed,
                skipped_duplicate=skipped_duplicate,
                throttled=True,
                procs=tuple(procs),
                spawned_meta=spawned_meta,
            )

        spawned += 1
        # AC1 (#921) — retain the process handle so the wake_driver can poll
        # completion. Spawns without a handle (test fakes, defensive None)
        # still count as spawned but cannot be liveness-tracked.
        proc = getattr(result, "proc", None)
        if proc is not None:
            procs.append((task_id, proc))
            # AC1/AC2 (#953) — capture original goal + lineage key + tz-aware spawn
            # time for the terminal-boundary evidence/lineage computation. Store the
            # *un-augmented* goal: default_spawn's AC5 branch directive points at the
            # same default head (task/<task_id>) the fresh-shape check derives.
            spawned_meta[task_id] = {
                "goal": row["goal"],
                "idempotency_key": str(row.get("idempotency_key", "") or ""),
                "spawned_at": spawn_started_at,
            }
            # AC2 (#952) — record spawn to sidecar for restart liveness recovery.
            if sidecar is not None:
                try:
                    # proc here is always the executor's Popen-shaped handle, which
                    # has no create_time(); we record wall-clock spawn time as the
                    # adoption key. adopt_task tolerates ≤1s skew vs the OS-reported
                    # create_time on restart (#952 AC2/AC3).
                    pid = proc.pid
                    create_time = time.time()
                    sidecar.record_spawn(task_id, pid, create_time)
                except Exception:  # noqa: BLE001 — sidecar write is best-effort
                    logger.exception(
                        "[task_dispatch] sidecar record_spawn failed for task %s; "
                        "liveness tracking degraded but task continues",
                        task_id,
                    )

    return DrainResult(
        spawned=spawned,
        failed=failed,
        skipped_duplicate=skipped_duplicate,
        procs=tuple(procs),
        spawned_meta=spawned_meta,
    )


def reclaim_stale_tasks(
    port: TaskQueuePort,
    *,
    assignee: str = DEFAULT_ASSIGNEE,
    claimed_stale_after_seconds: float = DEFAULT_CLAIMED_STALE_SECONDS,
    running_reap_after_seconds: float = DEFAULT_RUNNING_REAP_SECONDS,
    live_task_ids: Collection[str] = (),
) -> ReclaimResult:
    """Sweep stranded tasks before a drain (#909 AC5/AC6, #921 AC5 orphan-only).

    - **Stale claimed** rows return to ``pending`` via a direct UPDATE that
      bypasses the FSM (``claimed → pending`` is not a legal transition; this
      mirrors :meth:`wake_driver.PsycopgEventQueue.reclaim_stale`). Never
      touches ``running``.
    - **Orphaned running** rows — stale AND not in ``live_task_ids`` — are
      transitioned ``running → failed`` so rows with no process behind them (a
      child that died without a completion, a crash in the running↔spawn
      window, a pre-restart spawn) stop ratcheting the cap toward 0.

    ``live_task_ids`` is the wake_driver's tracked-process map keyset (#921
    AC5): a row with a live handle is *not* an orphan however old — legitimate
    long tasks are never time-reaped; genuinely stuck live processes are
    :func:`kill_runaways`' job, which kills the tree and closes the row
    explicitly. Restart semantics: a fresh driver has an empty map, so every
    stale running row is an orphan again (AC7 — the map does not survive
    restart; the backstop self-heals via Path-A).

    Invoked by :func:`wake_driver.tick` *before* :func:`drain_tasks`, so a row
    reclaimed this pass is eligible to be re-claimed and spawned in the same
    tick — symmetric to the event watchdog running before ``drain_pending``.
    """
    # Stale claimed → pending (FSM-bypassing direct UPDATE).
    reclaimed = port.reclaim_stale_claimed(
        assignee=assignee, older_than_seconds=claimed_stale_after_seconds
    )

    # Orphaned running → failed (stale + no tracked live process).
    reaped = 0
    for row in port.list_stale_running(
        assignee=assignee, older_than_seconds=running_reap_after_seconds
    ):
        task_id = str(row["id"])
        if task_id in live_task_ids:
            continue
        try:
            port.transition(
                task_id,
                "failed",
                reason=(
                    f"reaped: orphaned running row (no tracked process) "
                    f"after {running_reap_after_seconds:.0f}s"
                ),
            )
            reaped += 1
        except Exception:  # noqa: BLE001 — isolate one bad row; the rest still reap
            logger.exception(
                "[task_dispatch] orphan reap of task %s failed; retried next sweep",
                task_id,
            )

    return ReclaimResult(reclaimed_claimed=reclaimed, reaped_running=reaped)


def _remove_worktree(worktree_path: str) -> bool:
    """Best-effort ``git worktree remove --force`` — log-and-continue, never raise.

    Windows handle-locks make an un-removable tree a normal outcome (the
    existing ``.claude/worktrees/`` lane already drifts — 9 dirs on disk vs 5
    registered), not an exotic one; the AC6 sweep must not let one stuck tree
    abort the rest of the tick (#1390 AC6).
    """
    try:
        subprocess.run(
            ["git", "worktree", "remove", "--force", worktree_path],
            cwd=_REPO_ROOT,
            check=True,
            capture_output=True,
            text=True,
        )
        return True
    except (subprocess.CalledProcessError, OSError):
        logger.exception(
            "[task_dispatch] failed to remove worktree %s; retried next sweep",
            worktree_path,
        )
        return False


def _read_worktree_failed_at(worktree_path: str, *, default: float) -> float:
    """Read the ``_WORKTREE_FAILED_AT_MARKER`` epoch timestamp, or ``default``
    if the marker is missing/unreadable — e.g. a worktree that failed before
    #1390 AC6 shipped the marker write. Defaulting to "now" rather than "very
    old" means an unreadable marker errs toward retaining the tree, not
    losing it to an eager TTL prune.
    """
    marker_path = os.path.join(worktree_path, _WORKTREE_FAILED_AT_MARKER)
    try:
        with open(marker_path, encoding="utf-8") as fh:
            return float(fh.read().strip())
    except (OSError, ValueError):
        return default


def sweep_task_worktrees(
    port: TaskQueuePort,
    *,
    retention_seconds: float = DEFAULT_WORKTREE_RETENTION_TTL_SECONDS,
    retention_cap: int = DEFAULT_WORKTREE_RETENTION_CAP,
    now: Callable[[], float] = time.time,
) -> WorktreeSweepResult:
    """Tick-start reaping sweep over ``.reactive/worktrees/*`` (#1390 AC6).

    Keyed on the owning task's queue-row status, looked up via
    :meth:`TaskQueuePort.get_status`:

    - **Absent row, or terminal-non-failure** (``done``, ``parked``,
      ``skipped_duplicate``) → removed immediately. Nothing needs the tree
      any more.
    - **``failed``** → retained for post-mortem, subject to TTL
      (``retention_seconds``, measured from the tree's
      ``_WORKTREE_FAILED_AT_MARKER``) and a count cap (``retention_cap``,
      oldest-first eviction) so failures don't accumulate unbounded.
    - **Any active state** (``pending``, ``claimed``, ``running``) → left
      untouched. A live spawn's worktree is never touched by this sweep;
      :func:`reclaim_stale_tasks` (run immediately before this in
      ``wake_driver.tick``) is what turns an orphaned ``running`` row into
      ``failed`` so it becomes eligible here.

    Finishes with a best-effort ``git worktree prune`` so git's own
    registration bookkeeping stays in sync with what's actually on disk.
    Removal failures (Windows handle-locks are a normal, not exotic, outcome)
    are logged and retried next tick rather than raised — one stuck tree must
    not abort the sweep.
    """
    worktrees_root = os.path.join(_REPO_ROOT, ".reactive", "worktrees")
    pruned = 0
    retained_failures: list[tuple[str, float]] = []

    if os.path.isdir(worktrees_root):
        for name in sorted(os.listdir(worktrees_root)):
            if not _SAFE_TASK_ID_RE.match(name):
                continue
            worktree_path = os.path.join(worktrees_root, name)
            if not os.path.isdir(worktree_path):
                continue

            status = port.get_status(name)
            if status == "failed":
                failed_at = _read_worktree_failed_at(worktree_path, default=now())
                retained_failures.append((worktree_path, failed_at))
            elif status in (None, "done", "parked", "skipped_duplicate"):
                if _remove_worktree(worktree_path):
                    pruned += 1
            # else: active (pending/claimed/running) — untouched this sweep.

    # TTL-prune retained failures past their retention window.
    ttl_pruned = 0
    cutoff = now() - retention_seconds
    survivors: list[tuple[str, float]] = []
    for worktree_path, failed_at in retained_failures:
        if failed_at < cutoff:
            if _remove_worktree(worktree_path):
                ttl_pruned += 1
            continue
        survivors.append((worktree_path, failed_at))

    # Count-cap survivors, oldest-first, independent of TTL.
    cap_evicted = 0
    if len(survivors) > retention_cap:
        survivors.sort(key=lambda item: item[1])
        overflow = len(survivors) - retention_cap
        for worktree_path, _failed_at in survivors[:overflow]:
            if _remove_worktree(worktree_path):
                cap_evicted += 1

    try:
        subprocess.run(
            ["git", "worktree", "prune"],
            cwd=_REPO_ROOT,
            check=True,
            capture_output=True,
            text=True,
        )
    except (subprocess.CalledProcessError, OSError):
        logger.exception("[task_dispatch] git worktree prune failed; retried next sweep")

    return WorktreeSweepResult(
        pruned=pruned,
        retained=len(survivors) - cap_evicted,
        ttl_pruned=ttl_pruned,
        cap_evicted=cap_evicted,
    )


class SupabaseTaskQueue:
    """Real :class:`TaskQueuePort` over :mod:`agents.task_queue` (AC10).

    Thin delegation — the FSM and SQL live in :mod:`agents.task_queue`. Tasks
    stay on supabase-py (PostgREST); only events need raw psycopg (``LISTEN``),
    so this is the task-side analogue of
    :class:`wake_driver.PsycopgEventQueue`. ``client`` defaults to ``None`` so
    ad-hoc construction still works (each call then resolves a client lazily
    inside ``task_queue``), but a long-running caller should build one Supabase
    client and inject it here — same MAJOR fix as PR #1011's event client,
    applied to the task-queue side (finding #2, PR #1475 review). Not
    unit-tested (needs live Supabase); the tested logic lives in
    :func:`drain_tasks` / :func:`reclaim_stale_tasks` above.
    """

    def __init__(self, client: Client | None = None) -> None:
        self._client = client

    def claim_next(self, *, assignee: str) -> dict[str, Any] | None:
        return task_queue.claim_next(assignee=assignee, client=self._client)

    def count_running(self, *, assignee: str) -> int:
        return task_queue.count_running(assignee=assignee, client=self._client)

    def transition(
        self, task_id: str, to_status: str, *, reason: str | None = None
    ) -> dict[str, Any]:
        return task_queue.transition(task_id, to_status, reason=reason, client=self._client)

    def reclaim_stale_claimed(self, *, assignee: str, older_than_seconds: float) -> int:
        return task_queue.reclaim_stale_claimed(
            assignee=assignee, older_than_seconds=older_than_seconds, client=self._client
        )

    def list_stale_running(
        self, *, assignee: str, older_than_seconds: float
    ) -> list[dict[str, Any]]:
        return task_queue.list_stale_running(
            assignee=assignee, older_than_seconds=older_than_seconds, client=self._client
        )

    def requeue_running(self, task_id: str) -> bool:
        return task_queue.requeue_running(task_id, client=self._client)

    def get_status(self, task_id: str) -> str | None:
        return task_queue.get_status(task_id, client=self._client)

    def get_statuses(self, task_ids: list[str]) -> dict[str, str]:
        return task_queue.get_statuses(task_ids, client=self._client)


def reconcile_stranded_prs(
    github: GitHubClient | None = None,
    *,
    repo: str | None = None,
    dry_run: bool = False,
) -> int:
    """Reconciliation sweep for merged PRs with still-open issues (#1169 item 2).

    Lists open issues with the ``sandcastle`` label. For each, searches merged
    PRs whose body links ``Closes/Fixes/Resolves #<N>`` using ``gh search prs``.
    When a match is found, the issue should have been auto-closed by
    ``pr-merged.yml`` but wasn't (the #948 failure mode — bot merges suppress
    native auto-close). Closes the issue and removes stale labels.

    Uses ``gh`` CLI for issue listing and search; ``github`` client is accepted
    for consistency but not used directly — the search endpoint is GraphQL-only.

    Returns the number of issues closed. Dry-run logs what would be done.
    """
    import json as _json
    import subprocess

    active_repo = repo or os.environ.get("GITHUB_REPO", "your-username/your-repo")

    # List open issues with the sandcastle label
    try:
        result = subprocess.run(
            [
                "gh",
                "issue",
                "list",
                "--repo",
                active_repo,
                "--label",
                "sandcastle",
                "--state",
                "open",
                "--json",
                "number",
                "--jq",
                ".[].number",
            ],
            capture_output=True,
            text=True,
            check=True,
            timeout=30,
        )
    except (subprocess.CalledProcessError, FileNotFoundError, OSError) as exc:
        logger.warning(
            "[task_dispatch] reconcile_stranded_prs: gh issue list failed: %s",
            exc,
        )
        return 0

    issue_numbers = [int(n) for n in result.stdout.strip().split() if n.strip()]
    if not issue_numbers:
        return 0

    closed = 0
    for issue_number in issue_numbers:
        # Search for merged PRs closing this issue
        try:
            search_result = subprocess.run(
                [
                    "gh",
                    "pr",
                    "list",
                    "--repo",
                    active_repo,
                    "--state",
                    "merged",
                    "--json",
                    "number",
                    "title",
                    "body",
                    "--search",
                    f"closes #{issue_number} in:body",
                    "--limit",
                    "1",
                ],
                capture_output=True,
                text=True,
                check=True,
                timeout=30,
            )
        except (subprocess.CalledProcessError, FileNotFoundError, OSError) as exc:
            logger.warning(
                "[task_dispatch] reconcile_stranded_prs: search for #%s failed: %s",
                issue_number,
                exc,
            )
            continue

        merged = _json.loads(search_result.stdout.strip() or "[]")
        if not merged:
            continue

        pr = merged[0]
        pr_number = pr.get("number")
        logger.info(
            "[task_dispatch] reconcile_stranded_prs: issue #%s has "
            "merged PR #%s but is still open — closing",
            issue_number,
            pr_number,
        )
        if not dry_run:
            try:
                subprocess.run(
                    [
                        "gh",
                        "issue",
                        "close",
                        str(issue_number),
                        "--repo",
                        active_repo,
                        "--comment",
                        f"Auto-closed: merged PR #{pr_number} links this issue",
                    ],
                    capture_output=True,
                    check=True,
                    timeout=30,
                )
                closed += 1
            except (subprocess.CalledProcessError, OSError) as exc:
                logger.warning(
                    "[task_dispatch] reconcile_stranded_prs: close #%s failed: %s",
                    issue_number,
                    exc,
                )

    return closed
