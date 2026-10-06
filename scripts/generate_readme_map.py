"""README class map generator (ADR-0004 decisions 2, 13, 17, 18; #243).

Writes the generated part of README.md between two markers: the "Early version: N of M classes
written" line, the cross-cutting docs, the class × where-it-surfaces map, and the candidates that
are not established. The rest of README.md is hand-written and never touched.

    python scripts/generate_readme_map.py            # rewrite the part between the markers
    python scripts/generate_readme_map.py --check    # exit 1 if README.md would change

Inputs, all read from the root and none hard-coded here:

- `docs/classes/*.md` frontmatter: `class`, `surfaces_at`, and `scope: cross-cutting`. A doc with
  that scope is linked above the map, has no row and is not counted in N.
- `incidents/incidents.csv`: the count per class, and the share that comes from the maintainer's
  own repos. A row is the maintainer's when its link is a repository URL under one of the
  registry's `maintainer_owners`, or is the label "private, not verifiable".
- `docs/vocabularies.json`: the map's stage columns, in that order.
- `docs/class-registry.json`: `planned_classes` (the M), `maintainer_owners` and `candidates`
  (id, name, description). The candidate list is edited by hand: remove an entry when its class
  doc is written.

The generator is calibration-era (decision 11): it changes with the class-doc contract until the
freeze after the third class doc. It imports the gate's frontmatter parser and constants from
`tests/structure_gate.py`, so the two read a doc the same way.

Install: nothing to install; Python 3.12+ and the standard library.
Check: `python scripts/generate_readme_map.py --check` prints nothing and exits 0 on a README that
is up to date. `tests/test_generate_readme_map.py` runs the same comparison in the Tests workflow,
so a PR that changes a class doc or the dataset without regenerating README.md fails.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tests"))

from structure_gate import (  # noqa: E402
    CLASS_DOCS_DIR,
    CROSS_CUTTING,
    DATASET_FILE,
    VOCABULARIES_FILE,
    _parse_frontmatter,
)

START = "<!-- class-map:start -->"
END = "<!-- class-map:end -->"
REGISTRY_FILE = "docs/class-registry.json"
PRIVATE_LABEL = "private, not verifiable"
README = "README.md"

_OWNER_RE = re.compile(r"^https://github\.com/([^/\s]+)/")


def _read(root: Path, rel: str) -> str:
    return (root / rel).read_bytes().decode("utf-8").replace("\r\n", "\n")


def _load_registry(root: Path) -> dict:
    registry = json.loads(_read(root, REGISTRY_FILE))
    if (
        not isinstance(registry.get("planned_classes"), int)
        or registry["planned_classes"] < 0
    ):
        raise ValueError(
            f"{REGISTRY_FILE}: planned_classes must be a non-negative integer"
        )
    for key in ("maintainer_owners", "candidates"):
        if not isinstance(registry.get(key), list):
            raise ValueError(f"{REGISTRY_FILE}: {key} must be a list")
    return registry


def _is_maintainers(link: str, owners: list[str]) -> bool:
    if link == PRIVATE_LABEL:
        return True
    match = _OWNER_RE.match(link)
    return bool(match) and match.group(1).lower() in {o.lower() for o in owners}


def _incident_counts(root: Path, owners: list[str]) -> dict[str, tuple[int, int]]:
    """class -> (all incidents, those from the maintainer's own repos)."""
    counts: dict[str, tuple[int, int]] = {}
    text = _read(root, DATASET_FILE)
    for row in csv.DictReader(text.splitlines()):
        total, mine = counts.get(row["class"], (0, 0))
        counts[row["class"]] = (total + 1, mine + _is_maintainers(row["link"], owners))
    return counts


def _class_docs(root: Path) -> list[tuple[str, dict]]:
    docs = []
    for path in sorted((root / CLASS_DOCS_DIR).glob("*.md")):
        fields = _parse_frontmatter(_read(root, path.relative_to(root).as_posix()))
        if not isinstance(fields.get("class"), str):
            raise ValueError(
                f"{path.relative_to(root).as_posix()}: no 'class' in the frontmatter"
            )
        docs.append((path.relative_to(root).as_posix(), fields))
    return docs


def render_block(root: Path) -> str:
    root = Path(root)
    registry = _load_registry(root)
    stages = json.loads(_read(root, VOCABULARIES_FILE))["surfaces_at"]
    counts = _incident_counts(root, registry["maintainer_owners"])
    docs = _class_docs(root)
    cross = [(p, f) for p, f in docs if f.get("scope") == CROSS_CUTTING]
    classes = sorted(
        ((p, f) for p, f in docs if f.get("scope") != CROSS_CUTTING),
        key=lambda d: d[1]["class"],
    )
    planned = registry["planned_classes"]
    if len(classes) > planned:
        raise ValueError(
            f"{len(classes)} class docs written but planned_classes is {planned} in {REGISTRY_FILE}"
        )

    lines = [
        f"Early version: {len(classes)} of {planned} classes written. A class missing from the "
        "map is not written yet, which does not mean it never happens.",
        "",
    ]
    if cross:
        lines += [
            "Read these every time, whatever your repo looks like. They apply across classes and "
            "are not counted below:",
            "",
        ]
        lines += [f"- [{fields['class']}]({path})" for path, fields in cross]
    else:
        lines.append("No cross-cutting doc is written yet.")
    lines += [
        "",
        "| Class | "
        + " | ".join(stages)
        + " | Incidents | From the maintainer's repos |",
        "|---|" + ":-:|" * len(stages) + "--:|--:|",
    ]
    for path, fields in classes:
        surfaces = fields.get("surfaces_at") or []
        marks = " | ".join("●" if stage in surfaces else "" for stage in stages)
        total, mine = counts.get(fields["class"], (0, 0))
        lines.append(f"| [{fields['class']}]({path}) | {marks} | {total} | {mine} |")
    if not classes:
        lines += ["", "No class is written yet, so the map has no rows."]
    if registry["candidates"]:
        lines += [
            "",
            "**Candidates, not established.** No class doc and no incident evidence yet. "
            "Do not report them as classes.",
            "",
        ]
        lines += [
            f"- **{c['id']}, {c['name']}**: {c['description']}."
            for c in registry["candidates"]
        ]
    return "\n".join(lines) + "\n"


def render_readme(root: Path) -> str:
    root = Path(root)
    text = _read(root, README)
    if (
        text.count(START) != 1
        or text.count(END) != 1
        or text.index(START) > text.index(END)
    ):
        raise ValueError(
            f"{README} needs the markers {START} and {END}, once each, in that order"
        )
    head, rest = text.split(START, 1)
    tail = rest.split(END, 1)[1]
    return f"{head}{START}\n{render_block(root)}{END}{tail}"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generate the README class map.")
    parser.add_argument(
        "--check", action="store_true", help="exit 1 if README.md would change"
    )
    parser.add_argument(
        "--root", default=str(Path(__file__).parent.parent), help="repository root"
    )
    args = parser.parse_args(argv)
    root = Path(args.root)
    expected = render_readme(root)
    if _read(root, README) == expected:
        return 0
    if args.check:
        print(f"{README} is out of date; run: python scripts/generate_readme_map.py")
        return 1
    (root / README).write_bytes(expected.encode("utf-8"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
