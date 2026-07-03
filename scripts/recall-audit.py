"""Recall-audit: scan session jsonl for decision points without preceding recall (#333).

NOTE (#510): the ``/reflect`` references below describe the OLD reflect
skill (cross-session aggregate consumer). After #510, ``/reflect`` is a
behavioral comms-audit skill and no longer consumes this aggregate.
The aggregate consumer migrates to ``/self-improve`` once that skill is
grilled — see ``docs/design/reflect-aggregates-pending-migration.md``
and follow-up issue #516.

Complements ``scripts/memory-recall-hook.py`` (UserPromptSubmit) and
``scripts/pretooluse-recall-hook.py`` (PreToolUse). Those hooks *inject*
recall. This script *audits* whether recall actually happened at the
moments that matter — so ``/end`` can show per-session gaps and
``/reflect`` can aggregate across sessions.

Detectors
---------
1. **record_decision with empty ``memories_used``** — hard signal of a
   recall gap or broken attribution. The decision was made, but nothing
   from memory informed it (or nothing was attributed back).

2. **"Decision language" in assistant text without a preceding
   ``memory_recall`` call in the last ``WINDOW_TOOLS`` tool uses** —
   soft signal of an autonomous call made without looking at prior art.
   The pattern list is intentionally conservative to keep false
   positives down (we'd rather miss a few than cry wolf).

3. **memory_store of ``feedback`` / ``decision`` memory without a
   preceding ``memory_recall`` in the last ``WINDOW_TOOLS`` tool uses** —
   duplicate-creation risk. Every new feedback/decision memory should
   have been preceded by "is this already there?" recall.

Output
------
``dict`` with ``session_id``, ``file``, ``counters``, and ``flags`` (a
list of per-event records). Can be printed as JSON or pretty markdown
via ``--format=md``. The markdown rendering is what ``/end`` and
``/reflect`` embed.

Exit codes
----------
- ``0`` — scan completed (regardless of how many flags found).
- ``1`` — file not found, unreadable, or malformed beyond recovery.
- ``2`` — CLI usage error.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

WINDOW_TOOLS = 15  # lookback window for detectors 2 + 3
MIN_DECISION_CHARS = 24  # drop tiny "ok, do X" noise

DECISION_PATTERNS = [
    r"\bi (?:decided|propose|choose|chose|opted|am going) (?:to|for|with)\b",
    r"\blet(?:'s| us) (?:do|go with|use|try)\b",
    r"\bgoing (?:to|with) (?:implement|use|try)\b",
    r"\bwe(?:'ll| will) (?:go with|use|implement)\b",
    r"\bchosen approach\b",
    r"\bfinal (?:choice|call|decision)\b",
]
_DECISION_RE = re.compile("|".join(DECISION_PATTERNS), re.IGNORECASE)

RECALL_TOOLS = {"mcp__memory__memory_recall", "mcp__memory__memory_get"}
DECISION_TOOL = "mcp__memory__record_decision"
STORE_TOOL = "mcp__memory__memory_store"

AUDITED_STORE_TYPES = {"feedback", "decision"}


@dataclass
class Flag:
    """One recall-gap finding. ``line`` is the 1-indexed jsonl line number."""

    kind: str  # detector id: "empty_memories_used" | "decision_text_no_recall" | "store_no_recall"
    line: int
    summary: str
    detail: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "kind": self.kind,
            "line": self.line,
            "summary": self.summary,
            "detail": self.detail,
        }


@dataclass
class AuditResult:
    file: str
    session_id: str
    counters: dict = field(default_factory=dict)
    flags: list[Flag] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "file": self.file,
            "session_id": self.session_id,
            "counters": self.counters,
            "flags": [f.to_dict() for f in self.flags],
        }


def _iter_records(path: Path) -> Iterable[tuple[int, dict]]:
    """Yield (1-indexed line_no, parsed-record) pairs, skipping bad JSON."""
    with path.open("r", encoding="utf-8", errors="replace") as fh:
        for i, line in enumerate(fh, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                yield i, json.loads(line)
            except json.JSONDecodeError:
                continue


def _assistant_blocks(record: dict) -> list[dict]:
    """Return content blocks if this is an assistant message; else []."""
    if record.get("type") != "assistant":
        return []
    msg = record.get("message") or {}
    if msg.get("role") != "assistant":
        return []
    content = msg.get("content") or []
    return [b for b in content if isinstance(b, dict)]


def _extract_decision_sentences(text: str) -> list[str]:
    """Return the (trimmed) sentences in `text` that match DECISION_PATTERNS."""
    if not text or len(text) < MIN_DECISION_CHARS:
        return []
    hits = []
    for raw in re.split(r"(?<=[.!?])\s+", text):
        sentence = raw.strip()
        if len(sentence) < MIN_DECISION_CHARS:
            continue
        if _DECISION_RE.search(sentence):
            hits.append(sentence[:200])
    return hits


def _tool_use_info(block: dict) -> tuple[str, dict] | None:
    """If `block` is a tool_use content block, return (name, input). Else None."""
    if block.get("type") != "tool_use":
        return None
    return (block.get("name") or "", block.get("input") or {})


def audit_session(path: Path) -> AuditResult:
    """Scan one session's jsonl and collect flags from all three detectors.

    Algorithm: single forward pass. We keep a sliding "recent tools" list
    (capped at WINDOW_TOOLS) so detectors 2 + 3 can check whether a
    ``memory_recall`` happened before each decision/store moment. The
    window resets per-session (one file = one session).
    """
    if not path.exists():
        raise FileNotFoundError(f"session jsonl not found: {path}")

    result = AuditResult(file=str(path), session_id=path.stem)
    counters = {
        "records": 0,
        "assistant_messages": 0,
        "tool_uses": 0,
        "record_decision_calls": 0,
        "memory_store_calls": 0,
        "memory_recall_calls": 0,
    }

    recent_tools: list[str] = []

    def _note_tool(name: str) -> None:
        recent_tools.append(name)
        if len(recent_tools) > WINDOW_TOOLS:
            del recent_tools[: len(recent_tools) - WINDOW_TOOLS]

    def _recall_in_window() -> bool:
        return any(t in RECALL_TOOLS for t in recent_tools)

    for line_no, record in _iter_records(path):
        counters["records"] += 1
        if not result.session_id or result.session_id == path.stem:
            sid = record.get("sessionId") or ""
            if sid:
                result.session_id = sid

        blocks = _assistant_blocks(record)
        if not blocks:
            continue
        counters["assistant_messages"] += 1

        for block in blocks:
            if block.get("type") == "text":
                text = block.get("text") or ""
                hits = _extract_decision_sentences(text)
                if hits and not _recall_in_window():
                    result.flags.append(
                        Flag(
                            kind="decision_text_no_recall",
                            line=line_no,
                            summary=hits[0],
                            detail={
                                "matches": hits,
                                "recent_tools": list(recent_tools),
                            },
                        )
                    )
                continue

            tool = _tool_use_info(block)
            if tool is None:
                continue
            name, tool_input = tool
            counters["tool_uses"] += 1

            if name == DECISION_TOOL:
                counters["record_decision_calls"] += 1
                mem_used = tool_input.get("memories_used") or []
                if not mem_used:
                    result.flags.append(
                        Flag(
                            kind="empty_memories_used",
                            line=line_no,
                            summary=(tool_input.get("decision") or "")[:200],
                            detail={
                                "rationale_preview": (tool_input.get("rationale") or "")[:200],
                            },
                        )
                    )
            elif name == STORE_TOOL:
                counters["memory_store_calls"] += 1
                mem_type = (tool_input.get("type") or "").lower()
                if mem_type in AUDITED_STORE_TYPES and not _recall_in_window():
                    result.flags.append(
                        Flag(
                            kind="store_no_recall",
                            line=line_no,
                            summary=(tool_input.get("name") or "")[:200],
                            detail={
                                "memory_type": mem_type,
                                "recent_tools": list(recent_tools),
                            },
                        )
                    )
            elif name in RECALL_TOOLS:
                counters["memory_recall_calls"] += 1

            _note_tool(name)

    result.counters = counters
    return result


def render_markdown(result: AuditResult) -> str:
    """Render an audit result as the compact markdown /end embeds.

    Returns an empty string when there are zero flags — /end can then
    skip the whole Recall-audit section without a visible placeholder.
    """
    if not result.flags:
        return ""

    by_kind: dict[str, list[Flag]] = {}
    for f in result.flags:
        by_kind.setdefault(f.kind, []).append(f)

    kind_label = {
        "empty_memories_used": "Decisions with empty memories_used",
        "decision_text_no_recall": "Decision language without preceding recall",
        "store_no_recall": "New feedback/decision memory without preceding recall",
    }

    lines: list[str] = [f"### Recall audit — {len(result.flags)} flagged"]
    for kind, flags in by_kind.items():
        label = kind_label.get(kind, kind)
        lines.append(f"- **{label}** ({len(flags)}):")
        for f in flags[:5]:
            summary = f.summary.replace("\n", " ")
            lines.append(f"  - L{f.line}: {summary}")
        if len(flags) > 5:
            lines.append(f"  - … +{len(flags) - 5} more")
    return "\n".join(lines)


def aggregate(results: list[AuditResult]) -> dict:
    """Summarize a batch of audits for /reflect cross-session view."""
    totals = {
        "sessions": len(results),
        "records": 0,
        "record_decision_calls": 0,
        "memory_store_calls": 0,
        "memory_recall_calls": 0,
        "flags_total": 0,
        "flags_by_kind": {
            "empty_memories_used": 0,
            "decision_text_no_recall": 0,
            "store_no_recall": 0,
        },
    }
    for r in results:
        for k in ("records", "record_decision_calls", "memory_store_calls", "memory_recall_calls"):
            totals[k] += r.counters.get(k, 0)
        totals["flags_total"] += len(r.flags)
        for f in r.flags:
            totals["flags_by_kind"].setdefault(f.kind, 0)
            totals["flags_by_kind"][f.kind] += 1

    decisions = totals["record_decision_calls"] or 1
    totals["empty_memories_used_pct"] = round(
        100.0 * totals["flags_by_kind"].get("empty_memories_used", 0) / decisions, 1
    )
    return totals


def _resolve_project_dir(project_name: str) -> Path:
    """Map a short project name (jarvis, redrobot) to the ~/.claude/projects dir.

    Claude Code stores each working-dir's jsonl under a slug built by
    replacing path separators and colons with '-'. We don't know the
    owner's full path ahead of time, so we glob for a directory whose
    final path segment contains the project name.
    """
    base = Path.home() / ".claude" / "projects"
    if not base.exists():
        raise FileNotFoundError(f"no ~/.claude/projects directory at {base}")
    target = project_name.lower()
    candidates = [p for p in base.iterdir() if p.is_dir() and target in p.name.lower()]
    if not candidates:
        raise FileNotFoundError(f"no project dir matching {target} under {base}")
    candidates.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return candidates[0]


def _discover_sessions(project: str | None, session_path: str | None) -> list[Path]:
    """Return the list of session jsonl paths to audit."""
    if session_path:
        p = Path(session_path).expanduser()
        if not p.exists():
            raise FileNotFoundError(p)
        return [p]
    if project:
        root = _resolve_project_dir(project)
        return sorted(root.glob("*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True)
    return []


def _ensure_utf8_stdout() -> None:
    """On Windows, default cp1251 stdout chokes on non-ASCII payloads.

    Session text is frequently mixed-language (owner writes Russian, we
    quote assistant output). Force UTF-8 so the pretty-printed markdown
    and JSON never hit a ``UnicodeEncodeError`` at print-time.
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            try:
                reconfigure(encoding="utf-8", errors="replace")
            except (AttributeError, ValueError):
                pass


