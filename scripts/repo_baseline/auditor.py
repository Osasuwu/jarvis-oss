"""Auditor — thin gh/REST shell that dumps a repo's real GitHub setup.

Slice 1 of repo-baseline (milestone #48), the dependency root. The Auditor
reads each repo's labels / workflows / settings / branch-protection /
dependabot ecosystems and emits a structured :class:`RepoSnapshot`. The
committed JSON snapshots become the canonical fixtures every downstream
pure-core test runs against, and :func:`seed_manifest` derives a per-repo
:class:`~scripts.repo_baseline.manifest.Manifest` skeleton from a snapshot.

The gh/REST boundary is a single injected *runner* callable so the parsing
logic is fully unit-testable against canned ``gh api`` JSON — no live calls,
no network. The live runner (:func:`gh_runner`) shells out to ``gh api``.
"""

from __future__ import annotations

import base64
import json
import re
import subprocess
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from typing import Any, Protocol

import yaml


class GhNotFound(Exception):
    """Raised by a runner when a ``gh api`` path 404s.

    Used for *expected* absences — a repo with no branch protection or no
    ``.github/dependabot.yml`` — which the Auditor treats as "feature off",
    not an error.
    """


class GhRunner(Protocol):
    """A runner takes a gh api path (e.g. ``repos/your-username/jarvis/labels``) and
    returns parsed JSON. ``paginate=True`` requests ``gh api --paginate``.

    A ``Protocol`` (not a bare ``Callable[..., Any]`` alias) so the keyword-only
    ``paginate`` parameter is part of the type — a runner missing it, or taking
    it positionally, is a type error rather than silently accepted.
    """

    def __call__(self, path: str, *, paginate: bool = False) -> Any: ...


def _load_account_passes() -> dict[str, list[str]]:
    """Audit scope from ``config/repos.conf``, grouped one pass per owner.

    The account pass is the **write** unit of the orchestration, not the read
    unit: admin-scoped writes (repo settings, branch protection) need each
    owner's own credential, while audit reads usually succeed under a single
    token with collaborator access.

    Falls back to a single ``example-owner`` pass over
    ``example-owner/example-repo`` when the config is missing or carries no
    real entries yet, so the committed example fixture (and its round-trip
    test) still resolves on a fresh clone. Add your repos to
    ``config/repos.conf`` to widen the audit scope.
    """
    passes: dict[str, list[str]] = {}
    conf = Path(__file__).resolve().parents[2] / "config" / "repos.conf"
    try:
        for raw in conf.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            slug = line.split()[0]  # "owner/repo" (drop trailing project=N)
            if "/" in slug:
                passes.setdefault(slug.split("/", 1)[0].lower(), []).append(slug)
    except OSError:
        pass
    return passes or {"example-owner": ["example-owner/example-repo"]}


ACCOUNT_PASSES: dict[str, list[str]] = _load_account_passes()
_pass_lists = list(ACCOUNT_PASSES.values())
# First-listed owner in config/repos.conf forms the primary account pass; any
# further owners form the secondary pass (flat list — one extra account is the
# common case).
BASELINE_REPOS: list[str] = _pass_lists[0]
SECONDARY_REPOS: list[str] = [r for lst in _pass_lists[1:] for r in lst]


# ── Snapshot value objects ───────────────────────────────────────────


@dataclass(frozen=True)
class LabelSnapshot:
    """A label as found on a repo — name + color + description."""

    name: str
    color: str = ""
    description: str = ""


@dataclass
class RepoSettings:
    """Repo-level merge/visibility settings (from ``GET /repos/{repo}``).

    Merge-method defaults mirror GitHub's own repo defaults: squash, merge-commit,
    and rebase are all **enabled** on a fresh repo, so omitting them from the API
    payload means "on", not "off". ``allow_auto_merge`` and
    ``delete_branch_on_merge`` default off — GitHub leaves those disabled until
    explicitly turned on. Aligning the dataclass defaults with the API's omission
    semantics keeps :meth:`Auditor._read_settings` honest when a field is absent.
    """

    allow_auto_merge: bool = False
    allow_squash_merge: bool = True
    allow_merge_commit: bool = True
    allow_rebase_merge: bool = True
    delete_branch_on_merge: bool = False
    visibility: str = "public"
    default_branch: str = "main"


