"""Memory recall evaluation harness — thin adapter over recall() (#499).

Runs the query set in tests/memory-eval/queries.yaml against the live Supabase
corpus and reports:

    - recall@3, recall@5, recall@10:  fraction of queries where >=1 expected name is in top-k
    - MRR:                            mean reciprocal rank of the first expected hit
    - mean_rank:                      average position of first expected hit (lower better)
    - must_not violations:            queries where a "should not surface" memory landed
                                      in top 5 (tracked separately — lifecycle signal)

Usage:
    python scripts/eval-recall.py                                # run, pretty-print, don't save
    python scripts/eval-recall.py --save-baseline                # run + overwrite baseline.json
    python scripts/eval-recall.py --diff baseline                # run + print delta vs baseline.json
    python scripts/eval-recall.py --quiet                        # only print aggregates
    python scripts/eval-recall.py --record <path>                # capture RPC snapshot for offline replay
    python scripts/eval-recall.py --replay <path> --diff baseline # replay cached data (no secrets)
    python scripts/eval-recall.py --ci <path> --diff baseline     # CI: replay + fail on regressions

Pipeline (embed → semantic+keyword RPCs → RRF → links → temporal) runs inside
recall() from mcp-memory/recall.py — one implementation, three adapters. Ablation
modes (--with-links, --with-rewriter) are expressed as RecallConfig flag flips via
dataclasses.replace(PROD_RECALL_CONFIG, ...) — no inline branching.

Known divergences vs pre-#499 behavior:
  • --with-rewriter sets use_rewriter=True in RecallConfig as a mode label; the
    actual rewriter call is adapter-side and not yet implemented inside recall().
    _load_hook_module() shim removed (#499 AC). rewriter_fired_count = 0 always.
  • Context-rot path (--with-session-context): uses direct RPCs so keyword_query
    can be injected with the session blob. recall() doesn't yet support keyword
    injection; both legs (plain and with-context) use the same direct path via
    _run_query_direct() to keep the delta signal clean.
"""

from __future__ import annotations

import argparse
import asyncio
import dataclasses
import importlib.util
import json
import math
import os
import sys
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path

# ---------------------------------------------------------------------------
# Pipeline constants & public seam — single source of truth lives in
# mcp-memory/recall.py (#496-#499). Add mcp-memory/ to sys.path so the
# import resolves regardless of cwd.
# ---------------------------------------------------------------------------
_eval_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_eval_root / "mcp-memory"))
from recall import (  # noqa: E402
    SIMILARITY_THRESHOLD,
    PROD_RECALL_CONFIG,
    RecallConfig,
    filter_excluded_tags as _filter_excluded_tags,
    recall,
    rrf_merge as _rrf_merge,
    apply_temporal_scoring as _apply_temporal_scoring,
    enrich_with_confidence as _enrich_with_confidence,
)

VOYAGE_URL = "https://api.voyageai.com/v1/embeddings"
VOYAGE_MODEL = "voyage-3-lite"
EMBED_TIMEOUT = 10.0


def _load_hook_module():
    """Load scripts/memory-recall-hook.py to import its rewriter (#499 fix-forward).

    The rewriter is caller-policy code (LLM call, prompt template) and lives
    in the hook adapter, not in recall.py. The eval needs it for ablation
    parity with the hook in --with-rewriter mode. Hyphen in the hook filename
    blocks normal imports — load via importlib.
    """
    here = Path(__file__).resolve().parent
    hook_path = here / "memory-recall-hook.py"
    if not hook_path.exists():
        return None
    try:
        spec = importlib.util.spec_from_file_location("memory_recall_hook", hook_path)
        if spec is None or spec.loader is None:
            return None
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod
    except Exception as e:
        print(f"WARN: failed to load hook module: {e}", file=sys.stderr)
        return None


def _load_env() -> None:
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    here = Path(__file__).resolve().parent
    for candidate in (here.parent / ".env", here.parent.parent / ".env"):
        if candidate.exists():
            load_dotenv(candidate)
            return


async def _embed_query(text: str) -> list[float]:
    """Sync-in-async Voyage embed. Raises on error — eval should fail loud, not open."""
    import httpx

    api_key = os.environ["VOYAGE_API_KEY"]
    async with httpx.AsyncClient(timeout=EMBED_TIMEOUT) as client:
        resp = await client.post(
            VOYAGE_URL,
            headers={"Authorization": f"Bearer {api_key}"},
            json={
                "model": VOYAGE_MODEL,
                "input": [text],
                "input_type": "query",
            },
        )
        resp.raise_for_status()
        return resp.json()["data"][0]["embedding"]


# ---------------------------------------------------------------------------
# Eval core
# ---------------------------------------------------------------------------


@dataclass
class QueryResult:
    id: str
    query: str
    kind: str
    expected: list[str]
    must_not: list[str]
    top_names: list[str]  # top-10 names in order
    first_hit_rank: int | None  # 1-indexed, None if no expected hit in top-10
    hits_at_3: list[str]  # expected names that appeared in top-3
    hits_at_5: list[str]  # expected names that appeared in top-5
    hits_at_10: list[str]
    must_not_violations_at_5: list[str]  # must_not names that appeared in top-5
    must_not_violations_at_10: list[str]  # must_not names that appeared in top-10 (S0 expansion)
    passed: bool  # recall@5 >= 1 AND no must_not violations
    rewriter_entities: list[str] = field(default_factory=list)
    rewriter_types: list[str] = field(default_factory=list)
    rewriter_fired: bool = False  # True when rewriter returned a non-null result
    links_added: int = 0  # new rows added via 1-hop BFS


@dataclass
class EvalReport:
    timestamp: str
    mode: str  # e.g. "server_only", "with_rewriter+links"
    corpus_size: int
    total_queries: int
    recall_at_3: float
    recall_at_5: float
    recall_at_10: float
    mrr: float
    mean_rank: float  # average first_hit_rank across all queries (NaN if none in top-10)
    must_not_violations: int  # must_not violations at @5 (primary signal)
    must_not_violations_at_10: int  # must_not violations at @10 (S0 expansion)
    passed: int
    failed: int
    rewriter_fired_count: int = 0  # queries where rewriter produced output
    links_added_total: int = 0  # total new rows pulled in via link expansion
    results: list[dict] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Offline replay snapshot — record RPC results to disk, replay without
# Supabase/Voyage keys. ln-635: enables CI eval without secrets.
# ---------------------------------------------------------------------------


