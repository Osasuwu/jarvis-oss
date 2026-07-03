"""CI guard test for include_unreviewed recall flag (issue #685, Slice 5).

Validates the migration file and schema.sql are in sync: each of the 4
recall-path RPCs must accept an `include_unreviewed boolean default false`
parameter and apply the conditional filter.
"""

from __future__ import annotations

import re
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
MIGRATION_DIR = REPO_ROOT / "supabase" / "migrations"
SCHEMA_PATH = REPO_ROOT / "mcp-memory" / "schema.sql"

# The canonical migration filename for this slice.
EXPECTED_MIGRATION = "20260520000001_add_include_unreviewed_recall_flag.sql"

# RPCs that must accept include_unreviewed.
RECALL_RPCS = [
    "match_memories_v2",
    "match_memories",
    "keyword_search_memories",
    "get_linked_memories",
]


def _extract_param_list(text: str, func_name: str) -> str | None:
    """Return the parameter list of the **last** definition of *func_name* in SQL text.

    Finds ``CREATE OR REPLACE FUNCTION <func_name>(`` (case-insensitive) and
    returns the substring between the opening ``(`` and its matching ``)``.
    The last match is used because schema.sql is cumulative — each migration
    appends a new ``CREATE OR REPLACE FUNCTION`` that supersedes earlier ones,
    so only the last definition is authoritative (mirrors Postgres semantics).
    Returns None if the function is not found.
    """
    pattern = re.compile(
        r"create\s+or\s+replace\s+function\s+" + re.escape(func_name) + r"\s*\(",
        re.IGNORECASE,
    )
    matches = list(pattern.finditer(text))
    if not matches:
        return None
    m = matches[-1]
    start = m.end()
    depth, pos = 1, start
    while pos < len(text) and depth > 0:
        if text[pos] == "(":
            depth += 1
        elif text[pos] == ")":
            depth -= 1
        pos += 1
    return text[start : pos - 1]


def _extract_function_body(text: str, func_name: str) -> str | None:
    """Return the body (text between `CREATE OR REPLACE FUNCTION <name>(` and `$$;`).

    Uses word-boundary matching so ``match_memories`` doesn't match
    ``match_memories_v2``. As with _extract_param_list, the LAST definition
    in the file is authoritative under Postgres CREATE OR REPLACE semantics.
    Returns None if the function is not found.
    """
    pattern = re.compile(
        r"create\s+or\s+replace\s+function\s+" + re.escape(func_name) + r"\s*\(",
        re.IGNORECASE,
    )
    matches = list(pattern.finditer(text))
    if not matches:
        return None
    m = matches[-1]
    body_end = text.find("$$;", m.end())
    if body_end == -1:
        return None
    return text[m.end() : body_end]


class TestMigrationFile:
    """The migration file exists and has the expected RPC signatures."""

    def test_migration_file_exists(self):
        migration = MIGRATION_DIR / EXPECTED_MIGRATION
        assert migration.exists(), (
            f"Expected migration {EXPECTED_MIGRATION} not found in {MIGRATION_DIR}"
        )

    def test_migration_has_include_unreviewed_on_match_memories_v2(self):
        text = (MIGRATION_DIR / EXPECTED_MIGRATION).read_text(encoding="utf-8")
        params = _extract_param_list(text, "match_memories_v2")
        assert params is not None, "match_memories_v2 not found in migration"
        assert "include_unreviewed boolean default false" in params.lower(), (
            "match_memories_v2 parameter list missing include_unreviewed"
        )

    def test_migration_has_include_unreviewed_on_match_memories(self):
        text = (MIGRATION_DIR / EXPECTED_MIGRATION).read_text(encoding="utf-8")
        params = _extract_param_list(text, "match_memories")
        assert params is not None, "match_memories not found in migration"
        assert "include_unreviewed boolean default false" in params.lower(), (
            "match_memories parameter list missing include_unreviewed"
        )

    def test_migration_has_include_unreviewed_on_keyword_search(self):
        text = (MIGRATION_DIR / EXPECTED_MIGRATION).read_text(encoding="utf-8")
        params = _extract_param_list(text, "keyword_search_memories")
        assert params is not None, "keyword_search_memories not found in migration"
        assert "include_unreviewed boolean default false" in params.lower(), (
            "keyword_search_memories parameter list missing include_unreviewed"
        )

    def test_migration_has_include_unreviewed_on_get_linked(self):
        text = (MIGRATION_DIR / EXPECTED_MIGRATION).read_text(encoding="utf-8")
        params = _extract_param_list(text, "get_linked_memories")
        assert params is not None, "get_linked_memories not found in migration"
        assert "include_unreviewed boolean default false" in params.lower(), (
            "get_linked_memories parameter list missing include_unreviewed"
        )

    def test_migration_merge_targets_always_filtered(self):
        """merge_targets IS NULL check must remain — never include proposals."""
        text = (MIGRATION_DIR / EXPECTED_MIGRATION).read_text(encoding="utf-8")
        count = text.count("merge_targets is null or array_length")
        # Appears in every subquery: match_memories_v2 (1), match_memories (1),
        # keyword_search_memories (1), get_linked_memories (2 = each union branch).
        assert count >= 5, (
            f"Expected >=5 merge_targets filter occurrences, found {count}. "
            "The filter must remain unconditional."
        )


