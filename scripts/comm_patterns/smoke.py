"""Local E2E smoke — pick a recent interactive transcript, run the
extractor end-to-end with the live Ollama classifier and the in-memory
store, print the rows. No Supabase writes.

Useful for:
  * verifying Ollama + qwen3 + JSON parsing line up on this device
  * sanity-checking the classifier output shape on real data
  * confirming idempotency on a real transcript

Run:
    .venv/Scripts/python.exe -m comm_patterns.smoke <session-jsonl>

Prefer the latest interactive jarvis transcript by default.
"""

from __future__ import annotations

import socket
import sys
from pathlib import Path

from .classifier import call_ollama
from .extractor import extract_session
from .store import InMemoryStore

PROJECTS_ROOT = Path.home() / ".claude" / "projects"


def _pick_default_transcript() -> Path | None:
    """Pick the most-recent jsonl across all CCD project dirs on this device.

    The directory slug differs per device (Windows vs Linux mangle the
    cwd path differently), so glob across all of them by mtime — never
    hardcode a single device's slug here.
    """
    if not PROJECTS_ROOT.exists():
        return None
    files = sorted(
        PROJECTS_ROOT.glob("*/*.jsonl"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    # Skip the live one (current session) — use the one before.
    return files[1] if len(files) > 1 else (files[0] if files else None)


def main() -> int:
    transcript = (
        Path(sys.argv[1]) if len(sys.argv) > 1 else _pick_default_transcript()
    )
    if not transcript or not transcript.exists():
        print("usage: smoke.py <transcript.jsonl>", file=sys.stderr)
        return 2

    store = InMemoryStore()
    common = dict(
        device=socket.gethostname(),
        session_id=transcript.stem,
        transcript_path=transcript,
        cwd=str(Path.cwd()),
        store=store,
        classify_fn=call_ollama,
        source_provenance="smoke:local",
    )
    print(f"transcript: {transcript}")
    print("--- pass 1 ---")
    stats1 = extract_session(**common)
    print(stats1)
    if stats1.get("connection_errors"):
        print(
            "Ollama unavailable — classifier could not run. "
            "Start Ollama (`ollama serve`) and re-run this smoke.",
            file=sys.stderr,
        )
        return 3
    print(f"rows in store: {len(store.rows)}")
    for r in store.rows[:8]:
        print(
            f"  idx={r['message_idx']} label={r['primary_label']} "
            f"conf={r['confidence']} redacted={r['redacted']} "
            f"sub={r['subtype']} anchor={r['anchor_quote'][:80]!r}"
        )

    print("--- pass 2 (idempotency) ---")
    stats2 = extract_session(**common)
    print(stats2)
    assert stats2["rows_written"] == 0, "second pass should be a no-op"
    print(f"rows in store after re-run: {len(store.rows)}  (expected: same as pass 1)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
