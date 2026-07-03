"""Reusable secret-scrubber module for the two-layer privacy model (Slice 3, #553).

Pure function ``scrub(text: str) -> tuple[str, dict[str, int]]`` that
returns scrubbed text plus a dict of per-pattern fire counts.  Zero-count
dict when no patterns fire.

Patterns redacted:
  - API key prefixes (Anthropic ``sk-ant-``, OpenAI ``sk-``, GitHub ``ghp_``,
    Slack ``xox[baprs]-``, JWT ``eyJ…``, AWS ``AKIA``)
  - ``.env`` blocks (lines matching ``^[A-Z_]+=.{8,}$`` inside fenced or
    labelled env content)
  - Path normalisation (Windows ``C:\\Users\\<name>\\…``, macOS
    ``/Users/<name>/…``, Linux ``/home/<name>/…`` → ``<USER_PATH>/…``)

Consumed by:
  - Slice 4 (MCP write-path scrubber in ``mcp-memory/server.py``)
  - Slice 6 (SessionEnd hook for Deriver input sanitisation)
"""

from __future__ import annotations

import re
from typing import Tuple

# ---------------------------------------------------------------------------
# Pattern definitions
# ---------------------------------------------------------------------------

# API key / token prefixes — match the key prefix plus sufficient entropy
# to avoid false positives on short random strings.
#
# Anthropic keys (`sk-ant-api03-<entropy>`) must be matched BEFORE the OpenAI
# pattern: the `-` after `sk-ant` terminates OpenAI's `[A-Za-z0-9]{20,}` run at
# length 3, so the OpenAI pattern never catches an Anthropic key. This is the
# credential type most likely to surface in *this* codebase (every Claude Code
# session handles `sk-ant-*` keys), so a dedicated pattern is non-negotiable.
# Real sk-ant-api03 keys carry ~60-90 entropy chars after the 12-char prefix;
# require {30,} (like the GitHub pattern) to keep the false-positive surface low.
PAT_API_KEY_ANTHROPIC = re.compile(r"sk-ant-[A-Za-z0-9-]{30,}")
PAT_API_KEY_OPENAI = re.compile(r"sk-[A-Za-z0-9]{20,}")
PAT_API_KEY_GITHUB = re.compile(r"ghp_[A-Za-z0-9]{30,}")
PAT_API_KEY_SLACK = re.compile(r"xox[baprs]-[A-Za-z0-9-]{12,}")
PAT_API_KEY_JWT = re.compile(r"eyJ[A-Za-z0-9_=-]+\.eyJ[A-Za-z0-9_=-]+\.[A-Za-z0-9_=-]+")
PAT_API_KEY_AWS = re.compile(r"AKIA[0-9A-Z]{16}")
# VoyageAI keys (`pa-<entropy>`) — this codebase actively reads VOYAGE_API_KEY
# for embeddings, so a Voyage key is a realistic leak vector here. Require
# {32,} alphanumerics after the `pa-` prefix (real keys carry ~40+) to keep the
# false-positive surface near zero — the short prefix alone would be too eager.
PAT_API_KEY_VOYAGEAI = re.compile(r"pa-[A-Za-z0-9]{32,}")

# Combined API key patterns for counting. Anthropic is first so `sk-ant-*` is
# attributed to its own pattern, not partially mangled by the OpenAI pass.
API_KEY_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("api_key_anthropic", PAT_API_KEY_ANTHROPIC),
    ("api_key_openai", PAT_API_KEY_OPENAI),
    ("api_key_github", PAT_API_KEY_GITHUB),
    ("api_key_slack", PAT_API_KEY_SLACK),
    ("api_key_jwt", PAT_API_KEY_JWT),
    ("api_key_aws", PAT_API_KEY_AWS),
    ("api_key_voyageai", PAT_API_KEY_VOYAGEAI),
]

# Non-API-key pattern names emitted by scrub() (the env-block + path passes).
# Exported so consumers (write_scrubber) derive their known-name set from the
# source of truth instead of hardcoding string literals that silently drift if
# a pattern is renamed here. Keep in lockstep with the fires keys set below.
EXTRA_PATTERN_NAMES: frozenset[str] = frozenset({"env_block", "path_username"})

# .env block detection: lines matching `^[A-Z_]+=.{8,}$` inside env content.
# Matches fenced blocks where the language is "env" / "dotenv" / ".env", or a
# code fence with no language label that looks like env content (all lines are
# assignments).  Also matches plain labelled blocks.
PAT_ENV_BLOCK = re.compile(
    r"(?:^```(?:env|dotenv|\.env)\s*\n"
    r"|^```\s*\n(?=(?:[A-Z_]+=.{8,}\n)+))"
    r"((?:[A-Z_]+=.{8,}\n)+)"
    r"^```",
    re.MULTILINE,
)

# Path normalisation — replace the *entire* platform-specific user-path
# prefix with ``<USER_PATH>/`` (or ``<USER_PATH>\\`` on Windows) so the
# user's name and device layout are never recoverable from scrubbed output.
PAT_PATH_WINDOWS = re.compile(
    r"(?i)[A-Z]:\\Users\\[^\\/]+\\",
)
PAT_PATH_MACOS = re.compile(
    r"/Users/[^/]+/",
)
PAT_PATH_LINUX = re.compile(
    r"/home/[^/]+/",
)

PATH_REPLACEMENTS: list[tuple[re.Pattern, str]] = [
    (PAT_PATH_WINDOWS, r"<USER_PATH>\\"),
    (PAT_PATH_MACOS, "<USER_PATH>/"),
    (PAT_PATH_LINUX, "<USER_PATH>/"),
]

# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def scrub(text: str) -> Tuple[str, dict[str, int]]:
    """Scrub secrets from *text*, returning (clean_text, fire_counts).

    *fire_counts* maps pattern names to the number of redactions applied.
    When nothing fires, the dict is empty.
    """
    fires: dict[str, int] = {}
    result = text

    # 1. API key pattern replacements
    for name, pat in API_KEY_PATTERNS:
        new_result, n = pat.subn(f"<<REDACTED:{name}>>", result)
        if n:
            fires[name] = fires.get(name, 0) + n
            result = new_result

    # 2. .env block replacement
    def _replace_env_block(m: re.Match) -> str:
        """Replace each line in an env block with a redacted placeholder."""
        block = m.group(1)
        n_lines = block.count("\n")
        fires["env_block"] = fires.get("env_block", 0) + n_lines
        redacted_lines = "<<REDACTED:env_line>>\n" * n_lines
        # Reconstruct with the fence markers
        fence_start = m.group(0)[: m.start(1) - m.start()]
        return fence_start + redacted_lines + "```"

    result = PAT_ENV_BLOCK.sub(_replace_env_block, result)

    # 3. Path normalisation — replace prefix+username with <USER_PATH>
    for pat, repl in PATH_REPLACEMENTS:
        result, n = pat.subn(repl, result)
        if n:
            fires["path_username"] = fires.get("path_username", 0) + n

    return result, fires
