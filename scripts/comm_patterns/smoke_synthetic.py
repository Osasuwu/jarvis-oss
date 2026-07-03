"""Synthetic E2E smoke — minimal transcript exercising the full pipeline.

Real transcripts hit ~50+ user turns × ~15s/qwen3-call = >10 min, too long
for an in-band smoke. This builds a 3-turn fixture that hits each branch:
correction, affirmation, no-pattern — and runs the live classifier on each.

Run:
    .venv/Scripts/python.exe -m comm_patterns.smoke_synthetic
"""

from __future__ import annotations

import json
import socket
import sys
import tempfile
from pathlib import Path

from .classifier import call_ollama
from .extractor import extract_session
from .store import InMemoryStore


def _build_transcript(tmp: Path) -> Path:
    fp = tmp / "synthetic.jsonl"
    rows = [
        {"type": "assistant", "timestamp": "2026-05-10T12:00:00Z",
         "message": {"content": [{"type": "text", "text": "I closed issue #200 since #199 covers it."}]}},
        {"type": "user", "timestamp": "2026-05-10T12:00:01Z",
         "message": {"content": "нет, не закрывай — там разные acceptance criteria, проверь #200 ещё раз."}},
        {"type": "assistant", "timestamp": "2026-05-10T12:00:02Z",
         "message": {"content": [{"type": "text", "text": "Reopened. Updated AC mapping."}]}},
        {"type": "user", "timestamp": "2026-05-10T12:00:03Z",
         "message": {"content": "правильно, спасибо."}},
        {"type": "assistant", "timestamp": "2026-05-10T12:00:04Z",
         "message": {"content": [{"type": "text", "text": "Tests pass. Want me to open the PR?"}]}},
        {"type": "user", "timestamp": "2026-05-10T12:00:05Z",
         "message": {"content": "да, открой."}},
    ]
    with fp.open("w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")
    return fp


def main() -> int:
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        fp = _build_transcript(tmp)
        store = InMemoryStore()
        common = dict(
            device=socket.gethostname(),
            session_id="synthetic-smoke",
            transcript_path=fp,
            cwd=str(Path.cwd()),
            store=store,
            classify_fn=call_ollama,
            source_provenance="smoke:synthetic",
        )
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
        for r in store.rows:
            print(
                f"  idx={r['message_idx']} label={r['primary_label']} "
                f"sub={r['subtype']} conf={r['confidence']} "
                f"redacted={r['redacted']} anchor={r['anchor_quote'][:80]!r}"
            )
        rows_after_pass1 = list(store.rows)

        print("--- pass 2 (idempotency) ---")
        stats2 = extract_session(**common)
        print(stats2)
        if stats2["rows_written"] != 0:
            print("FAIL: pass 2 wrote rows; expected 0", file=sys.stderr)
            return 1
        if len(store.rows) != len(rows_after_pass1):
            print("FAIL: row count grew on re-run", file=sys.stderr)
            return 1

    print("OK: pipeline works end-to-end and is idempotent.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
