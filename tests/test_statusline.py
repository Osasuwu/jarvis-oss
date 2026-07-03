"""Tests for scripts/statusline.py — the Claude Code status-line formatter.

The script reads a status-line JSON payload on stdin and prints one line:
``<model> | ctx <N>% | <git-branch>``. These pin the formatting contract and
the fail-soft branches (missing fields, invalid JSON, non-repo cwd).
"""
import importlib.util
import io
import json
from pathlib import Path

_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "statusline.py"


def _load():
    spec = importlib.util.spec_from_file_location("statusline", _SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _run(monkeypatch, capsys, payload, *, raw=None):
    stdin = raw if raw is not None else json.dumps(payload)
    monkeypatch.setattr("sys.stdin", io.StringIO(stdin))
    _load().main()
    return capsys.readouterr().out


def test_full_payload(monkeypatch, capsys, tmp_path):
    # tmp_path is not a git repo -> branch resolves to "no repo"
    out = _run(
        monkeypatch,
        capsys,
        {
            "model": {"display_name": "Opus 4.8"},
            "context_window": {"used_percentage": 42.7},
            "cwd": str(tmp_path),
        },
    )
    assert out.strip() == "Opus 4.8 | ctx 42% | no repo"


def test_missing_context_window(monkeypatch, capsys, tmp_path):
    out = _run(
        monkeypatch,
        capsys,
        {"model": {"display_name": "Haiku"}, "cwd": str(tmp_path)},
    )
    assert out.startswith("Haiku |")
    assert "ctx -" in out


def test_missing_model_falls_back(monkeypatch, capsys, tmp_path):
    out = _run(monkeypatch, capsys, {"cwd": str(tmp_path)})
    assert out.startswith("? |")


def test_invalid_json_emits_nothing(monkeypatch, capsys):
    out = _run(monkeypatch, capsys, None, raw="not json{{")
    assert out == ""


def _seed_gen(monkeypatch, home, session_id, value):
    monkeypatch.setattr(Path, "home", lambda: home)
    d = home / ".claude" / "compaction-counts"
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{session_id}.txt").write_text(str(value), encoding="utf-8")


def test_compaction_gen_rendered(monkeypatch, capsys, tmp_path):
    home = tmp_path / "home"
    _seed_gen(monkeypatch, home, "sess-abc", 3)
    out = _run(
        monkeypatch,
        capsys,
        {
            "model": {"display_name": "Opus 4.8"},
            "context_window": {"used_percentage": 20},
            "session_id": "sess-abc",
            "cwd": str(tmp_path),
        },
    )
    assert "ctx 20% · gen 3" in out


def test_compaction_gen_zero_suppressed(monkeypatch, capsys, tmp_path):
    home = tmp_path / "home"
    _seed_gen(monkeypatch, home, "sess-abc", 0)
    out = _run(
        monkeypatch,
        capsys,
        {
            "model": {"display_name": "Opus 4.8"},
            "context_window": {"used_percentage": 20},
            "session_id": "sess-abc",
            "cwd": str(tmp_path),
        },
    )
    assert "gen" not in out
    assert "ctx 20%" in out


def test_compaction_gen_no_session_id(monkeypatch, capsys, tmp_path):
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "home")
    out = _run(
        monkeypatch,
        capsys,
        {
            "model": {"display_name": "Opus 4.8"},
            "context_window": {"used_percentage": 20},
            "cwd": str(tmp_path),
        },
    )
    assert "gen" not in out
