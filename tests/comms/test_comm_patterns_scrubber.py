"""Scrubber unit tests — secrets + PII regression coverage.

#581 acceptance criterion: planted email, planted path with username, and
planted API-key-shape all trigger ``redacted=True`` and replace the quote
with a placeholder.
"""

from __future__ import annotations

from comm_patterns.scrubber import scrub


def test_clean_text_passes_through_unchanged():
    text = "правильно, спасибо — теперь всё работает."
    out, redacted = scrub(text)
    assert out == text
    assert redacted is False


def test_planted_email_is_redacted():
    text = "user said: john.doe@example.com is my contact"
    out, redacted = scrub(text)
    assert redacted is True
    assert "john.doe@example.com" not in out
    assert "[REDACTED:email]" in out


def test_planted_path_with_username_is_redacted_windows():
    text = r"check C:\Users\jdoe\GitHub\jarvis\config.json"
    out, redacted = scrub(text)
    assert redacted is True
    assert "jdoe" not in out
    assert "[REDACTED:user]" in out
    # Path structure preserved.
    assert "GitHub" in out


def test_planted_path_with_username_is_redacted_posix_home():
    text = "ls /home/jdoe/.cache/jarvis-comms-analysis"
    out, redacted = scrub(text)
    assert redacted is True
    assert "/home/jdoe" not in out
    assert "[REDACTED:user]" in out


def test_planted_path_with_username_is_redacted_macos_users():
    text = "open /Users/jdoe/Documents/secret.txt"
    out, redacted = scrub(text)
    assert redacted is True
    assert "/Users/jdoe" not in out
    assert "[REDACTED:user]" in out


def test_planted_api_key_shape_is_redacted_anthropic():
    fake_key = "sk-ant-" + "a" * 40
    text = f"my key is {fake_key} please remove"
    out, redacted = scrub(text)
    assert redacted is True
    assert fake_key not in out
    assert "[REDACTED:secret:anthropic-key]" in out


def test_planted_api_key_shape_is_redacted_github_token():
    fake_token = "ghp_" + "x" * 40
    text = f"GITHUB_TOKEN={fake_token}"
    out, redacted = scrub(text)
    assert redacted is True
    assert fake_token not in out


def test_planted_jwt_is_redacted():
    # Real JWTs have three segments. Two-segment shape leaks the signature
    # so the scrubber explicitly requires the third segment to match.
    fake_jwt = "eyJ" + "A" * 40 + "." + "B" * 20 + "." + "C" * 30
    text = f"Authorization: Bearer {fake_jwt}"
    out, redacted = scrub(text)
    assert redacted is True
    assert fake_jwt not in out


def test_two_segment_jwt_is_not_redacted():
    """Sentinel: only three-segment shape matches. If this ever flips
    silently, real JWTs lose their signature segment."""
    two_segment = "eyJ" + "A" * 40 + "." + "B" * 20
    out, redacted = scrub(two_segment)
    assert redacted is False
    assert out == two_segment


def test_sk_ant_key_is_labeled_anthropic_not_openai():
    """Negative-lookahead guard against the openai pattern stealing
    Anthropic keys when the list is reordered."""
    fake_key = "sk-ant-" + "a" * 40
    out, redacted = scrub(fake_key)
    assert redacted is True
    assert "[REDACTED:secret:anthropic-key]" in out
    assert "openai-key" not in out


def test_credential_assignment_consumes_full_tail():
    """The credential regex is greedy on its value class — the whole tail
    of contiguous matchable chars is replaced, not just the first 16."""
    word = "p" + "assword"
    fixture = f"{word}=" + "a" * 16 + "1234567_some_more_chars_here"
    out, redacted = scrub(fixture)
    assert redacted is True
    assert "1234567_some_more_chars_here" not in out
    assert "[REDACTED:secret:credential-assignment]" in out


def test_dotenv_short_values_are_not_false_positives():
    """20-char threshold: version strings and build numbers must NOT
    redact. Below-threshold values stay in the anchor for analyst
    readability."""
    for clean in (
        "VERSION=1.2.3.4.5.6",
        "BUILD_NUMBER=2026051012",
        "PORT=8080",
        "DEBUG=true",
    ):
        out, redacted = scrub(clean)
        assert redacted is False, f"unexpected redaction of: {clean}"
        assert out == clean


def test_dotenv_long_value_is_redacted():
    """At ≥20 chars, env-var-shaped lines do redact — connection strings
    and tokens that don't match the named-credential pattern."""
    line = "DATABASE_URL=postgres://user:p4ss@host.example.com:5432/dbname"
    out, redacted = scrub(line)
    assert redacted is True
    assert "DATABASE_URL=[REDACTED:env]" in out


def test_dotenv_shaped_value_is_redacted():
    # Non-credential-named var so the credential-assignment regex doesn't
    # win first — exercises the dotenv fallback specifically.
    text = "DEPLOY_HASH=abcdef1234567890abcdefghi"  # 25 chars, ≥20 threshold
    out, redacted = scrub(text)
    assert redacted is True
    assert "abcdef1234567890abcdefghi" not in out
    assert "DEPLOY_HASH=[REDACTED:env]" in out


