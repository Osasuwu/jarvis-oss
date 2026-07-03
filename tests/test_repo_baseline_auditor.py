"""Tests for the Auditor shell — empirical 6-repo audit + manifest seed (slice 1).

The Auditor is a thin gh/REST shell with an *injectable runner* so the parsing
logic is exercised against canned ``gh api`` JSON with zero live calls. Each
test class maps to one acceptance-criterion bullet of issue #934.
"""

from __future__ import annotations

import base64
import json

import pytest

from scripts.repo_baseline import Manifest
from scripts.repo_baseline import auditor as auditor_mod
from scripts.repo_baseline.auditor import (
    BASELINE_REPOS,
    Auditor,
    BranchProtection,
    GhNotFound,
    RepoSettings,
    RepoSnapshot,
    gh_runner,
    scrub_topology,
    seed_manifest,
)

from conftest import FakeRunner, _FakeProc, _dependabot_b64, _jarvis_responses


class TestRepoSnapshotParsing:
    """AC1 — Auditor reads labels/workflows/settings/protection/dependabot."""

    def test_audit_builds_full_snapshot(self):
        runner = FakeRunner(_jarvis_responses())
        auditor = Auditor(runner)
        snap = auditor.audit("Osasuwu/jarvis")

        assert isinstance(snap, RepoSnapshot)
        assert snap.repo == "Osasuwu/jarvis"

        # Pagination contract (#978 MAJOR 4): labels is the only paginated
        # endpoint; every other reader must call without --paginate. FakeRunner
        # silently accepting an unverified kwarg let a dropped paginate=True slip.
        assert runner.paginate_for("repos/Osasuwu/jarvis/labels") is True
        for path in (
            "repos/Osasuwu/jarvis",
            "repos/Osasuwu/jarvis/actions/workflows?per_page=100",
            "repos/Osasuwu/jarvis/branches/main/protection",
            "repos/Osasuwu/jarvis/contents/.github/dependabot.yml",
        ):
            assert runner.paginate_for(path) is False

        # repo settings
        assert snap.settings.allow_auto_merge is True
        assert snap.settings.allow_squash_merge is True
        assert snap.settings.allow_merge_commit is False
        assert snap.settings.delete_branch_on_merge is True
        assert snap.settings.visibility == "public"
        assert snap.settings.default_branch == "main"

        # labels — name + color + description
        assert [lb.name for lb in snap.labels] == [
            "priority:critical",
            "status:in-progress",
        ]
        assert snap.labels[0].color == "b60205"
        assert snap.labels[0].description == "Hotfix"

        # workflow filenames
        assert snap.workflows == [
            ".github/workflows/code-review.yml",
            ".github/workflows/pytest.yml",
        ]

        # branch protection
        assert isinstance(snap.branch_protection, BranchProtection)
        assert snap.branch_protection.strict is True
        assert snap.branch_protection.contexts == ["review", "pytest"]

        # contexts_source: "review" has no matching local workflow (app check),
        # "pytest" matches the workflow named "pytest" at its known path.
        assert snap.branch_protection.contexts_source == [
            None,
            ".github/workflows/pytest.yml",
        ]

        # dependabot ecosystems
        assert snap.dependabot_ecosystems == ["pip", "github-actions"]

    def test_workflows_reader_uses_large_page_size(self):
        """The workflows endpoint returns a ``{total_count, workflows}`` envelope,
        not a bare array — the runner's array-paginate path cannot merge it, so
        ``--paginate`` is unusable here. Bumping ``per_page`` to 100 fetches every
        workflow in a single page instead of silently truncating at the 30-item
        API default. (#978 round-3 MINOR 3.)"""
        runner = FakeRunner(
            {
                "repos/Osasuwu/x/actions/workflows?per_page=100": {
                    "workflows": [{"name": "Workflow A", "path": ".github/workflows/a.yml"}]
                }
            }
        )
        auditor = Auditor(runner)
        paths, name_map = auditor._read_workflows("Osasuwu/x")
        assert paths == [".github/workflows/a.yml"]
        assert name_map == {"Workflow A": ".github/workflows/a.yml"}
        # Single, non-paginated request that carries the larger page size.
        assert runner.paginate_for("repos/Osasuwu/x/actions/workflows?per_page=100") is False

    def test_bare_repo_absent_protection_and_dependabot(self):
        """A repo with no branch protection / no dependabot.yml audits cleanly:
        404 on those paths is 'feature off', not an error."""
        responses = {
            "repos/Osasuwu/beta": {
                "allow_auto_merge": False,
                "allow_squash_merge": True,
                "allow_merge_commit": True,
                "allow_rebase_merge": True,
                "delete_branch_on_merge": False,
                "visibility": "public",
                "default_branch": "main",
            },
            "repos/Osasuwu/beta/labels": [],
            "repos/Osasuwu/beta/actions/workflows?per_page=100": {"workflows": []},
        }
        not_found = {
            "repos/Osasuwu/beta/branches/main/protection",
            "repos/Osasuwu/beta/contents/.github/dependabot.yml",
        }
        auditor = Auditor(FakeRunner(responses, not_found))
        snap = auditor.audit("Osasuwu/beta")

        assert snap.branch_protection is None
        assert snap.dependabot_ecosystems == []
        assert snap.labels == []
        assert snap.workflows == []
        assert snap.settings.allow_auto_merge is False

    def test_branch_protection_reads_modern_checks_field(self):
        """GitHub deprecated required_status_checks.contexts in favour of
        .checks ([{context, app_id}]). A repo configured after that migration
        can have contexts=[] while checks holds the real names — reading only
        contexts would report an apparently-protected repo with zero required
        checks. Fall back to .checks when contexts is empty. (#978 MAJOR 1.)"""
        responses = dict(_jarvis_responses())
        responses["repos/Osasuwu/jarvis/branches/main/protection"] = {
            "required_status_checks": {
                "strict": True,
                "contexts": [],
                "checks": [
                    {"context": "review", "app_id": 1},
                    {"context": "pytest", "app_id": 2},
                ],
            }
        }
        snap = Auditor(FakeRunner(responses)).audit("Osasuwu/jarvis")
        assert snap.branch_protection.strict is True
        assert snap.branch_protection.contexts == ["review", "pytest"]

    def test_branch_protection_prefers_contexts_over_checks_when_both_present(self):
        """A repo mid-migration can report BOTH the deprecated ``contexts`` and
        the modern ``checks`` simultaneously. ``contexts`` is the source of
        truth (the modern ``checks`` may lag); the fallback to ``checks`` only
        fires when ``contexts`` is empty. Pin the precedence. (#978 MAJOR 5.)"""
        responses = dict(_jarvis_responses())
        responses["repos/Osasuwu/jarvis/branches/main/protection"] = {
            "required_status_checks": {
                "strict": True,
                "contexts": ["ctx-a"],
                "checks": [{"context": "check-b", "app_id": 1}],
            }
        }
        snap = Auditor(FakeRunner(responses)).audit("Osasuwu/jarvis")
        assert snap.branch_protection.contexts == ["ctx-a"]

    def test_dependabot_unexpected_encoding_raises(self):
        """The Contents API sets encoding='none' with empty content for files
        over ~1 MB. Silently base64-decoding '' yields [] — misreporting a repo
        that HAS dependabot as having none. A present-but-unreadable file is an
        audit failure, not 'feature off' — fail loud. (#978 MINOR 6.)"""
        responses = dict(_jarvis_responses())
        responses["repos/Osasuwu/jarvis/contents/.github/dependabot.yml"] = {
            "content": "",
            "encoding": "none",
        }
        with pytest.raises(RuntimeError, match="encoding"):
            Auditor(FakeRunner(responses)).audit("Osasuwu/jarvis")

    def test_dependabot_malformed_yaml_raises_with_repo_context(self):
        """A present-but-malformed dependabot.yml (valid base64, invalid YAML)
        must fail loudly with the repo name in the message — not propagate a
        bare yaml.YAMLError with no indication of which of 5 repos broke.
        (#978 MINOR — audit-boundary error context.)"""
        responses = dict(_jarvis_responses())
        bad = base64.b64encode(b"version: 2\nupdates: [unclosed\n").decode()
        responses["repos/Osasuwu/jarvis/contents/.github/dependabot.yml"] = {
            "content": bad,
            "encoding": "base64",
        }
        with pytest.raises(RuntimeError, match=r"Osasuwu/jarvis"):
            Auditor(FakeRunner(responses)).audit("Osasuwu/jarvis")

    def test_audit_wraps_unexpected_error_with_repo_name(self):
        """Any unexpected failure inside a reader (here: a missing settings
        response → KeyError) is wrapped at the audit() boundary with the repo
        name, so a multi-repo batch can attribute the failure. (#978 MINOR.)"""
        # No response registered for the settings path → FakeRunner KeyError.
        runner = FakeRunner({}, not_found=set())
        with pytest.raises(RuntimeError, match=r"Audit failed for 'Osasuwu/ghost'"):
            Auditor(runner).audit("Osasuwu/ghost")

    def test_repo_settings_merge_method_defaults_match_github(self):
        """GitHub's real defaults for squash/merge-commit/rebase are all True;
        auto-merge and delete-branch default False. A test double (or any caller)
        that omits these fields must inherit GitHub's actual defaults, not a
        blanket False that misreports the repo. (#978 MINOR 7.)"""
        s = RepoSettings()
        assert s.allow_squash_merge is True
        assert s.allow_merge_commit is True
        assert s.allow_rebase_merge is True
        assert s.allow_auto_merge is False
        assert s.delete_branch_on_merge is False

        # _read_settings inherits the same defaults when the API omits a field.
        responses = {
            "repos/Osasuwu/x": {"visibility": "public", "default_branch": "main"},
            "repos/Osasuwu/x/labels": [],
            "repos/Osasuwu/x/actions/workflows?per_page=100": {"workflows": []},
        }
        not_found = {
            "repos/Osasuwu/x/branches/main/protection",
            "repos/Osasuwu/x/contents/.github/dependabot.yml",
        }
        snap = Auditor(FakeRunner(responses, not_found)).audit("Osasuwu/x")
        assert snap.settings.allow_squash_merge is True
        assert snap.settings.allow_merge_commit is True
        assert snap.settings.allow_rebase_merge is True