@dataclass
class BranchProtection:
    """Required-status-check config on the default branch.

    Scope is deliberately narrow: only the ``required_status_checks`` slice of
    GitHub's branch-protection payload (``strict`` flag + required context
    names). The other protection axes — required PR reviews, enforce-admins,
    restrictions, linear-history, force-push/deletion locks — are intentionally
    **not** modelled here. The baseline cares whether the right CI checks gate
    merges; the richer protection surface is out of scope for slice 1 and a
    downstream slice can extend this dataclass if the applier needs those axes.

    ``contexts_source`` is a parallel list to ``contexts``: for each required
    check context, the workflow file path that produces it (or ``None`` when no
    local workflow defines it — e.g. a marketplace/app check like ``review``).
    Populated by the auditor during :meth:`Auditor.audit`. ``None`` means
    provenance was not computed (legacy snapshot); an entry of ``None`` inside
    the list means that specific context has no matching local workflow.
    Added in issue #979.
    """

    strict: bool = False
    contexts: list[str] = field(default_factory=list)
    contexts_source: list[str | None] | None = None


@dataclass
class RepoSnapshot:
    """Structured snapshot of a repo's real GitHub setup."""

    repo: str
    settings: RepoSettings
    labels: list[LabelSnapshot] = field(default_factory=list)
    workflows: list[str] = field(default_factory=list)
    branch_protection: BranchProtection | None = None
    dependabot_ecosystems: list[str] = field(default_factory=list)

    observed_runners: dict[str, list[list[str]]] = field(default_factory=dict)
    """Runner classes observed in the repo's own workflows, keyed by workflow path.

    Each value is the *deduped* list of ``runs-on:`` label-lists appearing in
    that file's jobs, so a file contributes one vote per distinct runner class
    rather than one per job (a 5-job workflow must not outvote five 1-job ones).
    Keyed by path — not flattened — so the majority resolution can exclude the
    repo-baseline-managed files. Added in issue #1406.
    """

    has_tests_ci: bool | None = None
    """Whether the repo has a ``tests/ci`` directory — ``None`` when unobserved.

    Tri-state on purpose (#1406). Canon ci-meta.yml runs ``pytest tests/ci/``,
    which exits 4 on a repo without that directory, so a *positive* observation
    of absence skips the file. Silence is not absence: a snapshot fixture
    written before this field existed must keep the pre-#1406 behaviour rather
    than have ci-meta.yml dropped from every repo at once.
    """

    def to_dict(self) -> dict[str, Any]:
        """Structural dict for JSON serialization (no scrub — see
        :func:`scrub_topology`, applied at fixture-write time).

        Uses :func:`dataclasses.asdict`, which recurses through the nested
        dataclasses (``RepoSettings``, ``LabelSnapshot``, ``BranchProtection``)
        and **deep-copies** every contained list. The previous ``vars().copy()``
        was a shallow copy that shared the ``BranchProtection.contexts`` list
        with the live snapshot — mutating the returned dict leaked back into the
        object. ``asdict`` produces an identical key shape with independent copies.
        """
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "RepoSnapshot":
        # Filter each nested dict to the dataclass's own fields before splatting.
        # A snapshot written by a *newer* schema (extra keys on settings /
        # branch_protection / a label) must round-trip through an older reader
        # without a ``TypeError: unexpected keyword argument`` — forward
        # compatibility for the committed fixtures. Unknown keys are dropped, not
        # an error: the reader simply ignores axes it does not model yet.
        bp = data.get("branch_protection")
        return cls(
            repo=data["repo"],
            settings=RepoSettings(**_only_fields(RepoSettings, data["settings"])),
            labels=[
                LabelSnapshot(**_only_fields(LabelSnapshot, lb)) for lb in data.get("labels", [])
            ],
            workflows=list(data.get("workflows", [])),
            branch_protection=(
                BranchProtection(**_only_fields(BranchProtection, bp)) if bp is not None else None
            ),
            dependabot_ecosystems=list(data.get("dependabot_ecosystems", [])),
            observed_runners={
                path: [list(labels) for labels in classes]
                for path, classes in (data.get("observed_runners") or {}).items()
            },
            # ``.get`` with no default, deliberately: a fixture predating the
            # field restores as ``None`` (unobserved), not ``False`` (absent).
            has_tests_ci=data.get("has_tests_ci"),
        )


