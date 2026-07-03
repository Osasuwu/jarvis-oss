"""Memory handlers + recall helpers (#360 split).

Hosts every memory_* tool body and its helpers — the recall pipeline
(hybrid + RRF + keyword fallback + temporal scoring), the write
pipeline (auto-link, classifier-decision routing, supersession), the
graph queries, and the known-unknowns gap tracker.

Tests monkeypatch utility names on the `server` module — calls go
through `server.<name>` at runtime to propagate those patches. The
two duplicate definitions of `_cosine_sim` and `_upsert_known_unknown`
that lived in the original server.py (sync versions at L1508/1520
shadowed at runtime by async versions at L2402/2418) are dropped
here — pure dead code per Python module-load semantics. Behavior
preserved.
"""

from __future__ import annotations

import asyncio
import json
import os
from datetime import datetime, timezone

from mcp.types import TextContent

import server  # late-bound — see module docstring
import write_scrubber  # #555: Tier-2 write-path secret-scrubber gate

# Recall pipeline constants and primitive helpers live in mcp-memory/recall.py
# (deep-module split, #496). Aliased back to the legacy private names so that
# server.py's re-export chain and tests that patch `server.<name>` keep working
# unchanged.
from recall import (  # noqa: F401
    EXCLUDE_TAGS_FROM_RECALL,
    RRF_K,
    SIMILARITY_THRESHOLD,
    TEMPORAL_HALF_LIVES,
    DEFAULT_HALF_LIFE,
    ACCESS_BOOST_MAX,
    ACCESS_HALF_LIFE,
    CONFIDENCE_FLOOR,
    PROD_RECALL_CONFIG,
    RecallConfig,
    RecallHit,
    cosine_sim as _cosine_sim,
    filter_excluded_tags as _filter_excluded_tags,
    parse_pgvector as _parse_pgvector,
    recall,
    rrf_merge as _rrf_merge,
    enrich_with_confidence as _enrich_with_confidence,
    apply_temporal_scoring as _apply_temporal_scoring,
)
import dataclasses

# Phase 2b classifier — same conditional-import pattern as server.py.
try:
    from classifier import (  # type: ignore
        classify_write,
        ClassifierDecision,
        CLASSIFIER_MODEL,
    )
except Exception:  # pragma: no cover
    classify_write = None  # type: ignore
    ClassifierDecision = None  # type: ignore
    CLASSIFIER_MODEL = "claude-haiku-4-5"

# Re-export _canonical_embed_text so callers that previously imported
# it from server still work via server's re-export chain.
from embeddings import _canonical_embed_text, _model_slot, _embed_upsert_fields  # noqa: F401

VALID_TYPES = ("user", "project", "decision", "feedback", "reference")


LINK_SIM_THRESHOLD = 0.60
# Phase 2b: classifier replaces the bare similarity gate. We still keep a
# threshold, but it now decides *when to ask the classifier*, not whether to
# fire supersession. The classifier's decision (with confidence) determines
# the actual ADD/UPDATE/DELETE/NOOP outcome.
SUPERSEDE_SIM_THRESHOLD = 0.85  # legacy heuristic — kept for fallback when classifier unavailable
CLASSIFIER_TRIGGER_SIM = (
    0.70  # invoke classifier above this similarity (voyage-3-lite paraphrases sit ~0.73)
)
CLASSIFIER_APPLY_THRESHOLD = 0.70  # auto-apply UPDATE/DELETE above this confidence; else queue
CONSOLIDATION_SIM_THRESHOLD = 0.80
CONSOLIDATION_COUNT = 3
MAX_AUTO_LINKS = 5
MAX_CLASSIFIER_NEIGHBORS = 5


GAP_THRESHOLD = 0.45  # known-unknowns: log gaps when top_similarity < this
GAP_DEDUP_SIM = 0.9


# Strong references to fire-and-forget tasks. CPython holds only a weak ref to
# a bare ``asyncio.create_task`` result, so without an external strong ref the
# task can be GC-collected mid-flight before it completes (same pattern as
# write_scrubber._PENDING_BLOCK_LOGS and decision._PENDING_TASKS). Every
# detached task in this module — recall touch/backfill/recall-event, store-path
# auto-link/known-unknown resolution — is pinned here. Discard via the
# done-callback below.
_PENDING_TASKS: set[asyncio.Task] = set()


def _pin_task(task: asyncio.Task) -> None:
    """Strong-ref *task* until completion so it can't be GC-collected mid-flight."""
    _PENDING_TASKS.add(task)
    task.add_done_callback(_PENDING_TASKS.discard)


async def _upsert_known_unknown(
    client,
    query: str,
    query_embedding: list[float] | None,
    top_similarity: float,
    top_memory_id: str | None,
    context: dict | None = None,
) -> None:
    """Insert or update a known unknown, with semantic dedup.

    Semantic dedup: if an open known_unknown exists with cosine sim > 0.9
    on query_embedding, increment hit_count instead of inserting.
    Best-effort; never raises.
    """
    # Schema declares query_embedding vector(512). If PRIMARY model produces
    # a different dim (e.g. voyage-3 = 1024), store without embedding rather
    # than letting the insert fail and get swallowed by the best-effort catch.
    if query_embedding and len(query_embedding) != 512:
        query_embedding = None

    try:
        if not query_embedding:
            # Fallback: upsert without embedding — select hit_count so the
            # increment reflects the stored value (not the default).
            existing = (
                client.table("known_unknowns")
                .select("id, hit_count")
                .eq("query", query)
                .eq("status", "open")
                .limit(1)
                .execute()
            )
            if existing.data:
                row = existing.data[0]
                client.table("known_unknowns").update(
                    {
                        "hit_count": row.get("hit_count", 1) + 1,
                        "last_seen_at": datetime.now(timezone.utc).isoformat(),
                    }
                ).eq("id", row["id"]).execute()
            else:
                client.table("known_unknowns").insert(
                    {
                        "query": query,
                        "query_embedding": None,
                        "top_similarity": top_similarity,
                        "top_memory_id": top_memory_id,
                        "context": context,
                    }
                ).execute()
            return

        # Semantic dedup: fetch open unknowns and check sim > 0.9.
        # Include hit_count in the select so the increment is correct.
        open_unknowns = (
            client.table("known_unknowns")
            .select("id, query_embedding, hit_count")
            .eq("status", "open")
            .execute()
        )
        for row in open_unknowns.data or []:
            stored_embedding = _parse_pgvector(row.get("query_embedding"))
            if stored_embedding and _cosine_sim(query_embedding, stored_embedding) > 0.9:
                # Semantic match: increment hit_count
                client.table("known_unknowns").update(
                    {
                        "hit_count": row.get("hit_count", 1) + 1,
                        "last_seen_at": datetime.now(timezone.utc).isoformat(),
                    }
                ).eq("id", row["id"]).execute()
                return

        # No match: insert new row
        client.table("known_unknowns").insert(
            {
                "query": query,
                "query_embedding": query_embedding,
                "top_similarity": top_similarity,
                "top_memory_id": top_memory_id,
                "context": context,
            }
        ).execute()
    except Exception:
        pass  # best-effort, never block recall on failure


