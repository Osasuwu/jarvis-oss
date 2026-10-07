"""Behaviour checks for tests/structure_gate.py, the class-doc contract of ADR-0004 (#241).

Each violating fixture is the committed `valid` tree with one edit applied in the test, and
the test asserts the exact violation set, so a fixture trips the one rule it is named after.
"""

import json
import shutil
from pathlib import Path

import pytest

from structure_gate import check_tree

FIXTURES = Path(__file__).parent / "fixtures" / "structure_gate"
VALID = FIXTURES / "valid"
REPO_ROOT = Path(__file__).parent.parent

ALPHA = "docs/classes/alpha.md"
CROSS = "docs/classes/cross.md"
DATASET = "incidents/incidents.csv"
VOCAB = "docs/vocabularies.json"
HOLD_PATHS = ".github/hold-paths.json"


def _tree(tmp_path: Path) -> Path:
    root = tmp_path / "tree"
    shutil.copytree(VALID, root)
    return root


def _edit(root: Path, rel: str, old: str, new: str) -> None:
    path = root / rel
    text = path.read_text(encoding="utf-8")
    assert old in text, f"fixture edit does not apply: {old!r} not in {rel}"
    path.write_bytes(text.replace(old, new, 1).encode("utf-8"))


def _violations(root: Path) -> list[tuple[str, str]]:
    return sorted((v.path, v.code) for v in check_tree(root))


# --- the tree as it stands ---------------------------------------------------


def test_fully_valid_tree_passes():
    assert check_tree(VALID) == []


def test_real_tree_passes_structure_gate():
    # With no class docs and no dataset rows yet, the tree after this slice passes.
    assert check_tree(REPO_ROOT) == []


# --- a scanned directory that does not exist ---------------------------------


@pytest.mark.parametrize("missing", ["docs/classes", "incidents"])
def test_a_missing_scanned_directory_fails_and_names_it(tmp_path, missing):
    root = _tree(tmp_path)
    shutil.rmtree(root / missing)
    found = check_tree(root)
    assert [(v.path, v.code) for v in found if v.code == "scan_dir_missing"] == [
        (missing, "scan_dir_missing")
    ]
    assert all(missing in v.message for v in found if v.code == "scan_dir_missing")


def test_a_missing_dataset_file_in_an_existing_directory_fails(tmp_path):
    root = _tree(tmp_path)
    (root / DATASET).unlink()
    assert (DATASET, "required_file_missing") in _violations(root)


# --- the vocabularies and the path definition are read, not hard-coded --------


def test_the_vocabularies_come_from_the_file_not_from_the_gate(tmp_path):
    root = _tree(tmp_path)
    vocab = json.loads((root / VOCAB).read_text(encoding="utf-8"))
    vocab["stage"] = [s if s != "review" else "triage" for s in vocab["stage"]]
    (root / VOCAB).write_text(json.dumps(vocab), encoding="utf-8")
    # INC-001 is on stage "review", which the edited vocabulary no longer lists...
    assert _violations(root) == [(DATASET, "row_stage_not_in_vocabulary")]
    # ...and a stage only the edited vocabulary lists is accepted.
    _edit(root, DATASET, "INC-001,alpha,review", "INC-001,alpha,triage")
    assert check_tree(root) == []


@pytest.mark.parametrize("rel", [VOCAB, HOLD_PATHS])
def test_a_missing_input_file_fails_and_names_it(tmp_path, rel):
    root = _tree(tmp_path)
    (root / rel).unlink()
    found = check_tree(root)
    assert (rel, "required_file_missing") in [(v.path, v.code) for v in found]
    assert any(rel in v.message for v in found)


@pytest.mark.parametrize("key", ["stage", "evidence_strength", "surfaces_at"])
def test_a_vocabulary_that_is_not_a_list_of_words_fails(tmp_path, key):
    root = _tree(tmp_path)
    vocab = json.loads((root / VOCAB).read_text(encoding="utf-8"))
    vocab[key] = "review"
    (root / VOCAB).write_text(json.dumps(vocab), encoding="utf-8")
    assert (VOCAB, f"vocabulary_invalid:{key}") in _violations(root)


