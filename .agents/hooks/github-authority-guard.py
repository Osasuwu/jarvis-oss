"""PreToolUse hook: keep the agent off the human's three GitHub authorities.

The agent works under the human's account and token, so GitHub itself cannot
tell the two apart. This hook blocks the agent from:

- merging a PR (including auto-merge and the merge queue),
- removing the `waiting-human-review` hold label (or deleting/renaming it),
- writing branch protection or rulesets.

Routes covered: the `gh` CLI from Bash or PowerShell (`gh pr merge`,
`gh pr/issue edit --remove-label`, `gh label delete/edit`, `gh api` REST and
GraphQL), with global flags such as `-R` anywhere before the subcommand, raw
HTTP to api.github.com, and every GitHub MCP tool (the matcher takes the whole
server, see settings.snippet.json). A command run through `sh -c`/`bash -c`,
`pwsh -Command`/`-EncodedCommand`, `cmd /c`, `eval` or `Invoke-Expression`
gets the same verdict as the bare command.

This is a hold, not proof. A determined bypass (an alias, a script file, a
shell reading stdin or a heredoc, another interpreter such as `python -c`) is
not parsed. `.github/workflows/authority-detector.yml` is the other
half: it reports every label removal and protection change after the fact.

Reads the PreToolUse payload from stdin. Exits 2 with a deny decision to block.

Install: the matchers `Bash|PowerShell` and `^mcp__.*github` in `settings.snippet.json`
(same directory) run it; the MCP matcher takes the whole server under any prefix. Merge
those entries into `.claude/settings.local.json` under `hooks.PreToolUse`. Each command runs `python3` if it starts, else `python`, and is chained with
`|| exit 2`, so an interpreter that fails to launch blocks instead of passing. The
untracked `.claude/settings.local.json` is not in a fresh checkout; `.worktreeinclude`
lists it so Claude Code copies it into the worktrees it makes, while one made by
`git worktree add` needs a hand copy. Claude Code only as shipped; other harnesses
need their own wiring and parsing.

Check: the hook is silent when it does not fire. Ask the agent to merge a pull request;
a wired hook denies with a reason in the transcript, and one that fails to launch blocks
with the shell's error text instead. From the repository root, feeding it a payload by
hand must exit `2`:
`echo '{"tool_name":"Bash","tool_input":{"command":"gh pr merge 1"}}' | python .agents/hooks/github-authority-guard.py; echo $?`
`claude --debug` logs every hook invocation and exit code to
`~/.claude/debug/<session-id>.txt`. `tests/test_github_authority_guard.py` runs it on
constructed calls.
"""

import base64
import json
import re
import shlex
import sys

HOLD_LABEL = "waiting-human-review"

SHELL_TOOLS = ("Bash", "PowerShell")

# GraphQL mutations that merge, enqueue a merge, strip labels, or write
# protection. Matched as whole words anywhere in the query text.
GRAPHQL_MUTATIONS = (
    "mergePullRequest",
    "enablePullRequestAutoMerge",
    "enqueuePullRequest",
    "mergeBranch",
    "removeLabelsFromLabelable",
    "clearLabelsFromLabelable",
    "deleteLabel",
    "updateLabel",
    "createBranchProtectionRule",
    "updateBranchProtectionRule",
    "deleteBranchProtectionRule",
    "createRepositoryRuleset",
    "updateRepositoryRuleset",
    "deleteRepositoryRuleset",
)
_GRAPHQL_RE = re.compile(r"\b(" + "|".join(GRAPHQL_MUTATIONS) + r")\b")

# Same heredoc stripping as secret-scanner.py: a heredoc body is text (a PR
# body, a commit message), not a command, and may mention `gh pr merge`.
_HEREDOC_RE = re.compile(
    r"<<-?\s*'?(\w+)'?\s*\n.*?\n\s*\1\s*(?:\)|(?=\n)|$)",
    re.DOTALL,
)

_SEPARATORS = {";", "&&", "||", "|", "&", "(", ")", "$", "\n"}


# ---------------------------------------------------------------------------
# Shell commands
# ---------------------------------------------------------------------------


