"""Contract test for the write-doc skill (#64).

The skill gives writers a default shape for a practice doc. It must stay a default: a template
that reads as a form pushes writers to fill headings instead of serving the reader.
"""

from pathlib import Path

SKILL = Path(__file__).parent.parent / ".agents" / "skills" / "write-doc" / "SKILL.md"


def _skill_text() -> str:
    return SKILL.read_text(encoding="utf-8")


def test_skill_names_itself():
    text = _skill_text()
    assert text.startswith("---\n")
    assert "name: write-doc" in text


def test_shape_is_a_default_not_a_form():
    text = _skill_text()
    assert "## This is a default, not a form" in text
    assert "say what and why in the PR" in text


def test_how_to_choose_does_not_collapse_to_one_answer():
    assert "Do not collapse the choice to one answer." in _skill_text()


def test_options_are_searched_blind_before_they_are_written():
    text = _skill_text()
    assert "Search before you write this section, and search blind." in text
    assert "Put the queries in the PR." in text


def test_how_to_choose_filters_are_copied_from_each_option():
    text = _skill_text()
    assert "- **Fits only if**" in text
    assert "by copying each option's *fits only if* line" in text


def test_writer_checks_before_review_and_fixes_from_the_source():
    text = _skill_text()
    assert "## Before review" in text
    assert "python scripts/check_quotes.py" in text
    assert (Path(__file__).parent.parent / "scripts" / "check_quotes.py").is_file()
    assert "**Scope words.**" in text
    assert "**`tried` needs a trace**" in text
    assert "## When review finds something" in text
    assert "Fetch the source yourself" in text