def _only_fields(cls: type, data: dict[str, Any]) -> dict[str, Any]:
    """Project *data* onto the field names declared by dataclass *cls*.

    Keeps :meth:`RepoSnapshot.from_dict` forward-compatible: a fixture carrying
    keys a newer schema added is parsed by an older reader instead of raising.
    """
    valid = {f.name for f in fields(cls)}
    return {k: v for k, v in data.items() if k in valid}


def _is_workflow_file(path: str) -> bool:
    """True for a real ``.github/workflows/*.yml|*.yaml`` repository path (#1406)."""
    return path.startswith(".github/workflows/") and path.endswith((".yml", ".yaml"))


def _runner_classes(workflow: Any) -> list[list[str]]:
    """Deduped ``runs-on:`` label-lists across a parsed workflow's jobs (#1406).

    A scalar ``runs-on:`` normalises to a single-element list. A class carrying a
    ``${{ … }}`` expression (matrix fan-out, a repo variable) is *unresolvable*
    without evaluating the workflow, so it is dropped rather than counted as a
    literal label — a majority vote must not be swayed by ``${{ matrix.os }}``.
    """
    jobs = (workflow or {}).get("jobs") or {}
    classes: list[list[str]] = []
    for job in jobs.values():
        runs_on = (job or {}).get("runs-on")
        labels = [runs_on] if isinstance(runs_on, str) else list(runs_on or [])
        if any("${{" in str(label) for label in labels):
            continue
        if labels and labels not in classes:
            classes.append(labels)
    return classes


# ── Auditor ──────────────────────────────────────────────────────────


