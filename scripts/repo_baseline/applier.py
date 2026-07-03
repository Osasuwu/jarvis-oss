"""Applier — the Executor side of repo-baseline (#939, milestone #48 slice 5).

Symmetric to the :class:`~scripts.repo_baseline.auditor.Auditor`: where the
Auditor is a pure-parse *reader* behind an injected ``gh`` runner, the Applier
is a pure *translator* that turns a Planner action plan into an ordered,
idempotency-filtered sequence of semantic mutations (:class:`GhCall`),
rendering MANAGED / LANGUAGE-TEST file content through the
:class:`~scripts.repo_baseline.renderer.Renderer` + canon templates on the way.

The ``Action.content`` field the Planner leaves ``None`` (its docstring names
this slice as the populating step) is filled here.

Altitude boundary (deliberate, mirrors the package's "pure core + thin shell"
idiom):

* This module emits the ordered *semantic mutation sequence* and performs
  **no network writes**. Materialising each :class:`GhCall` into the concrete
  live sync-PR ``gh``/REST calls — branch creation, Contents-API ``sha``
  lookups, ``gh pr create``, and the per-repo admin-merge serialisation for a
  self-modifying ``code-review.yml`` (PRD story 22) — is the live-executor
  layer, deferred to a supervised HITL step so the dangerous tail stays gated.
* :func:`plan_account_pass` is the per-account-pass orchestrator (PRD: "run
  once per GitHub account"). It composes loader + Planner + Applier into a
  per-repo dry-run report; it does not mutate anything either.

Idempotency (PRD story 23): a :class:`GhCall` is emitted only when desired
differs from actual.

* ``SET_CHECK_CONTEXTS`` is dropped when the actual required contexts already
  match the manifest (order-insensitive set comparison).
* A managed-file write is dropped when :class:`ActualState` records a content
  hash equal to the rendered content's hash. The auditor records workflow
  *paths*, not bodies, today, so the actual hash is usually unknown — the write
  is then emitted and the git layer dedupes (a synced repo's sync-PR is empty).
  The hash comparison is forward-compatible for when the auditor learns to read
  bodies (#979).
"""

from __future__ import annotations

import enum
import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import yaml

from .auditor import BASELINE_REPOS, RepoSnapshot
from .manifest import Manifest
from .planner import Action, ActionKind, ActualState, Planner
from .renderer import RenderError, Renderer

# The account pass scope, re-exported from the auditor's canonical list
# (rather than redefined) so the two never drift; named in ``__all__`` so it
# is a deliberate public re-export, not an unused import. Scope comes from
# ``config/repos.conf`` — add your repos there.
__all__ = [
    "BASELINE_REPOS",
    "ApplyError",
    "Applier",
    "GhCall",
    "GhCallKind",
    "RepoPlan",
    "actual_state_from_snapshot",
    "load_canon",
    "load_manifest",
    "load_snapshot",
    "plan_account_pass",
]

_MODULE_DIR = Path(__file__).resolve().parent
CANON_DIR = _MODULE_DIR / "canon"
MANIFESTS_DIR = _MODULE_DIR / "manifests"
SNAPSHOTS_DIR = _MODULE_DIR / "snapshots"


class ApplyError(ValueError):
    """Raised when a plan cannot be translated — e.g. a managed-file write
    references a path with no canon template, or an unknown action kind."""


class GhCallKind(enum.Enum):
    """The semantic mutation kinds the Applier emits."""

    PUT_FILE = "put_file"
    """Create or overwrite a managed file with rendered content."""

    DELETE_FILE = "delete_file"
    """Remove a file no longer in the managed set."""

    SET_CHECK_CONTEXTS = "set_check_contexts"
    """Reconcile the required status-check contexts on the default branch."""


@dataclass(frozen=True)
class GhCall:
    """One semantic mutation in the ordered apply sequence.

    Frozen + tuple-valued so calls are hashable, comparable value objects —
    tests assert an exact expected sequence by equality.
    """

    kind: GhCallKind
    path: str
    content: Optional[str] = None
    """Rendered file body (PUT_FILE only)."""

    file_class: Optional[str] = None
    """Traceability — managed / language_test (PUT_FILE only)."""

    contexts: tuple[str, ...] = ()
    """Desired required-check contexts (SET_CHECK_CONTEXTS only)."""


