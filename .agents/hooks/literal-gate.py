"""PreToolUse hook: block text bound for GitHub that holds a personal literal (#100).

Covers the text the pre-push gate cannot see, because it never passes through git:
pull request and issue titles and bodies, comments, reviews, release notes and `gh api` writes.

- GitHub MCP tools (matcher `^mcp__.*github`, the whole server under any prefix, as the
  authority guard wires it): every string in the tool input, at any depth. A field list would
  miss the next tool's field.
- Bash and PowerShell (matcher `Bash|PowerShell`): only `gh` commands that send text (`gh pr|issue create/edit/comment/
  review/merge/close/reopen`, `gh release|gist create/edit`, and `gh api` with a field, an
  input file or a write method). A chained command (`&&`, `||`, `;`, `|`, `&`, newlines) is
  split, quote-aware, into steps, and only the `gh` steps are scanned: a home path in a `cd`,
  `ls` or `rm` step is not sent (#233). The scan widens to every step up to and including the
  `gh` one when that step could receive text from them: it reads a shell variable or a command
  substitution, it is fed by a pipe, or it reads its body from stdin. A command that cannot be
  split (an unclosed quote, a heredoc with no end) is scanned whole. The paths of body files are
  not sent; the files' contents are read and scanned instead, wherever they are (a `/tmp/...` Git
  Bash path is read from the Windows temp directory). A body file that cannot be read blocks,
  and so do: a path held in a shell variable (the hook runs before the shell expands it), a
  body file an earlier step of the same command names (that step may write it, and the hook
  reads the file as it is before the call), and a body piped in on stdin, unless it comes from
  a heredoc or a PowerShell here-string in the same command.

Scope by target repository (#233): a write whose target is exactly an allowlisted
`owner/repo` is not checked, so a session can write into the operator's own private repo
whose name or paths are themselves literals. The target is read per `gh` step: its `-R`/`--repo`
value, or for `gh api` a `repos/<owner>/<repo>/...` endpoint; for a GitHub MCP tool, its
`owner` + `repo` arguments. Everything else is checked: no repository named (the target is
the current one), a target that cannot be read unambiguously (two different repos, a step or
command that cannot be split, an inline `GH_REPO=` or `GH_HOST=`, a `--hostname`), and any
repo not on the list. There is no network lookup of visibility: the
list is the only source, so a public repo put on it would leak.

Literals come from `PERSONAL_LITERALS`, the source the CI scrub and the pre-push gate read,
and match in any variant (case, separators, path forms). Exit 2 blocks the call and names
where the hit is (the command, a body file or an MCP field, with line:col), never the value,
and names the allowed route for a body: a file outside the working tree passed with
`--body-file`. With no usable list, every GitHub MCP call and each `gh` command that sends
text is blocked and the message says nothing was checked; other shell calls pass. Input that
is not valid JSON blocks.

It is wired by `settings.snippet.json` (same directory), next to the other agent-safety hooks. Each command is chained with
`|| exit 2`, so an interpreter that fails to launch blocks instead of passing.

Not covered: `git push --no-verify`; a device without the hook or the list; text sent by
`curl`, the browser, another harness or a session with `disableAllHooks`; author names
and emails; typos, split literals, encoded forms; the output of a command substitution or
of a file read by a step other than a body-file flag.

Install (once per device that writes pull requests on a public repository):
1. List: keep it outside every repository, one literal per line, and export it as
   `PERSONAL_LITERALS` (the same list as the CI secret, so update both). PowerShell:
   `[Environment]::SetEnvironmentVariable("PERSONAL_LITERALS", (Get-Content -Raw "$HOME/.config/personal-literals.txt"), "User")`.
   Linux/macOS: `export PERSONAL_LITERALS="$(cat "$HOME/.config/personal-literals.txt")"`.
2. Merge the entries of `settings.snippet.json` into `.claude/settings.local.json` under
   `hooks.PreToolUse`. The untracked `.claude/settings.local.json` is not in a fresh
   checkout; `.worktreeinclude` lists it so Claude Code copies it into the worktrees it
   makes, while one made by `git worktree add` needs a hand copy. Claude Code only as
   shipped.
3. Optional allowlist: `~/.config/literal-gate-allowed-repos.txt` (next to the literal list,
   outside every repository, because a private repo's name is itself a literal). One
   `owner/repo` per line, matched case-insensitively; blank lines and `#` comments are
   ignored, and so is a line that is not `owner/repo`. Example line: `your-user/your-private-repo`.
   List private repositories only. A missing, empty or unreadable file allowlists nothing.
Cost per device: the variable, the merge, and a Python start per Bash or GitHub MCP call.

Check (after installing and after any change to the list or hook; `canary-7f3c9a` is
made up, use your own): add it to the list and export again, then from the repository
root this must print `2`:
`echo '{"tool_name":"Bash","tool_input":{"command":"gh pr comment 1 --body canary-7f3c9a"}}' | python .agents/hooks/literal-gate.py; echo $?`
Remove the canary from the list and export again. `tests/test_literal_gate_hook.py` runs
the hook on constructed calls.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import sys
import tempfile
from pathlib import Path
from typing import NamedTuple

BLOCK = 2
SHELL_TOOLS = ("Bash", "PowerShell")
PREFIX = "literal-gate"
ALLOWLIST_FILE = "~/.config/literal-gate-allowed-repos.txt"
ALLOWED_ROUTE = (
    "To send a body, write it to a file outside the working tree (the session scratchpad or a "
    "temp directory) in an earlier call, then pass its literal path with --body-file: the path "
    "is not sent, only the file's contents are scanned. Never write the body into the repo."
)

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
_CD_STEP = re.compile(rf"\s*(?:cd|Set-Location)\s+{_ARG}\s*", re.IGNORECASE)
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
# A step that can receive text from earlier steps of the same command.
_EXPANSION = re.compile(r"\$[A-Za-z0-9_{(@*]|`")
_UNEXPANDED = re.compile(r"\$|%[A-Za-z_][A-Za-z0-9_]*%|`")
_REPO = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+")
_API_REPO = re.compile(r"^/?repos/([^/?#]+)/([^/?#]+)")
_API_VALUE_FLAGS = frozenset(
    ("-X", "--method", "-H", "--header", "-f", "--raw-field", "-F", "--field", "--input",
     "-q", "--jq", "-t", "--template", "-p", "--preview", "--cache")
)


class Step(NamedTuple):
    start: int
    end: int
    sep: str  # the separator after this step ("" for the last one)


def deny(reason: str) -> int:
    print(f"{PREFIX}: blocked. {reason}", file=sys.stderr)
    return BLOCK


def deny_body(reason: str) -> int:
    return deny(f"{reason} {ALLOWED_ROUTE}")


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


def line_col(text: str, pos: int) -> str:
    line = text.count("\n", 0, pos) + 1
    col = pos - (text.rfind("\n", 0, pos) + 1) + 1
    return f"line {line}:{col}"


def unquote(arg: str) -> str:
    if len(arg) >= 2 and arg[0] == arg[-1] and arg[0] in "\"'":
        return arg[1:-1]
    return arg


def resolve(path: str, base: Path) -> Path:
    if os.name == "nt":
        tmp = re.match(r"^/tmp(/|$)", path)  # Git Bash mounts /tmp on the Windows temp dir
        if tmp:
            temp_dir = os.environ.get("TEMP") or os.environ.get("TMP") or tempfile.gettempdir()
            path = f"{temp_dir}/{path[5:]}"
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


# --- splitting a command into steps -------------------------------------------------------


def _heredoc_word(cmd: str, i: int) -> tuple[str, bool, int] | None:
    """Parse the delimiter after `<<` at i; return (word, strip_tabs, index after it)."""
    i += 2
    strip_tabs = cmd.startswith("-", i)
    if strip_tabs:
        i += 1
    while i < len(cmd) and cmd[i] in " \t":
        i += 1
    word = []
    while i < len(cmd) and cmd[i] not in " \t\r\n;&|<>()":
        ch = cmd[i]
        if ch in "'\"":
            close = cmd.find(ch, i + 1)
            if close == -1:
                return None
            word.append(cmd[i + 1 : close])
            i = close + 1
            continue
        if ch != "\\":
            word.append(ch)
        i += 1
    return ("".join(word), strip_tabs, i) if word else None


def _skip_heredocs(cmd: str, pos: int, pending: list[tuple[str, bool]]) -> int | None:
    """Skip the heredoc bodies that start at pos; return the index after the last one."""
    for word, strip_tabs in pending:
        while True:
            if pos >= len(cmd):
                return None
            nl = cmd.find("\n", pos)
            line = cmd[pos : nl if nl != -1 else len(cmd)].rstrip("\r")
            pos = nl + 1 if nl != -1 else len(cmd)
            if (line.lstrip("\t") if strip_tabs else line) == word:
                break
    pending.clear()
    return pos


def split_steps(cmd: str, powershell: bool) -> list[Step] | None:
    """Split a shell command into the steps its separators join, honouring quotes, `$( )`,
    heredocs and here-strings. None when the command cannot be split with confidence."""
    escape = "`" if powershell else "\\"
    steps: list[Step] = []
    stack: list[str] = []  # '"' double quote, '(' parenthesis, '`' bash command substitution
    pending: list[tuple[str, bool]] = []
    start = i = 0
    n = len(cmd)
    while i < n:
        c = cmd[i]
        ctx = stack[-1] if stack else ""
        if ctx == '"':
            if c == escape:
                i += 2
            elif c == '"':
                stack.pop()
                i += 1
            elif cmd.startswith("$(", i):
                stack.append("(")
                i += 2
            elif c == "`" and not powershell:
                stack.append("`")
                i += 1
            else:
                i += 1
            continue
        if ctx == "`":
            if c == "\\":
                i += 2
            else:
                if c == "`":
                    stack.pop()
                i += 1
            continue
        # unquoted: top level or inside parentheses
        if c == escape:
            i += 2
            continue
        if c == "#" and (i == 0 or cmd[i - 1] in " \t\r\n;&|("):
            nl = cmd.find("\n", i)
            i = nl if nl != -1 else n
            continue
        if powershell and c == "@" and cmd[i + 1 : i + 2] in ("'", '"'):
            quote = cmd[i + 1]
            close = cmd.find(f"\n{quote}@", i + 2)
            if close == -1:
                return None
            i = close + 3
            continue
        if c == "'":
            if not powershell and i > 0 and cmd[i - 1] == "$":  # bash $'...' takes escapes
                j = i + 1
                while j < n and cmd[j] != "'":
                    j += 2 if cmd[j] == "\\" else 1
                if j >= n:
                    return None
                i = j + 1
                continue
            close = cmd.find("'", i + 1)
            if close == -1:
                return None
            i = close + 1
            continue
        if c == '"':
            stack.append('"')
            i += 1
            continue
        if c == "`":  # bash only; PowerShell's backtick is the escape handled above
            stack.append("`")
            i += 1
            continue
        if c == "(":
            stack.append("(")
            i += 1
            continue
        if c == ")":
            if ctx != "(":
                return None
            stack.pop()
            i += 1
            continue
        if not powershell and cmd.startswith("<<", i) and not cmd.startswith("<<<", i):
            if i > 0 and cmd[i - 1] == "<":
                return None
            parsed = _heredoc_word(cmd, i)
            if parsed is None:
                return None
            word, strip_tabs, i = parsed
            pending.append((word, strip_tabs))
            continue
        if c == "\n":
            after = i + 1
            end = i
            if pending:  # the heredoc bodies belong to the step that opened them
                skipped = _skip_heredocs(cmd, after, pending)
                if skipped is None:
                    return None
                after = skipped
                end = after - 1 if cmd[after - 1] == "\n" else after
            if not stack:
                steps.append(Step(start, end, "\n"))
                start = after
            i = after
            continue
        if not stack:
            sep = ""
            if cmd.startswith(("&&", "||"), i):
                sep = cmd[i : i + 2]
            elif c in ";|":
                sep = "|&" if cmd.startswith("|&", i) else c
            elif c == "&" and cmd[i - 1 : i] not in (">", "<") and cmd[i + 1 : i + 2] != ">":
                sep = "&"
            if sep:
                steps.append(Step(start, i, sep))
                i += len(sep)
                start = i
                continue
        i += 1
    if stack or pending:
        return None
    steps.append(Step(start, n, ""))
    return steps


# --- target repository ---------------------------------------------------------------------


def load_allowlist() -> frozenset[str]:
    """Allowlisted `owner/repo` names, lowercased. Missing or unreadable: none."""
    try:
        raw = Path(os.path.expanduser(ALLOWLIST_FILE)).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return frozenset()
    entries = set()
    for line in raw.splitlines():
        entry = line.split("#", 1)[0].strip()
        if _REPO.fullmatch(entry):
            entries.add(entry.lower())
    return frozenset(entries)


def _repo_from_flag(value: str) -> str | None:
    value = re.sub(r"^https?://", "", value, flags=re.IGNORECASE)
    if value.endswith(".git"):
        value = value[:-4]
    parts = value.strip("/").split("/")
    if len(parts) == 3 and parts[0].lower() == "github.com":
        parts = parts[1:]
    return "/".join(parts) if len(parts) == 2 else None


def step_target(text: str) -> str | None:
    """The one `owner/repo` a gh step writes to, lowercased, or None when it names none or
    cannot be read unambiguously (both are checked)."""
    try:
        tokens = shlex.split(text, posix=True)
    except ValueError:
        return None
    if "gh" not in tokens:
        return None
    at = tokens.index("gh")
    if any(t.startswith(("GH_HOST=", "GH_REPO=")) for t in tokens[:at]):
        return None
    found: list[str | None] = []
    command: str | None = None  # the gh subcommand: the first argument that is not a flag
    endpoint_next = False
    args = tokens[at + 1 :]
    i = 0
    while i < len(args):
        token = args[i]
        if token == "--hostname" or token.startswith("--hostname="):
            return None
        if token in ("-R", "--repo"):
            found.append(_repo_from_flag(args[i + 1]) if i + 1 < len(args) else None)
            i += 2
            continue
        if token.startswith("--repo="):
            found.append(_repo_from_flag(token[len("--repo=") :]))
        elif token.startswith("-R") and len(token) > 2:
            found.append(_repo_from_flag(token[2:].lstrip("=")))
        elif command is None and not token.startswith("-"):
            command = token
            endpoint_next = token == "api"
        elif endpoint_next and token in _API_VALUE_FLAGS:
            i += 2  # the flag's value is not the endpoint
            continue
        elif endpoint_next and not token.startswith("-"):
            endpoint_next = False
            match = _API_REPO.match(token)
            if match:
                found.append(f"{match.group(1)}/{match.group(2)}")
        i += 1
    names = {name.lower() if name and _REPO.fullmatch(name) else None for name in found}
    if len(names) != 1 or None in names:
        return None
    return names.pop()


# --- checks ----------------------------------------------------------------------------------


def check_bash(tool_input: dict, cwd: Path, powershell: bool) -> int:
    command = tool_input.get("command")
    if not isinstance(command, str) or not is_gh_write(command):
        return 0
    matcher = compile_literal_matcher(literals_from_env())
    if matcher is None:
        return no_list()

    steps = split_steps(command, powershell)
    parsed = steps is not None
    if steps is None:
        steps = [Step(0, len(command), "")]
    allowlist: frozenset[str] | None = None
    findings: list[str] = []
    scanned = [False] * len(command)
    withheld = [False] * len(command)
    base = cwd
    for k, step in enumerate(steps):
        text = command[step.start : step.end]
        cd = _CD_STEP.fullmatch(text)
        if cd and step.sep in ("&&", ";", "\n"):
            base = resolve(unquote(cd.group(1)), base)
            continue
        if not is_gh_write(text):
            continue
        if parsed:
            if allowlist is None:
                allowlist = load_allowlist()
            target = step_target(text)
            if target is not None and target in allowlist:
                continue
        piped = k > 0 and steps[k - 1].sep in ("|", "|&")
        scan_from = 0 if (piped or _EXPANSION.search(text)) else step.start
        for match in _FILE_FLAG.finditer(text):
            flag, value = match.group(1), unquote(match.group(2))
            path = body_file_path(flag, value)
            if path is None:
                continue
            for p in range(step.start + match.start(2), step.start + match.end(2)):
                withheld[p] = True
            if path == "-":
                if not any(h in command[: step.end] for h in ("<<", "@'", '@"')):
                    return deny_body(
                        f"{flag} - reads the body from a pipe, which this hook cannot see. "
                        "Use a body file, a heredoc or a here-string."
                    )
                scan_from = 0  # a heredoc or here-string body is in the command text
                continue
            shown = "<path withheld: it matches>" if matcher.search(path) else path
            if _UNEXPANDED.search(path):
                return deny_body(
                    f"body file {shown} is a path held in a shell variable, which this hook "
                    "cannot expand: it runs before the shell does. Pass a literal path."
                )
            name = Path(path.replace("\\", "/")).name
            if name and any(name in command[s.start : s.end] for s in steps[:k]):
                return deny_body(
                    f"body file {shown} is named by an earlier step of this same command, "
                    "which may write it, and this hook runs before the command, so it would "
                    "read the file as it was. Write the body file in a prior call, then run "
                    "gh in a call of its own."
                )
            try:
                contents = resolve(path, base).read_text(encoding="utf-8", errors="replace")
            except FileNotFoundError:
                return deny_body(
                    f"could not read body file {shown}: it does not exist (yet), so it was "
                    "not checked. If this command writes it, this hook cannot see that: it "
                    "runs before the command. Write it in a prior call."
                )
            except OSError as exc:
                return deny_body(
                    f"could not read body file {shown} ({type(exc).__name__}), so it was "
                    "not checked."
                )
            hit_in_file = matcher.search(contents)
            if hit_in_file:
                findings.append(f"body file {shown}, {line_col(contents, hit_in_file.start())}")
        for p in range(scan_from, step.end):
            scanned[p] = True
    sent = "".join(
        ch if scanned[p] and not withheld[p] else "\0" for p, ch in enumerate(command)
    )
    hit_in_command = matcher.search(sent)
    if hit_in_command:
        findings.insert(0, f"the command, {line_col(command, hit_in_command.start())}")
    if findings:
        return hit(findings, ALLOWED_ROUTE)
    return 0


def check_github_mcp(tool_input: dict) -> int:
    matcher = compile_literal_matcher(literals_from_env())
    if matcher is None:
        return no_list()
    owner, repo = tool_input.get("owner"), tool_input.get("repo")
    if isinstance(owner, str) and isinstance(repo, str):
        if f"{owner}/{repo}".lower() in load_allowlist():
            return 0
    findings = []
    for field, text in iter_strings(tool_input):
        found = matcher.search(text)
        if found:
            name = "<field name withheld: it matches>" if matcher.search(field) else field
            findings.append(f"field {name}, {line_col(text, found.start())}")
    return hit(findings, "") if findings else 0


def no_list() -> int:
    return deny(
        f"No personal literals configured: set {LITERALS_ENV_VAR} (one literal per line) "
        "from the list kept outside the repo. Nothing was checked, so text bound for GitHub "
        "is blocked."
    )


def hit(findings: list[str], route: str) -> int:
    return deny(
        "A personal literal (or a variant of one) is in the text bound for GitHub, at: "
        + "; ".join(findings)
        + ". Values withheld. Remove it and retry."
        + (f" {route}" if route else "")
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
    is_github = tool_name.startswith("mcp__") and "github" in tool_name.lower()
    if not is_github and tool_name not in SHELL_TOOLS:
        return 0
    if not isinstance(tool_input, dict):
        return deny("hook input has no tool_input object, so nothing was checked.")
    if is_github:
        return check_github_mcp(tool_input)
    cwd = data.get("cwd")
    base = Path(cwd) if isinstance(cwd, str) and cwd else Path.cwd()
    return check_bash(tool_input, base, powershell=tool_name == "PowerShell")


if __name__ == "__main__":
    sys.exit(main())
