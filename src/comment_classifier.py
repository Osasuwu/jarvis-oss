"""comment_classifier — keep/remove judge for the deslop pipeline.

Interface::

    classify(comment, context=None) -> str

Returns one of ``remove``, ``keep_why``, ``keep_external``, ``keep_warning``,
``keep_unsure``.

Rules encoded (from the deslop standard in ``docs/deslop-standard.md``):

1. Only ``remove`` deletes.
2. Safety comments (fail-open, fail-closed, guardrail) → ``keep_warning``.
3. External-fact comments (URL, wire format, upstream quirk) → ``keep_external``.
4. A comment citing an Issue/PR/ADR number is always kept (interim v1:
   distinguishing "pure restate" from "restate with added context" needs
   semantic judgment this rule-based classifier doesn't attempt — deferred
   to the LLM-judge follow-up; see `docs/deslop-standard.md` rule 5).
5. A comment that explains *why* (not *what*) → ``keep_why``.
6. A pure restatement of obvious code → ``remove``.
7. When unsure → ``keep_unsure``.
"""

from __future__ import annotations

import re

__all__ = ["classify", "ClassifierContext"]


# ---------------------------------------------------------------------------
# Safety patterns — rule 2
# ---------------------------------------------------------------------------

_SAFETY_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"fail[-\s]?open", re.IGNORECASE),
    re.compile(r"fail[-\s]?closed?", re.IGNORECASE),
    re.compile(r"\bguardrail\b", re.IGNORECASE),
    re.compile(r"\b(?:swallow|mask)(?:s|ed|ing)?\b.{0,40}\berror\b", re.IGNORECASE),
    re.compile(r"\berror\b.{0,40}\b(?:swallow|mask)(?:s|ed|ing)?\b", re.IGNORECASE),
]

# ---------------------------------------------------------------------------
# External-fact patterns — rule 3
# ---------------------------------------------------------------------------

_EXTERNAL_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"https?://\S+"),
    re.compile(r"\bwire format\b", re.IGNORECASE),
    re.compile(r"\bupstream\b", re.IGNORECASE),
    re.compile(r"\bthird.party\b", re.IGNORECASE),
]

# ---------------------------------------------------------------------------
# WHY-indicator patterns — rule 5
# ---------------------------------------------------------------------------

_WHY_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"\bbecause\b", re.IGNORECASE),
    re.compile(r"\bso that\b", re.IGNORECASE),
    re.compile(r"\botherwise\b", re.IGNORECASE),
    re.compile(r"\bensure\b", re.IGNORECASE),
    re.compile(r"\bprevent\b", re.IGNORECASE),
    re.compile(r"\binvariant\b", re.IGNORECASE),
    re.compile(r"\bmust\b", re.IGNORECASE),
    re.compile(r"\bnever\b", re.IGNORECASE),
]

# ---------------------------------------------------------------------------
# Restate indicators — a comment that simply restates obvious code
# ---------------------------------------------------------------------------

_RESTATE_PATTERNS: list[re.Pattern[str]] = [
    re.compile(
        r"^(filter|check|get|set|create|update|delete|find|load|save|"
        r"parse|format|validate|convert|build|run|call|initialize|configure|"
        r"register|import|export|iterate|loop|increment|decrement|count|sum|"
        r"compute|calculate|extract|transform|map|reduce)s?\b",
        re.IGNORECASE,
    ),
]

# ---------------------------------------------------------------------------
# Traceability
# ---------------------------------------------------------------------------

_TRACEABILITY_RE = re.compile(r"#[0-9]+")


class ClassifierContext:
    """Context surrounding a comment candidate.

    Parameters
    ----------
    file_path:
        Path to the file containing the comment.
    preceding_code:
        Source code immediately before the comment.
    following_code:
        Source code immediately after the comment.
    """

    def __init__(
        self,
        file_path: str = "",
        preceding_code: str = "",
        following_code: str = "",
    ):
        self.file_path = file_path
        self.preceding_code = preceding_code
        self.following_code = following_code


def classify(
    comment: str,
    context: ClassifierContext | None = None,
) -> str:
    """Classify *comment* into one of the five dispositions.

    Parameters
    ----------
    comment:
        The comment text to classify. May include ``#`` prefix.
    context:
        Optional context surrounding the comment.

    Returns
    -------
    One of ``"remove"``, ``"keep_why"``, ``"keep_external"``,
    ``"keep_warning"``, ``"keep_unsure"``.
    """
    cleaned = _clean_comment(comment)

    # Priority 1: Safety comments → keep_warning
    if _any_match(_SAFETY_PATTERNS, cleaned):
        return "keep_warning"

    # Priority 2: External-fact comments → keep_external
    if _any_match(_EXTERNAL_PATTERNS, cleaned):
        return "keep_external"

    # Priority 3: Traceability reference — always kept (interim v1, rule 4)
    has_traceability = bool(_TRACEABILITY_RE.search(cleaned))

    # Priority 4: WHY explanation → keep_why
    if _any_match(_WHY_PATTERNS, cleaned):
        return "keep_why"

    # Priority 5: Pure restate → remove (unless traceability ref)
    if _any_match(_RESTATE_PATTERNS, cleaned) and not has_traceability:
        return "remove"

    # Priority 6: Has traceability but no other signal → keep_unsure (conservative)
    if has_traceability:
        return "keep_unsure"

    # Priority 7: Default → keep_unsure
    return "keep_unsure"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _clean_comment(comment: str) -> str:
    """Strip ``#`` prefixes and whitespace from *comment*.

    Joins multi-line comments into a single space-joined string so pattern
    matching works across line boundaries.
    """
    lines = comment.splitlines()
    cleaned: list[str] = []
    for line in lines:
        s = line.strip()
        if s.startswith("#"):
            s = s[1:].strip()
        if s:
            cleaned.append(s)
    return " ".join(cleaned)


def _any_match(patterns: list[re.Pattern[str]], text: str) -> bool:
    """Return ``True`` if any of *patterns* match *text*."""
    return any(p.search(text) for p in patterns)