class Auditor:
    """Reads live repo state into a :class:`RepoSnapshot` via an injected runner."""

    def __init__(self, runner: GhRunner):
        self.runner = runner

    def audit(self, repo: str) -> RepoSnapshot:
        """Build a full snapshot for *repo* (``owner/name``).

        Any reader failure (a malformed dependabot.yml, an unexpected gh error,
        a missing core endpoint) is re-raised with the repo name prepended, so a
        failure in a batch ``audit_all`` run is attributable to a specific repo
        rather than an anonymous stack trace. The inner message is interpolated
        with its exception type, preserving the original detail (encoding
        mismatch, parser error, …).

        Note the asymmetry in how :class:`GhNotFound` is handled: the two
        *optional*-feature readers (``_read_branch_protection``,
        ``_read_dependabot_ecosystems``) catch it internally and report the
        feature as off. A ``GhNotFound`` from a *core* endpoint — settings,
        labels, workflows — is **not** expected (a real repo always has these),
        so it propagates here and is wrapped as a ``RuntimeError`` like any
        other failure. A 404 on a core endpoint means the repo name is wrong or
        the token can't see it; surfacing it as a loud per-repo error is correct.
        """
        try:
            settings = self._read_settings(repo)
            workflows, wf_name_map = self._read_workflows(repo)
            bp = self._read_branch_protection(repo, settings.default_branch)
            if bp is not None:
                # For each required check context, try to find a workflow whose
                # name matches — that workflow's path is the provenance source.
                # Contexts not produced by any local workflow (e.g. marketplace
                # app checks like "review") get a null entry.
                bp.contexts_source = [wf_name_map.get(ctx) for ctx in bp.contexts]
            return RepoSnapshot(
                repo=repo,
                settings=settings,
                labels=self._read_labels(repo),
                workflows=workflows,
                branch_protection=bp,
                dependabot_ecosystems=self._read_dependabot_ecosystems(repo),
                observed_runners=self._read_workflow_runners(repo, workflows),
                has_tests_ci=self._read_has_tests_ci(repo),
            )
        except Exception as e:  # noqa: BLE001 — boundary: attribute to the repo
            raise RuntimeError(f"Audit failed for {repo!r}: {type(e).__name__}: {e}") from e

    def audit_all(self, repos: list[str]) -> dict[str, RepoSnapshot]:
        """Audit each repo in *repos*, returning a ``repo -> RepoSnapshot`` map.

        Iteration order follows *repos*; the dict preserves insertion order so
        the committed fixture set is deterministic.

        Does **not** fail fast: a single repo's failure must not discard the
        snapshots already built for its siblings (a dict comprehension would
        throw out the whole batch on the first error). Every repo is attempted;
        if any failed, a single :class:`RuntimeError` naming *all* failures is
        raised at the end so one bad repo doesn't mask a second.
        """
        snapshots: dict[str, RepoSnapshot] = {}
        errors: dict[str, Exception] = {}
        for repo in repos:
            try:
                snapshots[repo] = self.audit(repo)
            except Exception as e:  # noqa: BLE001 — collect, don't fail-fast
                errors[repo] = e
        if errors:
            detail = "; ".join(f"{r} ({type(e).__name__}: {e})" for r, e in errors.items())
            raise RuntimeError(
                f"audit_all: {len(errors)} of {len(repos)} repo(s) failed — {detail}"
            )
        return snapshots

    # ── per-aspect readers ────────────────────────────────────────────

    def _read_settings(self, repo: str) -> RepoSettings:
        data = self.runner(f"repos/{repo}")
        return RepoSettings(
            allow_auto_merge=bool(data.get("allow_auto_merge", False)),
            # GitHub omits a merge method from the payload only when it equals the
            # platform default, which for squash/merge-commit/rebase is *enabled*.
            # Defaulting these to False would misreport an unconfigured repo as
            # having every merge button switched off.
            allow_squash_merge=bool(data.get("allow_squash_merge", True)),
            allow_merge_commit=bool(data.get("allow_merge_commit", True)),
            allow_rebase_merge=bool(data.get("allow_rebase_merge", True)),
            delete_branch_on_merge=bool(data.get("delete_branch_on_merge", False)),
            visibility=data.get("visibility", "public"),
            default_branch=data.get("default_branch", "main"),
        )

    def _read_labels(self, repo: str) -> list[LabelSnapshot]:
        data = self.runner(f"repos/{repo}/labels", paginate=True)
        return [
            LabelSnapshot(
                name=lb["name"],
                color=lb.get("color", ""),
                description=lb.get("description") or "",
            )
            for lb in data
        ]

    def _read_workflows(self, repo: str) -> tuple[list[str], dict[str, str]]:
        # The workflows endpoint returns a ``{total_count, workflows: [...]}``
        # envelope, not a bare array, so the runner's array-paginate merge path
        # cannot handle it — ``--paginate`` here would trip the "unexpected page
        # structure" guard. Instead bump ``per_page`` to 100 (the API max) to get
        # every workflow in a single page; the default page size is 30, which
        # would silently truncate a repo with more than 30 workflows. 100 covers
        # any realistic repo in scope; a repo exceeding it would need true
        # envelope-aware pagination, which no current target requires.
        data = self.runner(f"repos/{repo}/actions/workflows?per_page=100")
        wfs = data.get("workflows", [])
        paths = [wf["path"] for wf in wfs]
        name_to_path = {wf["name"]: wf["path"] for wf in wfs}
        return paths, name_to_path

    def _read_workflow_runners(self, repo: str, paths: list[str]) -> dict[str, list[list[str]]]:
        """Observe each workflow's ``runs-on:`` labels (issue #1406).

        Best-effort by construction: this is an *observation*, not a core
        endpoint, so a file the API lists but cannot serve must never take the
        whole repo's audit down with it (``audit`` wraps every escaping
        exception as a per-repo ``RuntimeError``). Two filters apply:

        * **Shape** — the workflows API also lists synthetic entries whose
          ``path`` is not a repository file at all (GitHub's own auto-generated
          workflows report ``dynamic/…``). Those are dropped before any fetch.
        * **Per-file 404** — a listed workflow whose blob is gone answers 404
          (a repo's ``_runner-smoke.yml`` can do this).
          Skipped, not raised.
        """
        observed: dict[str, list[list[str]]] = {}
        for path in paths:
            if not _is_workflow_file(path):
                continue
            try:
                data = self.runner(f"repos/{repo}/contents/{path}")
            except GhNotFound:
                continue
            raw = base64.b64decode(data["content"]).decode("utf-8")
            classes = _runner_classes(yaml.safe_load(raw))
            if classes:
                observed[path] = classes
        return observed

    def _read_has_tests_ci(self, repo: str) -> bool | None:
        """Observe whether the repo has a ``tests/ci`` directory (issue #1406).

        Optional-feature reader: a 404 is the answer (*no such directory*), not
        a failure. Anything else propagates — an auth or rate-limit error must
        not be misread as "absent", which would silently drop ci-meta.yml from a
        repo that actually has the suite.
        """
        try:
            self.runner(f"repos/{repo}/contents/tests/ci")
        except GhNotFound:
            return False
        return True

    def _read_branch_protection(self, repo: str, default_branch: str) -> BranchProtection | None:
        try:
            data = self.runner(f"repos/{repo}/branches/{default_branch}/protection")
        except GhNotFound:
            return None
        rsc = data.get("required_status_checks") or {}
        # GitHub deprecated ``required_status_checks.contexts`` in favour of
        # ``.checks`` ([{context, app_id}]). A repo configured after that
        # migration reports ``contexts: []`` with the real names living in
        # ``.checks`` — reading only ``contexts`` would record an
        # apparently-protected branch with zero required checks. Prefer
        # ``contexts`` when present, fall back to the ``.checks`` context names.
        contexts = list(rsc.get("contexts") or []) or [c["context"] for c in rsc.get("checks", [])]
        return BranchProtection(
            strict=bool(rsc.get("strict", False)),
            contexts=contexts,
        )

    def _read_dependabot_ecosystems(self, repo: str) -> list[str]:
        try:
            data = self.runner(f"repos/{repo}/contents/.github/dependabot.yml")
        except GhNotFound:
            return []
        # The contents API base64-encodes file bodies. For files above ~1MB it
        # switches to ``encoding: "none"`` and serves the body out-of-band — a
        # blind ``b64decode`` would silently produce garbage. Guard explicitly so
        # an unexpected encoding is a loud failure, not a misparsed manifest.
        encoding = data.get("encoding", "base64")
        if encoding != "base64":
            raise RuntimeError(
                f"dependabot.yml for {repo!r} returned unexpected content encoding "
                f"{encoding!r} (expected 'base64')"
            )
        raw = base64.b64decode(data["content"]).decode("utf-8")
        parsed = yaml.safe_load(raw) or {}
        updates = parsed.get("updates", []) or []
        return [u["package-ecosystem"] for u in updates if "package-ecosystem" in u]


