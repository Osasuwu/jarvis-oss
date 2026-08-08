"""repo-baseline: canonical, re-syncable GitHub-repo setup.

Pure decision core (Manifest, Renderer, Planner, LabelMigrator) behind
thin gh/REST shells. All dry-run safe — no network or filesystem mutation.
"""

from .manifest import FileClass, Manifest, AxisProfile
from .renderer import Renderer, RenderError
from .planner import Planner, Action, ActionKind
from .auditor import (
    ACCOUNT_PASSES,
    BASELINE_REPOS,
    SECONDARY_REPOS,
    Auditor,
    BranchProtection,
    GhNotFound,
    GhRunner,
    LabelSnapshot,
    RepoSettings,
    RepoSnapshot,
    gh_runner,
    scrub_topology,
    seed_manifest,
)
from .label_migrator import (
    ActualLabel,
    AddAction,
    LabelMigrator,
    LabelPlan,
    MergeAction,
    OrphanLabel,
    RenameAction,
)
from .label_schema import (
    CLEAN_LABELS,
    CleanLabel,
    clean_label_by_name,
    clean_label_names,
)
from .executor import (
    ACCOUNT_REPOS,
    SYNC_BRANCH,
    GhWriteRunner,
    IdentityError,
    RepoOutcome,
    WriteOp,
    evaluate_protection_guard,
    execute_account_pass,
    execute_repo,
    find_sync_pr,
    gh_write_runner,
    preflight_identity,
    render_pr_body,
    resolve_repos,
)

__all__ = [
    "FileClass",
    "Manifest",
    "AxisProfile",
    "Renderer",
    "RenderError",
    "Planner",
    "Action",
    "ActionKind",
    "ACCOUNT_PASSES",
    "BASELINE_REPOS",
    "SECONDARY_REPOS",
    "Auditor",
    "BranchProtection",
    "GhNotFound",
    "GhRunner",
    "LabelSnapshot",
    "RepoSettings",
    "RepoSnapshot",
    "gh_runner",
    "scrub_topology",
    "seed_manifest",
    "ActualLabel",
    "AddAction",
    "LabelMigrator",
    "LabelPlan",
    "MergeAction",
    "OrphanLabel",
    "RenameAction",
    "CLEAN_LABELS",
    "CleanLabel",
    "clean_label_by_name",
    "clean_label_names",
    "ACCOUNT_REPOS",
    "SYNC_BRANCH",
    "GhWriteRunner",
    "IdentityError",
    "RepoOutcome",
    "WriteOp",
    "evaluate_protection_guard",
    "execute_account_pass",
    "execute_repo",
    "find_sync_pr",
    "gh_write_runner",
    "preflight_identity",
    "render_pr_body",
    "resolve_repos",
]