@dataclass
class QuerySnapshot:
    """Cached RPC data for one query — enough to re-run the post-RPC pipeline."""

    query: str
    embedding: list[float]
    semantic_rows: list[dict]
    keyword_rows: list[dict]
    confidence_rows: list[dict]  # id + confidence pairs for enrich_with_confidence


@dataclass
class EvalSnapshot:
    """Full offline snapshot — corpus metadata + per-query cached RPC data."""

    timestamp: str
    corpus_size: int
    queries: dict[str, QuerySnapshot]  # qid → cached data


async def _record_snapshot(
    queries: list[dict],
    client,
    config: RecallConfig = PROD_RECALL_CONFIG,
) -> EvalSnapshot:
    """Run the full pipeline against live Supabase, capture raw RPC data for offline replay.

    Uses _run_query_direct() for a clean record/replay pipeline match. Runs
    embed + RPC calls live but captures the raw rows so _run_query_replay()
    can reproduce identical QueryResults without network access.
    """
    snapshots: dict[str, QuerySnapshot] = {}

    for q in queries:
        embedding = await _embed_query(q["query"])

        sem = client.rpc(
            "match_memories",
            {
                "query_embedding": embedding,
                "match_limit": 20,
                "similarity_threshold": SIMILARITY_THRESHOLD,
                "filter_project": None,
                "filter_type": None,
                "show_history": False,
            },
        ).execute()
        semantic_rows = sem.data or []

        kw = client.rpc(
            "keyword_search_memories",
            {
                "search_query": q["query"],
                "match_limit": 20,
                "filter_project": None,
                "filter_type": None,
                "show_history": False,
            },
        ).execute()
        keyword_rows = kw.data or []

        # Capture confidence for all returned ids
        all_ids = list(
            {r["id"] for r in semantic_rows if r.get("id")}
            | {r["id"] for r in keyword_rows if r.get("id")}
        )
        confidence_rows: list[dict] = []
        if all_ids:
            try:
                cr = client.table("memories").select("id, confidence").in_("id", all_ids).execute()
                confidence_rows = cr.data or []
            except Exception:
                confidence_rows = []

        snapshots[q["id"]] = QuerySnapshot(
            query=q["query"],
            embedding=embedding,
            semantic_rows=semantic_rows,
            keyword_rows=keyword_rows,
            confidence_rows=confidence_rows,
        )

    cs = (
        client.table("memories")
        .select("id", count="exact")
        .is_("deleted_at", "null")
        .limit(1)
        .execute()
    )

    return EvalSnapshot(
        timestamp=datetime.now(timezone.utc).isoformat(),
        corpus_size=cs.count or 0,
        queries=snapshots,
    )


async def _run_query_replay(
    q: dict,
    snapshot_entry: QuerySnapshot,
) -> QueryResult:
    """Replay path: use cached RPC data instead of calling Supabase/Voyage.

    Runs the same post-RPC pipeline (RRF merge → confidence → temporal →
    scoring) as _run_query_direct() on pre-recorded rows. The rewriter and
    link expansion are NOT available in replay mode.
    """
    sem_rows = _filter_excluded_tags(snapshot_entry.semantic_rows)
    kw_rows = _filter_excluded_tags(snapshot_entry.keyword_rows)

    merged = _rrf_merge(sem_rows, kw_rows, limit=10)

    # Backfill confidence from cached data
    conf_map = {r["id"]: r.get("confidence") for r in snapshot_entry.confidence_rows if r.get("id")}
    for row in merged:
        rid = row.get("id")
        if rid and rid in conf_map and "confidence" not in row:
            row["confidence"] = conf_map[rid]

    _apply_temporal_scoring(merged)

    top_names = [r.get("name", "?") for r in merged]
    expected = set(q.get("expected") or [])
    must_not = set(q.get("must_not") or [])

    top3 = top_names[:3]
    top5 = top_names[:5]
    top10 = top_names[:10]
    hits_3 = [n for n in top3 if n in expected]
    hits_5 = [n for n in top5 if n in expected]
    hits_10 = [n for n in top10 if n in expected]
    mn_viol_5 = [n for n in top5 if n in must_not]
    mn_viol_10 = [n for n in top10 if n in must_not]

    first_hit_rank = None
    for i, name in enumerate(top10, start=1):
        if name in expected:
            first_hit_rank = i
            break

    passed = bool(hits_5) and not mn_viol_5

    return QueryResult(
        id=q["id"],
        query=q["query"],
        kind=q.get("kind", ""),
        expected=sorted(expected),
        must_not=sorted(must_not),
        top_names=top_names,
        first_hit_rank=first_hit_rank,
        hits_at_3=hits_3,
        hits_at_5=hits_5,
        hits_at_10=hits_10,
        must_not_violations_at_5=mn_viol_5,
        must_not_violations_at_10=mn_viol_10,
        passed=passed,
        rewriter_entities=[],
        rewriter_types=[],
        rewriter_fired=False,
        links_added=0,
    )


async def run_all_replay(
    queries: list[dict],
    snapshot: EvalSnapshot,
) -> EvalReport:
    """Run full eval from a cached snapshot — no network access needed."""
    results = []
    for q in queries:
        entry = snapshot.queries.get(q["id"])
        if entry is None:
            # Query not in snapshot — skip
            continue
        r = await _run_query_replay(q, entry)
        results.append(r)

    total = len(results)
    r3 = sum(1 for r in results if r.hits_at_3) / total if total else 0.0
    r5 = sum(1 for r in results if r.hits_at_5) / total if total else 0.0
    r10 = sum(1 for r in results if r.hits_at_10) / total if total else 0.0
    mrr = sum(1.0 / r.first_hit_rank for r in results if r.first_hit_rank) / total if total else 0.0
    ranks_with_hit = [r.first_hit_rank for r in results if r.first_hit_rank]
    mean_rank = sum(ranks_with_hit) / len(ranks_with_hit) if ranks_with_hit else float("nan")
    mn_viol_5 = sum(1 for r in results if r.must_not_violations_at_5)
    mn_viol_10 = sum(1 for r in results if r.must_not_violations_at_10)
    passed = sum(1 for r in results if r.passed)

    return EvalReport(
        timestamp=datetime.now(timezone.utc).isoformat(),
        mode="replay",
        corpus_size=snapshot.corpus_size,
        total_queries=total,
        recall_at_3=r3,
        recall_at_5=r5,
        recall_at_10=r10,
        mrr=mrr,
        mean_rank=mean_rank,
        must_not_violations=mn_viol_5,
        must_not_violations_at_10=mn_viol_10,
        passed=passed,
        failed=total - passed,
        results=[asdict(r) for r in results],
    )


