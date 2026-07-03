"""One-time backfill: classify ~/.cache/jarvis-comms-analysis/* and write rows.

The cache holds previously-extracted (trigger, correction) pairs from the
old regex-driven /reflect pipeline. We re-classify each example through
the new Ollama classifier and write rows with
``source_provenance="backfill:reflect"``.

The cache files store *examples per category* — we don't have a real
session_id / message_idx for them. Synthesise:
  * session_id = "backfill:" + sha1(file_path + category + index)[:16]
  * message_idx = ordinal within the file (0, 1, 2, ...)
  * captured_at = first day of date_range from the file (best available)

These rows live in the same table; downstream readers can filter by
``source_provenance`` if they want only live extraction data.

Idempotent: the unique index (device, session_id, message_idx) is the
deduplication mechanism. Re-running on the same cache produces zero new
rows. The cache files can be left in place after backfill (per #581 AC).

Usage:
  .venv/Scripts/python.exe scripts/comm-patterns-backfill.py
  .venv/Scripts/python.exe scripts/comm-patterns-backfill.py --dry-run
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import socket
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# venv re-exec (mirrors the stop-hook entry).
# ---------------------------------------------------------------------------
_root = Path(__file__).resolve().parent.parent
_venv_py = _root / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")

if (
    __name__ == "__main__"
    and _venv_py.exists()
    and Path(sys.executable).resolve() != _venv_py.resolve()
):
    sys.exit(subprocess.call([str(_venv_py), str(Path(__file__).resolve()), *sys.argv[1:]]))

sys.path.insert(0, str(_root / "scripts"))

try:
    from dotenv import load_dotenv

    for _env in [_root / ".env", _root.parent / ".env"]:
        if _env.exists():
            load_dotenv(_env, override=True)
            break
except Exception:
    pass

from comm_patterns.classifier import call_ollama, OllamaUnavailable  # noqa: E402
from comm_patterns.extractor import CONFIDENCE_THRESHOLD  # noqa: E402
from comm_patterns.scrubber import scrub  # noqa: E402
from comm_patterns.store import InMemoryStore, SupabaseStore  # noqa: E402

CACHE_ROOT = Path.home() / ".cache" / "jarvis-comms-analysis"


def _synth_session_id(file_path: Path, category: str, idx: int) -> str:
    raw = f"{file_path.as_posix()}|{category}|{idx}".encode("utf-8")
    return "backfill:" + hashlib.sha1(raw).hexdigest()[:16]


def _captured_at_from_file(payload: dict) -> str:
    rng = payload.get("date_range") or []
    if rng and rng[0]:
        # Validate before formatting — older cache files have free-form
        # strings or null in slot 0; without parsing, an f-string would
        # silently emit "NoneT00:00:00+00:00" into Supabase.
        try:
            datetime.fromisoformat(str(rng[0]))
            return f"{rng[0]}T00:00:00+00:00"
        except (TypeError, ValueError):
            pass
    return datetime.now(timezone.utc).isoformat()


def _iter_examples(payload: dict) -> list[tuple[str, dict]]:
    """Yield (synthetic_anchor, example_dict) pairs across correctives + affirmatives."""
    out: list[tuple[str, dict]] = []
    correctives = payload.get("correctives") or {}
    for cat, cdata in correctives.items():
        for example in cdata.get("examples", []) or []:
            out.append((cat, example))
    aff = payload.get("affirmatives") or {}
    for example in aff.get("examples", []) or []:
        out.append(("affirmative", example))
    return out


def _example_to_user_text(example: dict) -> tuple[str, str]:
    """Return (user_text, prev_assistant_text)."""
    user_text = example.get("correction") or example.get("snippet") or ""
    prev = example.get("trigger") or ""
    return user_text, prev


def _row_for_example(
    *,
    device: str,
    file_path: Path,
    payload: dict,
    cat: str,
    idx: int,
    classified: dict[str, Any],
    user_text: str,
) -> dict[str, Any]:
    anchor_raw = classified.get("anchor_quote") or user_text[:600]
    anchor_scrubbed, redacted = scrub(anchor_raw)
    return {
        "device": device,
        "session_id": _synth_session_id(file_path, cat, idx),
        "message_idx": idx,
        "captured_at": _captured_at_from_file(payload),
        "primary_label": classified["primary_label"],
        "subtype": classified.get("subtype"),
        "confidence": classified["confidence"],
        "anchor_quote": anchor_scrubbed,
        "redacted": redacted,
        "embedding": None,
        "source_provenance": "backfill:reflect",
    }


def run(
    *,
    dry_run: bool,
    cache_root: Path = CACHE_ROOT,
    max_examples: int | None = None,
) -> dict[str, int]:
    files = sorted(cache_root.glob("*/*_patterns.json")) if cache_root.exists() else []
    stats = {
        "files_seen": len(files),
        "examples_seen": 0,
        "rows_written": 0,
        "no_pattern": 0,
        "low_confidence": 0,
        "classifier_errors": 0,
        "connection_errors": 0,
    }
    if not files:
        print(f"[backfill] no cache files at {cache_root}")
        return stats

    # SupabaseStore() raises RuntimeError on missing env. Surface it once
    # at the start so failures aren't tangled into the per-file loop.
    if dry_run:
        store = InMemoryStore()
    else:
        try:
            store = SupabaseStore()
        except RuntimeError as e:
            print(f"[backfill] cannot init Supabase: {e}", file=sys.stderr)
            return stats
    device = socket.gethostname()
    examples_processed = 0
    # Circuit-breaker: Ollama being unreachable is a host-wide condition, not a
    # per-example one. Once we see the first OllamaUnavailable we stop the whole
    # run instead of hammering every remaining example with the same dead socket.
    ollama_down = False

    for fp in files:
        if ollama_down:
            break
        try:
            payload = json.loads(fp.read_text(encoding="utf-8"))
        except Exception as e:
            print(f"[backfill] skip {fp}: {e}", file=sys.stderr)
            continue
        # The file's own device key is more authoritative than the host
        # running the backfill — these patterns came from somewhere else.
        device_for_file = payload.get("device") or device
        examples = _iter_examples(payload)
        stats["examples_seen"] += len(examples)
        for idx, (cat, example) in enumerate(examples):
            if max_examples is not None and examples_processed >= max_examples:
                break
            user_text, prev = _example_to_user_text(example)
            if not user_text:
                continue
            examples_processed += 1
            try:
                classified = call_ollama(user_text, prev)
            except OllamaUnavailable:
                # Host-wide condition — abort the run. The per-item line would
                # duplicate the summary WARNING below for zero diagnostic gain,
                # so we only count here and let the summary report it once.
                stats["connection_errors"] += 1
                ollama_down = True
                break
            except Exception as e:
                stats["classifier_errors"] += 1
                print(f"[backfill] classifier error on {fp}#{idx}: {type(e).__name__}", file=sys.stderr)
                continue
            if not classified or classified.get("primary_label") is None:
                stats["no_pattern"] += 1
                continue
            if float(classified.get("confidence", 0.0)) < CONFIDENCE_THRESHOLD:
                stats["low_confidence"] += 1
                continue
            row = _row_for_example(
                device=device_for_file,
                file_path=fp,
                payload=payload,
                cat=cat,
                idx=idx,
                classified=classified,
                user_text=user_text,
            )
            store.insert_row(row)
            stats["rows_written"] += 1

    if dry_run and isinstance(store, InMemoryStore):
        print(f"[backfill] DRY RUN — would write {len(store.rows)} rows")
    print(f"[backfill] {stats}")
    if stats["connection_errors"] > 0:
        print(
            f"[backfill] WARNING: Ollama unavailable — run aborted after first connection failure. "
            f"Re-run after starting Ollama; results in this run are partial.",
            file=sys.stderr,
        )
    return stats


def main() -> int:
    ap = argparse.ArgumentParser(description="Backfill comm_patterns from ~/.cache/jarvis-comms-analysis")
    ap.add_argument("--dry-run", action="store_true", help="Don't hit Supabase; print plan only.")
    ap.add_argument(
        "--cache-root",
        type=Path,
        default=CACHE_ROOT,
        help=f"Cache root (default: {CACHE_ROOT})",
    )
    ap.add_argument(
        "--max-examples",
        type=int,
        default=None,
        help="Stop after N examples (across all files). Useful for incremental runs.",
    )
    args = ap.parse_args()
    stats = run(dry_run=args.dry_run, cache_root=args.cache_root, max_examples=args.max_examples)
    if stats.get("connection_errors", 0) > 0:
        return 3  # partial run — matches smoke scripts exit-code convention
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
