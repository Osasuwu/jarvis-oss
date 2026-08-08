"""Backfill helper tests.

The backfill script is loaded via importlib (filename has a dash, can't be
imported as a module). We exercise the deterministic helpers — synthetic
session-id stability, captured-at validation, example iteration — without
calling Ollama or Supabase.

Per review #584 finding 13: ``_synth_session_id`` stability is the
idempotency contract; a refactor that silently changes its hash would
double-write every cache file on the next backfill run.
"""

from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path

# Ensure heavy optional deps don't fail at module import time.
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

_PATH = Path(__file__).resolve().parent.parent.parent / "scripts" / "comm-patterns-backfill.py"
_spec = importlib.util.spec_from_file_location("comm_patterns_backfill", _PATH)
_mod = importlib.util.module_from_spec(_spec)
assert _spec.loader is not None
_spec.loader.exec_module(_mod)


# ---------------------------------------------------------------------------
# _synth_session_id determinism
# ---------------------------------------------------------------------------


def test_synth_session_id_is_deterministic(tmp_path: Path):
    fp = tmp_path / "x.json"
    a = _mod._synth_session_id(fp, "permission_seeking", 0)
    b = _mod._synth_session_id(fp, "permission_seeking", 0)
    assert a == b
    assert a.startswith("backfill:")


def test_synth_session_id_differs_per_input(tmp_path: Path):
    fp = tmp_path / "x.json"
    others = {
        _mod._synth_session_id(fp, "permission_seeking", 0),
        _mod._synth_session_id(fp, "tunnel_vision", 0),
        _mod._synth_session_id(fp, "permission_seeking", 1),
        _mod._synth_session_id(tmp_path / "y.json", "permission_seeking", 0),
    }
    assert len(others) == 4


# ---------------------------------------------------------------------------
# _captured_at_from_file: validation tightened (#6)
# ---------------------------------------------------------------------------


def test_captured_at_from_valid_iso_date():
    out = _mod._captured_at_from_file({"date_range": ["2026-04-01", "2026-04-30"]})
    assert out == "2026-04-01T00:00:00+00:00"


def test_captured_at_from_garbage_falls_back_to_now():
    """Free-form strings or None used to produce 'NoneT00:00:00+00:00' —
    now they fall back to a real UTC ISO string (#584 review finding 6)."""
    out_garbage = _mod._captured_at_from_file({"date_range": ["Q1 2025", "Q2 2025"]})
    out_none = _mod._captured_at_from_file({"date_range": [None, None]})
    out_missing = _mod._captured_at_from_file({})
    for out in (out_garbage, out_none, out_missing):
        assert "None" not in out
        # ISO-shaped fallback parses cleanly.
        from datetime import datetime
        datetime.fromisoformat(out)


# ---------------------------------------------------------------------------
# _iter_examples: structural robustness
# ---------------------------------------------------------------------------


def test_iter_examples_handles_missing_keys():
    """Old cache files may have only correctives or only affirmatives."""
    only_corr = {"correctives": {"x": {"examples": [{"trigger": "t", "correction": "c"}]}}}
    only_aff = {"affirmatives": {"examples": [{"trigger": "t", "snippet": "s"}]}}
    empty = {}
    assert len(_mod._iter_examples(only_corr)) == 1
    assert len(_mod._iter_examples(only_aff)) == 1
    assert _mod._iter_examples(empty) == []


def test_iter_examples_yields_categories_with_examples():
    payload = {
        "correctives": {
            "permission_seeking": {"examples": [{"trigger": "t1", "correction": "c1"}]},
            "tunnel_vision": {"examples": [{"trigger": "t2", "correction": "c2"}]},
        },
        "affirmatives": {"examples": [{"trigger": "t3", "snippet": "s3"}]},
    }
    out = _mod._iter_examples(payload)
    cats = [cat for cat, _ in out]
    assert "permission_seeking" in cats
    assert "tunnel_vision" in cats
    assert "affirmative" in cats


def test_example_to_user_text_prefers_correction_over_snippet():
    """correction wins over snippet (correction is corrective; snippet is affirmative).
    Both populated → correction wins."""
    user, prev = _mod._example_to_user_text({"trigger": "t", "correction": "c", "snippet": "s"})
    assert user == "c"
    assert prev == "t"


def test_example_to_user_text_falls_back_to_snippet():
    user, prev = _mod._example_to_user_text({"trigger": "t", "snippet": "s"})
    assert user == "s"
    assert prev == "t"


def test_example_to_user_text_empty_when_neither_present():
    user, prev = _mod._example_to_user_text({"trigger": "t"})
    assert user == ""


def test_run_uses_shared_confidence_threshold():
    """Backfill must use the same threshold as the live extractor — drift
    silently changes which historical patterns get re-classified into
    the table."""
    from comm_patterns.extractor import CONFIDENCE_THRESHOLD as live_threshold
    # Value check: if the backfill module has a different number, this
    # catches the drift.
    assert _mod.CONFIDENCE_THRESHOLD == live_threshold
    # Source check: verify backfill imports from extractor rather than
    # hardcoding its own copy. The equality assertion could pass if both
    # happen to share the same literal — this guards against a refactor
    # that silently copies the value instead of importing it.
    import inspect
    source = inspect.getsource(_mod)
    assert "from comm_patterns.extractor import CONFIDENCE_THRESHOLD" in source, (
        "Backfill must import CONFIDENCE_THRESHOLD from extractor, not hardcode it"
    )