async def _resolve_known_unknowns(client, memory_embedding: list[float], memory_id: str) -> None:
    """Scan open known_unknowns; mark as resolved if cosine(new_embedding, query_embedding) > 0.7.

    Best-effort; never raises.
    """
    try:
        open_unknowns = (
            client.table("known_unknowns")
            .select("id, query_embedding")
            .eq("status", "open")
            .execute()
        )
        now = datetime.now(timezone.utc).isoformat()
        for row in open_unknowns.data or []:
            stored_embedding = _parse_pgvector(row.get("query_embedding"))
            if stored_embedding and _cosine_sim(memory_embedding, stored_embedding) > 0.7:
                client.table("known_unknowns").update(
                    {
                        "status": "resolved",
                        "resolved_at": now,
                        "resolved_by_memory_id": memory_id,
                    }
                ).eq("id", row["id"]).execute()
    except Exception:
        pass  # best-effort, never block store on failure


async def _handle_recall(args: dict) -> list[TextContent]:
    client = server._get_client()

    query_text = args.get("query", "")
    project = args.get("project")
    if project == "global":
        project = None
    mem_type = args.get("type")
    limit = args.get("limit", 10)

    include_links = args.get("include_links", False)
    show_history = args.get("show_history", False)
    include_unreviewed = args.get("include_unreviewed", False)
    brief = args.get("brief", False)

    # Hybrid search: combine semantic + keyword results via RRF + temporal scoring
    if query_text:
        rows, results = await _hybrid_recall(
            client,
            query_text,
            project,
            mem_type,
            limit,
            include_links,
            show_history,
            brief,
            include_unreviewed=include_unreviewed,
        )
        if rows:
            # Track reads (fire-and-forget)
            ids = [r["id"] for r in rows if r.get("id")]
            if ids:
                # CHANGE #767: Filter out always_load memories to de-bias access-boost.
                # Don't bump last_accessed_at for evergreen rules; recency should not
                # dominate semantic recall via ACT-R temporal scoring.
                ids_to_touch = [
                    rid
                    for rid, row in zip(ids, rows)
                    if "always_load" not in (row.get("tags") or [])
                ]
                if ids_to_touch:
                    _pin_task(asyncio.create_task(_touch_memories(client, ids_to_touch)))
            return results

    # Fallback: keyword-only search (embed failure or empty hybrid result).
    # Pass include_unreviewed through so the always-gate is enforced on the
    # fallback path too — the SQL RPCs do this server-side; this client-side
    # path must mirror them.
    results = await server._keyword_recall(
        client,
        query_text,
        project,
        mem_type,
        limit,
        brief,
        include_unreviewed=include_unreviewed,
    )

    # Lazily backfill embeddings for records missing them (fire-and-forget)
    if os.environ.get("VOYAGE_API_KEY"):
        _pin_task(asyncio.create_task(_backfill_missing_embeddings(client, project)))

    return results


async def _hybrid_recall(
    client,
    query_text: str,
    project,
    mem_type,
    limit: int,
    include_links: bool = False,
    show_history: bool = False,
    brief: bool = False,
    *,
    include_unreviewed: bool = False,
) -> tuple[list[dict], list[TextContent]]:
    """Adapter wrapping the recall() pipeline for the MCP recall tool.

    Pipeline mechanics live in mcp-memory/recall.py (#498). This wrapper owns
    the adapter-level concerns: result formatting, the "Linked memories"
    display section (preserved from the pre-#498 handler — partitioned out
    of the now-unified rank by RecallHit.source), and the recall_event
    metacognition emit (#250). Returns ``([], [])`` on empty result so the
    caller can take the keyword fallback path.
    """
    config = dataclasses.replace(
        PROD_RECALL_CONFIG,
        limit=limit,
        use_links=include_links,
        include_unreviewed=include_unreviewed,
    )
    try:
        hits = await recall(
            client,
            query_text,
            project=project,
            type_filter=mem_type,
            show_history=show_history,
            config=config,
        )
    except asyncio.CancelledError:
        raise
    except Exception:
        return [], []

    if not hits:
        return [], []

    direct_hits = [h for h in hits if h.source != "linked"]
    linked_hits = [h for h in hits if h.source == "linked"]
    direct_rows = [h.memory for h in direct_hits]
    linked_rows = [h.memory for h in linked_hits]

    formatted = _format_memories(direct_rows, brief=brief)
    search_type = "hybrid+temporal"
    mode_tag = ", brief" if brief else ""
    text = f"Found {len(direct_rows)} memories ({search_type} search{mode_tag}):\n\n" + (
        "\n".join(formatted) if brief else "\n---\n".join(formatted)
    )

    if include_links and linked_rows:
        link_formatted = _format_memories(linked_rows, link_info=True, brief=brief)
        text += f"\n\n### Linked memories ({len(linked_rows)}):\n\n" + (
            "\n".join(link_formatted) if brief else "\n---\n".join(link_formatted)
        )

    # Phase 5 metacognition: emit memory_recall event for FOK batch processing (#250).
    # FOK judge keys off the direct retrieval rank, so the payload mirrors what
    # rrf_merge produced — direct hits only, in their post-temporal order.
    returned_ids = [r.get("id") for r in direct_rows if r.get("id")]
    returned_similarities = [
        float(r["similarity"]) if isinstance(r.get("similarity"), (int, float)) else None
        for r in direct_rows
        if r.get("id")
    ]
    top_sim = direct_rows[0].get("similarity", 0.0) if direct_rows else 0.0
    payload = {
        "query": query_text,
        "returned_ids": returned_ids,
        "returned_similarities": returned_similarities,
        "returned_count": len(direct_rows),
        "top_sim": float(top_sim),
        "threshold": SIMILARITY_THRESHOLD,
        "project": project,
        "type_filter": mem_type,
        "show_history": show_history,
    }
    _pin_task(asyncio.create_task(_emit_recall_event(client, payload)))

    # Touch fans out across the whole displayed set (direct + linked) so
    # access-frequency boost matches what the user actually saw.
    all_rows = direct_rows + linked_rows
    return all_rows, [TextContent(type="text", text=text)]


