"""compress_patterns.py — per-device pattern compression for cross-device sync.

Reads comms_extract.jsonl → {DEVICE}_patterns.json (aggregate, <20KB).
Preserves actual text snippets (NOT paraphrased), truncated to 100 chars.
Safe to upload to GDrive via MCP (base64 fits in Read tool limit).
"""
from __future__ import annotations
import json, re, socket, sys, random
from collections import defaultdict
from pathlib import Path

NEG_RE = re.compile(
    r"\bне\s+так\b|\bне\s+то\b|\bстоп\b|\bнет,?\s|\bпочему\s+ты\b"
    r"|\bопять\b|\bя\s+же\s+(говорил|сказал|просил)\b|\bты\s+не\s+поня"
    r"|\bперестань\b|\bхватит\b"
    r"|\bno,?\s|\bstop\b|\bdon't\b|\bwhy\s+are\s+you\b|\bagain\b|\bnot\s+what\s+i\b",
    re.I,
)
POS_RE = re.compile(
    r"\bотлично\b|\bкруто\b|\bтак\s+и\s+делай\b|\bправильно\b"
    r"|\bда,?\s+именно\b|\bперфект|\bхорошо\b"
    r"|\bperfect\b|\bexactly\b|\bnice\b|\bgreat\b",
    re.I,
)
CYRILLIC_RE = re.compile(r"[а-яё]", re.I)
LATIN_RE = re.compile(r"[a-z]", re.I)

# Corrective category patterns — order matters (first match wins)
CATEGORIES = [
    ("permission_seeking", re.compile(
        r"хочешь.*сделаю|хочешь,?\s*я|можно\s+я|подтверд|разреш"
        r"|approve\b|confirm\b|shall\s+i\b|should\s+i\b|want\s+me\s+to\b"
        r"|можешь\s+ли|могу\s+ли\s+я",
        re.I,
    )),
    ("tunnel_vision", re.compile(
        r"узк|tunnel\s+vision|не\s+только\s+про|шире\s+смотр|масштаб"
        r"|frontend.*не\s+подключ|backend.*не\s+подключ|половин.*работ"
        r"|не\s+доделал|не\s+довел",
        re.I,
    )),
    ("hallucination_attribution", re.compile(
        r"не\s+говорил|галлюцин|приписыва|это\s+сказал\s+я|я\s+говорил\s+не"
        r"|не\s+мои\s+слова|не\s+моя\s+идея|я\s+имел\s+в\s+виду\s+не",
        re.I,
    )),
    ("repeat_mistake", re.compile(
        r"опять\s+то\s+же|снова\s+та\s+же|в\s+прошлый\s+раз.*так\s+же"
        r"|уже\s+говорил.*нельзя|already\s+told|same\s+mistake|again\s+and\s+again"
        r"|ты\s+же\s+так\s+в\s+прошлый",
        re.I,
    )),
    ("cross_device_miss", re.compile(
        r"\bwindows\b|\bустройств|\bдокер\s+нет\b|\bdocker\s+нет\b"
        r"|\.sh\s+файл|\bhardcod|\bабсолютн.*path\b|\blinux\s+команд"
        r"|на\s+этом\s+устройств|не\s+работает\s+на\s+windows",
        re.I,
    )),
]


def snip(s: str, n: int = 100) -> str:
    s = re.sub(r"\s+", " ", s).strip()
    return s[:n] + "…" if len(s) > n else s


def infer_category(trigger: str, correction: str) -> str:
    combined = (trigger or "") + " " + correction
    for cat, pat in CATEGORIES:
        if pat.search(combined):
            return cat
    return "other"


