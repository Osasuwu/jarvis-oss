"""PreToolUse hook: scan tool inputs for secret patterns before execution.

Handles three tool types:
- GitHub MCP tools: scans every string value in the input, at any depth
- Shell tools (Bash and PowerShell): scans the command string for secrets and dangerous
  exfiltration patterns, the same scan for either tool
- File writes (Edit/Write/NotebookEdit): scans the text being written to disk

Reads tool_input from stdin (JSON). Exits 2 to block if secrets detected.
Does NOT scan for personal data — only credentials that grant access.

Install: the matchers in `settings.snippet.json` (same directory) run it: `Bash|PowerShell`
(both shell tools; PowerShell is the primary one on Windows), `Edit|Write|NotebookEdit` and
`^mcp__github__` (the whole server, since a list of write tools fails open when the server
renames one; change the prefix if your server has another name). Merge those entries into `.claude/settings.local.json` under
`hooks.PreToolUse`. Each command runs `python3` if it starts, else `python`, and is chained with
`|| exit 2`, so an interpreter that fails to launch blocks instead of passing. The
untracked `.claude/settings.local.json` is not in a fresh checkout; `.worktreeinclude`
lists it so Claude Code copies it into the worktrees it makes, while one made by
`git worktree add` needs a hand copy. Claude Code only as shipped; other harnesses
need their own wiring and parsing.

Check: the hook is silent when it does not fire, so a session never shows whether it
was wired. Ask the agent to write `sk-ant-` plus 20 or more letters or digits to a
scratch file; a wired hook denies with a `BLOCKED:` reason in the transcript, and one
that fails to launch blocks with the shell's error text instead. `claude --debug` logs
every hook invocation and exit code, silent exit-0 runs included, to
`~/.claude/debug/<session-id>.txt`. The hook keeps no log of its own.
`tests/test_agent_safety_hooks.py` runs it on constructed calls.

Limit: it sees only what its matchers name. A file write made with `echo >` or
`Set-Content` in the shell is scanned as a shell command, not as a file write.

CUSTOMIZE: SECRET_PATTERNS and _SECRET_VARS below name a specific set of
providers. Add or remove entries for whatever your own project's stack uses.
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
    # Generic "secret" / "password" / "token" with value assignment. A quoted value is a
    # literal. An unquoted one is not when it is a PowerShell cmdlet name (PascalCase
    # Verb-Noun, ending there): a `$password` variable assigned from `ConvertTo-SecureString`
    # holds the cmdlet's output, not a secret (#281). An unquoted literal of that exact
    # shape passes.
    (
        r"""(?i)(?:password|secret|token|api_key|apikey)\s*[:=]\s*"""
        r"""(?:['"]|(?!(?-i:[A-Z][a-z]+(?:[A-Z][a-z]*)*-[A-Z][A-Za-z]*)(?![\w/+.-])))"""
        r"""[A-Za-z0-9_/+.-]{16,}""",
        "Credential assignment",
    ),
    # OpenAI-style
    (r"sk-[A-Za-z0-9]{20,}", "OpenAI-style API Key"),
    # Slack
    (r"xox[bpras]-[A-Za-z0-9-]{10,}", "Slack Token"),
    # Private keys
    (r"-----BEGIN (?:RSA |EC |DSA )?PRIVATE KEY-----", "Private Key"),
]

COMPILED_SECRETS = [(re.compile(p), label) for p, label in SECRET_PATTERNS]

# ---------------------------------------------------------------------------
# Shell-specific: dangerous command patterns that exfiltrate secrets, run on Bash and
# PowerShell commands alike, so each pattern takes both shells' spelling of its shape.
# These match the COMMAND STRING, not secret values themselves.
# ---------------------------------------------------------------------------

# Known secret env var names (values should never appear in commands).
# CUSTOMIZE: list whichever env vars your own project stores credentials in.
_SECRET_VARS = r"API_KEY|API_TOKEN|ACCESS_TOKEN|SECRET_KEY|DATABASE_URL"

# PowerShell's two web cmdlets and their aliases.
_PS_WEB = r"Invoke-WebRequest|iwr|Invoke-RestMethod|irm"

# The rest of a pipe that ends at a network tool: the short tool names are whole words (not
# `nc` inside `-Encoding`).
_NET_TAIL = r".*(?:\b(?:curl|wget|nc|ncat|socat|iwr|irm)\b|http|Invoke-WebRequest|Invoke-RestMethod)"