class TestSnapshotSerialization:
    """AC3 — structured JSON snapshot artifact, round-trippable + deterministic."""

    def test_to_dict_from_dict_round_trip(self):
        auditor = Auditor(FakeRunner(_jarvis_responses()))
        snap = auditor.audit("Osasuwu/jarvis")

        restored = RepoSnapshot.from_dict(snap.to_dict())
        assert restored == snap

    def test_to_dict_is_json_serialisable_and_deterministic(self):
        import json

        auditor = Auditor(FakeRunner(_jarvis_responses()))
        snap = auditor.audit("Osasuwu/jarvis")

        d = snap.to_dict()
        # Stable, sorted JSON — committed fixture must not churn on re-audit.
        s1 = json.dumps(d, sort_keys=True, indent=2)
        s2 = json.dumps(snap.to_dict(), sort_keys=True, indent=2)
        assert s1 == s2
        # Round-trips through a JSON string too.
        assert RepoSnapshot.from_dict(json.loads(s1)) == snap

    def test_from_dict_tolerates_extra_branch_protection_keys(self):
        """A snapshot written by a future auditor version may carry extra
        branch_protection keys. from_dict must filter to known fields, not
        blow up with a bare TypeError. (#978 MINOR — blind ** unpack.)"""
        data = {
            "repo": "Osasuwu/jarvis",
            "settings": {"visibility": "public", "default_branch": "main"},
            "labels": [],
            "workflows": [],
            "branch_protection": {
                "strict": True,
                "contexts": ["review"],
                "future_field": "ignored",  # not on the current dataclass
            },
            "dependabot_ecosystems": [],
        }
        snap = RepoSnapshot.from_dict(data)
        assert snap.branch_protection.strict is True
        assert snap.branch_protection.contexts == ["review"]

    def test_bare_repo_round_trip(self):
        """branch_protection=None must survive serialization."""
        responses = {
            "repos/Osasuwu/beta": {
                "visibility": "public",
                "default_branch": "main",
            },
            "repos/Osasuwu/beta/labels": [],
            "repos/Osasuwu/beta/actions/workflows?per_page=100": {"workflows": []},
        }
        not_found = {
            "repos/Osasuwu/beta/branches/main/protection",
            "repos/Osasuwu/beta/contents/.github/dependabot.yml",
        }
        snap = Auditor(FakeRunner(responses, not_found)).audit("Osasuwu/beta")
        assert RepoSnapshot.from_dict(snap.to_dict()) == snap
        assert snap.to_dict()["branch_protection"] is None

    def test_to_dict_nested_lists_are_independent_copies(self):
        """to_dict must deep-copy: mutating a nested list in the returned dict
        (e.g. branch_protection.contexts) must NOT corrupt the live snapshot.
        A shallow vars().copy() shares the list object. (#978 MINOR 5.)"""
        snap = Auditor(FakeRunner(_jarvis_responses())).audit("Osasuwu/jarvis")
        d = snap.to_dict()
        d["branch_protection"]["contexts"].append("INJECTED")
        d["dependabot_ecosystems"].append("INJECTED")
        d["workflows"].append("INJECTED")
        assert "INJECTED" not in snap.branch_protection.contexts
        assert "INJECTED" not in snap.dependabot_ecosystems
        assert "INJECTED" not in snap.workflows


