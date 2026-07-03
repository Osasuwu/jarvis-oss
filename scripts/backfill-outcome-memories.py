"""Backfill task_outcomes.memory_id from decision_made episodes (#288).

Every /implement run emits a decision_made episode whose payload carries
`memories_used` — the names of the memories the decision was based on.
The `task_outcomes.memory_id` FK then joins outcome rows back to those
memories so the `memory_calibration` view can score calibration per
memory. The FK column exists on the DB, but until #286 the MCP tool
schema didn't accept it — so every historical outcome has memory_id=NULL.

This script recovers the link retroactively.

## Join strategy

1. Read every `episodes` row where `kind='decision_made'` and
   `payload.memories_used` is non-empty.
2. Extract `#N` references from `payload.decision`. Keep only episodes
   that mention **exactly one** `#N` — multi-issue decisions (sprint
   planners, batch triages) are too ambiguous to attribute.
3. Map N -> (primary_memory_name, episode_id). When multiple episodes
   reference the same N, the most-recent one wins.
4. For each `task_outcomes` row with `memory_id IS NULL`, extract the
   issue # from `issue_url` and the PR # from `pr_url`. Either match
   against N is accepted (decisions sometimes quote PR #, sometimes
   issue #).
5. Resolve the memory name to `memories.id` — most-recently-updated
   live row wins if multiple memories share a name.
6. Update task_outcomes.memory_id, guarded by `memory_id IS NULL` at
   write time (true idempotency even under concurrent writes).

## Spec drift note

Issue body says "events table" and "payload contains pr_url" — both
wrong against the live DB. Decisions actually live in `episodes`
(kind='decision_made'), payload holds `decision` text + `memories_used`
(list of names, not UUIDs). This script matches the live shape.

## Usage

    python scripts/backfill-outcome-memories.py           # dry-run (default)
    python scripts/backfill-outcome-memories.py --apply   # persist updates

Environment: SUPABASE_URL, SUPABASE_KEY (or SUPABASE_SERVICE_KEY — either
works; the service key gets more privilege but isn't required for this
update path since PostgREST allows the owner role to update task_outcomes).
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent

try:
    from dotenv import load_dotenv

    for _env_path in [_HERE.parent / ".env", _HERE.parent.parent / ".env"]:
        if _env_path.exists():
            load_dotenv(_env_path, override=True)
            break
except ImportError:
    pass


_ISSUE_URL_RE = re.compile(r"/issues/(\d+)")
_PR_URL_RE = re.compile(r"/pull/(\d+)")
_HASH_RE = re.compile(r"#(\d+)")


def _parse_issue_number(url: str | None) -> int | None:
    if not url:
        return None
    m = _ISSUE_URL_RE.search(url)
    return int(m.group(1)) if m else None


def _parse_pr_number(url: str | None) -> int | None:
    if not url:
        return None
    m = _PR_URL_RE.search(url)
    return int(m.group(1)) if m else None


def _extract_single_hash(text: str | None) -> int | None:
    """Return the single `#N` in text, or None if zero or multiple."""
    if not text:
        return None
    hits = _HASH_RE.findall(text)
    if len(hits) != 1:
        return None
    return int(hits[0])


def _client():
    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_SERVICE_KEY") or os.environ.get("SUPABASE_KEY")
    if not url or not key:
        print("ERROR: SUPABASE_URL / SUPABASE_KEY (or SUPABASE_SERVICE_KEY) unset", file=sys.stderr)
        sys.exit(1)
    from supabase import create_client

    return create_client(url, key)


def _build_hash_to_memory_index(client) -> dict[int, tuple[str, str, str]]:
    """Map `#N` -> (primary_memory_name, episode_id, decision_preview).

    Scans `episodes` oldest-first so the most-recent decision for each N
    overwrites earlier ones in the dict.
    """
    result = (
        client.table("episodes")
        .select("id, payload, created_at")
        .eq("kind", "decision_made")
        .order("created_at", desc=False)
        .execute()
    )

    idx: dict[int, tuple[str, str, str]] = {}
    for ep in result.data or []:
        payload = ep.get("payload") or {}
        memories_used = payload.get("memories_used") or []
        if not memories_used:
            continue
        n = _extract_single_hash(payload.get("decision", ""))
        if n is None:
            continue
        primary_name = memories_used[0]
        if not isinstance(primary_name, str) or not primary_name:
            continue
        preview = (payload.get("decision") or "")[:80]
        idx[n] = (primary_name, ep["id"], preview)
    return idx


def _resolve_memory_name(client, name: str) -> str | None:
    """Resolve memories.name -> id, preferring the most recently updated live row."""
    result = (
        client.table("memories")
        .select("id, updated_at")
        .eq("name", name)
        .is_("deleted_at", "null")
        .order("updated_at", desc=True)
        .limit(1)
        .execute()
    )
    if result.data:
        return result.data[0]["id"]
    return None


def _fetch_null_outcomes(client) -> list[dict]:
    result = (
        client.table("task_outcomes")
        .select("id, task_description, issue_url, pr_url, created_at")
        .is_("memory_id", "null")
        .order("created_at", desc=True)
        .execute()
    )
    return result.data or []


def backfill(apply: bool) -> int:
    client = _client()

    index = _build_hash_to_memory_index(client)
    print(f"Indexed {len(index)} issue/PR # -> memory links from decision_made episodes.")
    for n, (name, ep_id, preview) in sorted(index.items()):
        print(f"  #{n} -> {name!r}  (episode {ep_id[:8]}, decision: {preview!r})")

    candidates = _fetch_null_outcomes(client)
    print(f"\nFound {len(candidates)} task_outcomes with memory_id=NULL.")

    linked: list[tuple[str, str, int, str, str]] = []
    skipped_no_url = 0
    skipped_no_decision = 0
    unresolved: set[str] = set()

    # Cache memory-name -> id to avoid hammering the memories table.
    name_cache: dict[str, str | None] = {}

    for oc in candidates:
        issue_n = _parse_issue_number(oc.get("issue_url"))
        pr_n = _parse_pr_number(oc.get("pr_url"))
        if issue_n is None and pr_n is None:
            skipped_no_url += 1
            continue

        match = None
        for n in (issue_n, pr_n):
            if n is not None and n in index:
                match = index[n]
                matched_n = n
                break
        if match is None:
            skipped_no_decision += 1
            continue

        primary_name, _ep_id, _preview = match
        if primary_name not in name_cache:
            name_cache[primary_name] = _resolve_memory_name(client, primary_name)
        memory_id = name_cache[primary_name]
        if memory_id is None:
            unresolved.add(primary_name)
            continue

        desc = (oc.get("task_description") or "")[:60]
        linked.append((oc["id"], memory_id, matched_n, primary_name, desc))

    total = len(candidates)
    print(
        f"\n=== Plan: {len(linked)} linkable, "
        f"{skipped_no_url} missing issue/PR url, "
        f"{skipped_no_decision} no matching decision, "
        f"{len(unresolved)} memory names unresolved "
        f"(of {total} candidates) ===\n"
    )
    for out_id, mem_id, n, name, desc in linked:
        print(f"  #{n}  outcome {out_id[:8]} -> memory {mem_id[:8]} ({name})  | {desc}")
    if unresolved:
        print(f"\nUnresolved memory names (renamed/deleted?): {sorted(unresolved)}")

    if not apply:
        print("\n(dry-run — default — no changes made; rerun with --apply to persist)")
        return 0

    if not linked:
        print("\nNothing to apply.")
        return 0

    print("\n--apply — writing updates...")
    applied = 0
    for out_id, mem_id, n, name, _desc in linked:
        # Guard with `memory_id IS NULL` at write time — true idempotency
        # even if the script races with a concurrent /implement writing
        # memory_id via outcome_record.
        result = (
            client.table("task_outcomes")
            .update({"memory_id": mem_id})
            .eq("id", out_id)
            .is_("memory_id", "null")
            .execute()
        )
        if result.data:
            applied += 1
            print(f"  [OK]   #{n} outcome {out_id[:8]} -> memory {mem_id[:8]} ({name})")
        else:
            print(f"  [SKIP] outcome {out_id[:8]} — already linked or vanished")

    print(f"\nApplied {applied}/{len(linked)} updates.")
    return 0 if applied == len(linked) else 2


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Backfill task_outcomes.memory_id from decision_made episodes."
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Actually write updates. Default is dry-run.",
    )
    args = parser.parse_args()
    return backfill(apply=args.apply)


if __name__ == "__main__":
    sys.exit(main())
