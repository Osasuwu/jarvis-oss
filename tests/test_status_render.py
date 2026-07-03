"""Tests for scripts/status_render.py — the deterministic /status renderer (#1018).

The renderer is a pure function over the status_digest MCP JSON
({health, detector_hits, ranking, provenance}). The default path is
0-LLM deterministic; --deep adds the deterministic full picture for the
skill to layer LLM narration onto. Every test traces to an AC bullet of
issue #1018.
"""

import sys
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from status_render import render  # noqa: E402


# ---------------------------------------------------------------------------
# Fixtures — digests in the exact shape mcp-status/server.py emits
# ---------------------------------------------------------------------------


def _hit(detector, severity, repo, num, title, desc):
    return {
        "detector": detector,
        "severity": severity,
        "repo": repo,
        "issue_number": num,
        "title": title,
        "description": desc,
    }


DIGEST_UNHEALTHY = {
    "health": {
        "ok": False,
        "reason": "Unhealthy: 2 detector hit(s) (0 critical, 2 major)",
    },
    "detector_hits": [
        _hit(
            "stale-in-progress",
            "major",
            "Osasuwu/jarvis",
            42,
            "Fix foo",
            "Issue #42 has been in-progress for 5.0 days (threshold: 3d)",
        ),
        _hit(
            "blocker-cascade",
            "major",
            "your-username/your-second-repo",
            7,
            "Root blocker",
            "Issue #7 transitively blocks 3 other issues",
        ),
    ],
    "ranking": [
        {
            "rank": 1,
            "detector_hit": _hit(
                "stale-in-progress",
                "major",
                "Osasuwu/jarvis",
                42,
                "Fix foo",
                "Issue #42 has been in-progress for 5.0 days (threshold: 3d)",
            ),
            "reason": "[MAJOR] stale-in-progress — Osasuwu/jarvis — #42",
        },
        {
            "rank": 2,
            "detector_hit": _hit(
                "blocker-cascade",
                "major",
                "your-username/your-second-repo",
                7,
                "Root blocker",
                "Issue #7 transitively blocks 3 other issues",
            ),
            "reason": "[MAJOR] blocker-cascade — your-username/your-second-repo — #7",
        },
    ],
    "provenance": {
        "jarvis": {"ran": True, "ok": True, "input_rows": 12, "age": 120.0},
        "your-second-repo": {"ran": True, "ok": True, "input_rows": 4, "age": 300.0},
    },
}


DIGEST_GREEN = {
    "health": {"ok": True, "reason": "All sources fresh, no anomalies detected"},
    "detector_hits": [],
    "ranking": [],
    "provenance": {
        "jarvis": {"ran": True, "ok": True, "input_rows": 10, "age": 60.0},
        "your-second-repo": {"ran": True, "ok": True, "input_rows": 3, "age": 90.0},
    },
}


# health.ok=True but a source did not run — the renderer must refuse green.
DIGEST_FALSE_GREEN = {
    "health": {"ok": True, "reason": "All sources fresh, no anomalies detected"},
    "detector_hits": [],
    "ranking": [],
    "provenance": {
        "jarvis": {"ran": True, "ok": True, "input_rows": 10, "age": 60.0},
        "your-second-repo": {"ran": False, "ok": False, "input_rows": 0, "age": 0.0},
    },
}


# health.ok=True but a source is stale (age beyond freshness) — refuse green.
DIGEST_STALE_GREEN = {
    "health": {"ok": True, "reason": "All sources fresh, no anomalies detected"},
    "detector_hits": [],
    "ranking": [],
    "provenance": {
        "jarvis": {"ran": True, "ok": True, "input_rows": 10, "age": 999999.0},
        "your-second-repo": {"ran": True, "ok": True, "input_rows": 3, "age": 90.0},
    },
}


# ---------------------------------------------------------------------------
# AC1 — default render is deterministic Python: health line + ranked top-N
#       "Куда смотреть" + "Аномалии"
# ---------------------------------------------------------------------------


class TestDefaultRender:
    def test_has_health_line_and_both_blocks(self):
        out = render(DIGEST_UNHEALTHY)
        assert "🔴" in out
        assert "Куда смотреть" in out
        assert "Аномалии" in out

    def test_ranked_items_present(self):
        out = render(DIGEST_UNHEALTHY)
        assert "stale-in-progress" in out
        assert "blocker-cascade" in out
        assert "#42" in out
        assert "#7" in out

    def test_is_deterministic(self):
        assert render(DIGEST_UNHEALTHY) == render(DIGEST_UNHEALTHY)

    def test_no_blocks_when_green(self):
        out = render(DIGEST_GREEN)
        assert "Куда смотреть" not in out
        assert "Аномалии" not in out


