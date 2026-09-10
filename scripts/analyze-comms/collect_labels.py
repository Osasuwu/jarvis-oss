"""collect_labels.py — parse owner-labeled report.md files into ground truth (#514).

Phase B report.md (produced by Phase B step B3 of the analysis workflow) renders one structured, machine-parseable
line per candidate for `rule_violations` and `hallucinations` (#513). The
owner edits a copy of the report in GDrive, replacing the leading `[ ]` with
`[+]` (valid), `[-]` (false positive), or `[?]` (missed — a violation the
detector did not flag, noted separately by the owner).

This script reads one or more labeled report.md files, extracts only the
lines whose checkbox was filled in, and aggregates them into a Supabase
`memories` row `reflect_eval_groundtruth_<period>` with rows shaped
`{message_id, label, detector_verdict, detector_kind, snippet}` — the ground
truth Phase 2 (`eval_detectors.py`) reads back out.

Idempotent on re-runs: the Supabase write is an upsert keyed on
`(project, name)`, so re-running against the same period overwrites rather
than duplicates.
"""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

_LABEL_SYMBOL_MAP = {"+": "valid", "-": "false_positive", "?": "missed"}

# Mirrors the structured line format SKILL.md Step B3 instructs the Phase B
# subagent to emit for rule_violations (#513) candidates.
_RULE_LINE_RE = re.compile(
    r"^-\s*\[(?P<label>[+\-?])\]\s*`(?P<message_id>[^`]+)`\s*—\s*rule:\s*"
    r"`(?P<rule_name>[^`]+)`,\s*confidence:\s*(?P<confidence>[\d.]+)\s*—\s*(?P<snippet>.+)$"
)

# Mirrors the structured line format for hallucinations (#513) candidates.
_HALLUC_LINE_RE = re.compile(
    r"^-\s*\[(?P<label>[+\-?])\]\s*`(?P<message_id>[^`]+)`\s*—\s*hallucination\s*—\s*"
    r'assistant:\s*"(?P<assistant_text>.*?)"\s*—\s*correction:\s*"(?P<correction_text>.*?)"\s*$'
)


def parse_labeled_report(text: str) -> list[dict]:
    """Extract labeled (non-`[ ]`) candidate rows from one report.md's text."""
    rows: list[dict] = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        m = _RULE_LINE_RE.match(line)
        if m:
            rows.append(
                {
                    "message_id": m.group("message_id"),
                    "label": _LABEL_SYMBOL_MAP[m.group("label")],
                    "detector_verdict": m.group("rule_name"),
                    "detector_kind": "rule_violation",
                    "snippet": m.group("snippet").strip(),
                }
            )
            continue
        m = _HALLUC_LINE_RE.match(line)
        if m:
            rows.append(
                {
                    "message_id": m.group("message_id"),
                    "label": _LABEL_SYMBOL_MAP[m.group("label")],
                    "detector_verdict": "hallucination",
                    "detector_kind": "hallucination",
                    "snippet": (
                        f'assistant: "{m.group("assistant_text")}" '
                        f'| correction: "{m.group("correction_text")}"'
                    ),
                }
            )
    return rows


def aggregate_reports(report_texts: list[str]) -> list[dict]:
    """Merge labeled rows from N report.md texts, deduped by (message_id, kind).

    Last occurrence wins — if the same candidate appears in more than one
    report text (e.g. owner re-exports a corrected copy), the later label
    is authoritative.
    """
    by_key: dict[tuple[str, str], dict] = {}
    for text in report_texts:
        for row in parse_labeled_report(text):
            by_key[(row["message_id"], row["detector_kind"])] = row
    return list(by_key.values())


def build_groundtruth_payload(period: str, labels: list[dict], project: str = "jarvis") -> dict:
    return {
        "name": f"reflect_eval_groundtruth_{period}",
        "type": "project",
        "project": project,
        "tags": ["reflect-eval", "groundtruth", f"period-{period}"],
        "source_provenance": "script:collect_labels",
        "description": f"/reflect eval ground-truth labels for period {period} (#514)",
        "content": json.dumps(labels, ensure_ascii=False),
    }


def merge_into_groundtruth_memory(
    client, period: str, labels: list[dict], project: str = "jarvis"
) -> None:
    payload = build_groundtruth_payload(period, labels, project=project)
    client.table("memories").upsert(payload, on_conflict="project,name").execute()


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


def main(report_paths: list[str], period: str) -> None:  # pragma: no cover — covered by smoke
    try:
        client = _get_supabase_client()
    except RuntimeError as e:
        print(f"[collect_labels] skipping — {e}", file=sys.stderr)
        return

    texts = [Path(p).read_text(encoding="utf-8") for p in report_paths]
    labels = aggregate_reports(texts)
    merge_into_groundtruth_memory(client, period, labels)
    print(
        f"[collect_labels] {len(labels)} labeled candidates -> reflect_eval_groundtruth_{period}",
        file=sys.stderr,
    )


if __name__ == "__main__":  # pragma: no cover
    if len(sys.argv) < 3:
        print(
            "usage: collect_labels.py <period> <report.md> [report.md ...]",
            file=sys.stderr,
        )
        sys.exit(1)
    main(report_paths=sys.argv[2:], period=sys.argv[1])
