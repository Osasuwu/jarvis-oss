"""UserPromptSubmit hook: task-aware hybrid recall injected as context.

Thin adapter over mcp-memory/recall.py (#499, slice 4 of 4). main()
calls asyncio.run(_recall(...)); the hook owns only caller-policy code:
  - rewriter call (LLM — stays adapter-side)
  - project detection
  - known-unknown gate (Phase 7.3 per-prompt widen signal)
  - ALLOWED_TYPES post-filter (user/project excluded here, loaded at
    session start by scripts/session-context.py)
  - brief vs full formatting + char-budget trim
  - stdout emission via hookSpecificOutput JSON

Pipeline (semantic + keyword + RRF + temporal + link expansion) runs
inside recall() from mcp-memory/recall.py — one implementation, three
adapters (hook, MCP server, eval harness).

Known divergences vs pre-#499 behavior (documented, not bugs):
  • Rewriter-extracted entities no longer denoise the keyword leg inside
    recall(). recall() receives the raw prompt; entity substitution is
    adapter-side but not yet plumbed through the recall() API. The
    rewriter output still appears in the header note for transparency.
  • TYPE_BOOST_MULTIPLIER (soft rewriter-type boost in rrf_merge) is not
    yet threaded into recall(). Future slice: add boost_types to
    RecallConfig or recall() signature.
  • Hook used to embed the prompt TWICE per invocation. Resolved by #508:
    embed() for the known-unknown gate now passes query_embedding into
    recall(), which skips its internal _embed_query(). Single Voyage API
    call per invocation.
"""

import asyncio
import dataclasses
import json
import os
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

# ---------------------------------------------------------------------------
# Bootstrap: re-exec under venv if running under system Python
# ---------------------------------------------------------------------------
_root = Path(__file__).resolve().parent.parent
_venv_py = _root / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")

# Guard: only re-exec when run as script. When imported (e.g. by tests via
# importlib with a non-"__main__" module name), skip the re-exec so the
# module's top-level sys.exit doesn't kill pytest collection.
if (
    __name__ == "__main__"
    and _venv_py.exists()
    and Path(sys.executable).resolve() != _venv_py.resolve()
):
    sys.exit(subprocess.call([str(_venv_py), str(Path(__file__).resolve())]))

# ---------------------------------------------------------------------------
# Under venv — safe to import deps
# ---------------------------------------------------------------------------
import httpx
from dotenv import load_dotenv

for _env in [_root / ".env", _root.parent / ".env"]:
    if _env.exists():
        # override=True: shells on some devices export empty ANTHROPIC_API_KEY
        # which would otherwise win over the .env value and disable the rewriter.
        load_dotenv(_env, override=True)
        break

from supabase import create_client

# recall.py is the deep module (#496-#499). Hook adds mcp-memory/ to
# sys.path and re-exports the public names so tests that introspect
# mrh.<NAME> directly (tests/memory/test_memory_recall_hook.py) keep working.
# noqa: F401 — names not used inside this module; they exist as the
# re-export surface for downstream test code.
sys.path.insert(0, str(_root / "mcp-memory"))
from recall import (  # noqa: E402
    LINK_DECAY,  # noqa: F401
    LINK_EXPAND_TOP_K,  # noqa: F401
    LINK_SCORE_FIELD,  # noqa: F401
    PROD_RECALL_CONFIG,
    RRF_K,  # noqa: F401
    RecallConfig,  # noqa: F401
    RecallHit,
    cosine_sim as _cosine_sim,  # noqa: F401
    expand_links,  # noqa: F401
    filter_excluded_tags as _filter_excluded_tags,  # noqa: F401
    merge_with_links,  # noqa: F401
    parse_pgvector as _parse_embedding,  # noqa: F401
    recall as _recall,
    rrf_merge,  # noqa: F401
    score_linked_rows as _score_linked_rows,  # noqa: F401
)

