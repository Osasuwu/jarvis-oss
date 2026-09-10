"""detect_hallucinations.py — correction-phrase detector for the comms-audit pipeline (#513).

Flags a preceding assistant message as a hallucination-attribution candidate
when a later user message contains a correction phrase (RU/EN — "не говорил",
"это не мои слова", "I never said that", "you're hallucinating", ...) within
a short lookback window. Candidates are for owner labeling (see #514) — this
is the regex-first starter, not a precision claim.

CORRECTION_RE mirrors compress_patterns.py's existing hallucination_attribution
CATEGORIES entry (kept in sync manually, not imported — see #513 PR notes on
why no shared _detector_common module exists between the two detectors).

Output merges into `{DEVICE}_patterns.json` under the "hallucinations" key.
"""

from __future__ import annotations

import json
import re
import socket
import sys
from collections import defaultdict
from pathlib import Path

# ceiling: fixed phrase list, not learned from labeled corrections — a
# correction phrased outside these RU/EN patterns (or a false-positive match,
# e.g. "I never said that" used sarcastically in agreement) is invisible to
# this detector. Upgrade path: once #514 has labeled corrections, mine the
# false-negative set to expand/tune the pattern list against real misses.
CORRECTION_RE = re.compile(
    r"не\s+говорил|галлюцин|приписыва|это\s+сказал\s+я|я\s+говорил\s+не"
    r"|не\s+мои\s+слова|не\s+моя\s+идея|я\s+имел\s+в\s+виду\s+не"
    r"|never\s+said|i\s+never\s+told|you'?re\s+hallucinat|hallucinating"
    r"|that'?s\s+not\s+what\s+i\s+said|i\s+didn'?t\s+say",
    re.I,
)

# ceiling: fixed window, not scaled to conversation density — a correction
# more than 3 messages after the flagged assistant turn (e.g. user re-reads
# a transcript later in a long session) is missed. Upgrade path: once #514
# has labeled data, tune this against the observed correction-to-source gap.
_DEFAULT_LOOKBACK = 3


def snip(s: str, n: int = 100) -> str:
    s = re.sub(r"\s+", " ", s).strip()
    return s[:n] + "…" if len(s) > n else s


def find_hallucination_candidates(
    messages_by_session: dict[str, list[dict]],
    lookback: int = _DEFAULT_LOOKBACK,
) -> list[dict]:
    candidates: list[dict] = []
    for sess_id, msgs in messages_by_session.items():
        for idx, m in enumerate(msgs):
            if m.get("role") != "u":
                continue
            text = m.get("text") or ""
            if not CORRECTION_RE.search(text):
                continue
            floor = max(0, idx - lookback)
            prev_a_idx = next(
                (j for j in range(idx - 1, floor - 1, -1) if msgs[j].get("role") == "a"),
                None,
            )
            if prev_a_idx is None:
                continue
            candidates.append(
                {
                    "session_id": sess_id,
                    "assistant_message_idx": prev_a_idx,
                    "user_message_idx": idx,
                    "assistant_text": snip(msgs[prev_a_idx].get("text") or ""),
                    "correction_text": snip(text),
                }
            )
    return candidates


def merge_into_patterns_json(target: Path, candidates: list[dict]) -> None:
    """Read-modify-write merge into `{DEVICE}_patterns.json` under "hallucinations".

    Duplicate of detect_rule_violations.py's merge helper — see module
    docstring / #513 PR notes for why this is not factored into a shared
    module.
    """
    existing: dict = {}
    if target.exists():
        existing = json.loads(target.read_text(encoding="utf-8"))
    existing["hallucinations"] = candidates
    target.write_text(json.dumps(existing, indent=2, ensure_ascii=False), encoding="utf-8")


def main(src_path: str, out_path: str) -> None:  # pragma: no cover — covered by smoke
    messages_by_session: dict[str, list[dict]] = defaultdict(list)
    with open(src_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            rec = json.loads(line)
            messages_by_session[rec["sess"]].append(rec)

    candidates = find_hallucination_candidates(messages_by_session)
    merge_into_patterns_json(Path(out_path), candidates)
    print(
        f"[detect_hallucinations] {len(candidates)} candidates -> {out_path}",
        file=sys.stderr,
    )


if __name__ == "__main__":  # pragma: no cover
    src = sys.argv[1] if len(sys.argv) > 1 else "comms_extract.jsonl"
    out = sys.argv[2] if len(sys.argv) > 2 else f"{socket.gethostname()}_patterns.json"
    main(src, out)