HASH_PREFIX = "sha256:"
"""Algorithm tag on every stored content hash. The format is part of the
cross-slice contract: when the auditor (#979) starts recording file-body
hashes into ``ActualState.files`` it MUST emit ``"sha256:<hex of utf-8 bytes>"``
— NOT the GitHub Contents-API ``sha`` (a git blob SHA1 over
``"blob {size}\\0{content}"``). Without the tag a SHA1-vs-SHA256 mismatch would
compare unequal *silently* and every managed file would emit a spurious write on
an already-synced repo, defeating idempotency forever with no CI signal. The
tag makes the format explicit so any future divergence is visible at the call
site, not buried in an always-false comparison."""


def _content_hash(text: str) -> str:
    """Stable, algorithm-tagged content hash for write-idempotency comparison."""
    return HASH_PREFIX + hashlib.sha256(text.encode("utf-8")).hexdigest()


def _canon_name(repo_path: str) -> str:
    """Canon template key for a repo-path — its basename.

    Managed-set basenames are unique (``code-review.yml``, ``bug.yml``,
    ``PULL_REQUEST_TEMPLATE.md`` …), so a basename key cleanly maps a repo-path
    like ``.github/ISSUE_TEMPLATE/bug.yml`` to ``canon/bug.yml``.
    """
    return repo_path.rsplit("/", 1)[-1]


class Applier:
    """Pure translator: action plan + actual state -> ordered ``GhCall`` list."""

    def __init__(
        self,
        manifest: Manifest,
        canon: dict[str, str],
        renderer: Optional[Renderer] = None,
    ):
        self.manifest = manifest
        # Canon templates keyed by basename (see ``load_canon``). A string
        # literal here would NOT be picked up as an attribute docstring by
        # Python tooling, so it stays a real comment.
        self.canon = canon
        self.renderer = renderer or Renderer()

    def render_content(self, repo_path: str) -> str:
        """Render the canon template for *repo_path* through the manifest axes.

        Raises :class:`ApplyError` if no canon template covers the path — a
        loud, attributable failure rather than a silently skipped write.
        """
        name = _canon_name(repo_path)
        template = self.canon.get(name)
        if template is None:
            raise ApplyError(
                f"No canon template for managed path {repo_path!r} "
                f"(expected canon entry {name!r}; repo={self.manifest.repo!r})"
            )
        return self.renderer.render(template, self.manifest)

    def missing_canon(self, plan: list[Action]) -> list[str]:
        """Repo-paths in *plan* that WRITE but have no canon template.

        A pre-flight gap check the orchestrator uses to surface an incomplete
        canon set without aborting the whole account pass.
        """
        return [
            a.path
            for a in plan
            if a.kind == ActionKind.WRITE_FILE and _canon_name(a.path) not in self.canon
        ]

    def _basename_collisions(self, plan: list[Action]) -> list[str]:
        """Canon basenames shared by two+ WRITE paths in *plan*.

        :func:`_canon_name` keys canon templates by basename, which is only safe
        while managed-set basenames are unique. Two managed paths sharing a
        basename (``a/x.yml`` and ``b/x.yml``) would both render the *same*
        template — silent wrong-content. This makes that invariant checkable.
        """
        names = [_canon_name(a.path) for a in plan if a.kind == ActionKind.WRITE_FILE]
        return sorted({n for n in names if names.count(n) > 1})

    def translate(self, plan: list[Action], actual: ActualState) -> list[GhCall]:
        """Translate a rendered plan into the idempotency-filtered call sequence.

        Order is preserved from the plan (the Planner already encodes the
        files-before-protection ordering invariant, PRD story 10).

        Self-enforcing pre-flight (so a direct caller can't get a partially built
        list silently discarded): raises :class:`ApplyError` upfront if any
        WRITE path lacks a canon template (mirrors :meth:`missing_canon`) or if
        two WRITE paths collide on a canon basename — *before* emitting any call.
        """
        gaps = self.missing_canon(plan)
        if gaps:
            raise ApplyError(
                f"No canon template for managed paths {gaps!r} "
                f"(repo={self.manifest.repo!r}); run missing_canon() pre-flight"
            )
        collisions = self._basename_collisions(plan)
        if collisions:
            raise ApplyError(
                f"Canon basename collision among managed paths {collisions!r} "
                f"(repo={self.manifest.repo!r}); basenames must be unique"
            )
        calls: list[GhCall] = []
        for action in plan:
            if action.kind == ActionKind.WRITE_FILE:
                content = self.render_content(action.path)
                actual_hash = actual.files.get(action.path)
                # Three distinct states, only the third is idempotent:
                #   None -> path absent (new file)         -> emit PUT
                #   ""   -> present but body unknown        -> emit PUT (git dedupes)
                #   tag  -> known hash; skip iff it matches -> idempotent
                # None and "" are both falsy but semantically different, so the
                # guard is explicit rather than relying on truthiness.
                if actual_hash and not actual_hash.startswith(HASH_PREFIX):
                    # Enforce the cross-slice hash contract at runtime (see
                    # HASH_PREFIX): an untagged value (e.g. a raw git-blob SHA1
                    # from a future auditor) would compare unequal forever and
                    # silently defeat idempotency. Fail loud instead.
                    raise ApplyError(
                        f"Content hash for {action.path!r} lacks the {HASH_PREFIX!r} "
                        f"algorithm tag (got {actual_hash!r}); #979 must emit tagged "
                        f"sha256 hashes — see HASH_PREFIX."
                    )
                if (
                    actual_hash is not None
                    and actual_hash != ""
                    and actual_hash == _content_hash(content)
                ):
                    continue  # idempotent: already byte-identical on the repo
                calls.append(
                    GhCall(
                        kind=GhCallKind.PUT_FILE,
                        path=action.path,
                        content=content,
                        file_class=action.file_class,
                    )
                )
            elif action.kind == ActionKind.DELETE_FILE:
                # Only delete what actually exists (the Planner derives deletes
                # from actual state, so this is defensive — a stale plan replayed
                # against a repo where the file is already gone is a no-op).
                if action.path in actual.files:
                    calls.append(GhCall(kind=GhCallKind.DELETE_FILE, path=action.path))
            elif action.kind == ActionKind.SET_CHECK_CONTEXTS:
                desired = list(action.context_names)
                # Sorted-list (not set) comparison so a duplicate on the actual
                # side — a real GitHub API quirk where contexts come back with
                # repeats — is detected as drift and gets cleaned up, instead of
                # being collapsed away by set() and left on the repo forever. The
                # emitted call carries the de-duplicated desired set.
                if sorted(desired) == sorted(actual.required_check_contexts):
                    continue  # idempotent: required gates already match
                calls.append(
                    GhCall(
                        kind=GhCallKind.SET_CHECK_CONTEXTS,
                        path=action.path,
                        contexts=tuple(dict.fromkeys(desired)),
                    )
                )
            else:  # pragma: no cover — defensive against a new ActionKind
                raise ApplyError(f"Unknown action kind {action.kind!r}")
        return calls


