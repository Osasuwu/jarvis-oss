"""Tests for ``generate_snapshots`` — the committed-fixture writer (slice 1).

The generator audits each repo and writes two artifacts per repo: a
``<slug>.snapshot.json`` (the full :class:`RepoSnapshot`, scrubbed) and a
``<slug>.manifest.yml`` (the :func:`seed_manifest` skeleton). These are the
canonical fixtures the downstream #939 applier runs against, so the writer's
contract is load-bearing:

* **Determinism** — a no-op re-audit must produce byte-identical files (#978
  MAJOR 6: ``sort_keys=True`` on *both* JSON and YAML).
* **Per-repo isolation** — one repo's audit failure must not discard the files
  already written for its siblings (#978 MAJOR 1, generate side).
* **Round-trip** — the emitted manifest YAML must load back through
  :meth:`Manifest.from_dict` without massaging.

Reuses ``FakeRunner`` + ``_jarvis_responses`` from ``tests/conftest.py`` (the
shared-test-infra home — importable as ``from conftest import ...``) so the
canned ``gh api`` JSON stays defined in one place (#980).
"""

from __future__ import annotations

import json

import pytest
import yaml

from scripts.repo_baseline import Manifest
from scripts.repo_baseline import generate_snapshots as gen

from conftest import FakeRunner, _jarvis_responses


def _generate_jarvis(tmp_path, *, repos=("Osasuwu/jarvis",)):
    """Run ``generate`` for the given repos into ``tmp_path`` subdirs.

    Returns ``(written, snapshots_dir, manifests_dir)``. A fresh FakeRunner is
    built per call so the recorded ``calls`` log never bleeds across runs.
    """
    snaps = tmp_path / "snapshots"
    mans = tmp_path / "manifests"
    written = gen.generate(
        list(repos),
        runner=FakeRunner(_jarvis_responses()),
        snapshots_dir=snaps,
        manifests_dir=mans,
    )
    return written, snaps, mans


class TestGenerateWritesArtifacts:
    """Each repo yields a snapshot JSON + manifest YAML at the slug path."""

    def test_writes_snapshot_and_manifest_per_repo(self, tmp_path):
        written, snaps, mans = _generate_jarvis(tmp_path)

        snap_path = snaps / "Osasuwu__jarvis.snapshot.json"
        man_path = mans / "Osasuwu__jarvis.manifest.yml"
        assert snap_path.exists()
        assert man_path.exists()

        # Return value lists both written paths (the run's manifest of work).
        assert str(snap_path) in written
        assert str(man_path) in written
        assert len(written) == 2

    def test_snapshot_json_is_sorted_and_parses(self, tmp_path):
        _, snaps, _ = _generate_jarvis(tmp_path)
        text = (snaps / "Osasuwu__jarvis.snapshot.json").read_text(encoding="utf-8")

        data = json.loads(text)
        assert data["repo"] == "Osasuwu/jarvis"

        # sort_keys=True: top-level keys appear alphabetically in the raw text.
        top_keys = list(data.keys())
        assert top_keys == sorted(top_keys)

    def test_creates_output_dirs_if_absent(self, tmp_path):
        # Neither subdir exists before the call; generate must mkdir(parents).
        nested = tmp_path / "deep" / "nesting"
        written = gen.generate(
            ["Osasuwu/jarvis"],
            runner=FakeRunner(_jarvis_responses()),
            snapshots_dir=nested / "snaps",
            manifests_dir=nested / "mans",
        )
        assert len(written) == 2
        assert (nested / "snaps" / "Osasuwu__jarvis.snapshot.json").exists()
        assert (nested / "mans" / "Osasuwu__jarvis.manifest.yml").exists()