# -- #241: context-rot measurement -----------------------------------------


@dataclass
class ContextRotResult:
    """Per-query comparison of plain vs. context-injected recall."""

    id: str
    query: str
    plain_top_names: list[str]
    context_top_names: list[str]
    plain_hits_at_5: list[str]
    context_hits_at_5: list[str]
    plain_first_hit_rank: int | None
    context_first_hit_rank: int | None
    plain_must_not_viol: list[str]
    context_must_not_viol: list[str]
    plain_must_not_viol_at_10: list[str]
    context_must_not_viol_at_10: list[str]
    plain_passed: bool
    context_passed: bool


@dataclass
class ContextRotReport:
    """Aggregate context-rot metrics.

    Sign convention (spec #241): `delta_* = with_context − plain`.
    Positive → context HELPS recall. Negative → retrieval-induced
    forgetting ("context rot"), as in LongMemEval.
    """

    timestamp: str
    corpus_size: int
    total_queries: int
    plain_recall_at_3: float
    context_recall_at_3: float
    delta_recall_at_3_pp: float
    plain_recall_at_5: float
    context_recall_at_5: float
    delta_recall_at_5_pp: float  # (context - plain) * 100
    plain_recall_at_10: float
    context_recall_at_10: float
    delta_recall_at_10_pp: float
    plain_mrr: float
    context_mrr: float
    delta_mrr: float
    plain_mean_rank: float
    context_mean_rank: float
    delta_mean_rank: float
    plain_must_not_violations: int
    context_must_not_violations: int
    plain_must_not_violations_at_10: int
    context_must_not_violations_at_10: int
    only_plain_passed: list[str]  # query ids where plain passed but context didn't (= rot)
    only_context_passed: list[str]  # query ids where context passed but plain didn't (= helps)
    context_budget: dict  # chars, tokens, item counts
    per_query: list[dict] = field(default_factory=list)


def _build_mode_string(with_rewriter: bool, with_links: bool) -> str:
    parts: list[str] = []
    if with_rewriter:
        parts.append("with_rewriter")
    if with_links:
        parts.append("links")
    return "+".join(parts) if parts else "server_only"


async def _run_query_direct(
    client,
    q: dict,
    context_blob: str | None = None,
) -> QueryResult:
    """Direct-RPC query path used by the context-rot eval (#241).

    recall() doesn't yet support keyword_query injection, which is the
    mechanism context-rot uses to simulate session-start pollution. Both
    legs of the context-rot comparison (plain and with-context) call this
    function so the delta stays clean: plain passes context_blob="" (no
    appending), with-context passes the real blob.

    This path stays direct until recall() adds a keyword_prefix parameter.
    """
    embedding = await _embed_query(q["query"])
    keyword_query = q["query"]
    if context_blob:
        keyword_query = f"{keyword_query} {context_blob}"

    sem = client.rpc(
        "match_memories",
        {
            "query_embedding": embedding,
            "match_limit": 20,
            "similarity_threshold": SIMILARITY_THRESHOLD,
            "filter_project": None,
            "filter_type": None,
            "show_history": False,
        },
    ).execute()
    sem_rows = _filter_excluded_tags(sem.data or [])

    kw = client.rpc(
        "keyword_search_memories",
        {
            "search_query": keyword_query,
            "match_limit": 20,
            "filter_project": None,
            "filter_type": None,
            "show_history": False,
        },
    ).execute()
    kw_rows = _filter_excluded_tags(kw.data or [])

    merged = _rrf_merge(sem_rows, kw_rows, limit=10)
    _enrich_with_confidence(client, merged)
    _apply_temporal_scoring(merged)

    top_names = [r.get("name", "?") for r in merged]
    expected = set(q.get("expected") or [])
    must_not = set(q.get("must_not") or [])

    top3 = top_names[:3]
    top5 = top_names[:5]
    top10 = top_names[:10]
    hits_3 = [n for n in top3 if n in expected]
    hits_5 = [n for n in top5 if n in expected]
    hits_10 = [n for n in top10 if n in expected]
    mn_viol_5 = [n for n in top5 if n in must_not]
    mn_viol_10 = [n for n in top10 if n in must_not]

    first_hit_rank = None
    for i, name in enumerate(top10, start=1):
        if name in expected:
            first_hit_rank = i
            break

    passed = bool(hits_5) and not mn_viol_5

    return QueryResult(
        id=q["id"],
        query=q["query"],
        kind=q.get("kind", ""),
        expected=sorted(expected),
        must_not=sorted(must_not),
        top_names=top_names,
        first_hit_rank=first_hit_rank,
        hits_at_3=hits_3,
        hits_at_5=hits_5,
        hits_at_10=hits_10,
        must_not_violations_at_5=mn_viol_5,
        must_not_violations_at_10=mn_viol_10,
        passed=passed,
        rewriter_entities=[],
        rewriter_types=[],
        rewriter_fired=False,
        links_added=0,
    )


