"""Behaviour checks for scripts/generate_readme_map.py, the README class map (#243).

The expected blocks are written out by hand from the committed `structure_gate/valid` tree plus
a registry, so they do not come from the generator's own steps.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import sys
from pathlib import Path

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

from generate_readme_map import END, START, main, render_block, render_readme  # noqa: E402

REPO_ROOT = Path(__file__).parent.parent
VALID = Path(__file__).parent / "fixtures" / "structure_gate" / "valid"

REGISTRY = "docs/class-registry.json"
DATASET = "incidents/incidents.csv"
ALPHA = "docs/classes/alpha.md"

CANDIDATE = {
    "id": "N2",
    "name": "Non-progressive iteration",
    "description": "repeated edits without new information, undetected",
}


def _tree(tmp_path: Path, planned: int = 18, candidates: list | None = None) -> Path:
    """The valid fixture tree plus a registry, with the dataset links the share rule is tried on:
    INC-001 is under a look-alike owner, INC-002 under the real one, INC-003 is the private
    label, and the beta rows stay external."""
    root = tmp_path / "tree"
    shutil.copytree(VALID, root)
    registry = {
        "planned_classes": planned,
        "maintainer_owners": ["acme"],
        "candidates": [CANDIDATE] if candidates is None else candidates,
    }
    (root / REGISTRY).write_text(json.dumps(registry), encoding="utf-8")
    dataset = root / DATASET
    text = dataset.read_text(encoding="utf-8")
    text = text.replace(
        "https://example.com/alpha/1", "https://github.com/acmefoo/repo/pull/1"
    )
    text = text.replace(
        "https://example.com/alpha/2", "https://github.com/acme/repo/pull/2"
    )
    dataset.write_bytes(text.encode("utf-8"))
    return root


def _readme(root: Path, between: str = "old") -> Path:
    path = root / "README.md"
    path.write_bytes(
        f"# Title\n\n{START}\n{between}\n{END}\n\nAfter.\n".encode("utf-8")
    )
    return path


EXPECTED_ONE_CLASS = """\
Early version: 1 of 18 classes written. A class missing from the map is not written yet, which does not mean it never happens.

Read these every time, whatever your repo looks like. They apply across classes and are not counted below:

- [irreversible-effects](docs/classes/cross.md)

| Class | task | code | ci | review | merge | after-merge | Incidents | From the maintainer's repos | Evidence strength |
|---|:-:|:-:|:-:|:-:|:-:|:-:|--:|--:|---|
| [alpha](docs/classes/alpha.md) |  |  | ● | ● |  |  | 3 | 2 | 1 primary, 1 secondary, 1 private |

**Candidates, not established.** No class doc and no incident evidence yet. Do not report them as classes.

- **N2, Non-progressive iteration**: repeated edits without new information, undetected.
"""

EXPECTED_EMPTY = """\
Early version: 0 of 18 classes written. A class missing from the map is not written yet, which does not mean it never happens.

No cross-cutting doc is written yet.

| Class | task | code | ci | review | merge | after-merge | Incidents | From the maintainer's repos | Evidence strength |
|---|:-:|:-:|:-:|:-:|:-:|:-:|--:|--:|---|

No class is written yet, so the map has no rows.
"""


def _empty_tree(tmp_path: Path) -> Path:
    root = _tree(tmp_path, candidates=[])
    (root / ALPHA).unlink()
    (root / "docs/classes/cross.md").unlink()
    return root


# --- the generated block -------------------------------------------------------


def test_block_lists_the_class_counts_the_share_and_keeps_the_cross_cutting_doc_out(
    tmp_path,
):
    assert render_block(_tree(tmp_path)) == EXPECTED_ONE_CLASS


def test_block_for_a_base_with_no_class_is_the_empty_map(tmp_path):
    assert render_block(_empty_tree(tmp_path)) == EXPECTED_EMPTY


def test_more_classes_written_than_planned_is_an_error(tmp_path):
    root = _tree(tmp_path, planned=0)
    with pytest.raises(
        ValueError, match="1 class docs written but planned_classes is 0"
    ):
        render_block(root)


# --- splicing into the README --------------------------------------------------


def test_render_readme_replaces_only_what_is_between_the_markers(tmp_path):
    root = _tree(tmp_path)
    _readme(root)
    assert render_readme(root) == (
        f"# Title\n\n{START}\n{EXPECTED_ONE_CLASS}{END}\n\nAfter.\n"
    )


@pytest.mark.parametrize("missing", [START, END])
def test_a_readme_without_both_markers_is_an_error(tmp_path, missing):
    root = _tree(tmp_path)
    readme = _readme(root)
    readme.write_bytes(readme.read_bytes().replace(missing.encode("utf-8"), b"", 1))
    with pytest.raises(ValueError, match="markers"):
        render_readme(root)


# --- the command ---------------------------------------------------------------


def test_check_fails_on_drift_and_write_brings_the_readme_back_in_line(
    tmp_path, capsys
):
    root = _tree(tmp_path)
    readme = _readme(root)
    assert main(["--root", str(root), "--check"]) == 1
    assert "README.md is out of date" in capsys.readouterr().out
    assert main(["--root", str(root)]) == 0
    assert readme.read_bytes().decode("utf-8") == (
        f"# Title\n\n{START}\n{EXPECTED_ONE_CLASS}{END}\n\nAfter.\n"
    )
    assert main(["--root", str(root), "--check"]) == 0


# --- the real README -----------------------------------------------------------


def test_real_readme_equals_the_generators_output():
    readme = (
        (REPO_ROOT / "README.md").read_bytes().decode("utf-8").replace("\r\n", "\n")
    )
    assert readme == render_readme(REPO_ROOT)


_URL_RE = re.compile(r"https?://\S+")
_LINK_RE = re.compile(r"\]\(([^)\s]+)\)")
_BACKTICK_PATH_RE = re.compile(r"`([\w.-]+(?:/[\w.-]+)+)`")


def test_every_path_the_readme_links_or_names_resolves():
    # Walks links and backticked paths, so a deleted file named in prose is caught as well as a
    # dead link. A path that does not exist is the oracle; no list of deleted files is kept.
    text = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    targets = [
        t for t in _LINK_RE.findall(text) if "://" not in t and not t.startswith("#")
    ]
    targets += _BACKTICK_PATH_RE.findall(_URL_RE.sub("", text))
    assert "docs/adr/0004-knowledge-base-of-failure-classes.md" in targets
    missing = sorted({t for t in targets if not (REPO_ROOT / t.split("#")[0]).exists()})
    assert missing == []
