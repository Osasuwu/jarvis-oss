"""Risk Radar — early detection of project health risks.

Five validated risk patterns:
  P1: CI instability      — recent workflow run failure rate above threshold
  P2: Critical stagnation — priority:high issues not updated for too long
  P3: Security alerts     — open Dependabot vulnerability alerts
  P4: Overdue milestones  — milestones past due date with remaining open work
  P5: Review backlog      — PRs with CHANGES_REQUESTED stale > 3 days

Severity levels:
  CRITICAL — requires immediate action
  HIGH     — address this sprint
  MEDIUM   — monitor closely
  LOW      — informational

Usage (standalone):
  python src/risk_radar.py
  python src/risk_radar.py --json   # machine-readable output
"""
from __future__ import annotations

import argparse
import json as _json
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[1]
REPORTS_DIR = ROOT_DIR / "reports"
REPOS_CONF = ROOT_DIR / "config" / "repos.conf"

# Pattern thresholds
CI_CRITICAL_RATE = 0.50
CI_HIGH_RATE = 0.30
CI_MEDIUM_RATE = 0.15
CI_RUNS_SAMPLE = 20

STAGNATION_DAYS = 7
STAGNATION_HIGH = 5

CHANGES_STALE_DAYS = 3
CHANGES_HIGH_COUNT = 3

SEVERITY_ORDER = {"critical": 4, "high": 3, "medium": 2, "low": 1}


# ── Data classes ──────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class RiskAlert:
    severity: str
    pattern: str
    repo: str
    title: str
    details: str
    evidence: str


@dataclass
class RiskRadarResult:
    alerts: tuple[RiskAlert, ...]
    report_text: str
    report_path: Path
    scan_timestamp: str
    repos_scanned: int
    critical_count: int
    high_count: int
    success: bool
    error: str = ""


# ── Shared gh CLI utilities ───────────────────────────────────────────────────


def _load_repos() -> list[str]:
    if not REPOS_CONF.exists():
        return []
    repos = []
    for line in REPOS_CONF.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "/" in line:
            repos.append(line)
    return repos


def _run_gh(args: list[str], timeout: int = 30) -> tuple[bool, str]:
    if shutil.which("gh") is None:
        return False, "gh CLI not found"
    try:
        result = subprocess.run(
            ["gh"] + args,
            capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=timeout,
        )
        if result.returncode != 0:
            return False, result.stderr.strip() or "gh command failed"
        return True, result.stdout.strip()
    except subprocess.TimeoutExpired:
        return False, "gh command timed out"
    except Exception as exc:
        return False, str(exc)


def _parse_json(raw: str) -> list[dict]:
    if not raw:
        return []
    try:
        data = _json.loads(raw)
        return data if isinstance(data, list) else []
    except _json.JSONDecodeError:
        return []


def _days_ago(iso_str: str) -> float:
    try:
        ts = datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
        return (datetime.now(UTC) - ts).total_seconds() / 86400
    except (ValueError, TypeError):
        return 0.0


# ── P1: CI instability ────────────────────────────────────────────────────────


def _check_ci_instability(repo: str) -> list[RiskAlert]:
    ok, out = _run_gh([
        "run", "list", "--repo", repo,
        "--json", "conclusion,name,createdAt",
        "--limit", str(CI_RUNS_SAMPLE),
    ])
    if not ok or not out:
        return []

    runs = _parse_json(out)
    terminal = [r for r in runs if r.get("conclusion") in {
        "success", "failure", "timed_out", "cancelled"
    }]
    if not terminal:
        return []

    failures = [r for r in terminal if r.get("conclusion") in {"failure", "timed_out"}]
    rate = len(failures) / len(terminal)

    if rate >= CI_CRITICAL_RATE:
        severity = "critical"
    elif rate >= CI_HIGH_RATE:
        severity = "high"
    elif rate >= CI_MEDIUM_RATE:
        severity = "medium"
    else:
        return []

    failure_names = list({r.get("name", "?") for r in failures[:5]})
    return [RiskAlert(
        severity=severity,
        pattern="ci-instability",
        repo=repo,
        title=f"CI failure rate {rate:.0%} in last {len(terminal)} runs",
        details=(
            f"{len(failures)}/{len(terminal)} terminal runs failed. "
            f"Patterns: {', '.join(failure_names)}."
        ),
        evidence=f"gh run list --repo {repo} --limit {CI_RUNS_SAMPLE}",
    )]


# ── P2: Critical issue stagnation ────────────────────────────────────────────