# ---------------------------------------------------------------------------
# AC2 — --deep adds the full picture; default path unchanged when flag absent
# ---------------------------------------------------------------------------


class TestDeepFlag:
    def test_default_equals_explicit_false(self):
        assert render(DIGEST_UNHEALTHY) == render(DIGEST_UNHEALTHY, deep=False)

    def test_deep_is_superset_of_default(self):
        default = render(DIGEST_UNHEALTHY, deep=False)
        deep = render(DIGEST_UNHEALTHY, deep=True)
        assert deep != default
        # every non-empty default line still appears in deep output
        for line in default.splitlines():
            if line.strip():
                assert line in deep

    def test_deep_adds_provenance_table(self):
        default = render(DIGEST_UNHEALTHY, deep=False)
        deep = render(DIGEST_UNHEALTHY, deep=True)
        assert "Провенанс" not in default
        assert "Провенанс" in deep
        assert "input_rows" in deep or "rows=" in deep


# ---------------------------------------------------------------------------
# AC3 — green health line ONLY when provenance all ok + fresh
# ---------------------------------------------------------------------------


class TestProvenanceContract:
    def test_green_when_healthy_and_fresh(self):
        out = render(DIGEST_GREEN)
        assert "🟢" in out
        assert "🔴" not in out

    def test_no_green_when_unhealthy(self):
        assert "🟢" not in render(DIGEST_UNHEALTHY)

    def test_no_green_when_source_did_not_run(self):
        out = render(DIGEST_FALSE_GREEN)
        assert "🟢" not in out
        assert "your-second-repo" in out

    def test_no_green_when_source_stale(self):
        out = render(DIGEST_STALE_GREEN)
        assert "🟢" not in out
        assert "jarvis" in out


# ---------------------------------------------------------------------------
# AC4 — covers both repos in one invocation
# ---------------------------------------------------------------------------


class TestBothRepos:
    def test_both_repos_appear(self):
        out = render(DIGEST_UNHEALTHY)
        assert "jarvis" in out
        assert "your-second-repo" in out


# ---------------------------------------------------------------------------
# #1059 AC4 — info hits (stale-backlog) hidden by default, shown under --deep
# ---------------------------------------------------------------------------


DIGEST_WITH_INFO = {
    "health": {"ok": True, "reason": "All sources fresh, no anomalies detected"},
    "detector_hits": [
        _hit(
            "stale-backlog",
            "info",
            "Osasuwu/jarvis",
            None,
            "3 issue(s) idle",
            "3 issue(s) sitting in Backlog/Ready for ≥30d: #1, #2, #3",
        ),
    ],
    "ranking": [],
    "provenance": {
        "jarvis": {"ran": True, "ok": True, "input_rows": 10, "age": 60.0},
    },
}


class TestInfoSeverityGate:
    def test_info_hidden_in_default_render(self):
        out = render(DIGEST_WITH_INFO, deep=False)
        assert "stale-backlog" not in out
        assert "Аномалии" not in out  # no non-info hits ⇒ block absent entirely

    def test_info_shown_under_deep(self):
        out = render(DIGEST_WITH_INFO, deep=True)
        assert "stale-backlog" in out
        assert "#1, #2, #3" in out

    def test_info_does_not_break_green_default(self):
        # info-only digest still reads green in the default surface.
        assert "🟢" in render(DIGEST_WITH_INFO, deep=False)


# ---------------------------------------------------------------------------
# AC5 — snapshot test pins the deterministic render against a fixture digest
# ---------------------------------------------------------------------------


# Frozen deterministic render of DIGEST_UNHEALTHY — any format change must
# update this deliberately (that is the regression signal this test exists for).
SNAPSHOT_UNHEALTHY = (
    "🔴 Unhealthy: 2 detector hit(s) (0 critical, 2 major)\n"
    "\n"
    "Куда смотреть:\n"
    "  1. [MAJOR] stale-in-progress — Osasuwu/jarvis — #42\n"
    "  2. [MAJOR] blocker-cascade — your-username/your-second-repo — #7\n"
    "\n"
    "Аномалии:\n"
    "  Osasuwu/jarvis:\n"
    "    • [MAJOR] stale-in-progress #42\n"
    "  your-username/your-second-repo:\n"
    "    • [MAJOR] blocker-cascade #7"
)


class TestSnapshot:
    def test_render_matches_snapshot(self):
        assert render(DIGEST_UNHEALTHY) == SNAPSHOT_UNHEALTHY
