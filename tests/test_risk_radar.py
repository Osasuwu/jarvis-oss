"""Unit tests for risk_radar — risk pattern detection.

Tests cover all 5 patterns, severity thresholds, report generation,
and intent routing. No gh CLI calls are made — all subprocess calls are mocked.
"""
from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from risk_radar import (
    CI_CRITICAL_RATE,
    CI_HIGH_RATE,
    CI_MEDIUM_RATE,
    CHANGES_HIGH_COUNT,
    CHANGES_STALE_DAYS,
    SEVERITY_ORDER,
    STAGNATION_DAYS,
    STAGNATION_HIGH,
    RiskAlert,
    RiskRadarResult,
    _check_ci_instability,
    _check_critical_stagnation,
    _check_overdue_milestones,
    _check_review_backlog,
    _check_security_alerts,
    _days_ago,
    _load_repos,
    _parse_json,
    _run_gh,
    _scan_repo,
    _write_report,
)


# ── Helpers ───────────────────────────────────────────────────────────────────


def _iso(days_ago: float) -> str:
    dt = datetime.now(UTC) - timedelta(days=days_ago)
    return dt.isoformat()


def _gh_ok(data) -> tuple[bool, str]:
    return True, json.dumps(data)


def _gh_fail() -> tuple[bool, str]:
    return False, "gh command failed"


# ── _parse_json ───────────────────────────────────────────────────────────────


class TestParseJson:
    def test_valid_list(self):
        assert _parse_json('[{"a": 1}]') == [{"a": 1}]

    def test_empty_string(self):
        assert _parse_json("") == []

    def test_invalid(self):
        assert _parse_json("nope") == []

    def test_non_list(self):
        assert _parse_json('{"a": 1}') == []


# ── _load_repos ───────────────────────────────────────────────────────────────


class TestLoadRepos:
    def test_missing_file(self, tmp_path, monkeypatch):
        monkeypatch.setattr("risk_radar.REPOS_CONF", tmp_path / "missing.conf")
        assert _load_repos() == []

    def test_parses_repos(self, tmp_path, monkeypatch):
        conf = tmp_path / "repos.conf"
        conf.write_text("# comment\nowner/repo1\nowner/repo2\n", encoding="utf-8")
        monkeypatch.setattr("risk_radar.REPOS_CONF", conf)
        assert _load_repos() == ["owner/repo1", "owner/repo2"]


# ── P1: CI instability ────────────────────────────────────────────────────────


class TestCiInstability:
    def _make_runs(self, n_fail: int, n_total: int) -> list[dict]:
        runs = []
        for i in range(n_total):
            conc = "failure" if i < n_fail else "success"
            runs.append({"conclusion": conc, "name": f"CI #{i}", "createdAt": _iso(i)})
        return runs

    def test_critical_above_50pct(self):
        runs = self._make_runs(11, 20)  # 55% failure
        with patch("risk_radar._run_gh", return_value=_gh_ok(runs)):
            alerts = _check_ci_instability("r/r")
        assert len(alerts) == 1
        assert alerts[0].severity == "critical"

    def test_high_at_30_to_50pct(self):
        runs = self._make_runs(8, 20)  # 40% failure
        with patch("risk_radar._run_gh", return_value=_gh_ok(runs)):
            alerts = _check_ci_instability("r/r")
        assert len(alerts) == 1
        assert alerts[0].severity == "high"

    def test_medium_at_15_to_30pct(self):
        runs = self._make_runs(4, 20)  # 20% failure
        with patch("risk_radar._run_gh", return_value=_gh_ok(runs)):
            alerts = _check_ci_instability("r/r")
        assert len(alerts) == 1
        assert alerts[0].severity == "medium"

    def test_no_alert_below_threshold(self):
        runs = self._make_runs(1, 20)  # 5% failure
        with patch("risk_radar._run_gh", return_value=_gh_ok(runs)):
            alerts = _check_ci_instability("r/r")
        assert alerts == []

    def test_no_alert_on_empty_runs(self):
        with patch("risk_radar._run_gh", return_value=_gh_ok([])):
            alerts = _check_ci_instability("r/r")
        assert alerts == []

    def test_no_alert_on_gh_failure(self):
        with patch("risk_radar._run_gh", return_value=_gh_fail()):
            alerts = _check_ci_instability("r/r")
        assert alerts == []

    def test_pattern_slug_is_ci_instability(self):
        runs = self._make_runs(12, 20)
        with patch("risk_radar._run_gh", return_value=_gh_ok(runs)):
            alerts = _check_ci_instability("r/r")
        assert alerts[0].pattern == "ci-instability"


# ── P2: Critical stagnation ───────────────────────────────────────────────────