def main(src_path: str, out_path: str) -> None:
    src = Path(src_path)
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)

    recs = [json.loads(line) for line in src.open(encoding="utf-8") if line.strip()]
    by_sess = defaultdict(list)
    for r in recs:
        by_sess[r["sess"]].append(r)
    interactive = {
        s: msgs for s, msgs in by_sess.items()
        if sum(1 for m in msgs if m["role"] == "u") >= 3
    }

    device = socket.gethostname()
    dates = []
    corrective_cats: dict[str, dict] = {
        cat: {"n_sessions": 0, "raw": []} for cat, _ in CATEGORIES
    }
    corrective_cats["other"] = {"n_sessions": 0, "raw": []}

    affirmative_raw: list[dict] = []
    style_lens: list[int] = []
    style_samples: list[str] = []
    ru = en = mixed = 0

    for sess_id, msgs in interactive.items():
        msgs.sort(key=lambda m: m["ts"])
        ts_list = [m["ts"] for m in msgs if m["ts"]]
        if ts_list:
            dates.append(ts_list[0][:10])

        sess_cats: set[str] = set()
        has_affirmative = False

        for i, m in enumerate(msgs):
            if m["role"] != "u":
                continue

            c = bool(CYRILLIC_RE.search(m["text"]))
            la = bool(LATIN_RE.search(m["text"]))
            if c and la:
                mixed += 1
            elif c:
                ru += 1
            elif la:
                en += 1

            style_lens.append(m["len"])
            if m["len"] < 200 and len(style_samples) < 20:
                style_samples.append(snip(m["text"], 150))

            prev_a = next(
                (msgs[j] for j in range(i - 1, -1, -1) if msgs[j]["role"] == "a"),
                None,
            )
            trigger_text = prev_a["text"] if prev_a else ""
            correction_text = m["text"]

            if NEG_RE.search(correction_text):
                cat = infer_category(trigger_text, correction_text)
                corrective_cats[cat]["raw"].append({
                    "trigger": snip(trigger_text),
                    "correction": snip(correction_text),
                })
                sess_cats.add(cat)
            elif POS_RE.search(correction_text):
                affirmative_raw.append({
                    "trigger": snip(trigger_text),
                    "snippet": snip(correction_text),
                })
                has_affirmative = True

        for cat in sess_cats:
            corrective_cats[cat]["n_sessions"] += 1

    total_sessions = len(interactive)
    dates_sorted = sorted(dates)

    # Best 4 examples per category (longest correction = most explicit pushback)
    correctives_out: dict[str, dict] = {}
    for cat, data in corrective_cats.items():
        if not data["raw"]:
            continue
        examples = sorted(data["raw"], key=lambda x: len(x["correction"]), reverse=True)[:4]
        correctives_out[cat] = {
            "n_sessions": data["n_sessions"],
            "freq_pct": round(100 * data["n_sessions"] / total_sessions, 1) if total_sessions else 0,
            "examples": [{"trigger": e["trigger"], "correction": e["correction"]} for e in examples],
        }

    # Best 5 affirmative examples
    aff_examples = sorted(affirmative_raw, key=lambda x: len(x["snippet"]), reverse=True)[:5]

    # Style stats
    style_lens_s = sorted(style_lens)
    n = len(style_lens_s)
    pct = lambda p: style_lens_s[int(n * p)] if n else 0
    total_lang = ru + en + mixed or 1

    random.seed(42)
    style_sample_out = random.sample(style_samples, min(5, len(style_samples)))

    result = {
        "device": device,
        "date_range": [dates_sorted[0] if dates_sorted else "", dates_sorted[-1] if dates_sorted else ""],
        "total_sessions": total_sessions,
        "correctives": correctives_out,
        "affirmatives": {
            "total": len(affirmative_raw),
            "examples": [{"trigger": e["trigger"], "snippet": e["snippet"]} for e in aff_examples],
        },
        "style": {
            "p50_len": pct(0.5),
            "p10_len": pct(0.1),
            "p90_len": pct(0.9),
            "short_pct": round(100 * sum(1 for l in style_lens_s if l < 100) / n, 1) if n else 0,
            "long_pct": round(100 * sum(1 for l in style_lens_s if l > 2000) / n, 1) if n else 0,
            "ru_pct": round(100 * ru / total_lang, 1),
            "en_pct": round(100 * en / total_lang, 1),
            "mixed_pct": round(100 * mixed / total_lang, 1),
            "samples": style_sample_out,
        },
    }

    with out.open("w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    size_kb = out.stat().st_size / 1024
    print(f"device: {device}")
    print(f"sessions: {total_sessions}  date_range: {dates_sorted[0] if dates_sorted else '?'} -> {dates_sorted[-1] if dates_sorted else '?'}")
    cats_summary = ", ".join(f"{k}={v['n_sessions']}" for k, v in correctives_out.items())
    print(f"corrective categories: {cats_summary}")
    print(f"affirmatives: {len(affirmative_raw)}")
    print(f"out: {out}  size: {size_kb:.1f} KB")
    if size_kb > 80:
        print(f"WARNING: {size_kb:.0f} KB exceeds 80 KB target — GDrive MCP upload may fail")


if __name__ == "__main__":
    src = sys.argv[1] if len(sys.argv) > 1 else "comms_extract.jsonl"
    out = sys.argv[2] if len(sys.argv) > 2 else f"{socket.gethostname()}_patterns.json"
    main(src, out)
