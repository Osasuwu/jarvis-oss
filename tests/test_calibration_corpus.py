"""Contract test for the calibration corpus (#101).

The corpus is read as data: n per class, and later caught and missed. These checks hold its shape
to the fields that `RULES.md` requires, so a count cannot drift away from the entries behind it.
"""

from __future__ import annotations

import importlib.util
import re
import subprocess
import sys
from collections import Counter
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SKILL_DIR = ROOT / ".agents" / "skills" / "review-doc"
CALIBRATION_DIR = SKILL_DIR / "calibration"
CORPUS = CALIBRATION_DIR / "corpus.md"

_spec = importlib.util.spec_from_file_location("doc_review", ROOT / "scripts" / "doc_review.py")
dr = importlib.util.module_from_spec(_spec)
sys.modules["doc_review"] = dr
_spec.loader.exec_module(dr)

CLASSES = ("status", "quote", "plan", "fact", "dead-end", "missing-option", "how-to-choose", "other")
ALWAYS_BLOCKING = {"status", "quote", "fact", "plan", "dead-end"}
LABELS = {"blocking", "follow-up"}
SOURCES = {"model", "click-audit", "reader"}
REQUIRED = (
    "source_pr",
    "commit",
    "location",
    "class",
    "label",
    "held_out",
    "selection_source",
    "escape",
    "defect",
    "split",
)

ENTRIES_TITLE = "Entries"
COUNTS_TITLE = "n per class"
HELD_PREFIX = "Held-out entries"

SHA = r"[0-9a-f]{7,40}"
PR_LINK = re.compile(r"^\[#(\d+)\]\(https://github\.com/Osasuwu/jarvis-oss/pull/(\d+)\)$")
ESCAPE = re.compile(
    rf"^round N `({SHA})` → \[round N\+1 finding at `({SHA})`\]"
    r"\((https://github\.com/Osasuwu/jarvis-oss/pull/(\d+)#issuecomment-\d+)\)$"
)
BLOB = re.compile(
    r"\(\[text at round N\]\(https://github\.com/Osasuwu/jarvis-oss/blob/([0-9a-f]{40})/([^#)]+)#(L\d+(?:-L\d+)?)\)\)$"
)
LOCATION = re.compile(r"^`[^`:]+\.md:\d+(-\d+)?`$")
SPLIT = re.compile(r"^(dev|test|held-out|excluded\(.+\))$")
# The calibration-2 split (#144): by PR lineage, so a dev run can never see a test file.
SPLIT_COUNTS = {"dev": 15, "test": 16, "held-out": 1, "excluded": 3}
TEST_BLOCKING = 10
SPLIT_PRS = {"dev": {"62"}, "test": {"75", "81"}}


def _text() -> str:
    return CORPUS.read_text(encoding="utf-8")


def _sections() -> dict[str, str]:
    parts = re.split(r"^## (.+)$", _text(), flags=re.M)
    return {parts[i].strip(): parts[i + 1] for i in range(1, len(parts), 2)}


def _section(prefix: str) -> str:
    matches = [body for title, body in _sections().items() if title.startswith(prefix)]
    assert len(matches) == 1, f"expected one section starting '## {prefix}'"
    return matches[0]


def _entries(body: str) -> dict[str, dict[str, str]]:
    entries: dict[str, dict[str, str]] = {}
    for block in re.split(r"^### ", body, flags=re.M)[1:]:
        head, _, rest = block.partition("\n")
        name = head.strip()
        assert name not in entries, f"duplicate entry id: {name}"
        fields: dict[str, str] = {}
        for line in rest.splitlines():
            m = re.match(r"^- ([a-z_]+): (.*)$", line)
            if m:
                assert m.group(1) not in fields, f"{name}: field {m.group(1)} twice"
                fields[m.group(1)] = m.group(2).strip()
        entries[name] = fields
    return entries


def _counted() -> dict[str, dict[str, str]]:
    return _entries(_section(ENTRIES_TITLE))


def _held() -> dict[str, dict[str, str]]:
    return _entries(_section(HELD_PREFIX))


def _all() -> list[tuple[str, dict[str, str]]]:
    return list(_counted().items()) + list(_held().items())