def _tokens(command: str) -> list[str]:
    lexer = shlex.shlex(command, posix=True, punctuation_chars=True)
    lexer.whitespace_split = True
    return list(lexer)


def _invocations(command: str) -> list[tuple[str, list[str]]]:
    """Every word of the command as (program name, the arguments after it up
    to the next separator). The name is lowercased, without directory, `.exe`
    or backticks (`` `gh pr merge` `` is a command substitution).

    Quoted arguments stay single tokens, so `--body "... gh pr merge ..."` is
    not an invocation. Unparseable input falls back to a whitespace split, so
    a parse error cannot hide a call.
    """
    command = _HEREDOC_RE.sub("", command)
    try:
        tokens = _tokens(command)
    except ValueError:
        tokens = command.split()
    calls = []
    for i, tok in enumerate(tokens):
        base = tok.replace("`", "").replace("\\", "/").rsplit("/", 1)[-1].lower()
        args = []
        for t in tokens[i + 1 :]:
            if t in _SEPARATORS or set(t) <= set(";&|()"):
                break
            args.append(t)
        calls.append((base.removesuffix(".exe"), args))
    return calls


_POSIX_SHELLS = {"sh", "bash", "zsh", "dash", "ksh", "fish"}
_POSIX_COMMAND_FLAG = re.compile(r"-[A-Za-z]*c[A-Za-z]*|--command")


def _decode_ps(value: str) -> str:
    """pwsh -EncodedCommand takes base64 of UTF-16LE text."""
    try:
        return base64.b64decode(value, validate=True).decode("utf-16-le")
    except ValueError:  # binascii.Error and UnicodeDecodeError are ValueErrors
        return ""


def _nested_commands(prog: str, args: list[str]) -> list[str]:
    """Command text a wrapper runs: `sh -c '...'` (and bash, zsh, ...),
    `pwsh -Command ...`, `pwsh -EncodedCommand ...`, `cmd /c ...`, `eval ...`,
    `Invoke-Expression ...`. A shell reading a script file or stdin is not seen.
    """
    if prog in _POSIX_SHELLS:
        # The first word after -c is the script, the rest are its $0, $1...;
        # each is checked, which over-blocks only a gh write passed as data.
        for j, a in enumerate(args):
            if _POSIX_COMMAND_FLAG.fullmatch(a):
                return args[j + 1 :]
    elif prog in ("pwsh", "powershell"):
        for j, a in enumerate(args):
            # Parameter names are case-insensitive, any unambiguous prefix, `-` or `/`.
            flag = "-" + a[1:].lower() if a[:1] in ("-", "/") else ""
            if len(flag) < 2:
                continue
            if flag in ("-cwa", "-commandwithargs") or "-command".startswith(flag):
                return [" ".join(args[j + 1 :])]
            if flag == "-ec" or "-encodedcommand".startswith(flag):
                return [_decode_ps(v) for v in args[j + 1 : j + 2]]
    elif prog == "cmd":
        for j, a in enumerate(args):
            if a.lower() in ("/c", "/k"):
                return [" ".join(args[j + 1 :])]
    elif prog in ("eval", "iex", "invoke-expression"):
        return [" ".join(args)]
    return []


def _flag_values(args: list[str], *names: str) -> list[str]:
    """Values of a flag given as `--name value`, `--name=value` or `-Xvalue`."""
    values = []
    for i, a in enumerate(args):
        for n in names:
            if a == n and i + 1 < len(args):
                values.append(args[i + 1])
            elif n.startswith("--") and a.startswith(n + "="):
                values.append(a[len(n) + 1 :])
            elif not n.startswith("--") and a.startswith(n) and len(a) > len(n):
                values.append(a[len(n) :])
    return values


def _names_hold_label(values: list[str]) -> bool:
    return any(
        part.strip().lower() == HOLD_LABEL
        for v in values
        for part in v.split(",")
    )


def _api_method(args: list[str]) -> str:
    methods = _flag_values(args, "-X", "--method")
    if methods:
        return methods[-1].upper()
    # gh api switches to POST when any field or input is given.
    if _flag_values(args, "-f", "-F", "--field", "--raw-field", "--input"):
        return "POST"
    return "GET"


