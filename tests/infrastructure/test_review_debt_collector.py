"""Unit tests for the review-debt collector pure-logic core (#1211).

The collector is a deterministic (no-LLM) CI module that, on each merged PR,
parses the code-review plugin's structured JSON findings block, persists
sub-MAJOR findings to the `review_debt` Supabase table with weighted dedup,
clusters them by parent-directory `module_area`, and auto-creates one GitHub
issue when a cluster reaches threshold.

This file pins the *pure* layer — parsing, dedup-key derivation, module-area
bucketing, severity weighting, cluster threshold, blocking-skip, and TTL
age-out. The I/O layer (PostgREST upsert, issue create, heartbeat) and the
#326 workflow meta-test live elsewhere. Every test traces to an acceptance
criterion bullet from the issue body (marked `AC<n>`).
"""

from __future__ import annotations

import json

import pytest

from review_debt_collector import (
    CONFIG,
    active_findings,
    cluster_weight,
    dedup_key,
    has_blocking_finding,
    is_expired,
    module_area,
    parse_findings_block,
    severity_weight,
    should_create_issue,
)


# ── AC2: parse the plugin's JSON findings block ────────────────────────────


def _wrap(payload: dict) -> str:
    return f"Some prose.\n\n<!-- code-review-findings\n{json.dumps(payload)}\n-->\n\nMore."


def test_parse_extracts_findings_from_html_comment_block():
    body = _wrap(
        {
            "schema_version": 1,
            "findings": [
                {
                    "severity": "MEDIUM",
                    "rule": "diff-coherence",
                    "file": "scripts/foo/bar.py",
                    "line": 42,
                    "description": "free text that must not affect dedup",
                },
                {
                    "severity": "INFO",
                    "rule": "simplification-scout",
                    "file": "src/util.py",
                    "line": 7,
                    "description": "nit",
                },
            ],
        }
    )
    findings = parse_findings_block(body)
    assert len(findings) == 2
    assert findings[0]["rule"] == "diff-coherence"
    assert findings[1]["severity"] == "INFO"


def test_parse_returns_empty_on_missing_block():
    # Degradation contract: no block → zero findings, never raise.
    assert parse_findings_block("no marker here at all") == []


def test_parse_returns_empty_on_unparseable_json():
    body = "<!-- code-review-findings\n{not valid json,,,}\n-->"
    assert parse_findings_block(body) == []


def test_parse_returns_empty_on_unknown_schema_version():
    body = _wrap({"schema_version": 99, "findings": [{"severity": "MEDIUM"}]})
    assert parse_findings_block(body) == []


def test_parse_handles_empty_findings_list():
    body = _wrap({"schema_version": 1, "findings": []})
    assert parse_findings_block(body) == []


# ── AC4: module_area is the parent directory ───────────────────────────────


@pytest.mark.parametrize(
    "path,expected",
    [
        ("scripts/foo/bar.py", "scripts/foo"),
        ("src/review_debt.py", "src"),
        ("README.md", "."),
        ("a/b/c/d.ts", "a/b/c"),
        ("scripts\\win\\path.py", "scripts/win"),  # backslash normalised
    ],
)
def test_module_area_is_parent_dir(path, expected):
    assert module_area(path) == expected


# ── AC2: dedup key excludes free-text description AND line number ───────────


def test_dedup_key_excludes_description_and_line():
    a = {
        "severity": "MEDIUM",
        "rule": "bug-scan",
        "file": "scripts/x/y.py",
        "line": 10,
        "description": "first wording",
    }
    b = {
        "severity": "MEDIUM",
        "rule": "bug-scan",
        "file": "scripts/x/y.py",
        "line": 999,
        "description": "totally different wording",
    }
    # AC3: cross-PR repeats collapse to a single row → identical dedup key.
    assert dedup_key(a) == dedup_key(b)


def test_dedup_key_differs_on_rule_file_or_area():
    base = {"severity": "MEDIUM", "rule": "bug-scan", "file": "scripts/x/y.py", "line": 1}
    diff_rule = {**base, "rule": "diff-coherence"}
    diff_file = {**base, "file": "scripts/x/z.py"}
    keys = {dedup_key(base), dedup_key(diff_rule), dedup_key(diff_file)}
    assert len(keys) == 3


# ── AC1/AC4: severity weighting; MEDIUM and INFO can differ ─────────────────