class TestContextsSource:
    """AC2 — BranchProtection.contexts_source provenance."""

    def test_locally_produced_check_resolves_to_workflow_path(self):
        """A context matching a workflow name gets that workflow's path."""
        responses = dict(_jarvis_responses())
        snap = Auditor(FakeRunner(responses)).audit("Osasuwu/jarvis")
        assert snap.branch_protection.contexts_source == [
            None,  # "review" is an app check, no local workflow
            ".github/workflows/pytest.yml",  # "pytest" workflow matches
        ]

    def test_all_checks_mapped_or_null_per_context(self):
        """contexts_source must be same length as contexts — one entry per
        required check context, in the same order."""
        responses = dict(_jarvis_responses())
        responses["repos/Osasuwu/jarvis/actions/workflows?per_page=100"] = {
            "workflows": [
                {"name": "review", "path": ".github/workflows/code-review.yml"},
                {"name": "pytest", "path": ".github/workflows/pytest.yml"},
            ]
        }
        snap = Auditor(FakeRunner(responses)).audit("Osasuwu/jarvis")
        assert snap.branch_protection.contexts_source == [
            ".github/workflows/code-review.yml",
            ".github/workflows/pytest.yml",
        ]

    def test_no_branch_protection_yields_none_contexts_source(self):
        """A repo with no branch protection has bp=None → contexts_source=None."""
        responses = {
            "repos/Osasuwu/beta": {
                "allow_auto_merge": False,
                "allow_squash_merge": True,
                "allow_merge_commit": True,
                "allow_rebase_merge": True,
                "delete_branch_on_merge": False,
                "visibility": "public",
                "default_branch": "main",
            },
            "repos/Osasuwu/beta/labels": [],
            "repos/Osasuwu/beta/actions/workflows?per_page=100": {"workflows": []},
        }
        not_found = {
            "repos/Osasuwu/beta/branches/main/protection",
            "repos/Osasuwu/beta/contents/.github/dependabot.yml",
        }
        snap = Auditor(FakeRunner(responses, not_found)).audit("Osasuwu/beta")
        assert snap.branch_protection is None

    def test_contexts_source_round_trips_through_to_dict_from_dict(self):
        """contexts_source survives serialization."""
        snap = Auditor(FakeRunner(_jarvis_responses())).audit("Osasuwu/jarvis")
        restored = RepoSnapshot.from_dict(snap.to_dict())
        assert restored.branch_protection.contexts_source == snap.branch_protection.contexts_source

    def test_from_dict_tolerates_missing_contexts_source(self):
        """A legacy snapshot without contexts_source loads with it as None."""
        data = {
            "repo": "Osasuwu/jarvis",
            "settings": {"visibility": "public", "default_branch": "main"},
            "labels": [],
            "workflows": [],
            "branch_protection": {
                "strict": True,
                "contexts": ["review"],
            },
            "dependabot_ecosystems": [],
        }
        snap = RepoSnapshot.from_dict(data)
        assert snap.branch_protection.strict is True
        assert snap.branch_protection.contexts == ["review"]
        assert snap.branch_protection.contexts_source is None