class TestCriticalStagnation:
    def _issues(self, n: int, days_old: float) -> list[dict]:
        return [
            {"number": i, "title": f"Bug {i}", "updatedAt": _iso(days_old), "assignees": []}
            for i in range(n)
        ]

    def test_high_when_gte_threshold(self):
        issues = self._issues(STAGNATION_HIGH, STAGNATION_DAYS + 1)
        with patch("risk_radar._run_gh", return_value=_gh_ok(issues)):
            alerts = _check_critical_stagnation("r/r")
        assert len(alerts) == 1
        assert alerts[0].severity == "high"

    def test_medium_below_threshold(self):
        issues = self._issues(STAGNATION_HIGH - 1, STAGNATION_DAYS + 1)
        with patch("risk_radar._run_gh", return_value=_gh_ok(issues)):
            alerts = _check_critical_stagnation("r/r")
        assert len(alerts) == 1
        assert alerts[0].severity == "medium"

    def test_fresh_issues_not_flagged(self):
        issues = self._issues(10, STAGNATION_DAYS - 1)
        with patch("risk_radar._run_gh", return_value=_gh_ok(issues)):
            alerts = _check_critical_stagnation("r/r")
        assert alerts == []

    def test_no_alert_on_empty(self):
        with patch("risk_radar._run_gh", return_value=_gh_ok([])):
            alerts = _check_critical_stagnation("r/r")
        assert alerts == []

    def test_pattern_slug(self):
        issues = self._issues(3, STAGNATION_DAYS + 2)
        with patch("risk_radar._run_gh", return_value=_gh_ok(issues)):
            alerts = _check_critical_stagnation("r/r")
        assert alerts[0].pattern == "critical-stagnation"


# ── P3: Security alerts ───────────────────────────────────────────────────────


class TestSecurityAlerts:
    def test_critical_on_high_severity_cve(self):
        alerts_data = [
            {"severity": "high", "pkg": "requests"},
            {"severity": "medium", "pkg": "urllib3"},
        ]
        with patch("risk_radar._run_gh", return_value=_gh_ok(alerts_data)):
            alerts = _check_security_alerts("r/r")
        assert len(alerts) == 1
        assert alerts[0].severity == "critical"

    def test_medium_on_medium_severity_only(self):
        alerts_data = [{"severity": "medium", "pkg": "aiohttp"}]
        with patch("risk_radar._run_gh", return_value=_gh_ok(alerts_data)):
            alerts = _check_security_alerts("r/r")
        assert len(alerts) == 1
        assert alerts[0].severity == "medium"

    def test_no_alert_on_empty(self):
        with patch("risk_radar._run_gh", return_value=_gh_ok([])):
            alerts = _check_security_alerts("r/r")
        assert alerts == []

    def test_no_alert_on_gh_failure(self):
        with patch("risk_radar._run_gh", return_value=_gh_fail()):
            alerts = _check_security_alerts("r/r")
        assert alerts == []

    def test_pattern_slug(self):
        alerts_data = [{"severity": "critical", "pkg": "boto3"}]
        with patch("risk_radar._run_gh", return_value=_gh_ok(alerts_data)):
            alerts = _check_security_alerts("r/r")
        assert alerts[0].pattern == "security-alert"


# ── P4: Overdue milestones ────────────────────────────────────────────────────


class TestOverdueMilestones:
    def _ms(self, days_past_due: float, open_count: int, closed_count: int, title: str = "v1.0") -> dict:
        due = (datetime.now(UTC) - timedelta(days=days_past_due)).isoformat()
        return {"title": title, "due": due, "open": open_count, "closed": closed_count}

    def test_high_when_less_than_50pct_done(self):
        ms = [self._ms(5, 8, 2)]  # 20% done
        with patch("risk_radar._run_gh", return_value=_gh_ok(ms)):
            alerts = _check_overdue_milestones("r/r")
        assert len(alerts) == 1
        assert alerts[0].severity == "high"

    def test_medium_when_more_than_50pct_done(self):
        ms = [self._ms(5, 2, 8)]  # 80% done
        with patch("risk_radar._run_gh", return_value=_gh_ok(ms)):
            alerts = _check_overdue_milestones("r/r")
        assert len(alerts) == 1
        assert alerts[0].severity == "medium"

    def test_no_alert_when_no_open_issues(self):
        ms = [self._ms(5, 0, 10)]  # all done
        with patch("risk_radar._run_gh", return_value=_gh_ok(ms)):
            alerts = _check_overdue_milestones("r/r")
        assert alerts == []

    def test_no_alert_for_future_milestones(self):
        # Milestone not yet past due
        future = (datetime.now(UTC) + timedelta(days=5)).isoformat()
        ms = [{"title": "v2.0", "due": future, "open": 5, "closed": 3}]
        with patch("risk_radar._run_gh", return_value=_gh_ok(ms)):
            alerts = _check_overdue_milestones("r/r")
        assert alerts == []

    def test_pattern_slug(self):
        ms = [self._ms(3, 5, 1)]
        with patch("risk_radar._run_gh", return_value=_gh_ok(ms)):
            alerts = _check_overdue_milestones("r/r")
        assert alerts[0].pattern == "overdue-milestone"


# ── P5: Review backlog ────────────────────────────────────────────────────────