# ── Manifest seeding ─────────────────────────────────────────────────


def seed_manifest(snapshot: RepoSnapshot) -> dict[str, Any]:
    """Derive a per-repo manifest dict from an observed snapshot.

    The seed captures *observed reality* as explicit axis values so the owner
    edits a populated skeleton rather than writing a manifest from scratch.

    Only the axes the snapshot actually observes are emitted — and they are
    emitted **explicitly**, including their absences (``auto_merge=False``,
    ``dependabot_ecosystems=[]``). Relying on the ``full`` profile's defaults
    for these would misreport a bare repo as fully baselined. Axes the snapshot
    does not capture (``ci_language``, ``code_review_marketplace``,
    ``test_extras`` — all buried inside workflow YAML bodies the auditor only
    records paths for) are left out so they resolve to the profile default; a
    downstream slice can deepen the audit to populate them.

    ``runs_on`` is deliberately absent from the seed *and* from the manifest
    schema (#1406): it is a fact about the repo, not a policy for it, so it is
    read from :attr:`RepoSnapshot.observed_runners` on every pass rather than
    declared once and left to drift. Seeding it would reintroduce exactly the
    "owner must verify before the executor runs" manual step that shipped
    GitHub-hosted workflows into a billing-blocked account.

    **Profile is chosen from observed governance posture, not hardcoded.** A bare
    repo (no auto-merge *and* no branch protection) seeds ``profile: "minimal"``
    so the skeleton reflects what the repo *is*, not an aspirational target —
    seeding ``full`` for a bare repo would prescribe a baseline rather than
    observe one (#978 MAJOR 2). Everything else seeds ``full``.

    **Profile heuristic is binary (#978 MAJOR 7).**
    ``is_bare = not allow_auto_merge and branch_protection is None`` collapses the
    2×2 governance space (auto-merge × branch-protection) onto two labels: a repo
    with *neither* governance signal seeds ``minimal``; anything with *either*
    seeds ``full``. The rule is empirical (which label fits the observed posture),
    deliberately not enumerated per-repo here — concrete repo postures are state
    that drifts and belongs in the committed snapshot/manifest fixtures, not in a
    docstring.

    A **partially governed** repo (e.g. branch protection on but auto-merge off)
    is not bare, so it rounds up to ``full`` — but the seed still emits its actual
    ``auto_merge`` / ``branch_protection`` values explicitly, so the partial state
    stays visible in the manifest body; only the coarse *profile label* rounds up.
    A dedicated third ``governed`` profile distinguishing partial from full
    governance is deliberately **deferred to #939** (the applier), where the
    profile→action mapping is defined and a finer label earns its keep — adding it
    here would be an unused indirection.

    Caveat for #939: the profile only governs the *governance* axes
    (auto-merge / branch-protection / managed-file set). The language axes
    (``ci_language``, ``test_extras``) still resolve to the
    profile default — ``minimal`` does **not** override ``ci_language``, so a
    non-Python bare repo will seed ``ci_language: "python"`` by inheritance.
    The owner must verify those axes before the #939 applier runs; full
    repo-language detection is deferred to #939 as out-of-scope here.

    The result is a plain dict accepted unchanged by
    :meth:`~scripts.repo_baseline.manifest.Manifest.from_dict`.
    """
    bp = snapshot.branch_protection
    is_bare = not snapshot.settings.allow_auto_merge and bp is None
    profile = "minimal" if is_bare else "full"
    return {
        "repo": snapshot.repo,
        "profile": profile,
        "visibility": snapshot.settings.visibility,
        "auto_merge": snapshot.settings.allow_auto_merge,
        "branch_protection": bp is not None,
        "required_check_contexts": list(bp.contexts) if bp is not None else [],
        # The snapshot lists one ecosystem per dependabot update block, so a repo
        # with pip blocks for two directories yields a duplicated entry. The
        # manifest axis is a *set* of ecosystem types (directory granularity
        # lives in the managed file body, not the axis) — dedupe, first-seen order.
        "dependabot_ecosystems": _dedupe(snapshot.dependabot_ecosystems),
    }


