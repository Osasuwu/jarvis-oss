"""Re-embed all memories with the Phase 2a canonical form.

Reads name + description + tags + content from every non-deleted memory,
batches through Voyage AI (voyage-3-lite), writes back embedding +
embedding_model='voyage-3-lite' + embedding_version='v2'.

Run once after Phase 2a code change. Idempotent: re-running just re-embeds.

Usage:
    python scripts/reembed-canonical.py                 # dry run: print what would change
    python scripts/reembed-canonical.py --apply         # actually write

Requires VOYAGE_API_KEY, SUPABASE_URL, SUPABASE_KEY.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

# Load .env
try:
    from dotenv import load_dotenv
    here = Path(__file__).resolve().parent
    for c in (here.parent / ".env", here.parent.parent / ".env"):
        if c.exists():
            load_dotenv(c)
            break
except ImportError:
    pass

import httpx
from supabase import create_client

VOYAGE_URL = "https://api.voyageai.com/v1/embeddings"
VOYAGE_MODEL = "voyage-3-lite"
BATCH = 64  # Voyage accepts up to 1000 but smaller batches are safer on retries


def canonical_text(name: str, description: str | None, tags: list[str] | None, content: str) -> str:
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


async def embed_batch(texts: list[str]) -> list[list[float]]:
    api_key = os.environ["VOYAGE_API_KEY"]
    async with httpx.AsyncClient(timeout=60.0) as c:
        resp = await c.post(
            VOYAGE_URL,
            headers={"Authorization": f"Bearer {api_key}"},
            json={"model": VOYAGE_MODEL, "input": texts, "input_type": "document"},
        )
        resp.raise_for_status()
        data = sorted(resp.json()["data"], key=lambda x: x["index"])
        return [item["embedding"] for item in data]


async def main(apply: bool) -> int:
    client = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_KEY"])

    rows = (
        client.table("memories")
        .select("id, name, description, tags, content")
        .is_("deleted_at", "null")
        .execute()
        .data
    )
    total = len(rows)
    print(f"Found {total} live memories to re-embed")

    if not apply:
        for r in rows[:3]:
            text = canonical_text(r.get("name", ""), r.get("description"), r.get("tags"), r.get("content", ""))
            print(f"\n[dry] {r['name']}: {text[:120]}{'...' if len(text) > 120 else ''}")
        print(f"\nDry run — pass --apply to write {total} updated embeddings.")
        return 0

    done = 0
    for i in range(0, total, BATCH):
        batch = rows[i : i + BATCH]
        texts = [
            canonical_text(r.get("name", ""), r.get("description"), r.get("tags"), r.get("content", ""))
            for r in batch
        ]
        embeddings = await embed_batch(texts)
        for r, emb in zip(batch, embeddings):
            client.table("memories").update({
                "embedding": emb,
                "embedding_model": VOYAGE_MODEL,
                "embedding_version": "v2",
            }).eq("id", r["id"]).execute()
        done += len(batch)
        print(f"  {done}/{total}")

    print(f"Done — {done} memories re-embedded with canonical form.")
    return 0


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--apply", action="store_true", help="Actually write (default: dry run)")
    args = p.parse_args()
    sys.exit(asyncio.run(main(apply=args.apply)))
