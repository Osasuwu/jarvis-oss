"""Unit tests for issue #1252 AC3: always_load admission cap.

Cap: 4 entries / 6000 bytes combined, enforced at read time in both
scripts/session-context.py (_query_always_load) and scripts/eval-recall.py
(_load_session_context), per decision 3e6594f6-27da-45d9-96d8-516a46716425.

Tests that:
1. Both loaders truncate to ALWAYS_LOAD_MAX_ENTRIES when more rows are tagged.
2. Both loaders truncate on ALWAYS_LOAD_MAX_BYTES even under the entry cap.
3. Truncation prints a warning to stderr rather than raising.
4. Under-cap input passes through untouched.
"""

from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path
from unittest.mock import MagicMock


for _stub in ("dotenv", "supabase"):
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


def _load_module(filename, modname):
    path = Path(__file__).resolve().parent.parent.parent / "scripts" / filename
    spec = importlib.util.spec_from_file_location(modname, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[modname] = mod  # dataclass() needs cls.__module__ in sys.modules
    spec.loader.exec_module(mod)
    return mod


sc = _load_module("session-context.py", "session_context_1252")
erc = _load_module("eval-recall.py", "eval_recall_1252")


def _mock_client(rows):
    client = MagicMock()
    query = MagicMock()
    query.select.return_value = query
    query.contains.return_value = query
    query.is_.return_value = query
    query.order.return_value = query
    query.execute.return_value = MagicMock(data=rows)
    client.table.return_value = query
    return client


def _row(i, content=""):
    return {
        "id": f"mem_{i}",
        "name": f"rule_{i}",
        "type": "reference",
        "project": "global",
        "description": "d",
        "tags": ["always_load"],
        "content": content,
    }


def test_session_context_caps_entries(capsys):
    rows = [_row(i) for i in range(7)]
    client = _mock_client(rows)

    text, ids = sc._query_always_load(client, compact=True)

    assert len(ids) == sc.ALWAYS_LOAD_MAX_ENTRIES
    assert ids == [f"mem_{i}" for i in range(sc.ALWAYS_LOAD_MAX_ENTRIES)]
    assert "cap is" in capsys.readouterr().err


def test_session_context_caps_bytes(capsys):
    big = "x" * (sc.ALWAYS_LOAD_MAX_BYTES // 2 + 1)
    rows = [_row(0, big), _row(1, big), _row(2, big)]
    client = _mock_client(rows)

    text, ids = sc._query_always_load(client, compact=False)

    assert len(ids) == 1
    assert "byte budget" in capsys.readouterr().err


def test_session_context_under_cap_passes_through(capsys):
    rows = [_row(i) for i in range(2)]
    client = _mock_client(rows)

    text, ids = sc._query_always_load(client, compact=True)

    assert len(ids) == 2
    assert capsys.readouterr().err == ""


def test_eval_recall_caps_entries(capsys):
    rows = [_row(i) for i in range(7)]
    client = _mock_client(rows)

    text, counts = erc._load_session_context(client)

    assert counts["always_load"] == erc.ALWAYS_LOAD_MAX_ENTRIES
    assert "cap is" in capsys.readouterr().err


def test_eval_recall_caps_bytes(capsys):
    big = "x" * (erc.ALWAYS_LOAD_MAX_BYTES // 2 + 1)
    rows = [_row(0, big), _row(1, big), _row(2, big)]
    client = _mock_client(rows)

    text, counts = erc._load_session_context(client)

    assert counts["always_load"] == 1
    assert "byte budget" in capsys.readouterr().err


def test_caps_match_across_loaders():
    assert sc.ALWAYS_LOAD_MAX_ENTRIES == erc.ALWAYS_LOAD_MAX_ENTRIES
    assert sc.ALWAYS_LOAD_MAX_BYTES == erc.ALWAYS_LOAD_MAX_BYTES


if __name__ == "__main__":
    import pytest

    pytest.main([__file__, "-v"])
