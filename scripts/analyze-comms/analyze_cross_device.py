"""analyze_cross_device.py — merge patterns from multiple devices.

Input:  one or more {DEVICE}_patterns.json files (downloaded from GDrive)
Output: merged_patterns.json — all patterns with confidence scores, sorted by strength.

Confidence score = weighted_frequency × n_devices_present
where weighted_frequency = sum(n_sessions_with_pattern) / sum(total_sessions_across_devices)
"""
from __future__ import annotations
import json, sys
from collections import defaultdict
from pathlib import Path


def load(fp: Path) -> dict:
    with fp.open(encoding="utf-8") as f:
        return json.load(f)


def merge(files: list[Path]) -> dict:
    all_data = [load(fp) for fp in files]
    devices = [d.get("device", fp.stem) for d, fp in zip(all_data, files)]
    total_sessions_global = sum(d.get("total_sessions", 0) for d in all_data)
    n_devices = len(all_data)

    # --- Corrective categories ---
    cat_stats: dict[str, dict] = defaultdict(lambda: {
        "n_sessions_by_device": {},
        "freq_pct_by_device": {},
        "devices_present": [],
        "examples": [],
    })

    for data, device in zip(all_data, devices):
        for cat, cat_data in data.get("correctives", {}).items():
            entry = cat_stats[cat]
            entry["n_sessions_by_device"][device] = cat_data.get("n_sessions", 0)
            entry["freq_pct_by_device"][device] = cat_data.get("freq_pct", 0.0)
            entry["devices_present"].append(device)
            for ex in cat_data.get("examples", [])[:2]:
                entry["examples"].append({**ex, "_device": device})

    corrective_patterns = []
    for cat, entry in cat_stats.items():
        n_devices_present = len(set(entry["devices_present"]))
        total_with_pattern = sum(entry["n_sessions_by_device"].values())
        weighted_freq = (
            sum(
                entry["freq_pct_by_device"].get(dev, 0) * data.get("total_sessions", 1)
                for dev, data in zip(devices, all_data)
            ) / total_sessions_global
            if total_sessions_global else 0
        )
        confidence_score = round(weighted_freq * n_devices_present, 2)

        corrective_patterns.append({
            "category": cat,
            "confidence_score": confidence_score,
            "frequency_pct": round(weighted_freq, 1),
            "total_sessions_with_pattern": total_with_pattern,
            "n_devices_present": n_devices_present,
            "devices_present": sorted(set(entry["devices_present"])),
            "n_sessions_by_device": entry["n_sessions_by_device"],
            "examples": entry["examples"][:6],
        })

    corrective_patterns.sort(key=lambda x: x["confidence_score"], reverse=True)

    # --- Affirmatives ---
    aff_total = 0
    aff_examples: list[dict] = []
    for data, device in zip(all_data, devices):
        aff = data.get("affirmatives", {})
        aff_total += aff.get("total", 0)
        for ex in aff.get("examples", [])[:3]:
            aff_examples.append({**ex, "_device": device})
    aff_examples.sort(key=lambda x: len(x.get("snippet", "")), reverse=True)

    # --- Style aggregate ---
    style_agg: dict[str, list] = defaultdict(list)
    style_samples: list[str] = []
    for data in all_data:
        s = data.get("style", {})
        for key in ("p50_len", "p10_len", "p90_len", "short_pct", "long_pct",
                    "ru_pct", "en_pct", "mixed_pct"):
            v = s.get(key)
            if v is not None:
                style_agg[key].append(v)
        style_samples.extend(s.get("samples", [])[:3])

    style_merged = {k: round(sum(vs) / len(vs), 1) for k, vs in style_agg.items() if vs}
    style_merged["samples"] = style_samples[:8]

    # --- Date ranges ---
    all_dates = [d for data in all_data for d in data.get("date_range", []) if d]
    overall_range = [min(all_dates), max(all_dates)] if all_dates else ["", ""]

    return {
        "meta": {
            "devices": devices,
            "n_devices": n_devices,
            "total_sessions": total_sessions_global,
            "date_range": overall_range,
            "date_ranges_by_device": {
                dev: data.get("date_range", []) for dev, data in zip(devices, all_data)
            },
        },
        "corrective_patterns": corrective_patterns,
        "affirmatives": {
            "total_moments": aff_total,
            "examples": aff_examples[:8],
        },
        "style": style_merged,
    }


def main(input_files: list[str], out_path: str) -> None:
    files = [Path(fp) for fp in input_files]
    missing = [fp for fp in files if not fp.exists()]
    if missing:
        print(f"ERROR: not found: {[str(m) for m in missing]}")
        sys.exit(1)

    result = merge(files)
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    meta = result["meta"]
    print(f"merged: {meta['n_devices']} devices, {meta['total_sessions']} sessions")
    print(f"date range: {meta['date_range'][0]} -> {meta['date_range'][1]}")
    print()
    print("corrective patterns (by confidence):")
    for p in result["corrective_patterns"]:
        bar = "█" * int(p["confidence_score"] * 2)
        print(f"  {p['category']:<30} conf={p['confidence_score']:.2f} {bar}")
        print(f"  {'':30} freq={p['frequency_pct']}%  "
              f"sessions={p['total_sessions_with_pattern']}  "
              f"devices={p['n_devices_present']}/{meta['n_devices']}")
    print()
    print(f"affirmatives: {result['affirmatives']['total_moments']} moments")
    print(f"out: {out}  size: {out.stat().st_size / 1024:.1f} KB")


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("usage: analyze_cross_device.py <d1_patterns.json> [<d2> ...] <out.json>")
        sys.exit(1)
    *inputs, output = sys.argv[1:]
    main(inputs, output)
