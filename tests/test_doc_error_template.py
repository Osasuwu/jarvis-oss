"""Checks for the doc-error issue template (#103): the four fields, which are required,
the applied label, and the public/no-personal-data notice."""

import re
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
TEMPLATE_PATH = REPO_ROOT / ".github" / "ISSUE_TEMPLATE" / "doc-error.yml"

_FIELD_RE = re.compile(
    r"id:\s*(\w+).*?required:\s*(true|false)", re.DOTALL
)


def _fields() -> dict[str, bool]:
    text = TEMPLATE_PATH.read_text(encoding="utf-8")
    blocks = text.split("- type:")[1:]
    fields: dict[str, bool] = {}
    for block in blocks:
        match = _FIELD_RE.search(block)
        if match:
            fields[match.group(1)] = match.group(2) == "true"
    return fields


def test_template_exists():
    assert TEMPLATE_PATH.is_file()


def test_template_applies_doc_error_label():
    text = TEMPLATE_PATH.read_text(encoding="utf-8")
    labels_line = next(line for line in text.splitlines() if line.startswith("labels:"))
    assert "doc-error" in labels_line


def test_template_has_four_fields_with_required_three():
    fields = _fields()
    assert set(fields) == {"doc", "quote", "problem", "source"}
    assert fields["doc"] is True
    assert fields["quote"] is True
    assert fields["problem"] is True
    assert fields["source"] is False


def test_template_says_public_and_no_personal_data():
    text = TEMPLATE_PATH.read_text(encoding="utf-8").lower()
    assert "public" in text
    assert "personal data" in text