class TestSchemaFileInSync:
    """schema.sql must reflect the same RPC signatures as the migration."""

    def test_schema_has_include_unreviewed_default(self):
        text = SCHEMA_PATH.read_text(encoding="utf-8")
        for rpc in RECALL_RPCS:
            params = _extract_param_list(text, rpc)
            assert params is not None, f"schema.sql: {rpc} function not found"
            assert "include_unreviewed boolean default false" in params.lower(), (
                f"schema.sql: {rpc} parameter list missing include_unreviewed"
            )

    def test_schema_conditions_on_flag(self):
        """The requires_review filter must be gated by include_unreviewed."""
        text = SCHEMA_PATH.read_text(encoding="utf-8")
        count = text.count("include_unreviewed or m.requires_review = false")
        # match_memories_v2 (1), match_memories (1), keyword_search (1),
        # get_linked_memories (2 = each union branch)
        assert count >= 5, (
            f"Expected >=5 conditional filter occurrences, found {count}. "
            "The `include_unreviewed or` guard must wrap every requires_review "
            "filter."
        )

    def test_merge_targets_still_absolute(self):
        """merge_targets filter must NOT be gated by include_unreviewed."""
        text = SCHEMA_PATH.read_text(encoding="utf-8")
        gated = text.count("include_unreviewed or m.merge_targets")
        assert gated == 0, (
            f"Found {gated} instances where merge_targets filter is gated by "
            "include_unreviewed. merge_targets must ALWAYS be filtered."
        )

    def test_match_memories_deleted_at_unconditional(self):
        """match_memories must apply `m.deleted_at is null` outside the show_history branch.

        Regression: round-2 had `deleted_at is null` INSIDE the show_history
        branch of match_memories — show_history=true surfaced soft-deleted
        rows from the fallback embedding slot while match_memories_v2
        (primary slot) hid them, producing non-deterministic RRF fusion.
        Fix mirrors match_memories_v2: deleted_at filtering is unconditional.
        """
        # Check BOTH the migration and the schema.sql copy — `supabase db reset`
        # uses schema.sql, so a regression there would otherwise pass CI silently.
        for source_label, text in (
            ("migration", (MIGRATION_DIR / EXPECTED_MIGRATION).read_text(encoding="utf-8")),
            ("schema.sql", SCHEMA_PATH.read_text(encoding="utf-8")),
        ):
            for func_name in ("match_memories", "keyword_search_memories"):
                body = _extract_function_body(text, func_name)
                assert body is not None, f"{source_label}: {func_name} not found"
                assert "\n      and m.deleted_at is null\n" in body, (
                    f"{source_label}: {func_name} must have "
                    "`and m.deleted_at is null` outside the show_history branch."
                )
                sh_start = body.find("(show_history")
                assert sh_start != -1, (
                    f"{source_label}: show_history clause not found in {func_name}"
                )
                sh_block_end = body.find("\n      and (include_unreviewed", sh_start)
                if sh_block_end == -1:
                    sh_block_end = len(body)
                sh_block = body[sh_start:sh_block_end]
                assert "deleted_at" not in sh_block, (
                    f"{source_label}: show_history branch in {func_name} must not "
                    "mention deleted_at — the unconditional filter above covers it."
                )

    def test_recall_rpcs_project_requires_review_column(self):
        """All 4 recall-path RPCs must return `requires_review boolean`.

        Regression: round-3's Python-side CONFIDENCE_FLOOR floor for
        unreviewed candidates (recall.py:apply_temporal_scoring) was dead
        code because the SQL RPCs never projected the column. PostgREST
        only materialises projected columns, so `row.get("requires_review")`
        always returned None → falsy → conf=1.0 (full entrenchment).
        """
        for source_label, text in (
            ("migration", (MIGRATION_DIR / EXPECTED_MIGRATION).read_text(encoding="utf-8")),
            ("schema.sql", SCHEMA_PATH.read_text(encoding="utf-8")),
        ):
            for rpc in RECALL_RPCS:
                body = _extract_function_body(text, rpc)
                assert body is not None, f"{source_label}: {rpc} body not found"
                # Either the literal `requires_review boolean,` in `returns table`
                # or `m.requires_review` (or `sub.requires_review`) in the SELECT
                # — both must appear in the body of each RPC.
                assert "requires_review boolean" in body, (
                    f"{source_label}: {rpc} returns table(...) must declare "
                    "`requires_review boolean` so the Python temporal-scoring "
                    "floor for unreviewed rows isn't dead code."
                )
                assert ("m.requires_review" in body) or ("sub.requires_review" in body), (
                    f"{source_label}: {rpc} SELECT must project `m.requires_review` "
                    "(or `sub.requires_review` in the linked-memories CTE)."
                )


