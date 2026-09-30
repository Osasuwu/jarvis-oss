"""Every action in every workflow is pinned to a full commit SHA.

A tag such as `@v4` can be moved to a different commit by whoever controls the action's repo;
a 40-character SHA cannot. The check runs over the whole workflows directory, so a new workflow
is covered without a test of its own.
"""

import re
from pathlib import Path

WORKFLOWS = Path(__file__).resolve().parents[1] / ".github" / "workflows"


def test_every_action_in_every_workflow_is_pinned_to_a_full_commit_sha():
    files = sorted(WORKFLOWS.glob("*.yml")) + sorted(WORKFLOWS.glob("*.yaml"))
    assert files, f"no workflows found in {WORKFLOWS}"
    unpinned = []
    for path in files:
        for ref in re.findall(r"uses:\s*(\S+)", path.read_text(encoding="utf-8")):
            # An action in this repo (`./path`) has no ref: it runs at the workflow's own commit.
            if ref.startswith("./"):
                continue
            if not re.fullmatch(r"[\w.-]+/[\w./-]+@[0-9a-f]{40}", ref):
                unpinned.append(f"{path.name}: {ref}")
    assert not unpinned, "\n".join(unpinned)