async def run_query(
    client,
    q: dict,
    config: RecallConfig = PROD_RECALL_CONFIG,
    context_blob: str | None = None,
) -> QueryResult:
    """Run one eval query and return a QueryResult.

    Standard path (context_blob is None): delegates to recall() from
    mcp-memory/recall.py — one pipeline, three adapters. Ablation modes
    expressed as RecallConfig flag flips via dataclasses.replace().

    Context-rot path (context_blob is not None): uses _run_query_direct()
    so the keyword leg can be polluted with the session blob. Both legs of
    the context-rot comparison use this path for a clean delta measurement.
    """
    if context_blob is not None:
        return await _run_query_direct(client, q, context_blob=context_blob)

    # Rewriter ablation (#499 fix-forward): when config.use_rewriter, call the
    # hook's rewrite_prompt to extract entities + types, then thread them into
    # recall() as keyword_query (entity-denoised FTS) and boost_types (soft
    # rank lift in rrf_merge). Matches the hook adapter's behavior so the
    # eval baseline reflects production.
    entities: list[str] = []
    types: list[str] = []
    rewriter_fired = False
    keyword_query: str | None = None
    boost_types: set[str] | None = None
    boost_multiplier = 1.5
    if config.use_rewriter:
        hook_mod = _load_hook_module()
        if hook_mod is not None and hasattr(hook_mod, "rewrite_prompt"):
            rewritten = await asyncio.to_thread(hook_mod.rewrite_prompt, q["query"])
            if rewritten:
                entities = rewritten.get("entities") or []
                types = rewritten.get("types") or []
                rewriter_fired = True
                if entities:
                    keyword_query = " ".join(entities)
                allowed = getattr(hook_mod, "ALLOWED_TYPES", None)
                if allowed and types:
                    boost_types = (allowed & set(types)) or None
                boost_multiplier = float(getattr(hook_mod, "TYPE_BOOST_MULTIPLIER", 1.5))

    hits = await recall(
        client,
        q["query"],
        keyword_query=keyword_query,
        boost_types=boost_types,
        boost_multiplier=boost_multiplier,
        config=config,
    )

    top_names = [h.memory.get("name", "?") for h in hits[:10]]
    expected = set(q.get("expected") or [])
    must_not = set(q.get("must_not") or [])

    top3 = top_names[:3]
    top5 = top_names[:5]
    top10 = top_names[:10]
    hits_3 = [n for n in top3 if n in expected]
    hits_5 = [n for n in top5 if n in expected]
    hits_10 = [n for n in top10 if n in expected]
    mn_viol_5 = [n for n in top5 if n in must_not]
    mn_viol_10 = [n for n in top10 if n in must_not]

    first_hit_rank = None
    for i, name in enumerate(top10, start=1):
        if name in expected:
            first_hit_rank = i
            break

    passed = bool(hits_5) and not mn_viol_5
    links_added = sum(1 for h in hits if h.source == "linked")

    return QueryResult(
        id=q["id"],
        query=q["query"],
        kind=q.get("kind", ""),
        expected=sorted(expected),
        must_not=sorted(must_not),
        top_names=top_names,
        first_hit_rank=first_hit_rank,
        hits_at_3=hits_3,
        hits_at_5=hits_5,
        hits_at_10=hits_10,
        must_not_violations_at_5=mn_viol_5,
        must_not_violations_at_10=mn_viol_10,
        passed=passed,
        rewriter_entities=entities,
        rewriter_types=types,
        rewriter_fired=rewriter_fired,
        links_added=links_added,
    )


async def run_all(
    queries: list[dict],
    client,
    config: RecallConfig = PROD_RECALL_CONFIG,
) -> EvalReport:
    results = []
    for q in queries:
        r = await run_query(client, q, config=config)
        results.append(r)

    total = len(results)
    r3 = sum(1 for r in results if r.hits_at_3) / total if total else 0.0
    r5 = sum(1 for r in results if r.hits_at_5) / total if total else 0.0
    r10 = sum(1 for r in results if r.hits_at_10) / total if total else 0.0
    mrr = sum(1.0 / r.first_hit_rank for r in results if r.first_hit_rank) / total if total else 0.0
    ranks_with_hit = [r.first_hit_rank for r in results if r.first_hit_rank]
    mean_rank = sum(ranks_with_hit) / len(ranks_with_hit) if ranks_with_hit else float("nan")
    mn_viol_5 = sum(1 for r in results if r.must_not_violations_at_5)
    mn_viol_10 = sum(1 for r in results if r.must_not_violations_at_10)
    passed = sum(1 for r in results if r.passed)
    rw_fired = sum(1 for r in results if r.rewriter_fired)
    links_added_total = sum(r.links_added for r in results)

    cs = (
        client.table("memories")
        .select("id", count="exact")
        .is_("deleted_at", "null")
        .limit(1)
        .execute()
    )
    corpus_size = cs.count or 0

    return EvalReport(
        timestamp=datetime.now(timezone.utc).isoformat(),
        mode=_build_mode_string(config.use_rewriter, config.use_links),
        corpus_size=corpus_size,
        total_queries=total,
        recall_at_3=r3,
        recall_at_5=r5,
        recall_at_10=r10,
        mrr=mrr,
        mean_rank=mean_rank,
        must_not_violations=mn_viol_5,
        must_not_violations_at_10=mn_viol_10,
        passed=passed,
        failed=total - passed,
        rewriter_fired_count=rw_fired,
        links_added_total=links_added_total,
        results=[asdict(r) for r in results],
    )


# ---------------------------------------------------------------------------
# #241: session-start context rot measurement
# ---------------------------------------------------------------------------

# ~4 chars per token is a rough-but-stable estimate for mixed EN/RU text.
CONTEXT_CHARS_PER_TOKEN = 4
CONTEXT_USER_LIMIT = 2  # match scripts/session-context.py