def _dedupe(items: list[str]) -> list[str]:
    """Deduplicate preserving first-seen order (``dict.fromkeys`` idiom)."""
    return list(dict.fromkeys(items))


# ── Topology scrubbing (fixture-write hygiene) ───────────────────────

# jarvis is a PUBLIC repo: committed snapshot fixtures must not leak device or
# infra topology. The username segment is matched *positionally* (any user, not
# a hardcoded login) so the scrub survives a different operator on another
# device. Over-redaction is the safe direction here.
#
# Scope boundary (intentional, documented for the audit trail): the POSIX home
# pattern covers ``/home/<user>`` and ``/Users/<user>`` only. macOS-specific
# topology paths — ``/private/Users``, ``/var/folders/...``, ``/run/user/<uid>``
# — are NOT scrubbed. The auditor runs on the owner's Windows box and emits
# GitHub-derived snapshots (no local paths in the payload), so these never
# appear in practice; widening the pattern would risk redacting legitimate
# repo content. Revisit if the auditor ever runs on macOS or ingests local FS
# paths. Tailnet scrub matches the full ``100.x`` block (not narrowed to the
# 100.64/10 CGNAT range) — over-redaction is the safe direction.
_OCTET = r"(?:25[0-5]|2[0-4]\d|1?\d?\d)"  # 0–255, rejects 256+ / 3-digit junk
_TAILNET_IP_RE = re.compile(rf"\b100\.{_OCTET}\.{_OCTET}\.{_OCTET}\b")
_WIN_USER_RE = re.compile(r"([A-Za-z]:\\Users\\)([^\\]+)")
_NIX_USER_RE = re.compile(r"(/(?:home|Users)/)([^/]+)")


