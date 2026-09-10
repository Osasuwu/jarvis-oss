"""Pure decision core for the `/weekly-release` capability (#1572).

Everything here is a pure function over plain dicts/dataclasses — no
network, filesystem, or `gh` calls. The I/O adapter (window collection via
`gh`, repos.conf traversal, GitHub releases API reads) lives in
`scripts/weekly_release_gather.py`, mirroring the morning_engine.py /
morning_gather.py split (#1586).

Covers: conventional-commit parsing, release-worthiness, semver
classification, the fact-anchoring linter, goal-section format selection,
draft-aware anchor window computation, and trust-ramp state.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# -- Conventional-commit parsing --------------------------------------------

_CONVENTIONAL_RE = re.compile(
    r"^(?P<type>[a-zA-Z]+)(\((?P<scope>[^)]*)\))?(?P<breaking>!)?:\s*(?P<desc>.*)$"
)


@dataclass(frozen=True)
class ConventionalCommit:
    type: str
    scope: str | None
    breaking: bool
    desc: str


def parse_conventional_type(title: str) -> ConventionalCommit:
    """Parse a PR/commit title's conventional-commit prefix.

    Titles that don't match the convention parse as type="" (mechanical
    classification below treats an unknown type as non-mechanical, so an
    unparseable title never silently suppresses a release).
    """
    m = _CONVENTIONAL_RE.match(title.strip())
    if not m:
        return ConventionalCommit(type="", scope=None, breaking=False, desc=title.strip())
    return ConventionalCommit(
        type=m.group("type").lower(),
        scope=(m.group("scope") or None),
        breaking=bool(m.group("breaking")),
        desc=m.group("desc"),
    )


# -- Release-worthiness (AC4) -------------------------------------------------

# ceiling: fixed mechanical-type set, not learned from labels; matches the
# issue's own examples (dep-bumps, CI fixes). Extend here if a new mechanical
# category shows up in a false-positive "release" during trust-ramp review.
_MECHANICAL_TYPES = {"chore", "ci", "build", "test", "docs"}


def _is_mechanical(entry: dict) -> bool:
    if entry.get("is_dependabot"):
        return True
    c = parse_conventional_type(entry["title"])
    if c.type in _MECHANICAL_TYPES:
        return True
    # A `fix` scoped to `ci` is still a CI-fix per the issue's own framing
    # ("Неделя из dep-bump'ов и CI-фиксов -> релиза нет"), not user-facing.
    if c.type == "fix" and c.scope == "ci":
        return True
    return False


def is_release_worthy(entries: list[dict]) -> bool:
    """A window is release-worthy iff it contains at least one non-mechanical
    entry (not a dependency bump, not a CI-only fix)."""
    return any(not _is_mechanical(e) for e in entries)


# -- Semver classification (AC5) ---------------------------------------------


def classify_bump(entries: list[dict]) -> str:
    """ "patch" if the window is fixes-only, "minor" if it contains a new
    capability (any `feat` entry). Never "major" — decision 5b811a25:
    there is no breaking-change signal in any form; trust ramp is the only
    human gate, so a `!` marker or a `BREAKING CHANGE:` body never escalates
    the bump beyond minor."""
    for e in entries:
        c = parse_conventional_type(e["title"])
        if c.type == "feat":
            return "minor"
    return "patch"


# -- Fact-anchoring linter (AC6) ---------------------------------------------

_PR_REF_RE = re.compile(r"#(\d+)")
_BARE_NUMBER_RE = re.compile(r"\d")
# #1668: structural lines carry no factual claim of their own, so the
# citation gate must skip them rather than flag every heading/blank line as
# an uncited claim. Bare tags only (`<details>`, `</details>`, `<summary>`,
# `</summary>`) — a tag WITH inline content (`<summary>3 fixes</summary>`)
# still carries a claim and must still be linted normally.
_BLANK_LINE_RE = re.compile(r"^\s*$")
_ATX_HEADER_RE = re.compile(r"^#{1,6}\s")
_BARE_HTML_TAG_RE = re.compile(r"^\s*</?(?:details|summary)>\s*$", re.IGNORECASE)


def _is_structural_line(line: str) -> bool:
    return bool(
        _BLANK_LINE_RE.match(line) or _ATX_HEADER_RE.match(line) or _BARE_HTML_TAG_RE.match(line)
    )


def _has_quantitative_claim(line_without_refs: str) -> bool:
    return bool(_BARE_NUMBER_RE.search(line_without_refs))


def lint_release_notes(lines: list[str], window_refs: set[str]) -> list[str]:
    """Language-independent citation gate: every note line must cite >=1
    PR/issue number that is actually in the collected window; a line with a
    quantitative claim (any digit outside of a `#NNN` citation) but no valid
    citation is rejected with a more specific message. Replaces a
    banned-word list (per issue text) — it checks provenance, not phrasing.

    #1668: structural lines (blank lines, ATX headers like `## Осталось`,
    bare `<details>`/`</details>`/`<summary>`/`</summary>` tags) are skipped
    entirely — they carry no fact to cite, so treating them as uncited
    claims broke every multi-section/`<details>`-wrapped body. A tag with
    inline content is NOT exempt and is still linted normally.

    Returns a list of violation messages; empty list == the notes pass.
    """
    violations = []
    for i, line in enumerate(lines):
        if _is_structural_line(line):
            continue
        refs = _PR_REF_RE.findall(line)
        valid_refs = [r for r in refs if r in window_refs]
        if valid_refs:
            continue
        stripped = _PR_REF_RE.sub("", line)
        if _has_quantitative_claim(stripped):
            violations.append(
                f"line {i}: quantitative claim without a source citation from the window: {line!r}"
            )
        else:
            violations.append(f"line {i}: missing PR/issue citation from the window: {line!r}")
    return violations


# -- Goal-section format selection (AC8) -------------------------------------


def format_goal_section(goals: list[dict]) -> str:
    """0/1 active goal (with movement) -> narrative prose; >=2 -> a
    🎯-anchored split, one bullet per goal. Goals with no movement in the
    window (`no_movement=True`) are omitted entirely from either format —
    the section is a progress note, not a goal inventory."""
    moved = [g for g in goals if not g.get("no_movement")]
    if not moved:
        return ""
    if len(moved) <= 1:
        g = moved[0]
        progress = g.get("progress_note") or g.get("title", "")
        return f"This week's focus was **{g.get('title', g.get('slug', ''))}**: {progress}"
    lines = ["## 🎯 Goals"]
    for g in moved:
        note = g.get("progress_note") or ""
        lines.append(f"- 🎯 **{g.get('title', g.get('slug', ''))}** — {note}".rstrip(" —"))
    return "\n".join(lines)


# -- Goal-movement derivation (#1669) ------------------------------------------

# `goal_list()` (formerly backed by the `mcp__memory__goal_list` tool,
# retired in #1801) returns rendered markdown, not structured data - one
# `## <title>` block per goal, a `Slug: `<slug>`` line,
# and a `**Progress (N%):**` checklist whose done items end in a
# `(YYYY-MM-DD)` completion date. This module has no MCP access (pure core,
# no I/O per the module docstring), so the caller passes the raw markdown
# string through unchanged; parsing it into format_goal_section()'s
# list[dict] input is itself pure text processing and belongs here.
_GOAL_SLUG_RE = re.compile(r"Slug:\s*`([^`]+)`")
_GOAL_PROGRESS_BULLET_RE = re.compile(
    r"^- \[(x| )\]\s*(.+?)(?:\s*\((\d{4}-\d{2}-\d{2})\))?\s*$", re.MULTILINE
)


def extract_goal_movements(
    goal_list_markdown: str, window_start: str, window_end: str
) -> list[dict]:
    """Parse `goal_list()`'s raw markdown into format_goal_section()'s input:
    one dict per goal, `{"slug", "title", "progress_note"}` when at least one
    checked Progress bullet carries a completion date inside
    `[window_start, window_end)`, otherwise `{"slug", "title",
    "no_movement": True}`. A goal with no `Slug:` line (malformed/unexpected
    block) is skipped rather than guessed at - a missing citation is better
    than a wrong one."""
    from datetime import datetime

    def _parse_date(d: str) -> datetime:
        return datetime.fromisoformat(d).replace(tzinfo=None)

    def _parse_bound(s: str) -> datetime:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).replace(tzinfo=None)

    start_dt = _parse_bound(window_start)
    end_dt = _parse_bound(window_end)

    movements: list[dict] = []
    blocks = goal_list_markdown.split("\n## ")[1:]  # drop the "# Goals (N)" preamble
    for block in blocks:
        header_line, _, rest = block.partition("\n")
        title = header_line.strip()
        slug_m = _GOAL_SLUG_RE.search(rest)
        if not slug_m:
            continue
        slug = slug_m.group(1)

        notes_in_window: list[str] = []
        for bullet_m in _GOAL_PROGRESS_BULLET_RE.finditer(rest):
            checked, text, date_str = bullet_m.groups()
            if checked != "x" or not date_str:
                continue
            try:
                bullet_dt = _parse_date(date_str)
            except ValueError:
                continue
            if start_dt <= bullet_dt < end_dt:
                notes_in_window.append(text.strip())

        if notes_in_window:
            movements.append(
                {"slug": slug, "title": title, "progress_note": "; ".join(notes_in_window)}
            )
        else:
            movements.append({"slug": slug, "title": title, "no_movement": True})
    return movements


# -- Retraction section (#1659 AC1/AC2) ---------------------------------------


def format_retraction_section(retractions: list[dict]) -> str:
    """Empty `retractions` -> "" (AC2: the section is present only when
    non-empty). Otherwise a `## ⏪ Отозвано` heading with one bullet per
    retraction, each citing both the original PR/issue and the reverting PR
    (`{"original_ref", "revert_ref", "title"}`, as produced by
    weekly_release_gather.py's Reverts #N extraction). Citation-correct by
    construction — never passed through lint_release_notes, mirroring
    format_goal_section's own exemption for the same reason (a heading line
    carries no digit citation of its own)."""
    if not retractions:
        return ""
    lines = ["## ⏪ Отозвано"]
    for r in retractions:
        lines.append(f"- #{r['original_ref']} отозвано в #{r['revert_ref']}: {r['title']}")
    return "\n".join(lines)


# -- Window-truncation disclosure (#1668) -------------------------------------


def format_window_disclosure(window_start: str, window_end: str, truncated: bool) -> str:
    """ "" when `truncated` is False (AC: disclosure only when the window was
    actually capped). Otherwise a one-line disclosure of the covered period,
    structural like format_retraction_section's heading — never passed
    through lint_release_notes (it cites no PR/issue, it states the window
    itself, which the caller already trusts as gathered fact)."""
    if not truncated:
        return ""
    return f"_Покрывает период с {window_start} по {window_end}._"


# -- Draft-aware anchor window (AC11) ----------------------------------------


@dataclass(frozen=True)
class WindowResult:
    start: str  # ISO-8601 UTC
    end: str  # ISO-8601 UTC
    truncated: bool


def compute_window(
    last_release_at: str | None,
    now: str,
    cap_days: int = 30,
) -> WindowResult:
    """Window anchor = last *published* release's publish time; capped at
    `cap_days` from `now`. All inputs/outputs are ISO-8601 UTC strings so
    callers don't need to thread datetime objects through the `gh`-calling
    adapter layer.

    #1667: an unpublished pending draft must never anchor this window — a
    draft created near `now` would collapse the window to near-empty on
    every re-run, permanently hiding the real unreleased history. Draft
    in-place-update (matching a re-run against an existing draft instead of
    creating a duplicate) is handled by the caller scanning `prior_releases`
    for an existing draft to update, not by this function's anchor choice."""
    from datetime import datetime, timedelta

    def _parse(s: str) -> datetime:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))

    now_dt = _parse(now)
    anchor = last_release_at
    if anchor is None:
        start_dt = now_dt - timedelta(days=cap_days)
        return WindowResult(start=start_dt.isoformat(), end=now_dt.isoformat(), truncated=True)

    anchor_dt = _parse(anchor)
    cap_dt = now_dt - timedelta(days=cap_days)
    if anchor_dt < cap_dt:
        return WindowResult(start=cap_dt.isoformat(), end=now_dt.isoformat(), truncated=True)
    return WindowResult(start=anchor_dt.isoformat(), end=now_dt.isoformat(), truncated=False)


