"""eval_detectors.py — regex vs LLM precision/recall A/B for the comms-audit detectors (#514).

Phase 2 of #514: pulls the owner-labeled ground truth written by
`collect_labels.py` (`reflect_eval_groundtruth_<period>`), scores the
existing regex detectors (#513) against it directly (the ground-truth rows
already carry each regex detector's verdict), runs an LLM classification
pass over the same candidate snippets, and applies the decision rule from
the issue: LLM adopted only if it beats regex by >=2x recall at comparable
(within 10 points) precision.

# ceiling: scoring runs on the ~100-char snippet captured in the report line
# (via detect_rule_violations.py/detect_hallucinations.py's `snip()`), not
# the full source message — that's the only text that survives the
# report.md -> collect_labels.py round trip today. Upgrade path: once this
# scaffolding proves out, have collect_labels.py also pull full message text
# from the original comms_extract.jsonl per session_id#message_idx.

This is scaffolding only (#514 AC): the scoring/decision logic below is
real and fixture-tested, but Phase 2 cannot actually fire until >=3 weekly
labeled reports exist (HITL, ~3 weeks) — see the issue's Phase 1 gate. The
live LLM-pass invocation (`_default_classify_fn`) is present but exercised
only by manual smoke, never by CI, for the same reason `detect_rule_violations
.py`'s Supabase path is smoke-only.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

_RECALL_2X_THRESHOLD = 2.0
_PRECISION_TOLERANCE = 0.10


def compute_precision_recall(labels: list[dict]) -> dict[str, dict]:
    """Regex-detector precision/recall per detector_kind, straight from ground truth.

    Ground-truth rows already encode the regex verdict: `valid` = detector
    caught a real one (TP), `false_positive` = detector flagged a non-issue
    (FP), `missed` = a real one the detector never flagged (FN, owner-added).
    """
    counts: dict[str, dict[str, int]] = {}
    for row in labels:
        kind = row["detector_kind"]
        bucket = counts.setdefault(kind, {"tp": 0, "fp": 0, "fn": 0})
        if row["label"] == "valid":
            bucket["tp"] += 1
        elif row["label"] == "false_positive":
            bucket["fp"] += 1
        elif row["label"] == "missed":
            bucket["fn"] += 1

    metrics: dict[str, dict] = {}
    for kind, c in counts.items():
        tp, fp, fn = c["tp"], c["fp"], c["fn"]
        metrics[kind] = {
            "tp": tp,
            "fp": fp,
            "fn": fn,
            "precision": (tp / (tp + fp)) if (tp + fp) > 0 else None,
            "recall": (tp / (tp + fn)) if (tp + fn) > 0 else None,
        }
    return metrics


def run_llm_classification(labels: list[dict], classify_fn) -> dict[str, bool]:
    """Run `classify_fn(snippet, detector_kind) -> bool` over every ground-truth row.

    `classify_fn` is injected so tests exercise this with a fixture stub
    instead of a live Claude API call — see `_default_classify_fn` for the
    real (untested-by-CI) implementation wired in `main()`.
    """
    return {row["message_id"]: classify_fn(row["snippet"], row["detector_kind"]) for row in labels}


def compute_llm_precision_recall(
    labels: list[dict], llm_verdicts: dict[str, bool]
) -> dict[str, dict]:
    """LLM precision/recall per detector_kind, against the same ground truth.

    Actual truth per row: `label != "false_positive"` (i.e. `valid` or
    `missed` — both are real violations, one caught by regex, one not).
    """
    counts: dict[str, dict[str, int]] = {}
    for row in labels:
        kind = row["detector_kind"]
        bucket = counts.setdefault(kind, {"tp": 0, "fp": 0, "fn": 0})
        actual = row["label"] != "false_positive"
        predicted = llm_verdicts.get(row["message_id"], False)
        if predicted and actual:
            bucket["tp"] += 1
        elif predicted and not actual:
            bucket["fp"] += 1
        elif not predicted and actual:
            bucket["fn"] += 1

    metrics: dict[str, dict] = {}
    for kind, c in counts.items():
        tp, fp, fn = c["tp"], c["fp"], c["fn"]
        metrics[kind] = {
            "tp": tp,
            "fp": fp,
            "fn": fn,
            "precision": (tp / (tp + fp)) if (tp + fp) > 0 else None,
            "recall": (tp / (tp + fn)) if (tp + fn) > 0 else None,
        }
    return metrics


def decision_rule(regex_metrics: dict[str, dict], llm_metrics: dict[str, dict]) -> dict:
    """Per-kind + overall upgrade/stay verdict per the issue's decision rule.

    "upgrade" for a kind requires BOTH: LLM recall >= 2x regex recall, AND
    LLM precision within 10 percentage points of regex precision. Overall
    is "upgrade" only if every scored kind agrees — a mixed result stays on
    regex rather than partially migrating one detector.
    """
    verdicts: dict[str, str] = {}
    for kind, regex_m in regex_metrics.items():
        llm_m = llm_metrics.get(kind)
        if (
            llm_m is None
            or regex_m.get("recall") is None
            or llm_m.get("recall") is None
            or regex_m.get("precision") is None
            or llm_m.get("precision") is None
        ):
            verdicts[kind] = "insufficient-data"
            continue
        precision_close = abs(llm_m["precision"] - regex_m["precision"]) <= _PRECISION_TOLERANCE
        recall_2x = (
            regex_m["recall"] > 0 and llm_m["recall"] >= _RECALL_2X_THRESHOLD * regex_m["recall"]
        )
        verdicts[kind] = "upgrade" if (precision_close and recall_2x) else "stay"

    scored = [v for v in verdicts.values() if v != "insufficient-data"]
    overall = "upgrade" if scored and all(v == "upgrade" for v in scored) else "stay"
    return {"overall": overall, "per_kind": verdicts}


def write_summary_md(
    regex_metrics: dict[str, dict],
    llm_metrics: dict[str, dict],
    decision: dict,
    out_path: Path,
    period: str,
) -> None:
    lines = [f"# /reflect eval summary — {period}", ""]
    lines.append(f"**Decision: {decision['overall']}**")
    lines.append("")
    lines.append(
        "| detector_kind | regex precision | regex recall | LLM precision | LLM recall | verdict |"
    )
    lines.append("|---|---|---|---|---|---|")
    for kind in sorted(set(regex_metrics) | set(llm_metrics)):
        rm = regex_metrics.get(kind, {})
        lm = llm_metrics.get(kind, {})
        lines.append(
            f"| {kind} | {rm.get('precision')} | {rm.get('recall')} | "
            f"{lm.get('precision')} | {lm.get('recall')} | "
            f"{decision['per_kind'].get(kind, 'insufficient-data')} |"
        )
    out_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


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


def fetch_groundtruth(
    client, period: str, project: str = "jarvis"
) -> list[dict]:  # pragma: no cover — covered by smoke
    resp = (
        client.table("memories")
        .select("content")
        .eq("project", project)
        .eq("name", f"reflect_eval_groundtruth_{period}")
        .is_("deleted_at", "null")
        .execute()
    )
    rows = resp.data or []
    if not rows:
        return []
    return json.loads(rows[0]["content"])


def _default_classify_fn(
    snippet: str, detector_kind: str
) -> bool:  # pragma: no cover — live API, smoke-only
    """Real LLM-pass classifier — lazy `anthropic` import, not exercised by CI.

    Scaffolding only per #514 Phase 1 gate: there is no labeled dataset yet
    to run this against meaningfully. Present so Phase 2 can fire the moment
    >=3 weekly labeled reports exist, without another code-writing pass.
    """
    from anthropic import Anthropic

    client = Anthropic()
    prompt = (
        "You are checking a Claude Code assistant message snippet for a "
        f"possible {detector_kind.replace('_', ' ')}. Snippet:\n\n{snippet}\n\n"
        'Reply with exactly one word: "yes" or "no".'
    )
    resp = client.messages.create(
        model="claude-sonnet-4-5",
        max_tokens=8,
        messages=[{"role": "user", "content": prompt}],
    )
    text = "".join(b.text for b in resp.content if hasattr(b, "text")).strip().lower()
    return text.startswith("yes")


def main(
    period: str, out_dir: str, classify_fn=None
) -> None:  # pragma: no cover — covered by smoke
    try:
        client = _get_supabase_client()
    except RuntimeError as e:
        print(f"[eval_detectors] skipping — {e}", file=sys.stderr)
        return

    labels = fetch_groundtruth(client, period)
    if not labels:
        print(
            f"[eval_detectors] no ground truth for period={period} — "
            "run collect_labels.py first (needs >=3 weeks of labeled reports per #514)",
            file=sys.stderr,
        )
        return

    regex_metrics = compute_precision_recall(labels)
    llm_verdicts = run_llm_classification(labels, classify_fn or _default_classify_fn)
    llm_metrics = compute_llm_precision_recall(labels, llm_verdicts)
    decision = decision_rule(regex_metrics, llm_metrics)

    out_path = Path(out_dir) / f"reflect_eval_summary_{period}.md"
    write_summary_md(regex_metrics, llm_metrics, decision, out_path, period)
    print(f"[eval_detectors] decision={decision['overall']} -> {out_path}", file=sys.stderr)


if __name__ == "__main__":  # pragma: no cover
    if len(sys.argv) < 3:
        print("usage: eval_detectors.py <period> <out_dir>", file=sys.stderr)
        sys.exit(1)
    main(period=sys.argv[1], out_dir=sys.argv[2])