def _load_session_context(client) -> tuple[str, dict]:
    """Load the items the SessionStart hook injects, return a single keyword
    blob + budget metrics. Mirrors scripts/session-context.py:
      - top 2 user memories (by updated_at)
      - all memories tagged `always_load`
      - active goals

    Skips working_state (tied to cwd, not meaningful in the eval harness).
    The blob is plain text — name + description + tags + content — because
    injection goes into pg_trgm keyword search, which tokenises on whitespace.
    """
    parts: list[str] = []
    counts = {"user": 0, "always_load": 0, "goals": 0}

    def _mem_text(m: dict) -> str:
        pieces = [
            m.get("name") or "",
            m.get("description") or "",
            " ".join(m.get("tags") or []),
            m.get("content") or "",
        ]
        return " ".join(p for p in pieces if p)

    # 1. Top-2 user memories
    try:
        r = (
            client.table("memories")
            .select("name, description, tags, content")
            .eq("type", "user")
            .is_("deleted_at", "null")
            .order("updated_at", desc=True)
            .limit(CONTEXT_USER_LIMIT)
            .execute()
        )
        for m in r.data or []:
            parts.append(_mem_text(m))
            counts["user"] += 1
    except Exception as e:
        print(f"[context-rot] user query failed: {e}", file=sys.stderr)

    # 2. always_load memories (evergreen rules)
    try:
        r = (
            client.table("memories")
            .select("name, description, tags, content")
            .contains("tags", ["always_load"])
            .is_("deleted_at", "null")
            .order("updated_at", desc=True)
            .execute()
        )
        for m in r.data or []:
            parts.append(_mem_text(m))
            counts["always_load"] += 1
    except Exception as e:
        print(f"[context-rot] always_load query failed: {e}", file=sys.stderr)

    # 3. Active goals — flatten title + why + jarvis_focus into blob
    try:
        r = (
            client.table("goals")
            .select("title, why, jarvis_focus, owner_focus")
            .eq("status", "active")
            .execute()
        )
        for g in r.data or []:
            pieces = [
                g.get("title") or "",
                g.get("why") or "",
                g.get("jarvis_focus") or "",
                g.get("owner_focus") or "",
            ]
            parts.append(" ".join(p for p in pieces if p))
            counts["goals"] += 1
    except Exception as e:
        print(f"[context-rot] goals query failed: {e}", file=sys.stderr)

    # 4. Memory catalog (Phase 7.1) — lazy-index entries for live, in-scope
    #    memories. Mirrors scripts/session-context.py:_query_catalog output
    #    format (one bullet per entry, type/scope label, description truncated
    #    to 120 chars) so the context-rot baseline reflects the shape/budget
    #    actually injected at session start. Eval runs project-agnostic so we
    #    scope to global (project IS NULL) — matches non-project cwd sessions.
    try:
        now_iso = datetime.now(timezone.utc).isoformat()
        r = (
            client.table("memories")
            .select("name, description, tags, type, project")
            .is_("expired_at", "null")
            .is_("superseded_by", "null")
            .is_("deleted_at", "null")
            .is_("project", "null")
            .or_(f"valid_to.is.null,valid_to.gt.{now_iso}")
            .order("last_accessed_at", desc=True, nullsfirst=False)
            .limit(200)
            .execute()
        )
        for m in r.data or []:
            if m.get("type") == "user":
                continue
            tags = m.get("tags") or []
            if "always_load" in tags:
                continue
            # Mirror _fmt_catalog_entry in session-context.py — global scope
            # here since we filter project IS NULL.
            desc = (m.get("description") or "").strip()
            if len(desc) > 120:
                desc = desc[:117] + "..."
            parts.append(f"- {m['name']} [{m['type']}/global]: {desc}")
            counts["catalog"] = counts.get("catalog", 0) + 1
    except Exception as e:
        print(f"[context-rot] catalog query failed: {e}", file=sys.stderr)

    blob = " ".join(p.strip() for p in parts if p.strip())
    chars = len(blob)
    budget = {
        "chars": chars,
        "tokens_approx": chars // CONTEXT_CHARS_PER_TOKEN,
        "item_count": sum(counts.values()),
        **counts,
    }
    return blob, budget


def _metrics_from_results(
    results: list[QueryResult], total: int
) -> tuple[float, float, float, float, float, int]:
    r3 = sum(1 for r in results if r.hits_at_3) / total if total else 0.0
    r5 = sum(1 for r in results if r.hits_at_5) / total if total else 0.0
    r10 = sum(1 for r in results if r.hits_at_10) / total if total else 0.0
    mrr = sum(1.0 / r.first_hit_rank for r in results if r.first_hit_rank) / total if total else 0.0
    ranks_with_hit = [r.first_hit_rank for r in results if r.first_hit_rank]
    mean_rank = sum(ranks_with_hit) / len(ranks_with_hit) if ranks_with_hit else float("nan")
    mn = sum(1 for r in results if r.must_not_violations_at_5)
    return r3, r5, r10, mrr, mean_rank, mn


async def run_context_rot_eval(
    queries: list[dict],
    client,
    context_blob: str,
    budget: dict,
) -> ContextRotReport:
    """Run each query twice — plain then with context blob injected into the
    keyword leg — and return aggregate + per-query deltas.

    Sign convention: delta = with_context − plain. Positive = context helps;
    negative = retrieval-induced forgetting (rot).
    """
    plain_results: list[QueryResult] = []
    context_results: list[QueryResult] = []
    per_query: list[dict] = []

    for q in queries:
        # Both legs use _run_query_direct() for a clean delta: context_blob=""
        # → no blob appended (same pipeline as None but same code path as with_ctx).
        plain = await run_query(client, q, context_blob="")
        with_ctx = await run_query(client, q, context_blob=context_blob)
        plain_results.append(plain)
        context_results.append(with_ctx)
        per_query.append(
            asdict(
                ContextRotResult(
                    id=q["id"],
                    query=q["query"],
                    plain_top_names=plain.top_names[:5],
                    context_top_names=with_ctx.top_names[:5],
                    plain_hits_at_5=plain.hits_at_5,
                    context_hits_at_5=with_ctx.hits_at_5,
                    plain_first_hit_rank=plain.first_hit_rank,
                    context_first_hit_rank=with_ctx.first_hit_rank,
                    plain_must_not_viol=plain.must_not_violations_at_5,
                    context_must_not_viol=with_ctx.must_not_violations_at_5,
                    plain_must_not_viol_at_10=plain.must_not_violations_at_10,
                    context_must_not_viol_at_10=with_ctx.must_not_violations_at_10,
                    plain_passed=plain.passed,
                    context_passed=with_ctx.passed,
                )
            )
        )

    total = len(queries)
    p_r3, p_r5, p_r10, p_mrr, p_mean_rank, p_mn = _metrics_from_results(plain_results, total)
    c_r3, c_r5, c_r10, c_mrr, c_mean_rank, c_mn = _metrics_from_results(context_results, total)
    p_mn_10 = sum(1 for r in plain_results if r.must_not_violations_at_10)
    c_mn_10 = sum(1 for r in context_results if r.must_not_violations_at_10)

    only_plain = [
        q["id"]
        for q, p, c in zip(queries, plain_results, context_results)
        if p.passed and not c.passed
    ]
    only_context = [
        q["id"]
        for q, p, c in zip(queries, plain_results, context_results)
        if c.passed and not p.passed
    ]

    cs = (
        client.table("memories")
        .select("id", count="exact")
        .is_("deleted_at", "null")
        .limit(1)
        .execute()
    )

    return ContextRotReport(
        timestamp=datetime.now(timezone.utc).isoformat(),
        corpus_size=cs.count or 0,
        total_queries=total,
        plain_recall_at_3=p_r3,
        context_recall_at_3=c_r3,
        delta_recall_at_3_pp=(c_r3 - p_r3) * 100,
        plain_recall_at_5=p_r5,
        context_recall_at_5=c_r5,
        delta_recall_at_5_pp=(c_r5 - p_r5) * 100,
        plain_recall_at_10=p_r10,
        context_recall_at_10=c_r10,
        delta_recall_at_10_pp=(c_r10 - p_r10) * 100,
        plain_mrr=p_mrr,
        context_mrr=c_mrr,
        delta_mrr=c_mrr - p_mrr,
        plain_mean_rank=p_mean_rank,
        context_mean_rank=c_mean_rank,
        delta_mean_rank=c_mean_rank - p_mean_rank,
        plain_must_not_violations=p_mn,
        context_must_not_violations=c_mn,
        plain_must_not_violations_at_10=p_mn_10,
        context_must_not_violations_at_10=c_mn_10,
        only_plain_passed=only_plain,
        only_context_passed=only_context,
        context_budget=budget,
        per_query=per_query,
    )