async def _keyword_recall(
    client,
    query_text: str,
    project,
    mem_type,
    limit: int,
    brief: bool = False,
    *,
    include_unreviewed: bool = False,
) -> list[TextContent]:
    """ILIKE keyword search (fallback when semantic unavailable).

    In brief mode we skip the `content` column — it's never rendered and
    would bloat the fallback payload, which is hit precisely when the fast
    path failed and we're already on a slower code path.

    Lifecycle filters mirror the show_history=false branch of
    match_memories / keyword_search_memories: exclude soft-deleted,
    expired, superseded, and past-valid_to rows (#284).

    Always-gate (#552): `requires_review=true` rows and merge-proposal rows
    (`merge_targets` non-empty) are filtered to match the SQL RPCs. Without
    this, a VoyageAI outage that routes traffic here would expose pending
    review candidates to production callers. `include_unreviewed=True`
    relaxes the requires_review gate; merge proposals are filtered
    unconditionally (they are meta-rows, never knowledge).

    valid_to is filtered client-side (not via .or_()) because PostgREST
    accepts only one `or=` parameter per query, and this path already uses
    .or_() for project scoping and for the keyword ILIKE clauses — adding
    a third would silently overwrite one of them. Same pattern as
    scripts/session-context.py _load_recent_recall_results.
    """
    cols = (
        "id, name, type, project, description, tags, updated_at, valid_to"
        if brief
        else "id, name, type, project, description, content, tags, updated_at, valid_to"
    )
    q = (
        client.table("memories")
        .select(cols)
        .is_("deleted_at", "null")
        .is_("expired_at", "null")
        .is_("superseded_by", "null")
        .is_("merge_targets", "null")  # always-gate: merge proposals never surface
    )
    if not include_unreviewed:
        q = q.eq("requires_review", False)

    if project is not None:
        q = q.or_(f"project.eq.{project},project.is.null")
    if mem_type:
        q = q.eq("type", mem_type)

    if query_text:
        terms = query_text.split()
        clauses = ",".join(
            f"name.ilike.%{t}%,description.ilike.%{t}%,content.ilike.%{t}%" for t in terms
        )
        q = q.or_(clauses)

    # Fetch extra rows so the client-side valid_to filter still leaves `limit`
    # live rows in the worst case. 2x is a simple heuristic; tombstoned
    # valid_to rows are rare in practice.
    result = q.limit(limit * 2).order("updated_at", desc=True).execute()

    # #417: drop session-snapshot etc. before valid_to filter so the
    # `live` budget isn't burned on operational artifacts.
    candidate_rows = _filter_excluded_tags(result.data or [])

    now_utc = datetime.now(timezone.utc)
    live: list[dict] = []
    for row in candidate_rows:
        vt = row.get("valid_to")
        if vt is not None:
            try:
                vt_dt = datetime.fromisoformat(vt.replace("Z", "+00:00"))
            except (ValueError, AttributeError):
                vt_dt = None
            if vt_dt is not None and vt_dt <= now_utc:
                continue
        live.append(row)
        if len(live) >= limit:
            break

    if not live:
        return [TextContent(type="text", text="No memories found.")]

    formatted = _format_memories(live, brief=brief)
    mode_tag = ", brief" if brief else ""
    return [
        TextContent(
            type="text",
            text=f"Found {len(live)} memories (keyword search{mode_tag}):\n\n"
            + ("\n".join(formatted) if brief else "\n---\n".join(formatted)),
        )
    ]


async def _touch_memories(client, ids: list[str]) -> None:
    """Fire-and-forget: update last_accessed_at for accessed memories via RPC."""
    try:
        client.rpc("touch_memories", {"memory_ids": ids}).execute()
    except Exception:
        pass


async def _emit_recall_event(client, payload: dict) -> None:
    """Fire-and-forget: emit memory_recall event for FOK batch processing (#250)."""
    try:
        client.table("events").insert(
            {
                "event_type": "memory_recall",
                "severity": "info",
                "repo": "your-username/your-repo",
                "source": "mcp_memory",
                "title": f"Memory recall: {payload.get('query', '')[:60]}",
                "payload": payload,
            }
        ).execute()
    except Exception:
        pass


def _format_memories(
    memories: list[dict], link_info: bool = False, brief: bool = False
) -> list[str]:
    """Format memory rows for display.

    brief=False (default): full markdown block with header + description +
    updated_at + content. Suited to a Jarvis-driven targeted recall where the
    whole memory needs to land in the prompt.

    brief=True: single-line `- name [type/project] (score): description`.
    Suited to bulk/auto injection (UserPromptSubmit hook) where the agent
    should preview what's relevant and pull full content via memory_get on
    hits it actually wants. Content-free, so it can't rot long answers.
    """
    formatted = []
    for mem in memories:
        tags_str = f" [{', '.join(mem.get('tags', []))}]" if mem.get("tags") else ""
        link_str = ""
        if link_info and mem.get("link_type"):
            link_str = f" ← {mem['link_type']}"
            if mem.get("link_strength"):
                link_str += f" ({mem['link_strength']:.2f})"
        proj = mem.get("project") or "global"
        if brief:
            # `_temporal_score` (set by _apply_temporal_scoring) is the actual
            # sort key after rrf × recency × access × entrenchment. Show it
            # first so the displayed value matches the displayed order.
            # Retrieval provenance (rrf/sim/rank) follows as secondary signal
            # — useful for debugging why a row surfaced at all.
            temporal = mem.get("_temporal_score")
            rrf = mem.get("_rrf_score")
            sim = mem.get("similarity")
            rank = mem.get("rank")
            base_parts = []
            if rrf is not None:
                base_parts.append(f"rrf {rrf:.3f}")
            elif isinstance(sim, (int, float)):
                base_parts.append(f"sim {sim:.2f}")
            elif isinstance(rank, (int, float)):
                base_parts.append(f"rank {rank:.2f}")
            if isinstance(temporal, (int, float)):
                lead = f"score {temporal:.3f}"
                score_str = f" ({lead}; {base_parts[0]})" if base_parts else f" ({lead})"
            elif base_parts:
                score_str = f" ({base_parts[0]})"
            else:
                score_str = ""
            desc = (mem.get("description") or "").strip()
            formatted.append(
                f"- {mem['name']} [{mem['type']}/{proj}]{tags_str}{score_str}{link_str}: {desc} — id={mem.get('id', '?')}"
            )
        else:
            formatted.append(
                f"## {mem['name']} ({mem['type']}, {proj}){tags_str}{link_str} — id=`{mem.get('id', '?')}`\n"
                f"*{mem.get('description', '')}*\n"
                f"Updated: {mem.get('updated_at', '?')}\n\n"
                f"{mem['content']}\n"
            )
    return formatted


async def _backfill_missing_embeddings(client, project) -> None:
    """Fire-and-forget: generate embeddings for records saved without one.

    Batches all missing records into a single Voyage AI call.
    """
    try:
        # #242: backfill the column that matches PRIMARY — if we've cut over
        # to v2, the "missing embedding" we care about is embedding_v2.
        primary_col = _model_slot(server.EMBEDDING_MODEL_PRIMARY)["embedding_column"]
        q = client.table("memories").select("id, name, description, tags, content")
        q = q.is_(primary_col, "null").is_("deleted_at", "null")
        if project is not None:
            q = q.or_(f"project.eq.{project},project.is.null")
        rows = q.execute().data
        if not rows:
            return

        # Phase 2a: canonical form (name + tags + description + content)
        texts = [
            _canonical_embed_text(
                r.get("name", ""), r.get("description", ""), r.get("tags") or [], r["content"]
            )
            for r in rows
        ]
        # #242: this path only backfills the column for PRIMARY — the legacy
        # "missing embedding" cleanup. v2 corpus-wide backfill is a separate
        # issue per #242 non-goals.
        embeddings = await server._embed_batch(texts, model=server.EMBEDDING_MODEL_PRIMARY)
        if embeddings is None:
            return

        for mem, embedding in zip(rows, embeddings):
            client.table("memories").update(
                _embed_upsert_fields(embedding, server.EMBEDDING_MODEL_PRIMARY)
            ).eq("id", mem["id"]).execute()
    except Exception:
        pass  # fire-and-forget: silently swallow all errors so caller never fails


