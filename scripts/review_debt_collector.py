"""Review-debt collector (#1211) — deterministic, no-LLM CI module.

On each merged PR the code-review plugin emits a structured JSON findings block
(schema documented in `commands/code-review.md` §8.1 of the review plugin). Its
`severity` field is bucket-derived (MEDIUM for the Code-review bucket, INFO for
Simplification) and per §8.1 AC6 never gates the merge — those findings
evaporate today. (This is distinct from a merge-blocking *prose heading* like
"### MEDIUM" in the comment body, which the merge gate does act on — see
`BLOCKING_RE` below.) This module collects the JSON-field findings: it parses
the block, persists each unique finding to
the `review_debt` Supabase table with weighted dedup, clusters by parent-
directory `module_area`, and — when a cluster's weighted count crosses a
threshold — auto-creates exactly one `review-debt-cluster` GitHub issue.

Layering:
  * PURE core (this file, top half) — parsing, dedup key, module area, severity
    weighting, cluster threshold, blocking skip, TTL age-out. No I/O, fully unit
    tested (tests/infrastructure/test_review_debt_collector.py).
  * I/O layer (bottom half) — PostgREST upsert, idempotent issue create,
    milestone resolve, heartbeat. Guarded by env; exercised by the workflow.

Design note (`record_decision` unavailable this session — see memory
`review_debt_collector_architecture_1211`): one deep module + one dedicated
workflow; ONE additive table with an ON-CONFLICT upsert; `weight` (per-severity)
kept separate from `seen_count` (raw occurrences); heartbeat to the LEGACY
`events` table (events_canonical anon-insert is sandcastle-gated).
"""

from __future__ import annotations

import datetime as _dt
import json
import os
import re
import sys
from pathlib import PurePosixPath
from typing import Any

# ── Tunables — SINGLE source of truth (AC: both configurable in one place) ──
CONFIG: dict[str, Any] = {
    # AC4: weighted cluster count that triggers one auto-created issue.
    "cluster_threshold": 4.0,
    # AC5: findings older than this age out of the active cluster count.
    "ttl_days": 30,
    # AC1/AC4: per-severity weight. MEDIUM and INFO deliberately differ so an
    # INFO-only cluster needs more accumulation before it earns an issue.
    "severity_weights": {"MEDIUM": 1.0, "INFO": 0.5},
    # Unknown / unexpected severity → treated as low-signal.
    "default_weight": 0.5,
    # Only these severities are collected — anything MAJOR+ blocks the merge and
    # is handled by the merge gate, never reaching this collector (AC6).
    "collected_severities": ("MEDIUM", "INFO"),
    # Supabase table (additive; shared DB — migration coordinated / HITL).
    "table": "review_debt",
    # Heartbeat lands in the legacy `events` table (AC9).
    "heartbeat_table": "events",
    # Labels on the auto-created cluster issue (AC4).
    "issue_labels": ("review-debt-cluster", "tech-debt"),
    # Schema version this parser understands (AC2 degradation contract).
    "supported_schema_version": 1,
}

# AC2: the canonical HTML-comment marker the plugin wraps its JSON in. Pinned
# here and asserted byte-identical by the #326 meta-test config dimension.
FINDINGS_BLOCK_RE = re.compile(
    r"<!--\s*code-review-findings\s*\n(?P<json>.*?)\n\s*-->",
    re.DOTALL,
)