class TestSeedManifest:
    """AC4 — derive a per-repo Manifest skeleton from a snapshot, populating
    the axis values from observed reality. Output must round-trip through
    Manifest.from_dict (no unknown keys)."""

    def test_seed_captures_observed_axes(self):
        snap = Auditor(FakeRunner(_jarvis_responses())).audit("Osasuwu/jarvis")
        seed = seed_manifest(snap)

        # The seed is a plain manifest dict that from_dict accepts unchanged.
        m = Manifest.from_dict(seed)
        assert m.repo == "Osasuwu/jarvis"
        assert m.resolve_axis("auto_merge") is True
        assert m.resolve_axis("branch_protection") is True
        assert m.resolve_axis("required_check_contexts") == ["review", "pytest"]
        assert m.resolve_axis("dependabot_ecosystems") == ["pip", "github-actions"]
        assert m.resolve_axis("visibility") == "public"

    def test_seed_bare_repo_captures_absences_explicitly(self):
        """A bare repo's absences (no protection, no dependabot, auto_merge off)
        must be captured as explicit values, NOT left to resolve to the full
        profile's defaults — otherwise the seed would misreport reality."""
        responses = {
            "repos/Osasuwu/beta": {
                "allow_auto_merge": False,
                "visibility": "public",
                "default_branch": "main",
            },
            "repos/Osasuwu/beta/labels": [],
            "repos/Osasuwu/beta/actions/workflows?per_page=100": {"workflows": []},
        }
        not_found = {
            "repos/Osasuwu/beta/branches/main/protection",
            "repos/Osasuwu/beta/contents/.github/dependabot.yml",
        }
        snap = Auditor(FakeRunner(responses, not_found)).audit("Osasuwu/beta")
        m = Manifest.from_dict(seed_manifest(snap))

        assert m.resolve_axis("auto_merge") is False
        assert m.resolve_axis("branch_protection") is False
        assert m.resolve_axis("required_check_contexts") == []
        # Crucially [] not the ["pip","github-actions"] profile default.
        assert m.resolve_axis("dependabot_ecosystems") == []

    def test_seed_private_repo_visibility(self):
        responses = dict(_jarvis_responses())
        responses["repos/Osasuwu/jarvis"] = {
            **responses["repos/Osasuwu/jarvis"],
            "visibility": "private",
        }
        snap = Auditor(FakeRunner(responses)).audit("Osasuwu/jarvis")
        m = Manifest.from_dict(seed_manifest(snap))
        assert m.resolve_axis("visibility") == "private"

    def test_seed_dedupes_dependabot_ecosystems(self):
        """A repo with multiple dependabot update blocks for the same ecosystem
        (e.g. pip for two directories) yields a duplicated ecosystem list on the
        snapshot. The manifest axis is a *set* of ecosystem types, so the seed
        must dedupe — preserving first-seen order — or the renderer would emit
        duplicate dependabot blocks. (Surfaced by the live audit of Osasuwu/jarvis,
        which has two pip blocks; unit fixtures used distinct ecosystems.)"""
        responses = dict(_jarvis_responses())
        responses["repos/Osasuwu/jarvis/contents/.github/dependabot.yml"] = _dependabot_b64(
            "pip", "pip", "github-actions"
        )
        snap = Auditor(FakeRunner(responses)).audit("Osasuwu/jarvis")
        assert snap.dependabot_ecosystems == ["pip", "pip", "github-actions"]

        seed = seed_manifest(snap)
        assert seed["dependabot_ecosystems"] == ["pip", "github-actions"]
        m = Manifest.from_dict(seed)
        assert m.resolve_axis("dependabot_ecosystems") == ["pip", "github-actions"]

    def test_seed_profile_full_for_baselined_repo(self):
        """A repo with auto-merge AND branch protection is observably baselined
        → profile 'full'. (#978 MAJOR 2.)"""
        snap = Auditor(FakeRunner(_jarvis_responses())).audit("Osasuwu/jarvis")
        assert seed_manifest(snap)["profile"] == "full"

    def test_seed_profile_minimal_for_bare_repo(self):
        """A bare repo (no auto-merge AND no branch protection) is observably
        un-baselined → profile 'minimal', not 'full'. Hardcoding 'full' would
        make it silently inherit the full profile's Python-shaped axes
        (ci_language, test_extras) that seed_manifest omits — the seed must
        reflect observed posture, not prescribe a target. (#978 MAJOR 2.)"""
        responses = {
            "repos/Osasuwu/beta": {
                "allow_auto_merge": False,
                "visibility": "public",
                "default_branch": "main",
            },
            "repos/Osasuwu/beta/labels": [],
            "repos/Osasuwu/beta/actions/workflows?per_page=100": {"workflows": []},
        }
        not_found = {
            "repos/Osasuwu/beta/branches/main/protection",
            "repos/Osasuwu/beta/contents/.github/dependabot.yml",
        }
        snap = Auditor(FakeRunner(responses, not_found)).audit("Osasuwu/beta")
        seed = seed_manifest(snap)
        assert seed["profile"] == "minimal"
        # Explicit observed axes still win over the minimal profile defaults.
        m = Manifest.from_dict(seed)
        assert m.resolve_axis("auto_merge") is False
        assert m.resolve_axis("branch_protection") is False

    def test_seed_profile_full_for_auto_merge_without_protection(self):
        """The 4th governance state — auto-merge ON, branch-protection NONE — is
        not bare (it has one governance signal), so it rounds up to 'full', not
        'minimal'. is_bare requires *both* signals absent. This state is
        unobserved in the milestone-#48 scope but the heuristic must classify it
        deterministically. (#978 round-3 NIT — 4th-state coverage.)"""
        responses = {
            "repos/Osasuwu/x": {
                "allow_auto_merge": True,
                "visibility": "public",
                "default_branch": "main",
            },
            "repos/Osasuwu/x/labels": [],
            "repos/Osasuwu/x/actions/workflows?per_page=100": {"workflows": []},
        }
        not_found = {
            "repos/Osasuwu/x/branches/main/protection",
            "repos/Osasuwu/x/contents/.github/dependabot.yml",
        }
        snap = Auditor(FakeRunner(responses, not_found)).audit("Osasuwu/x")
        seed = seed_manifest(snap)
        assert seed["profile"] == "full"
        # The partial state stays visible in the body despite the rounded-up label.
        assert seed["auto_merge"] is True
        assert seed["branch_protection"] is False


