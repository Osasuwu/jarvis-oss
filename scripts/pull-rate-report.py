"""Pull-rate telemetry reader for the Glossary `@import` instruction (#1275).

On-demand only — imported by nothing on the SessionStart hot path. Invoke via:

    python scripts/pull-rate-report.py

Denominator: rows in ``.claude/logs/session-context-size.jsonl``, written by
``_self_log`` in session-context.py — one row per context-assembly run, keyed
on ``(session_id, project)``. Per that function's docstring, the pair is NOT
unique per run (Claude Code reuses session ids across ``--resume``), so rows
sharing a key are split into ``[ts, next_ts)`` windows rather than treated as
one run.

Numerator: transcript rows where the agent issued a ``Read`` or ``Grep``
targeting the Glossary source (``CONTEXT.md``) inside a run's window.
Transcript discovery is a recursive glob so subagent transcripts — stored as
separate files at ``<session_id>/subagents/agent-*.jsonl``, not as
``isSidechain`` rows inside the parent transcript — are included.

Escalation rule (for the month-later review that reads this file):
  - ``sample_size < MIN_SAMPLE_SIZE``      -> no action (too little data to trust the rate)
  - ``pull_rate <= LOW_THRESHOLD``          -> escalate: move the Glossary-pull
    instruction's target content from the `@import` carrier (level 2) down to
    level 1 (`.claude/rules/*.md` + `paths:` glob, per #1274) — an instruction
    the agent chooses whether to follow isn't reaching a workable rate, so
    back it with a path-gated mechanical load instead.
  - otherwise                               -> no action (healthy)
  Full rationale: docs/reference/pull-rate-escalation.md
"""

from __future__ import annotations

import json
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator

LOW_THRESHOLD = 0.15
MIN_SAMPLE_SIZE = 20

_GLOSSARY_SOURCE_BASENAME = "context.md"


def _parse_ts(raw: str | None) -> datetime | None:
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None


def _targets_glossary_source(name: str, tool_input: dict[str, Any]) -> bool:
    """Numerator match: Read OR Grep against the Glossary's host file.

    Grep is how the index is meant to be used (per #1275 AC), not just Read —
    a plain basename match on CONTEXT.md, not content-level keyword matching.
    """
    if name not in ("Read", "Grep"):
        return False
    path = tool_input.get("file_path") or tool_input.get("path") or ""
    return Path(path).name.lower() == _GLOSSARY_SOURCE_BASENAME


def discover_transcripts_for_session(projects_root: Path, session_id: str | None) -> list[Path]:
    """Recursive glob so subagent transcripts are included (separate files,
    not isSidechain rows in the parent — confirmed by direct inspection)."""
    if not session_id:
        return []
    main = list(projects_root.glob(f"**/{session_id}.jsonl"))
    sub = list(projects_root.glob(f"**/{session_id}/subagents/*.jsonl"))
    return main + sub


def _iter_glossary_pulls(path: Path) -> Iterator[datetime]:
    """Yield a timestamp per matching tool_use row. Skip malformed lines
    rather than raise — same precedent as scripts/comm_patterns/transcript.py."""
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if obj.get("type") != "assistant":
                    continue
                content = (obj.get("message") or {}).get("content") or []
                for block in content:
                    if not isinstance(block, dict) or block.get("type") != "tool_use":
                        continue
                    if _targets_glossary_source(block.get("name", ""), block.get("input") or {}):
                        ts = _parse_ts(obj.get("timestamp"))
                        if ts is not None:
                            yield ts
    except OSError:
        return


def _load_self_log(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    except OSError:
        return []
    return rows


def _session_windows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Group by (session_id, project), sort by ts, window each row against
    the next row sharing its key. Rows missing session_id (pre-#1275 schema)
    have no join key and are skipped."""
    by_key: dict[tuple[str, Any], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        session_id = row.get("session_id")
        if not session_id:
            continue
        by_key[(session_id, row.get("project"))].append(row)

    windows: list[dict[str, Any]] = []
    for (session_id, project), group in by_key.items():
        ordered = sorted(group, key=lambda r: r.get("ts") or "")
        for i, row in enumerate(ordered):
            start = _parse_ts(row.get("ts"))
            end = _parse_ts(ordered[i + 1].get("ts")) if i + 1 < len(ordered) else None
            windows.append(
                {
                    "session_id": session_id,
                    "project": project,
                    "start": start,
                    "end": end,
                    "row": row,
                }
            )
    return windows


def _build_transcript_index(projects_root: Path) -> dict[str, list[Path]]:
    """One walk of the whole projects tree instead of a glob per session.

    ceiling: single full rglob pass on every invocation — fine while the
    ~/.claude/projects tree stays in the thousands-of-files range (this is an
    on-demand monthly-review reader, not a hot path); if it grows enough to
    make even one pass slow, switch to an mtime-cached index instead of
    re-walking every call.
    """
    index: dict[str, list[Path]] = defaultdict(list)
    try:
        for path in projects_root.rglob("*.jsonl"):
            session_id = path.parent.parent.name if path.parent.name == "subagents" else path.stem
            index[session_id].append(path)
    except OSError:
        pass
    return index


def compute_pull_rate(self_log_rows: list[dict[str, Any]], projects_root: Path) -> dict[str, Any]:
    windows = _session_windows(self_log_rows)
    total = len(windows)
    index = _build_transcript_index(projects_root)
    pull_cache: dict[Path, list[datetime]] = {}
    pulled = 0
    for window in windows:
        start = window["start"]
        if start is None:
            continue
        end = window["end"]
        transcripts = index.get(window["session_id"], [])
        hit = False
        for transcript in transcripts:
            if transcript not in pull_cache:
                pull_cache[transcript] = list(_iter_glossary_pulls(transcript))
            for ts in pull_cache[transcript]:
                if ts >= start and (end is None or ts < end):
                    hit = True
                    break
            if hit:
                break
        if hit:
            pulled += 1
    rate = (pulled / total) if total else 0.0
    return {"total_runs": total, "runs_with_pull": pulled, "pull_rate": rate}


def evaluate_escalation(pull_rate: float, sample_size: int) -> dict[str, Any]:
    """Two branches (the earlier three-branch design's branch 1 was checked
    arithmetically impossible and dropped, per #1275 AC)."""
    if sample_size < MIN_SAMPLE_SIZE:
        return {"action": "none", "reason": "insufficient_sample", "sample_size": sample_size}
    if pull_rate <= LOW_THRESHOLD:
        return {
            "action": "escalate_to_level1",
            "reason": "pull_rate_at_or_below_threshold",
            "pull_rate": pull_rate,
        }
    return {"action": "none", "reason": "healthy", "pull_rate": pull_rate}


def main() -> None:
    repo_root = Path(__file__).resolve().parent.parent
    self_log_path = repo_root / ".claude" / "logs" / "session-context-size.jsonl"
    projects_root = Path.home() / ".claude" / "projects"

    rows = _load_self_log(self_log_path)
    result = compute_pull_rate(rows, projects_root)
    escalation = evaluate_escalation(result["pull_rate"], result["total_runs"])
    print(json.dumps({**result, "escalation": escalation}, indent=2))


if __name__ == "__main__":
    main()