# AC6: merge-blocking severity heading. Byte-aligned with code-review.yml's
# BLOCK_RE and tests/ci/test_code_review_verdict_guard.py — an all-caps
# CRITICAL/MAJOR/BLOCKING/MEDIUM heading after 1-6 '#'s, decoration (emoji)
# tolerated. Case-SENSITIVE (no re.I): title-case prose like "### Blocking
# issues — None" or advisory "Medium" text must NOT match. MINOR is not
# blocking.
#
# MEDIUM added per the #1385 gate-severity follow-up: the merge gate now
# blocks on an all-caps "### MEDIUM" heading, same as CRITICAL/MAJOR/BLOCKING.
# This is NOT the same signal as `CONFIG["collected_severities"]` below — that
# reads the JSON findings-block's `severity` field, which is a coarse,
# bucket-derived value (always "MEDIUM" for the Code-review bucket, "INFO" for
# Simplification per commands/code-review.md §8.1) that the plugin documents
# as informational-only and never gating (§8.1 AC6: "never CRITICAL/MAJOR/
# BLOCKING... never affects the merge-gate verdict"). A prose "### MEDIUM"
# heading is a deviant/ad hoc bot output (same class as the historical bare
# "### MAJOR" on #956 or "🔴 BLOCKING" on #954), not the documented findings
# block — so a legitimately-merged PR's JSON block can still legitimately
# carry `"severity": "MEDIUM"` findings without ever having tripped this
# heading check. Keep `collected_severities`/`severity_weights` unchanged.
BLOCKING_RE = re.compile(r"^#{1,6}[^A-Za-z0-9\n]*(?:CRITICAL|MAJOR|BLOCKING|MEDIUM)\b", re.M)


# ── PURE core ──────────────────────────────────────────────────────────────


def parse_findings_block(comment_body: str) -> list[dict[str, Any]]:
    """Extract collected findings from the plugin's JSON block.

    Degradation contract (AC2): a missing block, unparseable JSON, or an
    unknown ``schema_version`` yields ``[]`` — never raises, never blocks.
    Only ``collected_severities`` are returned (MAJOR+ shouldn't appear, but we
    defend against it).
    """
    if not comment_body:
        return []
    m = FINDINGS_BLOCK_RE.search(comment_body)
    if not m:
        return []
    try:
        payload = json.loads(m.group("json"))
    except (ValueError, TypeError):
        return []
    if not isinstance(payload, dict):
        return []
    if payload.get("schema_version") != CONFIG["supported_schema_version"]:
        return []
    findings = payload.get("findings")
    if not isinstance(findings, list):
        return []
    out: list[dict[str, Any]] = []
    for f in findings:
        if not isinstance(f, dict):
            continue
        if f.get("severity") not in CONFIG["collected_severities"]:
            continue
        out.append(f)
    return out


def module_area(file_path: str) -> str:
    """Parent directory of a finding's file — the clustering bucket (AC4).

    Backslashes are normalised to POSIX so a finding reported on Windows and one
    on the CI runner bucket identically. A repo-root file yields ``"."``.
    """
    posix = str(file_path).replace("\\", "/")
    parent = PurePosixPath(posix).parent
    return str(parent)


def dedup_key(finding: dict[str, Any]) -> str:
    """Stable identity of a finding for cross-PR dedup (AC2/AC3).

    Key = module_area + rule + file. Deliberately EXCLUDES the free-text
    ``description`` (wording drifts between runs) and ``line`` (shifts as the
    file evolves), so the same defect recurring across PRs collapses to one row.
    """
    file = str(finding.get("file", ""))
    rule = str(finding.get("rule", ""))
    return f"{module_area(file)}\x1f{rule}\x1f{file}"


def severity_weight(severity: str) -> float:
    """Per-severity contribution to a cluster's weighted count (AC1/AC4)."""
    return CONFIG["severity_weights"].get(severity, CONFIG["default_weight"])


def has_blocking_finding(comment_body: str) -> bool:
    """True iff the review comment carries a merge-blocking heading (AC6).

    When true the whole collector step is skipped: a PR that shipped with a
    CRITICAL/MAJOR/BLOCKING finding is not a source of sub-MAJOR debt to mine.
    """
    if not comment_body:
        return False
    return BLOCKING_RE.search(comment_body) is not None


def _parse_ts(ts: str) -> _dt.datetime:
    """Parse an ISO-8601 timestamp (tolerating a trailing ``Z``) as UTC-aware."""
    s = str(ts).replace("Z", "+00:00")
    dt = _dt.datetime.fromisoformat(s)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=_dt.timezone.utc)
    return dt


