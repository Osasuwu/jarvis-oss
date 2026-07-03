"""Voyage AI embedding helpers + canonical embed-text builder.

(#360 split.) Pure I/O around the Voyage REST API, plus the
_canonical_embed_text helper that produces the structured text fed
into the embedder. Read by both server.py and handlers/memory.py.
"""

from __future__ import annotations

import asyncio
import os

import httpx

VOYAGE_API_URL = "https://api.voyageai.com/v1/embeddings"
VOYAGE_MODEL = "voyage-3-lite"
EMBED_TIMEOUT = 30.0  # seconds

# #242 dual-embedding machinery. PRIMARY drives reads (which RPC is called
# + what model embeds the query). SECONDARY, if set, enables dual-write so
# the v2 column fills up in parallel without touching the read path.
# When SECONDARY is unset, behavior is bit-identical to pre-#242.
EMBEDDING_MODEL_PRIMARY = os.environ.get("EMBEDDING_MODEL_PRIMARY", VOYAGE_MODEL)
EMBEDDING_MODEL_SECONDARY = os.environ.get("EMBEDDING_MODEL_SECONDARY") or None

# Model → (column, RPC, version-tag) mapping. Extend here when adding a
# new supported model. Keep the table read-only at runtime.
EMBEDDING_MODELS = {
    "voyage-3-lite": {
        "embedding_column": "embedding",
        "model_column": "embedding_model",
        "version_column": "embedding_version",
        "rpc": "match_memories",
        "version_tag": "v2",  # Phase 2a canonical form
    },
    "voyage-3": {
        "embedding_column": "embedding_v2",
        "model_column": "embedding_model_v2",
        "version_column": "embedding_version_v2",
        "rpc": "match_memories_v2",
        "version_tag": "v2",
    },
}


def _model_slot(model: str) -> dict:
    """Look up the column/RPC slot for a model. Falls back to PRIMARY for
    unknown models so misconfiguration never crashes startup — it just
    degrades to legacy behavior."""
    return EMBEDDING_MODELS.get(model) or EMBEDDING_MODELS[VOYAGE_MODEL]


async def _embed(
    text: str, input_type: str = "document", model: str | None = None
) -> list[float] | None:
    """Call Voyage AI REST API asynchronously. Retries up to 3x on 429."""
    api_key = os.environ.get("VOYAGE_API_KEY")
    if not api_key:
        return None
    use_model = model or VOYAGE_MODEL
    for attempt in range(3):
        try:
            async with httpx.AsyncClient(timeout=EMBED_TIMEOUT) as client:
                resp = await client.post(
                    VOYAGE_API_URL,
                    headers={"Authorization": f"Bearer {api_key}"},
                    json={"model": use_model, "input": [text], "input_type": input_type},
                )
                resp.raise_for_status()
                return resp.json()["data"][0]["embedding"]
        except asyncio.CancelledError:
            raise
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 429 and attempt < 2:
                await asyncio.sleep(2**attempt)
                continue
            return None
        except (httpx.HTTPError, KeyError, IndexError, TypeError, ValueError):
            return None
    return None


async def _embed_batch(
    texts: list[str], input_type: str = "document", model: str | None = None
) -> list[list[float]] | None:
    """Embed multiple texts in a single API call (up to 1000 per request)."""
    api_key = os.environ.get("VOYAGE_API_KEY")
    if not api_key or not texts:
        return None
    use_model = model or VOYAGE_MODEL
    for attempt in range(3):
        try:
            async with httpx.AsyncClient(timeout=EMBED_TIMEOUT) as client:
                resp = await client.post(
                    VOYAGE_API_URL,
                    headers={"Authorization": f"Bearer {api_key}"},
                    json={"model": use_model, "input": texts, "input_type": input_type},
                )
                resp.raise_for_status()
                data = sorted(resp.json()["data"], key=lambda x: x["index"])
                return [item["embedding"] for item in data]
        except asyncio.CancelledError:
            raise
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 429 and attempt < 2:
                await asyncio.sleep(2**attempt)
                continue
            return None
        except (httpx.HTTPError, KeyError, IndexError, TypeError, ValueError):
            return None
    return None


def _embed_upsert_fields(embedding: list[float], model: str) -> dict:
    """Build the dict of columns to upsert for a (embedding, model) pair.
    Returns {} if model is unknown (shouldn't happen at write time; silently
    degrades so we never corrupt rows)."""
    slot = EMBEDDING_MODELS.get(model)
    if not slot:
        return {}
    return {
        slot["embedding_column"]: embedding,
        slot["model_column"]: model,
        slot["version_column"]: slot["version_tag"],
    }


async def _embed_query(text: str) -> list[float] | None:
    # #242: read path embeds with PRIMARY so the vector matches whichever
    # column we're about to query via _hybrid_recall's RPC selection.
    return await _embed(text, input_type="query", model=EMBEDDING_MODEL_PRIMARY)


def _canonical_embed_text(name: str, description: str, tags: list[str], content: str) -> str:
    """Build the text used for embedding. Structured so name/tags get weight.

    Why: a long-form memory whose key topic is in the name but whose content
    drifts into narrative detail embeds poorly — name/tags get drowned out.
    Prefixing them in a separate line gives them comparable weight under the
    tokenizer.
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
