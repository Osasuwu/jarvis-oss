"""Memory consolidation — Haiku merge-plan generator (dry-run + apply).

Takes the clusters that `scripts/consolidation-report.py` surfaces
and asks Claude Haiku-4.5 to emit one of:

    MERGE         — members hold partial views of the same fact; synthesize
                    a new canonical memory combining them
    SUPERSEDE     — one member is current/correct, others are stale; mark
                    the stale ones expired
    KEEP_DISTINCT — same topic but different purposes; leave them alone

Default mode is dry-run. Pass `--apply` (Phase 5.1b-β) to persist:

  * MERGE / SUPERSEDE, confidence >= gate → apply_consolidation_plan RPC,
    embedding backfill for MERGE, queue row status=auto_applied, event log.
  * MERGE / SUPERSEDE, confidence <  gate → queue row status=pending.
  * KEEP_DISTINCT (any confidence)        → queue row status=auto_applied.

Clusters whose exact member-set already has a queue entry (any status
except `rolled_back`) are skipped before the Haiku call — prevents
re-spending tokens on the same cluster every week.

Output: markdown report (default) or JSON. `--save-memory` upserts the
markdown as `consolidation_plan_YYYY-MM-DD` (`type=project`).

The Haiku call follows the same pattern as `mcp-memory/classifier.py`
(httpx, tolerant JSON parse, graceful fallback to KEEP_DISTINCT on
network/parse failure). No SDK dependency.

Usage:
    python scripts/consolidation-merge-plan.py                       # markdown, dry-run
    python scripts/consolidation-merge-plan.py --json                # machine-readable
    python scripts/consolidation-merge-plan.py --min-size 3 --threshold 0.80
    python scripts/consolidation-merge-plan.py --apply --limit 10    # real mutations
    python scripts/consolidation-merge-plan.py --apply --confidence-gate 0.9

Requires SUPABASE_URL, SUPABASE_KEY, ANTHROPIC_API_KEY. VOYAGE_API_KEY
needed for --apply (canonical embedding). .env auto-loaded.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

# Windows cp1251 console can't encode em-dashes / arrows / Cyrillic —
# force UTF-8 so output works on all 3 devices. Safe no-op elsewhere.
try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

try:
    from dotenv import load_dotenv

    here = Path(__file__).resolve().parent
    for c in (here.parent / ".env", here.parent.parent / ".env"):
        if c.exists():
            # override=True: empty-string shell vars (observed in this repo's
            # login shell) don't win over the real value in .env.
            load_dotenv(c, override=True)
            break
except ImportError:
    pass

import httpx
from supabase import create_client


DEFAULT_MIN_SIZE = 3
DEFAULT_THRESHOLD = 0.80
DEFAULT_MODEL = "claude-haiku-4-5"
DEFAULT_TIMEOUT = 15.0  # per-cluster; clusters are larger than single-write classifier
DEFAULT_CONFIDENCE_GATE = 0.85  # owner-decided 2026-04-19 (#221)
DEFAULT_APPLY_LIMIT = 20

ANTHROPIC_API_URL = "https://api.anthropic.com/v1/messages"
ANTHROPIC_VERSION = "2023-06-01"
MAX_TOKENS = 1500  # room for canonical_content on MERGE
MAX_MEMBER_CONTENT_CHARS = 800  # truncate long memories before sending

VOYAGE_API_URL = "https://api.voyageai.com/v1/embeddings"
VOYAGE_MODEL = "voyage-3-lite"
VOYAGE_TIMEOUT = 30.0
MAX_CANONICAL_TAGS = 15  # trim merged tag UNION — stops tag explosion

VALID_DECISIONS = ("MERGE", "SUPERSEDE", "KEEP_DISTINCT")

# Maps Haiku's output labels to the DB-side CHECK values for
# memory_review_queue.decision. SUPERSEDE → SUPERSEDE_CONSOLIDATION
# disambiguates from Phase 2 classifier's single-candidate UPDATE/DELETE.
DB_DECISION = {
    "MERGE": "MERGE",
    "SUPERSEDE": "SUPERSEDE_CONSOLIDATION",
    "KEEP_DISTINCT": "KEEP_DISTINCT",
}


SYSTEM_PROMPT = """You are a memory-consolidation planner for a personal AI agent's long-term memory store.

You receive a CLUSTER of memories that are semantically similar (cosine similarity above threshold). Decide one of:

- MERGE: Members hold partial, complementary views of the same underlying fact. A unified memory would serve strictly better than the parts. Synthesize canonical_name, canonical_description, canonical_content combining the best information from all members.
- SUPERSEDE: One member is the current/correct version; the others are stale, wrong, or incomplete and should be marked expired. Pick canonical_id from the existing members.
- KEEP_DISTINCT: Members cover the same topic but serve different purposes (e.g., reflection + results + status of the same event; decisions in the same area with different scope; notes from different dates). Leave them alone.

Rules:
  - Different types (user vs project vs decision) usually mean KEEP_DISTINCT — the type carries semantic meaning.
  - Different dates or events referenced inside the content → lean KEEP_DISTINCT. Only MERGE/SUPERSEDE if they genuinely refer to the same fact.
  - Supersession requires evidence that one is both newer AND more correct. Not just more recent.
  - Merging should produce a memory strictly better than any single member. If the members are already useful individually, prefer KEEP_DISTINCT.
  - Be conservative. When in doubt, KEEP_DISTINCT.
  - Confidence: 0.9+ for unambiguous cases; 0.5-0.7 for judgment calls; <0.5 when guessing.