async def _create_auto_links(
    client,
    stored_id: str,
    similar_rows: list[dict],
    mem_type: str,
    candidate: dict | None = None,
) -> None:
    """Fire-and-forget: create links + apply Phase 2b classifier decision.

    Pipeline:
      1. Always create `related` links to every neighbor (graph signal).
      2. For neighbors above CLASSIFIER_TRIGGER_SIM, ask the Haiku
         classifier to choose ADD / UPDATE / DELETE / NOOP.
      3. confidence >= CLASSIFIER_APPLY_THRESHOLD → apply the decision
         immediately (UPDATE: target.superseded_by = stored_id;
         DELETE: target.expired_at = now()). Record as auto_applied
         in memory_review_queue for audit.
      4. confidence < threshold → record in queue with status=pending,
         do NOT mutate the target. Owner reviews later.
      5. classifier unavailable (no API key, network fail, no candidate
         metadata) → fall back to the legacy SUPERSEDE_SIM_THRESHOLD
         heuristic so we never regress to "do nothing".
    """
    try:
        # --- (1) base links: everything is `related` until a classifier upgrade ---
        links = []
        for row in similar_rows[:MAX_AUTO_LINKS]:
            links.append(
                {
                    "source_id": stored_id,
                    "target_id": row["id"],
                    "link_type": "related",
                    "strength": round(row.get("similarity", 0), 3),
                }
            )
        if links:
            client.table("memory_links").upsert(
                links, on_conflict="source_id,target_id,link_type"
            ).execute()

        # --- (2) classifier or fallback heuristic ---
        # Pick the high-similarity slice we'd consider for supersession.
        candidates_for_classifier = [
            r
            for r in similar_rows[:MAX_CLASSIFIER_NEIGHBORS]
            if r.get("similarity", 0) >= CLASSIFIER_TRIGGER_SIM
        ]
        if not candidates_for_classifier:
            return  # nothing close enough — pure ADD, no supersession to consider

        decision = None
        if candidate is not None and classify_write is not None:
            # Hydrate neighbors with description/content for richer prompting.
            # find_similar_memories only returns id/name/type/similarity.
            hydrated = await _hydrate_neighbors(client, candidates_for_classifier)
            try:
                decision = await classify_write(candidate, hydrated)
            except Exception:
                decision = None

        if decision is not None:
            await _apply_classifier_decision(client, stored_id, decision, candidates_for_classifier)
        else:
            # Legacy heuristic fallback: same-type + sim >= 0.85 → supersede.
            await _apply_legacy_supersede(client, stored_id, candidates_for_classifier, mem_type)
    except Exception:
        pass


async def _hydrate_neighbors(client, rows: list[dict]) -> list[dict]:
    """Fetch description+content for the neighbor rows so the classifier
    prompt has real context, not just names."""
    ids = [r["id"] for r in rows if r.get("id")]
    if not ids:
        return rows
    try:
        full = (
            client.table("memories")
            .select("id, name, type, description, content, tags")
            .in_("id", ids)
            .execute()
        )
        full_by_id = {row["id"]: row for row in (full.data or [])}
    except Exception:
        return rows

    hydrated = []
    for r in rows:
        extra = full_by_id.get(r.get("id"), {})
        hydrated.append(
            {
                "id": r.get("id"),
                "name": r.get("name") or extra.get("name", ""),
                "type": r.get("type") or extra.get("type", ""),
                "similarity": r.get("similarity", 0),
                "description": extra.get("description", ""),
                "content": extra.get("content", ""),
                "tags": extra.get("tags", []) or [],
            }
        )
    return hydrated


async def _apply_classifier_decision(
    client,
    stored_id: str,
    decision,  # ClassifierDecision
    neighbors: list[dict],
) -> None:
    """Apply the classifier's ADD/UPDATE/DELETE/NOOP decision and record
    it in memory_review_queue (auto_applied if high confidence, pending if
    we want a human in the loop).

    The candidate is *already* persisted by the time we get here — that's
    intentional, we never lose data. UPDATE/DELETE only mutate the target.
    """
    apply_now = decision.confidence >= CLASSIFIER_APPLY_THRESHOLD

    target_id = decision.target_id
    if decision.decision in ("UPDATE", "DELETE") and target_id:
        # Sanity check: target_id must be one of the neighbors we showed it.
        # Otherwise the model hallucinated an id — refuse to mutate.
        valid_ids = {n.get("id") for n in neighbors}
        if target_id not in valid_ids:
            target_id = None
            apply_now = False

    if decision.decision == "ADD":
        # ADD just confirms the upsert we already did. No queue entry needed
        # unless the classifier had low confidence (then we want a record).
        if decision.confidence >= CLASSIFIER_APPLY_THRESHOLD:
            return
        queue_status = "pending"
        applied_at = None
    elif apply_now and target_id and decision.decision == "UPDATE":
        # Try to mutate; only mark auto_applied if the row was actually changed.
        # rowcount==0 happens when the target was already superseded by someone
        # else — a real race we want to flag for review, not silently overwrite.
        mutated = False
        try:
            res = (
                client.table("memories")
                .update({"superseded_by": stored_id})
                .eq("id", target_id)
                .is_("superseded_by", "null")
                .execute()
            )
            mutated = bool(getattr(res, "data", None))
        except Exception:
            mutated = False
        if mutated:
            # Upgrade the auto-created `related` link to `supersedes` so the
            # graph reflects the supersession (matches legacy fallback behavior).
            try:
                client.table("memory_links").upsert(
                    {
                        "source_id": stored_id,
                        "target_id": target_id,
                        "link_type": "supersedes",
                        "strength": 1.0,
                    },
                    on_conflict="source_id,target_id,link_type",
                ).execute()
            except Exception:
                pass  # link upgrade is cosmetic; don't roll back the supersession
            queue_status = "auto_applied"
            applied_at = datetime.now(timezone.utc).isoformat()
        else:
            queue_status = "pending"
            applied_at = None
    elif apply_now and target_id and decision.decision == "DELETE":
        mutated = False
        try:
            res = (
                client.table("memories")
                .update(
                    {
                        "expired_at": datetime.now(timezone.utc).isoformat(),
                    }
                )
                .eq("id", target_id)
                .is_("expired_at", "null")
                .execute()
            )
            mutated = bool(getattr(res, "data", None))
        except Exception:
            mutated = False
        if mutated:
            queue_status = "auto_applied"
            applied_at = datetime.now(timezone.utc).isoformat()
        else:
            queue_status = "pending"
            applied_at = None
    elif apply_now and decision.decision == "NOOP":
        # NOOP: nothing to mutate, but the decision was applied (no-op is the
        # desired state). Record as auto_applied for audit.
        queue_status = "auto_applied"
        applied_at = datetime.now(timezone.utc).isoformat()
    else:
        # Low confidence (or UPDATE/DELETE without a valid target) — queue for review.
        queue_status = "pending"
        applied_at = None

    # Record the decision (always — auditability).
    try:
        client.table("memory_review_queue").insert(
            {
                "candidate_id": stored_id,
                "decision": decision.decision,
                "target_id": target_id,
                "confidence": round(decision.confidence, 3),
                "reasoning": decision.reasoning,
                "classifier_model": CLASSIFIER_MODEL,
                "neighbors_seen": [
                    {
                        "id": n.get("id"),
                        "name": n.get("name"),
                        "similarity": round(n.get("similarity", 0), 3),
                    }
                    for n in neighbors
                ],
                "status": queue_status,
                "applied_at": applied_at,
            }
        ).execute()
    except Exception:
        pass


async def _apply_legacy_supersede(
    client, stored_id: str, similar_rows: list[dict], mem_type: str
) -> None:
    """Fallback used when the classifier is unavailable. Same logic as
    pre-Phase-2b: same-type + similarity >= SUPERSEDE_SIM_THRESHOLD →
    mark target.superseded_by = stored_id."""
    supersede_target_ids = [
        r["id"]
        for r in similar_rows
        if r.get("type") == mem_type
        and r.get("similarity", 0) >= SUPERSEDE_SIM_THRESHOLD
        and r.get("id")
    ]
    if not supersede_target_ids:
        return
    try:
        client.table("memories").update({"superseded_by": stored_id}).in_(
            "id", supersede_target_ids
        ).is_("superseded_by", "null").execute()
        # Also upgrade the link type from `related` to `supersedes`.
        for tid in supersede_target_ids:
            client.table("memory_links").upsert(
                {
                    "source_id": stored_id,
                    "target_id": tid,
                    "link_type": "supersedes",
                    "strength": 1.0,
                },
                on_conflict="source_id,target_id,link_type",
            ).execute()
    except Exception:
        pass