def print_context_rot_report(report: ContextRotReport, quiet: bool = False) -> None:
    if not quiet:
        print(
            f"\nQueries: {report.total_queries}   Corpus: {report.corpus_size} memories   Mode: context-rot\n"
        )
        print(
            f"Context budget: {report.context_budget['chars']} chars "
            f"(~{report.context_budget['tokens_approx']} tokens), "
            f"{report.context_budget['item_count']} items "
            f"(user={report.context_budget['user']}, "
            f"always_load={report.context_budget['always_load']}, "
            f"goals={report.context_budget['goals']}, "
            f"catalog={report.context_budget.get('catalog', 0)})\n"
        )

        # Per-query table: plain rank → context rank, mark regressions/wins
        print(f"{'id':<5} {'plain':>6}  {'ctx':>6}  drank  result   query")
        print("-" * 100)
        for r in report.per_query:
            p_rank = r["plain_first_hit_rank"]
            c_rank = r["context_first_hit_rank"]
            p_s = str(p_rank) if p_rank else "-"
            c_s = str(c_rank) if c_rank else "-"
            # drank: positive if context ranked worse (higher number)
            if p_rank and c_rank:
                drank = c_rank - p_rank
                d_s = f"{drank:+d}" if drank else "  0"
            else:
                d_s = "  ."
            if r["plain_passed"] and not r["context_passed"]:
                verdict = "ROT  "
            elif r["context_passed"] and not r["plain_passed"]:
                verdict = "HELP "
            elif r["plain_passed"] and r["context_passed"]:
                verdict = "both "
            else:
                verdict = "miss "
            q = r["query"] if len(r["query"]) <= 55 else r["query"][:52] + "..."
            print(f"{r['id']:<5} {p_s:>6}  {c_s:>6}  {d_s:>5}  {verdict}   {q}")

    print("\n=== CONTEXT-ROT AGGREGATES ===")
    print(f"plain    recall@3 : {_fmt_pct(report.plain_recall_at_3)}")
    print(f"context  recall@3 : {_fmt_pct(report.context_recall_at_3)}")
    sign3 = "+" if report.delta_recall_at_3_pp >= 0 else ""
    print(f"delta recall@3         : {sign3}{report.delta_recall_at_3_pp:.1f} pp")
    print(f"plain    recall@5 : {_fmt_pct(report.plain_recall_at_5)}")
    print(f"context  recall@5 : {_fmt_pct(report.context_recall_at_5)}")
    sign = "+" if report.delta_recall_at_5_pp >= 0 else ""
    print(
        f"delta recall@5        : {sign}{report.delta_recall_at_5_pp:.1f} pp   "
        f"({'context helps' if report.delta_recall_at_5_pp > 0 else 'context rot' if report.delta_recall_at_5_pp < 0 else 'no effect'})"
    )
    sign10 = "+" if report.delta_recall_at_10_pp >= 0 else ""
    print(f"delta recall@10       : {sign10}{report.delta_recall_at_10_pp:.1f} pp")
    sign_mrr = "+" if report.delta_mrr >= 0 else ""
    print(f"delta MRR             : {sign_mrr}{report.delta_mrr:.3f}")
    sign_mrank = "+" if report.delta_mean_rank >= 0 else ""
    print(f"delta mean_rank       : {sign_mrank}{report.delta_mean_rank:.2f}")
    print(
        f"must_not @5 (p/c) : {report.plain_must_not_violations} / {report.context_must_not_violations}"
    )
    print(
        f"must_not @10 (p/c): {report.plain_must_not_violations_at_10} / {report.context_must_not_violations_at_10}"
    )
    if report.only_plain_passed:
        print(
            f"lost with context : {report.only_plain_passed}  (rot — passed plain, failed with context)"
        )
    if report.only_context_passed:
        print(
            f"won  with context : {report.only_context_passed}  (helps — passed only with context)"
        )
    print()


# ---------------------------------------------------------------------------
# Formatting / CLI
# ---------------------------------------------------------------------------


def _fmt_pct(x: float) -> str:
    return f"{x * 100:5.1f}%"