class TestScrubTopology:
    """AC5 — redact device/infra topology before a snapshot is committed to a
    PUBLIC repo. Applied at fixture-write time. Must catch ANY username, not a
    hardcoded one, and leave a clean structure byte-identical."""

    def test_redacts_tailnet_ip(self):
        out = scrub_topology({"x": "runner reachable at 100.83.12.7 ok"})
        assert "100.83.12.7" not in json.dumps(out)
        assert out["x"] == "runner reachable at <REDACTED-IP> ok"

    def test_redacts_windows_user_path_any_username(self):
        out = scrub_topology({"x": r"see C:\Users\someguy\GitHub\jarvis\foo"})
        assert "someguy" not in json.dumps(out)
        assert out["x"] == r"see C:\Users\<user>\GitHub\jarvis\foo"

    def test_redacts_unix_home_paths(self):
        out = scrub_topology({"a": "/home/alice/code", "b": "/Users/bob/x"})
        assert out["a"] == "/home/<user>/code"
        assert out["b"] == "/Users/<user>/x"

    def test_redacts_nested_dicts_and_lists(self):
        data = {
            "labels": [{"description": "self-hosted runner 100.1.2.3"}],
            "nested": {"path": r"C:\Users\joe\x"},
        }
        out = scrub_topology(data)
        assert out["labels"][0]["description"] == "self-hosted runner <REDACTED-IP>"
        assert out["nested"]["path"] == r"C:\Users\<user>\x"

    def test_redacts_topology_in_dict_keys_too(self):
        """scrub_topology must recurse into dict KEYS, not only values — a path
        used as a key (e.g. a file-path-keyed map) would otherwise leak a
        username. (#978 MINOR — scrub recurses on keys.)"""
        out = scrub_topology({r"C:\Users\joe\config": "v", "/home/amy/x": 1})
        assert r"C:\Users\<user>\config" in out
        assert "/home/<user>/x" in out
        assert "joe" not in json.dumps(out)
        assert "amy" not in json.dumps(out)

    def test_tailnet_regex_ignores_invalid_octets(self):
        """The tailnet IP pattern must accept only valid IPv4 octets (0-255),
        not \\d{1,3} which over-matches 'version'-like 100.300.400.500. A real
        100.x address is still redacted. (#978 MINOR — octet range.)"""
        out = scrub_topology({"ver": "build 100.300.400.500", "ip": "node 100.83.12.7 up"})
        assert out["ver"] == "build 100.300.400.500"  # not an IP → untouched
        assert out["ip"] == "node <REDACTED-IP> up"

    def test_does_not_mutate_input(self):
        data = {"x": "100.5.6.7"}
        scrub_topology(data)
        assert data["x"] == "100.5.6.7"  # original untouched

    def test_clean_snapshot_unchanged(self):
        snap = Auditor(FakeRunner(_jarvis_responses())).audit("Osasuwu/jarvis")
        d = snap.to_dict()
        assert scrub_topology(d) == d


