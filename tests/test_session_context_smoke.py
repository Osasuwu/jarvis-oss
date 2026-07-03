"""Unit tests for scripts/session-context.py ``--smoke`` dry health probe (#963).

The install manifest runs ``session-context.py --smoke`` as a health command.
Smoke mode must:
  - short-circuit before any Supabase client construction / network call
  - never block on stdin (checked before _read_hook_input)
  - exit 0 regardless of credential presence (creds are owner config, not an
    install failure the health-check should fail on)
  - report whether credentials are present, for operator visibility

Same session-context import scaffolding as test_mirror_drift.py.
"""

from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path
from unittest.mock import MagicMock

import pytest


# Stub optional deps so module import succeeds without them installed. The
# supabase/ dir in repo root creates a namespace package already in
# sys.modules — add create_client to the existing module rather than replace
# it (same approach as test_mirror_drift.py / test_milestone_sweep.py).
for _stub in ("dotenv", "supabase"):
    mod = sys.modules.setdefault(_stub, types.ModuleType(_stub))
    if _stub == "dotenv":
        mod.load_dotenv = lambda *a, **k: None
    if _stub == "supabase" and not hasattr(mod, "create_client"):
        mod.create_client = MagicMock()

_PATH = Path(__file__).resolve().parent.parent / "scripts" / "session-context.py"
_spec = importlib.util.spec_from_file_location("session_context_smoke", _PATH)
sc = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(sc)


def test_run_smoke_check_makes_no_network_call(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """_run_smoke_check must never construct a Supabase client."""
    sentinel = MagicMock(side_effect=AssertionError("create_client called in smoke"))
    monkeypatch.setattr(sc, "create_client", sentinel)
    monkeypatch.setenv("SUPABASE_URL", "https://x.supabase.co")
    monkeypatch.setenv("SUPABASE_KEY", "anon-key")

    sc._run_smoke_check()

    # Smoke message goes to stderr (#963): the installer redirects child stdout
    # to DEVNULL and captures only stderr, so a stdout print would be discarded.
    err = capsys.readouterr().err
    assert "smoke ok" in err
    assert "creds present" in err
    sentinel.assert_not_called()


def test_run_smoke_check_creds_absent_still_exits_clean(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Missing creds is config-pending, not an install failure → exit 0 + note."""
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.delenv("SUPABASE_KEY", raising=False)

    sc._run_smoke_check()  # must not raise / sys.exit non-zero

    err = capsys.readouterr().err
    assert "smoke ok" in err
    assert "creds absent" in err


def test_main_smoke_short_circuits_before_supabase(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """main() with --smoke must not connect to Supabase or read hook stdin."""
    monkeypatch.setattr(sys, "argv", ["session-context.py", "--smoke"])
    monkeypatch.setattr(
        sc, "create_client",
        MagicMock(side_effect=AssertionError("create_client called in smoke")),
    )
    monkeypatch.setattr(
        sc, "_read_hook_input",
        MagicMock(side_effect=AssertionError("_read_hook_input called in smoke")),
    )
    monkeypatch.setenv("SUPABASE_URL", "https://x.supabase.co")
    monkeypatch.setenv("SUPABASE_KEY", "anon-key")

    sc.main()

    assert "smoke ok" in capsys.readouterr().err


def test_main_without_smoke_does_not_smoke(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Absent --smoke, main() takes the normal path (no smoke line emitted)."""
    monkeypatch.setattr(sys, "argv", ["session-context.py"])
    # No creds → main() prints the SUPABASE_URL/KEY warning and returns early,
    # never reaching the smoke branch.
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.delenv("SUPABASE_KEY", raising=False)
    monkeypatch.setattr(sc, "_read_hook_input", MagicMock(return_value={}))

    sc.main()

    captured = capsys.readouterr()
    assert "smoke ok" not in captured.out
    assert "smoke ok" not in captured.err
