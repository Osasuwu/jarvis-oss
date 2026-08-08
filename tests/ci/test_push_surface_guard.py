"""Anti-regrowth ratchet on the always-pushed context layer (#1273).

CI ceilings on push surfaces only — the five canonical source files: the four
original identity/rules files plus the @import-delivered
``docs/context/invariants.md`` that replaced the SessionStart hook's
assembly-derived CONTEXT.md push (#1417). #1417 extracted a second file,
``docs/context/glossary-index.md``; #1418 retired it — a category index of
where to look does not need to be always-loaded to be findable, so a pull
pointer in CLAUDE.md replaced it and its surface row is gone.
Ceilings live in checked-in JSON
(``tests/ci/fixtures/push_surface_ceilings.json``); the guard fails red when a
surface exceeds its ceiling, and raising a ceiling requires editing the fixture
in the same PR (the JSON IS the fixture — ``same_pr_raise`` in the fixture).

Key design rules, per #1273 (as amended by #1417):

- **Every canonical surface is a plain file.** All five surfaces are measured
  by reading their file directly — there is no assembly-derived surface left
  since #1417 retired ``_load_project_context`` and its budget-constrained
  push. ``docs/context/invariants.md`` rides a bare ``@import`` in CLAUDE.md,
  which bypasses the SessionStart assembler and its ``ASSEMBLY_BUDGET_CHARS``
  cap entirely.
- **Item definition is pinned and load-bearing.** bullet at any nesting depth +
  numbered line; only block-level HTML comments are excluded. Fenced code, YAML
  frontmatter, and ``.claude/rules/*`` without a ``paths:`` key are all counted
  (they load at session start). A synthetic fixture test asserts this.
- **A new always-pushed surface fails red.** A rules file without a ``paths:``
  key and ``CLAUDE.local.md`` load at launch; if one appears and the fixture
  does not list it, the guard fails. Both rules directories are scanned —
  ``.claude/rules/`` and ``.claude-userlevel/rules/`` (#1430) — because the
  user-level one is the repo-side source that mirrors to ``~/.claude/rules/``.
- **A permissive ``paths:`` glob is rejected, not enrolled** (#1430). A glob
  matching everything costs what an unscoped rule costs while claiming to be
  conditional; enrolling it with a ceiling would legitimize the mislabel and
  open a second, dishonest door into the always-loaded set. "Permissive" is an
  explicit literal set, not a match-everything analyzer.
- **CONTEXT.md as a whole file has NO ceiling** — the Invariants/Glossary
  content that used to be measured through it now lives in its own ratcheted
  files (see above); CONTEXT.md itself carries only pointers.

Runs via ci-meta.yml (``pytest tests/ci/``, deliberately not path-filtered) on
every PR, per the #326 guard-test pattern.
"""

from __future__ import annotations

import json
import re
from pathlib import Path, PurePosixPath

from ._md_helpers import find_bare_imports

REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURE_PATH = Path(__file__).resolve().parent / "fixtures" / "push_surface_ceilings.json"

# ---------------------------------------------------------------------------
# Pinned item definition (#1273): bullet at any nesting depth or a numbered
# line. Only block-level HTML comments are excluded; fenced code and YAML
# frontmatter are counted (the metric measures what is actually pushed).
# ---------------------------------------------------------------------------

_HTML_COMMENT_RE = re.compile(r"<!--.*?-->", flags=re.DOTALL)
_BULLET_RE = re.compile(r"^[-*+] ")
_NUMBERED_RE = re.compile(r"^\d+[.)]\s")


def strip_html_comments(text: str) -> str:
    return _HTML_COMMENT_RE.sub("", text)


def count_items(text: str) -> int:
    """Count 'items' per the pinned definition. See the fixture's
    ``item_definition`` — this implementation must stay in lockstep with it."""
    n = 0
    for line in strip_html_comments(text).splitlines():
        s = line.strip()
        if not s:
            continue
        if _BULLET_RE.match(s) or _NUMBERED_RE.match(s):
            n += 1
    return n


def count_bytes(text: str) -> int:
    return len(text.encode("utf-8"))


# ---------------------------------------------------------------------------
# Surface discovery + measurement
# ---------------------------------------------------------------------------

# The five canonical file surfaces — all plain files, none assembly-derived.
CANONICAL_SURFACES = {
    "soul_md": "config/SOUL.md",
    "project_claude_md": "CLAUDE.md",
    "userlevel_claude_md": ".claude-userlevel/CLAUDE.md",
    "userlevel_doctrine_md": ".claude-userlevel/DOCTRINE.md",
    "invariants_md": "docs/context/invariants.md",
}


