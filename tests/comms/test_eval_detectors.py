"""eval_detectors.py tests (#514).

Same importlib idiom as test_collect_labels.py / test_detect_rule_violations.py.
Only pure/deterministic scoring + decision logic is exercised — the Supabase
fetch and live LLM call are smoke-only, per the module's own pragma markers.
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
    / "eval_detectors.py"
)
_spec = importlib.util.spec_from_file_location("eval_detectors", _PATH)
_mod = importlib.util.module_from_spec(_spec)
assert _spec.loader is not None
_spec.loader.exec_module(_mod)


# ---------------------------------------------------------------------------
# compute_precision_recall — regex metrics straight from ground-truth labels
# ---------------------------------------------------------------------------


def test_compute_precision_recall_basic_counts():
    labels = [
        {"detector_kind": "rule_violation", "label": "valid"},
        {"detector_kind": "rule_violation", "label": "valid"},
        {"detector_kind": "rule_violation", "label": "false_positive"},
        {"detector_kind": "rule_violation", "label": "missed"},
    ]

    metrics = _mod.compute_precision_recall(labels)

    m = metrics["rule_violation"]
    assert m["tp"] == 2
    assert m["fp"] == 1
    assert m["fn"] == 1
    assert m["precision"] == 2 / 3
    assert m["recall"] == 2 / 3


def test_compute_precision_recall_separates_by_kind():
    labels = [
        {"detector_kind": "rule_violation", "label": "valid"},
        {"detector_kind": "hallucination", "label": "false_positive"},
    ]

    metrics = _mod.compute_precision_recall(labels)

    assert set(metrics) == {"rule_violation", "hallucination"}
    assert metrics["rule_violation"]["tp"] == 1
    assert metrics["hallucination"]["fp"] == 1


def test_compute_precision_recall_none_when_no_denominator():
    labels = [{"detector_kind": "rule_violation", "label": "missed"}]

    metrics = _mod.compute_precision_recall(labels)

    assert metrics["rule_violation"]["precision"] is None
    assert metrics["rule_violation"]["recall"] == 0.0


# ---------------------------------------------------------------------------
# run_llm_classification — injected classify_fn, no live API
# ---------------------------------------------------------------------------


def test_run_llm_classification_calls_injected_fn_per_row():
    labels = [
        {"message_id": "a#1", "snippet": "s1", "detector_kind": "rule_violation"},
        {"message_id": "a#2", "snippet": "s2", "detector_kind": "hallucination"},
    ]
    calls = []

    def fake_classify(snippet, kind):
        calls.append((snippet, kind))
        return snippet == "s1"

    verdicts = _mod.run_llm_classification(labels, fake_classify)

    assert verdicts == {"a#1": True, "a#2": False}
    assert calls == [("s1", "rule_violation"), ("s2", "hallucination")]


# ---------------------------------------------------------------------------
# compute_llm_precision_recall
# ---------------------------------------------------------------------------


def test_compute_llm_precision_recall_matches_ground_truth():
    labels = [
        {"message_id": "a#1", "detector_kind": "rule_violation", "label": "valid"},
        {"message_id": "a#2", "detector_kind": "rule_violation", "label": "false_positive"},
        {"message_id": "a#3", "detector_kind": "rule_violation", "label": "missed"},
    ]
    llm_verdicts = {"a#1": True, "a#2": True, "a#3": False}

    metrics = _mod.compute_llm_precision_recall(labels, llm_verdicts)

    m = metrics["rule_violation"]
    assert m["tp"] == 1
    assert m["fp"] == 1
    assert m["fn"] == 1


def test_compute_llm_precision_recall_missing_verdict_treated_as_false():
    labels = [{"message_id": "a#1", "detector_kind": "rule_violation", "label": "valid"}]

    metrics = _mod.compute_llm_precision_recall(labels, {})

    assert metrics["rule_violation"]["fn"] == 1
    assert metrics["rule_violation"]["tp"] == 0


# ---------------------------------------------------------------------------
# decision_rule — upgrade / stay / insufficient-data
# ---------------------------------------------------------------------------


def test_decision_rule_upgrade_when_2x_recall_and_close_precision():
    regex_metrics = {"rule_violation": {"precision": 0.8, "recall": 0.3}}
    llm_metrics = {"rule_violation": {"precision": 0.75, "recall": 0.7}}

    decision = _mod.decision_rule(regex_metrics, llm_metrics)

    assert decision["per_kind"]["rule_violation"] == "upgrade"
    assert decision["overall"] == "upgrade"


def test_decision_rule_stay_when_precision_gap_too_large():
    regex_metrics = {"rule_violation": {"precision": 0.9, "recall": 0.3}}
    llm_metrics = {"rule_violation": {"precision": 0.5, "recall": 0.9}}

    decision = _mod.decision_rule(regex_metrics, llm_metrics)

    assert decision["per_kind"]["rule_violation"] == "stay"
    assert decision["overall"] == "stay"


def test_decision_rule_stay_when_recall_not_2x():
    regex_metrics = {"rule_violation": {"precision": 0.8, "recall": 0.5}}
    llm_metrics = {"rule_violation": {"precision": 0.8, "recall": 0.6}}

    decision = _mod.decision_rule(regex_metrics, llm_metrics)

    assert decision["per_kind"]["rule_violation"] == "stay"


def test_decision_rule_insufficient_data_when_metric_missing():
    regex_metrics = {"rule_violation": {"precision": None, "recall": 0.5}}
    llm_metrics = {"rule_violation": {"precision": 0.8, "recall": 0.9}}

    decision = _mod.decision_rule(regex_metrics, llm_metrics)

    assert decision["per_kind"]["rule_violation"] == "insufficient-data"
    assert decision["overall"] == "stay"


def test_decision_rule_overall_upgrade_requires_all_kinds_to_agree():
    regex_metrics = {
        "rule_violation": {"precision": 0.8, "recall": 0.3},
        "hallucination": {"precision": 0.8, "recall": 0.5},
    }
    llm_metrics = {
        "rule_violation": {"precision": 0.75, "recall": 0.7},
        "hallucination": {"precision": 0.8, "recall": 0.6},
    }

    decision = _mod.decision_rule(regex_metrics, llm_metrics)

    assert decision["per_kind"]["rule_violation"] == "upgrade"
    assert decision["per_kind"]["hallucination"] == "stay"
    assert decision["overall"] == "stay"


# ---------------------------------------------------------------------------
# write_summary_md
# ---------------------------------------------------------------------------


def test_write_summary_md_writes_table_and_decision(tmp_path):
    regex_metrics = {"rule_violation": {"precision": 0.8, "recall": 0.3}}
    llm_metrics = {"rule_violation": {"precision": 0.75, "recall": 0.7}}
    decision = {"overall": "upgrade", "per_kind": {"rule_violation": "upgrade"}}
    out_path = tmp_path / "summary.md"

    _mod.write_summary_md(regex_metrics, llm_metrics, decision, out_path, "2026-W32")

    text = out_path.read_text(encoding="utf-8")
    assert "2026-W32" in text
    assert "Decision: upgrade" in text
    assert "rule_violation" in text


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

    _mod.main("2026-W32", str(tmp_path))  # must not raise

    assert "skipping" in capsys.readouterr().err