# Per-session (id, mode, generation) dedup state, shared with the PreToolUse
# recall hook (#1276). Mode keeps the two hooks' namespaces disjoint so a
# memory shown at UserPromptSubmit can still surface mid-turn and vice versa.
sys.path.insert(0, str(_root / "scripts"))
from lib.recall_dedup import (  # noqa: E402
    MODE_BRIEF,
    MODE_FULL,
    filter_emittable,
    record_emitted,
)

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
# Hook-local override: tighter than recall.SIMILARITY_THRESHOLD (0.25, server
# default). Calibrated 2026-04-17 against voyage-3-lite/512 on real user
# prompts: top-similarity sits at 0.35-0.50 for relevant memories and below
# 0.25 for unrelated ones. 0.30 catches clearly-relevant matches without
# firing on conversational glue tokens. Expressed as a RecallConfig flag
# flip via dataclasses.replace(PROD_RECALL_CONFIG, semantic_threshold=0.30)
# in main() (#499).
SIMILARITY_THRESHOLD = 0.30
# Phase 7.2 default: brief-mode one-line entries instead of full content. Jarvis
# sees the inventory relevant to the prompt and fetches content via memory_get
# on hits it actually wants. Reduces per-turn rot since we no longer dump
# 5-10 full bodies into every UserPromptSubmit.
BRIEF_MODE = True
CHAR_BUDGET_FULL = 16_000  # ~4K tokens — widened full-mode cap (#1276, was 40K)
CHAR_BUDGET_BRIEF = 12_000  # ~3K tokens ceiling — rarely hit after MAX_BRIEF_ENTRIES cap
CHAR_BUDGET = CHAR_BUDGET_BRIEF if BRIEF_MODE else CHAR_BUDGET_FULL
FETCH_LIMIT = 50  # retained for legacy reference; recall() controls its own fetch window
# Cap brief-mode injection at top-N direct hits. Earlier default was char-budget
# only, which let 30-40 entries through every prompt (~4-5KB) — mostly tail
# noise the agent never reads. Top-7 preserves the relevance head; deeper hits
# stay reachable via memory_recall on demand.
MAX_BRIEF_ENTRIES = 7
# Top-N safety net for widened full mode, on top of the CHAR_BUDGET_FULL char
# cap — a pathological recall can't inject an unbounded number of bodies even
# when each individual one is small (#1276).
MAX_FULL_ENTRIES = 8

# Phase 7.3: known-unknowns as per-prompt gate. When the current prompt is
# semantically close to an open known_unknown (a query that previously hit
# a recall gap), switch OFF brief mode for this invocation — the topic has
# historically needed more context than names + descriptions. Fail-soft: any
# DB error returns False and the default brief path runs.
#
# Threshold 0.85 picks "same topic, possibly different phrasing" — calibrated
# against the 0.9 used for known_unknowns semantic dedup in server.py so a
# prompt that would DEDUP to an existing gap also triggers widening, but a
# prompt that's only loosely related doesn't. SCAN_LIMIT caps the client-side
# cosine sweep; open unknowns are bounded by Haiku's recall-gap rate and
# rarely exceed a few hundred, so 200 is a safe cap.
KNOWN_UNKNOWN_GATE_THRESHOLD = 0.85
KNOWN_UNKNOWN_SCAN_LIMIT = 200
# Types loaded per-prompt. Excluded:
#   'user'    — loaded at session start (scripts/session-context.py top-2)
#   'project' — dominated by working_state_* which is session-specific and
#               already session-loaded; broader project memories will be
#               pulled in once Phase 3 gains a proper tag filter.
ALLOWED_TYPES = {"feedback", "decision", "reference"}
MIN_PROMPT_CHARS = 15  # too-short prompts produce noisy embeddings
# Rewriter type boost calibration constant. Pre-#499 this was threaded into
# rrf_merge via boost_types. After #499, the orchestration runs inside
# recall() which does not yet accept boost_types; the constant is kept here
# so tests (TestRrfMergeBoost) can verify the calibration invariant against
# mrh.TYPE_BOOST_MULTIPLIER, and for a future slice that adds boost_types
# to RecallConfig.
TYPE_BOOST_MULTIPLIER = 1.5

# 1-hop BFS link-expansion constants imported from recall.py (#497):
# LINK_EXPAND_TOP_K, LINK_DECAY, LINK_SCORE_FIELD