Output strict JSON, nothing else. No prose before or after.

Schema:
{
  "decision": "MERGE" | "SUPERSEDE" | "KEEP_DISTINCT",
  "canonical_id": "<uuid of winning member>" | null,
  "supersede_ids": ["<uuid>", ...],
  "canonical_name": "<snake_case identifier>" | null,
  "canonical_description": "<one sentence summary>" | null,
  "canonical_content": "<full merged content>" | null,
  "confidence": <float 0..1>,
  "reasoning": "<one short sentence>"
}

Field rules by decision:
  - MERGE: canonical_id = null; canonical_name / description / content REQUIRED; supersede_ids = ALL cluster member ids.
  - SUPERSEDE: canonical_id = id of the winning member; canonical_* may be null; supersede_ids = the other ids (not including the winner).
  - KEEP_DISTINCT: canonical_id = null; canonical_* = null; supersede_ids = [].
"""


def fetch_clusters(client, min_size: int, threshold: float) -> list[dict]:
    """Call find_consolidation_clusters RPC."""
    resp = client.rpc(
        "find_consolidation_clusters",
        {"min_cluster_size": min_size, "sim_threshold": threshold},
    ).execute()
    return resp.data or []


def fetch_member_details(client, memory_ids: list[str]) -> dict[str, dict]:
    """Fetch full details (description, tags, content, lifecycle) for Haiku input."""
    if not memory_ids:
        return {}
    rows = (
        client.table("memories")
        .select(
            "id, name, type, project, description, tags, content, "
            "expired_at, valid_to, superseded_by, deleted_at, "
            "updated_at, content_updated_at"
        )
        .in_("id", memory_ids)
        .execute()
        .data
    )
    return {r["id"]: r for r in rows}


def _parse_ts(ts) -> datetime | None:
    if ts is None:
        return None
    if isinstance(ts, datetime):
        return ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)
    try:
        return datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None


def is_live(row: dict) -> bool:
    """Phase 1 live-filter (kept in sync with server.py `_hybrid_recall`).

    RPC already filters at SQL source (Phase 5.1c), so this is a defensive
    no-op against current DB revs.
    """
    if row.get("expired_at") is not None:
        return False
    if row.get("superseded_by") is not None:
        return False
    if row.get("deleted_at") is not None:
        return False
    valid_to = _parse_ts(row.get("valid_to"))
    if valid_to is not None and valid_to <= datetime.now(timezone.utc):
        return False
    return True


def _truncate(text: str, limit: int = MAX_MEMBER_CONTENT_CHARS) -> str:
    if not text:
        return ""
    return text if len(text) <= limit else text[:limit] + "…"


def group_clusters(rpc_rows: list[dict], details_by_id: dict[str, dict]) -> list[dict]:
    """Re-group RPC rows by cluster_id, drop dead memories, dedupe by id.

    Content is truncated at `MAX_MEMBER_CONTENT_CHARS` — it's what Haiku will
    see and what ends up in `--json` output; keeping full text on every
    cluster member bloats output for large memories with no benefit.
    """
    by_cluster: dict[int, dict[str, dict]] = defaultdict(dict)
    for r in rpc_rows:
        mid = r["memory_id"]
        details = details_by_id.get(mid)
        if details is None or not is_live(details):
            continue
        sim = float(r["similarity"])
        existing = by_cluster[r["cluster_id"]].get(mid)
        if existing and existing["similarity"] >= sim:
            continue
        by_cluster[r["cluster_id"]][mid] = {
            "id": mid,
            "name": r["memory_name"],
            "type": r["memory_type"],
            "project": details.get("project") or "",
            "similarity": sim,
            "updated_at": r["updated_at"],
            "description": details.get("description") or "",
            "tags": details.get("tags") or [],
            "content": _truncate(details.get("content") or ""),
        }

    clusters: list[dict] = []
    for cid, by_id in by_cluster.items():
        members = sorted(by_id.values(), key=lambda m: m["updated_at"], reverse=True)
        max_sim = max(m["similarity"] for m in members)
        clusters.append(
            {
                "cluster_id": cid,
                "size": len(members),
                "max_similarity": round(max_sim, 4),
                "types": sorted({m["type"] for m in members}),
                "projects": sorted({m["project"] for m in members}),
                "members": members,
            }
        )
    clusters.sort(key=lambda c: (c["size"], c["max_similarity"]), reverse=True)
    return clusters


def _build_user_message(cluster: dict) -> str:
    lines = [
        f"CLUSTER {cluster['cluster_id']} — {cluster['size']} members, "
        f"max_similarity={cluster['max_similarity']:.3f}, types={', '.join(cluster['types'])}",
        "",
        "MEMBERS:",
    ]
    for m in cluster["members"]:
        block = [
            f"- id: {m['id']}",
            f"  name: {m['name']}",
            f"  type: {m['type']}",
            f"  updated_at: {m['updated_at']}",
            f"  similarity_to_cluster: {m['similarity']:.3f}",
        ]
        if m.get("tags"):
            block.append(f"  tags: {', '.join(m['tags'])}")
        if m.get("description"):
            block.append(f"  description: {m['description']}")
        if m.get("content"):
            block.append(f"  content: {_truncate(m['content'])}")
        lines.append("\n".join(block))
    return "\n".join(lines)


def _parse_response(text: str, member_ids: list[str]) -> dict | None:
    """Parse Haiku's JSON reply. Tolerant of leading/trailing prose.

    Validates against member_ids so the model can't invent UUIDs.
    Returns None on any unrecoverable issue; caller falls back to KEEP_DISTINCT.
    """
    if not text:
        return None
    first = text.find("{")
    last = text.rfind("}")
    if first < 0 or last <= first:
        return None
    try:
        data = json.loads(text[first : last + 1])
    except json.JSONDecodeError:
        return None

    decision = str(data.get("decision", "")).upper().strip()
    if decision not in VALID_DECISIONS:
        return None

    member_set = set(member_ids)

    canonical_id = data.get("canonical_id")
    if canonical_id in ("", "null", None):
        canonical_id = None
    elif not isinstance(canonical_id, str) or canonical_id not in member_set:
        # Model invented an ID or returned non-string. For SUPERSEDE this is
        # fatal; for other decisions we can null it out.
        if decision == "SUPERSEDE":
            return None
        canonical_id = None

    supersede_ids_raw = data.get("supersede_ids") or []
    if not isinstance(supersede_ids_raw, list):
        supersede_ids_raw = []
    supersede_ids = [s for s in supersede_ids_raw if isinstance(s, str) and s in member_set]

    # Cross-field consistency checks — downgrade to KEEP_DISTINCT with low
    # confidence if Haiku contradicts its own decision, rather than silently
    # trusting garbage. These checks run BEFORE invariant normalization so
    # the downgrade reason reflects what Haiku actually said.
    if decision == "SUPERSEDE" and canonical_id is None:
        return _downgrade(data, "SUPERSEDE without canonical_id")
    if decision == "MERGE":
        name = data.get("canonical_name")
        content = data.get("canonical_content")
        if not (isinstance(name, str) and name.strip()) or not (
            isinstance(content, str) and content.strip()
        ):
            return _downgrade(data, "MERGE missing canonical_name or canonical_content")

    # Enforce schema invariants — the model's output is advisory; downstream
    # consumers (plan renderer, upcoming 5.1b-β --apply) rely on these
    # invariants rather than trusting that Haiku filled every field
    # consistently with its chosen decision.
    if decision == "MERGE":
        # All members get superseded by the new canonical; no existing id wins.
        canonical_id = None
        supersede_ids = sorted(member_set)
    elif decision == "SUPERSEDE":
        # Exactly one winner from the existing set; every other member loses.
        # Derive supersede_ids deterministically so Haiku can't "forget" a loser.
        supersede_ids = sorted(member_set - {canonical_id})
    else:  # KEEP_DISTINCT
        canonical_id = None
        supersede_ids = []

    try:
        confidence = float(data.get("confidence", 0.5))
    except (TypeError, ValueError):
        confidence = 0.5
    confidence = max(0.0, min(1.0, confidence))

    reasoning = str(data.get("reasoning", "")).strip()[:500]

    return {
        "decision": decision,
        "canonical_id": canonical_id,
        "supersede_ids": supersede_ids,
        "canonical_name": (data.get("canonical_name") or None) if decision == "MERGE" else None,
        "canonical_description": (data.get("canonical_description") or None)
        if decision == "MERGE"
        else None,
        "canonical_content": (data.get("canonical_content") or None)
        if decision == "MERGE"
        else None,
        "confidence": confidence,
        "reasoning": reasoning,
    }


def _downgrade(data: dict, why: str) -> dict:
    """Return a safe KEEP_DISTINCT with low confidence + note."""
    original_reasoning = str(data.get("reasoning", "")).strip()[:300]
    note = f"downgraded ({why})"
    if original_reasoning:
        note = f"{note}: {original_reasoning}"
    return {
        "decision": "KEEP_DISTINCT",
        "canonical_id": None,
        "supersede_ids": [],
        "canonical_name": None,
        "canonical_description": None,
        "canonical_content": None,
        "confidence": 0.2,
        "reasoning": note,
    }


def _fallback_keep_distinct(why: str) -> dict:
    """When the API call itself fails — default to safe no-op."""
    return {
        "decision": "KEEP_DISTINCT",
        "canonical_id": None,
        "supersede_ids": [],
        "canonical_name": None,
        "canonical_description": None,
        "canonical_content": None,
        "confidence": 0.0,
        "reasoning": f"fallback: {why}",
    }


# ---------------------------------------------------------------------------
# Apply path (5.1b-β): queue routing, RPC call, embedding backfill.
# ---------------------------------------------------------------------------


def member_ids_key(ids: list[str]) -> str:
    """Deterministic key for a cluster's member-id set.

    Must match the SQL index expression `consolidation_payload->>'member_ids_key'`
    (schema.sql, Phase 5.1b-β). Sorted + comma-joined keeps it both
    human-readable in the queue row and cheap to index.
    """
    return ",".join(sorted(ids))


def fetch_existing_queue_keys(client, candidate_keys: list[str]) -> set[str]:
    """Return member_ids_keys that already exist in the queue for current candidates.

    Excludes `rolled_back` — those are cases where the owner (or rollback
    script) explicitly wants the cluster reconsidered. Includes `pending`
    because those are awaiting review and we don't need a duplicate row.

    Uses `.in_()` on `consolidation_payload->>member_ids_key` so queries hit
    the functional index (`idx_review_queue_member_ids_key`) instead of a
    full scan — matters once the KEEP_DISTINCT audit set grows large.
    """
    if not candidate_keys:
        return set()
    quoted = ",".join(f'"{k}"' for k in candidate_keys)
    rows = (
        client.table("memory_review_queue")
        .select("consolidation_payload")
        .filter("consolidation_payload->>member_ids_key", "in", f"({quoted})")
        .neq("status", "rolled_back")
        .execute()
        .data
    ) or []
    found: set[str] = set()
    for r in rows:
        payload = r.get("consolidation_payload") or {}
        k = payload.get("member_ids_key")
        if isinstance(k, str) and k:
            found.add(k)
    return found


def canonical_tags_union(members: list[dict]) -> list[str]:
    """UNION of member tags, deterministic order, trimmed.

    Sorted alphabetically so repeated runs produce the same canonical —
    makes the canonical embedding reproducible if we ever need to recompute.
    """
    tags: set[str] = set()
    for m in members:
        for t in m.get("tags") or []:
            if isinstance(t, str) and t.strip():
                tags.add(t.strip())
    return sorted(tags)[:MAX_CANONICAL_TAGS]


def _canonical_embed_text(name: str, description: str, tags: list[str], content: str) -> str:
    """Mirror of mcp-memory/server.py:_canonical_embed_text.

    Must stay byte-exact with server.py or canonicals embed on a slightly
    different axis than neighbours written via MCP — cosine drifts, future
    clustering misses them.
    """
    parts: list[str] = []
    if name:
        parts.append(name.replace("_", " "))
    if tags:
        parts.append("tags: " + ", ".join(tags))
    if description:
        parts.append(description)
    if content:
        parts.append(content)
    return "\n".join(p for p in parts if p).strip()


def embed_document(text: str, *, timeout: float = VOYAGE_TIMEOUT) -> list[float] | None:
    """Sync VoyageAI call. Returns None on any failure (caller retries / skips)."""
    api_key = os.environ.get("VOYAGE_API_KEY")
    if not api_key or not text:
        return None
    try:
        with httpx.Client(timeout=timeout) as http:
            resp = http.post(
                VOYAGE_API_URL,
                headers={"Authorization": f"Bearer {api_key}"},
                json={"model": VOYAGE_MODEL, "input": [text], "input_type": "document"},
            )
            resp.raise_for_status()
            return resp.json()["data"][0]["embedding"]
    except (httpx.HTTPError, KeyError, IndexError, ValueError, TypeError):
        return None


def _cluster_type(cluster: dict) -> str:
    """find_consolidation_clusters joins on equal type — clusters are homogeneous."""
    types = cluster.get("types") or []
    return types[0] if types else "project"


def _cluster_project(cluster: dict) -> str:
    """find_consolidation_clusters (#1187) partitions on (type, project_key) —
    clusters are homogeneous by project too. Falls back to "jarvis" only if
    every member's project is empty (legacy rows predating the project field).
    """
    projects = cluster.get("projects") or []
    return projects[0] if projects and projects[0] else "jarvis"


def build_payload(cluster: dict, plan: dict, source_provenance: str) -> dict:
    """Assemble the jsonb payload stored on every queue entry.

    Downstream consumers (apply RPC, rollback RPC, future review UI) rely
    on field names here — keep them stable.
    """
    members = cluster["members"]
    member_ids = [m["id"] for m in members]
    canonical_tags = canonical_tags_union(members) if plan["decision"] == "MERGE" else []
    return {
        "cluster_id": cluster["cluster_id"],
        "member_ids": sorted(member_ids),
        "member_ids_key": member_ids_key(member_ids),
        "member_names": [m["name"] for m in members],
        "supersede_ids": sorted(plan.get("supersede_ids") or []),
        "canonical_project": _cluster_project(cluster),
        "canonical_type": _cluster_type(cluster),
        "canonical_name": plan.get("canonical_name"),
        "canonical_description": plan.get("canonical_description"),
        "canonical_content": plan.get("canonical_content"),
        "canonical_tags": canonical_tags,
        "source_provenance": source_provenance,
        "haiku_reasoning": plan.get("reasoning") or "",
        "haiku_confidence": plan.get("confidence", 0.0),
        "planned_at": datetime.now(timezone.utc).isoformat(),
    }


def _queue_insert(
    client,
    *,
    decision: str,
    status: str,
    target_id: str | None,
    confidence: float,
    reasoning: str,
    payload: dict,
    classifier_model: str,
    applied_at: str | None = None,
) -> str | None:
    """Insert one row into memory_review_queue. Returns id or None on error.

    Used for status in {pending, auto_applied:KEEP_DISTINCT} — paths that
    don't mutate memories. For auto_applied MERGE/SUPERSEDE the queue insert
    is done inside `apply_consolidation_plan` (transactional with mutations).
    """
    row = {
        "decision": decision,
        "confidence": float(confidence),
        "reasoning": (reasoning or "")[:1000],
        "status": status,
        "consolidation_payload": payload,
        "classifier_model": classifier_model,
    }
    if target_id:
        row["target_id"] = target_id
    if applied_at:
        row["applied_at"] = applied_at
    try:
        resp = client.table("memory_review_queue").insert(row).execute()
        data = resp.data or []
        return data[0]["id"] if data else None
    except Exception as e:  # supabase client raises on HTTP errors
        print(f"! queue insert failed ({decision}, {status}): {e}", file=sys.stderr)
        return None


def _log_event(
    client,
    *,
    canonical_id: str,
    decision: str,
    cluster_id: int,
    superseded_count: int,
    queue_id: str | None,
) -> None:
    """Append an audit trail row to `events`. Best-effort."""
    try:
        client.table("events").insert(
            {
                "event_type": "consolidation_applied",
                "severity": "info",
                "repo": "your-username/your-repo",
                "source": "manual",
                "title": f"Consolidation {decision} applied (cluster {cluster_id})",
                "payload": {
                    "decision": decision,
                    "canonical_id": canonical_id,
                    "cluster_id": cluster_id,
                    "superseded_count": superseded_count,
                    "queue_id": queue_id,
                },
            }
        ).execute()
    except Exception as e:
        print(f"! event log failed: {e}", file=sys.stderr)


def apply_plan(
    client,
    cluster: dict,
    plan: dict,
    *,
    today: str,
    model: str,
    dry_run_embed: bool = False,
) -> dict:
    """Apply one high-confidence plan. Returns result dict for summary output.

    The RPC mutates memories + links AND writes the auto_applied queue row
    in one transaction — if the queue insert fails, the memory mutations
    roll back with it (#224). Embedding backfill runs after; if it fails,
    the canonical is still correct, just not recallable until the next
    lazy-backfill (matches server.py's NULL-embedding behaviour).
    """
    db_decision = DB_DECISION[plan["decision"]]
    source_provenance = f"skill:consolidation:{plan['decision'].lower()}:{today}"
    payload = build_payload(cluster, plan, source_provenance)
    applied_at = datetime.now(timezone.utc).isoformat()

    rpc_plan = {
        "decision": db_decision,
        "canonical_id": plan.get("canonical_id"),
        "supersede_ids": payload["supersede_ids"],
        "canonical_project": payload["canonical_project"],
        "canonical_type": payload["canonical_type"],
        "canonical_name": payload.get("canonical_name"),
        "canonical_description": payload.get("canonical_description"),
        "canonical_content": payload.get("canonical_content"),
        "canonical_tags": payload.get("canonical_tags"),
        "source_provenance": source_provenance,
        "confidence": plan.get("confidence", 0.8),
    }
    queue_meta = {
        "decision": db_decision,
        "status": "auto_applied",
        "confidence": float(plan.get("confidence", 0.8)),
        "reasoning": (plan.get("reasoning") or "")[:1000],
        "classifier_model": model,
        "consolidation_payload": payload,
        "applied_at": applied_at,
        # target_id is set inside the RPC (to the canonical it just computed
        # or picked) — the caller doesn't know v_new_id for MERGE, and it's
        # load-bearing for rollback_consolidation.
    }

    resp = client.rpc(
        "apply_consolidation_plan",
        {"plan": rpc_plan, "queue_meta": queue_meta},
    ).execute()
    rpc_out = resp.data or {}
    canonical_id = rpc_out.get("canonical_id")
    superseded_count = int(rpc_out.get("superseded_count") or 0)
    queue_id = rpc_out.get("queue_id")
    if not canonical_id:
        raise RuntimeError(f"apply_consolidation_plan returned no canonical_id: {rpc_out!r}")
    if not queue_id:
        raise RuntimeError(f"apply_consolidation_plan returned no queue_id: {rpc_out!r}")

    embedded = False
    if plan["decision"] == "MERGE" and not dry_run_embed:
        embed_text = _canonical_embed_text(
            plan.get("canonical_name") or "",
            plan.get("canonical_description") or "",
            payload.get("canonical_tags") or [],
            plan.get("canonical_content") or "",
        )
        vec = embed_document(embed_text)
        if vec is not None:
            try:
                client.table("memories").update(
                    {
                        "embedding": vec,
                        "embedding_model": VOYAGE_MODEL,
                        "embedding_version": "v2",
                    }
                ).eq("id", canonical_id).execute()
                embedded = True
            except Exception as e:
                print(f"! embedding backfill failed for {canonical_id}: {e}", file=sys.stderr)

    _log_event(
        client,
        canonical_id=canonical_id,
        decision=db_decision,
        cluster_id=cluster["cluster_id"],
        superseded_count=superseded_count,
        queue_id=queue_id,
    )

    return {
        "cluster_id": cluster["cluster_id"],
        "decision": db_decision,
        "canonical_id": canonical_id,
        "superseded_count": superseded_count,
        "embedded": embedded,
        "queue_id": queue_id,
        "status": "applied",
    }


def queue_for_review(client, cluster: dict, plan: dict, *, today: str, model: str) -> dict:
    """Route a low-confidence MERGE/SUPERSEDE plan to the review queue.

    Raises RuntimeError if the queue insert returns no id — otherwise the
    cluster would be silently dropped (and re-planned next week, burning
    tokens) while the caller thinks it was queued.
    """
    db_decision = DB_DECISION[plan["decision"]]
    source_provenance = f"skill:consolidation:{plan['decision'].lower()}:{today}"
    payload = build_payload(cluster, plan, source_provenance)
    target_id = plan.get("canonical_id") if plan["decision"] == "SUPERSEDE" else None
    queue_id = _queue_insert(
        client,
        decision=db_decision,
        status="pending",
        target_id=target_id,
        confidence=plan.get("confidence", 0.0),
        reasoning=plan.get("reasoning") or "",
        payload=payload,
        classifier_model=model,
    )
    if not queue_id:
        raise RuntimeError(
            f"Queue insert returned no id for cluster {cluster['cluster_id']} "
            f"({db_decision}) — refusing to report as queued"
        )
    return {
        "cluster_id": cluster["cluster_id"],
        "decision": db_decision,
        "queue_id": queue_id,
        "status": "queued",
    }


def note_keep_distinct(client, cluster: dict, plan: dict, *, today: str, model: str) -> dict:
    """Record KEEP_DISTINCT so the same cluster isn't re-planned next week.

    Raises RuntimeError if the queue insert returns no id — otherwise the
    same cluster will be replanned on the next run (wasting Haiku tokens)
    with no indication the audit row failed.
    """
    source_provenance = f"skill:consolidation:keep_distinct:{today}"
    payload = build_payload(cluster, plan, source_provenance)
    queue_id = _queue_insert(
        client,
        decision="KEEP_DISTINCT",
        status="auto_applied",
        target_id=None,
        confidence=plan.get("confidence", 0.0),
        reasoning=plan.get("reasoning") or "",
        payload=payload,
        classifier_model=model,
        applied_at=datetime.now(timezone.utc).isoformat(),
    )
    if not queue_id:
        raise RuntimeError(
            f"KEEP_DISTINCT queue insert returned no id for cluster "
            f"{cluster['cluster_id']} — refusing to report as noted"
        )
    return {
        "cluster_id": cluster["cluster_id"],
        "decision": "KEEP_DISTINCT",
        "queue_id": queue_id,
        "status": "noted",
    }


def plan_cluster(cluster: dict, *, model: str, timeout: float) -> dict:
    """Call Haiku for one cluster. Returns a plan dict (see _parse_response).

    On any API/parse error returns a KEEP_DISTINCT fallback with confidence=0
    so the caller (and ultimately 5.1b-β --apply) never acts on garbage.
    """
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        return _fallback_keep_distinct("ANTHROPIC_API_KEY missing")

    body = {
        "model": model,
        "max_tokens": MAX_TOKENS,
        "system": SYSTEM_PROMPT,
        "messages": [{"role": "user", "content": _build_user_message(cluster)}],
    }

    try:
        with httpx.Client(timeout=timeout) as http:
            resp = http.post(
                ANTHROPIC_API_URL,
                headers={
                    "x-api-key": api_key,
                    "anthropic-version": ANTHROPIC_VERSION,
                    "content-type": "application/json",
                },
                json=body,
            )
            resp.raise_for_status()
            payload = resp.json()
    except httpx.HTTPError as e:
        return _fallback_keep_distinct(f"http_error: {type(e).__name__}")
    except ValueError:
        return _fallback_keep_distinct("invalid_json_payload")

    blocks = payload.get("content", [])
    text = ""
    for b in blocks:
        if isinstance(b, dict) and b.get("type") == "text":
            text = b.get("text", "")
            break

    member_ids = [m["id"] for m in cluster["members"]]
    parsed = _parse_response(text, member_ids)
    if parsed is None:
        return _fallback_keep_distinct("unparseable_response")
    return parsed


def render_markdown(
    clusters: list[dict],
    plans: list[dict],
    min_size: int,
    threshold: float,
    model: str,
    *,
    apply: bool = False,
    confidence_gate: float = DEFAULT_CONFIDENCE_GATE,
) -> str:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    by_decision: dict[str, int] = defaultdict(int)
    for p in plans:
        by_decision[p["decision"]] += 1

    mode_line = (
        f"_Apply mode (confidence gate {confidence_gate:.2f}). "
        "High-confidence plans auto-applied; rest routed to review queue._"
        if apply
        else "_Dry-run only. No mutations. Rerun with `--apply` to persist high-confidence plans._"
    )

    lines = [
        f"# Memory consolidation plan — {now}",
        "",
        f"- RPC: `find_consolidation_clusters(min_cluster_size={min_size}, sim_threshold={threshold})`",
        f"- Model: `{model}`",
        f"- Clusters planned: **{len(clusters)}**",
        f"- Decisions: MERGE={by_decision.get('MERGE', 0)}, "
        f"SUPERSEDE={by_decision.get('SUPERSEDE', 0)}, "
        f"KEEP_DISTINCT={by_decision.get('KEEP_DISTINCT', 0)}",
        "",
        mode_line,
        "",
    ]

    if not clusters:
        lines.append("_No live clusters above threshold. Nothing to plan._")
        return "\n".join(lines)

    id_to_name = {m["id"]: m["name"] for c in clusters for m in c["members"]}

    for cluster, plan in zip(clusters, plans):
        lines.append(
            f"## Cluster {cluster['cluster_id']} — {plan['decision']} "
            f"(confidence {plan['confidence']:.2f})"
        )
        lines.append("")
        lines.append(f"**Reasoning:** {plan['reasoning'] or '_(empty)_'}")
        lines.append("")
        lines.append(
            f"Members ({cluster['size']}, max_sim {cluster['max_similarity']:.3f}, "
            f"types: {', '.join(cluster['types'])}):"
        )
        for m in cluster["members"]:
            marker = ""
            if plan["decision"] == "SUPERSEDE":
                if m["id"] == plan["canonical_id"]:
                    marker = " **[CANONICAL]**"
                elif m["id"] in plan["supersede_ids"]:
                    marker = " **[SUPERSEDE]**"
            elif plan["decision"] == "MERGE" and m["id"] in plan["supersede_ids"]:
                marker = " **[MERGE→archived]**"
            lines.append(f"- `{m['name']}` ({m['type']}, updated {m['updated_at'][:10]}){marker}")
        lines.append("")

        if plan["decision"] == "MERGE":
            lines.append(f"**Canonical name:** `{plan['canonical_name']}`")
            lines.append("")
            if plan.get("canonical_description"):
                lines.append(f"**Canonical description:** {plan['canonical_description']}")
                lines.append("")
            if plan.get("canonical_content"):
                lines.append("**Canonical content:**")
                lines.append("")
                lines.append("```")
                lines.append(plan["canonical_content"])
                lines.append("```")
                lines.append("")
        elif plan["decision"] == "SUPERSEDE":
            winner_name = id_to_name.get(plan["canonical_id"], plan["canonical_id"])
            lines.append(f"**Keep:** `{winner_name}`")
            lines.append("")
            if plan["supersede_ids"]:
                loser_names = [id_to_name.get(i, i) for i in plan["supersede_ids"]]
                lines.append(f"**Expire:** {', '.join(f'`{n}`' for n in loser_names)}")
                lines.append("")

    lines.append("---")
    lines.append("")
    if apply:
        lines.append("**Next**: see _Apply summary_ below for per-cluster outcomes.")
    else:
        lines.append(
            "**Next**: rerun with `--apply` to persist high-confidence plans "
            "and route the rest to the review queue."
        )
    return "\n".join(lines)


def save_plan_memory(client, plan_md: str, plans: list[dict]) -> None:
    """Upsert as `consolidation_plan_YYYY-MM-DD`, type=project.

    Parallel to `save_report_memory` in consolidation-report.py but with a
    distinct name so the plan and the raw report don't collide.
    """
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    name = f"consolidation_plan_{today}"
    by_decision: dict[str, int] = defaultdict(int)
    for p in plans:
        by_decision[p["decision"]] += 1
    description = (
        f"Consolidation plan {today}: "
        f"{by_decision.get('MERGE', 0)} MERGE, "
        f"{by_decision.get('SUPERSEDE', 0)} SUPERSEDE, "
        f"{by_decision.get('KEEP_DISTINCT', 0)} KEEP_DISTINCT. "
        "Haiku dry-run (Phase 5.1b-α)."
    )
    existing = (
        client.table("memories")
        .select("id")
        .eq("project", "jarvis")
        .eq("name", name)
        .is_("deleted_at", "null")
        .execute()
        .data
    )
    payload = {
        "project": "jarvis",
        "name": name,
        "type": "project",
        "description": description,
        "content": plan_md,
        "tags": ["memory", "consolidation", "phase-5", "haiku-plan"],
        "source_provenance": "skill:consolidation",
    }
    if existing:
        client.table("memories").update(payload).eq("id", existing[0]["id"]).execute()
        print(f"Updated memory `{name}` (id={existing[0]['id']})", file=sys.stderr)
    else:
        client.table("memories").insert(payload).execute()
        print(f"Inserted memory `{name}`", file=sys.stderr)


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument(
        "--min-size",
        type=int,
        default=DEFAULT_MIN_SIZE,
        help=f"Minimum cluster size (default {DEFAULT_MIN_SIZE})",
    )
    p.add_argument(
        "--threshold",
        type=float,
        default=DEFAULT_THRESHOLD,
        help=f"Cosine similarity threshold (default {DEFAULT_THRESHOLD})",
    )
    p.add_argument(
        "--model", default=DEFAULT_MODEL, help=f"Anthropic model id (default {DEFAULT_MODEL})"
    )
    p.add_argument(
        "--timeout",
        type=float,
        default=DEFAULT_TIMEOUT,
        help=f"Per-cluster API timeout seconds (default {DEFAULT_TIMEOUT})",
    )
    p.add_argument("--json", action="store_true", help="Emit JSON instead of markdown")
    p.add_argument(
        "--save-memory",
        action="store_true",
        help="Upsert the plan as a Jarvis memory (`consolidation_plan_YYYY-MM-DD`)",
    )
    p.add_argument(
        "--apply",
        action="store_true",
        help="Persist mutations: high-confidence → apply RPC, "
        "rest → review queue. Default is dry-run.",
    )
    p.add_argument(
        "--confidence-gate",
        type=float,
        default=DEFAULT_CONFIDENCE_GATE,
        help=f"Minimum confidence for auto-apply (default {DEFAULT_CONFIDENCE_GATE})",
    )
    p.add_argument(
        "--limit",
        type=int,
        default=DEFAULT_APPLY_LIMIT,
        help=f"Cap clusters processed per run (default {DEFAULT_APPLY_LIMIT})",
    )
    p.add_argument(
        "--include-seen",
        action="store_true",
        help="Don't skip clusters already in memory_review_queue. "
        "Default: skip (prevents re-spending Haiku tokens).",
    )
    args = p.parse_args()

    sb_url = os.environ.get("SUPABASE_URL")
    sb_key = os.environ.get("SUPABASE_KEY")
    if not sb_url or not sb_key:
        print("SUPABASE_URL / SUPABASE_KEY missing from env", file=sys.stderr)
        return 2
    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("ANTHROPIC_API_KEY missing from env", file=sys.stderr)
        return 2
    if args.apply and not os.environ.get("VOYAGE_API_KEY"):
        print(
            "! VOYAGE_API_KEY missing — MERGE canonicals will be created without embeddings "
            "(recall will miss them until backfilled)",
            file=sys.stderr,
        )

    client = create_client(sb_url, sb_key)

    rpc_rows = fetch_clusters(client, args.min_size, args.threshold)
    ids = sorted({r["memory_id"] for r in rpc_rows})
    details_by_id = fetch_member_details(client, ids)

    clusters = group_clusters(rpc_rows, details_by_id)
    clusters = [c for c in clusters if c["size"] >= args.min_size]

    skipped_seen = 0
    if not args.include_seen and clusters:
        candidate_keys = [member_ids_key([m["id"] for m in c["members"]]) for c in clusters]
        seen_keys = fetch_existing_queue_keys(client, candidate_keys)
        if seen_keys:
            before = len(clusters)
            clusters = [
                c
                for c in clusters
                if member_ids_key([m["id"] for m in c["members"]]) not in seen_keys
            ]
            skipped_seen = before - len(clusters)
            if skipped_seen:
                print(
                    f"Skipped {skipped_seen} cluster(s) already in review queue "
                    f"(use --include-seen to override)",
                    file=sys.stderr,
                )

    if args.limit and len(clusters) > args.limit:
        print(
            f"Capping at --limit {args.limit} (had {len(clusters)} eligible clusters)",
            file=sys.stderr,
        )
        clusters = clusters[: args.limit]

    if not clusters:
        if args.json:
            print(
                json.dumps(
                    {
                        "generated_at": datetime.now(timezone.utc).isoformat(),
                        "params": {"min_size": args.min_size, "threshold": args.threshold},
                        "model": args.model,
                        "clusters": [],
                        "plans": [],
                        "apply": bool(args.apply),
                        "skipped_seen": skipped_seen,
                    },
                    indent=2,
                )
            )
        else:
            print(
                render_markdown(
                    [],
                    [],
                    args.min_size,
                    args.threshold,
                    args.model,
                    apply=bool(args.apply),
                    confidence_gate=args.confidence_gate,
                )
            )
            if skipped_seen:
                print(f"\n_Skipped {skipped_seen} already-seen cluster(s)._")
        return 0

    print(f"Planning {len(clusters)} cluster(s) with {args.model}...", file=sys.stderr)
    plans = []
    for i, cluster in enumerate(clusters, 1):
        print(
            f"  [{i}/{len(clusters)}] cluster {cluster['cluster_id']} "
            f"({cluster['size']} members)...",
            file=sys.stderr,
        )
        plans.append(plan_cluster(cluster, model=args.model, timeout=args.timeout))

    apply_results: list[dict] = []
    if args.apply:
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        print(f"Applying plans (confidence gate {args.confidence_gate:.2f})...", file=sys.stderr)
        for cluster, plan in zip(clusters, plans):
            try:
                if plan["decision"] == "KEEP_DISTINCT":
                    r = note_keep_distinct(client, cluster, plan, today=today, model=args.model)
                elif plan["confidence"] >= args.confidence_gate:
                    r = apply_plan(client, cluster, plan, today=today, model=args.model)
                    print(
                        f"  ✓ cluster {cluster['cluster_id']}: APPLIED {plan['decision']} "
                        f"(canonical={r.get('canonical_id')}, superseded={r.get('superseded_count')})",
                        file=sys.stderr,
                    )
                else:
                    r = queue_for_review(client, cluster, plan, today=today, model=args.model)
                    print(
                        f"  ⋯ cluster {cluster['cluster_id']}: queued {plan['decision']} "
                        f"(confidence {plan['confidence']:.2f} < gate)",
                        file=sys.stderr,
                    )
                apply_results.append(r)
            except Exception as e:
                print(f"  ✗ cluster {cluster['cluster_id']}: FAILED — {e}", file=sys.stderr)
                apply_results.append(
                    {
                        "cluster_id": cluster["cluster_id"],
                        "status": "error",
                        "error": str(e),
                    }
                )

    if args.json:
        out = {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "params": {"min_size": args.min_size, "threshold": args.threshold},
            "model": args.model,
            "clusters": clusters,
            "plans": plans,
            "apply": bool(args.apply),
            "confidence_gate": args.confidence_gate,
            "limit": args.limit,
            "skipped_seen": skipped_seen,
            "apply_results": apply_results,
        }
        print(json.dumps(out, indent=2, default=str))
    else:
        md = render_markdown(
            clusters,
            plans,
            args.min_size,
            args.threshold,
            args.model,
            apply=bool(args.apply),
            confidence_gate=args.confidence_gate,
        )
        print(md)
        if args.apply and apply_results:
            print()
            print("## Apply summary")
            print()
            by_status: dict[str, int] = defaultdict(int)
            for r in apply_results:
                by_status[r["status"]] += 1
            for status in ("applied", "queued", "noted", "error"):
                if by_status.get(status):
                    print(f"- **{status}**: {by_status[status]}")
            if skipped_seen:
                print(f"- **skipped (already in queue)**: {skipped_seen}")
        if args.save_memory:
            save_plan_memory(client, md, plans)

    return 0


if __name__ == "__main__":
    sys.exit(main())
