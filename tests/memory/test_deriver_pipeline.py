"""Tests for the Deriver pipeline (S3, #683).

Covers the acceptance criteria:
  - ``derive_from_session`` returns ≤5 candidate IDs given any input.
  - Scrubbed transcript + privacy token → persisted content is clean.
  - Documented shape and ≤5 cap.
  - Sparse corpus → empty result without error.
  - DeepSeek fallback on Ollama failure → no half-write.
  - No insert without scrubber call (enforced by pipeline shape).
"""

from __future__ import annotations

import json
from pathlib import Path
from uuid import UUID, uuid4

# --
# Ensure the scripts package is importable before loading the module
# under test.  conftest.py already inserts ``scripts/`` into sys.path.
# --
from deriver.pipeline import (
    derive_from_session,
    _build_row,
    _validate_candidate,
    _parse_json_response,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

SESSION_ID = "test-session-001"
PROJECT_HASH = "a1b2c3d4e5f6"


def _write_buffer(buffer_dir: Path, project_hash: str, session_id: str, rows: list[dict]) -> Path:
    """Write a JSONL buffer file (same format as deriver-accumulator.py).

    Path: ``<buffer_dir>/<project_hash>/<session-id>.jsonl``.
    """
    proj_dir = buffer_dir / project_hash
    proj_dir.mkdir(parents=True, exist_ok=True)
    path = proj_dir / f"{session_id}.jsonl"
    with path.open("w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")
    return path


def _user_turn(text: str) -> dict:
    return {"role": "user", "content": text}


def _asst_turn(text: str) -> dict:
    return {"role": "assistant", "content": text}


def _make_llm(candidates: list[dict]) -> callable:
    """Return a fake LLM callable that returns the given candidates as JSON."""

    def llm_fn(prompt: str) -> str:
        return json.dumps(candidates)

    return llm_fn


def _make_fake_insert() -> tuple[callable, list[dict]]:
    """Return (insert_fn, captured) — ``captured`` accumulates inserted rows."""
    captured: list[dict] = []

    def insert_fn(row: dict) -> UUID:
        uid = uuid4()
        captured.append({**row, "_inserted_id": str(uid)})
        return uid

    return insert_fn, captured


def _make_failing_llm() -> callable:
    """LLM that simulates an unreachable backend."""

    def llm_fn(prompt: str) -> None:
        return None

    return llm_fn


# ---------------------------------------------------------------------------
# Basic pipeline: happy path
# ---------------------------------------------------------------------------


def test_derive_from_session_returns_up_to_5_candidates(tmp_path: Path):
    """Given a buffer with content, the pipeline returns ≤5 candidate IDs."""
    buffer_dir = tmp_path / ".deriver-buffer"
    _write_buffer(
        buffer_dir,
        PROJECT_HASH,
        SESSION_ID,
        [
            _asst_turn("Hello, how can I help?"),
            _user_turn("We should use early returns instead of nested ifs"),
            _asst_turn("Good point, let me refactor that"),
        ],
    )

    candidates = [
        {
            "type": "feedback",
            "project": "jarvis",
            "name": "prefer-early-return",
            "description": "Prefers early return pattern",
            "content": "User prefers early returns over nested if statements in Python code.",
            "tags": ["coding-style", "python"],
        },
    ]
    llm_fn = _make_llm(candidates)
    insert_fn, captured = _make_fake_insert()

    result = derive_from_session(
        SESSION_ID,
        project_hash=PROJECT_HASH,
        llm_fn=llm_fn,
        insert_fn=insert_fn,
        buffer_root=buffer_dir,
    )

    assert len(result) == 1
    assert len(captured) == 1
    row = captured[0]
    assert row["type"] == "feedback"
    assert row["project"] == "jarvis"
    assert row["requires_review"] is True
    assert row["source_provenance"] == f"deriver:{SESSION_ID}"
    assert row["merge_targets"] is None


def test_derive_from_session_returns_at_most_5(tmp_path: Path):
    """If LLM returns 7 candidates, the pipeline caps at 5."""
    buffer_dir = tmp_path / ".deriver-buffer"
    _write_buffer(
        buffer_dir,
        PROJECT_HASH,
        SESSION_ID,
        [
            _asst_turn("ok"),
            _user_turn("Lesson 1"),
            _asst_turn("ok"),
            _user_turn("Lesson 2"),
            _asst_turn("ok"),
            _user_turn("Lesson 3"),
            _asst_turn("ok"),
            _user_turn("Lesson 4"),
            _asst_turn("ok"),
            _user_turn("Lesson 5"),
            _asst_turn("ok"),
            _user_turn("Lesson 6"),
            _asst_turn("ok"),
            _user_turn("Lesson 7"),
        ],
    )

    many_candidates = []
    for i in range(7):
        many_candidates.append(
            {
                "type": "feedback" if i % 2 == 0 else "user",
                "project": "jarvis" if i % 2 == 0 else None,
                "name": f"insight-{i}",
                "description": f"Insight {i}",
                "content": f"Content for insight {i}.",
                "tags": ["test"],
            }
        )

    llm_fn = _make_llm(many_candidates)
    insert_fn, captured = _make_fake_insert()

    result = derive_from_session(
        SESSION_ID,
        project_hash=PROJECT_HASH,
        llm_fn=llm_fn,
        insert_fn=insert_fn,
        buffer_root=buffer_dir,
    )

    assert len(result) == 5
    assert len(captured) == 5
    names = [r["name"] for r in captured]
    assert names == ["insight-0", "insight-1", "insight-2", "insight-3", "insight-4"]


# ---------------------------------------------------------------------------
# Sparse / empty buffer
# ---------------------------------------------------------------------------


def test_derive_from_session_empty_buffer_returns_empty(tmp_path: Path):
    """Buffer file exists but is empty (no user/assistant turns) → empty result."""
    buffer_dir = tmp_path / ".deriver-buffer"
    proj_dir = buffer_dir / PROJECT_HASH
    proj_dir.mkdir(parents=True, exist_ok=True)
    path = proj_dir / f"{SESSION_ID}.jsonl"
    path.write_text("", encoding="utf-8")

    insert_fn, captured = _make_fake_insert()
    llm_fn = _make_llm([{"type": "user", "name": "x", "content": "y"}])

    result = derive_from_session(
        SESSION_ID,
        project_hash=PROJECT_HASH,
        llm_fn=llm_fn,
        insert_fn=insert_fn,
        buffer_root=buffer_dir,
    )

    assert result == []
    assert captured == []


def test_derive_from_session_missing_buffer_returns_empty(tmp_path: Path):
    """No buffer directory at all → empty result."""
    buffer_dir = tmp_path / ".deriver-buffer"

    insert_fn, captured = _make_fake_insert()
    llm_fn = _make_llm([{"type": "user", "name": "x", "content": "y"}])

    result = derive_from_session(
        "unknown-session",
        project_hash=PROJECT_HASH,
        llm_fn=llm_fn,
        insert_fn=insert_fn,
        buffer_root=buffer_dir,
    )

    assert result == []
    assert captured == []


# ---------------------------------------------------------------------------
# Scrubber integration
# ---------------------------------------------------------------------------


def test_scrubber_wired_on_tags(tmp_path: Path):
    """Tags MUST pass through scrub() like name/content/description.

    Regression: round-3 lowercased and length-capped tags but never called
    scrub() on them. An LLM-fabricated tag like `aws-AKIAIOSFODNN7EXAMPLE`
    would land in memories.tags unredacted, leaking a credential through a
    column the privacy invariant claims to cover.
    """
    buffer_dir = tmp_path / ".deriver-buffer"
    _write_buffer(
        buffer_dir,
        PROJECT_HASH,
        SESSION_ID,
        [
            _asst_turn("ok"),
            _user_turn("some content"),
        ],
    )
    # The literal AWS-key example MUST be redacted by the scrubber.
    candidates = [
        {
            "type": "feedback",
            "project": "jarvis",
            "name": "test",
            "description": "test",
            "content": "test",
            "tags": ["normal-tag", "leaked-AKIAIOSFODNN7EXAMPLE"],
        },
    ]
    llm_fn = _make_llm(candidates)
    insert_fn, captured = _make_fake_insert()

    result = derive_from_session(
        SESSION_ID,
        project_hash=PROJECT_HASH,
        llm_fn=llm_fn,
        insert_fn=insert_fn,
        buffer_root=buffer_dir,
    )

    assert len(result) == 1
    row = captured[0]
    persisted_tags = row["tags"]
    # The AWS key must not survive in any tag:
    for t in persisted_tags:
        assert "AKIAIOSFODNN7EXAMPLE" not in t, (
            f"Tag {t!r} contains unscrubbed AWS key — scrub() not wired on tags."
        )


def test_cap_holds_when_all_inserts_fail(tmp_path: Path):
    """MAX_CANDIDATES cap must fire on attempted (not just succeeded) inserts.

    Regression: round-3 incremented the cap counter only on successful
    inserts; a persistent insert failure (RLS rejection after credential
    rotation, schema mismatch, etc.) made the cap silently never fire and
    the pipeline attempted N>>5 candidates with N failure log lines each.
    """
    buffer_dir = tmp_path / ".deriver-buffer"
    _write_buffer(
        buffer_dir,
        PROJECT_HASH,
        SESSION_ID,
        [
            _asst_turn("ok"),
            _user_turn("plenty of insights"),
        ],
    )
    # 20 valid candidates so the cap is the only thing that can stop the loop.
    candidates = []
    for i in range(20):
        candidates.append(
            {
                "type": "user",
                "project": None,
                "name": f"insight-{i}",
                "description": f"d{i}",
                "content": f"content {i}",
                "tags": ["t"],
            }
        )
    llm_fn = _make_llm(candidates)

    attempts: list[dict] = []

    def always_fail_insert(row):
        attempts.append(row)
        raise RuntimeError("simulated RLS rejection")

    result = derive_from_session(
        SESSION_ID,
        project_hash=PROJECT_HASH,
        llm_fn=llm_fn,
        insert_fn=always_fail_insert,
        buffer_root=buffer_dir,
    )

    assert result == []  # nothing inserted (all attempts failed)
    # Cap fires at MAX_CANDIDATES=5 attempts — NOT all 20.
    assert len(attempts) == 5, (
        f"Cap must fire on attempted inserts; got {len(attempts)} attempts "
        "(round-3 regression: only-on-success counter let this run unbounded)."
    )


def test_scrubber_wired_on_candidate_content(tmp_path: Path):
    """Given a transcript with a known privacy token, the persisted
    candidate content does NOT contain the token — proves the scrubber
    is wired in the pipeline."""
    buffer_dir = tmp_path / ".deriver-buffer"
    _write_buffer(
        buffer_dir,
        PROJECT_HASH,
        SESSION_ID,
        [
            _asst_turn("Sure, I'll look into it"),
            _user_turn("The AWS key is AKIAIOSFODNN7EXAMPLE, please fix the config"),
        ],
    )

    # LLM echoes the key back in the candidate content
    candidates = [
        {
            "type": "feedback",
            "project": "jarvis",
            "name": "fix-aws-config",
            "description": "Fix AWS config",
            "content": "User reported key AKIAIOSFODNN7EXAMPLE in config. Should rotate it.",
            "tags": ["security"],
        },
    ]
    llm_fn = _make_llm(candidates)
    insert_fn, captured = _make_fake_insert()

    result = derive_from_session(
        SESSION_ID,
        project_hash=PROJECT_HASH,
        llm_fn=llm_fn,
        insert_fn=insert_fn,
        buffer_root=buffer_dir,
    )

    assert len(result) == 1
    row = captured[0]
    # The raw AWS key must not appear in the persisted content
    assert "AKIAIOSFODNN7EXAMPLE" not in row["content"]
    # The scrubber should have redacted it
    assert "<<REDACTED:api_key_aws>>" in row["content"]


def test_scrubber_wired_on_description_and_name(tmp_path: Path):
    """Description and content pass through the scrubber (path redaction)."""
    buffer_dir = tmp_path / ".deriver-buffer"
    _write_buffer(
        buffer_dir,
        PROJECT_HASH,
        SESSION_ID,
        [
            _asst_turn("ok"),
            _user_turn("fix /home/alice/projects thing"),
        ],
    )

    candidates = [
        {
            "type": "user",
            "project": None,
            "name": "alice-project",
            "description": "User /home/alice/projects mentioned in context",
            "content": "User referenced their home directory /home/alice/projects during the session.",
            "tags": [],
        },
    ]
    llm_fn = _make_llm(candidates)
    insert_fn, captured = _make_fake_insert()

    result = derive_from_session(
        SESSION_ID,
        project_hash=PROJECT_HASH,
        llm_fn=llm_fn,
        insert_fn=insert_fn,
        buffer_root=buffer_dir,
    )

    assert len(result) == 1
    row = captured[0]
    # Content and description are scrubbed: /home/alice/ → <USER_PATH>/
    assert "/home/alice/" not in row["content"]
    assert "/home/alice/" not in row["description"]
    assert "<USER_PATH>/" in row["content"]
    # Name is a short slug ("alice-project") — the scrubber targets path
    # patterns, not bare words, so "alice" in the name slug is expected.
    assert row["name"] == "alice-project"


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def test_validate_candidate_rejects_missing_name():
    err = _validate_candidate({"type": "user", "content": "hello"})
    assert err is not None
    assert "name" in err.lower()


def test_validate_candidate_rejects_invalid_type():
    err = _validate_candidate({"type": "invalid", "name": "x", "content": "y"})
    assert err is not None
    assert "type" in err.lower()


def test_validate_candidate_missing_content():
    err = _validate_candidate({"type": "user", "name": "x"})
    assert err is not None
    assert "content" in err.lower()


def test_validate_candidate_accepts_valid():
    err = _validate_candidate(
        {
            "type": "user",
            "name": "good-one",
            "content": "This is valid content.",
        }
    )
    assert err is None


# ---------------------------------------------------------------------------
# _normalize_project via _build_row
# ---------------------------------------------------------------------------


def test_user_type_always_global_project():
    row = _build_row(
        {"type": "user", "name": "test", "content": "test", "tags": []},
        session_id=SESSION_ID,
    )
    assert not isinstance(row, str)  # not an error
    assert row["project"] is None
    assert row["type"] == "user"


def test_feedback_type_preserves_valid_project():
    row = _build_row(
        {"type": "feedback", "project": "jarvis", "name": "test", "content": "test", "tags": []},
        session_id=SESSION_ID,
    )
    assert not isinstance(row, str)
    assert row["project"] == "jarvis"
    assert row["type"] == "feedback"


def test_feedback_type_rejects_invalid_project():
    row = _build_row(
        {
            "type": "feedback",
            "project": "invalid-proj",
            "name": "test",
            "content": "test",
            "tags": [],
        },
        session_id=SESSION_ID,
    )
    assert not isinstance(row, str)
    assert row["project"] is None  # sanitised to None


# ---------------------------------------------------------------------------
# JSON parsing
# ---------------------------------------------------------------------------


def test_parse_empty_array():
    assert _parse_json_response("[]") == []


def test_parse_valid_json():
    result = _parse_json_response('[{"type":"user","name":"x"}]')
    assert len(result) == 1
    assert result[0]["name"] == "x"


def test_parse_json_with_code_fence():
    raw = '```json\n[{"type":"user","name":"x"}]\n```'
    result = _parse_json_response(raw)
    assert len(result) == 1


def test_parse_json_with_surrounding_text():
    raw = 'Here are the insights:\n\n[{"type":"user","name":"x","content":"hello"}]\n\nEnd.'
    result = _parse_json_response(raw)
    assert len(result) == 1


def test_parse_invalid_returns_empty():
    assert _parse_json_response("not json at all") == []


def test_parse_json_with_nested_tags_arrays_greedy_match():
    """Regex must match the OUTERMOST candidates array, not the inner tags array.

    Regression: round-2 used non-greedy ``\\[[\\s\\S]*?\\]`` which stopped at
    the first ``]`` — i.e. the nested ``"tags": [...]`` of the first candidate.
    ``json.loads`` then succeeded on a list of tag strings; every
    ``_validate_candidate`` failed; zero candidates inserted silently.
    """
    raw = (
        "Here are the candidates I extracted:\n\n"
        '[{"type":"user","name":"a","content":"x","tags":["t1","t2"]},'
        '{"type":"feedback","name":"b","content":"y","tags":["t3"]}]\n'
        "Hope this helps!"
    )
    result = _parse_json_response(raw)
    assert len(result) == 2, (
        "Greedy regex must capture the full outer array including both candidates; "
        f"got {len(result)} result(s)"
    )
    assert result[0]["name"] == "a"
    assert result[1]["name"] == "b"


# ---------------------------------------------------------------------------
# Cap enforcement: LLM returns more than MAX_CANDIDATES
# ---------------------------------------------------------------------------


def test_cap_at_5_llm_returns_8(tmp_path: Path):
    """MAX_CANDIDATES=5 enforced even when LLM returns 8 valid candidates."""
    buffer_dir = tmp_path / ".deriver-buffer"
    _write_buffer(
        buffer_dir,
        PROJECT_HASH,
        SESSION_ID,
        [
            _asst_turn("hi"),
            _user_turn("lots of insights here"),
        ],
    )
    many = []
    for i in range(8):
        many.append(
            {
                "type": "user",
                "project": None,
                "name": f"i-{i}",
                "description": f"d{i}",
                "content": f"content {i}",
                "tags": [],
            }
        )
    llm_fn = _make_llm(many)
    insert_fn, captured = _make_fake_insert()

    result = derive_from_session(
        SESSION_ID,
        project_hash=PROJECT_HASH,
        llm_fn=llm_fn,
        insert_fn=insert_fn,
        buffer_root=buffer_dir,
    )
    assert len(result) == 5
    assert len(captured) == 5


def test_cap_counts_valid_inserts_not_raw_indices(tmp_path: Path):
    """MAX_CANDIDATES cap counts VALID candidates, not raw list indices.

    Regression: round-2 indexed with ``enumerate`` and tripped ``i >= 5``;
    leading invalid entries burned indices and silently dropped valid
    candidates past position 4. With a list of two invalid + five valid +
    one extra, the cap should yield 5 valid inserts (not 3).
    """
    buffer_dir = tmp_path / ".deriver-buffer"
    _write_buffer(
        buffer_dir,
        PROJECT_HASH,
        SESSION_ID,
        [
            _asst_turn("hi"),
            _user_turn("some content"),
        ],
    )
    # Two leading invalids (missing required fields) + 5 valids + 1 extra.
    candidates = [
        {"type": "user"},  # missing name/content
        {"type": "feedback"},  # missing name/content
    ]
    for i in range(6):
        candidates.append(
            {
                "type": "user",
                "project": None,
                "name": f"valid-{i}",
                "description": f"d{i}",
                "content": f"valid content {i}",
                "tags": [],
            }
        )
    llm_fn = _make_llm(candidates)
    insert_fn, captured = _make_fake_insert()

    result = derive_from_session(
        SESSION_ID,
        project_hash=PROJECT_HASH,
        llm_fn=llm_fn,
        insert_fn=insert_fn,
        buffer_root=buffer_dir,
    )
    assert len(result) == 5, f"Expected 5 valid inserts (cap counts valid_seen); got {len(result)}"
    assert len(captured) == 5
    # The first 5 VALID candidates (indexes 2..6 in the raw list) must land.
    assert all(row["name"].startswith("valid-") for row in captured)
    # The very last valid (valid-5) is dropped — that's the cap kicking in.
    names = [row["name"] for row in captured]
    assert "valid-0" in names
    assert "valid-4" in names
    assert "valid-5" not in names


# ---------------------------------------------------------------------------
# LLM failure / fallback
# ---------------------------------------------------------------------------


def test_llm_failure_returns_empty_no_half_write(tmp_path: Path):
    """When the LLM returns None (both backends failed), no rows are
    inserted — no half-write to memories."""
    buffer_dir = tmp_path / ".deriver-buffer"
    _write_buffer(
        buffer_dir,
        PROJECT_HASH,
        SESSION_ID,
        [
            _asst_turn("hi"),
            _user_turn("some text"),
        ],
    )
    llm_fn = _make_failing_llm()
    insert_fn, captured = _make_fake_insert()

    result = derive_from_session(
        SESSION_ID,
        project_hash=PROJECT_HASH,
        llm_fn=llm_fn,
        insert_fn=insert_fn,
        buffer_root=buffer_dir,
    )
    assert result == []
    assert captured == []


# ---------------------------------------------------------------------------
# Shape: all inserted rows have the documented fields
# ---------------------------------------------------------------------------


def test_inserted_rows_have_required_shape(tmp_path: Path):
    """Every inserted candidate has requires_review=true,
    source_provenance='deriver:<session-id>', valid type."""
    buffer_dir = tmp_path / ".deriver-buffer"
    _write_buffer(
        buffer_dir,
        PROJECT_HASH,
        SESSION_ID,
        [
            _asst_turn("ok"),
            _user_turn("Using fixtures for test data is cleaner"),
            _asst_turn("agreed"),
            _user_turn("We should add more type hints"),
        ],
    )

    candidates = [
        {
            "type": "feedback",
            "project": "jarvis",
            "name": "use-fixtures",
            "description": "Prefer fixtures",
            "content": "Use fixtures for test data setup.",
            "tags": ["testing"],
        },
        {
            "type": "user",
            "project": None,
            "name": "likes-type-hints",
            "description": "Type hints preference",
            "content": "User prefers adding type hints.",
            "tags": ["coding-style"],
        },
    ]
    llm_fn = _make_llm(candidates)
    insert_fn, captured = _make_fake_insert()

    result = derive_from_session(
        SESSION_ID,
        project_hash=PROJECT_HASH,
        llm_fn=llm_fn,
        insert_fn=insert_fn,
        buffer_root=buffer_dir,
    )

    assert len(result) == 2
    for row in captured:
        assert row["requires_review"] is True
        assert row["source_provenance"] == f"deriver:{SESSION_ID}"
        assert row["type"] in ("user", "feedback")
        assert row["merge_targets"] is None
        assert isinstance(row["name"], str) and row["name"]
        assert isinstance(row["content"], str) and row["content"]


# ---------------------------------------------------------------------------
# No insert without scrubber — enforced by pipeline shape
# ---------------------------------------------------------------------------


def test_pipeline_shape_enforces_scrub_before_insert(tmp_path: Path):
    """The pipeline never calls insert_fn without first calling scrub on
    the candidate content.  Verified structurally: _build_row always calls
    scrub() on content/description/name before returning the row dict."""
    buffer_dir = tmp_path / ".deriver-buffer"
    _write_buffer(
        buffer_dir,
        PROJECT_HASH,
        SESSION_ID,
        [
            _asst_turn("ok"),
            _user_turn("secret is sk-AbCdEfGhIjKlMnOpQrStUvWxYz123456"),
        ],
    )

    candidates = [
        {
            "type": "feedback",
            "project": "jarvis",
            "name": "found-secret",
            "description": "There is a secret",
            "content": "sk-AbCdEfGhIjKlMnOpQrStUvWxYz123456",
            "tags": [],
        },
    ]
    llm_fn = _make_llm(candidates)
    insert_fn, captured = _make_fake_insert()

    result = derive_from_session(
        SESSION_ID,
        project_hash=PROJECT_HASH,
        llm_fn=llm_fn,
        insert_fn=insert_fn,
        buffer_root=buffer_dir,
    )

    assert len(result) == 1
    row = captured[0]
    # The raw OpenAI key format (sk- + 20+ alnum) should be scrubbed.
    assert "sk-AbCdEfGhIjKlMnOpQrStUvWxYz123456" not in row["content"]
    assert "<<REDACTED:api_key_openai>>" in row["content"]