BASH_DANGER_PATTERNS = [
    # Reading .env and piping/redirecting somewhere. The reader starts a word (not `type`
    # inside `-ItemType`), and `.env` is not the start of `.Environment`
    # (`[System.Environment]`): both are everyday PowerShell.
    (r"(?<![\w-])(?:cat|type|Get-Content|gc)\s+[^\|;]*\.env(?!iron)", "Reading .env file"),
    # The same read through a .NET file API (`[IO.File]::ReadAllText('.env')`), PowerShell's
    # other `cat`, from either shell tool (a Bash call can run it through `pwsh -c`). Only the
    # methods that read or open the file: `Exists` reads nothing out of it (#281).
    (
        r"\[(?:System\.)?IO\.File\]::(?:Read|Open)\w*\s*\(\s*['\"]?[^'\")]*\.env(?!iron)",
        "Reading .env file",
    ),
    # Expanding secret env vars in curl/wget/http commands. `(?:env:)?` takes PowerShell's
    # spelling of the same variable, `$env:NAME` / `${env:NAME}`.
    (
        rf"(?:curl|wget|http|{_PS_WEB})\s.*\$(?:env:)?(?:{_SECRET_VARS})",
        "Secret var in HTTP command",
    ),
    (
        rf"(?:curl|wget|http|{_PS_WEB})\s.*\$\{{(?:env:)?(?:{_SECRET_VARS})\}}",
        "Secret var in HTTP command",
    ),
    # Piping env/printenv to network tools. `env:` / `env:\` is PowerShell's env drive
    # (`Get-ChildItem env:`). The head starts a word (not `set` ending `$dataset |`), and
    # the short tool names are whole words (not `nc` inside `-Encoding`).
    (
        r"(?<![\w-])(?:env(?::\\?)?|printenv|set)\s*\|" + _NET_TAIL,
        "Env dump to network",
    ),
    # One environment variable piped the same way: `$env:API_KEY | iwr ...` (#281).
    (r"\$\{?env:\w+\}?\s*\|" + _NET_TAIL, "Env var piped to network"),
    # Sending .env contents via curl -d / --data
    (r"curl\s.*(?:-d|--data)\s*@?\.env", "Sending .env via curl"),
    # The web cmdlets' counterpart: `-InFile .env` posts the file as the request body.
    (rf"(?:{_PS_WEB})\s.*-InFile\s+\S*\.env", "Sending .env via web cmdlet"),
    # netcat/socat with .env
    (r"(?:nc|ncat|socat)\s.*\.env", "Sending .env via netcat"),
    # base64 encoding .env (obfuscation attempt)
    (r"base64\s.*\.env", "Encoding .env"),
    (r"\.env.*\|\s*base64", "Encoding .env"),
    # PowerShell's base64: `[Convert]::ToBase64String(...)` over the file's bytes.
    (r"ToBase64String.*\.env", "Encoding .env"),
]

COMPILED_BASH = [(re.compile(p, re.IGNORECASE), label) for p, label in BASH_DANGER_PATTERNS]


# ---------------------------------------------------------------------------
# Text extraction per tool type
# ---------------------------------------------------------------------------


def iter_strings(value):
    """Yield every string found in value, at any nesting depth.

    A field-name whitelist enumerates instances, not the class: #74 found
    `custom_instructions`/`rationale` (assign_copilot_to_issue*) and
    `commit_message`/`commit_title` (merge_pull_request, permanent
    default-branch history) unscanned because they weren't on the list.
    """
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for v in value.values():
            yield from iter_strings(v)
    elif isinstance(value, list):
        for v in value:
            yield from iter_strings(v)


def extract_github_text(tool_input: dict) -> str:
    """Pull every string value out of a GitHub MCP tool_input, any nesting depth."""
    return "\n".join(iter_strings(tool_input))


# Claude Code's shell tools. Both carry the command in `command`, and both get the same
# scan: PowerShell is the primary shell on Windows, and a call through it was never
# scanned while the branch named `Bash` alone (#92).
SHELL_TOOLS = ("Bash", "PowerShell")


def extract_bash_command(tool_input: dict) -> str:
    """Pull command string from a shell tool's input (Bash or PowerShell)."""
    cmd = tool_input.get("command", "")
    return cmd if isinstance(cmd, str) else ""


# Fields that carry text destined for disk, per file-write tool:
#   Write        -> content
#   Edit         -> new_string
#   NotebookEdit -> new_source
# Only the *incoming* text is scanned. old_string is deliberately excluded:
# it is text already on disk, and blocking there would make an existing
# secret unremovable by the one tool that could remove it.
FILE_WRITE_TOOLS = ("Edit", "Write", "NotebookEdit")
_FILE_WRITE_KEYS = ("content", "new_string", "new_source")

# A scanner's test file may have to hold secret-SHAPED fixtures; scanning it
# blocks the one file that must contain them. Exempt by basename only —
# deliberately not a `tests/` prefix, because a test directory is exactly where
# a real leaked key would otherwise hide.
# Empty as shipped: this repo's tests/test_agent_safety_hooks.py builds its
# fixtures by concatenation and needs no exemption.
# CUSTOMIZE: name your own project's fixture files here.
SELF_TEST_BASENAMES = ()