# ── Loaders ──────────────────────────────────────────────────────────


_CANON_SUFFIXES = (".yml", ".yaml", ".md")
"""Template extensions a canon entry may carry. An allowlist (not a
``__init__.py`` denylist) so a stray ``.py`` helper or ``.DS_Store`` dropped in
``canon/`` can never be picked up as a renderable template — the basename would
silently shadow a real managed path otherwise."""


def load_canon(canon_dir: Path = CANON_DIR) -> dict[str, str]:
    """Load canon templates as a ``{basename: text}`` map.

    Only files whose suffix is in :data:`_CANON_SUFFIXES` are loaded (so the
    ``__init__.py`` package marker and any non-template stray are skipped).
    Sorted for deterministic iteration in error messages.
    """
    if not canon_dir.is_dir():
        raise ApplyError(f"Canon directory not found: {canon_dir}")
    return {
        p.name: p.read_text(encoding="utf-8")
        for p in sorted(canon_dir.iterdir())
        if p.is_file() and p.suffix in _CANON_SUFFIXES
    }


def _slug(repo: str) -> str:
    """``your-username/jarvis`` -> ``your-username__jarvis`` (mirrors generate_snapshots)."""
    return repo.replace("/", "__")


def load_manifest(repo: str, manifests_dir: Path = MANIFESTS_DIR) -> Manifest:
    """Load the committed seeded manifest for *repo*."""
    path = manifests_dir / f"{_slug(repo)}.manifest.yml"
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    # Only an empty document (``None``) defaults to an empty manifest. A
    # list/scalar-shaped YAML (copy-paste slip, anchor regression) is a defect,
    # not an empty manifest — ``data or {}`` would silently swallow ``[]`` (falsy)
    # into ``{}``, so guard the type explicitly rather than via truthiness.
    if data is None:
        data = {}
    if not isinstance(data, dict):
        raise ApplyError(
            f"Manifest for {repo!r} is not a mapping (got {type(data).__name__}): {path}"
        )
    return Manifest.from_dict(data)


def load_snapshot(repo: str, snapshots_dir: Path = SNAPSHOTS_DIR) -> RepoSnapshot:
    """Load the committed audit snapshot for *repo*."""
    path = snapshots_dir / f"{_slug(repo)}.snapshot.json"
    return RepoSnapshot.from_dict(json.loads(path.read_text(encoding="utf-8")))