# Projects Jarvis tracks — cwd basename must match one of these to scope recall.
# Anything else → no project filter (load from global + all projects).
# Derived from config/repos.conf so the set is owner-agnostic; falls back to
# this checkout's own directory name when no repos are configured yet.
def _load_known_projects() -> set[str]:
    names: set[str] = set()
    try:
        for line in (_root / "config" / "repos.conf").read_text(
            encoding="utf-8"
        ).splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "/" in line.split()[0]:
                names.add(line.split()[0].split("/", 1)[1])
    except OSError:
        pass
    return names or {_root.name}


KNOWN_PROJECTS = _load_known_projects()

# Stats file for per-session fire-rate audit (#1276). Mirrors the
# pretooluse-recall-hook stats file (same keys, same best-effort semantics):
# fired (recall returned ≥1 allowed hit), emitted (≥1 memory injected),
# deduped (memories skipped by the (id, mode) generation dedup).
_CLAUDE_HOME_OVERRIDE = os.environ.get("JARVIS_CLAUDE_HOME")
_CLAUDE_HOME = (
    Path(_CLAUDE_HOME_OVERRIDE).expanduser()
    if _CLAUDE_HOME_OVERRIDE
    else Path.home() / ".claude"
)
STATS_FILE = _CLAUDE_HOME / "cache" / "memory-recall-stats.json"


def _bump_stat(key: str, delta: int = 1) -> None:
    """Increment a counter in the stats file. Best-effort; never raises."""
    try:
        STATS_FILE.parent.mkdir(parents=True, exist_ok=True)
        if STATS_FILE.exists():
            try:
                data = json.loads(STATS_FILE.read_text(encoding="utf-8"))
                if not isinstance(data, dict):
                    data = {}
            except (OSError, json.JSONDecodeError):
                data = {}
        else:
            data = {}
        data[key] = int(data.get(key, 0)) + delta
        data.setdefault("session_started_at", time.time())
        tmp = STATS_FILE.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(data), encoding="utf-8")
        tmp.replace(STATS_FILE)
    except OSError:
        pass

VOYAGE_API_URL = "https://api.voyageai.com/v1/embeddings"
VOYAGE_MODEL = "voyage-3-lite"
EMBED_TIMEOUT = 8.0  # keep responsive; hook blocks user prompt

# Haiku rewriter: extracts {entities, types} from the raw prompt.
# Budget is read-path — user is waiting — so 2.5s is the cap. On failure
# we fall back to raw-prompt FTS and the default ALLOWED_TYPES.
ANTHROPIC_API_URL = "https://api.anthropic.com/v1/messages"
ANTHROPIC_VERSION = "2023-06-01"
REWRITER_MODEL = "claude-haiku-4-5"
REWRITER_TIMEOUT = 2.5
REWRITER_MAX_TOKENS = 200
MIN_REWRITE_CHARS = 25  # shorter than this → LLM call not worth the latency
REWRITER_MAX_ENTITIES = 8
REWRITER_MAX_TYPES = 3
# Types the rewriter is allowed to suggest. Subset of {feedback, decision,
# reference} matches ALLOWED_TYPES; "episode" is rejected here because
# episodic memories aren't yet surfaced by this hook (Phase 4 feature,
# separate wiring).
REWRITER_VALID_TYPES = {"feedback", "decision", "reference"}

REWRITER_SYSTEM_PROMPT = """You extract recall signals from a user prompt for a personal AI agent's long-term memory system.

Given the user's raw prompt, output two things:

1. entities: 1-8 literal keywords or short phrases likely to appear verbatim in relevant memories. Prefer:
   - proper nouns (project names, people, tools)
   - file paths, function names, identifiers (e.g. memory_store, classifier.py)
   - technical terms kept as multi-word phrases (e.g. "Phase 3", "RRF merge")
   Drop stopwords, pronouns, conversational glue ("can you", "help me", "I want").
   Lowercase everything. If nothing literal stands out, return an empty list.

2. types: subset of {feedback, decision, reference} when the prompt clearly concerns ONE of them:
   - feedback: user preferences, working style, corrections ("how do I like tests written?")
   - decision: past architectural choices, rationale ("did we decide X?", "why did we pick Y?")
   - reference: pointers to external systems, dashboards, repo links ("where is Z tracked?")
   Empty list = no narrowing (default behavior, use when the prompt is a generic task).

Output strict JSON only, no prose:
{"entities": ["..."], "types": ["..."]}"""