async def _expand_with_links(
    client,
    memory_ids: list[str],
    show_history: bool = False,
    *,
    include_unreviewed: bool = False,
) -> list[dict]:
    """Fetch 1-hop linked memories via graph traversal RPC.

    show_history mirrors the primary recall flag: when true, skip the
    lifecycle filter so history views don't drop linked neighbors.

    include_unreviewed forwards the always-gate opt-in (#552). Currently
    no call site exercises this helper (the active expand_links lives in
    recall.py), but the parameter is plumbed so any future reinstatement
    can't silently drop the flag — matches the SQL RPC contract.
    """
    try:
        result = client.rpc(
            "get_linked_memories",
            {
                "memory_ids": memory_ids,
                "link_types": None,
                "show_history": show_history,
                "include_unreviewed": include_unreviewed,
            },
        ).execute()
        return result.data or []
    except Exception:
        return []


async def _handle_store(args: dict) -> list[TextContent]:
    client = server._get_client()

    mem_type = args["type"]
    mem_name = args["name"]
    content = args["content"]
    description = args.get("description", "")
    project = args.get("project")
    if project == "global":
        project = None  # "global" and null are synonymous — normalize to NULL in DB
    tags = args.get("tags", [])
    source_provenance = args.get("source_provenance")

    if mem_type not in VALID_TYPES:
        return [
            TextContent(type="text", text=f"Invalid type: {mem_type}. Must be one of {VALID_TYPES}")
        ]

    # Phase 2c: provenance required. Reject at the MCP boundary so callers get
    # a readable error instead of a NOT NULL violation from Postgres. Strip
    # whitespace so an accidental " " doesn't pass the guard.
    source_provenance = (source_provenance or "").strip()
    if not source_provenance:
        return [
            TextContent(
                type="text",
                text=(
                    "Error: source_provenance is required (Phase 2c). "
                    "Use a namespaced source like 'session:<id>', 'skill:<name>', "
                    "'hook:<name>', 'user:explicit', or 'episode:<id>'. This is the "
                    "JTMS attribution for this memory — without it, future revisions "
                    "can't be traced."
                ),
            )
        ]

    # Tier-2 write-path secret-scrubber gate; see write_scrubber module
    # docstring. Run AFTER validation but BEFORE any embedding/insert so a
    # blocked write generates no embedding and lands no row. Reject (not
    # silent-scrub) — the write is intent-bearing. Scan every caller-supplied
    # field that persists free text, including `project` (a caller string
    # written to the memories row).
    block = write_scrubber.check_write(
        client,
        {
            "name": mem_name,
            "content": content,
            "description": description,
            "tags": tags,
            "source_provenance": source_provenance,
            "project": project,
        },
        write_path="memory_store",
    )
    if block is not None:
        return [TextContent(type="text", text=block)]

    # Phase 2a: canonical-form embedding — include name + tags + description + content.
    # Name and tags carry high-signal lexical cues that raw content often dilutes
    # (long narrative memories where the key topic is only in the name).
    embed_text = _canonical_embed_text(mem_name, description, tags, content)
    # #242: may populate embedding + embedding_v2 in one shot when SECONDARY set.
    embed_fields = await server._compute_write_embeddings(embed_text)

    data = {
        "type": mem_type,
        "name": mem_name,
        "content": content,
        "description": description,
        "project": project,
        "tags": tags,
        "source_provenance": source_provenance,  # Phase 2c: always present, validated above
        "deleted_at": None,  # clear soft-delete on store/upsert
    }
    data.update(embed_fields)

    # Preserve the old "derive embedding column presence" cue for the user
    # message — we care whether PRIMARY landed.
    embedding = data.get(_model_slot(server.EMBEDDING_MODEL_PRIMARY)["embedding_column"])
    embed_note = " (with embedding)" if embedding is not None else ""

    if project is not None:
        # Atomic upsert via unique constraint on (project, name) — no race condition
        result = client.table("memories").upsert(data, on_conflict="project,name").execute()
        stored_id = result.data[0]["id"] if result.data else None
        action = "saved"
        proj_label = f"project={project}"
    else:
        # Manual upsert for NULL project: PostgreSQL unique constraint doesn't
        # deduplicate NULLs, so we handle this case explicitly.
        q = client.table("memories").select("id").eq("name", mem_name).is_("project", "null")
        existing = q.limit(1).execute()
        if existing.data:
            stored_id = existing.data[0]["id"]
            client.table("memories").update(data).eq("id", stored_id).execute()
            action = "updated"
        else:
            result = client.table("memories").insert(data).execute()
            stored_id = result.data[0]["id"] if result.data else None
            action = "created"
        proj_label = "project=global"

    msg = f"Memory '{mem_name}' {action} ({proj_label}){embed_note}"

    # #658: structured response fields. Advisory only — the store has already
    # landed atomically above; nothing below this point can block or undo it.
    consolidation_names: list[str] = []
    classifier_pending = False

    server._audit_log(
        client, "memory_store", action, mem_name, {"project": project or "global", "type": mem_type}
    )

    # -- Memory 2.0: auto-linking + consolidation hints --
    if embedding is not None and stored_id:
        try:
            similar = client.rpc(
                "find_similar_memories",
                {
                    "query_embedding": embedding,
                    "exclude_id": stored_id,
                    "match_limit": MAX_AUTO_LINKS + 5,
                    "similarity_threshold": LINK_SIM_THRESHOLD,
                    "filter_type": None,
                },
            ).execute()
            similar_rows = similar.data or []
            classifier_pending = bool(similar_rows)

            # Consolidation candidates: 3+ memories above 0.80 similarity.
            # Phrased as `info:` (no warning glyph, no "hint" framing) so the
            # LLM reader doesn't mistake an advisory note for a rejection —
            # see #658 for the iter:13 confabulation that prompted this.
            consolidation_candidates = [
                r for r in similar_rows if r.get("similarity", 0) >= CONSOLIDATION_SIM_THRESHOLD
            ]
            if len(consolidation_candidates) >= CONSOLIDATION_COUNT:
                consolidation_names = [r["name"] for r in consolidation_candidates[:5]]
                msg += (
                    f"\n\ninfo: {len(consolidation_candidates)} similar memories nearby "
                    f"(not blocking): {', '.join(consolidation_names)}"
                )

            # Fire-and-forget: classify (Phase 2b) + create links.
            # We pass the candidate so the classifier has full context;
            # _create_auto_links falls back to the legacy heuristic if the
            # classifier is unavailable.
            if similar_rows:
                candidate_for_classifier = {
                    "name": mem_name,
                    "type": mem_type,
                    "description": description,
                    "content": content,
                    "tags": tags,
                }
                _pin_task(
                    asyncio.create_task(
                        _create_auto_links(
                            client,
                            stored_id,
                            similar_rows,
                            mem_type,
                            candidate=candidate_for_classifier,
                        )
                    )
                )

            # Phase 5: resolve gaps
            try:
                open_gaps = (
                    client.table("known_unknowns")
                    .select("id, query_embedding")
                    .eq("status", "open")
                    .limit(100)
                    .execute()
                )
                for gap in open_gaps.data or []:
                    gap_emb = _parse_pgvector(gap.get("query_embedding"))
                    if gap_emb and embedding and _cosine_sim(embedding, gap_emb) > 0.7:
                        client.table("known_unknowns").update(
                            {
                                "status": "resolved",
                                "resolved_at": datetime.now(timezone.utc).isoformat(),
                                "resolved_by_memory_id": stored_id,
                            }
                        ).eq("id", gap["id"]).execute()
            except Exception:
                pass
        except Exception:
            pass  # auto-linking is best-effort, never blocks store

        # Resolve known unknowns: if stored memory matches any open unknown > 0.7 similarity,
        # mark as resolved (fire-and-forget, best-effort)
        _pin_task(asyncio.create_task(_resolve_known_unknowns(client, embedding, stored_id)))

    # #658: structured envelope. `stored=True` is the unambiguous success
    # signal; callers must not infer success/failure from `message` prose.
    # `(project, name)` is the identity — same-name writes are idempotent
    # by atomic upsert (project-scoped) or explicit SELECT-then-write
    # (global-scoped). No similarity threshold gates this code path.
    response = {
        "stored": True,
        "action": action,
        "memory_id": stored_id,
        "project": project if project is not None else "global",
        "consolidation_candidates": consolidation_names,
        "classifier_pending": classifier_pending,
        "message": msg,
    }
    return [TextContent(type="text", text=json.dumps(response))]