def test_the_gate_selects_documents_by_the_hold_path_definition(tmp_path):
    root = _tree(tmp_path)
    # docs/adr/ is excluded by the definition; the same file anywhere else under docs/ is a doc.
    (root / "docs" / "adr").mkdir()
    (root / "docs" / "adr" / "0001-x.md").write_text("# ADR-0001\n\nDecided.\n", encoding="utf-8")
    assert check_tree(root) == []
    (root / "docs" / "other").mkdir()
    (root / "docs" / "other" / "0001-x.md").write_text("# Not an ADR\n", encoding="utf-8")
    assert ("docs/other/0001-x.md", "doc_missing_key:class") in _violations(root)
    # Moving docs/adr/ out of the exclusion list in the definition makes the ADR a doc too.
    definition = json.loads((root / HOLD_PATHS).read_text(encoding="utf-8"))
    definition["excluded_prefixes"] = []
    (root / HOLD_PATHS).write_text(json.dumps(definition), encoding="utf-8")
    assert ("docs/adr/0001-x.md", "doc_missing_key:class") in _violations(root)


# --- class docs: frontmatter --------------------------------------------------


def _cut(root: Path, rel: str, start: str, end: str) -> None:
    """Remove the text from `start` up to (not including) `end`."""
    path = root / rel
    text = path.read_text(encoding="utf-8")
    i, j = text.index(start), text.index(end)
    assert i < j, f"fixture cut does not apply: {start!r} .. {end!r} in {rel}"
    path.write_bytes((text[:i] + text[j:]).encode("utf-8"))


@pytest.mark.parametrize(
    ("key", "line"),
    [
        ("class", "class: alpha\n"),
        ("surfaces_at", "surfaces_at: [review, ci]\n"),
        ("applies_when", "applies_when: the repo lets an agent open pull requests\n"),
        ("applies_when_not", "applies_when_not: no agent writes code in the repo\n"),
    ],
)
def test_a_class_doc_without_a_required_key_fails(tmp_path, key, line):
    root = _tree(tmp_path)
    _edit(root, ALPHA, line, "")
    # A doc without a class has none to hold the cited rows to; only the missing key is reported.
    assert _violations(root) == [(ALPHA, f"doc_missing_key:{key}")]


def test_surfaces_at_that_is_not_a_list_fails(tmp_path):
    root = _tree(tmp_path)
    _edit(root, ALPHA, "surfaces_at: [review, ci]", "surfaces_at: review")
    assert _violations(root) == [(ALPHA, "surfaces_at_not_list")]


def test_surfaces_at_value_outside_the_vocabulary_fails(tmp_path):
    root = _tree(tmp_path)
    _edit(root, ALPHA, "surfaces_at: [review, ci]", "surfaces_at: [review, nowhere]")
    assert _violations(root) == [(ALPHA, "surfaces_at_not_in_vocabulary:nowhere")]


@pytest.mark.parametrize(
    "heading",
    ["TL;DR", "Symptom", "Examples", "Mechanism", "Where it surfaces", "Protections", "Evidence"],
)
def test_a_class_doc_without_a_required_section_fails(tmp_path, heading):
    root = _tree(tmp_path)
    _edit(root, ALPHA, f"## {heading}\n", "")
    assert _violations(root) == [(ALPHA, f"doc_missing_section:{heading}")]


# --- class docs: Examples -----------------------------------------------------

CROSS_EXAMPLES = "- INC-001: a case from class alpha.\n- INC-004: a case from class beta.\n"


def _cross_cites(root: Path, ids: list[str]) -> None:
    _edit(root, CROSS, CROSS_EXAMPLES, "".join(f"- {i}: a case.\n" for i in ids))


def test_examples_citing_one_incident_fail(tmp_path):
    root = _tree(tmp_path)
    _cross_cites(root, ["INC-001"])
    assert _violations(root) == [(CROSS, "examples_count")]


def test_examples_citing_six_incidents_fail(tmp_path):
    root = _tree(tmp_path)
    _cross_cites(root, [f"INC-00{n}" for n in range(1, 7)])
    assert _violations(root) == [(CROSS, "examples_count")]


def test_examples_citing_five_incidents_pass(tmp_path):
    root = _tree(tmp_path)
    _cross_cites(root, [f"INC-00{n}" for n in range(1, 6)])
    assert check_tree(root) == []


def test_examples_citing_one_incident_twice_count_once(tmp_path):
    root = _tree(tmp_path)
    _cross_cites(root, ["INC-001", "INC-001"])
    assert _violations(root) == [(CROSS, "examples_count")]


def test_an_incident_cited_outside_examples_is_not_an_example(tmp_path):
    root = _tree(tmp_path)
    _cross_cites(root, ["INC-001"])
    _edit(root, CROSS, "A deleted resource that cannot be restored.", "See INC-004 and INC-005.")
    assert _violations(root) == [(CROSS, "examples_count")]