def _table() -> dict[str, int]:
    rows = re.findall(r"^\| ([a-z-]+) \| (\d+) \|$", _section(COUNTS_TITLE), re.M)
    table: dict[str, int] = {}
    for name, n in rows:
        assert name not in table, f"class listed twice: {name}"
        table[name] = int(n)
    return table


# --- where the file sits ----------------------------------------------------------------------


def test_corpus_sits_outside_the_hashed_skill_file():
    assert CORPUS.is_file()
    assert CORPUS.parent == CALIBRATION_DIR
    assert CORPUS.resolve() != (SKILL_DIR / "SKILL.md").resolve()


def test_corpus_states_what_its_figures_are():
    text = " ".join(_text().split())
    assert "a floor on same-model agreement, not a recall figure" in text


def test_corpus_has_entries():
    assert _counted(), "the corpus has no counted entries"


# --- fields -----------------------------------------------------------------------------------


@pytest.mark.parametrize("name,fields", _all() if CORPUS.is_file() else [])
def test_entry_has_every_required_field(name, fields):
    missing = [f for f in REQUIRED if f not in fields]
    assert not missing, f"{name}: missing {missing}"
    for key, value in fields.items():
        assert value, f"{name}: {key} is empty"


@pytest.mark.parametrize("name,fields", _all() if CORPUS.is_file() else [])
def test_entry_values_are_valid(name, fields):
    assert fields["class"] in CLASSES, f"{name}: unknown class {fields['class']}"
    assert fields["label"] in LABELS, f"{name}: unknown label {fields['label']}"
    if fields["class"] in ALWAYS_BLOCKING:
        assert fields["label"] == "blocking", f"{name}: {fields['class']} is always blocking"
    assert fields["selection_source"] in SOURCES, f"{name}: unknown source"
    assert re.fullmatch(rf"`{SHA}`", fields["commit"]), f"{name}: commit is not a SHA"
    assert LOCATION.match(fields["location"]), f"{name}: location is not path:line"


@pytest.mark.parametrize("name,fields", _all() if CORPUS.is_file() else [])
def test_escape_evidence_links_the_next_round(name, fields):
    pr = PR_LINK.match(fields["source_pr"])
    assert pr and pr.group(1) == pr.group(2), f"{name}: source_pr is not a PR link"
    escape = ESCAPE.match(fields["escape"])
    assert escape, f"{name}: escape needs the round N commit and a link to the finding"
    round_n, finding, _, link_pr = escape.groups()
    assert f"`{round_n}`" == fields["commit"], f"{name}: commit is not round N"
    assert round_n != finding, f"{name}: round N and round N+1 are the same commit"
    assert link_pr == pr.group(1), f"{name}: finding link is on another PR"


@pytest.mark.parametrize("name,fields", _all() if CORPUS.is_file() else [])
def test_defect_links_the_text_at_round_n(name, fields):
    path, _, lines = fields["location"].strip("`").partition(":")
    m = BLOB.search(fields["defect"])
    assert m, f"{name}: defect does not link the doc text at round N"
    sha, linked_path, anchor = m.groups()
    assert sha.startswith(fields["commit"].strip("`")), f"{name}: linked text is not at round N"
    assert linked_path == path, f"{name}: linked text is another file"
    first, _, last = lines.partition("-")
    assert anchor == (f"L{first}-L{last}" if last else f"L{first}"), f"{name}: anchor != location"


# --- held-out ---------------------------------------------------------------------------------


def test_counted_entries_are_not_held_out():
    for name, fields in _counted().items():
        assert fields["held_out"] == "no", f"{name}: held-out entry in the counted list"
        assert "held_out_by" not in fields, f"{name}: held_out_by on a counted entry"


def test_held_out_entries_are_flagged_and_attributed():
    for name, fields in _held().items():
        assert fields["held_out"] == "yes", name
        assert fields.get("held_out_by"), f"{name}: held_out_by missing"


def test_held_out_section_states_its_own_number():
    title = next(t for t in _sections() if t.startswith(HELD_PREFIX))
    m = re.fullmatch(rf"{HELD_PREFIX} \(n = (\d+)\)", title)
    assert m, f"held-out heading must state n: {title}"
    assert int(m.group(1)) == len(_held())


def test_no_entry_is_both_counted_and_held_out():
    assert not set(_counted()) & set(_held())


# --- counts -----------------------------------------------------------------------------------