# Both rules directories are scanned (#1430). ``.claude-userlevel/rules/`` is the
# repo-side source that mirrors to ``~/.claude/rules/``, exactly as
# ``.claude-userlevel/CLAUDE.md`` is already a canonical surface — measuring the
# repo copy is what makes the user-level half reviewable in a PR at all.
_RULES_DIRS = {
    ".claude/rules": "claude_rules",
    ".claude-userlevel/rules": "userlevel_rules",
}

# "Permissive" is an explicit literal set, deliberately NOT a general
# does-this-match-everything analyzer (#1430). An analyzer is the crutchy
# option and false-positives on legitimately broad but bounded globs like
# ``docs/**``; this set covers the forms someone actually writes when they mean
# "everything".
PERMISSIVE_GLOBS = frozenset({"*", "**", "**/*", "**/**", "./*", "./**"})


def _frontmatter(text: str) -> str | None:
    """The YAML frontmatter block, or None when the file has none."""
    if not text.startswith("---"):
        return None
    end = text.find("\n---", 3)
    if end == -1:
        return None
    return text[3:end]


def _unquote(value: str) -> str:
    value = value.strip().strip(",").strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        return value[1:-1]
    return value


def parse_rule_paths(text: str) -> list[str] | None:
    """Return the rule's ``paths:`` globs, or None when the key is absent.

    None and ``[]`` mean different things and must stay distinguishable: no key
    means the rule loads at session start (always-pushed), while an empty value
    means it loads nothing and therefore costs nothing. Handles the three forms
    a rule is actually written in — inline scalar, flow sequence, block list.
    """
    frontmatter = _frontmatter(text)
    if frontmatter is None:
        return None
    lines = frontmatter.splitlines()
    for i, line in enumerate(lines):
        stripped = line.strip()
        if not (stripped == "paths:" or stripped.startswith("paths:")):
            continue
        inline = stripped[len("paths:") :].strip()
        if inline:
            if inline.startswith("["):
                body = inline.strip("[]")
                return [g for g in (_unquote(p) for p in body.split(",")) if g]
            return [_unquote(inline)]
        globs = []
        for follow in lines[i + 1 :]:
            if not follow.strip():
                continue
            if not follow.startswith((" ", "\t")) or not follow.strip().startswith("- "):
                break
            globs.append(_unquote(follow.strip()[2:]))
        return globs
    return None


def classify_rule_scope(text: str) -> str:
    """``unscoped`` | ``scoped`` | ``permissive`` for a rules file.

    Per [code.claude.com/docs/en/claude-directory], a rule without ``paths:``
    loads at session start like ``CLAUDE.md`` — an always-pushed surface. One
    with a real glob is conditional. A glob that matches everything costs what
    an unscoped rule costs while *claiming* to be conditional, so it gets its
    own verdict rather than passing as scoped (#1430).

    We classify by the key, never by directory — excluding the directory
    wholesale would make ``mv`` from CLAUDE.md into ``.claude/rules/`` a
    zero-cost laundering path (#1273).
    """
    globs = parse_rule_paths(text)
    if globs is None:
        return "unscoped"
    if any(g in PERMISSIVE_GLOBS for g in globs):
        return "permissive"
    return "scoped"


def _iter_rule_files(root: Path):
    """(repo-relative posix path, surface-id prefix, Path) for every rules file."""
    for reldir, prefix in _RULES_DIRS.items():
        rules_dir = root / reldir
        if not rules_dir.exists():
            continue
        for md in sorted(rules_dir.glob("*.md")):
            yield md.relative_to(root).as_posix(), prefix, md


def find_permissive_rules(root: Path = REPO_ROOT) -> list[tuple[str, str]]:
    """(relpath, offending glob) for every rule whose ``paths:`` matches all."""
    offenders = []
    for relpath, _prefix, md in _iter_rule_files(root):
        globs = parse_rule_paths(md.read_text(encoding="utf-8")) or []
        for glob in globs:
            if glob in PERMISSIVE_GLOBS:
                offenders.append((relpath, glob))
                break
    return offenders