def _command_path(args: list[str]) -> list[tuple[int, str]]:
    """The first two positional words, with their index, found the way gh's
    command parser (cobra) finds a subcommand: a `-X` or `--name` flag without
    `=` consumes the next word as its value. So `gh -R o/r pr merge` and
    `gh pr -R o/r merge` both give `pr merge`. (The boolean flags cobra knows
    there, `--help` and `--version`, print help instead of running anything,
    so reading them as value flags cannot hide a write.)
    """
    path = []
    i = 0
    while i < len(args) and len(path) < 2:
        a = args[i]
        if not a.startswith("-"):
            path.append((i, a))
        elif "=" not in a and (a.startswith("--") or len(a) == 2):
            i += 1
        i += 1
    return path


def check_gh(args: list[str]) -> str | None:
    path = _command_path(args)
    words = [w for _, w in path]
    if words == ["pr", "merge"]:
        return "gh pr merge: the human merges PRs"
    if words in (["pr", "edit"], ["issue", "edit"]):
        if _names_hold_label(_flag_values(args, "--remove-label")):
            return f"removing the {HOLD_LABEL} label: only the human releases the hold"
    if len(words) == 2 and words[0] == "label" and words[1] in ("delete", "edit"):
        # Any argument naming the hold label, wherever a flag puts it.
        rest = args[path[1][0] + 1 :]
        if any(a.lower() == HOLD_LABEL for a in rest if not a.startswith("-")):
            return f"gh label {words[1]} {HOLD_LABEL}: the hold label is the human's"
    if words[:1] == ["api"]:
        return check_api(args)
    return None


# Updating an issue or PR (`PATCH /issues/N`) with a `labels` list replaces the
# whole set, so a list without the hold label drops it.
_ISSUE_RE = re.compile(r"/issues/\d+(?![\d/])")
_LABEL_SET_REASON = (
    f"a labels list without {HOLD_LABEL} replaces the set and drops the hold; "
    "add labels with `gh issue edit --add-label` instead"
)


def check_api(args: list[str]) -> str | None:
    method = _api_method(args)
    text = " ".join(args)
    if re.search(r"(^|\s)graphql(\s|$)", text):
        if _flag_values(args, "--input"):
            return "gh api graphql --input: pass the query inline so it can be checked"
        found = _GRAPHQL_RE.search(text)
        if found:
            return f"GraphQL mutation {found.group(1)}: merges, label removal and protection are the human's"
        return None
    if _ISSUE_RE.search(text):
        if _flag_values(args, "--input"):
            return "gh api --input to an issue: pass the fields inline so a labels list can be checked"
        fields = _flag_values(args, "-f", "-F", "--field", "--raw-field")
        labels = [v for k, _, v in (f.partition("=") for f in fields) if re.match(r"labels(\[|$)", k)]
        if labels and not _names_hold_label(labels):
            return _LABEL_SET_REASON
    return check_rest(method, text)


def check_rest(method: str, text: str) -> str | None:
    if method == "GET":
        return None
    if re.search(r"/protection\b|/rulesets\b", text):
        return f"{method} to branch protection or rulesets: protection is the human's"
    if re.search(r"/pulls/\d+/merge\b", text) or re.search(r"/merges\b", text):
        return f"{method} to a merge endpoint: the human merges PRs"
    if method in ("PUT", "DELETE") and re.search(r"/issues/\d+/labels\b", text):
        return f"{method} on an issue's labels can drop {HOLD_LABEL}; add labels with POST instead"
    if method in ("PATCH", "DELETE") and re.search(
        rf"/labels/{re.escape(HOLD_LABEL)}\b", text, re.I
    ):
        return f"{method} on the {HOLD_LABEL} label itself: the hold label is the human's"
    return None


_HTTP_TOOL_RE = re.compile(
    r"\b(curl|wget|Invoke-WebRequest|Invoke-RestMethod|iwr|irm)\b", re.I
)
_HTTP_METHOD_RE = re.compile(
    r"(?:-X\s*|--request[\s=]|-Method\s+)['\"]?(PUT|POST|PATCH|DELETE)", re.I
)