# -- Trust ramp (AC10) --------------------------------------------------------


def trust_ramp_state(prior_releases: list[dict]) -> str:
    """ "draft" or "auto". Read live from the GitHub releases API by the
    caller (no local state, per the issue text) — this function only
    applies the rule to whatever release history is handed to it.

    Rule: the first 4 releases for a repo are drafts published by the
    operator. Once the 4 most-recent releases were each published without
    a post-publish edit, the skill auto-publishes going forward.
    `prior_releases` is ordered most-recent-first; each entry needs
    `published: bool` and `edited_after_publish: bool`.
    """
    if len(prior_releases) < 4:
        return "draft"
    last_four = prior_releases[:4]
    if all(r.get("published") and not r.get("edited_after_publish") for r in last_four):
        return "auto"
    return "draft"


# -- Routine mode (#1658 AC1/AC3) ---------------------------------------------


def is_routine_host(device_config: dict) -> bool:
    """True iff this device is the sole routine host (``config/device.json``'s
    ``routine_host`` flag) — decision 1b7ff8d1-bbca-4207-a7e4-4c1edddef67e. A
    missing or falsy flag means "not the host", never an error — refusal is
    the caller's job."""
    return device_config.get("routine_host") is True


def weekly_release_notification_for(repo: str, version: str, status: str) -> tuple[str, str] | None:
    """(subject, body) for a weekly-release routine notification (AC3's fixed
    phrasing), or ``None`` when the run produced no release. ``None`` is the
    "stay silent" signal — the routine caller skips ``notify_text`` entirely
    rather than sending an empty/no-op notification, since a run with
    nothing to report must not become weekly spam.

    #1670: `version` is passed through verbatim — it is the tag name as
    computed by the caller (e.g. semver classification), which may or may
    not itself carry a `v` prefix. This function must not double it."""
    if status == "draft":
        return f"Draft awaiting publication: {repo} {version}", ""
    if status == "published":
        return f"Published release: {repo} {version}", ""
    return None