async def _handle_get(args: dict) -> list[TextContent]:
    client = server._get_client()

    mem_name = args["name"]
    project = args.get("project")
    if project == "global":
        project = None

    q = client.table("memories").select("*").eq("name", mem_name).is_("deleted_at", "null")
    if project is not None:
        q = q.eq("project", project)
    else:
        q = q.is_("project", "null")

    result = q.limit(1).execute()

    if not result.data:
        return [
            TextContent(
                type="text", text=f"Memory '{mem_name}' not found (project={project or 'global'})."
            )
        ]

    mem = result.data[0]
    tags_str = f"\nTags: {', '.join(mem.get('tags', []))}" if mem.get("tags") else ""
    return [
        TextContent(
            type="text",
            text=(
                f"## {mem['name']}\n"
                f"Type: {mem['type']} | Project: {mem.get('project') or 'global'}{tags_str}\n"
                f"Created: {mem.get('created_at')} | Updated: {mem.get('updated_at')}\n"
                f"Description: {mem.get('description', '')}\n\n"
                f"{mem['content']}"
            ),
        )
    ]


async def _handle_list(args: dict) -> list[TextContent]:
    client = server._get_client()

    project = args.get("project")
    if project == "global":
        project = None
    mem_type = args.get("type")

    q = (
        client.table("memories")
        .select("name, type, project, description, updated_at")
        .is_("deleted_at", "null")
    )

    if project is not None:
        q = q.or_(f"project.eq.{project},project.is.null")
    if mem_type:
        q = q.eq("type", mem_type)

    result = q.order("type").order("updated_at", desc=True).execute()

    if not result.data:
        return [TextContent(type="text", text="No memories found.")]

    lines = []
    current_type = None
    for mem in result.data:
        if mem["type"] != current_type:
            current_type = mem["type"]
            lines.append(f"\n### {current_type.upper()}")
        proj = mem.get("project") or "global"
        desc = f" — {mem['description']}" if mem.get("description") else ""
        lines.append(f"- **{mem['name']}** ({proj}){desc}")

    return [
        TextContent(
            type="text", text=f"## All Memories ({len(result.data)} total)\n" + "\n".join(lines)
        )
    ]


async def _handle_delete(args: dict) -> list[TextContent]:
    client = server._get_client()

    mem_name = args["name"]
    project = args.get("project")
    if project == "global":
        project = None  # normalize "global" → NULL, same as in _handle_store

    q = (
        client.table("memories")
        .update({"deleted_at": datetime.now(timezone.utc).isoformat()})
        .eq("name", mem_name)
        .is_("deleted_at", "null")
    )
    if project is not None:
        q = q.eq("project", project)
    else:
        q = q.is_("project", "null")

    result = q.execute()

    if result.data:
        server._audit_log(
            client, "memory_delete", "soft_delete", mem_name, {"project": project or "global"}
        )
        return [
            TextContent(
                type="text",
                text=f"Soft-deleted memory '{mem_name}' (project={project or 'global'}). Recoverable for 30 days via memory_restore.",
            )
        ]
    return [TextContent(type="text", text=f"Memory '{mem_name}' not found.")]


async def _handle_restore(args: dict) -> list[TextContent]:
    client = server._get_client()

    mem_name = args["name"]
    project = args.get("project")
    if project == "global":
        project = None

    q = (
        client.table("memories")
        .update({"deleted_at": None})
        .eq("name", mem_name)
        .not_.is_("deleted_at", "null")
    )
    if project is not None:
        q = q.eq("project", project)
    else:
        q = q.is_("project", "null")

    result = q.execute()

    if result.data:
        server._audit_log(
            client, "memory_restore", "restore", mem_name, {"project": project or "global"}
        )
        return [
            TextContent(
                type="text", text=f"Restored memory '{mem_name}' (project={project or 'global'})."
            )
        ]
    return [TextContent(type="text", text=f"No soft-deleted memory '{mem_name}' found.")]


# -- Hygiene handlers (M45 S3 / #768) ---------------------------------------
#
# Lifecycle vs soft-delete: memory_delete sets `deleted_at` (30-day recoverable
# trash); memory_mark_stale sets `expired_at` (hygiene — owner says "this
# belief is wrong/outdated") OR `superseded_by` (hygiene — "this belief has a
# named replacement"). The two namespaces are orthogonal and the recall path
# filters all three independently (`is_(deleted_at,null) & is_(expired_at,
# null) & is_(superseded_by,null)`, see #284).
#
# RLS is enforced at the SUPABASE layer via service-role-vs-anon (#542). The
# `SANDCASTLE_RUN_ID` env-var refusal below is a defense-in-depth Python-side
# gate: sandcastle containers ship anon-only and MUST NOT be able to mark
# arbitrary memories stale (decision d5bfd444 supersedes 719fb533).


def _is_sandcastle_runtime() -> bool:
    """Defense-in-depth host-only gate. Refuses hygiene writes in sandcastle."""
    return bool(os.environ.get("SANDCASTLE_RUN_ID"))


def _normalize_project(project):
    """Match _handle_delete's project='global' → NULL convention."""
    if project == "global":
        return None
    return project


def _apply_name_project_filter(query, name: str, project):
    """Scope to (name, project) — also excludes soft-deleted rows (`deleted_at` null).

    Hygiene operations match `_handle_get` / `_handle_delete`'s default behavior:
    soft-deleted rows are NOT visible to mark_stale / unmark_stale. The recall path
    already filters them out, so editing their lifecycle fields here would be a
    no-op at best and an audit-trail confusion at worst.
    """
    query = query.eq("name", name).is_("deleted_at", "null")
    if project is not None:
        return query.eq("project", project)
    return query.is_("project", "null")


