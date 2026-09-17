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
