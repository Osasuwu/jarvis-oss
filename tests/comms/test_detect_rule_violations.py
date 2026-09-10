"""detect_rule_violations.py tests (#513).

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
    / "detect_rule_violations.py"
)
_spec = importlib.util.spec_from_file_location("detect_rule_violations", _PATH)
_mod = importlib.util.module_from_spec(_spec)
assert _spec.loader is not None
_spec.loader.exec_module(_mod)


# ---------------------------------------------------------------------------
# scan_messages — tracer bullet: rules -> keywords -> scan -> candidates
# ---------------------------------------------------------------------------


def test_scan_messages_flags_assistant_text_matching_rule_keywords():
    rules = [
        {"name": "no_git_add_all", "keywords": ["sweeps", "worktrees", "sandcastle", "explicit"]}
    ]
    messages_by_session = {
        "sess-1": [
            {"sess": "sess-1", "role": "u", "text": "please commit this"},
            {
                "sess": "sess-1",
                "role": "a",
                "text": "Running git add -A across worktrees, includes sandcastle sweeps.",
            },
        ]
    }

    candidates = _mod.scan_messages(messages_by_session, rules, min_matches=2)

    assert len(candidates) == 1
    c = candidates[0]
    assert c["session_id"] == "sess-1"
    assert c["message_idx"] == 1
    assert c["rule_name"] == "no_git_add_all"
    assert "worktrees" in c["matched_text"] or "sweeps" in c["matched_text"]
    assert 0 < c["confidence"] <= 1


# ---------------------------------------------------------------------------
# extract_keywords — top-N frequency keywords from rule content, stopwords out
# ---------------------------------------------------------------------------


def test_extract_keywords_ranks_by_frequency_and_drops_stopwords():
    content = (
        "Never git add -A in jarvis. The sweeps pick up sandcastle worktrees "
        "and docs/research drafts. Use explicit paths instead of git add -A. "
        "This git add -A pattern keeps recurring across sessions."
    )

    keywords = _mod.extract_keywords(content, top_n=5)

    assert "git" in keywords
    assert "add" in keywords
    # common short/stop words should never surface as keywords
    assert "the" not in keywords
    assert "and" not in keywords
    assert len(keywords) <= 5


# ---------------------------------------------------------------------------
# merge_into_patterns_json — read-modify-write merge (compress_patterns.py
# fully overwrites the same file, so this must not clobber its other keys)
# ---------------------------------------------------------------------------


def test_merge_into_patterns_json_preserves_existing_keys(tmp_path):
    target = tmp_path / "MAINPC_patterns.json"
    target.write_text(
        json.dumps({"corrective_patterns": [{"category": "no_git_add_all"}]}),
        encoding="utf-8",
    )

    _mod.merge_into_patterns_json(target, [{"rule_name": "no_git_add_all"}])

    written = json.loads(target.read_text(encoding="utf-8"))
    assert written["corrective_patterns"] == [{"category": "no_git_add_all"}]
    assert written["rule_violations"] == [{"rule_name": "no_git_add_all"}]


def test_merge_into_patterns_json_creates_file_if_absent(tmp_path):
    target = tmp_path / "MAINPC_patterns.json"

    _mod.merge_into_patterns_json(target, [])

    written = json.loads(target.read_text(encoding="utf-8"))
    assert written["rule_violations"] == []


# ---------------------------------------------------------------------------
# load_rules_from_memories — feedback-memory rows -> {name, keywords} rules
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# main — graceful degradation when Supabase creds are unset (review finding,
# #513 PR: SKILL.md documents "skip that one detector ... continue", but the
# uncaught RuntimeError aborted the && chain before detect_hallucinations.py
# ever ran)
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
    src = tmp_path / "comms_extract.jsonl"
    src.write_text("", encoding="utf-8")
    out = tmp_path / "MAINPC_patterns.json"

    _mod.main(str(src), str(out))  # must not raise

    assert not out.exists()  # skipped before any write — nothing to merge
    assert "skipping" in capsys.readouterr().err


def test_load_rules_from_memories_derives_keywords_per_memory():
    memories = [
        {
            "name": "feedback_no_git_add_all",
            "content": (
                "Never git add -A in jarvis. Sweeps pick up sandcastle "
                "worktrees and docs/research drafts. Use explicit paths."
            ),
        },
        {"name": "feedback_empty", "content": ""},
    ]

    rules = _mod.load_rules_from_memories(memories)

    assert len(rules) == 2
    first = next(r for r in rules if r["name"] == "feedback_no_git_add_all")
    assert "git" in first["keywords"]
    assert "worktrees" in first["keywords"]
    empty = next(r for r in rules if r["name"] == "feedback_empty")
    assert empty["keywords"] == []