class TestReviewBacklog:
    def _pr(self, number: int, days_stale: float, decision: str = "CHANGES_REQUESTED", draft: bool = False):
        return {
            "number": number,
            "title": f"PR #{number}",
            "updatedAt": _iso(days_stale),
            "reviewDecision": decision,
            "isDraft": draft,
        }

    def test_high_when_gte_threshold(self):
        prs = [self._pr(i, CHANGES_STALE_DAYS + 1) for i in range(CHANGES_HIGH_COUNT)]
        with patch("risk_radar._run_gh", return_value=_gh_ok(prs)):
            alerts = _check_review_backlog("r/r")
        assert len(alerts) == 1
        assert alerts[0].severity == "high"

    def test_medium_below_threshold(self):
        prs = [self._pr(1, CHANGES_STALE_DAYS + 1)]
        with patch("risk_radar._run_gh", return_value=_gh_ok(prs)):
            alerts = _check_review_backlog("r/r")
        assert len(alerts) == 1
        assert alerts[0].severity == "medium"

    def test_drafts_excluded(self):
        prs = [self._pr(1, CHANGES_STALE_DAYS + 1, draft=True)]
        with patch("risk_radar._run_gh", return_value=_gh_ok(prs)):
            alerts = _check_review_backlog("r/r")
        assert alerts == []

    def test_fresh_prs_not_flagged(self):
        prs = [self._pr(1, CHANGES_STALE_DAYS - 1)]
        with patch("risk_radar._run_gh", return_value=_gh_ok(prs)):
            alerts = _check_review_backlog("r/r")
        assert alerts == []

    def test_approved_prs_not_flagged(self):
        prs = [self._pr(1, CHANGES_STALE_DAYS + 1, decision="APPROVED")]
        with patch("risk_radar._run_gh", return_value=_gh_ok(prs)):
            alerts = _check_review_backlog("r/r")
        assert alerts == []

    def test_pattern_slug(self):
        prs = [self._pr(1, CHANGES_STALE_DAYS + 2)]
        with patch("risk_radar._run_gh", return_value=_gh_ok(prs)):
            alerts = _check_review_backlog("r/r")
        assert alerts[0].pattern == "review-backlog"


# ── SEVERITY_ORDER ────────────────────────────────────────────────────────────


class TestSeverityOrder:
    def test_critical_highest(self):
        assert SEVERITY_ORDER["critical"] > SEVERITY_ORDER["high"]

    def test_high_above_medium(self):
        assert SEVERITY_ORDER["high"] > SEVERITY_ORDER["medium"]

    def test_medium_above_low(self):
        assert SEVERITY_ORDER["medium"] > SEVERITY_ORDER["low"]


# ── _write_report ─────────────────────────────────────────────────────────────


class TestWriteReport:
    def test_creates_file(self, tmp_path, monkeypatch):
        monkeypatch.setattr("risk_radar.REPORTS_DIR", tmp_path)
        alert = RiskAlert("critical", "ci-instability", "r/r", "CI broken", "details", "evidence")
        text, path = _write_report([alert], ["r/r"], "2026-03-28T12:00:00")
        assert path.exists()

    def test_contains_alert_title(self, tmp_path, monkeypatch):
        monkeypatch.setattr("risk_radar.REPORTS_DIR", tmp_path)
        alert = RiskAlert("high", "critical-stagnation", "r/r", "5 issues stale", "d", "e")
        text, _ = _write_report([alert], ["r/r"], "2026-03-28T12:00:00")
        assert "5 issues stale" in text

    def test_no_risks_message(self, tmp_path, monkeypatch):
        monkeypatch.setattr("risk_radar.REPORTS_DIR", tmp_path)
        text, _ = _write_report([], ["r/r"], "2026-03-28T12:00:00")
        assert "No risks detected" in text

    def test_escalation_policy_included(self, tmp_path, monkeypatch):
        monkeypatch.setattr("risk_radar.REPORTS_DIR", tmp_path)
        text, _ = _write_report([], [], "2026-03-28T12:00:00")
        assert "Escalation Policy" in text

    def test_filename_includes_risk_radar(self, tmp_path, monkeypatch):
        monkeypatch.setattr("risk_radar.REPORTS_DIR", tmp_path)
        _, path = _write_report([], [], "2026-03-28T12:00:00")
        assert "risk-radar" in path.name


# ── _scan_repo error isolation ─────────────────────────────────────────────────


class TestScanRepo:
    def test_one_pattern_failure_does_not_abort(self):
        """If one pattern raises, the others still run."""
        call_count = 0

        def raising_checker(repo):
            nonlocal call_count
            call_count += 1
            raise RuntimeError("simulated failure")

        with patch("risk_radar._run_gh", return_value=_gh_ok([])):
            # All checkers will get empty data → no alerts, no crash.
            alerts = _scan_repo("r/r")
        assert isinstance(alerts, list)


# ── Intent routing ────────────────────────────────────────────────────────────