def _check_critical_stagnation(repo: str) -> list[RiskAlert]:
    ok, out = _run_gh([
        "issue", "list", "--repo", repo, "--state", "open",
        "--label", "priority:high",
        "--json", "number,title,updatedAt,assignees",
        "--limit", "100",
    ])
    if not ok:
        return []

    stagnant = [
        i for i in _parse_json(out)
        if _days_ago(i.get("updatedAt", "")) >= STAGNATION_DAYS
    ]
    if not stagnant:
        return []

    count = len(stagnant)
    severity = "high" if count >= STAGNATION_HIGH else "medium"
    sample = [f"#{i['number']}: {i.get('title', '')[:60]}" for i in stagnant[:3]]
    return [RiskAlert(
        severity=severity,
        pattern="critical-stagnation",
        repo=repo,
        title=f"{count} priority:high issue{'s' if count != 1 else ''} stagnant >{STAGNATION_DAYS}d",
        details=(
            f"{count} critical issues have not been updated in over {STAGNATION_DAYS} days."
        ),
        evidence="; ".join(sample),
    )]


# ── P3: Security / Dependabot alerts ─────────────────────────────────────────


def _check_security_alerts(repo: str) -> list[RiskAlert]:
    ok, out = _run_gh([
        "api", f"repos/{repo}/dependabot/alerts",
        "--jq", '[.[] | select(.state == "open") | {severity:.security_vulnerability.severity, pkg:.dependency.package.name}]',
    ])
    if not ok:
        return []

    alerts = _parse_json(out)
    if not alerts:
        return []

    by_sev: dict[str, int] = {}
    for a in alerts:
        s = str(a.get("severity", "low")).lower()
        by_sev[s] = by_sev.get(s, 0) + 1

    top_sev = max(by_sev.keys(), key=lambda s: SEVERITY_ORDER.get(s, 0))
    severity = "critical" if top_sev in {"critical", "high"} else "medium"

    pkgs = list({a.get("pkg", "?") for a in alerts[:5]})
    total = len(alerts)
    summary = ", ".join(f"{s}:{n}" for s, n in sorted(by_sev.items(), key=lambda x: -SEVERITY_ORDER.get(x[0], 0)))
    return [RiskAlert(
        severity=severity,
        pattern="security-alert",
        repo=repo,
        title=f"{total} open Dependabot alert{'s' if total != 1 else ''} ({summary})",
        details=f"Affected packages: {', '.join(pkgs)}.",
        evidence=f"gh api repos/{repo}/dependabot/alerts",
    )]


# ── P4: Overdue milestones ────────────────────────────────────────────────────


def _check_overdue_milestones(repo: str) -> list[RiskAlert]:
    ok, out = _run_gh([
        "api", f"repos/{repo}/milestones",
        "--jq", "[.[] | select(.state == \"open\" and .due_on != null)"
                " | {title:.title, due:.due_on, open:.open_issues, closed:.closed_issues}]",
    ])
    if not ok:
        return []

    now = datetime.now(UTC)
    overdue = []
    for ms in _parse_json(out):
        due_str = ms.get("due", "")
        if not due_str:
            continue
        try:
            due = datetime.fromisoformat(due_str.replace("Z", "+00:00"))
        except ValueError:
            continue
        if due < now and ms.get("open", 0) > 0:
            total = ms["open"] + ms.get("closed", 0)
            pct = round(ms["closed"] / total * 100) if total else 0
            overdue.append({"name": ms["title"], "open": ms["open"], "pct": pct,
                            "days_late": (now - due).days})

    if not overdue:
        return []

    severity = "high" if any(ms["pct"] < 50 for ms in overdue) else "medium"
    sample = [f"'{ms['name']}' ({ms['days_late']}d late, {ms['pct']}% done)" for ms in overdue[:3]]
    return [RiskAlert(
        severity=severity,
        pattern="overdue-milestone",
        repo=repo,
        title=f"{len(overdue)} milestone{'s' if len(overdue) != 1 else ''} overdue with open issues",
        details="Scheduled milestones have passed their due date but still contain open work.",
        evidence="; ".join(sample),
    )]


# ── P5: Review-blocked / stale PRs ───────────────────────────────────────────


def _check_review_backlog(repo: str) -> list[RiskAlert]:
    ok, out = _run_gh([
        "pr", "list", "--repo", repo, "--state", "open",
        "--json", "number,title,updatedAt,reviewDecision,isDraft",
        "--limit", "100",
    ])
    if not ok:
        return []

    blocked = [
        pr for pr in _parse_json(out)
        if not pr.get("isDraft")
        and pr.get("reviewDecision") == "CHANGES_REQUESTED"
        and _days_ago(pr.get("updatedAt", "")) >= CHANGES_STALE_DAYS
    ]
    if not blocked:
        return []

    count = len(blocked)
    severity = "high" if count >= CHANGES_HIGH_COUNT else "medium"
    sample = [f"#{pr['number']}: {pr.get('title', '')[:60]}" for pr in blocked[:3]]
    return [RiskAlert(
        severity=severity,
        pattern="review-backlog",
        repo=repo,
        title=f"{count} PR{'s' if count != 1 else ''} with CHANGES_REQUESTED stale >{CHANGES_STALE_DAYS}d",
        details=(
            f"{count} non-draft PRs have unaddressed review changes and have not been "
            f"updated in over {CHANGES_STALE_DAYS} days. This blocks merging."
        ),
        evidence="; ".join(sample),
    )]