def _record_hygiene_outcome(client, *, action: str, mem_name: str, project, mem_id, reason):
    """Fire-and-forget outcome_record write. Never raises into the caller."""
    try:
        client.table("task_outcomes").insert(
            {
                "task_type": "fix",
                "task_description": f"memory_{action} {mem_name}",
                "outcome_status": "success",
                "outcome_summary": (
                    f"Curated memory '{mem_name}' (project={project or 'global'}, "
                    f"id={mem_id}, action={action}). Reason: {reason or 'unspecified'}."
                ),
                "project": project,
                "memory_id": mem_id,
                "pattern_tags": ["memory-hygiene", "manual-curation"]
                if action == "mark_stale"
                else ["memory-hygiene", "revival"],
            }
        ).execute()
    except Exception:
        pass


async def _handle_memory_mark_stale(args: dict) -> list[TextContent]:
    """Mark a memory stale (hygiene). Owner-invoked, host-only.

    Two modes:
      - successor_uuid given → UPDATE superseded_by = successor_uuid
        (chain walk reaches the replacement on recall)
      - successor_uuid omitted → UPDATE expired_at = now()
        (the belief is wrong/outdated, no replacement)

    Returns a TextContent block carrying action / target_uuid / prior_state.
    """
    if _is_sandcastle_runtime():
        return [
            TextContent(
                type="text",
                text=(
                    "Refused: memory_mark_stale is host-only. "
                    "Sandcastle containers ship anon-only Supabase keys and "
                    "must not edit hygiene lifecycle fields."
                ),
            )
        ]

    client = server._get_client()
    mem_name = args["name"]
    project = _normalize_project(args.get("project"))
    reason = args.get("reason")
    successor_uuid = args.get("successor_uuid")

    # Fetch prior state — we need it for the return payload AND to surface
    # not-found before issuing the UPDATE.
    select_q = _apply_name_project_filter(
        client.table("memories").select("id,name,project,expired_at,superseded_by"),
        mem_name,
        project,
    )
    sel = select_q.execute()
    if not sel.data:
        return [
            TextContent(
                type="text",
                text=f"Memory '{mem_name}' not found (project={project or 'global'}).",
            )
        ]

    target = sel.data[0]
    mem_id = target["id"]
    prior_state = {
        "expired_at": target.get("expired_at"),
        "superseded_by": target.get("superseded_by"),
    }

    if successor_uuid:
        # Supersession is a stronger statement than expiration ("this row has a
        # named replacement, recall should chain-walk there"). If the row was
        # previously marked expired and the owner is now upgrading to a
        # supersede, clear expired_at so the lifecycle state is unambiguous.
        update_payload = {"superseded_by": successor_uuid, "expired_at": None}
        action = "superseded"
    else:
        update_payload = {"expired_at": datetime.now(timezone.utc).isoformat()}
        action = "expired"

    update_q = _apply_name_project_filter(
        client.table("memories").update(update_payload), mem_name, project
    )
    update_q.execute()

    server._audit_log(
        client,
        "memory_mark_stale",
        action,
        mem_name,
        {"project": project or "global", "reason": reason, "successor_uuid": successor_uuid},
    )

    _record_hygiene_outcome(
        client,
        action="mark_stale",
        mem_name=mem_name,
        project=project,
        mem_id=mem_id,
        reason=reason,
    )

    field_changed = "superseded_by set" if successor_uuid else "expired_at set"
    return [
        TextContent(
            type="text",
            text=(
                f"{action.capitalize()} '{mem_name}' ({field_changed}; "
                f"id={mem_id}, project={project or 'global'}). "
                f"Prior state: expired_at={prior_state['expired_at']}, "
                f"superseded_by={prior_state['superseded_by']}. "
                f"Inverse: memory_unmark_stale(project='{project or 'global'}', name='{mem_name}')."
            ),
        )
    ]


async def _handle_memory_unmark_stale(args: dict) -> list[TextContent]:
    """Revive a stale memory by clearing BOTH expired_at and superseded_by.

    Owner-invoked inverse of memory_mark_stale. Host-only.
    """
    if _is_sandcastle_runtime():
        return [
            TextContent(
                type="text",
                text=(
                    "Refused: memory_unmark_stale is host-only. "
                    "Sandcastle containers ship anon-only Supabase keys and "
                    "must not edit hygiene lifecycle fields."
                ),
            )
        ]

    client = server._get_client()
    mem_name = args["name"]
    project = _normalize_project(args.get("project"))

    select_q = _apply_name_project_filter(
        client.table("memories").select("id,name,project,expired_at,superseded_by"),
        mem_name,
        project,
    )
    sel = select_q.execute()
    if not sel.data:
        return [
            TextContent(
                type="text",
                text=f"Memory '{mem_name}' not found (project={project or 'global'}).",
            )
        ]

    target = sel.data[0]
    mem_id = target["id"]
    prior_state = {
        "expired_at": target.get("expired_at"),
        "superseded_by": target.get("superseded_by"),
    }

    update_q = _apply_name_project_filter(
        client.table("memories").update({"expired_at": None, "superseded_by": None}),
        mem_name,
        project,
    )
    update_q.execute()

    server._audit_log(
        client,
        "memory_unmark_stale",
        "revive",
        mem_name,
        {"project": project or "global"},
    )

    _record_hygiene_outcome(
        client,
        action="unmark_stale",
        mem_name=mem_name,
        project=project,
        mem_id=mem_id,
        reason="revival",
    )

    return [
        TextContent(
            type="text",
            text=(
                f"Revived '{mem_name}' (id={mem_id}, project={project or 'global'}). "
                f"Cleared prior state: expired_at={prior_state['expired_at']}, "
                f"superseded_by={prior_state['superseded_by']}."
            ),
        )
    ]


# -- Graph handlers ---------------------------------------------------------


async def _handle_graph(args: dict) -> list[TextContent]:
    mode = args.get("mode", "overview")
    client = server._get_client()

    if mode == "overview":
        return await _graph_overview(client)
    elif mode == "links":
        name = args.get("name")
        if not name:
            return [TextContent(type="text", text="Error: 'name' is required for 'links' mode.")]
        return await _graph_links(client, name)
    elif mode == "clusters":
        return await _graph_clusters(client)
    else:
        return [TextContent(type="text", text=f"Unknown graph mode: {mode}")]