def print_report(report: EvalReport, quiet: bool = False) -> None:
    if not quiet:
        print(
            f"\nQueries: {report.total_queries}   Corpus: {report.corpus_size} memories   Mode: {report.mode}\n"
        )
        print(
            f"{'id':<5} {'kind':<10} {'rank':>5}  {'hit3':>5}  {'hit5':>5}  {'v5':>3}  {'v10':>3}  {'rw':>3}  query"
        )
        print("-" * 115)
        for r in report.results:
            rank_s = str(r["first_hit_rank"]) if r["first_hit_rank"] else "-"
            hit3 = "Y" if r.get("hits_at_3") else " "
            hit = "Y" if r["hits_at_5"] else " "
            v5 = "!" if r["must_not_violations_at_5"] else " "
            v10 = "!" if r.get("must_not_violations_at_10") else " "
            rw = "*" if r.get("rewriter_fired") else " "
            q = r["query"] if len(r["query"]) <= 52 else r["query"][:49] + "..."
            print(
                f"{r['id']:<5} {r['kind']:<10} {rank_s:>5}  {hit3:>5}  {hit:>5}  {v5:>3}  {v10:>3}  {rw:>3}  {q}"
            )

        # fail details
        fails = [r for r in report.results if not r["passed"]]
        if fails:
            print("\nFAILED queries (recall@5 miss or must_not violation):")
            for r in fails:
                print(f"\n  [{r['id']}] {r['query']}")
                print(f"    expected:  {r['expected']}")
                if r["must_not"]:
                    print(f"    must_not:  {r['must_not']}")
                print(f"    top-5:     {r['top_names'][:5]}")
                if r["must_not_violations_at_5"]:
                    print(f"    violated@5:  {r['must_not_violations_at_5']}")
                if r.get("must_not_violations_at_10"):
                    print(f"    violated@10: {r.get('must_not_violations_at_10')}")
                if r.get("rewriter_fired"):
                    print(
                        f"    rewriter:  entities={r.get('rewriter_entities') or []} types={r.get('rewriter_types') or []}"
                    )

    print("\n=== AGGREGATES ===")
    print(f"mode               : {report.mode}")
    print(f"recall@3           : {_fmt_pct(report.recall_at_3)}")
    print(
        f"recall@5           : {_fmt_pct(report.recall_at_5)}  ({report.passed}/{report.total_queries} queries retrieved at least one expected memory in top-5)"
    )
    print(f"recall@10          : {_fmt_pct(report.recall_at_10)}")
    print(f"MRR                : {report.mrr:.3f}")
    print(
        f"mean_rank          : {report.mean_rank:.2f}"
        if not math.isnan(report.mean_rank)
        else "mean_rank          : NaN"
    )
    print(
        f"must_not @5        : {report.must_not_violations}  (lifecycle signal — should be 0 after Phase 1)"
    )
    print(
        f"must_not @10       : {report.must_not_violations_at_10}  (extended lifecycle signal — regressions at ranks 6-10)"
    )
    print(f"passed / failed    : {report.passed} / {report.failed}")
    if "with_rewriter" in report.mode:
        print(f"rewriter fired     : {report.rewriter_fired_count}/{report.total_queries}")
    if "links" in report.mode:
        print(f"links added (sum)  : {report.links_added_total}  (rows added via 1-hop BFS)")
    print()