def _discover_extra_file_surfaces(root: Path = REPO_ROOT) -> dict[str, str]:
    """Always-pushed file surfaces beyond the canonical six: unscoped rules in
    either rules directory, and CLAUDE.local.md if present. Returns
    {surface_id: repo-relative path}.

    Permissive rules are deliberately absent — they are rejected outright by
    ``find_permissive_rules``, not offered an enrollment path. One canonical way
    to be always-loaded (#1430).
    """
    surfaces: dict[str, str] = {}
    for relpath, prefix, md in _iter_rule_files(root):
        if classify_rule_scope(md.read_text(encoding="utf-8")) == "unscoped":
            surfaces[f"{prefix}:{relpath}"] = relpath
    local = root / "CLAUDE.local.md"
    if local.exists():
        surfaces["claude_local_md"] = "CLAUDE.local.md"
    return surfaces


def _format_permissive_violation(relpath: str, glob: str) -> str:
    return (
        f"Rule '{relpath}' declares paths: '{glob}', which matches everything — "
        "it is loaded at session start in practice while claiming to be "
        "conditional, so it costs what an unscoped rule costs.\n"
        "Enrolling it with a ceiling would legitimize the mislabel, so it fails "
        "instead. Two honest fixes: narrow the glob to the files the rule "
        "actually applies to, or drop the paths: key and enroll the file as an "
        "always-pushed surface in CANONICAL_SURFACES plus "
        "tests/ci/fixtures/push_surface_ceilings.json in the same PR."
    )


def _format_unenrolled_rule_violation(relpath: str, surface_id: str) -> str:
    return (
        f"Rule '{relpath}' (discovered as '{surface_id}') carries no paths: key, "
        "so it loads at session start with the same priority as CLAUDE.md — an "
        "always-pushed surface every session pays for, and one that is re-injected "
        "from disk after every compaction.\n"
        "Admitting one must be deliberate: add it to CANONICAL_SURFACES and give "
        "it a ceiling in tests/ci/fixtures/push_surface_ceilings.json in the same "
        "PR. If it was meant to be conditional, add a paths: glob instead — a "
        "scoped rule costs zero always-loaded bytes."
    )


def _measure_surface(surface_id: str, spec: dict) -> tuple[int, int]:
    """Return (items, bytes) for a fixture surface — always a plain file read."""
    text = (REPO_ROOT / spec["path"]).read_text(encoding="utf-8")
    return count_items(text), count_bytes(text)


def _format_violation(
    surface_id: str, items: int, items_ceil: int, bytes_: int, bytes_ceil: int
) -> str:
    fixture = _load_fixture()
    spec = fixture["surfaces"][surface_id]
    source = spec["path"]
    parts = [f"Push surface '{surface_id}' ({source}) exceeds its ceiling:"]
    if items > items_ceil:
        parts.append(f"  items: {items} > {items_ceil} (over by {items - items_ceil})")
    if bytes_ > bytes_ceil:
        parts.append(f"  bytes: {bytes_} > {bytes_ceil} (over by {bytes_ - bytes_ceil})")
    parts.append(
        "Every session pays this cost. Move content to a cheaper carrier per "
        ".claude-userlevel/DOCTRINE.md → Baseline carrier selection "
        "(code/CI gate, PreToolUse deny hook, .claude/rules/ + paths:, retrieval) "
        "instead of raising the ceiling. Raising a ceiling requires editing "
        "tests/ci/fixtures/push_surface_ceilings.json in the same PR "
        f"(decision {spec['decision_uuid']})."
    )
    return "\n".join(parts)


def _load_fixture() -> dict:
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# Fan-out budget (#1324)
#
# Measured in #1270 (docs/research/context-management.md A.7): both CLAUDE.md
# levels and every *bare* ``@import`` under them are inherited verbatim by each
# Agent-tool subagent, while SessionStart hook output is not. So an inherited
# byte is paid N+1 times on a fan-out of N, and a hook-injected byte is paid
# once regardless. The per-surface ratchet cannot see this: adding a new bare
# import is a fan-out-multiplied cost that no single surface ceiling measures.
#
# There is no per-agent CLAUDE.md profile to opt out of (see A.7 note on #1324
# AC3) — Explore and Plan are the only subagents that omit CLAUDE.md, and that
# is not configurable. Budgeting is therefore the only available lever.
# ---------------------------------------------------------------------------


# The two CLAUDE.md levels are the inheritance roots.
_INHERITANCE_ROOTS = ("CLAUDE.md", ".claude-userlevel/CLAUDE.md")

# ``.claude-userlevel/`` is installed to ``~/.claude/``; one file is templated
# from elsewhere in the repo, so a bare import written against the installed
# layout resolves to a different repo path.
_INSTALL_ALIASES = {".claude-userlevel/SOUL.md": "config/SOUL.md"}


