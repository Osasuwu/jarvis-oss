"""Deterministic /status renderer (#1018).

Pure function over the `status_digest` MCP JSON
(`{health, detector_hits, ranking, provenance}` — see mcp-status/server.py).
The default render is 0-LLM deterministic Python: a health line, a ranked
top-N "Куда смотреть" list, and an "Аномалии" block. `--deep` adds the
deterministic full picture (every hit + a provenance table) for the
/status skill session to layer LLM narration onto — the renderer itself
never calls an LLM.

Provenance contract (issue #1018 AC3): the renderer is forbidden from
emitting a green health line unless *every* source ran ok and is fresh.
It re-derives that independently from `provenance` rather than trusting
`health.ok` alone, so a malformed digest (ok=True but a source silently
failed/stale) still reads as suspicious, not "all clear".
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

# Single source of truth for the freshness threshold — mirror the engine.
sys.path.insert(0, str(Path(__file__).resolve().parent))
try:
    from status_engine import FRESHNESS_AGE_SECONDS
except Exception:  # pragma: no cover - defensive fallback if engine unavailable
    FRESHNESS_AGE_SECONDS = 86400

GREEN = "🟢"
RED = "🔴"


def _provenance_issues(provenance: dict) -> list[str]:
    """Return human-readable descriptions of every degraded/stale source.

    A source is degraded if it did not run, reported ok=False, or is older
    than the freshness threshold. Empty list ⇒ every source is ok + fresh.
    """
    issues: list[str] = []
    for name, prov in sorted((provenance or {}).items()):
        ran = prov.get("ran", False)
        ok = prov.get("ok", False)
        age = prov.get("age", 0.0) or 0.0
        if not ran:
            issues.append(f"{name}: did not run")
        elif not ok:
            issues.append(f"{name}: failed (ok=False)")
        elif age > FRESHNESS_AGE_SECONDS:
            issues.append(f"{name}: stale ({age:.0f}s > {FRESHNESS_AGE_SECONDS}s)")
    return issues


def _health_line(digest: dict) -> str:
    """Render the one-line health verdict.

    Green ONLY when health.ok AND no provenance source is degraded/stale.
    """
    health = digest.get("health", {}) or {}
    prov_issues = _provenance_issues(digest.get("provenance", {}))

    if health.get("ok") and not prov_issues:
        reason = health.get("reason") or "Все источники свежие, аномалий нет"
        return f"{GREEN} Всё чисто — {reason}"

    if health.get("ok"):
        # health claims ok but provenance disagrees — surface the degradation.
        reason = "источники деградировали: " + "; ".join(prov_issues)
    else:
        reason = health.get("reason") or "состояние неизвестно"
        if prov_issues:
            reason = f"{reason}; источники: " + "; ".join(prov_issues)
    return f"{RED} {reason}"


def _kuda_smotret(digest: dict) -> list[str]:
    """Ranked top-N action list — straight from the engine's ranking."""
    ranking = digest.get("ranking") or []
    if not ranking:
        return []
    lines = ["Куда смотреть:"]
    for item in ranking:
        rank = item.get("rank", "?")
        reason = item.get("reason", "")
        lines.append(f"  {rank}. {reason}")
    return lines


def _anomalies(digest: dict, deep: bool) -> list[str]:
    """Аномалии block — detector hits, grouped by repo so both repos show.

    Default surface is symptom-level (one line per hit). `deep` adds the
    full description text for each hit.
    """
    hits = digest.get("detector_hits") or []
    # info hits (stale-backlog, #1059) are advisory — the default surface hides
    # them; they appear only under --deep.
    if not deep:
        hits = [h for h in hits if (h.get("severity") or "") != "info"]
    if not hits:
        return []
    lines = ["Аномалии:"]
    by_repo: dict[str, list[dict]] = {}
    for hit in hits:
        by_repo.setdefault(hit.get("repo", "?"), []).append(hit)
    for repo in sorted(by_repo):
        lines.append(f"  {repo}:")
        for hit in by_repo[repo]:
            sev = (hit.get("severity") or "").upper()
            detector = hit.get("detector", "?")
            num = hit.get("issue_number")
            ref = f" #{num}" if num else ""
            lines.append(f"    • [{sev}] {detector}{ref}")
            if deep and hit.get("description"):
                lines.append(f"        {hit['description']}")
    return lines


def _provenance_table(digest: dict) -> list[str]:
    """Deep-only: per-source provenance table."""
    provenance = digest.get("provenance") or {}
    if not provenance:
        return ["Провенанс: (нет источников)"]
    lines = ["Провенанс:"]
    for name, prov in sorted(provenance.items()):
        lines.append(
            f"  {name}: ran={prov.get('ran')} ok={prov.get('ok')} "
            f"input_rows={prov.get('input_rows', 0)} age={prov.get('age', 0.0):.0f}s"
        )
    return lines


def render(digest: dict, deep: bool = False) -> str:
    """Render a status digest to deterministic text.

    Args:
        digest: the status_digest MCP JSON dict.
        deep: when True, append the deterministic full picture (full hit
            descriptions + a provenance table). The default (False) path is
            the cheap several-times-a-day surface and is unaffected by deep.

    Returns:
        A newline-joined string. Never calls an LLM.
    """
    blocks: list[list[str]] = [[_health_line(digest)]]

    kuda = _kuda_smotret(digest)
    if kuda:
        blocks.append(kuda)

    anomalies = _anomalies(digest, deep)
    if anomalies:
        blocks.append(anomalies)

    if deep:
        blocks.append(_provenance_table(digest))

    return "\n\n".join("\n".join(block) for block in blocks)


def main(argv: list[str] | None = None) -> int:
    """CLI: read a status_digest JSON from stdin, print the render.

    Usage: status_digest (MCP) | python scripts/status_render.py [--deep]
    """
    argv = sys.argv[1:] if argv is None else argv
    deep = "--deep" in argv
    # Health emoji are non-cp1251; force UTF-8 so the CLI never crashes on a
    # Windows console (cp1251 default). render() itself returns a plain str.
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):  # pragma: no cover - non-reconfigurable stream
        pass
    raw = sys.stdin.read()
    try:
        digest = json.loads(raw)
    except json.JSONDecodeError as exc:
        sys.stderr.write(f"status_render: invalid digest JSON on stdin: {exc}\n")
        return 2
    print(render(digest, deep=deep))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
