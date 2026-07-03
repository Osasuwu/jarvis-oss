"""Pure axis-substitution renderer for canon file templates.

Templates use ``{{ axis_name }}`` placeholders (Jinja-free — str.replace).
Unknown or missing axes that are not in ``OPTIONAL_AXES`` are a hard error.
"""

from __future__ import annotations

import re
from typing import Any

from .manifest import Manifest

_PATTERN = re.compile(r"\{\{\s*(\w+)\s*\}\}")

# Axes that MAY be omitted (template silently skips them).
_OPTIONAL_AXES: set[str] = {
    "test_extras",  # empty-string OK — no extra install
}


class RenderError(ValueError):
    """Raised when a template references an unknown or missing axis."""


class Renderer:
    """Pure axis-substitution renderer.

    Usage::

        renderer = Renderer()
        result = renderer.render(template_text, manifest)
    """

    def resolve(self, key: str, manifest: Manifest) -> Any:
        """Resolve a single axis name to its value."""
        return manifest.resolve_axis(key)

    def _format(self, key: str, value: Any) -> str:
        """Format a resolved axis value for template insertion.

        Lists are rendered as inline YAML arrays ``[a, b]`` to avoid
        indentation-dependent block-sequence issues in line-level
        substitutions.
        """
        if isinstance(value, bool):
            return str(value).lower()
        if isinstance(value, list):
            if not value:
                return "[]"
            parts = ", ".join(str(v) for v in value)
            return f"[{parts}]"
        return str(value) if value is not None else ""

    def render(self, template: str, manifest: Manifest) -> str:
        """Substitute ``{{ axis }}`` placeholders in *template*.

        Raises ``RenderError`` for unknown or missing mandatory axes.
        """
        def _sub(m: re.Match) -> str:
            key = m.group(1)
            val = self.resolve(key, manifest)
            if val is None and key not in _OPTIONAL_AXES:
                raise RenderError(
                    f"Axis '{key}' is required but has no value in manifest "
                    f"(profile={manifest.profile!r})"
                )
            return self._format(key, val)

        result = _PATTERN.sub(_sub, template)
        return result
