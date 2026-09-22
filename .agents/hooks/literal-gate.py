"""PreToolUse hook: block text bound for GitHub that holds a personal literal (#100).

Covers the text the pre-push gate cannot see, because it never passes through git:
pull request and issue titles and bodies, comments, reviews, release notes and `gh api` writes.

- GitHub MCP tools (matcher `^mcp__github__`, the whole server): every string in the tool
  input, at any depth. A field list would miss the next tool's field.
- Bash (matcher `Bash`): only `gh` commands that send text (`gh pr|issue create/edit/comment/
  review/merge/close/reopen`, `gh release|gist create/edit`, and `gh api` with a field, an
  input file or a write method). The command text is scanned, minus leading `cd <dir> &&`
  steps and the paths of body files, which are not sent; the body files' contents are read
  and scanned instead. A body file that cannot be read, or a body piped in on stdin, blocks.

Literals come from `PERSONAL_LITERALS`, the source the CI scrub and the pre-push gate read,
and match in any variant (case, separators, path forms). Exit 2 blocks the call and names
where the hit is, never the value. With no usable list, a call that would send text is
blocked and the message says nothing was checked; other calls pass. Input that is not valid
JSON blocks.

Wire it up with `literal-gate.snippet.json` (same directory). Each command is chained with
`|| exit 2`, so an interpreter that fails to launch blocks instead of passing.
"""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

BLOCK = 2
PREFIX = "literal-gate"

try:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
    from scrub_personal_literals import (  # noqa: E402
        LITERALS_ENV_VAR,
        compile_literal_matcher,
        literals_from_env,
    )
except Exception as exc:  # noqa: BLE001 - any import failure must block, not pass
    print(f"{PREFIX}: cannot load the literal matcher ({exc}); blocking.", file=sys.stderr)
    sys.exit(BLOCK)

_ARG = r"(\"[^\"]*\"|'[^']*'|[^\s;&|]+)"
_LEADING_CD = re.compile(rf"^\s*cd\s+{_ARG}\s*(?:&&|;)\s*")
_GH = r"\bgh\s+(?:(?:-R|--repo)(?:\s+|=)\S+\s+)?"
_GH_WRITE = re.compile(
    _GH + r"(?:(?:pr|issue)\s+(?:create|edit|comment|review|merge|close|reopen)"
    r"|(?:release|gist)\s+(?:create|edit))\b"
)
_GH_API = re.compile(_GH + r"api\b")
_API_WRITE = re.compile(
    r"(?:^|\s)(?:-f|-F|--field|--raw-field|--input)(?:\s|=)"
    r"|(?:^|\s)(?:-X|--method)(?:\s+|=)?['\"]?(?:POST|PATCH|PUT)\b",
    re.IGNORECASE,
)
_FILE_FLAG = re.compile(
    rf"(?:(?<=\s)|^)(--body-file|--notes-file|--input|-F|--field)(?:\s+|=){_ARG}"
)


def deny(reason: str) -> int:
    print(f"{PREFIX}: blocked. {reason}", file=sys.stderr)
    return BLOCK


def iter_strings(value, path: str = ""):
    """Yield (field path, string) for every string in value, at any nesting depth."""
    if isinstance(value, str):
        yield path or "input", value
    elif isinstance(value, dict):
        for key, v in value.items():
            yield from iter_strings(v, f"{path}.{key}" if path else str(key))
    elif isinstance(value, list):
        for i, v in enumerate(value):
            yield from iter_strings(v, f"{path}[{i}]")


def unquote(arg: str) -> str:
    if len(arg) >= 2 and arg[0] == arg[-1] and arg[0] in "\"'":
        return arg[1:-1]
    return arg


def resolve(path: str, base: Path) -> Path:
    if os.name == "nt":
        drive = re.match(r"^/([A-Za-z])(/|$)", path)
        if drive:
            path = f"{drive.group(1)}:/{path[3:]}"
    candidate = Path(os.path.expanduser(path))
    return candidate if candidate.is_absolute() else base / candidate