def is_self_test_fixture(tool_input: dict) -> bool:
    """True when the write targets a file named in SELF_TEST_BASENAMES."""
    path = tool_input.get("file_path") or tool_input.get("notebook_path") or ""
    if not isinstance(path, str):
        return False
    basename = path.replace("\\", "/").rsplit("/", 1)[-1]
    return basename in SELF_TEST_BASENAMES


def extract_file_write_text(tool_input: dict) -> str:
    """Pull the text a file-write tool is about to put on disk."""
    parts = []
    for key in _FILE_WRITE_KEYS:
        val = tool_input.get(key)
        if isinstance(val, str):
            parts.append(val)
    # MultiEdit-style batches: list of {old_string, new_string}
    edits = tool_input.get("edits")
    if isinstance(edits, list):
        for e in edits:
            if isinstance(e, dict) and isinstance(e.get("new_string"), str):
                parts.append(e["new_string"])
    return "\n".join(parts)


# Regex to match heredoc bodies: <<'EOF'...EOF or <<EOF...EOF (multiline).
# The closing delimiter line ends the match at a `)` (subshell close), a
# following newline (more commands after the heredoc — the common case), or
# absolute end-of-string. Without the `\n` lookahead, a heredoc followed by
# anything but `)`/end-of-string failed to strip at all, leaving its full
# body exposed to BASH_DANGER_PATTERNS.
#
# HEREDOC_RE and strip_heredocs are shared: `gh_write.py` (same directory) loads them from
# this file to decide which heredoc bodies hold gh calls (#285), so a change here changes
# what the literal gate and the authority guard see too.
HEREDOC_RE = re.compile(
    r"<<-?\s*'?(\w+)'?\s*\n.*?\n\s*\1\s*(?:\)|(?=\n)|$)",
    re.DOTALL,
)


# Programs that run what arrives on their standard input. A heredoc fed to one is a script,
# and its body runs (#93). `python[\d.]*` takes `python3` and `python3.12`, `py` is the
# Windows launcher, and `ssh` runs its stdin on the remote host.
# CUSTOMIZE: add any other interpreter your own stack feeds heredocs to.
_INTERPRETER_RE = re.compile(
    r"(?:ba|z|da|k|c|tc|fi)?sh|python[\d.]*|py|node|deno|bun|perl|ruby|php|pwsh|powershell|ssh",
    re.IGNORECASE,
)

# A word on a heredoc's opening line. Shell punctuation, quotes, `$` and `=` split words, so
# `"$(cat`, `'bash -s'` and `--shell=bash` each yield their bare words.
_LINE_WORD_RE = re.compile(r"""[^\s;|&()<>`'"$=]+""")


def _feeds_interpreter(command: str, start: int) -> bool:
    """True when a word before `<<` on the heredoc's opening line names an interpreter.

    Any word counts, not only the command word: `bash -c "$(cat <<EOF` runs the body that
    `cat` prints, and `sudo -u x bash <<EOF` hides the command word behind a wrapper. A text
    heredoc whose opening line merely mentions an interpreter is scanned too; that is the
    safe direction. A word is compared by its basename without `.exe`, so `/usr/bin/python3`
    counts and `deploy.sh` does not.
    """
    line_start = command.rfind("\n", 0, start) + 1
    for word in _LINE_WORD_RE.findall(command[line_start:start]):
        name = re.split(r"[/\\]", word)[-1].lower()
        if name.endswith(".exe"):
            name = name[: -len(".exe")]
        if _INTERPRETER_RE.fullmatch(name):
            return True
    return False


def strip_heredocs(command: str) -> str:
    """Remove the bodies of heredocs that write text, and keep those fed to an interpreter.

    A heredoc that writes text (`cat > file <<EOF`, `git commit -F - <<EOF`, a commit message
    in `"$(cat <<EOF`) is stripped, so a body that names a dangerous command without running
    it is not denied. A heredoc fed to an interpreter (`bash <<EOF`, `python3 - <<EOF`,
    `ssh host <<EOF`) is that interpreter's standard input: its body runs, so it stays in
    the text scan_bash_dangers checks (#93). scan_secrets checks the FULL command either way
    (a literal key is dangerous anywhere).
    """
    return HEREDOC_RE.sub(
        lambda m: m.group(0) if _feeds_interpreter(command, m.start()) else "",
        command,
    )


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
    """Check a shell command for dangerous exfiltration patterns.

    Strips the bodies of heredocs that write text first (strip_heredocs). The body of a
    heredoc fed to an interpreter runs, so it is scanned with the rest of the command.
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

    if tool_name in SHELL_TOOLS:
        command = extract_bash_command(tool_input)
        if not command:
            sys.exit(0)
        # Check for literal secret values in the command
        findings.extend(scan_secrets(command))
        # Check for dangerous exfiltration patterns
        findings.extend(scan_bash_dangers(command))
    elif tool_name in FILE_WRITE_TOOLS:
        # File writes — the path a secret actually takes to reach git
        if is_self_test_fixture(tool_input):
            sys.exit(0)
        text = extract_file_write_text(tool_input)
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
