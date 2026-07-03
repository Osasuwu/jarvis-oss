"""repo-baseline: canonical, re-syncable GitHub-repo setup.

Pure decision core (Manifest, Renderer, Planner, LabelMigrator) behind
thin gh/REST shells. All dry-run safe — no network or filesystem mutation.
"""

from .manifest import FileClass, Manifest, AxisProfile
from .renderer import Renderer, RenderError
from .planner import Planner, Action, ActionKind
from .auditor import (
    BASELINE_REPOS,
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

__all__ = [
    "FileClass",
    "Manifest",
    "AxisProfile",
    "Renderer",
    "RenderError",
    "Planner",
    "Action",
    "ActionKind",
    "BASELINE_REPOS",
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
]
