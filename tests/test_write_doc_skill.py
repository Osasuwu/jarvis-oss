"""Contract test for the write-doc skill (#242).

The skill teaches a writer the class-doc contract that tests/structure_gate.py enforces, and it
is only useful while the two agree. These tests therefore read the gate and the vocabularies
file and compare the skill with them; none of them pins the skill's wording.
"""

import json
import re
from pathlib import Path

from structure_gate import (
    CROSS_CUTTING,
    DATASET_COLUMNS,
    DOC_SIZE_CAP_BYTES,
    EXAMPLES_MAX,
    EXAMPLES_MIN,
    VOCABULARIES_FILE,
    check_tree,
)

REPO_ROOT = Path(__file__).parent.parent
SKILL = REPO_ROOT / ".agents" / "skills" / "write-doc" / "SKILL.md"

# Terms of the contract ADR-0004 retired. The gate no longer enforces them, so a writer who
# meets one in the skill would add what nothing checks and a reviewer would have to refuse.
RETIRED_TERMS = (
    r"\bkind\b",
    r"Relations:",
    r"numbered options?",
    r"check dates?",
    r"catalog",
)


def _skill_text() -> str:
    return SKILL.read_text(encoding="utf-8")


def _skeleton(text: str) -> str:
    assert "## The skeleton" in text, "the skill has no '## The skeleton' section"
    match = re.search(
        r"```markdown\n(.*?)\n```", text.split("## The skeleton", 1)[1], re.DOTALL
    )
    assert match, "'## The skeleton' has no ```markdown block"
    return match.group(1)


def test_the_skeleton_passes_the_gate_it_describes(tmp_path):
    """The skill's skeleton, with its placeholders filled in, is a doc the gate accepts."""
    vocabularies = json.loads(
        (REPO_ROOT / VOCABULARIES_FILE).read_text(encoding="utf-8")
    )
    skeleton = _skeleton(_skill_text())
    # The skill points at the vocabularies file instead of listing values, so the test supplies
    # one value for the line that takes them.
    assert re.search(r"^surfaces_at:", skeleton, re.MULTILINE), (
        "skeleton has no surfaces_at line"
    )
    doc = re.sub(
        r"^surfaces_at:.*$",
        f"surfaces_at: [{vocabularies['surfaces_at'][0]}]",
        skeleton,
        flags=re.MULTILINE,
    ).replace("<class-slug>", "example-class")
    # The skeleton's Cost placeholder shows the date form. Given a figure, the gate's rule on
    # dated figures is applied to the form the skill teaches.
    doc = doc.replace("YYYY-MM", "2026-09")
    doc = doc.replace("**Cost:** ", "**Cost:** 5 ", 1)
    # Examples are written as `INC-<digits>`; give each placeholder its own row of the dataset
    # built below.
    placeholders = doc.count("INC-<digits>")
    assert placeholders >= EXAMPLES_MIN, (
        "skeleton shows fewer Examples than the gate needs"
    )
    for n in range(1, placeholders + 1):
        doc = doc.replace("INC-<digits>", f"INC-00{n}", 1)

    root = tmp_path / "tree"
    (root / "docs" / "classes").mkdir(parents=True)
    (root / ".github").mkdir()
    (root / "incidents").mkdir()
    for rel in (VOCABULARIES_FILE, ".github/hold-paths.json"):
        (root / rel).write_bytes((REPO_ROOT / rel).read_bytes())
    (root / "docs" / "classes" / "example-class.md").write_text(doc, encoding="utf-8")
    values = {
        "class": "example-class",
        "stage": vocabularies["stage"][0],
        "source_type": "pull request",
        "evidence_strength": vocabularies["evidence_strength"][0],
        "link": "https://example.com/incident",
    }
    rows = [",".join(DATASET_COLUMNS)] + [
        ",".join([f"INC-00{n}"] + [values[c] for c in DATASET_COLUMNS[1:]])
        for n in range(1, placeholders + 1)
    ]
    (root / "incidents" / "incidents.csv").write_text(
        "\n".join(rows) + "\n", encoding="utf-8"
    )

    assert [(v.code, v.message) for v in check_tree(root)] == []


def test_the_skill_states_the_limits_the_gate_checks():
    text = _skill_text()
    assert f"{DOC_SIZE_CAP_BYTES:,} bytes" in text
    assert f"{EXAMPLES_MIN}–{EXAMPLES_MAX}" in text
    assert f"scope: {CROSS_CUTTING}" in text


def test_vocabularies_are_pointed_to_not_copied():
    text = _skill_text()
    assert VOCABULARIES_FILE in text
    vocabularies = json.loads(
        (REPO_ROOT / VOCABULARIES_FILE).read_text(encoding="utf-8")
    )
    # A hyphenated value ("after-merge") cannot turn up in ordinary prose by accident, so it is
    # what shows a list was copied. The guard keeps the check from passing with nothing to find.
    hyphenated = sorted(
        {v for values in vocabularies.values() for v in values if "-" in v}
    )
    assert hyphenated, (
        "no hyphenated vocabulary value left; pick another marker for a copy"
    )
    assert [v for v in hyphenated if v in text] == []


def test_the_skill_does_not_teach_the_retired_contract():
    text = _skill_text()
    assert [t for t in RETIRED_TERMS if re.search(t, text, re.IGNORECASE)] == []


def test_every_path_the_skill_points_at_exists():
    text = _skill_text()
    links = [
        link.split("#")[0]
        for link in re.findall(r"\]\(([^)\s]+)\)", text)
        if "://" not in link and not link.startswith("#")
    ]
    scripts = re.findall(r"\bscripts/[\w./-]+\.py", text)
    assert links and scripts, (
        "the skill points at no repo file or no script; the check is empty"
    )
    missing = [str(p) for p in links if not (SKILL.parent / p).resolve().is_file()]
    missing += [p for p in scripts if not (REPO_ROOT / p).is_file()]
    assert missing == []