def check_raw_http(command: str) -> str | None:
    """Raw HTTP to the GitHub API: same REST rules, method read from the flags."""
    command = _HEREDOC_RE.sub("", command)
    if "api.github.com" not in command or not _HTTP_TOOL_RE.search(command):
        return None
    if _GRAPHQL_RE.search(command):
        return "GraphQL mutation over raw HTTP: merges, label removal and protection are the human's"
    m = _HTTP_METHOD_RE.search(command)
    method = m.group(1).upper() if m else ("POST" if re.search(r"\s(-d|--data)", command) else "GET")
    if (
        method != "GET"
        and _ISSUE_RE.search(command)
        and re.search(r"\blabels\b", command)
        and HOLD_LABEL not in command.lower()
    ):
        return _LABEL_SET_REASON
    return check_rest(method, command)


def check_shell(command: str) -> str | None:
    calls = _invocations(command)
    for prog, args in calls:
        reason = check_gh(args) if prog == "gh" else None
        if reason:
            return reason
    reason = check_raw_http(command)
    if reason:
        return reason
    # A nested shell's command text gets the same verdict as the bare command.
    for prog, args in calls:
        for nested in _nested_commands(prog, args):
            reason = check_shell(nested)
            if reason:
                return reason
    return None


# ---------------------------------------------------------------------------
# GitHub MCP tools
# ---------------------------------------------------------------------------

_READ_PREFIXES = ("get_", "list_", "search_")


def _strings(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for v in value.values():
            yield from _strings(v)
    elif isinstance(value, list):
        for v in value:
            yield from _strings(v)


def check_mcp(tool_name: str, tool_input: dict) -> str | None:
    """Rules by tool-name class, not a tool-name list: names drift
    and a matcher that lists tool names goes quiet when the server renames one."""
    name = tool_name.rsplit("__", 1)[-1].lower()
    if name.startswith(_READ_PREFIXES) or name.endswith("_read"):
        return None
    if "merge" in name:
        return f"{tool_name}: the human merges PRs"
    if "protection" in name or "ruleset" in name:
        return f"{tool_name}: protection is the human's"

    mentions_hold = any(s.strip().lower() == HOLD_LABEL for s in _strings(tool_input))
    method = str(tool_input.get("method", "")).lower()
    if "label" in name and mentions_hold and (
        method in ("delete", "update") or "remove" in name or "delete" in name
    ):
        return f"{tool_name} on {HOLD_LABEL}: only the human releases the hold"

    # A `labels` list replaces the whole set. Without the hold label in it,
    # an update can silently drop the hold. Creating a new item cannot.
    labels = tool_input.get("labels")
    if isinstance(labels, list) and method != "create" and not name.startswith(("create_", "add_")):
        if not any(isinstance(lbl, str) and lbl.strip().lower() == HOLD_LABEL for lbl in labels):
            return (
                f"{tool_name} replaces the label set without {HOLD_LABEL}, which can drop "
                "the hold; add labels with `gh issue edit --add-label` instead"
            )
    return None


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def block(reason: str):
    json.dump(
        {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": f"BLOCKED: {reason}. This is the human's authority (#99).",
            }
        },
        sys.stdout,
    )
    sys.exit(2)


def main():
    raw = sys.stdin.read()
    if not raw.strip():
        sys.exit(0)
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        block("unparseable hook payload")

    tool_name = data.get("tool_name", "")
    tool_input = data.get("tool_input", {})
    if not isinstance(tool_input, dict):
        sys.exit(0)

    if tool_name in SHELL_TOOLS:
        command = tool_input.get("command", "")
        reason = check_shell(command) if isinstance(command, str) else None
    elif tool_name.startswith("mcp__") and "github" in tool_name.lower():
        reason = check_mcp(tool_name, tool_input)
    else:
        reason = None

    if reason:
        block(reason)
    sys.exit(0)


if __name__ == "__main__":
    main()
