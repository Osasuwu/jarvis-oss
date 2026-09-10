"""detect_rule_violations.py — regex/keyword starter detector for the comms-audit pipeline (#513).

Reads `feedback`-type, `always_load`-tagged memories from Supabase, extracts a
keyword set per rule, and flags assistant messages in a comms_extract.jsonl
that match a rule's keywords above a threshold. Candidates are for owner
labeling (see #514) — this is the regex-first starter, not a precision claim.

Output merges into `{DEVICE}_patterns.json` under the "rule_violations" key.
"""

from __future__ import annotations

import json
import os
import re
import socket
import sys
from collections import Counter, defaultdict
from pathlib import Path

_KEYWORD_WORD_RE = re.compile(r"[a-zA-Zа-яА-ЯёЁ]{3,}")

# Common function words that would otherwise dominate frequency counts without
# carrying any rule-specific signal — kept short and generic, not per-rule tuned.
_STOPWORDS = {
    "the",
    "and",
    "use",
    "this",
    "that",
    "with",
    "from",
    "have",
    "never",
    "always",
    "into",
    "than",
    "then",
    "when",
    "what",
    "should",
    "would",
    "could",
    "here",
    "there",
    "which",
    "your",
    "keeps",
    "pattern",
    "across",
    "sessions",
    "instead",
    "explicit",
    "это",
    "того",
    "если",
    "когда",
    "которые",
    "также",
}


def snip(s: str, n: int = 100) -> str:
    s = re.sub(r"\s+", " ", s).strip()
    return s[:n] + "…" if len(s) > n else s


def extract_keywords(content: str, top_n: int = 8) -> list[str]:
    """Frequency-ranked keyword set from a rule's memory content.

    Single-document term frequency (no corpus for real TF-IDF) — the simple
    top-N frequency ranking the issue's AC explicitly allows.
    # ceiling: single-doc frequency has no cross-rule specificity weighting;
    # short/generic memory content yields low-discriminative keywords (see
    # #513 PR smoke run — 860 false-positive candidates on one rule). Upgrade
    # path: real corpus-wide TF-IDF once #514 has enough labeled rules to
    # form a corpus.
    """
    words = [w.lower() for w in _KEYWORD_WORD_RE.findall(content)]
    counts = Counter(w for w in words if w not in _STOPWORDS)
    return [w for w, _ in counts.most_common(top_n)]


def load_rules_from_memories(memories: list[dict]) -> list[dict]:
    """Turn feedback-memory rows into {name, keywords} rules for scan_messages."""
    return [
        {"name": m["name"], "keywords": extract_keywords(m.get("content") or "")} for m in memories
    ]


def merge_into_patterns_json(target: Path, candidates: list[dict]) -> None:
    """Read-modify-write merge into `{DEVICE}_patterns.json` under "rule_violations".

    compress_patterns.py fully overwrites this file, so this detector (which
    runs after it in the Phase A pipeline) must merge rather than clobber the
    other top-level keys compress_patterns.py already wrote.
    """
    existing: dict = {}
    if target.exists():
        existing = json.loads(target.read_text(encoding="utf-8"))
    existing["rule_violations"] = candidates
    target.write_text(json.dumps(existing, indent=2, ensure_ascii=False), encoding="utf-8")


def scan_messages(
    messages_by_session: dict[str, list[dict]],
    rules: list[dict],
    # ceiling: hardcoded absolute-match floor, not scaled to len(keywords) —
    # a rule with 2 keywords needs both to match, one with 8 needs only 2/8.
    # Upgrade path: scale to a min_matches/len(keywords) ratio once #514's
    # labeled data shows where the current floor over/under-fires.
    min_matches: int = 2,
) -> list[dict]:
    candidates: list[dict] = []
    for sess_id, msgs in messages_by_session.items():
        for idx, m in enumerate(msgs):
            if m.get("role") != "a":
                continue
            text = m.get("text") or ""
            text_lower = text.lower()
            for rule in rules:
                keywords = rule["keywords"]
                if not keywords:
                    continue
                matched = [kw for kw in keywords if kw in text_lower]
                if len(matched) >= min_matches:
                    candidates.append(
                        {
                            "session_id": sess_id,
                            "message_idx": idx,
                            "rule_name": rule["name"],
                            "matched_text": snip(text),
                            "confidence": round(len(matched) / len(keywords), 2),
                        }
                    )
    return candidates


def _get_supabase_client():  # pragma: no cover — covered by smoke
    """Lazy import so unit tests don't pay supabase startup cost."""
    _root = Path(__file__).resolve().parent.parent.parent
    try:
        from dotenv import load_dotenv

        for _env in [_root / ".env", _root.parent / ".env"]:
            if _env.exists():
                load_dotenv(_env, override=True)
                break
    except Exception:
        pass

    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_KEY")
    if not url or not key:
        raise RuntimeError("SUPABASE_URL / SUPABASE_KEY not set")
    from supabase import create_client

    return create_client(url, key)


def fetch_always_load_feedback_memories(
    client,
) -> list[dict]:  # pragma: no cover — covered by smoke
    """`feedback`-type, `always_load`-tagged memory rows — the rule source."""
    resp = (
        client.table("memories")
        .select("id, name, content")
        .eq("type", "feedback")
        .contains("tags", ["always_load"])
        .is_("deleted_at", "null")
        .execute()
    )
    return resp.data or []


def main(src_path: str, out_path: str) -> None:  # pragma: no cover — covered by smoke
    try:
        client = _get_supabase_client()
    except RuntimeError as e:
        print(f"[detect_rule_violations] skipping — {e}", file=sys.stderr)
        return
    rules = load_rules_from_memories(fetch_always_load_feedback_memories(client))

    messages_by_session: dict[str, list[dict]] = defaultdict(list)
    with open(src_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            rec = json.loads(line)
            messages_by_session[rec["sess"]].append(rec)

    candidates = scan_messages(messages_by_session, rules)
    merge_into_patterns_json(Path(out_path), candidates)
    print(
        f"[detect_rule_violations] {len(candidates)} candidates -> {out_path}",
        file=sys.stderr,
    )


if __name__ == "__main__":  # pragma: no cover
    src = sys.argv[1] if len(sys.argv) > 1 else "comms_extract.jsonl"
    out = sys.argv[2] if len(sys.argv) > 2 else f"{socket.gethostname()}_patterns.json"
    main(src, out)