def _parse_bare_imports(text: str) -> list[str]:
    """Import targets that Claude Code actually expands: bare, line-start, own
    line, outside fenced code. A mid-prose ``@path`` mention does not resolve
    (#1426) and therefore costs nothing — counting it would overstate the
    budget and, worse, hide the fact that it is not delivered.

    Delegates to the shared parser so the byte-accounting definition of an
    import cannot drift from the one the import-form guards enforce (#1426)."""
    return [path for _, path in find_bare_imports(text)]


def _resolve_import(target: str, containing: str) -> str | None:
    """Repo-relative path for an import target, or None when it does not exist
    in this checkout (a bare import to a missing file is silently empty).

    The normalization is `PurePosixPath`, deliberately not `str.lstrip("./")`:
    `lstrip` takes a character *set*, so it ate the leading dot of
    `.claude-userlevel/SOUL.md` and produced `claude-userlevel/SOUL.md`, which
    exists nowhere — the alias below never matched and both user-level imports
    were silently dropped from the byte accounting (#1426). Same silent-nothing
    failure class the guard exists to catch, one layer down.
    """
    base = PurePosixPath(containing).parent
    rel = str(base / target) if str(base) != "." else target
    rel = _INSTALL_ALIASES.get(rel, rel)
    return rel if (REPO_ROOT / rel).is_file() else None


def inherited_surfaces() -> dict[str, int]:
    """{repo-relative path: bytes} for every surface a subagent inherits.

    Derived by walking bare imports transitively from the two CLAUDE.md roots —
    never hardcoded, so a newly added import is picked up (and charged) by the
    ratchet automatically instead of needing anyone to remember."""
    seen: dict[str, int] = {}
    queue = list(_INHERITANCE_ROOTS)
    while queue:
        rel = queue.pop(0)
        if rel in seen:
            continue
        path = REPO_ROOT / rel
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8")
        seen[rel] = count_bytes(text)
        for target in _parse_bare_imports(text):
            resolved = _resolve_import(target, rel)
            if resolved is not None:
                queue.append(resolved)
    return seen


def inherited_bytes() -> int:
    return sum(inherited_surfaces().values())


def max_safe_fanout() -> int:
    """Largest N for which (N+1) copies of the inherited layer stay inside the
    declared per-agent ceiling. This is the number ``/delegate`` cites when
    sizing a fan-out."""
    per_agent = inherited_bytes()
    ceiling = _load_fixture()["_meta"]["fanout_budget"]["per_agent_ceiling_bytes"]
    if per_agent > ceiling:
        return 0
    # Within the ceiling, every additional agent costs the same fixed amount,
    # so the budget is expressed per agent and the reference fan-out is the
    # multiplier used when reporting total cost.
    return _load_fixture()["_meta"]["fanout_budget"]["reference_fanout"]


def _format_fanout_violation(per_agent: int, n: int, ceiling: int) -> str:
    total = per_agent * (n + 1)
    return "\n".join(
        [
            "Inherited-context layer exceeds the fan-out budget:",
            f"  per agent: {per_agent} > {ceiling} (over by {per_agent - ceiling})",
            f"  at the reference fan-out of {n}: {total} bytes of prompt "
            f"({n} subagents + the parent session)",
            "Inherited surfaces (both CLAUDE.md levels + every bare @import under "
            "them): " + ", ".join(sorted(inherited_surfaces())),
            "There is no per-agent CLAUDE.md profile to opt out of. Move content to "
            "a cheaper carrier per .claude-userlevel/DOCTRINE.md → Baseline carrier "
            "selection (code/CI gate, PreToolUse deny hook, .claude/rules/ + paths:, "
            "retrieval) — those are inherited zero times. Raising the ceiling "
            "requires editing tests/ci/fixtures/push_surface_ceilings.json in the "
            "same PR, which is the point: the raise makes the N+1 cost visible at "
            "review time.",
        ]
    )


# ---------------------------------------------------------------------------
# Synthetic fixture for the pinned item definition (#1273 AC3)
# ---------------------------------------------------------------------------

SYNTHETIC_CONTENT = """\
<!-- a block-level HTML comment
   that spans lines and contains - not a bullet
   and 1. not a numbered item -->
---
- frontmatter bullet
---

# Heading

- a bullet
- another bullet
    - nested bullet
* star bullet
    * nested star
+ plus bullet
1. numbered one
2) numbered paren
   3. nested numbered

| col | col |
| - | 2. x |

```fenced
- inside fenced code (counted)
1. also counted
```

- after the fence
"""


