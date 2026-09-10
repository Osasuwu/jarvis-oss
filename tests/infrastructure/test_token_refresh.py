"""Unit test for scripts/token-refresh.py's load_repos() (#1572).

load_repos() used to append the whole trimmed repos.conf line, including
any trailing key=value token (e.g. ``project=3``) — the raw string was then
fed straight into ``gh secret set --repo <repo>``, which would fail the
moment a repos.conf line carried a token. Migrated to the shared
scripts/repos_conf.py parser; this test pins the fix.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

_PATH = Path(__file__).resolve().parent.parent.parent / "scripts" / "token-refresh.py"
_spec = importlib.util.spec_from_file_location("token_refresh", _PATH)
token_refresh = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(token_refresh)


def test_load_repos_strips_trailing_tokens(tmp_path, monkeypatch):
    conf = tmp_path / "repos.conf"
    conf.write_text(
        "# comment\nOsasuwu/jarvis project=3\nSergazyNarynov/redrobot project=1\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(token_refresh, "REPOS_CONF", str(conf))

    assert token_refresh.load_repos() == ["Osasuwu/jarvis", "SergazyNarynov/redrobot"]
