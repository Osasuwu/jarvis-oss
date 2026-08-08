"""Tests for scripts/lib/secret_scrubber.py — secret detection + redaction.

Covers each pattern: positive (redacts) + negative (no false positive on
natural text).
"""

from __future__ import annotations

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "scripts"))

from lib.secret_scrubber import scrub


# ── API keys ────────────────────────────────────────────────────────────


def test_openai_key_redacted():
    k = "sk-proj" + "AbCdEfGhIjKlMnOpQrStUvWxYz"
    cleaned, fires = scrub(f"my key is {k}")
    assert "<<REDACTED:api_key_openai>>" in cleaned
    assert fires["api_key_openai"] == 1


def test_openai_key_negative():
    """Short sk- prefix (not enough entropy) should NOT fire."""
    cleaned, fires = scrub("sk-test")
    assert cleaned == "sk-test"
    assert fires == {}


def test_anthropic_key_redacted():
    """sk-ant-api03-<entropy> must be caught by the dedicated Anthropic pattern.
    The `-` after `sk-ant` breaks the OpenAI run at 3 chars, so without this
    pattern the key would slip the gate entirely (the bug round-7 surfaced)."""
    k = "sk-ant-" + "api03-" + "0123456789abcdefghijABCDEFG"
    cleaned, fires = scrub(f"export ANTHROPIC_API_KEY={k}")
    assert "<<REDACTED:api_key_anthropic>>" in cleaned
    assert fires.get("api_key_anthropic") == 1
    # Attributed to its own pattern — not partially mangled as api_key_openai.
    assert "api_key_openai" not in fires
    assert k not in cleaned


def test_anthropic_key_negative():
    """Short sk-ant- prefix without enough entropy ({30,}) should NOT fire."""
    cleaned, fires = scrub("sk-ant-test")
    assert cleaned == "sk-ant-test"
    assert fires == {}


def test_github_token_redacted():
    t = "ghp_" + "ABCDEFGHIJKLM" + "NOPQRSTUVWXYZabcdefghij123456"
    cleaned, fires = scrub(f"token={t}")
    assert "<<REDACTED:api_key_github>>" in cleaned
    assert fires["api_key_github"] == 1


def test_github_token_negative():
    cleaned, fires = scrub("ghp_test")
    assert cleaned == "ghp_test"
    assert fires == {}


def test_slack_token_redacted():
    # Construct token dynamically to avoid GitHub's static secret scanner.
    # Prefix + entropy segments (test construct, not a real token).
    prefix = "xoxb"
    token = f"{prefix}-1111111111-aaaaaaaaaaaaaaaaaaaa"
    cleaned, fires = scrub(token)
    assert "<<REDACTED:api_key_slack>>" in cleaned
    assert fires["api_key_slack"] == 1


def test_jwt_redacted():
    part1 = "eyJhbGciOiJIUzI1NiJ9"
    part2 = "eyJzdWIiOiIxMjM0NTY3ODkwIn0"
    cleaned, fires = scrub(f"{part1}.{part2}.signature_part_here")
    assert "<<REDACTED:api_key_jwt>>" in cleaned
    assert fires["api_key_jwt"] == 1


def test_jwt_negative():
    """A single base64-encoded segment is not a JWT."""
    cleaned, fires = scrub("eyJhbGciOiJIUzI1NiJ9")
    assert fires == {}


def test_aws_key_redacted():
    k = "AKIA" + "IOSFODNN7EXAMPLE"
    cleaned, fires = scrub(f"AWS key: {k}")
    assert "<<REDACTED:api_key_aws>>" in cleaned
    assert fires["api_key_aws"] == 1


def test_aws_key_negative():
    cleaned, fires = scrub("AKIA1234")
    assert fires == {}


def test_voyageai_key_redacted():
    """VoyageAI `pa-<entropy>` keys must be caught — this codebase reads
    VOYAGE_API_KEY, so a Voyage key is a realistic leak vector here."""
    k = "pa-" + "0123456789abcdefghijABCDEFGHIJ0123456789"
    cleaned, fires = scrub(f"export VOYAGE_API_KEY={k}")
    assert "<<REDACTED:api_key_voyageai>>" in cleaned
    assert fires["api_key_voyageai"] == 1
    assert k not in cleaned


def test_voyageai_key_negative():
    """Short `pa-` runs (a word like 'pa-11' or prose) must NOT fire — the
    {32,} entropy floor keeps the false-positive surface near zero."""
    cleaned, fires = scrub("pa-11 is a typo for the pa-system")
    assert cleaned == "pa-11 is a typo for the pa-system"
    assert fires == {}


# ── .env blocks ──────────────────────────────────────────────────────────

ENV_BLOCK_SAMPLE = """Here is my config:

```env
DB_HOST=localhost
DB_PASSWORD=supersecretpassword123
API_SECRET=verylongapisecretvaluehere
```

That's it.
"""


def test_env_block_redacted():
    cleaned, fires = scrub(ENV_BLOCK_SAMPLE)
    assert "<<REDACTED:env_line>>" in cleaned
    assert "DB_PASSWORD=" not in cleaned
    assert fires["env_block"] == 3


def test_env_block_dotenv_label():
    text = "```dotenv\nTOKEN=very-long-secret-key-here-12345\n```"
    cleaned, fires = scrub(text)
    assert "<<REDACTED:env_line>>" in cleaned
    assert fires["env_block"] == 1


def test_env_block_negative():
    """A normal code block with short assignment should not fire."""
    text = "```\nkey=val\n```"
    cleaned, fires = scrub(text)
    assert cleaned == text
    assert fires == {}


# ── Path normalisation ───────────────────────────────────────────────────


def test_linux_path_redacted():
    cleaned, fires = scrub("I work at /home/alice/projects/jarvis")
    assert "<USER_PATH>/projects/jarvis" in cleaned
    assert fires["path_username"] == 1
    assert "alice" not in cleaned


def test_macos_path_redacted():
    cleaned, fires = scrub("Config at /Users/bob/.claude/settings.json")
    assert "<USER_PATH>/.claude/settings.json" in cleaned
    assert fires["path_username"] == 1
    assert "bob" not in cleaned


def test_windows_path_redacted():
    cleaned, fires = scrub(r"Code in C:\Users\charlie\src\project")
    assert r"<USER_PATH>\src\project" in cleaned or "<USER_PATH>/src/project" in cleaned
    assert fires["path_username"] == 1
    assert "charlie" not in cleaned


def test_path_negative():
    """Plain English mentions of 'home' or 'user' should not fire."""
    cleaned, fires = scrub("Go home, user!")
    assert fires == {}


def test_multiple_paths():
    """Multiple user paths in the same text should all be redacted."""
    text = "Compare /home/alice/proj1 and /home/bob/proj2"
    cleaned, fires = scrub(text)
    assert cleaned.count("<USER_PATH>") == 2
    assert fires["path_username"] == 2


# ── Empty / no-op ────────────────────────────────────────────────────────


def test_clean_text_unchanged():
    cleaned, fires = scrub("Hello world, this is normal text.")
    assert cleaned == "Hello world, this is normal text."
    assert fires == {}


def test_empty_string():
    cleaned, fires = scrub("")
    assert cleaned == ""
    assert fires == {}