class TestItemDefinition:
    """The pinned definition must be asserted, not implied (#1273 AC3)."""

    def test_synthetic_content_counts_as_pinned(self):
        assert count_items(SYNTHETIC_CONTENT) == 13

    def test_block_html_comments_excluded(self):
        assert count_items("<!-- - hidden\n   1. also hidden\n-->\n- real") == 1

    def test_nested_bullets_counted(self):
        assert count_items("- top\n    - mid\n        - deep\n") == 3

    def test_numbered_lines_counted(self):
        assert count_items("1. one\n2) two\n   3. three\n10. ten\n") == 4

    def test_fenced_code_is_counted(self):
        # Fenced code is NOT stripped — it is pushed at session start, so it
        # counts toward the byte metric just like any other text (#1273 AC4).
        assert count_items("```\n- in fence\n1. also\n```\n") == 2

    def test_yaml_frontmatter_is_counted(self):
        assert count_items("---\n- fm bullet\n1. fm num\n---\nbody\n") == 2

    def test_table_rows_are_not_items(self):
        assert count_items("| - | 2. x |\n| * | 3) y |\n") == 0

    def test_plain_lines_are_not_items(self):
        assert count_items("not a bullet\n= heading =\njust text\n") == 0


class TestFixtureIntegrity:
    def test_fixture_exists_and_is_valid_json(self):
        assert FIXTURE_PATH.is_file()
        fixture = _load_fixture()
        assert "surfaces" in fixture

    def test_item_definition_pinned_in_fixture(self):
        fixture = _load_fixture()
        definition = fixture["_meta"]["item_definition"]
        assert "bullet" in definition and "numbered" in definition
        assert "HTML comment" in definition

    def test_margin_stated(self):
        fixture = _load_fixture()
        margin = fixture["_meta"]["margin"]
        assert margin["pct"] >= 0
        assert "statement" in margin

    def test_every_surface_has_record_decision_uuid(self):
        fixture = _load_fixture()
        uuid_re = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
        for surface_id, spec in fixture["surfaces"].items():
            assert uuid_re.match(spec["decision_uuid"]), (
                f"{surface_id}: decision_uuid must be a record_decision UUID"
            )

    def test_context_md_whole_file_has_no_ceiling(self):
        fixture = _load_fixture()
        assert fixture["_meta"]["context_md_whole_file"]["has_ceiling"] is False
        # The whole-file CONTEXT.md must not be ratcheted; the extracted
        # content lives in its own ratcheted files instead (#1417).
        assert "invariants_md" in fixture["surfaces"]
        # #1418 retired the glossary category index; its row must stay gone.
        assert "glossary_index_md" not in fixture["surfaces"]
        assert "context_md_pushed" not in fixture["surfaces"]
        assert "context_md" not in fixture["surfaces"]

    def test_fixture_surfaces_exist_on_disk(self):
        fixture = _load_fixture()
        for surface_id, spec in fixture["surfaces"].items():
            assert (REPO_ROOT / spec["path"]).is_file(), f"{surface_id}: {spec['path']} missing"

    def test_fixture_covers_discovered_surfaces(self):
        """A new always-pushed file surface must be added to the fixture before
        the guard passes — adding one without updating the fixture fails red."""
        fixture = _load_fixture()
        fixture_paths = {spec["path"] for spec in fixture["surfaces"].values()}
        for surface_id, relpath in _discover_extra_file_surfaces().items():
            if "/rules/" in relpath:
                message = _format_unenrolled_rule_violation(relpath, surface_id)
            else:
                message = (
                    f"new always-pushed surface {relpath} (discovered as "
                    f"{surface_id}) is not in "
                    "tests/ci/fixtures/push_surface_ceilings.json — add a ceiling "
                    "for it in the same PR, else the ratchet silently misses it"
                )
            assert relpath in fixture_paths, message

    def test_same_pr_raise_documented(self):
        """The JSON-is-the-fixture property must be documented so a reviewer
        knows a ceiling bump is a fixture edit in the same PR."""
        fixture = _load_fixture()
        assert "same_pr_raise" in fixture["_meta"]


