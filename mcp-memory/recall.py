"""Recall pipeline: deep module behind every recall call site.

Single source of truth for the constants and primitive helpers that
shape the hybrid-recall pipeline (semantic + keyword fusion + temporal
scoring + link expansion + known-unknown gate). Three adapters live
elsewhere and import from here:

    - mcp-memory/handlers/memory.py  — MCP `recall` tool
    - scripts/memory-recall-hook.py  — UserPromptSubmit hook
    - scripts/eval-recall.py         — eval harness

Slice 1 of 4 (issue #496): constants and primitive filter/math helpers.
Subsequent slices migrate scoring, merge, link-expansion, and finally
the public `RecallConfig` / `RecallHit` / `recall()` orchestrator.
See CONTEXT.md (Recall, RecallConfig, RecallHit) for the deep-module
contract.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Literal


# Reciprocal-Rank-Fusion constant. Higher k flattens rank weighting; k=60
# is the canonical value from the original RRF paper and matches every
# existing call site.
RRF_K = 60

# Minimum cosine similarity for a semantic hit to count. Calibrated
# against voyage-3-lite/512 on the canonical eval set: rows below 0.25 are
# overwhelmingly unrelated, rows above 0.30 are strongly relevant.
# Adapter-level overrides exist (the UserPromptSubmit hook tightens this
# to 0.30 for conversational prompts, which carry more glue tokens) — those
# stay local to the adapter until slice 3 expresses them as RecallConfig
# flags.
SIMILARITY_THRESHOLD = 0.25

# Per-type half-life (days) for the temporal-decay component. Shorter
# half-lives for fast-moving content (project state, references), longer
# for slow-moving content (user profile, behavioral feedback). Memories
# with a type not in this map use DEFAULT_HALF_LIFE at the call site.
TEMPORAL_HALF_LIVES: dict[str, float] = {
    "project": 7,
    "reference": 30,
    "decision": 60,
    "feedback": 90,
    "user": 180,
}

# #417: operational artifacts like session snapshots carry mixed
# transcript content that semantically matches a wide range of queries.
# They're meant to be fetched by name via memory_get during /end recovery,
# never to compete in normal recall. Filtering at the Python layer keeps
# the schema and RPCs untouched while measurably lifting recall@5.
EXCLUDE_TAGS_FROM_RECALL: frozenset[str] = frozenset({"session-snapshot"})


def filter_excluded_tags(rows):
    """Drop rows whose tags overlap EXCLUDE_TAGS_FROM_RECALL. See #417.

    Preserves input ordering. Pass-through for falsy input.
    """
    if not rows or not EXCLUDE_TAGS_FROM_RECALL:
        return rows
    out = []
    for row in rows:
        tags = row.get("tags") or []
        if isinstance(tags, list) and any(t in EXCLUDE_TAGS_FROM_RECALL for t in tags):
            continue
        out.append(row)
    return out


def parse_pgvector(v: list[float] | str | None) -> list[float] | None:
    """Normalize a pgvector value returned by supabase-py.

    PostgREST returns vector columns as JSON-encoded strings
    (e.g. ``"[0.1,0.2,...]"``), not Python lists. Callers that pass the
    raw value into ``cosine_sim`` hit the len-mismatch guard and silently
    score 0. Return a float list, or None if the value is missing /
    unparseable.
    """
    if v is None:
        return None
    if isinstance(v, list):
        return v
    if isinstance(v, str):
        try:
            parsed = json.loads(v)
        except (ValueError, TypeError):
            return None
        return parsed if isinstance(parsed, list) else None
    return None


def cosine_sim(v1: list[float] | None, v2: list[float] | None) -> float:
    """Cosine similarity between two embedding vectors.

    Returns 0.0 if either is None/empty or if lengths differ. The
    length-mismatch guard is load-bearing during embedding-model
    migrations: zip would silently truncate to the shorter vector and
    yield a meaningless score.
    """
    if v1 is None or v2 is None or len(v1) == 0 or len(v2) == 0:
        return 0.0
    if len(v1) != len(v2):
        return 0.0
    dot = sum(a * b for a, b in zip(v1, v2))
    mag1 = math.sqrt(sum(a * a for a in v1))
    mag2 = math.sqrt(sum(b * b for b in v2))
    if mag1 == 0 or mag2 == 0:
        return 0.0
    return dot / (mag1 * mag2)


# ---------------------------------------------------------------------------
# Slice 2 of 4 (issue #497): scoring, merge, and link-expansion functions.
# ---------------------------------------------------------------------------

# Temporal decay constants. DEFAULT_HALF_LIFE applies when a memory type is
# absent from TEMPORAL_HALF_LIVES. ACCESS_* tune the ACT-R access-frequency
# boost; CONFIDENCE_FLOOR ensures a zero-confidence memory still ranks at 50%
# of its temporal score rather than zeroing out.
DEFAULT_HALF_LIFE = 30
ACCESS_BOOST_MAX = 0.3
ACCESS_HALF_LIFE = 14
CONFIDENCE_FLOOR = 0.5

# Link-expansion constants for 1-hop BFS via get_linked_memories.
# LINK_DECAY attenuates a linked row's score relative to its parent's direct
# hit (0.5 → linked row worth half a direct hit at the same rank). LINK_SCORE_FIELD
# marks linked-only rows for display and downstream merge logic.
# Default 1.5 for boost_multiplier in rrf_merge matches TYPE_BOOST_MULTIPLIER in
# the hook adapter — re-tune both together if the calibration changes.
LINK_EXPAND_TOP_K = 5
LINK_DECAY = 0.5
LINK_SCORE_FIELD = "_link_score"


def rrf_merge(
    semantic_rows: list[dict],
    keyword_rows: list[dict],
    limit: int | None = None,
    k: int = RRF_K,
    boost_types: set[str] | None = None,
    boost_multiplier: float = 1.5,
) -> list[dict]:
    """Reciprocal Rank Fusion over two ranked lists.

    Score = sum(1 / (k + rank)) for each list the item appears in. Higher k
    flattens rank weighting; k=60 is the canonical value from the RRF paper.

    Semantics: only rows hit by BOTH signals get ``_rrf_score`` — that is the
    semantic meaning of fusion and is used by downstream display logic to pick
    the right score label. Every row gets ``_final_score`` (the unified sort key
    consumed by ``merge_with_links``). Single-hit rows that receive a type boost
    also get ``_sort_score`` so the displayed score stays in sync with the ranking.

    ``boost_types``: when set, rows whose ``type`` is in the set get their score
    multiplied by ``boost_multiplier`` before the rank sort. The default 1.5
    matches the hook's ``TYPE_BOOST_MULTIPLIER`` — a boosted single-hit (0.025)
    stays below an unboosted dual-hit (0.0333), preserving fusion ordering.

    ``limit``: if provided, the result is sliced to at most this many rows.
    Pass ``None`` (default) to let the caller cap by budget or row count.

    Divergences vs. the three pre-slice-2 copies documented in the slice-2 PR:
    - handler/memory.py always set ``_rrf_score`` on every row; canonical
      matches the hook (dual-hit only) which is semantically correct.
    - handler's ``by_id`` kept the keyword row when a memory appeared in both
      lists; canonical keeps the semantic row (hook behavior) to preserve the
      ``similarity`` field for display fallback.
    """
    scores: dict[str, float] = {}
    hits: dict[str, int] = {}
    by_id: dict[str, dict] = {}
    for rank, row in enumerate(semantic_rows):
        rid = row.get("id") or row.get("name")
        if not rid:
            continue
        scores[rid] = scores.get(rid, 0.0) + 1.0 / (k + rank)
        hits[rid] = hits.get(rid, 0) + 1
        by_id[rid] = row
    for rank, row in enumerate(keyword_rows):
        rid = row.get("id") or row.get("name")
        if not rid:
            continue
        scores[rid] = scores.get(rid, 0.0) + 1.0 / (k + rank)
        hits[rid] = hits.get(rid, 0) + 1
        by_id.setdefault(rid, row)
    if boost_types:
        for rid, row in by_id.items():
            if row.get("type") in boost_types:
                scores[rid] *= boost_multiplier
                if hits[rid] == 1:
                    row["_sort_score"] = scores[rid]
    ranked_ids = sorted(scores.keys(), key=lambda r: scores[r], reverse=True)
    out = []
    for rid in ranked_ids:
        row = by_id[rid]
        if hits[rid] >= 2:
            row["_rrf_score"] = scores[rid]
        row["_final_score"] = scores[rid]
        out.append(row)
    if limit is not None:
        out = out[:limit]
    return out


def score_linked_rows(
    top_rows: list[dict],
    linked_rows: list[dict],
    *,
    top_k: int = LINK_EXPAND_TOP_K,
    decay: float = LINK_DECAY,
    k: int = RRF_K,
) -> list[dict]:
    """Annotate linked_rows with a synthetic RRF-like score derived from
    their parent's rank in top_rows.

    Pure function — takes already-fetched link rows (from
    ``get_linked_memories``, which carries ``linked_from`` pointing at the seed
    id) and returns the subset whose parent is in the top-K seed window,
    deduped against the seeds and against each other.

    Score formula: ``(1 / (k + parent_rank)) * decay * link_strength``.
    Mirrors the RRF math in ``rrf_merge`` so link scores live in the same
    space; ``decay`` attenuates them (0.5 → linked hit worth half a direct hit
    at the same rank). ``link_strength`` (0..1, from DB) scales by edge
    confidence.
    """
    if not top_rows or not linked_rows:
        return []
    seed_rank: dict[str, int] = {}
    for i, row in enumerate(top_rows[:top_k]):
        rid = row.get("id")
        if rid is not None and rid not in seed_rank:
            seed_rank[rid] = i
    if not seed_rank:
        return []
    seen: set[str] = set(seed_rank.keys())
    out: list[dict] = []
    for row in linked_rows:
        rid = row.get("id")
        if not rid or rid in seen:
            continue
        parent = row.get("linked_from")
        if parent not in seed_rank:
            continue
        seen.add(rid)
        strength = row.get("link_strength")
        try:
            strength_f = float(strength) if strength is not None else 1.0
        except (TypeError, ValueError):
            strength_f = 1.0
        parent_rank = seed_rank[parent]
        row[LINK_SCORE_FIELD] = (1.0 / (k + parent_rank)) * decay * strength_f
        out.append(row)
    return out


def expand_links(
    client,
    top_rows: list[dict],
    *,
    link_types: list[str] | None = None,
    top_k: int = LINK_EXPAND_TOP_K,
    decay: float = LINK_DECAY,
    include_unreviewed: bool = False,
) -> list[dict]:
    """Fetch 1-hop linked neighbors of the top-K rows via ``get_linked_memories``.

    Returns annotated linked rows (see ``score_linked_rows``). On RPC failure
    returns []. Fail-soft contract — links are a coverage bonus, not a hard
    requirement.
    """
    seed_ids = [r["id"] for r in top_rows[:top_k] if r.get("id")]
    if not seed_ids:
        return []
    try:
        result = client.rpc(
            "get_linked_memories",
            {
                "memory_ids": seed_ids,
                "link_types": link_types,
                "show_history": False,
                "include_unreviewed": include_unreviewed,
            },
        ).execute()
        linked_rows = result.data or []
    except Exception:
        return []
    return score_linked_rows(top_rows, linked_rows, top_k=top_k, decay=decay)


def merge_with_links(ranked_rows: list[dict], linked_rows: list[dict]) -> list[dict]:
    """Fold linked rows into the already-ranked hybrid result.

    Each ``ranked_rows`` entry carries ``_final_score`` (set by ``rrf_merge``);
    each ``linked_rows`` entry carries ``_link_score`` (set by
    ``score_linked_rows``). Rows that appear in both keep the max. Sort key
    after merge is the resulting score, written back to ``_final_score`` so
    downstream budget trim and display stay consistent.
    """
    if not linked_rows:
        return ranked_rows
    by_id: dict[str, dict] = {}
    scores: dict[str, float] = {}
    for row in ranked_rows:
        rid = row.get("id")
        if not rid:
            continue
        by_id[rid] = row
        scores[rid] = float(row.get("_final_score") or 0.0)
    for row in linked_rows:
        rid = row.get("id")
        if not rid:
            continue
        link_s = float(row.get(LINK_SCORE_FIELD) or 0.0)
        if rid in scores:
            scores[rid] = max(scores[rid], link_s)
        else:
            by_id[rid] = row
            scores[rid] = link_s
    final_ids = sorted(scores.keys(), key=lambda r: scores[r], reverse=True)
    out: list[dict] = []
    for rid in final_ids:
        row = by_id[rid]
        row["_final_score"] = scores[rid]
        out.append(row)
    return out


def enrich_with_confidence(client, rows: list[dict]) -> None:
    """Backfill ``confidence`` on rows that came from match_memories (which
    doesn't project it). Batched SELECT keeps this cheap. Best-effort: on
    error we leave rows untouched and scoring falls back to the NULL→1.0
    branch in ``apply_temporal_scoring``.

    Phase 1 polish (#240).
    """
    ids = [r["id"] for r in rows if r.get("id") and "confidence" not in r]
    if not ids:
        return
    try:
        result = client.table("memories").select("id, confidence").in_("id", ids).execute()
    except Exception:
        return
    conf_map = {r["id"]: r.get("confidence") for r in (result.data or [])}
    for row in rows:
        rid = row.get("id")
        if rid in conf_map and "confidence" not in row:
            row["confidence"] = conf_map[rid]


def apply_temporal_scoring(rows: list[dict]) -> list[dict]:
    """Re-rank rows by combining RRF score with temporal decay and access frequency.

    Reads ``_rrf_score`` (or ``_final_score`` as fallback; 0.01 if absent) as
    the base fusion weight, then multiplies by:
    - recency:       exponential decay since last content update
    - access boost:  ACT-R frequency boost since last access
    - entrenchment:  confidence-derived multiplier (Phase 1 polish #240)

    Writes ``_temporal_score`` and re-sorts rows in-place (descending).
    """
    now = datetime.now(timezone.utc)
    for row in rows:
        rrf = row.get("_rrf_score") or row.get("_final_score") or 0.01
        mem_type = row.get("type", "decision")
        half_life = TEMPORAL_HALF_LIVES.get(mem_type, DEFAULT_HALF_LIFE)

        updated_str = row.get("content_updated_at") or row.get("updated_at") or ""
        try:
            updated = datetime.fromisoformat(updated_str.replace("Z", "+00:00"))
            days_since_update = max(0, (now - updated).total_seconds() / 86400)
        except (ValueError, AttributeError):
            days_since_update = half_life

        accessed_str = row.get("last_accessed_at") or ""
        try:
            accessed = datetime.fromisoformat(accessed_str.replace("Z", "+00:00"))
            days_since_access = max(0, (now - accessed).total_seconds() / 86400)
        except (ValueError, AttributeError):
            days_since_access = days_since_update * 2

        recency = math.exp(-0.693 * days_since_update / half_life)
        access = 1.0 + ACCESS_BOOST_MAX * math.exp(-0.693 * days_since_access / ACCESS_HALF_LIFE)

        # Reviewed memories with no confidence column default to 1.0 (full
        # entrenchment) — pre-PR behavior, calibrated against existing rows.
        # Unreviewed Deriver/Dreamer candidates (requires_review=true) typically
        # have confidence=NULL at write time; treating them as fully entrenched
        # would rank a pending candidate above an established memory in
        # include_unreviewed=true queries. Floor them at CONFIDENCE_FLOOR so
        # entrenchment caps at 0.75 instead of 1.0 (#552 always-gate corollary).
        confidence_raw = row.get("confidence")
        if confidence_raw is None:
            conf = CONFIDENCE_FLOOR if row.get("requires_review") else 1.0
        else:
            try:
                conf = float(confidence_raw)
            except (TypeError, ValueError):
                conf = 1.0
        conf = max(0.0, min(1.0, conf))
        entrenchment = CONFIDENCE_FLOOR + (1.0 - CONFIDENCE_FLOOR) * conf

        row["_temporal_score"] = rrf * recency * access * entrenchment

    rows.sort(key=lambda r: r.get("_temporal_score", 0), reverse=True)
    return rows


# ---------------------------------------------------------------------------
# Slice 3 of 4 (issue #498): public seam — RecallConfig, RecallHit, recall().
# ---------------------------------------------------------------------------
#
# When use_links is True, rrf_merge runs against a wider window so the BFS over
# get_linked_memories has enough seed candidates to find the right parent;
# matches scripts/eval-recall.py:284 (`merge_limit = 25 if with_links else 10`).
LINK_MERGE_FETCH_LIMIT = 25


@dataclass(frozen=True)
class RecallConfig:
    """Toggles + thresholds that shape one recall() invocation.

    All-on defaults are PROD_RECALL_CONFIG. Adapters that want ablations
    (eval, slice-4 hook migration) flip flags via dataclasses.replace, never by
    mutation — frozen=True locks the instance.

    Slice 3 only consumes ``use_links``, ``use_temporal``, ``semantic_threshold``,
    ``rrf_k``, and ``limit``. ``use_rewriter`` and ``use_classifier`` are
    declared here for the slice-4 adapter migration so the public surface is
    stable in advance: the rewriter is still adapter-side (the hook rewrites,
    eval may not), and the classifier drives memory_store side-effects, not
    recall mechanics. ``temporal_half_lives`` and ``excluded_tags`` mirror the
    module constants for documentation; the helpers still read the module-level
    versions in slice 3.
    """

    use_rewriter: bool = True
    use_links: bool = True
    use_classifier: bool = True
    use_temporal: bool = True
    semantic_threshold: float = SIMILARITY_THRESHOLD
    rrf_k: int = RRF_K
    temporal_half_lives: dict[str, float] = field(default_factory=lambda: dict(TEMPORAL_HALF_LIVES))
    excluded_tags: frozenset[str] = EXCLUDE_TAGS_FROM_RECALL
    include_unreviewed: bool = False
    limit: int = 10


PROD_RECALL_CONFIG = RecallConfig()


@dataclass(frozen=True)
class RecallHit:
    """One ranked recall result with full score-stack attribution.

    ``memory`` is the raw row dict (still carries ``_rrf_score`` /
    ``_temporal_score`` / ``similarity`` so existing display helpers keep
    working). ``source`` records which signal contributed the hit at retrieval
    time — semantic-only, keyword-only, or pulled in via 1-hop link expansion.
    ``linked_via`` is the parent memory's UUID for ``source="linked"``, None
    otherwise.
    """

    memory: dict
    semantic_score: float
    keyword_score: float
    rrf_score: float
    temporal_score: float
    final_score: float
    source: Literal["semantic", "keyword", "linked"]
    linked_via: str | None = None


def _row_to_hit(row: dict, semantic_ids: set, keyword_ids: set) -> RecallHit:
    """Build a RecallHit from a pipeline row + retrieval-leg id sets.

    Source attribution: if the row carries ``linked_from`` (set by
    score_linked_rows during 1-hop expansion) AND it didn't come back from
    either the semantic or keyword leg, it's a pure link hit. Rows that
    appeared in either retrieval leg take that leg's label; dual-hits get
    "semantic" since rrf_merge keeps the semantic row to preserve the
    similarity field for display fallback.
    """
    rid = row.get("id")
    linked_from = row.get("linked_from")
    if linked_from and rid not in semantic_ids and rid not in keyword_ids:
        source: Literal["semantic", "keyword", "linked"] = "linked"
        linked_via = linked_from
    elif rid in semantic_ids:
        source = "semantic"
        linked_via = None
    elif rid in keyword_ids:
        source = "keyword"
        linked_via = None
    else:
        # Defensive: a row with no leg attribution shouldn't happen, but
        # keep the pipeline lossy-safe rather than raising.
        source = "semantic"
        linked_via = None

    similarity = row.get("similarity")
    rank = row.get("rank")
    # rrf_score: post-fusion score (RRF + optional link-merge) — every row
    # gets _final_score from rrf_merge; merge_with_links may overwrite it
    # with max(_final_score, _link_score), so a pure-link hit reads its
    # link score here. _rrf_score (dual-hit marker) is intentionally not
    # used: single-hit rows would otherwise read 0.0 and the field would
    # mostly be empty.
    rrf = row.get("_final_score") or row.get(LINK_SCORE_FIELD) or 0.0
    temporal = row.get("_temporal_score") or 0.0
    # final_score follows the production sort key: temporal score when
    # use_temporal is on, otherwise the post-link/post-rrf _final_score.
    final = row.get("_temporal_score")
    if final is None:
        final = row.get("_final_score") or 0.0

    return RecallHit(
        memory=row,
        semantic_score=float(similarity) if isinstance(similarity, (int, float)) else 0.0,
        keyword_score=float(rank) if isinstance(rank, (int, float)) else 0.0,
        rrf_score=float(rrf),
        temporal_score=float(temporal),
        final_score=float(final),
        source=source,
        linked_via=linked_via,
    )


async def recall(
    client,
    query: str,
    *,
    project: str | None = None,
    type_filter: str | None = None,
    show_history: bool = False,
    keyword_query: str | None = None,
    boost_types: set[str] | None = None,
    boost_multiplier: float = 1.5,
    config: RecallConfig = PROD_RECALL_CONFIG,
    query_embedding: list[float] | None = None,
) -> list[RecallHit]:
    """Run the hybrid-recall pipeline and return ranked RecallHits.

    Pipeline (matches scripts/eval-recall.py:run_query for behavior parity —
    the eval baseline.json is generated with the same composition):

        embed → semantic + keyword RPCs → rrf_merge
              → (use_links) expand_links + merge_with_links + trim
              → enrich_with_confidence
              → (use_temporal) apply_temporal_scoring

    Returns ``[]`` on embed failure or when both legs return no rows; the
    caller (handler / hook) decides whether to fall back to a keyword-only
    path. Embedding model + RPC name are read from the ``server`` module via
    late-import so test monkeypatches of ``server.EMBEDDING_MODEL_PRIMARY``
    and ``server._embed_query`` apply at call time.

    ``show_history`` is plumbed through to both RPCs but is not a
    RecallConfig flag — it's a query-time audit/debug knob, not a pipeline
    toggle. Same shape as the pre-#498 ``_hybrid_recall`` signature.

    ``keyword_query`` (#499 fix-forward): when provided, used as the
    ``search_query`` for the FTS keyword leg instead of the raw ``query``.
    Adapter-side rewriters (the hook's Haiku prompt-to-entities rewriter)
    use this to denoise the keyword leg with literal-content tokens
    (proper nouns, paths, identifiers) — the kind that match memories by
    keyword but not semantically. Falls back to ``query`` when None,
    preserving server-side recall() behavior unchanged.

    ``boost_types`` + ``boost_multiplier`` (#499 fix-forward): threaded into
    rrf_merge to apply a soft rank boost on rows whose type matches. The
    hook uses this to lift rewriter-suggested types without a hard filter
    (a misclassified-but-relevant memory still survives single-signal). When
    None or empty, no boost is applied. Default multiplier 1.5 matches the
    pre-#499 hook calibration.

    ``query_embedding`` (#508): pre-computed embedding for the query. When
    provided, ``recall()`` skips the internal ``_embed_query()`` call and
    uses this directly. This eliminates the double-embed overhead when
    the caller (notably the UserPromptSubmit hook) already computed an
    embedding for its own gate check. Falls back to embedding internally
    when None (default), preserving the existing behavior for all current
    callers.
    """
    # Late-bind `server` so test patches of the embedding model + embed
    # function still apply. Same pattern as handlers/memory.py.
    import server  # noqa: PLC0415

    if query_embedding is None:
        query_embedding = await server._embed_query(query)
    if query_embedding is None:
        return []

    # Wider window when links will be merged — the BFS over get_linked_memories
    # needs enough seed candidates to reach parents that edge toward the
    # expected memory.
    fetch_limit = LINK_MERGE_FETCH_LIMIT if config.use_links else config.limit
    rpc_fetch_limit = fetch_limit * 2

    from embeddings import _model_slot  # noqa: PLC0415

    try:
        sem_rpc = _model_slot(server.EMBEDDING_MODEL_PRIMARY)["rpc"]
        sem_result = client.rpc(
            sem_rpc,
            {
                "query_embedding": query_embedding,
                "match_limit": rpc_fetch_limit,
                "similarity_threshold": config.semantic_threshold,
                "filter_project": project,
                "filter_type": type_filter,
                "show_history": show_history,
                "include_unreviewed": config.include_unreviewed,
            },
        ).execute()
        semantic_rows = filter_excluded_tags(sem_result.data or [])

        kw_result = client.rpc(
            "keyword_search_memories",
            {
                "search_query": keyword_query or query,
                "match_limit": rpc_fetch_limit,
                "filter_project": project,
                "filter_type": type_filter,
                "show_history": show_history,
                "include_unreviewed": config.include_unreviewed,
            },
        ).execute()
        keyword_rows = filter_excluded_tags(kw_result.data or [])
    except Exception:
        return []

    # Capture leg membership BEFORE rrf_merge mutates row dicts — needed for
    # RecallHit.source attribution at the end.
    semantic_ids = {r.get("id") for r in semantic_rows if r.get("id")}
    keyword_ids = {r.get("id") for r in keyword_rows if r.get("id")}

    merged = rrf_merge(
        semantic_rows,
        keyword_rows,
        limit=fetch_limit,
        k=config.rrf_k,
        boost_types=boost_types,
        boost_multiplier=boost_multiplier,
    )
    if not merged:
        return []

    if config.use_links:
        linked = expand_links(client, merged, include_unreviewed=config.include_unreviewed)
        if linked:
            # Linked rows are tag-filtered to match semantic/keyword legs —
            # otherwise session-snapshot etc. can ride in as 1-hop neighbors of
            # a seed row and bypass the same filter applied above on lines 608/621.
            linked = filter_excluded_tags(linked)
        if linked:
            merged = merge_with_links(merged, linked)
        merged = merged[: config.limit]

    enrich_with_confidence(client, merged)

    if config.use_temporal:
        apply_temporal_scoring(merged)

    return [_row_to_hit(row, semantic_ids, keyword_ids) for row in merged[: config.limit]]
