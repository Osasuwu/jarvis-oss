"""detect_hallucinations.py tests (#513).

Loaded via importlib — `scripts/analyze-comms/` has a dash, can't be a normal
package import (idiom per tests/comms/test_comm_patterns_backfill.py). Only
pure/deterministic helpers are exercised; Supabase/network paths are not
unit-tested here (covered by the manual smoke run in the PR).
"""

from __future__ import annotations

import importlib.util
import json
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
    / "detect_hallucinations.py"
)
_spec = importlib.util.spec_from_file_location("detect_hallucinations", _PATH)
_mod = importlib.util.module_from_spec(_spec)
assert _spec.loader is not None
_spec.loader.exec_module(_mod)


# ---------------------------------------------------------------------------
# find_hallucination_candidates — correction phrase -> nearest preceding
# assistant message within the lookback cap
# ---------------------------------------------------------------------------


def test_finds_candidate_when_user_disputes_a_preceding_assistant_claim_ru():
    messages_by_session = {
        "sess-1": [
            {"sess": "sess-1", "role": "a", "text": "Ты говорил использовать git add -A."},
            {"sess": "sess-1", "role": "u", "text": "Я такого не говорил, это не мои слова."},
        ]
    }

    candidates = _mod.find_hallucination_candidates(messages_by_session)

    assert len(candidates) == 1
    c = candidates[0]
    assert c["session_id"] == "sess-1"
    assert c["assistant_message_idx"] == 0
    assert c["user_message_idx"] == 1
    assert "git add" in c["assistant_text"]


def test_finds_candidate_for_english_correction_phrase():
    messages_by_session = {
        "sess-2": [
            {"sess": "sess-2", "role": "a", "text": "You asked me to delete the branch."},
            {"sess": "sess-2", "role": "u", "text": "I never said that, you're hallucinating."},
        ]
    }

    candidates = _mod.find_hallucination_candidates(messages_by_session)

    assert len(candidates) == 1
    assert candidates[0]["session_id"] == "sess-2"


def test_no_candidate_when_no_correction_phrase_present():
    messages_by_session = {
        "sess-3": [
            {"sess": "sess-3", "role": "a", "text": "Running the tests now."},
            {"sess": "sess-3", "role": "u", "text": "Sounds good, thanks."},
        ]
    }

    candidates = _mod.find_hallucination_candidates(messages_by_session)

    assert candidates == []


def test_lookback_cap_ignores_assistant_messages_too_far_back():
    messages_by_session = {
        "sess-4": [
            {"sess": "sess-4", "role": "a", "text": "Ты говорил использовать git add -A."},
            {"sess": "sess-4", "role": "u", "text": "ok"},
            {"sess": "sess-4", "role": "u", "text": "ok"},
            {"sess": "sess-4", "role": "u", "text": "ok"},
            {"sess": "sess-4", "role": "u", "text": "ok"},
            {"sess": "sess-4", "role": "u", "text": "Я такого не говорил."},
        ]
    }

    candidates = _mod.find_hallucination_candidates(messages_by_session, lookback=3)

    assert candidates == []


# ---------------------------------------------------------------------------
# merge_into_patterns_json — same read-modify-write contract as the sibling
# detector, duplicated per-script by design (see #513 PR notes)
# ---------------------------------------------------------------------------


def test_merge_into_patterns_json_preserves_existing_keys(tmp_path):
    target = tmp_path / "MAINPC_patterns.json"
    target.write_text(json.dumps({"rule_violations": []}), encoding="utf-8")

    _mod.merge_into_patterns_json(target, [{"session_id": "sess-1"}])

    written = json.loads(target.read_text(encoding="utf-8"))
    assert written["rule_violations"] == []
    assert written["hallucinations"] == [{"session_id": "sess-1"}]
