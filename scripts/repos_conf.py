"""repos_conf.py — shared parser for config/repos.conf.

Single source of truth for parsing repos.conf-style files: a bare
``owner/repo`` per line, optionally followed by whitespace-delimited
``key=value`` tokens (e.g. ``project=3``, ``releases=weekly``).

Two entry points, deliberately different strictness:

- ``parse_repos_conf`` — legacy, permissive, name-only. Only the first
  whitespace-delimited token (the ``owner/repo``) is extracted; every
  trailing token is silently discarded regardless of recognition. This
  is depended on by weekly_release_gather.py and its tests
  (#1059) — it must never raise and must never change shape.
- ``parse_repos_conf_entries`` — structured parser with per-token
  validation, for consumers that need repos.conf metadata (the
  weekly-release skill). An unknown key is forward-compat
  tolerated (non-fatal warning, OSS operators may add tokens this parser
  doesn't know yet); a known key with an invalid value is an error.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field

REPOS_CONF_RELPATH = "config/repos.conf"


def _valid_lang(value: str) -> bool:
    tokens = value.split(",")
    return bool(value) and all(tok.strip() for tok in tokens)


# Known keys and a validator for their value. A key absent from this dict
# is unknown — parsed into .tokens unvalidated, with a non-fatal warning.
_KNOWN_KEYS = {
    "project": str.isdigit,
    "releases": lambda v: v in {"weekly"},
    "lang": _valid_lang,
}


@dataclass
class RepoEntry:
    name: str
    tokens: dict[str, str] = field(default_factory=dict)


def parse_repos_conf(raw: str) -> list[str]:
    """Parse repos.conf content into owner/repo list (pure, tested directly).

    A line may carry trailing key=value tokens (e.g. ``project=3``, #1059);
    only the first whitespace-delimited token — the ``owner/repo`` — is the
    repo identifier. Bare lines (no tokens) are returned unchanged.
    """
    repos: list[str] = []
    for line in raw.splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            repos.append(line.split()[0])
    return repos


def _parse_line_tokens(name: str, raw_tokens: list[str]) -> dict[str, str]:
    tokens: dict[str, str] = {}
    for tok in raw_tokens:
        if "=" not in tok:
            print(
                f"[repos_conf] warning: ignoring malformed token {tok!r} on line for {name}",
                file=sys.stderr,
            )
            continue
        key, _, value = tok.partition("=")
        validator = _KNOWN_KEYS.get(key)
        if validator is None:
            print(
                f"[repos_conf] warning: unknown token key {key!r} on line for {name} "
                "(forward-compat: kept unvalidated)",
                file=sys.stderr,
            )
        elif not validator(value):
            raise ValueError(
                f"repos.conf: invalid value {value!r} for key {key!r} on line for {name}"
            )
        tokens[key] = value
    return tokens


def parse_repos_conf_entries(raw: str) -> list[RepoEntry]:
    """Parse repos.conf content into structured entries (name + tokens).

    Raises ValueError if a KNOWN key carries an invalid value (e.g.
    ``releases=weeekly``). An unknown key is forward-compat tolerated —
    non-fatal warning, token kept unvalidated in ``.tokens``.
    """
    entries: list[RepoEntry] = []
    for line in raw.splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            parts = line.split()
            name, raw_tokens = parts[0], parts[1:]
            entries.append(RepoEntry(name=name, tokens=_parse_line_tokens(name, raw_tokens)))
    return entries