# ── Per-repo scan ─────────────────────────────────────────────────────────────


def _scan_repo(repo: str) -> list[RiskAlert]:
    alerts: list[RiskAlert] = []
    for checker in (
        _check_ci_instability,
        _check_critical_stagnation,
        _check_security_alerts,
        _check_overdue_milestones,
        _check_review_backlog,
    ):
        try:
            alerts.extend(checker(repo))
        except Exception:
            pass
    return alerts


# ── Report writer ─────────────────────────────────────────────────────────────


_SEVERITY_EMOJI = {"critical": "🔴", "high": "🟠", "medium": "🟡", "low": "🟢"}
_ESCALATION_POLICY = """\
## Escalation Policy

| Severity | Action |
|----------|--------|
| CRITICAL | Immediate: ⚠ header in all responses, include in every daily/weekly report |
| HIGH     | Daily: surfaced in triage summary |
| MEDIUM   | Weekly: included in weekly report |
| LOW      | Archive: logged to memory only |
"""


def _write_report(alerts: list[RiskAlert], repos: list[str], timestamp: str) -> tuple[str, Path]:
    criticals = [a for a in alerts if a.severity == "critical"]
    highs = [a for a in alerts if a.severity == "high"]
    mediums = [a for a in alerts if a.severity == "medium"]

    lines = [
        f"# Risk Radar — {timestamp}",
        "",
        f"**Repos scanned:** {len(repos)}  ",
        f"**Alerts:** {len(criticals)} CRITICAL · {len(highs)} HIGH · {len(mediums)} MEDIUM",
        "",
    ]

    if not alerts:
        lines.append("No risks detected. All patterns within acceptable thresholds.\n")
    else:
        lines.append("## Active Risks\n")
        sorted_alerts = sorted(alerts, key=lambda a: -SEVERITY_ORDER.get(a.severity, 0))
        for alert in sorted_alerts:
            emoji = _SEVERITY_EMOJI.get(alert.severity, "")
            lines.append(f"### {emoji} [{alert.severity.upper()}] {alert.title}")
            lines.append(f"- **Repo**: `{alert.repo}`")
            lines.append(f"- **Pattern**: {alert.pattern}")
            lines.append(f"- **Details**: {alert.details}")
            lines.append(f"- **Evidence**: `{alert.evidence}`")
            lines.append("")

    lines.append(_ESCALATION_POLICY)
    report_text = "\n".join(lines)
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    slug = re.sub(r"[: ]", "-", timestamp[:19])
    report_path = REPORTS_DIR / f"risk-radar-{slug}.md"
    report_path.write_text(report_text, encoding="utf-8")
    return report_text, report_path


# ── Main ─────────────────────────────────────────────────────────────────────


def run(as_json: bool = False) -> int:
    """Scan all configured repos. Returns exit code (0=ok, 1=alerts found, 2=error)."""
    timestamp = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S")
    repos = _load_repos()

    if not repos:
        msg = f"No repos configured. Add entries to {REPOS_CONF}"
        print(msg, file=sys.stderr)
        return 2

    all_alerts: list[RiskAlert] = []
    for repo in repos:
        all_alerts.extend(_scan_repo(repo))

    all_alerts.sort(key=lambda a: -SEVERITY_ORDER.get(a.severity, 0))
    critical_count = sum(1 for a in all_alerts if a.severity == "critical")
    high_count = sum(1 for a in all_alerts if a.severity == "high")

    if as_json:
        print(_json.dumps({
            "timestamp": timestamp,
            "repos_scanned": len(repos),
            "critical": critical_count,
            "high": high_count,
            "alerts": [
                {"severity": a.severity, "pattern": a.pattern, "repo": a.repo,
                 "title": a.title, "details": a.details}
                for a in all_alerts
            ],
        }, indent=2))
        return 0 if not all_alerts else 1

    report_text, report_path = _write_report(all_alerts, repos, timestamp)
    print(report_text)
    print(f"\nReport saved: {report_path}", file=sys.stderr)
    return 0 if not all_alerts else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Risk Radar — project health scan")
    parser.add_argument("--json", action="store_true", help="Output JSON instead of markdown")
    args = parser.parse_args()
    sys.exit(run(as_json=args.json))