class TestBareImportParsing:
    """The inherited set is derived by parsing imports — so the parser must
    distinguish the form that actually resolves (#1426's lesson)."""

    def test_bare_line_start_import_is_parsed(self):
        assert _parse_bare_imports("@docs/context/invariants.md\n") == [
            "docs/context/invariants.md"
        ]

    def test_mid_prose_mention_is_not_an_import(self):
        # This is exactly the #1426 defect: a mention inside a sentence never
        # expands, so it must not be counted as inherited weight either.
        text = "Identity lives in @SOUL.md, installed alongside this file.\n"
        assert _parse_bare_imports(text) == []

    def test_trailing_whitespace_tolerated(self):
        assert _parse_bare_imports("@a/b.md   \n") == ["a/b.md"]

    def test_fenced_import_is_not_parsed(self):
        assert _parse_bare_imports("```\n@a/b.md\n```\n") == []

    def test_indented_import_is_not_parsed(self):
        assert _parse_bare_imports("  @a/b.md\n") == []


class TestImportResolution:
    """Parsing an import is only half the accounting — it must also resolve.

    An import that parses but fails to resolve is dropped from the walk with no
    signal, so its bytes vanish from the fan-out budget. That is the same
    silent-nothing failure #1426 is about, one layer down, and it is how
    SOUL.md and DOCTRINE.md stayed uncharged even after their import lines were
    made bare.
    """

    def test_dotfile_dir_import_resolves_through_alias(self):
        """Regression: `str.lstrip("./")` takes a character SET, so it stripped
        the leading dot of `.claude-userlevel/SOUL.md` and produced
        `claude-userlevel/SOUL.md` — which matched no alias and no file, so the
        resolver returned None and the largest single surface in the layer went
        uncounted."""
        assert _resolve_import("SOUL.md", ".claude-userlevel/CLAUDE.md") == "config/SOUL.md"

    def test_dotfile_dir_import_resolves_without_alias(self):
        assert (
            _resolve_import("DOCTRINE.md", ".claude-userlevel/CLAUDE.md")
            == ".claude-userlevel/DOCTRINE.md"
        )

    def test_repo_root_import_resolves(self):
        assert (
            _resolve_import("docs/context/invariants.md", "CLAUDE.md")
            == "docs/context/invariants.md"
        )

    def test_missing_target_resolves_to_none(self):
        assert _resolve_import("nope-does-not-exist.md", "CLAUDE.md") is None

    def test_both_userlevel_imports_are_counted(self):
        """End-to-end: the two files #1426 restored must appear in the walk.

        Without this, the ceiling raise in the fixture could be satisfied by
        any 17 KB of growth rather than by these two surfaces specifically.
        """
        surfaces = inherited_surfaces()
        assert "config/SOUL.md" in surfaces
        assert ".claude-userlevel/DOCTRINE.md" in surfaces


class TestFanoutBudget:
    """#1324: every inherited byte is paid N+1 times on a fan-out of N.

    The per-surface ratchet does not capture this — adding a *new* bare
    ``@import`` to CLAUDE.md is a fan-out-multiplied cost that no single
    surface ceiling sees.
    """

    def test_fanout_budget_declared_in_fixture(self):
        budget = _load_fixture()["_meta"]["fanout_budget"]
        assert budget["reference_fanout"] >= 1
        assert budget["per_agent_ceiling_bytes"] > 0
        assert "statement" in budget
        assert re.match(
            r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
            budget["decision_uuid"],
        )

    def test_inherited_set_is_derived_and_nonempty(self):
        inherited = inherited_surfaces()
        # Both CLAUDE.md levels are inherited verbatim by every Agent-tool
        # subagent (measured, #1270 → docs/research/context-management.md A.7).
        assert "CLAUDE.md" in inherited
        assert ".claude-userlevel/CLAUDE.md" in inherited
        # ...as is every bare @import reachable from them.
        assert "docs/context/invariants.md" in inherited

    def test_hook_injected_context_is_not_inherited(self):
        """A one-time session cost must never be counted per agent (#1270)."""
        assert "CONTEXT.md" not in inherited_surfaces()

    def test_fanout_cost_within_budget(self):
        budget = _load_fixture()["_meta"]["fanout_budget"]
        per_agent = inherited_bytes()
        n = budget["reference_fanout"]
        ceiling = budget["per_agent_ceiling_bytes"]
        assert per_agent <= ceiling, _format_fanout_violation(per_agent, n, ceiling)
        assert per_agent * (n + 1) <= ceiling * (n + 1)

    def test_reference_fanout_is_affordable(self):
        budget = _load_fixture()["_meta"]["fanout_budget"]
        assert max_safe_fanout() >= budget["reference_fanout"]

    def test_violation_message_states_the_multiplier(self):
        msg = _format_fanout_violation(60000, 6, 52131)
        assert "60000" in msg
        assert "52131" in msg
        assert "420000" in msg  # 60000 * (6 + 1)
        assert "Baseline carrier selection" in msg
        assert "push_surface_ceilings.json" in msg


