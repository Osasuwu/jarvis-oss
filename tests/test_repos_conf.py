"""Tests for the shared config/repos.conf parser (scripts/repos_conf.py).

Two entry points, deliberately different strictness:
- ``parse_repos_conf`` — legacy, permissive, name-only. Existing consumers
  (status_gather.py, morning_gather.py) depend on this never raising and
  never validating trailing tokens (#1059 behavior, must not regress).
- ``parse_repos_conf_entries`` — new structured parser with per-token
  validation, for consumers that need repos.conf metadata (weekly-release
  skill).
"""

from __future__ import annotations

import pytest

from scripts.repos_conf import parse_repos_conf, parse_repos_conf_entries


def test_parse_repos_conf_bare_lines():
    raw = "Osasuwu/jarvis\nSergazyNarynov/redrobot\n"
    assert parse_repos_conf(raw) == ["Osasuwu/jarvis", "SergazyNarynov/redrobot"]


def test_parse_repos_conf_ignores_project_token():
    raw = "Osasuwu/jarvis project=3\nSergazyNarynov/redrobot project=1\n"
    assert parse_repos_conf(raw) == ["Osasuwu/jarvis", "SergazyNarynov/redrobot"]


def test_parse_repos_conf_ignores_unknown_trailing_token():
    # Trailing tokens are discarded regardless of recognition — including
    # unknown ones (#1059, exercised historically as `inactive=true`).
    raw = "SergazyNarynov/redrobot project=1 inactive=true\n"
    assert parse_repos_conf(raw) == ["SergazyNarynov/redrobot"]


def test_parse_repos_conf_skips_comments_and_blanks():
    raw = "# comment\n\nOsasuwu/jarvis\n"
    assert parse_repos_conf(raw) == ["Osasuwu/jarvis"]


def test_parse_repos_conf_never_raises_on_malformed_known_key_value():
    # The legacy function must stay pure/permissive — validation lives only
    # in parse_repos_conf_entries. A malformed known-key value must not
    # break the name-only consumers.
    raw = "Osasuwu/jarvis project=notanumber releases=weeekly\n"
    assert parse_repos_conf(raw) == ["Osasuwu/jarvis"]


def test_parse_repos_conf_entries_parses_known_tokens():
    raw = "Osasuwu/jarvis project=3 releases=weekly lang=ru,en\n"
    entries = parse_repos_conf_entries(raw)
    assert len(entries) == 1
    assert entries[0].name == "Osasuwu/jarvis"
    assert entries[0].tokens == {"project": "3", "releases": "weekly", "lang": "ru,en"}


def test_parse_repos_conf_entries_bare_line_has_empty_tokens():
    entries = parse_repos_conf_entries("Osasuwu/jarvis\n")
    assert entries[0].tokens == {}


def test_parse_repos_conf_entries_unknown_key_warns_not_raises(capsys):
    entries = parse_repos_conf_entries("SergazyNarynov/redrobot inactive=true\n")
    assert entries[0].name == "SergazyNarynov/redrobot"
    assert entries[0].tokens == {"inactive": "true"}
    captured = capsys.readouterr()
    assert "unknown" in captured.err.lower()


def test_parse_repos_conf_entries_bad_value_of_known_key_raises():
    with pytest.raises(ValueError, match="releases"):
        parse_repos_conf_entries("SergazyNarynov/redrobot releases=weeekly\n")


def test_parse_repos_conf_entries_bad_project_value_raises():
    with pytest.raises(ValueError, match="project"):
        parse_repos_conf_entries("Osasuwu/jarvis project=notanumber\n")


def test_parse_repos_conf_entries_lang_requires_nonempty_tokens():
    with pytest.raises(ValueError, match="lang"):
        parse_repos_conf_entries("Osasuwu/jarvis lang=ru,,en\n")