class TestAuditAll:
    """AC2 — audit a set of repos, returning one snapshot per repo."""

    def test_audit_all_returns_snapshot_per_repo(self):
        responses = {
            **_jarvis_responses(),
            "repos/Osasuwu/beta": {
                "allow_auto_merge": False,
                "visibility": "public",
                "default_branch": "main",
            },
            "repos/Osasuwu/beta/labels": [],
            "repos/Osasuwu/beta/actions/workflows?per_page=100": {"workflows": []},
        }
        not_found = {
            "repos/Osasuwu/beta/branches/main/protection",
            "repos/Osasuwu/beta/contents/.github/dependabot.yml",
        }
        auditor = Auditor(FakeRunner(responses, not_found))
        result = auditor.audit_all(["Osasuwu/jarvis", "Osasuwu/beta"])

        assert set(result) == {"Osasuwu/jarvis", "Osasuwu/beta"}
        assert isinstance(result["Osasuwu/jarvis"], RepoSnapshot)
        assert result["Osasuwu/jarvis"].settings.allow_auto_merge is True
        assert result["Osasuwu/beta"].branch_protection is None

    def test_audit_all_does_not_fail_fast_and_reports_every_failure(self):
        """A dict comprehension aborts on the first exception, discarding work
        and hiding which other repos would also fail. audit_all must attempt
        ALL repos and raise a summary naming EVERY failure — proven by making
        the first repo fail and asserting the second's failure still surfaces.
        (#978 MAJOR 1.)"""
        # Neither repo has a settings response → both fail inside audit().
        runner = FakeRunner({})
        with pytest.raises(RuntimeError) as excinfo:
            runner_auditor = Auditor(runner)
            runner_auditor.audit_all(["Osasuwu/alpha", "Osasuwu/beta"])
        msg = str(excinfo.value)
        # Both repos named → the first failure did not abort the batch.
        assert "Osasuwu/alpha" in msg
        assert "Osasuwu/beta" in msg
        # And it actually attempted the settings read for both.
        assert ("repos/Osasuwu/alpha", False) in runner.calls
        assert ("repos/Osasuwu/beta", False) in runner.calls

    def test_audit_all_isolates_one_failure_among_successes(self):
        """One transient failure must not lose the successful snapshots' work
        silently — audit_all still raises (truncated fixture set is worse than
        a loud failure), but only the failed repo is counted as a failure.
        (#978 MAJOR 1.)"""
        responses = {
            **_jarvis_responses(),
        }
        # beta has no responses → fails; jarvis succeeds.
        runner = FakeRunner(responses)
        with pytest.raises(RuntimeError) as excinfo:
            Auditor(runner).audit_all(["Osasuwu/jarvis", "Osasuwu/beta"])
        msg = str(excinfo.value)
        assert "Osasuwu/beta" in msg
        # Exactly one of two repos is flagged as failed — proving jarvis was
        # audited successfully rather than dragged down with beta. (A raw
        # "jarvis not in msg" check is unreliable: the failed repo's nested
        # error text can mention other paths; the failure *count* is the real
        # isolation invariant.)
        assert "1 of 2 repo(s) failed" in msg

    def test_baseline_repos_is_nonempty_owner_repo_slugs(self):
        # Baseline scope is derived from config/repos.conf at import time and
        # falls back to a single example placeholder on a fresh clone, so the
        # committed example fixture always resolves. Assert the shape, not a
        # hardcoded portfolio (which would leak owner-specific repos).
        assert isinstance(BASELINE_REPOS, list)
        assert BASELINE_REPOS, "baseline scope must never be empty"
        for slug in BASELINE_REPOS:
            assert isinstance(slug, str)
            owner, sep, name = slug.partition("/")
            assert sep == "/" and owner and name, f"not an owner/repo slug: {slug!r}"


