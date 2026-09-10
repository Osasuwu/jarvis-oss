"""collect_labels.py tests (#514).

Loaded via importlib — `scripts/analyze-comms/` has a dash, can't be a normal
package import (idiom per tests/comms/test_detect_rule_violations.py). Only
pure/deterministic helpers are exercised; the Supabase write path is not
unit-tested here (covered by manual smoke, same convention as #513).
"""

from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path

for _stub in ("supabase", "dotenv"):
    if _stub not in sys.modules:
        try:
            __import__(_stub)
        except ImportError:
            mod = types.ModuleType(_stub)
            if _stub == "dotenv":
                mod.load_dotenv = lambda *a, **k: None
            if _stub == "supabase":
                mod.create_client = lambda *a, **k: None
            sys.modules[_stub] = mod

_PATH = (
    Path(__file__).resolve().parent.parent.parent
    / "scripts"
    / "analyze-comms"
    / "collect_labels.py"
)
_spec = importlib.util.spec_from_file_location("collect_labels", _PATH)
_mod = importlib.util.module_from_spec(_spec)
assert _spec.loader is not None
_spec.loader.exec_module(_mod)


# ---------------------------------------------------------------------------
# parse_labeled_report — structured checkbox line parsing
# ---------------------------------------------------------------------------


def test_parse_labeled_report_extracts_valid_rule_violation_row():
    text = (
        "# report\n\n"
        "## Rule violation candidates\n\n"
        "- [+] `sess-1#3` — rule: `no_git_add_all`, confidence: 0.75 — "
        "Running git add -A across worktrees.\n"
    )

    rows = _mod.parse_labeled_report(text)

    assert len(rows) == 1
    r = rows[0]
    assert r["message_id"] == "sess-1#3"
    assert r["label"] == "valid"
    assert r["detector_verdict"] == "no_git_add_all"
    assert r["detector_kind"] == "rule_violation"
    assert r["snippet"] == "Running git add -A across worktrees."


def test_parse_labeled_report_extracts_false_positive_and_missed():
    text = (
        "- [-] `sess-2#1` — rule: `branching_strategy`, confidence: 0.5 — irrelevant match\n"
        "- [?] `sess-3#7` — rule: `no_git_add_all`, confidence: 0.9 — missed one\n"
    )

    rows = _mod.parse_labeled_report(text)

    assert len(rows) == 2
    assert rows[0]["label"] == "false_positive"
    assert rows[1]["label"] == "missed"


def test_parse_labeled_report_extracts_hallucination_row():
    text = (
        '- [+] `sess-4#2` — hallucination — assistant: "the file already exists" '
        '— correction: "no it doesn\'t, check again"\n'
    )

    rows = _mod.parse_labeled_report(text)

    assert len(rows) == 1
    r = rows[0]
    assert r["message_id"] == "sess-4#2"
    assert r["label"] == "valid"
    assert r["detector_kind"] == "hallucination"
    assert r["detector_verdict"] == "hallucination"
    assert "the file already exists" in r["snippet"]
    assert "no it doesn't, check again" in r["snippet"]


def test_parse_labeled_report_skips_unlabeled_checkboxes():
    text = (
        "- [ ] `sess-5#0` — rule: `no_git_add_all`, confidence: 0.6 — not yet labeled\n"
        '- [ ] `sess-5#1` — hallucination — assistant: "x" — correction: "y"\n'
    )

    rows = _mod.parse_labeled_report(text)

    assert rows == []


def test_parse_labeled_report_ignores_prose_lines():
    text = "Some analysis prose that mentions rule: `foo` but isn't a checkbox line.\n"

    rows = _mod.parse_labeled_report(text)

    assert rows == []


# ---------------------------------------------------------------------------
# aggregate_reports — merge across N report texts, dedup by (message_id, kind)
# ---------------------------------------------------------------------------


def test_aggregate_reports_dedupes_last_occurrence_wins():
    report_1 = "- [?] `sess-1#3` — rule: `no_git_add_all`, confidence: 0.75 — first pass\n"
    report_2 = "- [+] `sess-1#3` — rule: `no_git_add_all`, confidence: 0.75 — corrected\n"

    labels = _mod.aggregate_reports([report_1, report_2])

    assert len(labels) == 1
    assert labels[0]["label"] == "valid"
    assert labels[0]["snippet"] == "corrected"


def test_aggregate_reports_keeps_distinct_candidates_from_multiple_reports():
    report_1 = "- [+] `sess-1#3` — rule: `no_git_add_all`, confidence: 0.75 — a\n"
    report_2 = '- [-] `sess-2#0` — hallucination — assistant: "x" — correction: "y"\n'

    labels = _mod.aggregate_reports([report_1, report_2])

    assert len(labels) == 2
    ids = {row["message_id"] for row in labels}
    assert ids == {"sess-1#3", "sess-2#0"}


# ---------------------------------------------------------------------------
# build_groundtruth_payload / merge_into_groundtruth_memory — Supabase shape
# ---------------------------------------------------------------------------


def test_build_groundtruth_payload_shape():
    labels = [{"message_id": "sess-1#3", "label": "valid"}]

    payload = _mod.build_groundtruth_payload("2026-W32", labels, project="jarvis")

    assert payload["name"] == "reflect_eval_groundtruth_2026-W32"
    assert payload["project"] == "jarvis"
    assert payload["type"] == "project"
    assert "reflect-eval" in payload["tags"]
    assert "groundtruth" in payload["tags"]
    import json

    assert json.loads(payload["content"]) == labels


class _FakeTable:
    def __init__(self):
        self.upserts = []

    def upsert(self, payload, on_conflict=None):
        self.upserts.append((payload, on_conflict))
        return self

    def execute(self):
        return None


class _FakeClient:
    def __init__(self):
        self._table = _FakeTable()

    def table(self, name):
        assert name == "memories"
        return self._table


def test_merge_into_groundtruth_memory_upserts_on_project_and_name():
    client = _FakeClient()
    labels = [{"message_id": "sess-1#3", "label": "valid"}]

    _mod.merge_into_groundtruth_memory(client, "2026-W32", labels)

    assert len(client._table.upserts) == 1
    payload, on_conflict = client._table.upserts[0]
    assert on_conflict == "project,name"
    assert payload["name"] == "reflect_eval_groundtruth_2026-W32"


# ---------------------------------------------------------------------------
# main — graceful degradation when Supabase creds are unset
# ---------------------------------------------------------------------------


def test_get_supabase_client_raises_when_creds_unset(monkeypatch):
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.delenv("SUPABASE_KEY", raising=False)

    try:
        _mod._get_supabase_client()
        raise AssertionError("expected RuntimeError")
    except RuntimeError:
        pass


def test_main_skips_gracefully_without_crashing_when_creds_unset(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.delenv("SUPABASE_KEY", raising=False)
    report = tmp_path / "report.md"
    report.write_text("- [+] `sess-1#0` — rule: `x`, confidence: 0.5 — y\n", encoding="utf-8")

    _mod.main([str(report)], "2026-W32")  # must not raise

    assert "skipping" in capsys.readouterr().err