def test_examples_citing_an_id_that_is_not_in_the_dataset_fail(tmp_path):
    root = _tree(tmp_path)
    _edit(root, ALPHA, "- INC-003:", "- INC-099:")
    assert _violations(root) == [(ALPHA, "example_unknown_id:INC-099")]


def test_a_class_doc_citing_another_class_fails(tmp_path):
    root = _tree(tmp_path)
    _edit(root, ALPHA, "- INC-003:", "- INC-004:")
    assert _violations(root) == [(ALPHA, "example_wrong_class:INC-004")]


def test_a_cross_cutting_doc_may_cite_rows_of_any_class(tmp_path):
    root = _tree(tmp_path)
    text = (root / CROSS).read_text(encoding="utf-8")
    assert "scope: cross-cutting" in text and "INC-001" in text and "INC-004" in text
    assert check_tree(root) == []


def test_a_doc_loses_the_any_class_allowance_without_the_scope_field(tmp_path):
    root = _tree(tmp_path)
    _edit(root, CROSS, "scope: cross-cutting\n", "")
    assert _violations(root) == [
        (CROSS, "example_wrong_class:INC-001"),
        (CROSS, "example_wrong_class:INC-004"),
    ]


def test_a_scope_other_than_cross_cutting_fails(tmp_path):
    root = _tree(tmp_path)
    _edit(root, ALPHA, "applies_when:", "scope: local\napplies_when:")
    assert _violations(root) == [(ALPHA, "scope_invalid")]


def test_a_cross_cutting_doc_is_held_to_the_protection_rules(tmp_path):
    root = _tree(tmp_path)
    _edit(root, CROSS, "- **Breaks when:** the identity is shared\n", "")
    assert _violations(root) == [(CROSS, "rung_missing_breaks_when")]


# --- class docs: Protections --------------------------------------------------

FIRST_SOURCE = "- **Source:** [example source](https://example.com/practice)\n"
FIRST_COST = "- **Cost:** reviewer time, no figure\n"
FIRST_BREAKS = "- **Breaks when:** the diff is too large to read\n"
DATED_COST = "5 CI minutes per pull request (as of 2026-09, https://example.com/ci)"


def test_protections_without_a_rung_fail(tmp_path):
    root = _tree(tmp_path)
    _cut(root, ALPHA, "### Read the diff", "## Evidence")
    assert _violations(root) == [(ALPHA, "protections_no_rungs")]


def test_a_rung_without_a_source_fails(tmp_path):
    root = _tree(tmp_path)
    _edit(root, ALPHA, FIRST_SOURCE, "")
    assert _violations(root) == [(ALPHA, "rung_missing_source")]


def test_a_rung_with_an_empty_source_fails(tmp_path):
    root = _tree(tmp_path)
    _edit(root, ALPHA, FIRST_SOURCE, "- **Source:**\n")
    assert _violations(root) == [(ALPHA, "rung_missing_source")]


def test_the_label_one_operators_practice_counts_as_a_source(tmp_path):
    root = _tree(tmp_path)
    _edit(root, ALPHA, FIRST_SOURCE, "- **Source:** one operator's practice\n")
    assert check_tree(root) == []


def test_a_rung_without_a_cost_fails(tmp_path):
    root = _tree(tmp_path)
    _edit(root, ALPHA, FIRST_COST, "")
    assert _violations(root) == [(ALPHA, "rung_missing_cost")]


def test_a_rung_without_a_breaks_when_line_fails(tmp_path):
    root = _tree(tmp_path)
    _edit(root, ALPHA, FIRST_BREAKS, "")
    assert _violations(root) == [(ALPHA, "rung_missing_breaks_when")]


def test_rung_fields_are_found_without_bold_markers(tmp_path):
    root = _tree(tmp_path)
    _edit(root, ALPHA, "- **Cost:** reviewer time, no figure", "- Cost: reviewer time, no figure")
    assert check_tree(root) == []


@pytest.mark.parametrize(
    "cost",
    [
        "5 CI minutes per pull request",
        "5 CI minutes per pull request (as of 2026-9, https://example.com/ci)",
        "5 CI minutes per pull request (as of 2026-13, https://example.com/ci)",
        "5 CI minutes per pull request (as of 2026-09)",
        "5 CI minutes per pull request (as of 2026-09, )",
    ],
)
def test_a_cost_figure_without_a_dated_source_fails(tmp_path, cost):
    root = _tree(tmp_path)
    _edit(root, ALPHA, DATED_COST, cost)
    assert _violations(root) == [(ALPHA, "cost_figure_without_date")]


def test_a_cost_without_a_figure_needs_no_date(tmp_path):
    root = _tree(tmp_path)
    assert "no figure" in (root / ALPHA).read_text(encoding="utf-8")
    assert check_tree(root) == []


