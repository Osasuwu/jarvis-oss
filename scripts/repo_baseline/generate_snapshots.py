"""Generate committed audit snapshots + seeded manifests for the baseline repos.

Runs the live :class:`~scripts.repo_baseline.auditor.Auditor` over every repo in
every account pass (:data:`ACCOUNT_PASSES`) and writes, per repo:

* ``snapshots/<owner>__<name>.snapshot.json`` — the full :class:`RepoSnapshot`,
  scrubbed of device/infra topology (the repo is PUBLIC). These are the canonical
  fixtures the downstream pure-core (#939 applier) tests run against.
* ``manifests/<owner>__<name>.manifest.yml`` — the :func:`seed_manifest` skeleton,
  a populated starting point the owner edits rather than authoring from scratch.

Re-runnable: re-auditing is the whole point of a *re-syncable* baseline. Output is
deterministic (sorted-key JSON *and* sorted-key YAML) so a no-op re-audit produces
no diff regardless of the order keys are emitted in the source.

Scope is keyed by **account pass** (:data:`ACCOUNT_PASSES`), the orchestration
unit: one pass per owner listed in ``config/repos.conf``. Auditing is
read-only, so the default covers *both* — an account silently missing from the
default scope is exactly how a second-owner repo can stay absent from the
fixture set while downstream slices are built against it.

Usage::

    # Generate (or re-generate) committed fixtures for every account
    python -m scripts.repo_baseline.generate_snapshots

    # One account pass only
    python -m scripts.repo_baseline.generate_snapshots --account redrobot

    # Check for drift without writing
    python -m scripts.repo_baseline.generate_snapshots --check
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import yaml

from .auditor import (
    ACCOUNT_PASSES,
    Auditor,
    GhRunner,
    RepoSnapshot,
    gh_runner,
    scrub_topology,
    seed_manifest,
)

_MODULE_DIR = Path(__file__).resolve().parent
SNAPSHOTS_DIR = _MODULE_DIR / "snapshots"
MANIFESTS_DIR = _MODULE_DIR / "manifests"


def resolve_account(account: str) -> list[str]:
    """Map an ``--account`` value to its repo list. ``all`` = every pass.

    Raises :class:`KeyError` on an unknown account so a typo fails loudly
    instead of silently auditing nothing (an empty scope reports "0 repos
    match their snapshots" — a false clean).
    """
    if account == "all":
        return [repo for repos in ACCOUNT_PASSES.values() for repo in repos]
    if account not in ACCOUNT_PASSES:
        raise KeyError(f"unknown account pass {account!r}; known: {sorted(ACCOUNT_PASSES)}, all")
    return list(ACCOUNT_PASSES[account])


def _slug(repo: str) -> str:
    """``your-username/jarvis`` -> ``your-username__jarvis`` (filesystem-safe, owner-keyed)."""
    return repo.replace("/", "__")


def generate(
    repos: list[str],
    runner: GhRunner = gh_runner,
    *,
    snapshots_dir: Path | None = None,
    manifests_dir: Path | None = None,
) -> list[str]:
    """Audit each repo and write its snapshot + seeded manifest. Returns paths.

    ``snapshots_dir`` / ``manifests_dir`` default to the package's committed
    fixture dirs; tests pass a ``tmp_path`` so a generation run never touches
    the real fixtures.

    Like :meth:`Auditor.audit_all`, this does **not** fail fast: a single repo's
    audit failure must not discard the files already written for its siblings.
    Every repo is attempted; if any failed, a single :class:`RuntimeError` naming
    all failures is raised at the end. Files for the repos that succeeded remain
    on disk — a partial regen is recoverable, a lost batch is not.
    """
    snapshots_dir = snapshots_dir if snapshots_dir is not None else SNAPSHOTS_DIR
    manifests_dir = manifests_dir if manifests_dir is not None else MANIFESTS_DIR
    snapshots_dir.mkdir(parents=True, exist_ok=True)
    manifests_dir.mkdir(parents=True, exist_ok=True)

    auditor = Auditor(runner)
    written: list[str] = []
    errors: dict[str, Exception] = {}
    # NOTE: this loop deliberately does NOT delegate to ``Auditor.audit_all``.
    # ``audit_all`` collects every snapshot in memory and only raises at the end —
    # it never writes anything. Here the write *is* the per-repo work, and the
    # isolation property we need is write-as-you-go: a sibling's mid-run failure
    # must leave the already-written files on disk. Calling ``audit_all`` and
    # writing afterwards would discard the whole batch if any single repo failed,
    # regressing that property. The ``generate:`` error prefix is intentionally
    # distinct from ``audit_all:`` so a failure is traceable to this writer path.
    for repo in repos:
        try:
            snapshot = auditor.audit(repo)
            slug = _slug(repo)

            snap_path = snapshots_dir / f"{slug}.snapshot.json"
            scrubbed = scrub_topology(snapshot.to_dict())
            snap_path.write_text(
                json.dumps(scrubbed, sort_keys=True, indent=2) + "\n", encoding="utf-8"
            )
            written.append(str(snap_path))

            manifest_path = manifests_dir / f"{slug}.manifest.yml"
            seed = scrub_topology(seed_manifest(snapshot))
            manifest_path.write_text(
                yaml.safe_dump(seed, sort_keys=True, default_flow_style=False),
                encoding="utf-8",
            )
            written.append(str(manifest_path))
        except Exception as e:  # noqa: BLE001 — collect, don't fail-fast
            errors[repo] = e

    if errors:
        detail = "; ".join(f"{r} ({type(e).__name__}: {e})" for r, e in errors.items())
        raise RuntimeError(f"generate: {len(errors)} of {len(repos)} repo(s) failed — {detail}")
    return written


def check(
    repos: list[str],
    runner: GhRunner = gh_runner,
    *,
    snapshots_dir: Path | None = None,
) -> list[str]:
    """Re-audit each repo and diff against its committed snapshot.

    Returns a list of drift reports (one per drifted repo). Each report names
    the repo and the differing top-level axes. An empty list means every repo
    matches its committed snapshot — clean.

    Does **not** fail fast: every repo is audited, even if one has already
    drifted. Never writes anything — pure read-only check.
    """
    snapshots_dir = snapshots_dir if snapshots_dir is not None else SNAPSHOTS_DIR

    auditor = Auditor(runner)
    drifts: list[str] = []

    for repo in repos:
        try:
            committed = RepoSnapshot.from_dict(
                json.loads(
                    (snapshots_dir / f"{_slug(repo)}.snapshot.json").read_text(encoding="utf-8")
                )
            )
            fresh_scrubbed = scrub_topology(auditor.audit(repo).to_dict())
            committed_dict = scrub_topology(committed.to_dict())

            diff_axes = _diff_snapshot_dicts(fresh_scrubbed, committed_dict)
            if diff_axes:
                drifts.append(f"{repo}: {', '.join(sorted(diff_axes))}")
        except FileNotFoundError as e:
            drifts.append(f"{repo}: no committed snapshot found ({e})")
        except Exception as e:  # noqa: BLE001 — collect, don't fail-fast
            drifts.append(f"{repo}: check error ({type(e).__name__}: {e})")

    return drifts


def _diff_snapshot_dicts(fresh: dict, committed: dict) -> list[str]:
    """Return top-level axis names that differ between two snapshot dicts.
    Keys present in only one dict are reported as drifts."""
    differing: list[str] = []
    all_keys = set(fresh) | set(committed)
    for key in sorted(all_keys):
        if fresh.get(key) != committed.get(key):
            differing.append(key)
    return differing


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate or check repo baseline snapshots.")
    parser.add_argument(
        "--check",
        action="store_true",
        help="Re-audit and diff against committed snapshots. Exit non-zero on drift.",
    )
    parser.add_argument(
        "--account",
        default="all",
        choices=[*sorted(ACCOUNT_PASSES), "all"],
        help="Which account pass to audit (default: all).",
    )
    args = parser.parse_args()

    repos = resolve_account(args.account)

    if args.check:
        drifts = check(repos)
        if drifts:
            print(f"Drift detected in {len(drifts)} of {len(repos)} repo(s):")
            for d in drifts:
                print(f"  {d}")
            sys.exit(1)
        print(f"All {len(repos)} repo(s) match their committed snapshots.")
        return

    written = generate(repos)
    print(f"Wrote {len(written)} files for {len(repos)} repos:")
    for path in written:
        print(f"  {path}")


if __name__ == "__main__":
    main()
