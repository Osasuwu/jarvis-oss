"""PreToolUse hook: keep the agent off the human's three GitHub authorities.

The agent works under the human's account and token, so GitHub itself cannot
tell the two apart. This hook blocks the agent from:

- merging a PR (including auto-merge and the merge queue),
- removing the `waiting-human-review` hold label (or deleting/renaming it),
- writing branch protection or rulesets.

Routes covered: the `gh` CLI from Bash or PowerShell (`gh pr merge`,
`gh pr/issue edit --remove-label`, `gh label delete/edit`, `gh api` REST and
GraphQL), raw HTTP to api.github.com, and every GitHub MCP tool (the matcher
takes the whole server, see settings.snippet.json).

This is a hold, not proof. A determined bypass (an alias, `bash -c`, a script
file) is not parsed. `.github/workflows/authority-detector.yml` is the other
half: it reports every label removal and protection change after the fact.

Reads the PreToolUse payload from stdin. Exits 2 with a deny decision to block.
"""

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


def _gh_invocations(command: str) -> list[list[str]]:
    """Return the argument list after each `gh` executable in the command.

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
        base = tok.replace("\\", "/").rsplit("/", 1)[-1].lower()
        if base in ("gh", "gh.exe"):
            args = []
            for t in tokens[i + 1 :]:
                if t in _SEPARATORS or set(t) <= set(";&|()"):
                    break
                args.append(t)
            calls.append(args)
    return calls


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


def check_gh(args: list[str]) -> str | None:
    # Positional words; a flag value (`-R owner/repo`) may sit between them,
    # so the subcommand is looked for in the next two words, not just one.
    words = [a for a in args if not a.startswith("-")]
    sub = words[1:3]
    if words[:1] == ["pr"] and "merge" in sub:
        return "gh pr merge: the human merges PRs"
    if words[:1] in (["pr"], ["issue"]) and "edit" in sub:
        if _names_hold_label(_flag_values(args, "--remove-label")):
            return f"removing the {HOLD_LABEL} label: only the human releases the hold"
    if len(words) >= 3 and words[0] == "label" and words[1] in ("delete", "edit"):
        if words[2].lower() == HOLD_LABEL:
            return f"gh label {words[1]} {HOLD_LABEL}: the hold label is the human's"
    if words and words[0] == "api":
        return check_api(args)
    return None


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
    return check_rest(method, command)


def check_shell(command: str) -> str | None:
    for args in _gh_invocations(command):
        reason = check_gh(args)
        if reason:
            return reason
    return check_raw_http(command)


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
    (examples/mcp-matcher-tool-name-drift.md)."""
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
        if not any(isinstance(l, str) and l.strip().lower() == HOLD_LABEL for l in labels):
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