def test_severity_weight_medium_higher_than_info():
    assert severity_weight("MEDIUM") > severity_weight("INFO")


def test_severity_weight_unknown_falls_back_low():
    assert severity_weight("WHATEVER") == CONFIG["default_weight"]


# ── AC4: cluster weighted count + threshold ────────────────────────────────


def _row(
    area="scripts/x",
    rule="bug-scan",
    file="scripts/x/y.py",
    severity="MEDIUM",
    seen_count=1,
    issued_state="open_debt",
    last_seen_at="2026-07-21T00:00:00Z",
):
    return {
        "module_area": area,
        "rule": rule,
        "file": file,
        "severity": severity,
        "weight": severity_weight(severity),
        "seen_count": seen_count,
        "issued_state": issued_state,
        "last_seen_at": last_seen_at,
    }


def test_cluster_weight_sums_weight_times_seen_count():
    rows = [_row(seen_count=2), _row(rule="diff-coherence", seen_count=1)]
    # 1.0*2 + 1.0*1 = 3.0
    assert cluster_weight(rows) == pytest.approx(3.0)


def test_should_create_issue_at_threshold():
    # Four distinct MEDIUM findings, weight 1.0 each → 4.0 == N=4.
    rows = [_row(rule=f"r{i}", file=f"scripts/x/f{i}.py") for i in range(4)]
    assert should_create_issue(rows) is True


def test_should_not_create_issue_below_threshold():
    rows = [_row(rule=f"r{i}", file=f"scripts/x/f{i}.py") for i in range(3)]
    assert should_create_issue(rows) is False


def test_info_only_cluster_needs_more_to_reach_threshold():
    # INFO weight 0.5 → need 8 to hit 4.0; 4 INFO findings = 2.0, below.
    rows = [_row(rule=f"r{i}", severity="INFO") for i in range(4)]
    assert should_create_issue(rows) is False


# ── AC6: skip entirely when a merge-blocking finding is present ────────────


@pytest.mark.parametrize(
    "body",
    [
        "### CRITICAL findings\nsomething",
        "## MAJOR\nboom",
        "#### 🔴 BLOCKING\nnope",
        "### MEDIUM\nstate-corrupting in the moment",  # #1385 follow-up
    ],
)
def test_has_blocking_finding_true_for_allcaps_severity_headings(body):
    assert has_blocking_finding(body) is True


@pytest.mark.parametrize(
    "body",
    [
        "### Blocking issues — None",  # title-case prose, not a block
        "### MINOR\njust a nit",
        "No issues found.",
        "Found 3 issues:",
        "Medium priority follow-up recommended.",  # title-case advisory prose
    ],
)
def test_has_blocking_finding_false_for_nonblocking(body):
    assert has_blocking_finding(body) is False


def test_json_block_medium_severity_still_collected_after_gate_promotion():
    # #1385 follow-up: the merge gate now blocks on a prose "### MEDIUM"
    # heading, but the JSON findings-block `severity` field is a distinct,
    # bucket-derived signal (§8.1) that never gates the merge. A merged PR's
    # comment can legitimately carry `"severity": "MEDIUM"` findings without
    # ever tripping has_blocking_finding — collected_severities must still
    # include MEDIUM.
    body = _wrap(
        {
            "schema_version": 1,
            "findings": [
                {
                    "severity": "MEDIUM",
                    "rule": "bug-scan",
                    "file": "scripts/x/y.py",
                    "line": 1,
                    "description": "d",
                }
            ],
        }
    )
    assert has_blocking_finding(body) is False
    assert len(parse_findings_block(body)) == 1


# ── AC5: TTL age-out + issued-state exclusion ──────────────────────────────


def test_is_expired_past_ttl():
    assert is_expired("2026-06-01T00:00:00Z", now="2026-07-21T00:00:00Z") is True


def test_is_expired_within_ttl():
    assert is_expired("2026-07-10T00:00:00Z", now="2026-07-21T00:00:00Z") is False


def test_active_findings_drops_expired_and_issued():
    now = "2026-07-21T00:00:00Z"
    fresh = _row(rule="fresh", last_seen_at="2026-07-20T00:00:00Z")
    stale = _row(rule="stale", last_seen_at="2026-05-01T00:00:00Z")
    already_issued = _row(rule="issued", issued_state="clustered")
    kept = active_findings([fresh, stale, already_issued], now=now)
    assert kept == [fresh]