def is_expired(last_seen_at: str, now: str, ttl_days: int | None = None) -> bool:
    """True iff ``last_seen_at`` is older than the TTL relative to ``now`` (AC5)."""
    ttl = CONFIG["ttl_days"] if ttl_days is None else ttl_days
    age = _parse_ts(now) - _parse_ts(last_seen_at)
    return age > _dt.timedelta(days=ttl)


def active_findings(
    rows: list[dict[str, Any]], now: str, ttl_days: int | None = None
) -> list[dict[str, Any]]:
    """Rows that still count toward a cluster (AC5).

    Excludes: findings already folded into an issued cluster (``issued_state``
    other than the open-debt sentinel) and findings aged out past the TTL.
    """
    kept = []
    for r in rows:
        if r.get("issued_state") != "open_debt":
            continue
        if is_expired(r.get("last_seen_at", now), now=now, ttl_days=ttl_days):
            continue
        kept.append(r)
    return kept


def cluster_weight(rows: list[dict[str, Any]]) -> float:
    """Weighted count for a set of finding rows: Σ(weight × seen_count) (AC4)."""
    total = 0.0
    for r in rows:
        weight = r.get("weight")
        if weight is None:
            weight = severity_weight(str(r.get("severity", "")))
        total += float(weight) * int(r.get("seen_count", 1))
    return total


def should_create_issue(rows: list[dict[str, Any]], threshold: float | None = None) -> bool:
    """True iff a cluster's weighted count has reached the threshold (AC4)."""
    n = CONFIG["cluster_threshold"] if threshold is None else threshold
    return cluster_weight(rows) >= n


# ── I/O layer (env-guarded; not part of the pure unit surface) ─────────────


def _now_iso() -> str:
    return _dt.datetime.now(tz=_dt.timezone.utc).isoformat()


def _rest_headers(key: str) -> dict[str, str]:
    return {
        "apikey": key,
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
    }


def upsert_finding(
    finding: dict[str, Any], pr_ref: str, *, now: str, http, base_url: str, key: str
) -> dict[str, Any]:
    """Upsert one finding into ``review_debt`` via the ON-CONFLICT RPC (AC3).

    Calls ``review_debt_upsert`` so an existing ``dedup_key`` has its
    ``seen_count`` incremented server-side (PostgREST merge-duplicates cannot
    express ``seen_count + 1``) and a new finding is inserted with seen_count=1.
    ``http`` is an injected client exposing ``.post`` (httpx-shaped) so the call
    is testable without a live DB.
    """
    severity = str(finding.get("severity", ""))
    params = {
        "p_dedup_key": dedup_key(finding),
        "p_module_area": module_area(str(finding.get("file", ""))),
        "p_severity": severity,
        "p_weight": severity_weight(severity),
        "p_rule": str(finding.get("rule", "")),
        "p_file": str(finding.get("file", "")),
        "p_source_pr": pr_ref,
        "p_seen_at": now,
    }
    url = f"{base_url}/rest/v1/rpc/review_debt_upsert"
    resp = http.post(url, headers=_rest_headers(key), json=params)
    resp.raise_for_status()
    data = resp.json()
    if isinstance(data, list):
        return data[0] if data else params
    return data if isinstance(data, dict) else params


def fetch_active_cluster(
    area: str, *, now: str, http, base_url: str, key: str, ttl_days: int | None = None
) -> list[dict[str, Any]]:
    """Fetch findings in ``area`` that still count toward a cluster (AC4/AC5).

    Queries only ``issued_state=open_debt`` rows for the module area, then
    applies the TTL age-out in Python (``active_findings``) so the threshold
    math matches the unit-tested pure core exactly.
    """
    table = CONFIG["table"]
    url = f"{base_url}/rest/v1/{table}?module_area=eq.{area}&issued_state=eq.open_debt&select=*"
    resp = http.get(url, headers=_rest_headers(key))
    resp.raise_for_status()
    rows = resp.json() or []
    return active_findings(rows, now=now, ttl_days=ttl_days)


