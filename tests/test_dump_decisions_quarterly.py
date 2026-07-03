"""Tests for scripts/dump-decisions-quarterly.py — rendering logic only.

The full end-to-end flow requires Supabase credentials. These tests validate
the markdown rendering helpers using synthetic fixtures, so they run in CI
without external dependencies.

Import strategy: exec_module() on the script file, then test the module-level
functions. The supabase import is only used in main() so module-level import
succeeds as long as the package is available.
"""

import importlib.util
import json
import unittest
from pathlib import Path

_SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "dump-decisions-quarterly.py"
_spec = importlib.util.spec_from_file_location("dump_decisions_quarterly", _SCRIPT)
dq = importlib.util.module_from_spec(_spec)

try:
    _spec.loader.exec_module(dq)
    _HAVE_DQ = True
except ImportError:
    _HAVE_DQ = False
    dq = None


_SKIP_REASON = "dump-decisions-quarterly.py not importable (need supabase-py or deps)"


@unittest.skipIf(not _HAVE_DQ, _SKIP_REASON)
class TestParseQuarter(unittest.TestCase):
    def test_valid(self):
        self.assertEqual(dq.parse_quarter("2026-Q2"), (2026, 2))
        self.assertEqual(dq.parse_quarter("2025-Q4"), (2025, 4))

    def test_invalid_format(self):
        with self.assertRaises(ValueError):
            dq.parse_quarter("2026-Q5")
        with self.assertRaises(ValueError):
            dq.parse_quarter("2026-Q0")
        with self.assertRaises(ValueError):
            dq.parse_quarter("blah")


@unittest.skipIf(not _HAVE_DQ, _SKIP_REASON)
class TestQuarterDateRange(unittest.TestCase):
    def test_q1(self):
        start, end = dq.quarter_date_range(2026, 1)
        self.assertEqual(start, "2026-01-01T00:00:00Z")
        self.assertIn("2026-03-31", end)

    def test_q2(self):
        start, end = dq.quarter_date_range(2026, 2)
        self.assertEqual(start, "2026-04-01T00:00:00Z")
        self.assertIn("2026-06-30", end)

    def test_q4_boundary(self):
        start, end = dq.quarter_date_range(2026, 4)
        self.assertEqual(start, "2026-10-01T00:00:00Z")
        self.assertIn("2026-12-31", end)

    def test_leap_year(self):
        start, end = dq.quarter_date_range(2024, 1)
        self.assertIn("2024-03-31", end)


@unittest.skipIf(not _HAVE_DQ, _SKIP_REASON)
class TestRenderDecision(unittest.TestCase):
    def make_decision(self, overrides=None):
        d = {
            "id": "00000000-0000-0000-0000-000000000001",
            "actor": "skill:implement",
            "kind": "decision_made",
            "created_at": "2026-04-15T12:00:00Z",
            "payload": {
                "decision": "Test decision title",
                "rationale": "This is the rationale for the test decision.",
                "reversibility": "reversible",
                "confidence": 0.85,
                "project": "jarvis",
                "alternatives_considered": ["Option A (rejected)", "Option B (rejected)"],
                "memories_used": ["aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"],
                "outcomes_referenced": [],
            },
        }
        if overrides:
            d = {**d, **overrides}
            if "payload" in overrides:
                d["payload"] = {**d["payload"], **overrides["payload"]}
        return d

    def test_renders_decision(self):
        rendered = dq.render_decision(self.make_decision())
        self.assertIn("Test decision title", rendered)
        self.assertIn("00000000-0000-0000-0000-000000000001", rendered)
        self.assertIn("2026-04-15T12:00:00Z", rendered)
        self.assertIn("reversible", rendered)
        self.assertIn("0.85", rendered)
        self.assertIn("skill:implement", rendered)
        self.assertIn("jarvis", rendered)
        self.assertIn("Option A", rendered)
        self.assertIn("This is the rationale", rendered)
        self.assertIn("aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee", rendered)

    def test_no_alternatives(self):
        d = self.make_decision()
        d["payload"]["alternatives_considered"] = []
        rendered = dq.render_decision(d)
        self.assertIn("`none`", rendered)

    def test_no_confidence(self):
        d = self.make_decision()
        del d["payload"]["confidence"]
        rendered = dq.render_decision(d)
        self.assertNotIn("**Confidence:**", rendered)

    def test_intentionally_empty_memories(self):
        d = self.make_decision()
        d["payload"]["memories_used"] = []
        d["payload"]["intentionally_empty"] = True
        rendered = dq.render_decision(d)
        self.assertIn("intentionally empty", rendered)

    def test_payload_as_string(self):
        d = self.make_decision()
        d["payload"] = json.dumps(d["payload"])
        rendered = dq.render_decision(d)
        self.assertIn("Test decision title", rendered)

    def test_irreversible(self):
        d = self.make_decision()
        d["payload"]["reversibility"] = "irreversible"
        rendered = dq.render_decision(d)
        self.assertIn("irreversible", rendered)

    def test_outcomes_referenced(self):
        d = self.make_decision()
        d["payload"]["outcomes_referenced"] = ["x" * 36]
        rendered = dq.render_decision(d)
        self.assertIn("Outcomes referenced", rendered)

    def test_unresolved_memories(self):
        d = self.make_decision()
        d["payload"]["memories_used_unresolved"] = ["some_memory_name"]
        rendered = dq.render_decision(d)
        self.assertIn("unresolved", rendered)

    def test_no_project(self):
        d = self.make_decision()
        d["payload"]["project"] = ""
        rendered = dq.render_decision(d)
        self.assertIn("**Project:** `", rendered)