# ---------------------------------------------------------------------------
# MCP surface guard — include_unreviewed must be reachable end-to-end.
# ---------------------------------------------------------------------------


class TestMcpSurfaceExposesFlag:
    """Without these wirings the SQL flag is dead at the MCP boundary."""

    def test_tools_schema_declares_include_unreviewed(self):
        schema_py = (REPO_ROOT / "mcp-memory" / "tools_schema.py").read_text(encoding="utf-8")
        # memory_recall tool block must declare include_unreviewed property.
        recall_tool_idx = schema_py.find('name="memory_recall"')
        assert recall_tool_idx != -1, "memory_recall tool block not found"
        # Take the next ~3000 chars as the tool block extent (more than enough
        # for the inputSchema).
        block = schema_py[recall_tool_idx : recall_tool_idx + 3000]
        assert '"include_unreviewed"' in block, (
            "memory_recall tool schema must declare include_unreviewed property — "
            "otherwise the recall.py flag is unreachable from MCP callers."
        )

    def test_handler_reads_and_threads_include_unreviewed(self):
        handler_py = (REPO_ROOT / "mcp-memory" / "handlers" / "memory.py").read_text(
            encoding="utf-8"
        )
        # _handle_recall must read it from args:
        assert 'args.get("include_unreviewed"' in handler_py, (
            "_handle_recall must read include_unreviewed from args"
        )
        # _hybrid_recall must thread it into dataclasses.replace(...):
        # Heuristic: assert `include_unreviewed=` appears in the replace call.
        replace_idx = handler_py.find("dataclasses.replace(")
        assert replace_idx != -1
        replace_block = handler_py[replace_idx : replace_idx + 500]
        assert "include_unreviewed=" in replace_block, (
            "_hybrid_recall must pass include_unreviewed to dataclasses.replace"
        )

    def test_keyword_recall_applies_always_gate(self):
        """_keyword_recall (the embed-fallback) must enforce requires_review filter.

        Regression: round-2 _keyword_recall queried `memories` without any
        `requires_review` filter, so a VoyageAI outage surfaced pending review
        candidates to production callers — bypassing the SQL-side always-gate.
        """
        handler_py = (REPO_ROOT / "mcp-memory" / "handlers" / "memory.py").read_text(
            encoding="utf-8"
        )
        # Locate _keyword_recall body:
        kw_start = handler_py.find("async def _keyword_recall(")
        assert kw_start != -1, "_keyword_recall not found"
        # Take body until next top-level `async def` or `def `:
        next_def = handler_py.find("\nasync def ", kw_start + 1)
        if next_def == -1:
            next_def = len(handler_py)
        kw_body = handler_py[kw_start:next_def]
        assert 'eq("requires_review"' in kw_body or 'is_("requires_review"' in kw_body, (
            "_keyword_recall body must apply a requires_review filter — "
            "the always-gate is enforced server-side in the SQL RPCs and must "
            "be mirrored on this fallback path."
        )
        assert 'is_("merge_targets"' in kw_body or 'eq("merge_targets"' in kw_body, (
            "_keyword_recall body must filter out merge_targets rows "
            "(merge proposals are meta-rows, never knowledge)."
        )