def mark_clustered(area: str, issue_number: int, *, http, base_url: str, key: str) -> None:
    """Fold an area's open findings into a cluster issue (AC5/AC7).

    Flips ``issued_state`` to ``clustered`` and stamps the issue number so those
    findings are excluded from future cluster counts and never double-issued.
    """
    table = CONFIG["table"]
    url = f"{base_url}/rest/v1/{table}?module_area=eq.{area}&issued_state=eq.open_debt"
    resp = http.patch(
        url,
        headers=_rest_headers(key),
        json={"issued_state": "clustered", "cluster_issue": issue_number},
    )
    resp.raise_for_status()


def find_open_cluster_issue(area: str, existing: list[dict[str, Any]]) -> int | None:
    """Return the number of an already-open cluster issue for ``area`` (AC7).

    ``existing`` is the list of open issues carrying the ``review-debt-cluster``
    label (fetched by the caller). Idempotency: matching on the ``module_area``
    marker line the collector writes into every cluster-issue body means a
    concurrent or re-run invocation appends rather than double-creates.
    """
    marker = cluster_issue_marker(area)
    for issue in existing:
        if marker in (issue.get("body") or ""):
            return issue.get("number")
    return None


def cluster_issue_marker(area: str) -> str:
    """Hidden idempotency marker embedded in every cluster-issue body (AC7)."""
    return f"<!-- review-debt-cluster:{area} -->"


def resolve_milestone(open_milestones: list[dict[str, Any]]) -> str | None:
    """Pick a milestone for the auto-created issue, or None → triage (AC8).

    Heuristic: the open milestone with the nearest due date; failing any due
    date, the most recently created. None when there are no open milestones —
    the caller then adds a ``needs-triage`` marker instead of a milestone.
    """
    if not open_milestones:
        return None
    dated = [m for m in open_milestones if m.get("due_on")]
    if dated:
        return min(dated, key=lambda m: m["due_on"]).get("title")
    return open_milestones[-1].get("title")


def write_heartbeat(
    pr_ref: str, findings_count: int, *, now: str, http, base_url: str, key: str
) -> None:
    """Emit a heartbeat row to the legacy ``events`` table (AC9).

    A silent death (timeout, crash) leaves no heartbeat, so absence is
    detectable. Uses the legacy table because events_canonical's anon INSERT is
    gated to ``actor LIKE 'sandcastle:%'`` and this runs as CI.
    """
    url = f"{base_url}/rest/v1/{CONFIG['heartbeat_table']}"
    payload = {
        "event_type": "review_debt_collected",
        "severity": "info",
        "repo": os.environ.get("GITHUB_REPOSITORY", ""),
        "source": "review-debt-collector",
        "title": f"review-debt collector ran on {pr_ref}",
        "payload": {"pr": pr_ref, "findings": findings_count},
        "event_at": now,
    }
    resp = http.post(url, headers=_rest_headers(key), json=payload)
    resp.raise_for_status()


def _gh_json(args: list[str]) -> Any:  # pragma: no cover - subprocess glue
    import json as _json
    import subprocess

    out = subprocess.run(["gh", *args], check=True, capture_output=True, text=True).stdout.strip()
    return _json.loads(out) if out else None