def actual_state_from_snapshot(snapshot: RepoSnapshot) -> ActualState:
    """Bridge an audit :class:`RepoSnapshot` to the Planner's :class:`ActualState`.

    Only the axes the snapshot observes are populated: present workflow paths
    (with unknown content hash — the auditor records paths, not bodies, so the
    empty-string hash signals "present but content unknown") and the required
    check contexts. Template/community-health file presence and file bodies are
    not yet captured by the auditor (#979 deepens this), so write-idempotency on
    those falls through to the git layer.

    Consequence for DELETE_FILE: ``translate`` emits a delete only for a path
    present in ``files`` (the existence guard), and ``files`` here is exactly the
    snapshot's ``workflows`` list. So DELETE coverage is currently limited to
    ``.github/workflows/*`` paths — a stale non-workflow file outside the managed
    set cannot be detected for deletion until the auditor enumerates those paths
    (#979). Not a correctness bug (no spurious deletes), a coverage gap.
    """
    files = {path: "" for path in snapshot.workflows}
    contexts = list(snapshot.branch_protection.contexts) if snapshot.branch_protection else []
    return ActualState(files=files, required_check_contexts=contexts)


# ── Per-account-pass orchestrator (dry-run) ──────────────────────────


@dataclass
class RepoPlan:
    """The translated apply sequence for one repo, plus pre-flight gaps."""

    repo: str
    calls: list[GhCall] = field(default_factory=list)
    canon_gaps: list[str] = field(default_factory=list)
    """Managed-write paths with no canon template — a gap to fill before any
    live run; when non-empty the repo's calls are left empty (not translated).

    Named ``canon_gaps`` (not ``missing_canon``) so it never collides with the
    :meth:`Applier.missing_canon` *method*: ``if plan.canon_gaps:`` can't be
    confused with a forgotten method call (a bound method is always truthy)."""

    error: Optional[str] = None
    """Set when this repo's plan could not be built at all (bad fixture,
    malformed manifest/snapshot, render defect). Isolated per-repo so one bad
    repo never discards the rest of the account pass (PRD story 9)."""


def plan_account_pass(
    repos: list[str],
    *,
    canon: Optional[dict[str, str]] = None,
    manifests_dir: Path = MANIFESTS_DIR,
    snapshots_dir: Path = SNAPSHOTS_DIR,
) -> list[RepoPlan]:
    """Build the dry-run apply plan for an account's repos (PRD: per-account pass).

    For each repo: load its seeded manifest + committed snapshot, run the
    Planner to get the action plan, then the Applier to translate it into the
    idempotency-filtered ``GhCall`` sequence. A repo with a canon gap is
    reported (``canon_gaps`` populated, ``calls`` empty) rather than aborting
    the whole pass; a repo that fails to load/translate at all is reported with
    ``error`` set. Either way the rest of the account pass continues — staged
    blast-radius (PRD story 9) starts with knowing which repos are applyable.

    This performs **no live writes**: it is the planning half of slice 5. The
    live sync-PR executor that consumes these ``RepoPlan``\\ s is the supervised
    follow-up.

    The ``canon`` argument has two deliberately distinct values:
    ``None`` (the default) loads the committed canon set from disk; an explicit
    dict is used verbatim — so ``canon={}`` means "no templates available" and
    every managed-file WRITE becomes a ``canon_gaps`` entry (a fully-gapped
    no-op run), *not* "load from disk". Pass ``None``, never ``{}``, to mean
    "use the real canon".
    """
    canon = canon if canon is not None else load_canon()
    plans: list[RepoPlan] = []
    for repo in repos:
        try:
            manifest = load_manifest(repo, manifests_dir)
            snapshot = load_snapshot(repo, snapshots_dir)
            actual = actual_state_from_snapshot(snapshot)
            plan = Planner(manifest).plan(actual)
            applier = Applier(manifest, canon)
            gaps = applier.missing_canon(plan)
            if gaps:
                plans.append(RepoPlan(repo=repo, calls=[], canon_gaps=gaps))
                continue
            plans.append(RepoPlan(repo=repo, calls=applier.translate(plan, actual)))
        except (ApplyError, RenderError, OSError, ValueError, json.JSONDecodeError) as exc:
            # Per-repo isolation: a bad fixture / malformed manifest / render
            # defect for one repo must not sink the whole account pass.
            plans.append(RepoPlan(repo=repo, error=str(exc)))
    return plans