class TestManifestRoundTrips:
    """The emitted YAML loads back through Manifest.from_dict unchanged."""

    def test_manifest_yaml_loads_via_from_dict(self, tmp_path):
        _, _, mans = _generate_jarvis(tmp_path)
        loaded = yaml.safe_load((mans / "Osasuwu__jarvis.manifest.yml").read_text(encoding="utf-8"))

        manifest = Manifest.from_dict(loaded)
        assert manifest.repo == "Osasuwu/jarvis"
        # jarvis is fully baselined → 'full' profile, auto_merge on, bp present.
        assert manifest.profile == "full"
        assert manifest.auto_merge is True
        assert manifest.branch_protection is True
        assert manifest.required_check_contexts == ["review", "pytest"]

    def test_manifest_yaml_keys_are_sorted(self, tmp_path):
        _, _, mans = _generate_jarvis(tmp_path)
        text = (mans / "Osasuwu__jarvis.manifest.yml").read_text(encoding="utf-8")

        # Top-level mapping keys (block sequence items start with '- ', nested
        # values are indented) must be emitted alphabetically — sort_keys=True.
        top_keys = [
            line.split(":", 1)[0]
            for line in text.splitlines()
            if line and not line.startswith((" ", "-"))
        ]
        assert top_keys == sorted(top_keys)


class TestDeterminism:
    """A no-op re-audit produces byte-identical files (#978 MAJOR 6)."""

    def test_two_runs_are_byte_identical(self, tmp_path):
        _, snaps_a, mans_a = _generate_jarvis(tmp_path / "a")
        _, snaps_b, mans_b = _generate_jarvis(tmp_path / "b")

        for sub, name in (
            ("snapshots", "Osasuwu__jarvis.snapshot.json"),
            ("manifests", "Osasuwu__jarvis.manifest.yml"),
        ):
            a = (tmp_path / "a" / sub / name).read_bytes()
            b = (tmp_path / "b" / sub / name).read_bytes()
            assert a == b, f"{sub}/{name} differs across runs — non-deterministic"


class TestPerRepoIsolation:
    """One repo's failure must not discard a sibling's already-written files."""

    def test_sibling_failure_keeps_good_files_and_raises_summary(self, tmp_path):
        # jarvis is registered; 'ghost' has no canned responses → KeyError in
        # its audit. generate must still write jarvis's files, then raise a
        # single summary naming the failure (no fail-fast).
        snaps = tmp_path / "snapshots"
        mans = tmp_path / "manifests"
        with pytest.raises(RuntimeError) as excinfo:
            gen.generate(
                ["Osasuwu/jarvis", "Osasuwu/ghost"],
                runner=FakeRunner(_jarvis_responses()),
                snapshots_dir=snaps,
                manifests_dir=mans,
            )

        msg = str(excinfo.value)
        assert "1 of 2 repo(s) failed" in msg
        assert "Osasuwu/ghost" in msg

        # The good repo's artifacts survive the sibling's failure.
        assert (snaps / "Osasuwu__jarvis.snapshot.json").exists()
        assert (mans / "Osasuwu__jarvis.manifest.yml").exists()
        # The failed repo wrote nothing.
        assert not (snaps / "Osasuwu__ghost.snapshot.json").exists()