# ── I/O layer: injected fake client (no live DB / GitHub) ──────────────────

from review_debt_collector import (  # noqa: E402
    cluster_issue_marker,
    fetch_active_cluster,
    find_open_cluster_issue,
    resolve_milestone,
    upsert_finding,
    write_heartbeat,
)


class _Resp:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


class _FakeHttp:
    """Records calls; returns a queued payload per verb."""

    def __init__(self, get=None, post=None):
        self.calls = []
        self._get = get if get is not None else []
        self._post = post if post is not None else {}

    def get(self, url, headers=None):
        self.calls.append(("GET", url))
        return _Resp(self._get)

    def post(self, url, headers=None, json=None):
        self.calls.append(("POST", url, json))
        return _Resp(self._post)

    def patch(self, url, headers=None, json=None):
        self.calls.append(("PATCH", url, json))
        return _Resp([])


def test_upsert_finding_calls_the_rpc_with_named_params():
    # AC3: increment lives in the RPC, so the I/O layer must hit rpc/, not the
    # bare table (a table POST could never increment seen_count).
    http = _FakeHttp(post={"dedup_key": "k", "seen_count": 2})
    finding = {
        "severity": "MEDIUM",
        "rule": "bug-scan",
        "file": "scripts/x/y.py",
        "line": 3,
        "description": "w",
    }
    out = upsert_finding(
        finding,
        "Osasuwu/jarvis#123",
        now="2026-07-21T00:00:00Z",
        http=http,
        base_url="https://db",
        key="anon",
    )
    verb, url, body = http.calls[0]
    assert verb == "POST"
    assert url.endswith("/rest/v1/rpc/review_debt_upsert")
    assert body["p_dedup_key"] == dedup_key(finding)
    assert body["p_severity"] == "MEDIUM"
    assert out["seen_count"] == 2


def test_fetch_active_cluster_filters_area_and_applies_ttl():
    # AC4/AC5: query is scoped to open_debt rows for the area; TTL age-out is
    # applied on top so the count matches the pure core.
    rows = [
        {
            "module_area": "scripts/x",
            "severity": "MEDIUM",
            "weight": 1.0,
            "seen_count": 1,
            "issued_state": "open_debt",
            "last_seen_at": "2026-07-20T00:00:00Z",
        },
        {
            "module_area": "scripts/x",
            "severity": "MEDIUM",
            "weight": 1.0,
            "seen_count": 1,
            "issued_state": "open_debt",
            "last_seen_at": "2026-05-01T00:00:00Z",
        },  # aged out
    ]
    http = _FakeHttp(get=rows)
    kept = fetch_active_cluster(
        "scripts/x", now="2026-07-21T00:00:00Z", http=http, base_url="https://db", key="anon"
    )
    assert len(kept) == 1
    _, url = http.calls[0]
    assert "module_area=eq.scripts/x" in url
    assert "issued_state=eq.open_debt" in url


def test_find_open_cluster_issue_matches_on_marker():
    # AC7: idempotency via the hidden marker in the issue body.
    area = "scripts/x"
    issues = [
        {"number": 10, "body": "unrelated"},
        {"number": 42, "body": f"debt\n{cluster_issue_marker(area)}\nmore"},
    ]
    assert find_open_cluster_issue(area, issues) == 42
    assert find_open_cluster_issue("other/area", issues) is None


def test_resolve_milestone_prefers_nearest_due_then_none():
    # AC8: nearest due date wins; empty list → None (caller marks triage).
    milestones = [
        {"title": "later", "due_on": "2026-12-01T00:00:00Z"},
        {"title": "soon", "due_on": "2026-08-01T00:00:00Z"},
    ]
    assert resolve_milestone(milestones) == "soon"
    assert resolve_milestone([]) is None


def test_write_heartbeat_targets_legacy_events_table():
    # AC9: heartbeat lands in the legacy `events` table (events_canonical anon
    # insert is sandcastle-gated), so silent death is detectable.
    http = _FakeHttp(post={})
    write_heartbeat(
        "Osasuwu/jarvis#123",
        5,
        now="2026-07-21T00:00:00Z",
        http=http,
        base_url="https://db",
        key="anon",
    )
    verb, url, body = http.calls[0]
    assert url.endswith("/rest/v1/events")
    assert body["event_type"] == "review_debt_collected"
    assert body["payload"]["findings"] == 5
