"""Write-time scrubber for comm_patterns anchor quotes.

Per ADR 0004 §2: substitute-mode scrubber, no second column for raw text.
Patterns reused from `scripts/secret-scanner.py` (Pillar-9 Sprint-1) plus
PII regexes called out in #581 (email, paths-with-username, .env-shaped,
API-key-shaped).

Returns (scrubbed_text, redacted_bool). The bool is set when any
substitution happened — written into `comm_patterns.redacted`.
"""

from __future__ import annotations

import re

# ---------------------------------------------------------------------------
# Secret patterns (reused literal regexes from scripts/secret-scanner.py).
# Kept in sync by hand: changes there should propagate here.
# ---------------------------------------------------------------------------
_SECRET_PATTERNS: list[tuple[str, str]] = [
    (r"AKIA[0-9A-Z]{16}", "aws-key"),
    (r"sk-ant-[a-zA-Z0-9_-]{20,}", "anthropic-key"),
    (r"gh[ps]_[A-Za-z0-9_]{36,}", "github-token"),
    (r"github_pat_[A-Za-z0-9_]{22,}", "github-pat"),
    # JWT — must match all three dot-separated segments; two-segment shape
    # would leave the signature plaintext.
    (r"eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+", "jwt"),
    (r"pa-[A-Za-z0-9_-]{30,}", "voyage-key"),
    (r"\d{8,10}:[A-Za-z0-9_-]{35}", "telegram-token"),
    (r"fc-[A-Za-z0-9]{30,}", "firecrawl-key"),
    # Negative lookahead so this can't swallow ``sk-ant-*`` Anthropic keys
    # if the list is ever reordered alphabetically.
    (r"sk-(?!ant-)[A-Za-z0-9]{20,}", "openai-key"),
    (r"xox[bpras]-[A-Za-z0-9-]{10,}", "slack-token"),
    (r"-----BEGIN (?:RSA |EC |DSA )?PRIVATE KEY-----", "private-key"),
    # Generic credential assignment — same shape as secret-scanner.py.
    (
        r"""(?i)(?:password|secret|token|api[_-]?key|apikey)\s*[:=]\s*['"]?[A-Za-z0-9_/+.-]{16,}""",
        "credential-assignment",
    ),
]

# ---------------------------------------------------------------------------
# PII patterns (per #581 acceptance criteria).
# ---------------------------------------------------------------------------
# Email — simple but tight. Avoids matching markdown/refs.
_EMAIL_RE = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")

# Username inside a filesystem path. Substitutes only the username token,
# preserving the rest of the path so the anchor remains readable.
#   Windows: C:\Users\jdoe\... or c:/Users/jdoe/...
#   macOS  : /Users/jdoe/...
#   Linux  : /home/jdoe/...
_USER_PATH_RE = re.compile(
    r"(?P<prefix>(?:[A-Za-z]:[\\/]Users[\\/])|(?:/Users/)|(?:/home/))(?P<user>[A-Za-z0-9_.][A-Za-z0-9_.-]*)",
)

# Bare ENV_VAR=value — catches dotenv leaks that the credential-named
# regex above misses (var name is generic). Permissive char class on the
# value so connection strings like ``DATABASE_URL=postgres://u:p@h/db``
# get scrubbed.
#
# Threshold: 20 chars on the value. Lower threshold (12) was a false-
# positive trap — it ate ``VERSION=1.2.3.4.5.6.7.8`` and ``BUILD=20260510``
# in CI transcripts, degrading anchor quality with no security gain.
_DOTENV_RE = re.compile(
    r"(?m)^(?P<key>[A-Z][A-Z0-9_]{2,})=(?P<val>[^\s#]{20,})\s*$",
)

_COMPILED_SECRETS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(p), label) for p, label in _SECRET_PATTERNS
]


def scrub(text: str) -> tuple[str, bool]:
    """Return (scrubbed_text, redacted).

    redacted is True iff any pattern matched. Substitutions:
      * secrets             → ``[REDACTED:secret:<label>]``
      * email               → ``[REDACTED:email]``
      * user path component → ``<prefix>[REDACTED:user]``
      * dotenv value        → ``<KEY>=[REDACTED:env]``

    Order matters: secrets first (they're the highest-confidence patterns),
    then PII. Each pattern that matches sets ``redacted=True``.
    """
    if not text:
        return text, False

    redacted = False

    for pat, label in _COMPILED_SECRETS:
        new_text, n = pat.subn(f"[REDACTED:secret:{label}]", text)
        if n:
            redacted = True
            text = new_text

    new_text, n = _EMAIL_RE.subn("[REDACTED:email]", text)
    if n:
        redacted = True
        text = new_text

    def _user_sub(m: re.Match[str]) -> str:
        return f"{m.group('prefix')}[REDACTED:user]"

    new_text, n = _USER_PATH_RE.subn(_user_sub, text)
    if n:
        redacted = True
        text = new_text

    def _env_sub(m: re.Match[str]) -> str:
        return f"{m.group('key')}=[REDACTED:env]"

    new_text, n = _DOTENV_RE.subn(_env_sub, text)
    if n:
        redacted = True
        text = new_text

    return text, redacted