# -- Release-body assembly (AC9) ----------------------------------------------


def assemble_release_body(
    notes_body: str,
    remaining_section: str,
    full_changelog_url: str,
    footer: str = "Опубликовано ботом",
    retraction_section: str = "",
    disclosure_section: str = "",
    goal_section: str = "",
) -> str:
    """Structural assembly only. `notes_body` and `remaining_section` are
    already agent-authored prose — facts sourced from the collected window /
    open milestone issues, drafted by the caller and passed through
    `lint_release_notes` before reaching here. This function adds no issue
    links of its own: it only appends the `**Full Changelog**` line and the
    footer, which is what "no issue-links in the body" (issue #1572 AC9)
    actually constrains — the mechanical part, not the prose.

    `retraction_section` (#1659) is the pre-formatted "Отозвано" block from
    format_retraction_section() — included only when non-empty (AC2), placed
    after notes_body and before remaining_section.

    `disclosure_section` (#1668) is the pre-formatted cap-truncation notice
    from format_window_disclosure() — included only when non-empty, placed
    right after notes_body (before retractions) since it qualifies the whole
    body's coverage window.

    `goal_section` (#1669) is the pre-formatted goal-movement block from
    format_goal_section() — included only when non-empty, placed after
    retractions and before remaining_section."""
    parts = [notes_body.rstrip()]
    if disclosure_section.strip():
        parts.append(disclosure_section.rstrip())
    if retraction_section.strip():
        parts.append(retraction_section.rstrip())
    if goal_section.strip():
        parts.append(goal_section.rstrip())
    if remaining_section.strip():
        parts.append(remaining_section.rstrip())
    if full_changelog_url:
        parts.append(f"**Full Changelog**: {full_changelog_url}")
    parts.append(f"— {footer}")
    return "\n\n".join(parts)