class TestRatchet:
    def test_all_surfaces_within_ceilings(self):
        fixture = _load_fixture()
        violations = []
        for surface_id, spec in fixture["surfaces"].items():
            items, bytes_ = _measure_surface(surface_id, spec)
            if items > spec["items"] or bytes_ > spec["bytes"]:
                violations.append((surface_id, items, spec["items"], bytes_, spec["bytes"]))
        assert not violations, "\n" + "\n".join(
            _format_violation(sid, it, ic, by, bc) for sid, it, ic, by, bc in violations
        )

    def test_failure_message_names_carriers(self):
        """AC10: the failure message names the surface, both overage units, and
        carrier alternatives from the #1252 AC2 table (DOCTRINE.md)."""
        msg = _format_violation("soul_md", 50, 46, 12000, 11415)
        assert "soul_md" in msg
        assert "items: 50 > 46 (over by 4)" in msg
        assert "bytes: 12000 > 11415 (over by 585)" in msg
        assert "Baseline carrier selection" in msg
        assert ".claude/rules/ + paths:" in msg
        assert "retrieval" in msg


class TestRuleScopeClassification:
    """#1430: a rule file is always-loaded unless its ``paths:`` genuinely
    scopes it. Three outcomes — unscoped / scoped / permissive — because a glob
    that matches everything costs the same as no glob at all while *claiming*
    to be conditional."""

    def test_no_frontmatter_is_unscoped(self):
        assert classify_rule_scope("Some rule prose.\n") == "unscoped"

    def test_frontmatter_without_paths_key_is_unscoped(self):
        text = "---\ndescription: a rule\n---\n\nbody\n"
        assert classify_rule_scope(text) == "unscoped"

    def test_inline_glob_is_scoped(self):
        text = '---\npaths: "src/**/*.py"\n---\n\nbody\n'
        assert classify_rule_scope(text) == "scoped"

    def test_list_glob_is_scoped(self):
        text = '---\npaths:\n  - "src/**/*.py"\n  - tests/**\n---\n\nbody\n'
        assert classify_rule_scope(text) == "scoped"

    def test_flow_sequence_glob_is_scoped(self):
        text = '---\npaths: ["src/**/*.py", "docs/*.md"]\n---\n\nbody\n'
        assert classify_rule_scope(text) == "scoped"

    def test_empty_paths_value_is_scoped(self):
        """An empty ``paths:`` loads nothing, so it costs nothing. A silent
        no-op rule is a smell, but this guard is a context-cost ratchet."""
        assert classify_rule_scope("---\npaths:\n---\n\nbody\n") == "scoped"

    def test_double_star_is_permissive(self):
        assert classify_rule_scope('---\npaths: "**"\n---\n') == "permissive"

    def test_double_star_slash_star_is_permissive(self):
        assert classify_rule_scope('---\npaths:\n  - "**/*"\n---\n') == "permissive"

    def test_single_star_is_permissive(self):
        assert classify_rule_scope('---\npaths: "*"\n---\n') == "permissive"

    def test_permissive_entry_beside_narrow_one_still_permissive(self):
        text = '---\npaths:\n  - "src/**/*.py"\n  - "**"\n---\n'
        assert classify_rule_scope(text) == "permissive"

    def test_permissive_set_is_literal_not_analyzed(self):
        """Deliberately NOT a match-everything analyzer — that is the crutchy
        option, and it false-positives on broad-but-bounded globs."""
        assert classify_rule_scope('---\npaths: "docs/**"\n---\n') == "scoped"

    def test_parse_returns_none_when_key_absent(self):
        assert parse_rule_paths("---\ndescription: x\n---\n") is None

    def test_parse_returns_globs(self):
        text = '---\npaths:\n  - "a/**"\n  - b/*.md\n---\n'
        assert parse_rule_paths(text) == ["a/**", "b/*.md"]


