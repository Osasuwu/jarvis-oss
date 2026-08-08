"""Meta-test for the review-debt collector (#1211), per the #326 convention.

Two dimensions:

  * **Config** — pin the canonical wiring so a rename can't silently unhook the
    collector: the workflow runs `scripts/review_debt_collector.py`, is bounded
    (`timeout-minutes: 5`), fail-loud (no `continue-on-error`), only fires on a
    merged PR, and the parser's JSON-block marker (`code-review-findings`) is the
    canonical one the plugin emits. Both tunables (cluster N, TTL days) live in
    one CONFIG block.

  * **Logic** — reimplement the threshold / dedup-key / blocking-skip decision
    rule in pure Python here and assert it agrees with the shipped module across
    a battery of cases. If the module's rule drifts from this independent mirror,
    CI goes red.

The workflow is event-triggered (not path-filtered), so #326 does not strictly
mandate this file — but AC10 of the issue calls for it explicitly, and the
threshold/dedup/skip rule is exactly the kind of fragile decision logic the
convention exists to pin.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "review-debt-collector.yml"
SCRIPT = REPO_ROOT / "scripts" / "review_debt_collector.py"
MIGRATION = (
    REPO_ROOT / "supabase" / "migrations" / "20260721120000_create_review_debt.sql"
)
SCHEMA = REPO_ROOT / "mcp-memory" / "schema.sql"

# Import the shipped module (scripts/ is on sys.path via tests/conftest.py).
import review_debt_collector as rdc  # noqa: E402


# ── Config dimension ────────────────────────────────────────────────────────

def test_workflow_exists_and_runs_the_canonical_script():
    assert WORKFLOW.exists(), "review-debt-collector.yml missing"
    text = WORKFLOW.read_text(encoding="utf-8")
    assert "scripts/review_debt_collector.py" in text


def test_workflow_is_bounded_and_fail_loud():
    doc = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    job = doc["jobs"]["collect"]
    # AC9: bounded + no silent-swallow. Check the parsed structure, not raw text
    # (an explanatory comment legitimately names `continue-on-error`).
    assert job["timeout-minutes"] == 5
    assert "continue-on-error" not in job, "job must fail loud, not swallow"
    for step in job["steps"]:
        assert "continue-on-error" not in step, "no step may swallow errors"


def test_workflow_only_fires_on_merged_pr():
    doc = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    job = doc["jobs"]["collect"]
    assert "merged == true" in job["if"]


def test_parser_marker_is_the_canonical_plugin_block():
    # AC2/AC10: the JSON-block marker must be the one the plugin actually emits.
    assert "code-review-findings" in rdc.FINDINGS_BLOCK_RE.pattern


def test_tunables_live_in_one_config_block():
    # Both knobs configurable in ONE place.
    assert rdc.CONFIG["cluster_threshold"] == 4.0
    assert rdc.CONFIG["ttl_days"] == 30


def test_migration_and_schema_are_paired():
    # schema-drift-check contract: the table is documented in schema.sql AND has
    # an executable migration.
    assert MIGRATION.exists()
    assert "review_debt" in MIGRATION.read_text(encoding="utf-8")
    assert "review_debt" in SCHEMA.read_text(encoding="utf-8")


def test_migration_is_additive_only():
    sql = MIGRATION.read_text(encoding="utf-8").lower()
    # Additive: no destructive verbs against existing tables.
    for verb in ("drop table", "alter table memories", "delete from", "truncate"):
        assert verb not in sql, f"migration must be additive; found `{verb}`"


# ── Logic dimension: independent mirror of the decision rule ────────────────

CLUSTER_N = 4.0
WEIGHTS = {"MEDIUM": 1.0, "INFO": 0.5}
BLOCK_RE = re.compile(r"^#{1,6}[^A-Za-z0-9\n]*(?:CRITICAL|MAJOR|BLOCKING|MEDIUM)\b", re.M)


def _mirror_dedup_key(f: dict) -> str:
    file = str(f.get("file", ""))
    area = str(Path(file.replace("\\", "/")).parent).replace("\\", "/")
    return f"{area}|{f.get('rule', '')}|{file}"


def _mirror_cluster_weight(rows: list[dict]) -> float:
    return sum(
        WEIGHTS.get(r.get("severity", ""), 0.5) * int(r.get("seen_count", 1))
        for r in rows
    )


def _mirror_should_create(rows: list[dict]) -> bool:
    return _mirror_cluster_weight(rows) >= CLUSTER_N


def _mirror_is_blocking(body: str) -> bool:
    return BLOCK_RE.search(body or "") is not None


@pytest.mark.parametrize(
    "a,b,same",
    [
        # description + line differ → same key (AC2/AC3)
        ({"rule": "r", "file": "s/x/y.py", "line": 1, "description": "a"},
         {"rule": "r", "file": "s/x/y.py", "line": 9, "description": "b"}, True),
        # different rule → different key
        ({"rule": "r", "file": "s/x/y.py"},
         {"rule": "q", "file": "s/x/y.py"}, False),
        # different file → different key
        ({"rule": "r", "file": "s/x/y.py"},
         {"rule": "r", "file": "s/x/z.py"}, False),
    ],
)
def test_dedup_rule_matches_module(a, b, same):
    module_same = rdc.dedup_key(a) == rdc.dedup_key(b)
    mirror_same = _mirror_dedup_key(a) == _mirror_dedup_key(b)
    assert module_same == mirror_same == same


@pytest.mark.parametrize(
    "rows,expected",
    [
        ([{"severity": "MEDIUM", "seen_count": 1}] * 4, True),   # 4.0 == N
        ([{"severity": "MEDIUM", "seen_count": 1}] * 3, False),  # 3.0 < N
        ([{"severity": "INFO", "seen_count": 1}] * 4, False),    # 2.0 < N
        ([{"severity": "INFO", "seen_count": 1}] * 8, True),     # 4.0 == N
        ([{"severity": "MEDIUM", "seen_count": 4}], True),       # weight×count
    ],
)
def test_threshold_rule_matches_module(rows, expected):
    for r in rows:
        r.setdefault("weight", WEIGHTS.get(r["severity"], 0.5))
    assert rdc.should_create_issue(rows) == _mirror_should_create(rows) == expected


@pytest.mark.parametrize(
    "body,expected",
    [
        ("### CRITICAL\nx", True),
        ("## MAJOR findings", True),
        ("#### 🔴 BLOCKING", True),
        ("### MEDIUM\nx", True),  # #1385 follow-up: MEDIUM heading now blocks
        ("### Blocking issues — None", False),  # title-case prose
        ("### MINOR\nnit", False),
        ("No issues found.", False),
        ("Medium priority follow-up.", False),  # title-case advisory prose
    ],
)
def test_blocking_skip_rule_matches_module(body, expected):
    assert rdc.has_blocking_finding(body) == _mirror_is_blocking(body) == expected