def test_credential_assignment_is_redacted():
    text = "SOME_TOKEN=abcdef1234567890abcdef"
    out, redacted = scrub(text)
    assert redacted is True
    assert "abcdef1234567890abcdef" not in out
    assert "[REDACTED:secret:credential-assignment]" in out


def test_multiple_substitutions_each_count():
    fake_key = "sk-" + "z" * 40
    text = f"contact me at me@example.com with {fake_key}"
    out, redacted = scrub(text)
    assert redacted is True
    assert "me@example.com" not in out
    assert fake_key not in out


def test_empty_text_returns_false():
    out, redacted = scrub("")
    assert out == ""
    assert redacted is False


def test_scrubber_secret_labels_match_secret_scanner_coverage():
    """Drift sentinel: scrubber's secret-pattern labels must cover the
    same key types as ``scripts/secret-scanner.py`` (Pillar-9 Sprint-1).
    Regex bodies legitimately differ — JWT got tightened to 3 segments,
    sk- got a negative-lookahead — but the *coverage* must not drift.

    Counterpart in classifier tests reads schema.sql for the enum; this
    one reads secret-scanner.py for the label set."""
    import re as _re
    from pathlib import Path
    from comm_patterns.scrubber import _SECRET_PATTERNS

    scanner_src = (
        Path(__file__).resolve().parent.parent.parent / "scripts" / "secret-scanner.py"
    ).read_text(encoding="utf-8")

    # ------------------------------------------------------------------
    # Regex contract: extracts the 2nd element (human label) from
    # ``(r"...", "Friendly Label")`` tuples in ``SECRET_PATTERNS``.
    #
    # Pattern: ``, "Label"`` where Label starts with a letter, contains
    # alphanumerics / spaces / slashes / hyphens, and ends with ``Key``,
    # ``Token``, or ``PAT``.
    #
    # This omits ``BASH_DANGER_PATTERNS`` labels (e.g. "Reading .env file")
    # and ``SECRET_PATTERNS`` entries whose label suffix doesn't match
    # the Key/Token/PAT convention ("Credential assignment").
    #
    # Breaking conditions:
    #  - Tuple format changes (namedtuple, dict, dataclass) -> no match
    #  - New label doesn't end with Key/Token/PAT -> silently dropped
    #  - Label starts with non-alpha (e.g. "2FA Token") -> no match
    #
    # If you add a secret family whose label doesn't match this pattern,
    # update the regex to cover it AND add the family to the floor below.
    #
    # Known limitation: ``-`` was missing from the original character class
    # so "OpenAI-style API Key" was silently dropped before this test was
    # strengthened. Keep the class synced with label naming conventions.
    # ------------------------------------------------------------------
    _LABEL_RE = _re.compile(
        r',\s*"([A-Za-z][-A-Za-z0-9 /]+(?:Key|Token|PAT))"'
    )
    scanner_labels = set(
        m.group(1).lower().replace(" ", "-")
        for m in _re.finditer(_LABEL_RE, scanner_src)
    )
    scrubber_labels = {label for _, label in _SECRET_PATTERNS}

    def _stem(s: str) -> str:
        return s.lower().replace("-", "").replace(" ", "")

    scrubber_stems = {_stem(s) for s in scrubber_labels}
    # Floor of secret families we must always carry. Drift below the
    # floor (a family disappearing) trips the assert; new families above
    # the floor are silent because additions are never the bug.
    expected_floor = {"awskey", "anthropickey", "githubtoken", "openaikey",
                      "voyagekey", "firecrawlkey", "slacktoken", "telegramtoken"}
    # 1) Scrubber must cover every high-confidence family.
    assert expected_floor.issubset(scrubber_stems), (
        f"scrubber missing high-confidence secret families: "
        f"{expected_floor - scrubber_stems}"
    )
    # 2) Scanner must also cover the same floor (bidirectional drift
    #    sentinel). If the tuple format in secret-scanner.py changes and
    #    the regex silently drops labels, this catches the drift.
    #    Format matches scanner_labels (lowercased, hyphens for spaces).
    scanner_floor = {
        "aws-access-key", "anthropic-api-key", "github-token",
        "openai-style-api-key", "voyage-ai-key", "firecrawl-api-key",
        "slack-token", "telegram-bot-token",
    }
    assert scanner_floor.issubset(scanner_labels), (
        f"secret-scanner.py missing or regex failed to extract: "
        f"{scanner_floor - scanner_labels}"
    )


def test_quote_truncation_is_caller_responsibility():
    """Scrubber doesn't truncate — the row builder does. This guards against
    the scrubber growing surprise side-effects on length."""
    text = "a" * 5000
    out, redacted = scrub(text)
    assert out == text
    assert redacted is False
