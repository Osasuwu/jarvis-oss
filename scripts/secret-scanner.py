"""PreToolUse hook: scan tool inputs for secret patterns before execution.

Handles three tool types:
- GitHub MCP write tools: scans body/title/content fields
- Bash tool: scans command string for secrets and dangerous exfiltration patterns
- Memory MCP tools: scans content/description fields for secrets

Reads tool_input from stdin (JSON). Exits 2 to block if secrets detected.
Does NOT scan for personal data — only credentials that grant access.
"""

import json
import re
import sys

# ---------------------------------------------------------------------------
# Patterns that indicate real secrets (API keys, tokens, passwords).
# Tuned for low false-positive rate — only high-confidence patterns.
# ---------------------------------------------------------------------------
SECRET_PATTERNS = [
    # AWS
    (r"AKIA[0-9A-Z]{16}", "AWS Access Key"),
    # Anthropic
    (r"sk-ant-[a-zA-Z0-9_-]{20,}", "Anthropic API Key"),
    # GitHub tokens
    (r"gh[ps]_[A-Za-z0-9_]{36,}", "GitHub Token"),
    (r"github_pat_[A-Za-z0-9_]{22,}", "GitHub PAT"),
    # Supabase / JWT (eyJ... base64 tokens)
    (r"eyJ[A-Za-z0-9_-]{30,}\.[A-Za-z0-9_-]{10,}", "JWT / Supabase Key"),
    # Voyage AI
    (r"pa-[A-Za-z0-9_-]{30,}", "Voyage AI Key"),
    # Telegram bot token
    (r"\d{8,10}:[A-Za-z0-9_-]{35}", "Telegram Bot Token"),
    # Firecrawl
    (r"fc-[A-Za-z0-9]{30,}", "Firecrawl API Key"),
    # Generic "secret" / "password" / "token" with value assignment
    (r"""(?i)(?:password|secret|token|api_key|apikey)\s*[:=]\s*['"]?[A-Za-z0-9_/+.-]{16,}""", "Credential assignment"),
    # OpenAI-style
    (r"sk-[A-Za-z0-9]{20,}", "OpenAI-style API Key"),
    # Slack
    (r"xox[bpras]-[A-Za-z0-9-]{10,}", "Slack Token"),
    # Private keys
    (r"-----BEGIN (?:RSA |EC |DSA )?PRIVATE KEY-----", "Private Key"),
]

COMPILED_SECRETS = [(re.compile(p), label) for p, label in SECRET_PATTERNS]

# ---------------------------------------------------------------------------
# Bash-specific: dangerous command patterns that exfiltrate secrets.
# These match the COMMAND STRING, not secret values themselves.
# ---------------------------------------------------------------------------

# Known secret env var names (values should never appear in commands)
_SECRET_VARS = (
    r"SUPABASE_KEY|ANTHROPIC_API_KEY|VOYAGE_API_KEY|GITHUB_TOKEN"
    r"|FIRECRAWL_API_KEY|TELEGRAM_BOT_TOKEN"
)

BASH_DANGER_PATTERNS = [
    # Reading .env and piping/redirecting somewhere
    (r"(?:cat|type|Get-Content|gc)\s+[^\|;]*\.env", "Reading .env file"),
    # Expanding secret env vars in curl/wget/http commands
    (rf"(?:curl|wget|http|Invoke-WebRequest|iwr)\s.*\$(?:{_SECRET_VARS})", "Secret var in HTTP command"),
    (rf"(?:curl|wget|http|Invoke-WebRequest|iwr)\s.*\$\{{(?:{_SECRET_VARS})\}}", "Secret var in HTTP command"),
    # Piping env/printenv to network tools
    (r"(?:env|printenv|set)\s*\|.*(?:curl|wget|nc|ncat|socat|http)", "Env dump to network"),
    # Sending .env contents via curl -d / --data
    (r"curl\s.*(?:-d|--data)\s*@?\.env", "Sending .env via curl"),
    # netcat/socat with .env
    (r"(?:nc|ncat|socat)\s.*\.env", "Sending .env via netcat"),
    # base64 encoding .env (obfuscation attempt)
    (r"base64\s.*\.env", "Encoding .env"),
    (r"\.env.*\|\s*base64", "Encoding .env"),
]

