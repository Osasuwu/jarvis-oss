"""Pure-function renderer for memory review queue rows.

Takes a row from ``memory_review_list`` (or ``memory_review_queue``) plus
optional pre-fetched context and returns a formatted Markdown display block.
No DB calls inside — every input is passed as argument.

Display formats per row type:

  - **classifier UPDATE** — before/after diff (requires ``context`` with
    ``before_snapshot`` and ``decision='UPDATE'``).
  - **classifier ADD / DELETE / NOOP** — compact card.
  - **candidate** (not classifier) — compact card.
  - **merge proposal** — merge card showing target count.
"""

from __future__ import annotations

import difflib


def render_proposal(row: dict, context: dict | None = None) -> str:
    """Render one review-queue row as a Markdown display block.

    Parameters
    ----------
    row:
        A dict from ``memory_review_list`` or ``memory_review_queue``.
        Expected keys: ``id``, ``name``, ``type``, ``description``,
        ``content``, ``tags``, ``source_provenance``, ``requires_review``,
        ``merge_targets``, ``reject_reason``, ``created_at``.
    context:
        Optional pre-fetched context. Supported keys:

        - ``decision`` (``'UPDATE'`` | ``'ADD'`` | ``'DELETE'`` |
          ``'NOOP'``) — classifier decision type. When ``'UPDATE'`` and
          ``before_snapshot`` is present, renders a diff.
        - ``before_snapshot`` (``dict``) — snapshot of the target memory
          *before* the proposed change. Must have ``description``,
          ``content``, ``tags`` keys at minimum.
        - ``reasoning`` (``str``) — classifier reasoning to display.
    """
    source_prov = row.get("source_provenance") or ""
    is_classifier = source_prov.startswith("classifier:")
    has_merge_targets = bool(row.get("merge_targets"))

    decision = (context or {}).get("decision", "")
    before = (context or {}).get("before_snapshot")

    # merge_targets is a meta-row property — when present, the consolidation
    # intent is the most important thing for the reviewer to see, regardless
    # of whether the row also carries a classifier:* provenance. Route to the
    # merge card before any classifier short-circuit so compound rows
    # (e.g. classifier:add:* with merge_targets) don't silently drop the targets.
    if has_merge_targets:
        return _render_merge(row)
    if is_classifier and decision == "UPDATE" and before:
        return _render_diff_block(row, before, context)
    if is_classifier:
        return _render_compact(row, label=decision or "classifier", context=context)
    return _render_compact(row, label="candidate", context=context)


# ---------------------------------------------------------------------------
# UPDATE diff
# ---------------------------------------------------------------------------


def _render_diff_block(row: dict, before: dict, context: dict | None = None) -> str:
    """Render before/after diff for a classifier UPDATE proposal."""
    name = row.get("name", "?")
    mtype = row.get("type", "?")

    before_desc = (before.get("description") or "").strip()
    after_desc = (row.get("description") or "").strip()
    before_content = (before.get("content") or "").strip()
    after_content = (row.get("content") or "").strip()
    before_tags = set(before.get("tags") or [])
    after_tags = set(row.get("tags") or [])

    lines = [f"### {name} ({mtype}) — UPDATE"]

    # Description diff
    if before_desc != after_desc:
        lines.append("")
        lines.append("**Description:**")
        d = _simple_diff(before_desc, after_desc, "description")
        if d:
            lines.append(d)

    # Content diff
    if before_content != after_content:
        lines.append("")
        lines.append("**Content:**")
        d = _simple_diff(before_content, after_content, "content")
        if d:
            lines.append(d)

    # Tag diff
    added = after_tags - before_tags
    removed = before_tags - after_tags
    if added or removed:
        lines.append("")
        lines.append("**Tags:**")
        if added:
            lines.append(f"  + {', '.join(sorted(added))}")
        if removed:
            lines.append(f"  - {', '.join(sorted(removed))}")

    reasoning = (context or {}).get("reasoning", "")
    if reasoning:
        lines.append("")
        lines.append(f"**Reasoning:** {reasoning[:500]}")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Compact card (ADD / DELETE / NOOP / candidate / merge)
# ---------------------------------------------------------------------------


def _render_compact(row: dict, label: str, context: dict | None = None) -> str:
    """Render a compact card."""
    name = row.get("name", "?")
    mtype = row.get("type", "?")
    desc = (row.get("description") or "").strip()
    content = (row.get("content") or "").strip()
    tags = row.get("tags") or []

    header_label = label.upper() if label else "ITEM"
    lines = [f"### {name} ({mtype}) — {header_label}"]

    reject = row.get("reject_reason")
    if reject:
        lines.append("")
        lines.append(f"*Previously rejected: {reject[:300]}*")

    reasoning = (context or {}).get("reasoning", "")
    if reasoning:
        lines.append("")
        lines.append(f"**Reasoning:** {reasoning[:500]}")

    if desc:
        lines.append("")
        lines.append(f"**Description:** {desc}")

    if content:
        lines.append("")
        lines.append(f"**Content:** {content[:600]}{'…' if len(content) > 600 else ''}")

    if tags:
        lines.append("")
        lines.append(f"**Tags:** {', '.join(tags)}")

    lines.append("")
    lines.append(f"**Provenance:** {row.get('source_provenance', '—')}")
    lines.append(f"**ID:** `{row.get('id', '?')}`")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Merge proposal
# ---------------------------------------------------------------------------


def _render_merge(row: dict) -> str:
    """Render a merge proposal card."""
    name = row.get("name", "?")
    targets = row.get("merge_targets") or []
    desc = (row.get("description") or "").strip()
    lines = [f"### {name} — MERGE ({len(targets)} targets)"]

    if desc:
        lines.append("")
        lines.append(f"**Description:** {desc}")

    lines.append("")
    lines.append("**Merge targets:**")
    for t in targets:
        lines.append(f"  - `{t}`")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Diff utility
# ---------------------------------------------------------------------------


def _simple_diff(before: str, after: str, context: str = "") -> str:
    """Compute a concise unified diff between two strings.

    Returns a Markdown ``diff`` code block, or empty string if the strings
    are identical (ignoring leading/trailing whitespace).
    """
    if before.strip() == after.strip():
        return ""

    before_lines = before.splitlines(keepends=True)
    after_lines = after.splitlines(keepends=True)

    diff = list(
        difflib.unified_diff(
            before_lines,
            after_lines,
            fromfile=f"before/{context}",
            tofile=f"after/{context}",
            n=2,
        )
    )
    if not diff:
        return ""

    # Skip --- and +++ header lines; keep @@ hunk headers so multi-hunk diffs
    # don't silently concatenate disjoint sections.
    body_lines = list(diff[2:])
    body = "".join(body_lines).strip()
    return f"```diff\n{body}\n```" if body else ""


# ---------------------------------------------------------------------------
# Convenience: render a list of proposals
# ---------------------------------------------------------------------------


def render_proposal_list(
    rows: list[dict],
    contexts: list[dict | None] | None = None,
) -> str:
    """Render multiple proposals as a single display block.

    Each row is separated by ``---``. If ``contexts`` is provided it must
    be parallel to ``rows``.
    """
    if not rows:
        return "_No pending proposals to review._"

    if contexts is None:
        contexts = [None] * len(rows)

    if len(contexts) != len(rows):
        raise ValueError(
            f"render_proposal_list: contexts length {len(contexts)} != rows length {len(rows)}"
        )

    blocks: list[str] = []
    for i, (row, ctx) in enumerate(zip(rows, contexts)):
        blocks.append(render_proposal(row, ctx))
    return "\n\n---\n\n".join(blocks)
