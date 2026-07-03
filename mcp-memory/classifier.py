"""Phase 2b — Mem0-style write-time classifier.

Given a candidate memory and its top-K semantically-similar neighbors,
asks Claude Haiku-4.5 to emit one of:

    ADD    — candidate is new information; just insert it
    UPDATE — candidate revises one of the neighbors; supersede that neighbor
    DELETE — candidate's purpose is to mark a neighbor as no longer true
    NOOP   — candidate is redundant; the neighbor already covers it

This replaces the SUPERSEDE_SIM_THRESHOLD>=0.85 heuristic in
_create_auto_links. The heuristic was both too conservative (real
paraphrase similarity sits ~0.80) and too coarse (couldn't tell
"X is true" from "X is no longer true").

Async + httpx so we don't add the anthropic SDK as a dependency. The
endpoint is the public Messages API; the format is documented and
stable. A 3s timeout keeps memory_store latency bounded — on timeout
or any error we return None and the caller falls back to the legacy
SUPERSEDE_SIM_THRESHOLD heuristic (so we never regress to "do nothing").
"""

from __future__ import annotations

import asyncio
import json
import os
from dataclasses import dataclass
from typing import Any

import httpx

ANTHROPIC_API_URL = "https://api.anthropic.com/v1/messages"
ANTHROPIC_VERSION = "2023-06-01"
CLASSIFIER_MODEL = "claude-haiku-4-5"
CLASSIFIER_TIMEOUT = 3.0  # seconds — bounds memory_store latency
MAX_TOKENS = 400
MAX_NEIGHBOR_CONTENT_CHARS = 600  # truncate long memories before sending


VALID_DECISIONS = ("ADD", "UPDATE", "DELETE", "NOOP")


@dataclass
class ClassifierDecision:
    decision: str          # ADD / UPDATE / DELETE / NOOP
    target_id: str | None  # which neighbor (if UPDATE/DELETE)
    confidence: float      # 0..1, model's self-reported confidence
    reasoning: str         # one-line explanation, kept for the review queue


SYSTEM_PROMPT = """You are a memory-curation classifier for a personal AI agent's long-term memory store.

You receive a CANDIDATE memory the agent is about to save, plus the top semantically-similar NEIGHBORS already in storage. Your job is to decide what to do with the candidate so memory stays coherent over time.

Output exactly one of:
  - ADD: candidate is genuinely new information. None of the neighbors cover it. Just insert.
  - UPDATE: candidate refines or revises one specific neighbor (corrects it, adds detail, changes status). The old neighbor should be superseded by the candidate.
  - DELETE: candidate's content explicitly negates or invalidates one specific neighbor (e.g., "X is no longer true", "we abandoned approach Y"). The neighbor should be marked expired; the candidate may or may not be useful on its own — that's already decided by the caller, you only choose the neighbor's fate.
  - NOOP: candidate is redundant. A neighbor already states the same fact at the same level of detail. The candidate adds no new information.

Rules:
  - UPDATE / DELETE require picking exactly ONE neighbor (target_id). If multiple seem equally good, pick the highest-similarity one.
  - "Same topic, different fact" is UPDATE if it corrects/refines, DELETE if it negates.
  - Different memory types (user vs project vs decision) usually mean ADD even at high similarity — the type carries semantic meaning.
  - Be conservative with DELETE. Only when the candidate's wording explicitly invalidates the target.
  - Confidence should reflect ambiguity: 0.9+ for unambiguous cases, 0.5-0.7 when the call is judgement, <0.5 when you're guessing.

Output strict JSON, nothing else:
{
  "decision": "ADD" | "UPDATE" | "DELETE" | "NOOP",
  "target_id": "<uuid of chosen neighbor or null>",
  "confidence": <float 0..1>,
  "reasoning": "<one short sentence>"
}"""


def _truncate(text: str, limit: int = MAX_NEIGHBOR_CONTENT_CHARS) -> str:
    if not text:
        return ""
    if len(text) <= limit:
        return text
    return text[:limit] + "…"


def _build_user_message(candidate: dict, neighbors: list[dict]) -> str:
    """Render the prompt body. neighbors must include id, name, type, similarity,
    and ideally description+content (truncated)."""
    cand_lines = [
        "CANDIDATE",
        f"name: {candidate.get('name', '')}",
        f"type: {candidate.get('type', '')}",
        f"tags: {', '.join(candidate.get('tags', []) or [])}",
    ]
    if candidate.get("description"):
        cand_lines.append(f"description: {candidate['description']}")
    if candidate.get("content"):
        cand_lines.append(f"content: {_truncate(candidate['content'])}")

    nbr_blocks = []
    for n in neighbors:
        block = [
            f"- id: {n.get('id', '')}",
            f"  name: {n.get('name', '')}",
            f"  type: {n.get('type', '')}",
            f"  similarity: {round(float(n.get('similarity', 0)), 3)}",
        ]
        if n.get("description"):
            block.append(f"  description: {n['description']}")
        if n.get("content"):
            block.append(f"  content: {_truncate(n['content'])}")
        nbr_blocks.append("\n".join(block))

    return "\n".join(cand_lines) + "\n\nNEIGHBORS\n" + "\n\n".join(nbr_blocks)


def _parse_response(text: str) -> ClassifierDecision | None:
    """Parse the model's JSON reply. Tolerant of leading/trailing prose."""
    if not text:
        return None
    # Find first { ... last } — model may add stray whitespace.
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

    target = data.get("target_id")
    if target in ("", "null", None):
        target = None
    elif not isinstance(target, str):
        return None

    # UPDATE / DELETE require a target. If model contradicts itself,
    # downgrade to ADD with a confidence penalty so the caller logs it.
    if decision in ("UPDATE", "DELETE") and target is None:
        return ClassifierDecision(
            decision="ADD",
            target_id=None,
            confidence=0.3,
            reasoning=f"classifier said {decision} without target_id; downgraded to ADD",
        )

    try:
        confidence = float(data.get("confidence", 0.5))
    except (TypeError, ValueError):
        confidence = 0.5
    confidence = max(0.0, min(1.0, confidence))

    reasoning = str(data.get("reasoning", "")).strip()[:500]

    return ClassifierDecision(
        decision=decision,
        target_id=target,
        confidence=confidence,
        reasoning=reasoning,
    )


async def classify_write(
    candidate: dict,
    neighbors: list[dict],
    *,
    model: str = CLASSIFIER_MODEL,
    timeout: float = CLASSIFIER_TIMEOUT,
) -> ClassifierDecision | None:
    """Call Haiku to classify the candidate write.

    Returns None on missing API key, network error, timeout, or unparseable
    output. Caller should fall back to plain ADD in that case.
    """
    if not neighbors:
        return None  # nothing to compare against → trivially ADD; skip the call

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        return None

    body = {
        "model": model,
        "max_tokens": MAX_TOKENS,
        "system": SYSTEM_PROMPT,
        "messages": [
            {"role": "user", "content": _build_user_message(candidate, neighbors)}
        ],
    }

    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(
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
    except asyncio.CancelledError:
        raise
    except (httpx.HTTPError, ValueError):
        return None

    # Messages API returns content as a list of blocks; we want the first text block.
    blocks = payload.get("content", [])
    text = ""
    for b in blocks:
        if isinstance(b, dict) and b.get("type") == "text":
            text = b.get("text", "")
            break
    return _parse_response(text)