@unittest.skipIf(not _HAVE_DQ, _SKIP_REASON)
class TestRenderDocument(unittest.TestCase):
    def make_decisions(self, count=3):
        decisions = []
        for i in range(count):
            d = {
                "id": f"00000000-0000-0000-0000-{i:012d}",
                "actor": "skill:test",
                "kind": "decision_made",
                "created_at": f"2026-04-{10 + i:02d}T12:00:00Z",
                "payload": {
                    "decision": f"Decision #{i}",
                    "rationale": f"Rationale for #{i}",
                    "reversibility": "reversible",
                    "alternatives_considered": [],
                    "memories_used": [],
                },
            }
            decisions.append(d)
        return decisions

    def test_document_structure(self):
        decisions = self.make_decisions()
        doc = dq.render_document(decisions, "2026-Q2", "2026-05-18")
        self.assertIn("# Decision dump — 2026-Q2 (cutoff: 2026-05-18)", doc)
        self.assertIn("Total decisions: **3**", doc)
        self.assertIn("## 2026-04", doc)
        self.assertIn("Decision #0", doc)
        self.assertIn("Decision #1", doc)
        self.assertIn("Decision #2", doc)

    def test_empty_decision_list(self):
        doc = dq.render_document([], "2026-Q2", "2026-05-18")
        self.assertIn("Total decisions: **0**", doc)

    def test_actor_prefix_aggregation(self):
        decisions = self.make_decisions(2)
        decisions.append({
            "id": "f0000000-0000-0000-0000-000000000003",
            "actor": "session:orchestrator",
            "kind": "decision_made",
            "created_at": "2026-05-01T12:00:00Z",
            "payload": {
                "decision": "Session decision",
                "rationale": "Rationale",
                "reversibility": "hard",
                "alternatives_considered": [],
                "memories_used": [],
            },
        })
        doc = dq.render_document(decisions, "2026-Q2", "2026-05-18")
        self.assertIn("- `skill`: 2", doc)
        self.assertIn("- `session`: 1", doc)

    def test_multiple_months(self):
        decisions = []
        for month in ("2026-04", "2026-05"):
            decisions.append({
                "id": f"m-{month}-0000-0000-0000-000000000000",
                "actor": "skill:test",
                "kind": "decision_made",
                "created_at": f"{month}-15T12:00:00Z",
                "payload": {
                    "decision": f"Decision in {month}",
                    "rationale": "Rationale",
                    "reversibility": "reversible",
                    "alternatives_considered": [],
                    "memories_used": [],
                },
            })
        doc = dq.render_document(decisions, "2026-Q2", "2026-05-18")
        self.assertIn("## 2026-04", doc)
        self.assertIn("## 2026-05", doc)


if __name__ == "__main__":
    unittest.main()
