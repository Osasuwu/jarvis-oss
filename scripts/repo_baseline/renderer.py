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

# Axes appended to a shell command rather than assigned to a YAML key. They
# carry their own separating space so an unset axis leaves no trailing
# whitespace behind — the template writes ``pytest ...{{ axis }}``, not
# ``pytest ... {{ axis }}`` (#1406). A YAML-array format would be wrong here:
# the value is argv, not a sequence.
_SHELL_ARG_AXES: set[str] = {
    "ci_meta_pytest_args",
}


class RenderError(ValueError):
    """Raised when a template references an unknown or missing axis."""


class Renderer:
    """Pure axis-substitution renderer.

    Usage::

        renderer = Renderer()
        result = renderer.render(template_text, manifest)
    """

    def resolve(self, key: str, manifest: Manifest, overrides: dict[str, Any] | None = None) -> Any:
        """Resolve a single axis name to its value.

        *overrides* carries the **observed** axes — facts read from the live
        audit rather than declared in a manifest (``runs_on``,
        ``default_branch``; #1406). They win outright: a manifest cannot
        declare them, so there is nothing to arbitrate against.
        """
        if overrides and key in overrides:
            return overrides[key]
        return manifest.resolve_axis(key)

    def _format(self, key: str, value: Any) -> str:
        """Format a resolved axis value for template insertion.

        Lists are rendered as inline YAML arrays ``[a, b]`` to avoid
        indentation-dependent block-sequence issues in line-level
        substitutions. Axes in ``_SHELL_ARG_AXES`` are the exception — see
        that set's comment.
        """
        if key in _SHELL_ARG_AXES:
            text = str(value or "").strip()
            return f" {text}" if text else ""
        if isinstance(value, bool):
            return str(value).lower()
        if isinstance(value, list):
            if not value:
                return "[]"
            parts = ", ".join(str(v) for v in value)
            return f"[{parts}]"
        return str(value) if value is not None else ""

    def render(
        self, template: str, manifest: Manifest, overrides: dict[str, Any] | None = None
    ) -> str:
        """Substitute ``{{ axis }}`` placeholders in *template*.

        Raises ``RenderError`` for unknown or missing mandatory axes.
        """

        def _sub(m: re.Match) -> str:
            key = m.group(1)
            val = self.resolve(key, manifest, overrides)
            if val is None and key not in _OPTIONAL_AXES:
                raise RenderError(
                    f"Axis '{key}' is required but has no value in manifest "
                    f"(profile={manifest.profile!r})"
                )
            return self._format(key, val)

        result = _PATTERN.sub(_sub, template)
        return result