# --- class docs: size cap and links -------------------------------------------


def _pad_to(root: Path, rel: str, size: int) -> None:
    path = root / rel
    text = path.read_text(encoding="utf-8")
    pad = size - len(path.read_bytes())
    assert pad >= 0
    path.write_bytes((text + "x" * pad).encode("utf-8"))
    assert len(path.read_bytes()) == size


def test_a_doc_over_the_size_cap_fails(tmp_path):
    root = _tree(tmp_path)
    _pad_to(root, ALPHA, 30_001)
    assert _violations(root) == [(ALPHA, "doc_over_size_cap")]


def test_a_doc_exactly_at_the_size_cap_passes(tmp_path):
    root = _tree(tmp_path)
    _pad_to(root, ALPHA, 30_000)
    assert check_tree(root) == []


def test_the_size_cap_is_one_configured_value(tmp_path, monkeypatch):
    import structure_gate

    root = _tree(tmp_path)
    monkeypatch.setattr(structure_gate, "DOC_SIZE_CAP_BYTES", (root / CROSS).stat().st_size)
    assert _violations(root) == [(ALPHA, "doc_over_size_cap")]


def test_a_relative_link_that_resolves_to_no_file_fails(tmp_path):
    root = _tree(tmp_path)
    _edit(root, ALPHA, "(cross.md)", "(missing.md)")
    assert _violations(root) == [(ALPHA, "boundary_evidence_unresolvable")]


# --- dataset rows -------------------------------------------------------------

HEADER = "id,class,stage,source_type,evidence_strength,link"
COLUMNS = HEADER.split(",")
LAST_ROW = "INC-006,beta,after-merge,issue,primary,https://example.com/beta/3"


@pytest.mark.parametrize("column", COLUMNS)
def test_a_dataset_without_a_column_fails(tmp_path, column):
    root = _tree(tmp_path)
    renamed = ",".join(f"x_{c}" if c == column else c for c in COLUMNS)
    _edit(root, DATASET, HEADER, renamed)
    assert _violations(root) == [(DATASET, f"dataset_missing_column:{column}")]


def test_a_row_with_an_empty_cell_fails(tmp_path):
    root = _tree(tmp_path)
    _edit(root, DATASET, LAST_ROW, "INC-006,beta,after-merge,,primary,https://example.com/beta/3")
    assert _violations(root) == [(DATASET, "row_missing_value:source_type")]


def test_a_row_with_fewer_fields_than_the_header_fails(tmp_path):
    root = _tree(tmp_path)
    _edit(root, DATASET, LAST_ROW, "INC-006,beta,after-merge,issue")
    assert _violations(root) == [
        (DATASET, "row_missing_value:evidence_strength"),
        (DATASET, "row_missing_value:link"),
    ]


def test_a_repeated_id_fails(tmp_path):
    root = _tree(tmp_path)
    _edit(root, DATASET, "INC-006,beta,", "INC-005,beta,")
    assert _violations(root) == [(DATASET, "row_duplicate_id")]


def test_an_id_that_is_not_inc_and_digits_fails(tmp_path):
    root = _tree(tmp_path)
    _edit(root, DATASET, "INC-006,beta,", "INC-6,beta,")
    assert _violations(root) == [(DATASET, "row_bad_id")]


def test_a_stage_outside_the_vocabulary_fails(tmp_path):
    root = _tree(tmp_path)
    _edit(root, DATASET, "INC-001,alpha,review,", "INC-001,alpha,nowhere,")
    assert _violations(root) == [(DATASET, "row_stage_not_in_vocabulary")]


def test_an_evidence_strength_outside_the_vocabulary_fails(tmp_path):
    root = _tree(tmp_path)
    _edit(root, DATASET, "issue,private,", "issue,hearsay,")
    assert _violations(root) == [(DATASET, "row_evidence_strength_not_in_vocabulary")]


def test_a_row_that_no_class_doc_cites_passes(tmp_path):
    root = _tree(tmp_path)
    docs = [(root / ALPHA).read_text(encoding="utf-8"), (root / CROSS).read_text(encoding="utf-8")]
    assert not any("INC-006" in doc for doc in docs)
    assert LAST_ROW in (root / DATASET).read_text(encoding="utf-8")
    assert check_tree(root) == []


def test_a_quoted_cell_with_a_comma_is_one_cell(tmp_path):
    root = _tree(tmp_path)
    assert '"private, not verifiable"' in (root / DATASET).read_text(encoding="utf-8")
    assert check_tree(root) == []
