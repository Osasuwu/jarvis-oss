"""Unit tests for scripts/deriver-accumulator.py — the Stop-hook buffer.

The accumulator runs on EVERY session Stop (registered in
``.claude-userlevel/settings.json``) and feeds the Deriver pipeline. It
shipped in #632 without test coverage; these characterization tests pin the
invariants stated in its docstring so the live Stop hook can't silently
regress:

- ``_project_hash`` is deterministic, 12 hex chars, path-normalising.
- ``_should_skip`` rejects sandcastle / worktree / empty cwd sessions.
- ``accumulate`` appends (never truncates), filters to user/assistant turns,
  skips malformed JSONL lines, and returns ``None`` on skip / missing input.

Pure stdlib module — no DB/network/HTTP, so nothing to stub. Importing via
``spec_from_file_location`` names the module ``deriver_accumulator`` (not
``__main__``), so the venv re-exec bootstrap guard does not fire on import.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

SCRIPT_PATH = Path(__file__).resolve().parent.parent / "scripts" / "deriver-accumulator.py"

# Hyphen in filename → import via spec_from_file_location (repo convention,
# see tests/test_consolidation_review.py).
spec = importlib.util.spec_from_file_location("deriver_accumulator", SCRIPT_PATH)
assert spec and spec.loader
acc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(acc)


# ---------------------------------------------------------------------------
# _project_hash
# ---------------------------------------------------------------------------


def test_project_hash_is_12_hex_chars():
    h = acc._project_hash("/home/user/GitHub/jarvis")
    assert len(h) == 12
    assert all(c in "0123456789abcdef" for c in h)


def test_project_hash_is_deterministic():
    p = "/home/user/GitHub/jarvis"
    assert acc._project_hash(p) == acc._project_hash(p)


def test_project_hash_differs_per_path():
    assert acc._project_hash("/home/user/jarvis") != acc._project_hash("/home/user/redrobot")


def test_project_hash_normalises_redundant_separators(tmp_path):
    # os.path.realpath collapses '//' and '.' segments, so equivalent paths
    # that point at the same dir must hash identically.
    base = str(tmp_path)
    assert acc._project_hash(base) == acc._project_hash(base + "/./")


# ---------------------------------------------------------------------------
# _should_skip
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "cwd",
    [
        "",
        "/srv/sandcastle/worktrees/x",
        "/home/user/Sandcastle/job",  # case-insensitive
        "/home/user/.claude/worktrees/funny-goldwasser",
    ],
)
def test_should_skip_rejects_nonstandard(cwd):
    assert acc._should_skip(cwd) is True


@pytest.mark.parametrize(
    "cwd",
    [
        "/home/user/GitHub/jarvis",
        "C:/Users/jdoe/GitHub/jarvis",
        "/home/user/.claude/skills",  # .claude but not a worktree
    ],
)
def test_should_skip_allows_standard(cwd):
    assert acc._should_skip(cwd) is False


# ---------------------------------------------------------------------------
# accumulate — helpers
# ---------------------------------------------------------------------------


def _write_transcript(path: Path, objs):
    path.write_text(
        "\n".join(json.dumps(o) for o in objs) + "\n",
        encoding="utf-8",
    )


def _read_buffer(buffer_path: Path):
    return [
        json.loads(line)
        for line in buffer_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


@pytest.fixture
def buffer_root(tmp_path, monkeypatch):
    """Redirect the module-level BUFFER_ROOT into a tmp dir."""
    root = tmp_path / "buffer"
    monkeypatch.setattr(acc, "BUFFER_ROOT", root)
    return root


# ---------------------------------------------------------------------------
# accumulate — skip / missing-input paths
# ---------------------------------------------------------------------------


def test_accumulate_returns_none_when_skipped(buffer_root, tmp_path):
    t = tmp_path / "t.jsonl"
    _write_transcript(t, [{"role": "user", "content": "hi"}])
    assert acc.accumulate("sess", str(t), "/srv/sandcastle/x") is None
    assert not buffer_root.exists()


def test_accumulate_returns_none_when_transcript_missing(buffer_root, tmp_path):
    missing = tmp_path / "nope.jsonl"
    assert acc.accumulate("sess", str(missing), str(tmp_path)) is None


# ---------------------------------------------------------------------------
# accumulate — content filtering
# ---------------------------------------------------------------------------


def test_accumulate_keeps_only_user_and_assistant(buffer_root, tmp_path):
    t = tmp_path / "t.jsonl"
    _write_transcript(
        t,
        [
            {"role": "user", "content": "q"},
            {"role": "system", "content": "sys"},
            {"role": "assistant", "content": "a"},
            {"role": "tool", "content": "result"},
            {"type": "summary"},  # no role
        ],
    )
    buf = acc.accumulate("sess", str(t), str(tmp_path))
    assert buf is not None
    rows = _read_buffer(buf)
    assert [r["role"] for r in rows] == ["user", "assistant"]


def test_accumulate_skips_malformed_lines(buffer_root, tmp_path):
    t = tmp_path / "t.jsonl"
    t.write_text(
        '{"role": "user", "content": "ok"}\n'
        "{not valid json\n"
        "\n"  # blank line
        '{"role": "assistant", "content": "ok2"}\n',
        encoding="utf-8",
    )
    buf = acc.accumulate("sess", str(t), str(tmp_path))
    rows = _read_buffer(buf)
    assert [r["content"] for r in rows] == ["ok", "ok2"]


def test_accumulate_preserves_unicode(buffer_root, tmp_path):
    t = tmp_path / "t.jsonl"
    _write_transcript(t, [{"role": "user", "content": "привет мир"}])
    buf = acc.accumulate("sess", str(t), str(tmp_path))
    rows = _read_buffer(buf)
    assert rows[0]["content"] == "привет мир"


# ---------------------------------------------------------------------------
# accumulate — append invariant (survives restart mid-session)
# ---------------------------------------------------------------------------


def test_accumulate_appends_not_truncates(buffer_root, tmp_path):
    t1 = tmp_path / "t1.jsonl"
    t2 = tmp_path / "t2.jsonl"
    _write_transcript(t1, [{"role": "user", "content": "first"}])
    _write_transcript(t2, [{"role": "assistant", "content": "second"}])

    buf1 = acc.accumulate("sess", str(t1), str(tmp_path))
    buf2 = acc.accumulate("sess", str(t2), str(tmp_path))

    # Same session id → same buffer file, second call appends.
    assert buf1 == buf2
    rows = _read_buffer(buf2)
    assert [r["content"] for r in rows] == ["first", "second"]


def test_accumulate_buffer_path_layout(buffer_root, tmp_path):
    t = tmp_path / "t.jsonl"
    _write_transcript(t, [{"role": "user", "content": "x"}])
    buf = acc.accumulate("sess-123", str(t), str(tmp_path))
    assert buf.name == "sess-123.jsonl"
    assert buf.parent.parent == buffer_root
    assert buf.parent.name == acc._project_hash(str(tmp_path))


def test_accumulate_creates_empty_buffer_when_no_qualifying_turns(buffer_root, tmp_path):
    # All turns filtered out → buffer file is created (mkdir + open 'a') but
    # stays empty. The function still returns the path, not None.
    t = tmp_path / "t.jsonl"
    _write_transcript(t, [{"role": "system", "content": "sys"}])
    buf = acc.accumulate("sess", str(t), str(tmp_path))
    assert buf is not None
    assert buf.exists()
    assert _read_buffer(buf) == []
