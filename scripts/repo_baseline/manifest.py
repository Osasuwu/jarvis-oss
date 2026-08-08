"""Manifest model — per-repo declarative config for the baseline sync.

Every axis has a default so per-repo manifests are sparse (declare only
deviations from the default profile).
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from typing import ClassVar, Dict, List, Optional


class FileClass(enum.Enum):
    """Three-class file model for the baseline sync."""

    MANAGED = "managed"
    """Overwrite byte-for-byte modulo axis substitution."""

    LANGUAGE_TEST = "language_test"
    """Managed + ``ci_language`` axis + escape-hatch (``test_extras`` / override)."""

    REPO_CUSTOM = "repo_custom"
    """Default-deny: untouched unless listed in ``manifest.custom_files``."""


@dataclass
class AxisProfile:
    """Named set of axis defaults (e.g. ``full``, ``minimal``)."""

    runs_on: List[str] = field(default_factory=lambda: ["ubuntu-latest"])
    ci_language: str = "python"
    code_review_marketplace: str = "anthropics/claude-code-action@v1"
    auto_merge: bool = True
    branch_protection: bool = True
    visibility: str = "public"
    test_extras: str = "[full,dev]"
    ci_meta_pytest_args: str = ""
    dependabot_ecosystems: List[str] = field(default_factory=lambda: ["pip", "github-actions"])
    managed_files: List[str] = field(default_factory=lambda: list(_MANAGED_FILES_DEFAULT))
    custom_files: List[str] = field(default_factory=list)
    prune: bool = False


# ── Canonical set  ────────────────────────────────────────────────────
_MANAGED_FILES_DEFAULT = [
    ".github/workflows/code-review.yml",
    ".github/workflows/owner-queue-guard.yml",
    ".github/workflows/pr-body-check.yml",
    ".github/workflows/ci-meta.yml",
    ".github/dependabot.yml",
    ".github/ISSUE_TEMPLATE/bug.yml",
    ".github/ISSUE_TEMPLATE/task.yml",
    ".github/ISSUE_TEMPLATE/config.yml",
    ".github/PULL_REQUEST_TEMPLATE.md",
]


def _resolve_managed_files(
    override: list | None, profile: str, profiles: dict[str, AxisProfile] | None = None
) -> list[str]:
    """Resolve managed_files: explicit override → profile default → hardcoded default."""
    if override is not None:
        return override
    profiles = profiles or {}
    profile_val = profiles.get(profile, AxisProfile()).managed_files if profile else None
    return profile_val or list(_MANAGED_FILES_DEFAULT)


@dataclass
class Manifest:
    """Per-repo declarative configuration for the baseline sync.

    Use ``from_dict`` for deserialization from a YAML/JSON manifest file.
    """

    repo: str = ""
    profile: str = "full"
    visibility: Optional[str] = None

    # ── Axis overrides (None = use profile default) ───────────────────
    # NOTE: ``runs_on`` is deliberately absent — it is an *observed* axis
    # (#1406), resolved from the live audit by
    # :func:`~scripts.repo_baseline.applier.resolve_runs_on`. ``AxisProfile``
    # still carries it as the last-resort default for a repo with no
    # observable workflows, but a manifest may not declare it: a declaration
    # would silently outrank the observation, which is how a billing-blocked
    # account got GitHub-hosted workflows written into it.
    ci_language: Optional[str] = None
    code_review_marketplace: Optional[str] = None
    auto_merge: Optional[bool] = None
    branch_protection: Optional[bool] = None
    test_extras: Optional[str] = None
    # Extra argv appended to ci-meta.yml's pytest invocation. Declared, not
    # observed (#1406): whether cutting conftest discovery is safe depends on
    # what the repo's root conftest does for its *other* suites, which the
    # audit cannot see.
    ci_meta_pytest_args: Optional[str] = None
    dependabot_ecosystems: Optional[List[str]] = None
    prune: Optional[bool] = None

    # ── Explicit check-contexts (required axis — no fallback) ──────────
    required_check_contexts: List[str] = field(default_factory=list)

    # ── File inventory (Optional = resolve via profile) ─────────────────
    managed_files: Optional[List[str]] = None
    custom_files: List[str] = field(default_factory=list)

    # ── LANGUAGE-TEST class files (None = derive from ci_language) ──────
    language_test_files: Optional[List[str]] = None

    # ── Profiles (class constant — not a field) ────────────────────────
    _PROFILES: ClassVar[Dict[str, AxisProfile]] = {
        "full": AxisProfile(),
        "minimal": AxisProfile(
            auto_merge=False,
            branch_protection=False,
            dependabot_ecosystems=[],  # bare repo has no dependabot config
            managed_files=[
                ".github/workflows/owner-queue-guard.yml",
                ".github/workflows/pr-body-check.yml",
                ".github/workflows/ci-meta.yml",
                ".github/dependabot.yml",
            ],
        ),
    }

    @classmethod
    def from_dict(cls, data: dict) -> Manifest:
        valid = {
            "repo",
            "profile",
            "visibility",
            "ci_language",
            "code_review_marketplace",
            "auto_merge",
            "branch_protection",
            "test_extras",
            "ci_meta_pytest_args",
            "dependabot_ecosystems",
            "required_check_contexts",
            "managed_files",
            "custom_files",
            "language_test_files",
            "prune",
        }
        extra = set(data) - valid
        if extra:
            raise ValueError(f"Unknown manifest keys: {sorted(extra)}")

        return cls(
            repo=data.get("repo", ""),
            profile=data.get("profile", "full"),
            visibility=data.get("visibility"),
            ci_language=data.get("ci_language"),
            code_review_marketplace=data.get("code_review_marketplace"),
            auto_merge=data.get("auto_merge"),
            branch_protection=data.get("branch_protection"),
            test_extras=data.get("test_extras"),
            ci_meta_pytest_args=data.get("ci_meta_pytest_args"),
            dependabot_ecosystems=data.get("dependabot_ecosystems"),
            required_check_contexts=data.get("required_check_contexts", []),
            managed_files=data.get("managed_files"),
            custom_files=data.get("custom_files", []),
            prune=data.get("prune"),
            language_test_files=data.get("language_test_files"),
        )

    def resolve_axis(self, key: str) -> str | int | bool | list | None:
        """Resolve an axis value: per-manifest override → profile default."""
        axis_defaults = self._PROFILES.get(self.profile, AxisProfile())
        override = getattr(self, key, None)
        profile_val = getattr(axis_defaults, key, None)

        if key == "required_check_contexts":
            return self.required_check_contexts  # always explicit, no fallback

        if key == "managed_files":
            return _resolve_managed_files(override, self.profile, self._PROFILES)

        # Only None (not set) → use profile default. Explicit empty list/string
        # is a valid override meaning "clear this field".
        if override is None:
            return profile_val
        return override

    @property
    def resolved_managed_files(self) -> List[str]:
        """Managed files resolved through current profile."""
        result = self.resolve_axis("managed_files")
        if result is None:
            return list(_MANAGED_FILES_DEFAULT)
        return result

    @property
    def resolved_language_test_files(self) -> List[str]:
        """Language-test files: explicit override → derived from ci_language."""
        if self.language_test_files is not None:
            return self.language_test_files
        if self.resolve_axis("ci_language") == "python":
            return [".github/workflows/pytest.yml"]
        return []

    def class_for_file(self, path: str) -> FileClass:
        """Determine the file class for a given repo-path."""
        if path in self.custom_files:
            return FileClass.REPO_CUSTOM
        if path in self.resolved_language_test_files:
            return FileClass.LANGUAGE_TEST
        if path in self.resolved_managed_files:
            return FileClass.MANAGED
        return FileClass.REPO_CUSTOM  # default-deny

    def file_class_label(self, path: str) -> str:
        return self.class_for_file(path).value