def print_diff(current: EvalReport, baseline: dict) -> None:
    def delta(k: str) -> str:
        cur = getattr(current, k)
        base = baseline.get(k, 0) if k in baseline else None
        if base is None:
            return f"{cur:.3f}  (baseline: n/a — new metric)"
        d = cur - base
        sign = "+" if d >= 0 else ""
        return f"{cur:.3f}  (baseline {base:.3f}, {sign}{d:.3f})"

    print("=== DELTA vs baseline.json ===")
    print(f"baseline saved : {baseline.get('timestamp', '?')}")
    base_mode = baseline.get("mode", "server_only")
    if base_mode != current.mode:
        print(
            f"MODE MISMATCH  : current={current.mode}  baseline={base_mode}  — delta mixes pipeline changes with mode change"
        )
    else:
        print(f"mode           : {current.mode}")
    print(f"recall@3       : {delta('recall_at_3')}")
    print(f"recall@5       : {delta('recall_at_5')}")
    print(f"recall@10      : {delta('recall_at_10')}")
    print(f"MRR            : {delta('mrr')}")
    print(f"mean_rank      : {delta('mean_rank')}")
    print(
        f"must_not viol  : {current.must_not_violations}  (baseline {baseline.get('must_not_violations', '?')})"
    )
    print()

    # per-query regression
    base_results = {r["id"]: r for r in baseline.get("results", [])}
    regressed = []
    improved = []
    for r in current.results:
        br = base_results.get(r["id"])
        if not br:
            continue
        if br["passed"] and not r["passed"]:
            regressed.append(r["id"])
        elif not br["passed"] and r["passed"]:
            improved.append(r["id"])
    if regressed:
        print(f"REGRESSIONS: {regressed}")
    if improved:
        print(f"IMPROVEMENTS: {improved}")
    if not regressed and not improved:
        print("(no per-query status changes vs baseline)")
    print()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--save-baseline",
        action="store_true",
        help="Overwrite tests/memory-eval/baseline.json with this run's results",
    )
    ap.add_argument("--diff", choices=["baseline"], help="Print delta vs baseline.json")
    ap.add_argument("--quiet", action="store_true")
    ap.add_argument(
        "--queries",
        default=None,
        help="Path to queries.yaml (default: tests/memory-eval/queries.yaml)",
    )
    ap.add_argument(
        "--with-rewriter",
        action="store_true",
        help="Run the hook's Haiku rewriter on each query — entities "
        "substitute for the raw prompt in keyword search, and "
        "returned types narrow both RPC result sets Python-side. "
        "Requires ANTHROPIC_API_KEY; falls back per-query to raw "
        "prompt when the rewriter returns None.",
    )
    ap.add_argument(
        "--with-links",
        action="store_true",
        help="Expand top-5 RRF rows with a 1-hop BFS on memory_links — "
        "closes coverage gaps where the expected memory is one "
        "edge away from a retrieved row. Uses get_linked_memories "
        "RPC with a decayed RRF score (decay=0.5); fails soft if "
        "the RPC is unavailable.",
    )
    ap.add_argument(
        "--with-session-context",
        action="store_true",
        help="#241 context-rot mode: measure whether session-start "
        "context injection helps or hurts recall. Runs each query "
        "twice — plain vs. with the SessionStart hook's blob "
        "appended to the keyword leg — and reports delta "
        "(positive = helps, negative = rot).",
    )
    ap.add_argument(
        "--save-context-rot-baseline",
        action="store_true",
        help="Write tests/memory-eval/context-rot-baseline.json from "
        "this run (only valid with --with-session-context).",
    )
    ap.add_argument(
        "--context-warn-threshold",
        type=float,
        default=-5.0,
        help="Warn if delta recall@5 < this (pp). Default -5.",
    )
    ap.add_argument(
        "--context-fail-threshold",
        type=float,
        default=None,
        help="Exit 1 if delta recall@5 < this (pp). Advisory-only by "
        "default (spec #241: flip to blocking after 2-week "
        "baseline).",
    )
    ap.add_argument(
        "--record",
        default=None,
        help="Run live and save RPC snapshot to PATH (offline replay, ln-635)",
    )
    ap.add_argument(
        "--replay",
        default=None,
        help="Run from cached snapshot at PATH instead of live Supabase (ln-635)",
    )
    ap.add_argument(
        "--ci",
        default=None,
        metavar="SNAPSHOT_PATH",
        help="CI mode: replay from SNAPSHOT_PATH, diff against baseline, exit 1 "
        "if regressions. Needs --diff baseline (ln-633)",
    )
    args = ap.parse_args()

    _load_env()

    repo = Path(__file__).resolve().parent.parent
    queries_path = (
        Path(args.queries) if args.queries else repo / "tests" / "memory-eval" / "queries.yaml"
    )
    baseline_path = repo / "tests" / "memory-eval" / "baseline.json"

    import yaml

    with queries_path.open("r", encoding="utf-8") as f:
        doc = yaml.safe_load(f)
    queries = doc["queries"]

    # --replay / --ci skip Supabase client — load from cached snapshot
    if args.replay or args.ci:
        snapshot_path = Path(args.replay or args.ci)
        if not snapshot_path.exists():
            print(f"ERROR: snapshot not found at {snapshot_path}", file=sys.stderr)
            return 2
        with snapshot_path.open("r", encoding="utf-8") as f:
            raw = json.load(f)
        snapshot = EvalSnapshot(
            timestamp=raw["timestamp"],
            corpus_size=raw["corpus_size"],
            queries={
                qid: QuerySnapshot(**entry) for qid, entry in (raw.get("queries") or {}).items()
            },
        )
        if args.replay:
            report = asyncio.run(run_all_replay(queries, snapshot))
            print_report(report, quiet=args.quiet)
            if args.diff == "baseline":
                if baseline_path.exists():
                    with baseline_path.open("r", encoding="utf-8") as f:
                        baseline = json.load(f)
                    print_diff(report, baseline)
            return 0 if report.failed == 0 else 1
        else:  # --ci
            if args.diff != "baseline":
                print("ERROR: --ci requires --diff baseline", file=sys.stderr)
                return 2
            if not baseline_path.exists():
                print(
                    f"ERROR: no baseline at {baseline_path} — run --save-baseline first",
                    file=sys.stderr,
                )
                return 2
            report = asyncio.run(run_all_replay(queries, snapshot))
            with baseline_path.open("r", encoding="utf-8") as f:
                baseline = json.load(f)
            print_report(report, quiet=args.quiet)
            print_diff(report, baseline)
            # Fail on regressions (baseline query passing, now failing)
            base_results = {r["id"]: r for r in baseline.get("results", [])}
            for r in report.results:
                br = base_results.get(r.id)
                if br and br["passed"] and not r["passed"]:
                    print(f"FAIL: regression on {r.id} — was passing, now failing", file=sys.stderr)
                    return 1
            return 0

    try:
        from supabase import create_client
    except ImportError:
        print("ERROR: supabase package not installed in venv", file=sys.stderr)
        return 2

    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_KEY")
    if not url or not key:
        print("ERROR: SUPABASE_URL / SUPABASE_KEY not set", file=sys.stderr)
        return 2
    client = create_client(url, key)

    # Build RecallConfig from CLI ablation flags (#499).
    cfg = PROD_RECALL_CONFIG
    if args.with_links:
        cfg = dataclasses.replace(cfg, use_links=True)
    if args.with_rewriter:
        cfg = dataclasses.replace(cfg, use_rewriter=True)

    # --record mode captures RPC snapshot then runs live eval
    if args.record:
        if args.with_rewriter or args.with_links:
            print(
                "WARN: --record only captures base pipeline data; rewriter/link state "
                "is NOT preserved in the snapshot.",
                file=sys.stderr,
            )
        snapshot = asyncio.run(_record_snapshot(queries, client, config=cfg))
        record_path = Path(args.record)
        record_path.parent.mkdir(parents=True, exist_ok=True)
        with record_path.open("w", encoding="utf-8") as f:
            json.dump(asdict(snapshot), f, indent=2, ensure_ascii=False, default=str)
        print(
            f"RPC snapshot saved to {record_path}  ({len(snapshot.queries)} queries, "
            f"{snapshot.corpus_size} corpus)"
        )
        # Also run the full live eval for immediate feedback
        report = asyncio.run(run_all(queries, client, config=cfg))
        print_report(report, quiet=args.quiet)
        if args.diff == "baseline":
            if baseline_path.exists():
                with baseline_path.open("r", encoding="utf-8") as f:
                    baseline = json.load(f)
                print_diff(report, baseline)
        return 0

        # (--replay / --ci handled before client init above)
        return 0

    # #241 context-rot path: dual-run plain vs context-injected and exit
    if args.with_session_context:
        if args.with_rewriter or args.with_links:
            print(
                "ERROR: --with-session-context is exclusive with --with-rewriter / "
                "--with-links (baseline signal only). Run separately.",
                file=sys.stderr,
            )
            return 2
        blob, budget = _load_session_context(client)
        if not blob:
            print("ERROR: loaded empty session context — nothing to inject.", file=sys.stderr)
            return 2
        rot_report = asyncio.run(run_context_rot_eval(queries, client, blob, budget))
        print_context_rot_report(rot_report, quiet=args.quiet)

        if args.save_context_rot_baseline:
            rot_path = repo / "tests" / "memory-eval" / "context-rot-baseline.json"
            rot_path.parent.mkdir(parents=True, exist_ok=True)
            with rot_path.open("w", encoding="utf-8") as f:
                json.dump(asdict(rot_report), f, indent=2, ensure_ascii=False)
            print(f"Context-rot baseline saved to {rot_path}")

        delta = rot_report.delta_recall_at_5_pp
        if delta < args.context_warn_threshold:
            print(
                f"WARN: delta recall@5 = {delta:+.1f} pp below warn threshold "
                f"({args.context_warn_threshold:+.1f} pp) — context rot present.",
                file=sys.stderr,
            )
        if args.context_fail_threshold is not None and delta < args.context_fail_threshold:
            print(
                f"FAIL: delta recall@5 = {delta:+.1f} pp below fail threshold "
                f"({args.context_fail_threshold:+.1f} pp).",
                file=sys.stderr,
            )
            return 1
        return 0

    report = asyncio.run(run_all(queries, client, config=cfg))
    print_report(report, quiet=args.quiet)

    if args.diff == "baseline":
        if not baseline_path.exists():
            print(f"(no baseline at {baseline_path} — run --save-baseline first)")
        else:
            with baseline_path.open("r", encoding="utf-8") as f:
                baseline = json.load(f)
            print_diff(report, baseline)

    if args.save_baseline:
        baseline_path.parent.mkdir(parents=True, exist_ok=True)
        with baseline_path.open("w", encoding="utf-8") as f:
            json.dump(asdict(report), f, indent=2, ensure_ascii=False)
        print(f"Baseline saved to {baseline_path}")

    # exit code: 0 if all queries passed, 1 otherwise — CI-friendly
    return 0 if report.failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