def is_gh_write(command: str) -> bool:
    if _GH_WRITE.search(command):
        return True
    return bool(_GH_API.search(command) and _API_WRITE.search(command))


def body_file_path(flag: str, value: str) -> str | None:
    """The file a flag reads its text from, or None when the value is the text itself."""
    if flag in ("--body-file", "--notes-file", "--input"):
        return value
    if "=@" in value:  # gh api -F key=@file
        return value.split("=@", 1)[1]
    if "=" in value:  # gh api -F key=value: the value is in the command text
        return None
    return value if flag == "-F" else None  # gh pr|issue|release -F <file>


def split_leading_cds(command: str, base: Path) -> tuple[str, Path]:
    while True:
        match = _LEADING_CD.match(command)
        if not match:
            return command, base
        base = resolve(unquote(match.group(1)), base)
        command = command[match.end() :]


def check_bash(tool_input: dict, cwd: Path) -> int:
    command = tool_input.get("command")
    if not isinstance(command, str) or not is_gh_write(command):
        return 0
    matcher = compile_literal_matcher(literals_from_env())
    if matcher is None:
        return no_list()

    command, base = split_leading_cds(command, cwd)
    findings: list[str] = []
    sent_text = command
    for match in reversed(list(_FILE_FLAG.finditer(command))):
        flag, value = match.group(1), unquote(match.group(2))
        path = body_file_path(flag, value)
        if path is None:
            continue
        sent_text = sent_text[: match.start(2)] + sent_text[match.end(2) :]
        if path == "-":
            if "<<" not in command:
                return deny(
                    f"{flag} - reads the body from a pipe, which this hook cannot see. Write "
                    "the body to a file and pass its path, or use a heredoc."
                )
            continue  # a heredoc body is part of the command text, scanned below
        shown = "<path withheld: it matches>" if matcher.search(path) else path
        try:
            contents = resolve(path, base).read_text(encoding="utf-8", errors="replace")
        except OSError:
            return deny(f"could not read body file {shown}, so it was not checked.")
        if matcher.search(contents):
            findings.append(f"body file {shown}")
    if matcher.search(sent_text):
        findings.insert(0, "the command text")
    if findings:
        return hit(findings)
    return 0


def check_github_mcp(tool_input: dict) -> int:
    matcher = compile_literal_matcher(literals_from_env())
    if matcher is None:
        return no_list()
    findings = []
    for field, text in iter_strings(tool_input):
        if matcher.search(text):
            name = "<field name withheld: it matches>" if matcher.search(field) else field
            findings.append(f"field {name}")
    return hit(findings) if findings else 0


def no_list() -> int:
    return deny(
        f"No personal literals configured: set {LITERALS_ENV_VAR} (one literal per line) "
        "from the list kept outside the repo. Nothing was checked, so text bound for GitHub "
        "is blocked."
    )


def hit(findings: list[str]) -> int:
    return deny(
        "A personal literal (or a variant of one) is in the text bound for GitHub, in: "
        + "; ".join(findings)
        + ". Values withheld. Remove it and retry."
    )


def main() -> int:
    try:
        data = json.loads(sys.stdin.read())
    except (json.JSONDecodeError, UnicodeDecodeError):
        return deny("hook input is not valid JSON, so nothing was checked.")
    if not isinstance(data, dict):
        return deny("hook input is not a JSON object, so nothing was checked.")
    tool_name = data.get("tool_name")
    tool_input = data.get("tool_input")
    if not isinstance(tool_name, str):
        return deny("hook input names no tool, so nothing was checked.")
    is_github = tool_name.startswith("mcp__github__")
    if not is_github and tool_name != "Bash":
        return 0
    if not isinstance(tool_input, dict):
        return deny("hook input has no tool_input object, so nothing was checked.")
    if is_github:
        return check_github_mcp(tool_input)
    cwd = data.get("cwd")
    base = Path(cwd) if isinstance(cwd, str) and cwd else Path.cwd()
    return check_bash(tool_input, base)


if __name__ == "__main__":
    sys.exit(main())