async def _graph_overview(client) -> list[TextContent]:
    """Graph overview: link stats, top connected memories, orphans."""
    lines = ["## Memory Graph Overview\n"]

    # 1. Link stats by type
    all_links = client.table("memory_links").select("link_type, strength").execute()
    link_data = all_links.data or []
    total = len(link_data)

    if total == 0:
        return [
            TextContent(
                type="text", text="No memory links found. Store more memories to build the graph."
            )
        ]

    type_stats: dict[str, list[float]] = {}
    for row in link_data:
        lt = row["link_type"]
        type_stats.setdefault(lt, []).append(row["strength"])

    lines.append(f"### Link Statistics ({total} total)\n")
    lines.append("| Type | Count | Avg Strength | Min | Max |")
    lines.append("|------|-------|-------------|-----|-----|")
    for lt, strengths in sorted(type_stats.items()):
        avg = sum(strengths) / len(strengths)
        lines.append(
            f"| {lt} | {len(strengths)} | {avg:.3f} | {min(strengths):.3f} | {max(strengths):.3f} |"
        )

    # 2. Top connected memories
    links_src = client.table("memory_links").select("source_id").execute()
    links_tgt = client.table("memory_links").select("target_id").execute()
    counts: dict[str, int] = {}
    for row in links_src.data or []:
        mid = row["source_id"]
        counts[mid] = counts.get(mid, 0) + 1
    for row in links_tgt.data or []:
        mid = row["target_id"]
        counts[mid] = counts.get(mid, 0) + 1

    top_ids = sorted(counts.keys(), key=lambda k: counts[k], reverse=True)[:10]
    if top_ids:
        # Fetch names for top IDs
        names_result = (
            client.table("memories")
            .select("id, name, type, project")
            .in_("id", top_ids)
            .is_("deleted_at", "null")
            .execute()
        )
        id_to_mem = {r["id"]: r for r in (names_result.data or [])}

        lines.append(f"\n### Top Connected ({len(top_ids)})\n")
        lines.append("| Memory | Type | Project | Links |")
        lines.append("|--------|------|---------|-------|")
        for mid in top_ids:
            mem = id_to_mem.get(mid, {})
            name = mem.get("name", mid[:8])
            mtype = mem.get("type", "?")
            proj = mem.get("project") or "global"
            lines.append(f"| {name} | {mtype} | {proj} | {counts[mid]} |")

    # 3. Orphans (have embedding, no links)
    total_with_emb = (
        client.table("memories")
        .select("id", count="exact")
        .not_.is_("embedding", "null")
        .is_("deleted_at", "null")
        .execute()
    )
    total_emb_count = total_with_emb.count or 0
    linked_ids = set(counts.keys())
    all_emb = (
        client.table("memories")
        .select("id, name, type, project")
        .not_.is_("embedding", "null")
        .is_("deleted_at", "null")
        .execute()
    )
    orphans = [r for r in (all_emb.data or []) if r["id"] not in linked_ids]

    lines.append(
        f"\n### Orphans ({len(orphans)} of {total_emb_count} embedded memories have no links)\n"
    )
    if orphans:
        for o in orphans[:15]:
            proj = o.get("project") or "global"
            lines.append(f"- **{o['name']}** ({o['type']}, {proj})")
        if len(orphans) > 15:
            lines.append(f"- ... and {len(orphans) - 15} more")

    return [TextContent(type="text", text="\n".join(lines))]


async def _graph_links(client, name: str) -> list[TextContent]:
    """All connections for a specific memory."""
    # Find memory by name
    mem_result = (
        client.table("memories")
        .select("id, name, type, project")
        .eq("name", name)
        .is_("deleted_at", "null")
        .execute()
    )
    if not mem_result.data:
        return [TextContent(type="text", text=f"Memory '{name}' not found.")]

    mem = mem_result.data[0]
    mem_id = mem["id"]
    proj = mem.get("project") or "global"

    lines = [f"## Links for: {name} ({mem['type']}, {proj})\n"]

    # Outgoing links (this memory → others)
    out_result = (
        client.table("memory_links")
        .select("target_id, link_type, strength")
        .eq("source_id", mem_id)
        .order("strength", desc=True)
        .execute()
    )
    out_links = out_result.data or []

    # Incoming links (others → this memory)
    in_result = (
        client.table("memory_links")
        .select("source_id, link_type, strength")
        .eq("target_id", mem_id)
        .order("strength", desc=True)
        .execute()
    )
    in_links = in_result.data or []

    # Resolve target/source names
    all_ids = [r["target_id"] for r in out_links] + [r["source_id"] for r in in_links]
    id_to_name = {}
    if all_ids:
        names = (
            client.table("memories")
            .select("id, name, type, project")
            .in_("id", all_ids)
            .is_("deleted_at", "null")
            .execute()
        )
        id_to_name = {r["id"]: r for r in (names.data or [])}

    # Format outgoing
    lines.append(f"### Outgoing ({len(out_links)})\n")
    if out_links:
        for link in out_links:
            target = id_to_name.get(link["target_id"], {})
            tname = target.get("name", link["target_id"][:8])
            ttype = target.get("type", "?")
            lines.append(f"- → **{tname}** ({ttype}) [{link['link_type']}, {link['strength']:.3f}]")
    else:
        lines.append("- (none)")

    # Format incoming
    lines.append(f"\n### Incoming ({len(in_links)})\n")
    if in_links:
        for link in in_links:
            source = id_to_name.get(link["source_id"], {})
            sname = source.get("name", link["source_id"][:8])
            stype = source.get("type", "?")
            lines.append(f"- ← **{sname}** ({stype}) [{link['link_type']}, {link['strength']:.3f}]")
    else:
        lines.append("- (none)")

    return [TextContent(type="text", text="\n".join(lines))]


async def _graph_clusters(client) -> list[TextContent]:
    """Find clusters of tightly connected memories (mutual links, strength > 0.7)."""
    # Get all strong links
    links_result = (
        client.table("memory_links")
        .select("source_id, target_id, link_type, strength")
        .gte("strength", 0.7)
        .execute()
    )
    links = links_result.data or []

    if not links:
        return [TextContent(type="text", text="No strong links (strength >= 0.7) found.")]

    # Build adjacency: collect neighbors for each memory
    neighbors: dict[str, set[str]] = {}
    link_info: dict[tuple[str, str], dict] = {}
    for link in links:
        s, t = link["source_id"], link["target_id"]
        neighbors.setdefault(s, set()).add(t)
        neighbors.setdefault(t, set()).add(s)
        link_info[(s, t)] = link

    # Simple clustering: connected components via BFS
    visited: set[str] = set()
    clusters: list[set[str]] = []
    for node in neighbors:
        if node in visited:
            continue
        cluster: set[str] = set()
        queue = [node]
        while queue:
            current = queue.pop(0)
            if current in visited:
                continue
            visited.add(current)
            cluster.add(current)
            for neighbor in neighbors.get(current, set()):
                if neighbor not in visited:
                    queue.append(neighbor)
        if len(cluster) >= 2:
            clusters.append(cluster)

    # Sort clusters by size (largest first)
    clusters.sort(key=len, reverse=True)

    # Resolve names
    all_ids = list(set().union(*clusters)) if clusters else []
    id_to_mem = {}
    if all_ids:
        mems = (
            client.table("memories")
            .select("id, name, type, project")
            .in_("id", all_ids)
            .is_("deleted_at", "null")
            .execute()
        )
        id_to_mem = {r["id"]: r for r in (mems.data or [])}

    lines = [f"## Memory Clusters ({len(clusters)} clusters, strength >= 0.7)\n"]

    for i, cluster in enumerate(clusters[:10], 1):
        # Calculate average internal strength
        internal_strengths = []
        for s, t in link_info:
            if s in cluster and t in cluster:
                internal_strengths.append(link_info[(s, t)]["strength"])

        avg_str = sum(internal_strengths) / len(internal_strengths) if internal_strengths else 0

        lines.append(f"### Cluster {i} ({len(cluster)} memories, avg strength: {avg_str:.3f})\n")
        for mid in sorted(cluster, key=lambda m: id_to_mem.get(m, {}).get("name", "")):
            mem = id_to_mem.get(mid, {})
            name = mem.get("name", mid[:8])
            mtype = mem.get("type", "?")
            proj = mem.get("project") or "global"
            lines.append(f"- **{name}** ({mtype}, {proj})")
        lines.append("")

    if len(clusters) > 10:
        lines.append(f"... and {len(clusters) - 10} more clusters")

    return [TextContent(type="text", text="\n".join(lines))]