COMPILED_BASH = [(re.compile(p, re.IGNORECASE), label) for p, label in BASH_DANGER_PATTERNS]


# ---------------------------------------------------------------------------
# Text extraction per tool type
# ---------------------------------------------------------------------------

def extract_github_text(tool_input: dict) -> str:
    """Pull all text fields from GitHub MCP tool inputs."""
    parts = []
    for key in ("body", "title", "content", "message", "description", "comment"):
        val = tool_input.get(key)
        if isinstance(val, str):
            parts.append(val)
    # push_files: list of {path, content}
    files = tool_input.get("files")
    if isinstance(files, list):
        for f in files:
            if isinstance(f, dict) and isinstance(f.get("content"), str):
                parts.append(f["content"])
    return "\n".join(parts)


def extract_bash_command(tool_input: dict) -> str:
    """Pull command string from Bash tool input."""
    cmd = tool_input.get("command", "")
    return cmd if isinstance(cmd, str) else ""


def extract_memory_text(tool_input: dict) -> str:
    """Pull text fields from memory_store input."""
    parts = []
    for key in ("content", "description", "name"):
        val = tool_input.get(key)
        if isinstance(val, str):
            parts.append(val)
    return "\n".join(parts)


# Regex to match heredoc bodies: <<'EOF'...EOF or <<EOF...EOF (multiline)
_HEREDOC_RE = re.compile(
    r"<<-?\s*'?(\w+)'?\s*\n.*?\n\s*\1\s*(?:\)|$)",
    re.DOTALL,
)


def strip_heredocs(command: str) -> str:
    """Remove heredoc bodies from a command string.

    Heredoc content is documentation/text, not executable commands.
    scan_bash_dangers should only check the executable parts.
    scan_secrets still checks the FULL command (literal keys are dangerous anywhere).
    """
    return _HEREDOC_RE.sub("", command)


# ---------------------------------------------------------------------------
# Scanning
# ---------------------------------------------------------------------------

def scan_secrets(text: str) -> list[str]:
    """Check text for literal secret values."""
    findings = []
    for pattern, label in COMPILED_SECRETS:
        if pattern.search(text):
            findings.append(label)
    return findings


def scan_bash_dangers(command: str) -> list[str]:
    """Check bash command for dangerous exfiltration patterns.

    Automatically strips heredoc bodies — those are text content,
    not executable commands.
    """
    executable_part = strip_heredocs(command)
    findings = []
    for pattern, label in COMPILED_BASH:
        if pattern.search(executable_part):
            findings.append(label)
    return findings


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def block(findings: list[str]):
    """Output deny JSON and exit 2."""
    types = ", ".join(sorted(set(findings)))
    result = {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": f"BLOCKED: secret pattern detected ({types}). Remove credentials before retrying.",
        }
    }
    json.dump(result, sys.stdout)
    sys.exit(2)


def main():
    raw = sys.stdin.read()
    if not raw.strip():
        sys.exit(0)

    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        sys.exit(0)  # can't parse — don't block

    tool_name = data.get("tool_name", "")
    tool_input = data.get("tool_input", {})
    if not isinstance(tool_input, dict):
        sys.exit(0)

    findings = []

    if tool_name == "Bash":
        command = extract_bash_command(tool_input)
        if not command:
            sys.exit(0)
        # Check for literal secret values in the command
        findings.extend(scan_secrets(command))
        # Check for dangerous exfiltration patterns
        findings.extend(scan_bash_dangers(command))
    elif "memory" in tool_name:
        # Memory MCP tools (memory_store)
        text = extract_memory_text(tool_input)
        if not text:
            sys.exit(0)
        findings.extend(scan_secrets(text))
    else:
        # GitHub MCP tools
        text = extract_github_text(tool_input)
        if not text:
            sys.exit(0)
        findings.extend(scan_secrets(text))

    if findings:
        block(findings)

    sys.exit(0)


if __name__ == "__main__":
    main()
