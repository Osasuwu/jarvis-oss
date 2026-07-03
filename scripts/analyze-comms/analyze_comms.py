"""Mechanical pass over extracted comms — cheap aggregate signals (no quotes)."""
from __future__ import annotations
import json, re, sys
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

NEG_PATTERNS = [
    r"\bне\s+так\b", r"\bне\s+то\b", r"\bстоп\b", r"\bнет,?\s", r"\bпочему\s+ты\b",
    r"\bопять\b", r"\bя\s+же\s+(говорил|сказал|просил)\b", r"\bты\s+не\s+поня",
    r"\bперестань\b", r"\bхватит\b",
    r"\bno,?\s", r"\bstop\b", r"\bdon't\b", r"\bwhy\s+are\s+you\b",
    r"\bagain\b", r"\bnot\s+what\s+i\b",
]
NEG_RE = re.compile("|".join(NEG_PATTERNS), re.I)
POS_PATTERNS = [
    r"\bотлично\b", r"\bкруто\b", r"\bтак\s+и\s+делай\b", r"\bправильно\b",
    r"\bда,?\s+именно\b", r"\bперфект", r"\bхорошо\b",
    r"\bperfect\b", r"\bexactly\b", r"\bnice\b", r"\bgreat\b",
]
POS_RE = re.compile("|".join(POS_PATTERNS), re.I)
CYRILLIC_RE = re.compile(r"[а-яё]", re.I)
LATIN_RE = re.compile(r"[a-z]", re.I)


def main(src_path: str):
    src = Path(src_path)
    recs = [json.loads(l) for l in open(src, encoding="utf-8")]
    by_sess = defaultdict(list)
    for r in recs:
        by_sess[r["sess"]].append(r)
    real = {s: msgs for s, msgs in by_sess.items() if sum(1 for m in msgs if m["role"] == "u") >= 3}
    user_msgs = [m for s in real.values() for m in s if m["role"] == "u"]
    asst_msgs = [m for s in real.values() for m in s if m["role"] == "a"]
    if not user_msgs:
        print("no interactive sessions found")
        return

    print(f"=== SCOPE ===")
    print(f"interactive sessions: {len(real)}")
    print(f"user msgs: {len(user_msgs)}  asst msgs: {len(asst_msgs)}")

    print(f"\n=== USER MSG LENGTH ===")
    lens = sorted(m["len"] for m in user_msgs)
    n = len(lens)
    pct = lambda p: lens[int(n * p)]
    print(f"p10={pct(0.1)}  p25={pct(0.25)}  p50={pct(0.5)}  p75={pct(0.75)}  p90={pct(0.9)}  p99={pct(0.99)}")
    short = sum(1 for l in lens if l < 100)
    long_ = sum(1 for l in lens if l > 2000)
    print(f"<100 chars: {short}/{n} = {100*short/n:.0f}%   >2000 chars: {long_}/{n} = {100*long_/n:.0f}%")

    ru = en = mixed = 0
    for m in user_msgs:
        c = bool(CYRILLIC_RE.search(m["text"]))
        l = bool(LATIN_RE.search(m["text"]))
        if c and l: mixed += 1
        elif c: ru += 1
        elif l: en += 1
    print(f"\n=== LANGUAGE ===  RU={ru}  EN={en}  mixed={mixed}")

    neg = sum(1 for m in user_msgs if NEG_RE.search(m["text"]))
    pos = sum(1 for m in user_msgs if POS_RE.search(m["text"]))
    print(f"\n=== TONE ===  corrective={neg} ({100*neg/n:.1f}%)  affirmative={pos} ({100*pos/n:.1f}%)")

    print(f"\n=== TIME OF DAY (UTC) ===")
    hours = Counter()
    for m in user_msgs:
        try:
            hours[datetime.fromisoformat(m["ts"].replace("Z", "+00:00")).hour] += 1
        except Exception:
            pass
    if hours:
        mx = max(hours.values())
        for h in sorted(hours):
            print(f"  {h:02d}: {hours[h]:3d}  {'#' * (hours[h] * 40 // mx)}")

    print(f"\n=== SESSION ECONOMICS (top 10) ===")
    rows = []
    for s, msgs in real.items():
        u = [m for m in msgs if m["role"] == "u"]
        a = [m for m in msgs if m["role"] == "a"]
        if not u: continue
        rows.append((len(u), s, len(u), len(a),
                     sum(m["len"] for m in u) / len(u),
                     sum(m["len"] for m in a) / max(1, len(a))))
    rows.sort(reverse=True)
    print(f"  {'sess':<10} {'u#':>4} {'a#':>4} {'avg_u':>6} {'avg_a':>6} {'a/u':>6}")
    for _, s, nu, na, au, aa in rows[:10]:
        print(f"  {s[:10]:<10} {nu:>4} {na:>4} {au:>6.0f} {aa:>6.0f} {aa/au:>6.2f}")


if __name__ == "__main__":
    src = sys.argv[1] if len(sys.argv) > 1 else "comms_extract.jsonl"
    main(src)