def test_run_max_examples_caps_processing(tmp_path: Path, monkeypatch):
    """--max-examples bounds the total Ollama-call budget across all files."""
    cache_root = tmp_path / "cache"
    sub = cache_root / "2026-04-30_X"
    sub.mkdir(parents=True)
    payload = {
        "device": "X",
        "date_range": ["2026-04-01", "2026-04-30"],
        "correctives": {
            "perm": {"examples": [
                {"trigger": "t", "correction": f"c{i}"} for i in range(10)
            ]}
        },
    }
    (sub / "X_patterns.json").write_text(_dumps_json(payload), encoding="utf-8")

    calls = {"n": 0}

    def fake_classify(user_text, prev):
        calls["n"] += 1
        return {"primary_label": "affirmation", "subtype": None,
                "confidence": 0.9, "anchor_quote": user_text}

    monkeypatch.setattr(_mod, "call_ollama", fake_classify)
    stats = _mod.run(dry_run=True, cache_root=cache_root, max_examples=3)
    assert calls["n"] == 3
    assert stats["rows_written"] == 3


def test_run_ollama_unavailable_aborts_run_circuit_breaker(tmp_path: Path, monkeypatch):
    """OllamaUnavailable is a host-wide condition, not a per-example one: the
    first one aborts the whole run (circuit-breaker) instead of hammering every
    remaining example with the same dead socket. Exactly one connection_error is
    counted and call_ollama is invoked exactly once, even though two examples
    exist. connection_errors stays distinct from no_pattern / classifier_errors."""
    cache_root = tmp_path / "cache"
    sub = cache_root / "2026-04-30_X"
    sub.mkdir(parents=True)
    payload = {
        "device": "X",
        "date_range": ["2026-04-01", "2026-04-30"],
        "correctives": {
            "perm": {"examples": [
                {"trigger": "t", "correction": "c1"},
                {"trigger": "t", "correction": "c2"},
            ]}
        },
    }
    (sub / "X_patterns.json").write_text(_dumps_json(payload), encoding="utf-8")

    calls = {"n": 0}

    def boom(user_text, prev):
        calls["n"] += 1
        raise _mod.OllamaUnavailable("connection refused")

    monkeypatch.setattr(_mod, "call_ollama", boom)
    stats = _mod.run(dry_run=True, cache_root=cache_root)
    # Circuit-breaker: stop after the first failure; example #2 is never tried.
    assert calls["n"] == 1
    assert stats["connection_errors"] == 1
    assert stats["no_pattern"] == 0
    assert stats["classifier_errors"] == 0
    assert stats["rows_written"] == 0


def test_run_ollama_unavailable_circuit_breaker_spans_files(tmp_path: Path, monkeypatch):
    """The circuit-breaker must break out of the *outer* file loop too, not just
    the inner example loop — Ollama being down for file A means it's down for file
    B. With two cache files, the run still calls Ollama exactly once."""
    cache_root = tmp_path / "cache"
    for day in ("2026-04-10_A", "2026-04-20_B"):
        sub = cache_root / day
        sub.mkdir(parents=True)
        payload = {
            "device": day[-1],
            "date_range": ["2026-04-01", "2026-04-30"],
            "correctives": {"perm": {"examples": [{"trigger": "t", "correction": "c"}]}},
        }
        (sub / f"{day[-1]}_patterns.json").write_text(_dumps_json(payload), encoding="utf-8")

    calls = {"n": 0}

    def boom(user_text, prev):
        calls["n"] += 1
        raise _mod.OllamaUnavailable("connection refused")

    monkeypatch.setattr(_mod, "call_ollama", boom)
    stats = _mod.run(dry_run=True, cache_root=cache_root)
    # Two files, one example each — but the breaker aborts on the first failure,
    # so the second file is never opened for classification.
    assert calls["n"] == 1
    assert stats["files_seen"] == 2
    assert stats["connection_errors"] == 1
    assert stats["rows_written"] == 0


def test_run_primary_label_null_increments_no_pattern_not_connection_errors(tmp_path: Path, monkeypatch):
    """Successful response with primary_label=None increments no_pattern,
    not connection_errors. This confirms we don't merge (1) and (3) from
    the issue description."""
    cache_root = tmp_path / "cache"
    sub = cache_root / "2026-04-30_X"
    sub.mkdir(parents=True)
    payload = {
        "device": "X",
        "date_range": ["2026-04-01", "2026-04-30"],
        "correctives": {
            "perm": {"examples": [
                {"trigger": "t", "correction": "c1"},
            ]}
        },
    }
    (sub / "X_patterns.json").write_text(_dumps_json(payload), encoding="utf-8")

    def null_label(user_text, prev):
        return {"primary_label": None, "subtype": None,
                "confidence": 0.0, "anchor_quote": user_text}

    monkeypatch.setattr(_mod, "call_ollama", null_label)
    stats = _mod.run(dry_run=True, cache_root=cache_root)
    assert stats["no_pattern"] == 1
    assert stats["connection_errors"] == 0
    assert stats["classifier_errors"] == 0
    assert stats["rows_written"] == 0


# Helper kept here so the import block stays stable.
def _dumps_json(obj) -> str:
    import json as _json
    return _json.dumps(obj, ensure_ascii=False)