class TestRulesDirDiscovery:
    """#1430 AC1: both rules directories are scanned, measured from the repo
    copy. ``.claude-userlevel/rules/`` is the repo-side source that mirrors to
    ``~/.claude/rules/``, exactly as ``.claude-userlevel/CLAUDE.md`` already is
    a canonical surface. Driven by synthetic trees (AC5), never live files."""

    @staticmethod
    def _write(root: Path, relpath: str, text: str) -> None:
        p = root / relpath
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")

    def test_project_rules_dir_is_scanned(self, tmp_path):
        self._write(tmp_path, ".claude/rules/a.md", "body\n")
        found = _discover_extra_file_surfaces(tmp_path)
        assert ".claude/rules/a.md" in found.values()

    def test_userlevel_rules_dir_is_scanned(self, tmp_path):
        self._write(tmp_path, ".claude-userlevel/rules/b.md", "body\n")
        found = _discover_extra_file_surfaces(tmp_path)
        assert ".claude-userlevel/rules/b.md" in found.values()

    def test_scoped_rule_is_not_a_surface(self, tmp_path):
        self._write(tmp_path, ".claude/rules/c.md", '---\npaths: "src/**"\n---\nbody\n')
        assert _discover_extra_file_surfaces(tmp_path) == {}

    def test_permissive_rule_is_not_silently_enrolled(self, tmp_path):
        """A permissive glob is rejected outright (see TestPermissiveGlobs), so
        it must not appear as an enrollable surface — one canonical way in."""
        self._write(tmp_path, ".claude/rules/d.md", '---\npaths: "**"\n---\nbody\n')
        assert _discover_extra_file_surfaces(tmp_path) == {}

    def test_surface_ids_distinguish_the_two_dirs(self, tmp_path):
        self._write(tmp_path, ".claude/rules/same.md", "body\n")
        self._write(tmp_path, ".claude-userlevel/rules/same.md", "body\n")
        found = _discover_extra_file_surfaces(tmp_path)
        assert len(found) == 2, f"same basename in both dirs collided: {found}"

    def test_claude_local_md_still_discovered(self, tmp_path):
        self._write(tmp_path, "CLAUDE.local.md", "body\n")
        assert _discover_extra_file_surfaces(tmp_path) == {"claude_local_md": "CLAUDE.local.md"}


class TestPermissiveGlobs:
    """#1430 AC3: a permissive glob fails red naming the offending glob, rather
    than being enrolled with a ceiling. Enrolling it would legitimize a rule
    that claims to be scoped while costing what an unscoped one costs."""

    def test_finder_reports_path_and_glob(self, tmp_path):
        p = tmp_path / ".claude/rules/wide.md"
        p.parent.mkdir(parents=True)
        p.write_text('---\npaths:\n  - "src/**"\n  - "**/*"\n---\nbody\n', encoding="utf-8")
        assert find_permissive_rules(tmp_path) == [(".claude/rules/wide.md", "**/*")]

    def test_finder_clean_on_scoped_tree(self, tmp_path):
        p = tmp_path / ".claude-userlevel/rules/narrow.md"
        p.parent.mkdir(parents=True)
        p.write_text('---\npaths: "docs/**"\n---\nbody\n', encoding="utf-8")
        assert find_permissive_rules(tmp_path) == []

    def test_message_names_glob_and_offers_both_honest_fixes(self):
        msg = _format_permissive_violation(".claude/rules/wide.md", "**/*")
        assert ".claude/rules/wide.md" in msg
        assert "**/*" in msg
        assert "narrow" in msg.lower()
        assert "push_surface_ceilings.json" in msg

    def test_live_repo_has_no_permissive_rule(self):
        offenders = find_permissive_rules()
        assert not offenders, "\n" + "\n".join(
            _format_permissive_violation(p, g) for p, g in offenders
        )


class TestUnenrolledRuleMessage:
    """#1430 AC4: the rejection explains *why* the file counts as always-loaded
    and points at the fixture, matching the existing violation formatter's tone."""

    def test_message_explains_why_and_where_to_enroll(self):
        msg = _format_unenrolled_rule_violation(".claude/rules/new.md", "claude_rules:new.md")
        assert ".claude/rules/new.md" in msg
        assert "paths:" in msg
        assert "session start" in msg.lower()
        assert "push_surface_ceilings.json" in msg
        assert "CANONICAL_SURFACES" in msg


class TestCanonicalSurfacesAreLoadBearing:
    """#1430 AC2: enrollment means CANONICAL_SURFACES *and* a fixture ceiling.
    Without this tie CANONICAL_SURFACES is dead code and the rejection message
    asks for a step nothing verifies."""

    def test_canonical_surfaces_match_the_fixture(self):
        fixture = _load_fixture()
        assert {sid: spec["path"] for sid, spec in fixture["surfaces"].items()} == (
            CANONICAL_SURFACES
        ), (
            "CANONICAL_SURFACES and tests/ci/fixtures/push_surface_ceilings.json "
            "disagree — every enrolled always-pushed surface must appear in both, "
            "in the same PR"
        )