def _ensure_cluster_issue(  # pragma: no cover - I/O glue
    area: str, rows: list[dict[str, Any]], repo: str, http, base_url: str, key: str
) -> None:
    """Idempotently create OR append the cluster issue for ``area`` (AC4/7/8)."""
    weight = cluster_weight(rows)
    open_issues = (
        _gh_json(
            [
                "issue",
                "list",
                "--repo",
                repo,
                "--label",
                "review-debt-cluster",
                "--state",
                "open",
                "--json",
                "number,body",
                "--limit",
                "200",
            ]
        )
        or []
    )
    existing = find_open_cluster_issue(area, open_issues)
    files = sorted({r.get("file", "") for r in rows})
    body_lines = [
        cluster_issue_marker(area),
        f"Auto-filed by the review-debt collector (#1211). Module area **`{area}`** "
        f"crossed the cluster threshold (weighted count {weight:g} ≥ "
        f"{CONFIG['cluster_threshold']:g}).",
        "",
        "Recurring sub-MAJOR code-review findings in this area:",
        *[f"- `{f}`" for f in files],
        "",
        "These are MEDIUM/INFO findings that individually never blocked a merge "
        "but have accumulated. Triage together.",
    ]
    body = "\n".join(body_lines)

    if existing:
        import subprocess

        subprocess.run(
            [
                "gh",
                "issue",
                "comment",
                str(existing),
                "--repo",
                repo,
                "--body",
                f"Cluster weight now {weight:g}. Latest files:\n"
                + "\n".join(f"- `{f}`" for f in files),
            ],
            check=True,
        )
        mark_clustered(area, existing, http=http, base_url=base_url, key=key)
        print(f"review-debt: appended to existing cluster issue #{existing} for {area}")
        return

    milestones = _gh_json(["api", f"repos/{repo}/milestones?state=open"]) or []
    milestone = resolve_milestone(milestones)
    cmd = [
        "gh",
        "issue",
        "create",
        "--repo",
        repo,
        "--title",
        f"Review-debt cluster: {area}",
        "--body",
        body,
        "--label",
        ",".join(CONFIG["issue_labels"]),
    ]
    if milestone:
        cmd += ["--milestone", milestone]
    else:
        cmd += ["--label", "needs-triage"]
    import subprocess

    url = subprocess.run(cmd, check=True, capture_output=True, text=True).stdout.strip()
    number = int(url.rstrip("/").rsplit("/", 1)[-1])
    mark_clustered(area, number, http=http, base_url=base_url, key=key)
    print(f"review-debt: created cluster issue #{number} for {area} (weight {weight:g})")


def main(argv: list[str] | None = None) -> int:  # pragma: no cover - I/O glue
    """CLI entrypoint invoked by review-debt-collector.yml.

    Reads the review comment body from ``$REVIEW_COMMENT_BODY`` (or a file named
    by ``$REVIEW_COMMENT_BODY_FILE``), skips on a blocking finding (AC6), then
    upserts findings, re-clusters each affected area, and writes a heartbeat.
    Fail-loud: any unexpected error propagates a non-zero exit (the workflow has
    ``timeout-minutes: 5`` and no ``continue-on-error``, AC9).
    """
    body = os.environ.get("REVIEW_COMMENT_BODY", "")
    body_file = os.environ.get("REVIEW_COMMENT_BODY_FILE")
    if body_file and os.path.exists(body_file):
        with open(body_file, encoding="utf-8") as fh:
            body = fh.read()

    repo = os.environ.get("GITHUB_REPOSITORY", "")
    pr_ref = os.environ.get("PR_REF") or f"{repo}#{os.environ.get('PR_NUMBER', '?')}"
    base_url = os.environ["SUPABASE_URL"].rstrip("/")
    key = os.environ["SUPABASE_ANON_KEY"]
    now = _now_iso()

    import httpx

    if has_blocking_finding(body):
        print("review-debt: blocking finding present — skipping collection (AC6)")
        with httpx.Client(timeout=20) as http:
            write_heartbeat(pr_ref, 0, now=now, http=http, base_url=base_url, key=key)
        return 0

    findings = parse_findings_block(body)
    print(f"review-debt: parsed {len(findings)} collectable finding(s)")

    with httpx.Client(timeout=20) as http:
        areas: set[str] = set()
        for f in findings:
            upsert_finding(f, pr_ref, now=now, http=http, base_url=base_url, key=key)
            areas.add(module_area(str(f.get("file", ""))))

        for area in sorted(areas):
            rows = fetch_active_cluster(area, now=now, http=http, base_url=base_url, key=key)
            if should_create_issue(rows):
                _ensure_cluster_issue(area, rows, repo, http, base_url, key)

        write_heartbeat(pr_ref, len(findings), now=now, http=http, base_url=base_url, key=key)

    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
