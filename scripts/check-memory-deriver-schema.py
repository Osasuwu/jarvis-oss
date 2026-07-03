#!/usr/bin/env python3
"""Pre-migration collision precheck for memory-deriver Slice 1 (#552).

Queries information_schema.columns for the `memories` table and asserts
that `superseded_by` either does not exist OR has compatible semantics.
Aborts with a non-zero exit code if incompatible.

Also verifies the existing `confidence` column is a numeric type (real,
numeric, float4, float8) so the deriver subsystem can store confidence
scores without a type migration.

Usage:
    python scripts/check-memory-deriver-schema.py  # uses execute_sql MCP tool
    # or direct SQL copy in Supabase SQL Editor:
    #   SELECT column_name, data_type, is_nullable, column_default
    #   FROM information_schema.columns
    #   WHERE table_name = 'memories' AND table_schema = 'public';
"""

import json
import os
import subprocess
import sys


def run_mcp_tool(tool_name: str, args: dict) -> dict:
    """Invoke an MCP tool via `mcp-tool` CLI if available."""
    cmd = ["mcp-tool", tool_name, json.dumps(args)]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    if result.returncode != 0:
        raise RuntimeError(f"MCP tool {tool_name} failed: {result.stderr}")
    return json.loads(result.stdout)


def check_columns(rows: list[dict]) -> None:
    """Validate memories columns are compatible with deriver subsystem."""
    expected = {
        "requires_review": {"type": "boolean", "nullable": False},
        "confidence": {"type": None, "nullable": True},  # any numeric type
        "derivation_run_id": {"type": "uuid", "nullable": True},
        "merge_targets": {"type": "ARRAY", "nullable": True, "element_type": "uuid"},
    }
    # Numeric-family types acceptable for the `confidence` column. Hoisted
    # to function scope so the new-column compatibility loop below can
    # reference it even when `confidence` is absent from the existing
    # schema.
    numeric_types = {"real", "numeric", "double precision", "float4", "float8"}

    # Existing columns we check compatibility against
    existing = {}

    col_lines = []
    for row in rows:
        name = row["column_name"]
        data_type = row["data_type"].lower() if row.get("data_type") else ""
        is_nullable = row.get("is_nullable", "").upper() == "YES"
        default = row.get("column_default", "")
        existing[name] = {
            "type": data_type,
            "nullable": is_nullable,
            "default": default,
        }
        col_lines.append(f"  {name:30s} {data_type:15s} nullable={is_nullable}")

    print("Existing `memories` columns:")
    for line in sorted(col_lines):
        print(line)

    # ---- superseded_by collision check ----
    if "superseded_by" in existing:
        sb = existing["superseded_by"]
        sb_type = sb["type"]
        if sb_type not in ("uuid",):
            print(
                f"\nERROR: superseded_by exists with type {sb_type}, expected uuid. "
                "Migration must handle this type; aborting.",
                file=sys.stderr,
            )
            sys.exit(1)
        if not sb["nullable"]:
            print(
                "\nERROR: superseded_by exists but is NOT NULL. "
                "Deriver subsystem expects nullable.",
                file=sys.stderr,
            )
            sys.exit(1)
        print(f"\nOK: superseded_by exists as uuid, nullable — compatible.")
    else:
        print(f"\nINFO: superseded_by does not exist — will be added by migration.")

    # ---- confidence compatibility (optional, informational) ----
    if "confidence" in existing:
        c = existing["confidence"]
        if c["type"] not in numeric_types:
            print(
                f"\nWARNING: confidence exists as {c['type']}, expected numeric type. "
                "Deriver expects NUMERIC / REAL — verify compatibility before proceeding.",
                file=sys.stderr,
            )

    # ---- New columns: check they don't exist with wrong type ----
    for col, spec in expected.items():
        if col in existing:
            if spec["type"] and existing[col]["type"] != spec["type"]:
                if col == "confidence":
                    # Confidence tolerates numeric-family types
                    if existing[col]["type"] in numeric_types:
                        continue
                print(
                    f"\nWARNING: {col} exists as {existing[col]['type']}, "
                    f"expected {spec['type']}. Verify compatibility.",
                    file=sys.stderr,
                )

    print("\nPrecheck complete (see warnings above if any).")


def main():
    # Determine run mode
    supabase_url = os.environ.get("SUPABASE_URL")
    supabase_key = os.environ.get("SUPABASE_KEY")

    if supabase_url and supabase_key:
        # Direct Supabase REST query
        import urllib.request

        sql = """
            SELECT column_name, data_type, is_nullable, column_default
            FROM information_schema.columns
            WHERE table_name = 'memories'
              AND table_schema = 'public'
            ORDER BY ordinal_position;
        """
        url = f"{supabase_url}/rest/v1/rpc/execute_sql"

        headers = {
            "Content-Type": "application/json",
            "apikey": supabase_key,
            "Authorization": f"Bearer {supabase_key}",
        }
        req = urllib.request.Request(
            url,
            data=json.dumps({"query": sql}).encode(),
            headers=headers,
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            rows = json.loads(resp.read().decode())
    else:
        # Fallback: run via MCP memory tool
        try:
            result = run_mcp_tool("execute_sql", {
                "query": """
                    SELECT column_name, data_type, is_nullable, column_default
                    FROM information_schema.columns
                    WHERE table_name = 'memories'
                      AND table_schema = 'public'
                    ORDER BY ordinal_position;
                """
            })
            rows = result.get("rows", [])
        except (FileNotFoundError, RuntimeError) as exc:
            print(
                f"Cannot reach database: {exc}\n"
                "Run this script with SUPABASE_URL + SUPABASE_KEY set, or "
                "execute the SQL manually in the Supabase SQL Editor:\n\n"
                "  SELECT column_name, data_type, is_nullable, column_default\n"
                "  FROM information_schema.columns\n"
                "  WHERE table_name = 'memories'\n"
                "    AND table_schema = 'public'\n"
                "  ORDER BY ordinal_position;\n",
                file=sys.stderr,
            )
            sys.exit(2)

    check_columns(rows)


if __name__ == "__main__":
    main()