def _scrub_str(value: str) -> str:
    value = _TAILNET_IP_RE.sub("<REDACTED-IP>", value)
    value = _WIN_USER_RE.sub(r"\1<user>", value)
    value = _NIX_USER_RE.sub(r"\1<user>", value)
    return value


def scrub_topology(data: Any) -> Any:
    """Recursively redact device/infra topology from a snapshot dict.

    Redacts tailnet IPs (``100.x.x.x``) and the username segment of Windows
    (``C:\\Users\\<user>``) and POSIX (``/home/<user>``, ``/Users/<user>``)
    home paths, in every string reachable through nested dicts/lists. Returns a
    new structure — the input is left untouched. A snapshot with no topology
    round-trips byte-identical.

    Applied at fixture-write time, never inside :meth:`RepoSnapshot.to_dict`
    (serialization and redaction are separate concerns — a live audit run may
    want the unscrubbed dict in memory).
    """
    if isinstance(data, str):
        return _scrub_str(data)
    if isinstance(data, dict):
        # Recurse on keys as well as values — topology can hide in a dict key
        # (e.g. a path used as a map key). Non-string keys pass through the
        # ``return data`` tail untouched.
        return {scrub_topology(k): scrub_topology(v) for k, v in data.items()}
    if isinstance(data, list):
        return [scrub_topology(v) for v in data]
    return data


# ── Live gh/REST runner ──────────────────────────────────────────────


def _parse_concatenated_json(text: str) -> list[Any]:
    """Parse a stream of whitespace-separated JSON values into a list.

    ``gh api --paginate`` emits one JSON document per page with no separator,
    so a multi-page array response is ``[...][...]`` — not a single valid JSON
    document. A streaming ``raw_decode`` loop handles both the single-page and
    multi-page cases without depending on a particular ``gh`` version's
    ``--slurp`` behavior.
    """
    decoder = json.JSONDecoder()
    values: list[Any] = []
    idx, n = 0, len(text)
    while idx < n:
        while idx < n and text[idx].isspace():
            idx += 1
        if idx >= n:
            break
        value, end = decoder.raw_decode(text, idx)
        values.append(value)
        idx = end
    return values