class TestCheckMode:
    """AC1 — generate_snapshots --check re-audits and diffs without writing."""

    def test_clean_match_returns_empty_drifts(self, tmp_path):
        """When committed snapshot matches a fresh audit, check returns []."""
        # First, write a snapshot via generate.
        gen.generate(
            ["Osasuwu/jarvis"],
            runner=FakeRunner(_jarvis_responses()),
            snapshots_dir=tmp_path / "snaps",
            manifests_dir=tmp_path / "mans",
        )
        drifts = gen.check(
            ["Osasuwu/jarvis"],
            runner=FakeRunner(_jarvis_responses()),
            snapshots_dir=tmp_path / "snaps",
        )
        assert drifts == []

    def test_single_axis_drift_detected(self, tmp_path):
        """When a setting changes, check detects the drift and names the axis."""
        gen.generate(
            ["Osasuwu/jarvis"],
            runner=FakeRunner(_jarvis_responses()),
            snapshots_dir=tmp_path / "snaps",
            manifests_dir=tmp_path / "mans",
        )
        # Modify the committed snapshot on disk — change visibility.
        snap_path = tmp_path / "snaps" / "Osasuwu__jarvis.snapshot.json"
        data = json.loads(snap_path.read_text(encoding="utf-8"))
        data["settings"]["visibility"] = "private"
        snap_path.write_text(json.dumps(data, sort_keys=True, indent=2) + "\n", encoding="utf-8")

        drifts = gen.check(
            ["Osasuwu/jarvis"],
            runner=FakeRunner(_jarvis_responses()),
            snapshots_dir=tmp_path / "snaps",
        )
        assert len(drifts) == 1
        assert "Osasuwu/jarvis" in drifts[0]
        assert "settings" in drifts[0]

    def test_missing_snapshot_file_reported(self, tmp_path):
        """If no committed snapshot exists, check lists it as a missing-snapshot
        drift rather than crashing."""
        drifts = gen.check(
            ["Osasuwu/jarvis"],
            runner=FakeRunner(_jarvis_responses()),
            snapshots_dir=tmp_path / "snaps",
        )
        assert len(drifts) == 1
        assert "Osasuwu/jarvis" in drifts[0]
        assert "no committed snapshot" in drifts[0]


class TestAccountPasses:
    """``--account`` scoping (#940 — the second, cross-owner account pass).

    The generator is keyed by *account pass* rather than one flat repo list
    because a pass is the credential unit: one ``gh`` token per owner. The
    property that matters is that no account can silently drop out of the
    default scope — that is precisely how a second-owner repo can stay
    un-snapshotted while downstream slices are built assuming its fixtures
    exist.
    """

    def test_default_scope_covers_every_account(self):
        every = {repo for repos in gen.ACCOUNT_PASSES.values() for repo in repos}
        assert set(gen.resolve_account("all")) == every

    def test_each_pass_groups_a_single_owner(self):
        """The reason passes exist at all: one owner per pass, hence one
        credential domain. A repo under a different owner appearing in a
        pass means the split has been flattened — this catches it."""
        for acct, repos in gen.ACCOUNT_PASSES.items():
            assert repos, f"empty account pass {acct!r}"
            for slug in repos:
                assert slug.split("/", 1)[0].lower() == acct

    def test_single_account_scopes_to_that_pass(self):
        for acct in gen.ACCOUNT_PASSES:
            assert gen.resolve_account(acct) == list(gen.ACCOUNT_PASSES[acct])

    def test_resolve_account_returns_a_copy(self):
        """Mutating the returned list must not corrupt the module-level pass —
        ``generate`` and ``check`` both take the list by reference."""
        acct = next(iter(gen.ACCOUNT_PASSES))
        got = gen.resolve_account(acct)
        got.append("someone/should-not-leak")
        assert "someone/should-not-leak" not in gen.ACCOUNT_PASSES[acct]

    def test_unknown_account_raises_rather_than_auditing_nothing(self):
        """An empty scope would report a false clean ('all 0 repos match')."""
        with pytest.raises(KeyError, match="unknown account pass"):
            gen.resolve_account("no-such-account")

    def test_every_account_pass_has_a_committed_snapshot(self):
        """The end-state assertion for #940: each repo in each pass has its
        fixtures on disk. Guards against adding a repo to a pass and forgetting
        to run the generator."""
        missing = [
            repo
            for repo in gen.resolve_account("all")
            if not (gen.SNAPSHOTS_DIR / f"{gen._slug(repo)}.snapshot.json").exists()
            or not (gen.MANIFESTS_DIR / f"{gen._slug(repo)}.manifest.yml").exists()
        ]
        assert missing == [], f"account-pass repos without committed fixtures: {missing}"
