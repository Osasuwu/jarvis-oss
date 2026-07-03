"""Canonical file set — templates with ``{{ axis }}`` placeholders.

Each template is a text file in this directory. Use ``load_canon_template()``
to load by path, or ``load_all_canon_templates()`` to get the full mapping.
"""

from __future__ import annotations

import os
from typing import Dict, Optional

_CANON_DIR = os.path.dirname(os.path.abspath(__file__))

# Mapping: repo-relative path → template filename
_CANON_MAP: Dict[str, str] = {
    ".github/workflows/code-review.yml": "code-review.yml",
    ".github/workflows/owner-queue-guard.yml": "owner-queue-guard.yml",
    ".github/workflows/pr-body-check.yml": "pr-body-check.yml",
    ".github/workflows/ci-meta.yml": "ci-meta.yml",
    ".github/dependabot.yml": "dependabot.yml",
    ".github/ISSUE_TEMPLATE/bug.yml": "bug.yml",
    ".github/ISSUE_TEMPLATE/task.yml": "task.yml",
    ".github/ISSUE_TEMPLATE/config.yml": "config.yml",
    ".github/PULL_REQUEST_TEMPLATE.md": "PULL_REQUEST_TEMPLATE.md",
}


def load_canon_template(path: str) -> Optional[str]:
    """Load a canon template by its repo-relative path.

    Returns the template text, or ``None`` if no canon template exists for
    that path.
    """
    filename = _CANON_MAP.get(path)
    if filename is None:
        return None
    filepath = os.path.join(_CANON_DIR, filename)
    try:
        with open(filepath, encoding="utf-8") as f:
            return f.read()
    except FileNotFoundError:
        return None


def load_all_canon_templates() -> Dict[str, str]:
    """Load all canon templates as ``{path: template_text}``."""
    result: Dict[str, str] = {}
    for path in _CANON_MAP:
        text = load_canon_template(path)
        if text is not None:
            result[path] = text
    return result
