"""Contract test for the calibration rules (#96).

The rules decide how an escaped defect is labelled and counted. They are written before the corpus
exists so they cannot be fitted to its results. These are the lines that carry that; an edit that
drops one fails the build.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SKILL_DIR = ROOT / ".agents" / "skills" / "review-doc"
CALIBRATION_DIR = SKILL_DIR / "calibration"
RULES = CALIBRATION_DIR / "RULES.md"
CORPUS = CALIBRATION_DIR / "corpus.md"
DRAW_SCRIPT = CALIBRATION_DIR / "draw_click_audit.py"

CLASSES = ("status", "quote", "plan", "fact", "dead-end", "missing-option", "how-to-choose", "other")


def _section(title: str) -> str:
    text = RULES.read_text(encoding="utf-8")
    match = re.search(rf"^## {re.escape(title)}\n(.*?)(?=^## |\Z)", text, re.M | re.S)
    assert match, f"missing section: ## {title}"
    return " ".join(match.group(1).split())


# --- where the file sits ----------------------------------------------------------------------


def test_rules_sit_outside_the_hashed_skill_file():
    # The drift key hashes SKILL.md only. The rules live beside it, not in it.
    assert RULES.is_file()
    assert RULES.resolve() != (SKILL_DIR / "SKILL.md").resolve()
    assert RULES.parent == CALIBRATION_DIR


def _git(*args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(ROOT), *args], capture_output=True, text=True, check=True
    ).stdout.strip()


def _first_commit(path: Path) -> str | None:
    rel = path.relative_to(ROOT).as_posix()
    shas = _git("log", "--diff-filter=A", "--format=%H", "--", rel).splitlines()
    return shas[-1] if shas else None


def test_rules_were_committed_before_the_corpus():
    try:
        shallow = _git("rev-parse", "--is-shallow-repository") == "true"
    except (subprocess.CalledProcessError, FileNotFoundError):
        pytest.skip("no git history available")
    if shallow:
        pytest.skip("shallow clone: the commit order cannot be read")
    rules_commit = _first_commit(RULES)
    corpus_commit = _first_commit(CORPUS)
    if corpus_commit is None:
        return  # no corpus yet: the order holds trivially
    assert rules_commit is not None, "the corpus exists but the rules were never committed"
    assert rules_commit != corpus_commit, "rules and corpus were added in the same commit"
    ancestor = subprocess.run(
        ["git", "-C", str(ROOT), "merge-base", "--is-ancestor", rules_commit, corpus_commit]
    )
    assert ancestor.returncode == 0, "the corpus was committed before the rules"


# --- label rules ------------------------------------------------------------------------------


def test_every_defect_class_is_defined():
    section = _section("Defect classes")
    for name in CLASSES:
        assert f"`{name}`" in section, name


def test_blocking_is_defined_exactly():
    section = _section("Blocking or follow-up")
    assert (
        "the doc leads an in-scope reader type to an option that (i) is unavailable on that "
        "reader's plan, (ii) contradicts a quoted source, or (iii) leaves the reader with no "
        "next step" in section
    )
    assert "every claim-check `mismatch`" in section
    assert "every wrong `tried` / `sourced` status" in section
    assert "A `missing` option is a follow-up unless it triggers (iii)." in section


def test_fallback_line_rule_exists():
    section = _section("When a fallback line satisfies (iii)")
    assert "satisfies (iii) only if" in section


def test_unverifiable_has_its_own_rule():
    section = _section("`unverifiable`")
    assert "load-bearing" in section
    assert "`blocking`" in section and "`follow-up`" in section


# --- held-out, escaped, selection source ------------------------------------------------------


def test_held_out_rule_states_how_an_entry_is_flagged():
    section = _section("Held-out")
    assert "`held_out: yes`" in section
    assert "`held_out_by:`" in section
    assert "excluded from the published counts" in section


def test_escaped_is_defined():
    assert "found in round N+1 in text unchanged since round N" in _section("Escaped")


def test_selection_source_has_three_values():
    section = _section("Selection source")
    for value in ("`model`", "`click-audit`", "`reader`"):
        assert value in section, value


# --- click-audit ------------------------------------------------------------------------------


def test_click_audit_procedure_states_k_draw_human_step_and_record():
    section = _section("Click-audit")
    assert "k = 5" in section
    assert "all of them if there are fewer" in section
    assert "seeded with the PR head SHA" in section
    assert "draw_click_audit.py" in section and DRAW_SCRIPT.is_file()
    assert "opens each source link" in section
    assert "selection source `click-audit`" in section
    assert "k is revisited after the first audited miss" in section


def test_draw_script_default_k_matches_the_rules():
    import importlib.util
    import sys

    spec = importlib.util.spec_from_file_location("draw_click_audit_k", DRAW_SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # dataclasses look the module up by name
    spec.loader.exec_module(module)
    assert f"k = {module.DEFAULT_K}" in _section("Click-audit")