def main(argv: list[str] | None = None) -> int:
    _ensure_utf8_stdout()
    parser = argparse.ArgumentParser(description="Audit a Claude Code session for recall gaps.")
    parser.add_argument("session", nargs="?", help="Path to session jsonl file.")
    parser.add_argument("--project", help="Short project name (e.g. jarvis); scans all sessions.")
    parser.add_argument("--limit", type=int, default=0, help="Max sessions when --project used.")
    parser.add_argument(
        "--format",
        choices=("json", "md"),
        default="json",
        help="Output format. json = machine; md = human (/end, /reflect).",
    )
    parser.add_argument(
        "--aggregate",
        action="store_true",
        help="With --project, emit a single aggregate dict instead of per-session.",
    )
    args = parser.parse_args(argv)

    if not args.session and not args.project:
        parser.error("either a session path or --project is required")

    try:
        sessions = _discover_sessions(args.project, args.session)
    except FileNotFoundError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1

    if args.limit and args.project:
        sessions = sessions[: args.limit]

    results: list[AuditResult] = []
    for path in sessions:
        try:
            results.append(audit_session(path))
        except (FileNotFoundError, OSError) as e:
            print(f"warn: skipping {path}: {e}", file=sys.stderr)

    if args.aggregate:
        payload: dict[str, Any] = aggregate(results)
        print(json.dumps(payload, indent=2))
        return 0

    if args.format == "md":
        chunks = []
        for r in results:
            md = render_markdown(r)
            if md:
                chunks.append(f"## {r.session_id}\n{md}")
        print("\n\n".join(chunks) if chunks else "No recall-gap flags found.")
        return 0

    payload = [r.to_dict() for r in results]
    print(json.dumps(payload if len(payload) != 1 else payload[0], indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
