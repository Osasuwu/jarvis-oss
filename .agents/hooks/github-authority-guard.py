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
`pwsh -Command`/`-EncodedCommand`, `cmd /c`, `eval`, `Invoke-Expression` or as
an `ssh` remote command, or as the body of a heredoc fed to a shell, `ssh` or
another interpreter (`bash <<EOF`), gets the same verdict as the bare command;
the body of a heredoc that writes text (`cat > file <<EOF`) is not a command
(#285). Which shell
commands are gh writes is decided by `gh_write.py` (same directory), the
detector the literal gate uses too (#278); this hook decides which of those
writes take one of the three authorities.

This is a hold, not proof. A determined bypass (an alias, a script file, a
shell reading a pipe, another interpreter's own syntax such as `python -c` or
the body of `python <<EOF`) is not parsed. `.github/workflows/authority-detector.yml` is the other
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

import json
import re
import sys
from pathlib import Path

try:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from gh_write import (
        api_method,
        command_path,
        command_texts,
        flag_values,
        gh_writes,
    )
except Exception as exc:  # fail closed: without the detector every gh write would pass
    json.dump(
        {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": f"BLOCKED: gh_write.py next to this hook did not load ({exc}).",
            }
        },
        sys.stdout,
    )
    sys.exit(2)

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

# ---------------------------------------------------------------------------
# Shell commands
# ---------------------------------------------------------------------------


def _names_hold_label(values: list[str]) -> bool:
    return any(
        part.strip().lower() == HOLD_LABEL
        for v in values
        for part in v.split(",")
    )


def check_gh(args: list[str]) -> str | None:
    path = command_path(args)
    words = [w for _, w in path]
    if words == ["pr", "merge"]:
        return "gh pr merge: the human merges PRs"
    if words in (["pr", "edit"], ["issue", "edit"]):
        if _names_hold_label(flag_values(args, "--remove-label")):
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
    text = " ".join(args)
    if re.search(r"(^|\s)graphql(\s|$)", text):
        if flag_values(args, "--input"):
            return "gh api graphql --input: pass the query inline so it can be checked"
        found = _GRAPHQL_RE.search(text)
        if found:
            return f"GraphQL mutation {found.group(1)}: merges, label removal and protection are the human's"
        return None
    if _ISSUE_RE.search(text):
        if flag_values(args, "--input"):
            return "gh api --input to an issue: pass the fields inline so a labels list can be checked"
        fields = flag_values(args, "-f", "-F", "--field", "--raw-field")
        labels = [v for k, _, v in (f.partition("=") for f in fields) if re.match(r"labels(\[|$)", k)]
        if labels and not _names_hold_label(labels):
            return _LABEL_SET_REASON
    return check_rest(api_method(args), text)


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
    """Raw HTTP to the GitHub API: same REST rules, method read from the flags. The text
    comes from command_texts, so a text heredoc's body is already gone and an interpreter's
    is kept; stripping again here would drop the second too (#285)."""
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
    """The command's gh writes (gh_write.py finds them, nested shells included), then raw
    HTTP in the command and in every command text nested in it."""
    for call in gh_writes(command):
        reason = check_gh(call.args)
        if reason:
            return reason
    for text, _ in command_texts(command):
        reason = check_raw_http(text)
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