class TestGhRunner:
    """The live gh/REST shell — exercised with a monkeypatched ``subprocess``
    so the 404 mapping + pagination parsing are tested with zero live calls."""

    def test_object_endpoint_returns_parsed_json(self, monkeypatch):
        captured = {}

        def fake_run(args, **kwargs):
            captured["args"] = args
            captured["kwargs"] = kwargs
            return _FakeProc(stdout='{"visibility": "public"}')

        monkeypatch.setattr(auditor_mod.subprocess, "run", fake_run)
        out = gh_runner("repos/Osasuwu/jarvis")
        assert out == {"visibility": "public"}
        assert captured["args"][:2] == ["gh", "api"]
        assert "repos/Osasuwu/jarvis" in captured["args"]
        assert "--paginate" not in captured["args"]
        # A bounded timeout is mandatory — an unbounded gh call can wedge the
        # whole 25-call audit on a single network stall. (#978 MAJOR 2.)
        assert captured["kwargs"].get("timeout") is not None

    def test_timeout_maps_to_runtime_error(self, monkeypatch):
        """subprocess.TimeoutExpired must surface as a RuntimeError naming the
        path, not an opaque traceback halfway through a 5-repo audit.
        (#978 MAJOR 2.)"""

        def fake_run(args, **kwargs):
            raise auditor_mod.subprocess.TimeoutExpired(args, kwargs.get("timeout", 60))

        monkeypatch.setattr(auditor_mod.subprocess, "run", fake_run)
        with pytest.raises(RuntimeError, match="timed out"):
            gh_runner("repos/Osasuwu/jarvis")

    def test_paginate_empty_response_returns_empty_list(self, monkeypatch):
        """A paginated endpoint that yields no documents at all (empty stdout)
        must return [] — NOT crash on the mixed-type guard. _parse_concatenated_json('')
        returns [], which the all-arrays / single-value branches both miss,
        falling through to a spurious 'unexpected page structure' RuntimeError.
        (#978 MINOR — empty-paginate crash.)"""

        def fake_run(args, **kwargs):
            return _FakeProc(stdout="")

        monkeypatch.setattr(auditor_mod.subprocess, "run", fake_run)
        assert gh_runner("repos/Osasuwu/jarvis/labels", paginate=True) == []

    def test_404_maps_to_gh_not_found(self, monkeypatch):
        def fake_run(args, **kwargs):
            return _FakeProc(returncode=1, stderr="gh: Not Found (HTTP 404)")

        monkeypatch.setattr(auditor_mod.subprocess, "run", fake_run)
        with pytest.raises(GhNotFound):
            gh_runner("repos/Osasuwu/jarvis/branches/main/protection")

    def test_non_404_error_raises_runtime_error(self, monkeypatch):
        def fake_run(args, **kwargs):
            return _FakeProc(returncode=1, stderr="gh: Bad credentials (HTTP 401)")

        monkeypatch.setattr(auditor_mod.subprocess, "run", fake_run)
        with pytest.raises(RuntimeError, match="401"):
            gh_runner("repos/Osasuwu/jarvis")

    def test_paginate_flattens_concatenated_array_pages(self, monkeypatch):
        # gh api --paginate emits one JSON array per page, concatenated with no
        # separator. The runner must merge them into a single flat list.
        page1 = '[{"name": "a"}, {"name": "b"}]'
        page2 = '[{"name": "c"}]'

        def fake_run(args, **kwargs):
            assert "--paginate" in args
            return _FakeProc(stdout=page1 + page2)

        monkeypatch.setattr(auditor_mod.subprocess, "run", fake_run)
        out = gh_runner("repos/Osasuwu/jarvis/labels", paginate=True)
        assert [d["name"] for d in out] == ["a", "b", "c"]

    def test_paginate_single_page(self, monkeypatch):
        def fake_run(args, **kwargs):
            return _FakeProc(stdout='[{"name": "only"}]')

        monkeypatch.setattr(auditor_mod.subprocess, "run", fake_run)
        out = gh_runner("repos/Osasuwu/jarvis/labels", paginate=True)
        assert [d["name"] for d in out] == ["only"]

    def test_paginate_raises_on_mixed_type_stream(self, monkeypatch):
        """A page stream of mixed types (array + trailing object) is corrupt.
        Returning the raw [list, dict] would fail deep in the caller with no
        useful error — raise explicitly instead. (#978 MINOR 3.)"""

        def fake_run(args, **kwargs):
            return _FakeProc(stdout='[{"name": "a"}]{"cursor": "x"}')

        monkeypatch.setattr(auditor_mod.subprocess, "run", fake_run)
        with pytest.raises(RuntimeError, match="page structure"):
            gh_runner("repos/Osasuwu/jarvis/labels", paginate=True)

    def test_digit_404_in_non_notfound_error_does_not_map_to_gh_not_found(self, monkeypatch):
        """A '404' digit run inside a non-NotFound error (e.g. a path or message
        referencing 404) must NOT false-positive into GhNotFound — match gh's
        actual 'HTTP 404' marker, not a bare digit run. (#978 MINOR 4.)"""

        def fake_run(args, **kwargs):
            return _FakeProc(returncode=1, stderr="gh: rate limited, see 404 widgets (HTTP 403)")

        monkeypatch.setattr(auditor_mod.subprocess, "run", fake_run)
        with pytest.raises(RuntimeError, match="403"):
            gh_runner("repos/Osasuwu/error-404-demo")

    def test_empty_stdout_non_paginate_raises_clear_error(self, monkeypatch):
        """A non-paginated call that exits 0 with an empty body is anomalous —
        gh produced no JSON for an endpoint that always returns an object.
        ``json.loads('')`` would surface an opaque ``JSONDecodeError`` deep in a
        caller; raise a clear RuntimeError naming the path instead, mirroring the
        paginate-path empty-body guard. (#978 round-3 MINOR 7.)"""

        def fake_run(args, **kwargs):
            return _FakeProc(stdout="   ")

        monkeypatch.setattr(auditor_mod.subprocess, "run", fake_run)
        with pytest.raises(RuntimeError, match="empty response body"):
            gh_runner("repos/Osasuwu/jarvis")

    def test_paginate_single_non_array_document_returned_as_is(self, monkeypatch):
        """A single-page response that is one *object* (not an array) under
        ``--paginate`` hits the ``len(values) == 1`` branch and must be returned
        verbatim — not flattened (the all-arrays branch is skipped) and not
        rejected as a mixed-type stream. This is the envelope shape an
        object-returning endpoint produces when paginated. (#978 round-3 NIT —
        single-document paginate branch coverage.)"""

        def fake_run(args, **kwargs):
            assert "--paginate" in args
            return _FakeProc(stdout='{"total_count": 1, "workflows": []}')

        monkeypatch.setattr(auditor_mod.subprocess, "run", fake_run)
        out = gh_runner("repos/Osasuwu/jarvis/actions/workflows", paginate=True)
        assert out == {"total_count": 1, "workflows": []}
