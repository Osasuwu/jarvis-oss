"""Jarvis Status MCP Server — status_digest tool.

Thin wrapper around status_gather (I/O adapter) and status_engine (pure function).

Public interface:
    status_digest: Single tool that calls gather() → analyze() and returns digest.

Usage in .mcp.json:
{
  "status": {
    "type": "stdio",
    "command": "python",
    "args": ["mcp-status/server.py"],
    "env": {
      "SUPABASE_URL": "https://xxx.supabase.co",
      "SUPABASE_KEY": "eyJ...",
    }
  }
}
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

# Timeout for synchronous gather() running off the event loop.
# A hung subprocess (gh/git) should not block the MCP server indefinitely.
_GATHER_TIMEOUT = 30.0  # seconds

# Repo root must be on sys.path BEFORE the `from scripts.*` imports below.
# When launched as a script (`python mcp-status/server.py`), sys.path[0] is
# the script's own dir (mcp-status/), NOT the repo root — so `scripts.*`
# would not resolve. pytest masks this because it injects rootdir. Insert
# the repo root (parent of mcp-status/) explicitly.
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

# NOTE: deliberately NOT aliasing this module to the global name "server".
# mcp-memory/server.py uses `sys.modules.setdefault("server", ...)` because its
# test suite imports `from server import ...`; copying that here makes the two
# server.py files race for the single global name "server" — whichever test is
# collected first wins, breaking the other (full-suite collision, #1017). This
# server is launched as `__main__` and nothing internally imports "server", so
# the alias served no purpose here. Tests load it under a unique module name.

# noqa: E402 — .env loaded before MCP/script imports (required, follows mcp-memory pattern)
from dotenv import load_dotenv  # noqa: E402

# Load .env from repo root (two levels up from mcp-status/server.py).
#
# override=True lets .env win for SUPABASE_* (the vars this server actually
# needs), but it must NOT clobber auth tokens the harness/shell already
# injected: a stale GITHUB_TOKEN/GH_TOKEN in .env would 401 every gh call in
# gather(), silently degrading the digest to empty for BOTH repos (the live
# token from the environment is the source of truth for gh, not the .env copy).
# Snapshot the pre-existing tokens and restore them after the load.
_preserved_tokens = {_k: os.environ[_k] for _k in ("GITHUB_TOKEN", "GH_TOKEN") if _k in os.environ}
_env_candidates = [
    Path(__file__).resolve().parent.parent / ".env",
    Path(__file__).resolve().parent.parent.parent / ".env",
]
for _env_path in _env_candidates:
    if _env_path.exists():
        load_dotenv(_env_path, override=True)
        break
os.environ.update(_preserved_tokens)

from mcp.server import Server  # noqa: E402
from mcp.server.stdio import stdio_server  # noqa: E402
from mcp.types import CallToolResult, TextContent, Tool  # noqa: E402

# Import gather and engine modules
from scripts.status_gather import gather  # noqa: E402
from scripts.status_engine import (  # noqa: E402
    analyze,
    deserialize_contradiction_cache,
)

# ============================================================================
# MCP Server
# ============================================================================

server = Server("jarvis-status")


# ============================================================================
# Tool registration
# ============================================================================


@server.list_tools()
async def list_tools() -> list[Tool]:
    """Return the single status_digest tool."""
    return [
        Tool(
            name="status_digest",
            description=(
                "Synthesize a status digest by gathering current repo state "
                "and analyzing it for anomalies. Wraps gather() → engine "
                "in a single call. Returns {health, detector_hits, ranking, "
                "provenance}."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "jarvis_home": {
                        "type": "string",
                        "description": (
                            "Root path of the jarvis repo. If empty, auto-detects "
                            "from CWD via git rev-parse."
                        ),
                    },
                },
                "required": [],
            },
        ),
    ]


# ============================================================================
# Tool dispatch
# ============================================================================


def _convert_gather_to_engine_format(gather_result):
    """Convert GatherResult to Baseline/Delta/decisions for engine.analyze()."""
    from scripts.status_engine import (
        Baseline,
        Delta,
        Provenance,
        RepoState,
        IssueInfo,
        DecisionInfo,
    )

    baseline = Baseline(gathered_at="", repos={}, provenance={})
    delta = Delta(gathered_at=gather_result.gathered_at, repos={})

    # Convert repo data to engine format
    for repo_entry in gather_result.repos:
        repo_name = repo_entry.get("name", "")
        issues_data = repo_entry.get("issues", [])
        prs_data = repo_entry.get("prs", [])

        # Convert issues to IssueInfo
        issues: list[IssueInfo] = []
        for issue in issues_data:
            # gh returns labels as objects [{"name":..,"color":..}]; the engine
            # (_issue_priority) treats each label as a hashable string. Flatten
            # to names so priority detection doesn't crash on unhashable dicts.
            raw_labels = issue.get("labels", []) or []
            labels = [lbl.get("name", "") if isinstance(lbl, dict) else lbl for lbl in raw_labels]
            issues.append(
                IssueInfo(
                    number=issue.get("number", 0),
                    title=issue.get("title", ""),
                    state="open",  # gather filters to open issues
                    labels=labels,
                    milestone=issue.get("milestone"),
                    updated_at=issue.get("updatedAt", ""),
                )
            )

        # Create RepoState for delta (most recent data)
        repo_state = RepoState(
            repo=repo_name,
            open_issues=issues,
            open_prs=prs_data or [],
            provenance=None,
        )

        # Attach per-repo provenance from gather onto the RepoState so the
        # engine and digest see it (previously built into a local that was
        # never used — the RepoState went out with provenance=None).
        if "provenance" in repo_entry:
            repo_prov = repo_entry["provenance"]
            # Use the first source stamp as the repo-level provenance, mirroring
            # how gather stamps a single {ran, ok, input_rows, age} per source.
            for _src, prov_dict in repo_prov.items():
                repo_state.provenance = Provenance(
                    ran=prov_dict.get("ran", False),
                    ok=prov_dict.get("ok", False),
                    input_rows=prov_dict.get("input_rows", 0),
                    age=prov_dict.get("age", 0.0),
                )
                break

        delta.repos[repo_name] = repo_state

    # Convert top-level provenance from gather
    for source_key, prov_dict in gather_result.provenance.items():
        baseline.provenance[source_key] = Provenance(
            ran=prov_dict.get("ran", False),
            ok=prov_dict.get("ok", False),
            input_rows=prov_dict.get("input_rows", 0),
            age=prov_dict.get("age", 0.0),
        )

    # Convert decisions
    decisions: list[DecisionInfo] = []
    for decision_rec in gather_result.decisions:
        payload = decision_rec.payload or {}
        decisions.append(
            DecisionInfo(
                decision_id=decision_rec.id,
                decision=payload.get("decision", ""),
                created_at=decision_rec.created_at,
                project=payload.get("project"),
            )
        )

    return baseline, delta, decisions


def _contradiction_verdicts_from_gather(gather_result):
    """Deserialize the cached L1 contradiction verdicts from a GatherResult.

    The L1 status-record audit ran the memory↔git contradiction LLM once and
    stored its verdicts in the status-snapshot memory; gather() read that cache
    back into `gather_result.contradiction_cache` (pure deserialization, no
    LLM). Here we turn the cache dict into ContradictionVerdict objects for
    analyze() to fold (#1016 AC4). No cache → empty list, which is the
    intraday/L2 default that folds nothing.
    """
    cache = gather_result.contradiction_cache
    if cache is None:
        return []
    # An empty-but-present dict still flows through deserialize (→ []); only a
    # genuinely absent cache short-circuits. `is None` keeps the two distinct.
    return deserialize_contradiction_cache(cache)


@server.call_tool()
async def call_tool(name: str, arguments: dict) -> list[TextContent] | CallToolResult:
    """Dispatch to the status_digest tool."""
    try:
        if name == "status_digest":
            jarvis_home = arguments.get("jarvis_home", "")

            # Call gather off the event loop via thread pool, with a timeout.
            # gather() makes blocking subprocess/HTTP calls; running it on the
            # event loop would stall the MCP server for all other requests.
            try:
                gather_result = await asyncio.wait_for(
                    asyncio.to_thread(gather, jarvis_home),
                    timeout=_GATHER_TIMEOUT,
                )
            except asyncio.TimeoutError:
                return [TextContent(
                    type="text",
                    text="status gather timed out after 30s",
                )]

            # Convert gather result to engine format
            baseline, delta, decisions = _convert_gather_to_engine_format(gather_result)

            # Fold the cached L1 memory↔git contradiction verdicts (if any).
            # Reading the cache is pure deserialization — the LLM ran once in
            # the L1 status-record audit, never here (#1016 AC2/AC4).
            contradiction_verdicts = _contradiction_verdicts_from_gather(gather_result)

            # Analyze with engine
            digest = analyze(
                baseline,
                delta,
                decisions,
                contradiction_verdicts=contradiction_verdicts,
            )

            # Build response: {health, detector_hits, ranking, provenance}
            response_data = {
                "health": {
                    "ok": digest.health.ok,
                    "reason": digest.health.reason,
                },
                "detector_hits": [
                    {
                        "detector": hit.detector,
                        "severity": hit.severity,
                        "repo": hit.repo,
                        "issue_number": hit.issue_number,
                        "title": hit.title,
                        "description": hit.description,
                    }
                    for hit in digest.detector_hits
                ],
                "ranking": [
                    {
                        "rank": item.rank,
                        "detector_hit": {
                            "detector": item.detector_hit.detector,
                            "severity": item.detector_hit.severity,
                            "repo": item.detector_hit.repo,
                            "issue_number": item.detector_hit.issue_number,
                            "title": item.detector_hit.title,
                            "description": item.detector_hit.description,
                        },
                        "reason": item.reason,
                    }
                    for item in digest.ranking
                ],
                "provenance": {
                    key: {
                        "ran": prov.ran,
                        "ok": prov.ok,
                        "input_rows": prov.input_rows,
                        "age": prov.age,
                    }
                    for key, prov in digest.provenance.items()
                },
            }

            import json

            result_text = json.dumps(response_data, indent=2, default=str)
            return [TextContent(type="text", text=result_text)]

        else:
            return [TextContent(type="text", text=f"Unknown tool: {name}")]

    except Exception as exc:
        import traceback

        traceback.print_exc()  # Log full traceback server-side
        return [TextContent(type="text", text=f"Error in {name}: {exc}")]


# ============================================================================
# Main
# ============================================================================


async def main():
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())


if __name__ == "__main__":
    asyncio.run(main())