def gh_runner(path: str, *, paginate: bool = False) -> Any:
    """Live runner — shells out to ``gh api`` and returns parsed JSON.

    A 404 is mapped to :class:`GhNotFound` so the Auditor can treat an absent
    branch protection / dependabot.yml as "feature off". Any other non-zero
    exit raises :class:`RuntimeError` with the captured stderr (never the
    response body, to avoid leaking anything sensitive into logs).

    With ``paginate=True`` the per-page JSON arrays are merged into one flat
    list (see :func:`_parse_concatenated_json`).
    """
    args = ["gh", "api", path]
    if paginate:
        args.append("--paginate")
    # A bounded timeout so a hung gh process (network stall, auth prompt waiting
    # on a tty that will never arrive) fails loudly instead of blocking the whole
    # audit_all batch indefinitely.
    #
    # ``encoding="utf-8"`` is load-bearing, not tidiness: GitHub's API is always
    # UTF-8, but bare ``text=True`` decodes with the *locale* codepage. On a
    # Russian-locale Windows box that is cp1251, which silently mojibakes every
    # non-ASCII label description ("Блокировано" -> "Р—Р°Р±Р»РѕРєРёСЂРѕРІР°РЅРѕ")
    # instead of failing. Two consequences, both bad: corrupted text lands in the
    # committed fixtures, and a Linux CI re-audit (UTF-8 locale) decodes the same
    # bytes correctly, so ``--check`` reports permanent phantom drift on the
    # labels axis. The bug was live from slice 1 and had *already* corrupted the
    # committed baseline snapshot carried five
    # mojibaked em-dashes ("вЂ”") in its own label descriptions. It went unnoticed
    # because near-ASCII labels hide it: a lone punctuation mark expanding
    # to three bytes reads as noise, not as breakage. Only the first pass over a
    # repo with genuinely non-ASCII labels (#940) made
    # it unmissable.
    try:
        proc = subprocess.run(args, capture_output=True, text=True, encoding="utf-8", timeout=60)
    except subprocess.TimeoutExpired as e:
        raise RuntimeError(f"gh api {path!r} timed out after 60s") from e
    if proc.returncode != 0:
        stderr = (proc.stderr or "").strip()
        # Match gh's actual not-found marker, not a bare "404" digit run — an
        # unrelated error that merely references the number 404 (a path, a count,
        # another HTTP status) must not be miscoded as a missing resource.
        if "HTTP 404" in stderr or "Not Found" in stderr:
            raise GhNotFound(path)
        raise RuntimeError(f"gh api {path!r} failed (exit {proc.returncode}): {stderr}")

    if not paginate:
        # An exit-0 call with an empty body is anomalous — these endpoints always
        # return a JSON object. ``json.loads('')`` would raise an opaque
        # JSONDecodeError deep in a caller; raise a clear error naming the path
        # instead (mirrors the paginate-path empty-body guard below).
        if not proc.stdout.strip():
            raise RuntimeError(f"gh api {path!r} returned an empty response body (exit 0)")
        return json.loads(proc.stdout)

    values = _parse_concatenated_json(proc.stdout)
    # An empty body (a repo with zero labels paginates to ``""``) yields no
    # values — the natural result is an empty list, not the "unexpected page
    # structure" error the all-arrays/single-doc checks below would raise on it.
    if not values:
        return []
    if all(isinstance(v, list) for v in values):
        flat: list[Any] = []
        for page in values:
            flat.extend(page)
        return flat
    if len(values) == 1:
        return values[0]
    # A multi-value stream that is neither all-arrays (paginated list) nor a
    # single document is corrupt — e.g. an array page followed by a stray object.
    # Returning it raw would fail opaquely deep in a caller; raise here instead.
    raise RuntimeError(
        f"gh api {path!r} --paginate: unexpected page structure "
        f"{[type(v).__name__ for v in values]}"
    )