def emit(context: str):
    """Output additionalContext as hookSpecificOutput JSON and exit 0."""
    if not context:
        sys.exit(0)
    out = {
        "hookSpecificOutput": {
            "hookEventName": "UserPromptSubmit",
            "additionalContext": context,
        }
    }
    json.dump(out, sys.stdout)
    sys.exit(0)


def silent_exit():
    sys.exit(0)


def embed(text: str) -> list[float] | None:
    api_key = os.environ.get("VOYAGE_API_KEY")
    if not api_key:
        return None
    try:
        with httpx.Client(timeout=EMBED_TIMEOUT) as client:
            resp = client.post(
                VOYAGE_API_URL,
                headers={"Authorization": f"Bearer {api_key}"},
                json={"model": VOYAGE_MODEL, "input": [text], "input_type": "query"},
            )
            resp.raise_for_status()
            return resp.json()["data"][0]["embedding"]
    except Exception:
        return None


def _parse_rewriter(text: str) -> dict | None:
    """Parse Haiku's JSON reply. Tolerant of stray prose around the object.

    Returns {"entities": [...], "types": [...]} with both lists validated
    and capped, or None when the output is unusable (no JSON, wrong shape,
    both lists empty after validation).
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

    # Guard against non-dict JSON slipping through brace-matching (e.g. the
    # model wraps a list or string in a way that still brackets with {}).
    # Without this, data.get below raises and the exception propagates out
    # of the future, breaking the hook's fail-soft contract.
    if not isinstance(data, dict):
        return None

    raw_entities = data.get("entities")
    if isinstance(raw_entities, list):
        entities = [
            str(e).strip().lower() for e in raw_entities if isinstance(e, (str, int, float))
        ]
        entities = [e for e in entities if e][:REWRITER_MAX_ENTITIES]
    else:
        entities = []

    raw_types = data.get("types")
    if isinstance(raw_types, list):
        types = [str(t).strip().lower() for t in raw_types if isinstance(t, str)]
        types = [t for t in types if t in REWRITER_VALID_TYPES][:REWRITER_MAX_TYPES]
    else:
        types = []

    if not entities and not types:
        return None
    return {"entities": entities, "types": types}


def rewrite_prompt(prompt: str) -> dict | None:
    """Ask Haiku to extract {entities, types} for better recall.

    Returns the parsed dict or None on: too-short prompt, missing API key,
    network/timeout error, unparseable output. Caller falls back to the
    raw prompt for keyword search and the default ALLOWED_TYPES.
    """
    if len(prompt) < MIN_REWRITE_CHARS:
        return None
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        return None
    try:
        with httpx.Client(timeout=REWRITER_TIMEOUT) as client:
            resp = client.post(
                ANTHROPIC_API_URL,
                headers={
                    "x-api-key": api_key,
                    "anthropic-version": ANTHROPIC_VERSION,
                    "content-type": "application/json",
                },
                json={
                    "model": REWRITER_MODEL,
                    "max_tokens": REWRITER_MAX_TOKENS,
                    "system": REWRITER_SYSTEM_PROMPT,
                    "messages": [{"role": "user", "content": prompt}],
                },
            )
            resp.raise_for_status()
            payload = resp.json()
    except Exception:
        return None

    blocks = payload.get("content", [])
    text = ""
    for b in blocks:
        if isinstance(b, dict) and b.get("type") == "text":
            text = b.get("text", "")
            break
    return _parse_rewriter(text)


def detect_project(cwd: str) -> str | None:
    """Return the known project a path belongs to, scanning all components.

    Worktree cwds (`<repo>/.claude/worktrees/<name>`) and subdirectories
    resolve to the containing repo; rightmost match wins.
    """
    try:
        parts = Path(cwd).parts
    except Exception:
        return None
    for part in reversed(parts):
        if part.lower() in KNOWN_PROJECTS:
            return part.lower()
    return None


def check_known_unknown_gate(client, query_embedding: list[float] | None) -> bool:
    """Phase 7.3 gate: has this prompt's topic been a recall gap before?

    Scans open known_unknowns and returns True on the first row whose stored
    query_embedding has cosine sim >= KNOWN_UNKNOWN_GATE_THRESHOLD with the
    current prompt's embedding. Caller uses the result to switch OFF brief
    mode and widen the char budget for this invocation.

    Fail-soft — returns False on any DB/parse error so the default (brief)
    path still runs. Never raises.
    """
    if not query_embedding:
        return False
    try:
        result = (
            client.table("known_unknowns")
            .select("query_embedding")
            .eq("status", "open")
            .not_.is_("query_embedding", "null")
            .limit(KNOWN_UNKNOWN_SCAN_LIMIT)
            .execute()
        )
    except Exception:
        return False
    for row in result.data or []:
        stored = _parse_embedding(row.get("query_embedding"))
        if stored and _cosine_sim(query_embedding, stored) >= KNOWN_UNKNOWN_GATE_THRESHOLD:
            return True
    return False


def _score_str(m: dict) -> str:
    """Render the ranking signal visible to the reader.

    Shared between `format_memory` (full) and `format_memory_brief`. The
    priority mirrors rrf_merge's field invariants: _rrf_score is set only on
    dual-signal rows; _sort_score only on boosted single-signal rows;
    _link_score only on rows pulled in by 1-hop BFS. Falling through to
    native similarity/rank keeps the displayed score truthful.
    """
    rrf = m.get("_rrf_score")
    sort_score = m.get("_sort_score")
    link_score = m.get(LINK_SCORE_FIELD)
    sim = m.get("similarity")
    rank = m.get("rank")
    if rrf is not None:
        return f" (rrf {rrf:.3f})"
    if sort_score is not None:
        return f" (boost {sort_score:.3f})"
    if link_score is not None:
        return f" (link {link_score:.3f})"
    if sim is not None:
        return f" (sim {sim:.2f})"
    if rank is not None:
        return f" (rank {rank:.2f})"
    return ""


def format_memory_brief(m: dict) -> str:
    """One-line preview for bulk auto-injection (Phase 7.2).

    Format: `- name [type/project] [tags] (score): description`. This hook
    block adds the per-query score so the agent can distinguish hybrid-ranked
    hits from a recency-sorted inventory, and always emits an explicit
    `type/project` scope. No content body — agent pulls via memory_get on
    anything worth reading.
    """
    tags = m.get("tags") or []
    tags_str = f" [{', '.join(tags)}]" if tags else ""
    proj = m.get("project") or "global"
    desc = (m.get("description") or "").strip()
    return f"- {m['name']} [{m['type']}/{proj}]{tags_str}{_score_str(m)}: {desc}"


def format_memory(m: dict) -> str:
    tags = m.get("tags") or []
    tags_str = f" [{', '.join(tags)}]" if tags else ""
    proj = m.get("project") or "global"
    desc = m.get("description") or ""
    content = m.get("content") or ""
    header = f"## {m['name']} ({m['type']}, {proj}){tags_str}{_score_str(m)}"
    body = f"*{desc}*\n\n{content}" if desc else content
    return f"{header}\n{body}"


def main():
    # Force UTF-8 decode: on Windows, sys.stdin default codec is cp1251 which
    # mangles Cyrillic prompts from Claude Code (always UTF-8).
    try:
        raw = sys.stdin.buffer.read().decode("utf-8", errors="replace")
    except Exception:
        silent_exit()
    if not raw.strip():
        silent_exit()

    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        silent_exit()

    prompt = (data.get("prompt") or "").strip()
    if len(prompt) < MIN_PROMPT_CHARS:
        silent_exit()

    # session_id keys the per-session compaction generation + dedup state.
    # Missing → dedup disabled (filter_emittable/record_emitted no-op).
    session_id = data.get("session_id") or data.get("sessionId") or ""

    cwd = data.get("cwd") or os.getcwd()
    project = detect_project(cwd)

    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_KEY")
    if not url or not key:
        silent_exit()

    # Embed and rewrite run in parallel — both are network-bound.
    # embed() result feeds the known-unknown gate (Phase 7.3 widening).
    # rewrite_prompt() result feeds the header note (entities display).
    # The embed() result is now passed into recall() via query_embedding,
    # eliminating the pre-#508 double-embed overhead.
    with ThreadPoolExecutor(max_workers=2) as ex:
        fut_embed = ex.submit(embed, prompt)
        fut_rewrite = ex.submit(rewrite_prompt, prompt)
        query_embedding = fut_embed.result()
        rewritten = fut_rewrite.result()

    entities = (rewritten or {}).get("entities") or []
    requested_types = (rewritten or {}).get("types") or []

    # Rewriter-derived signals threaded into recall() (#499 fix-forward):
    #   - keyword_query: entity-denoised text for the FTS leg. Strips
    #     conversational glue ("help me", "can you") that dilutes keyword
    #     matches; literal entities match memory bodies verbatim.
    #   - boost_types: ALLOWED ∩ requested. Soft rank boost in rrf_merge —
    #     misclassified-but-relevant single-signal hits still survive.
    keyword_query = " ".join(entities) if entities else None
    boost_types = (ALLOWED_TYPES & set(requested_types)) if requested_types else None

    try:
        client = create_client(url, key)
    except Exception:
        silent_exit()

    # Phase 7.3: is this prompt a repeat of a topic we've had weak recall on?
    # If yes, widen — disable brief mode for this invocation and raise the
    # char budget to CHAR_BUDGET_FULL (bounded by the widen-gate, #1276) so
    # the agent gets bodies, not just names. Gate is best-effort; any DB error
    # falls back to False.
    widened = BRIEF_MODE and check_known_unknown_gate(client, query_embedding)
    brief_mode = BRIEF_MODE and not widened
    char_budget = CHAR_BUDGET_BRIEF if brief_mode else CHAR_BUDGET_FULL
    # Dedup mode: brief and full (widened) are disjoint namespaces, so a memory
    # shown in a brief turn can still be deep-dived when the topic is widened.
    mode = MODE_BRIEF if brief_mode else MODE_FULL

    # Hook config: tighter semantic threshold than the PROD default (0.25),
    # calibrated for conversational prompts which carry more glue tokens.
    hook_config = dataclasses.replace(PROD_RECALL_CONFIG, semantic_threshold=SIMILARITY_THRESHOLD)

    try:
        hits: list[RecallHit] = asyncio.run(
            _recall(
                client,
                prompt,
                project=project,
                keyword_query=keyword_query,
                boost_types=boost_types,
                boost_multiplier=TYPE_BOOST_MULTIPLIER,
                config=hook_config,
                query_embedding=query_embedding,
            )
        )
    except Exception:
        silent_exit()

    # Derive signal label from RecallHit source attribution BEFORE the
    # ALLOWED_TYPES filter. This ensures the header note reflects pipeline
    # activity (which legs fired, RRF fusion) regardless of what survives
    # the type post-filter. If dual-hit rows happen to carry excluded types,
    # has_dual stays True and the signal reads "semantic+keyword (RRF)" —
    # truthful about what the pipeline did (#507).
    has_dual = any(h.memory.get("_rrf_score") is not None for h in hits)
    has_sem = any(h.source == "semantic" for h in hits)
    has_kw = any(h.source == "keyword" for h in hits) or has_dual
    linked_count = sum(1 for h in hits if h.source == "linked")

    # Post-filter by ALLOWED_TYPES (hook caller policy).
    # user/project memories are loaded by scripts/session-context.py at
    # session start and excluded here to avoid duplication.
    hits = [h for h in hits if h.memory.get("type") in ALLOWED_TYPES]
    if not hits:
        silent_exit()
    _bump_stat("fired")

    # (id, mode) generation dedup (#1276): a memory already injected in this
    # mode during the current compaction generation is not injected again.
    # filter_emittable is keyed off the memory row's id (name as fallback), so
    # we remap kept rows back onto their RecallHit objects by that same key.
    kept_memories = filter_emittable(session_id, [h.memory for h in hits], mode)
    kept_keys = {m.get("id") or m.get("name") for m in kept_memories}
    deduped_count = len(hits) - len(kept_memories)
    hits = [h for h in hits if (h.memory.get("id") or h.memory.get("name")) in kept_keys]
    if deduped_count:
        _bump_stat("deduped", delta=deduped_count)
    if not hits:
        silent_exit()
    signal = (
        "semantic+keyword (RRF)"
        if (has_sem and has_kw)
        else "semantic-only"
        if has_sem
        else "keyword-only"
    )
    if linked_count:
        signal += f" + {linked_count} linked"

    scope = f" (project: {project}+global)" if project else " (all projects)"
    rewriter_note = ""
    if rewritten:
        bits = []
        if entities:
            bits.append(f"entities: {', '.join(entities)}")
        if boost_types:
            bits.append(f"boost: {', '.join(sorted(boost_types))}")
        if bits:
            rewriter_note = f" — rewriter: {'; '.join(bits)}"
    mode_tag = ", brief" if brief_mode else ""
    if widened:
        mode_tag += ", widened: known-unknown match"
    hint = (
        "\n\n(Brief mode — names + descriptions only. "
        "Use memory_get(name=...) to fetch full content for any hit worth reading.)"
        if brief_mode
        else ""
    )
    header = (
        f"# Memories on topic{scope}\n\n"
        f"Hybrid recall ({signal}{mode_tag}){rewriter_note}:{hint}\n\n"
    )
    parts = [header]
    total = len(header)
    included_ids = []

    # Full mode separates bodies with `---`; brief mode is one line per entry,
    # so a single newline is enough and keeps the injected block compact.
    separator = "\n" if brief_mode else "\n\n---\n\n"
    formatter = format_memory_brief if brief_mode else format_memory
    entry_cap = MAX_BRIEF_ENTRIES if brief_mode else MAX_FULL_ENTRIES

    for hit in hits:
        row = hit.memory
        block = formatter(row) + separator
        if total + len(block) > char_budget:
            break
        if len(included_ids) >= entry_cap:
            break
        parts.append(block)
        total += len(block)
        if row.get("id"):
            included_ids.append(row["id"])

    if len(included_ids) == 0:
        silent_exit()

    # Trim trailing separator
    if parts[-1].endswith(separator):
        parts[-1] = parts[-1][: -len(separator)]

    # Record this generation's emissions BEFORE the touch, so a memory is
    # deduped from the next prompt only once the model has actually seen it.
    record_emitted(session_id, included_ids, mode)
    _bump_stat("emitted")

    # Touch accessed memories (fire-and-forget; failures ignored). Only the
    # emitted ids are touched — a hit that was deduped or cut by the budget was
    # not injected, so bumping its recency would lie about what the model saw
    # (#1276 accessed-fix).
    try:
        client.rpc("touch_memories", {"memory_ids": included_ids}).execute()
    except Exception:
        pass

    # Emit memory_recall event for FOK batch processor (#439 D2-bis).
    # Mirrors the server-side `_emit_recall_event` shape exactly so the FOK
    # judge sees identical features regardless of recall source: cosine
    # `similarity` (NOT `_final_score`, which is RRF-rescaled and on a
    # different scale), top_sim from the same field, and `repo` set to the
    # canonical full repo slug used elsewhere in the events table.
    try:
        included_set = set(included_ids)
        included_hits = [h for h in hits if h.memory.get("id") in included_set]
        returned_similarities = [
            h.semantic_score if h.semantic_score > 0 else None for h in included_hits
        ]
        top_sim = included_hits[0].semantic_score if included_hits else 0.0
        event_payload = {
            "query": prompt,
            "returned_ids": included_ids,
            "returned_similarities": returned_similarities,
            "returned_count": len(included_ids),
            "top_sim": top_sim,
            "project": project,
            "source": "memory-recall-hook",
        }
        client.table("events").insert(
            {
                "event_type": "memory_recall",
                "severity": "info",
                "repo": "your-username/your-repo",
                "source": "memory-recall-hook",
                "title": f"Memory recall: {prompt[:60]}",
                "payload": event_payload,
            }
        ).execute()
    except Exception:
        pass

    emit("".join(parts))


if __name__ == "__main__":
    main()