def test_every_class_is_listed_in_the_counts():
    table = _table()
    for name in CLASSES:
        assert name in table, f"class missing from the counts: {name}"
    assert set(table) == set(CLASSES) | {"total"}, "unexpected rows in the counts"


def test_counts_match_the_entries():
    table = _table()
    actual = Counter(fields["class"] for fields in _counted().values())
    for name in CLASSES:
        assert table[name] == actual.get(name, 0), f"{name}: table {table[name]}, entries {actual.get(name, 0)}"
    assert table["total"] == len(_counted())


# --- split (#144) -----------------------------------------------------------------------------


def _split(fields: dict[str, str]) -> str:
    return fields["split"].partition("(")[0]


def _file(fields: dict[str, str]) -> str:
    return fields["location"].strip("`").partition(":")[0]


@pytest.mark.parametrize("name,fields", _all() if CORPUS.is_file() else [])
def test_entry_split_is_valid(name, fields):
    assert SPLIT.match(fields["split"]), f"{name}: split is not dev, test, held-out or excluded(reason)"
    pr = PR_LINK.match(fields["source_pr"]).group(1)
    if _split(fields) in SPLIT_PRS:
        assert pr in SPLIT_PRS[_split(fields)], f"{name}: PR #{pr} is not in the {_split(fields)} lineage"


def test_held_out_section_is_exactly_the_held_out_split():
    for name, fields in _held().items():
        assert _split(fields) == "held-out", f"{name}: in the held-out section but split {fields['split']}"
    for name, fields in _counted().items():
        assert _split(fields) != "held-out", f"{name}: split held-out outside the held-out section"


def test_split_counts_are_the_registered_ones():
    actual = Counter(_split(fields) for _, fields in _all())
    assert dict(actual) == SPLIT_COUNTS
    blocking = sum(1 for _, f in _all() if _split(f) == "test" and f["label"] == "blocking")
    assert blocking == TEST_BLOCKING


def test_corpus_states_the_split_counts():
    text = " ".join(_text().split())
    assert "Counts: dev 15, test 16 (10 `blocking`), held-out 1, excluded 3." in text


# --- the split cannot leak ----------------------------------------------------------------------
#
# A dev run at a dev commit reviews the docs that hold dev entries there, plus every file under
# examples/ or resources/ that names such a doc in `pairs_with`, plus the repo-internal `.md`
# links of all of those, one hop out (the review scope SKILL.md gives a doc, as CALIBRATION.md's
# Method states it). None of that may be a test or held-out entry's file. Files are read at the
# commit with `git show`, so the check is on the tree a snapshot is cut from, not on `main`.


def _git(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(ROOT), *args], capture_output=True, text=True)


def _show(commit: str, path: str) -> str | None:
    result = _git("show", f"{commit}:{path}")
    return result.stdout if result.returncode == 0 else None


def _dev_scope(commit: str, docs: set[str]) -> set[str]:
    """What a dev run sees: the scope `scripts/doc_review.py` gives a review of each dev doc."""
    listing = _git("ls-tree", "-r", "--name-only", commit, "--", "examples", "resources")
    return set().union(*(dr.in_scope_files(doc, commit, _show, listing.stdout.split())
                         for doc in docs))


def _dev_commits() -> list[str]:
    return sorted({f["commit"].strip("`") for _, f in _all() if _split(f) == "dev"})


@pytest.mark.parametrize("commit", _dev_commits() if CORPUS.is_file() else [])
def test_a_dev_run_cannot_see_a_test_or_held_out_file(commit):
    if _git("cat-file", "-e", f"{commit}^{{commit}}").returncode != 0:
        pytest.fail(f"dev commit {commit} is not in this clone; fetch every branch (fetch-depth: 0)")
    dev_files = {_file(f) for _, f in _all() if _split(f) == "dev" and f["commit"].strip("`") == commit}
    docs = {p for p in dev_files if p.startswith("docs/")}
    assert docs, f"{commit}: no dev doc at this commit"
    scope = _dev_scope(commit, docs)
    assert dev_files <= scope, f"{commit}: a dev entry's file is outside the reviewed scope"
    off_limits = {_file(f) for _, f in _all() if _split(f) in ("test", "held-out")}
    leaked = scope & off_limits
    assert not leaked, f"{commit}: a dev run would review a test or held-out file: {sorted(leaked)}"
