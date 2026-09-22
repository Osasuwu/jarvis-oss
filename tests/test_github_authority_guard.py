"""The authority guard blocks every route to a merge, a hold-label removal or a
protection write, and lets each route's harmless neighbour through.

Payloads are the PreToolUse shape the harness sends: {tool_name, tool_input}.
GitHub MCP tool names and fields checked against github/github-mcp-server's
README on 2026-09-22.
"""

import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
GUARD = ROOT / ".agents" / "hooks" / "github-authority-guard.py"
SNIPPET = ROOT / ".agents" / "hooks" / "settings.snippet.json"


def _run(tool_name: str, tool_input: dict) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(GUARD)],
        input=json.dumps({"tool_name": tool_name, "tool_input": tool_input}),
        capture_output=True,
        text=True,
    )


def _blocked(result: subprocess.CompletedProcess) -> bool:
    if result.returncode == 2:
        assert json.loads(result.stdout)["hookSpecificOutput"]["permissionDecision"] == "deny"
        return True
    assert result.returncode == 0, result.stderr
    return False


def _shell(command: str, tool: str = "Bash") -> bool:
    return _blocked(_run(tool, {"command": command}))


def _mcp(name: str, tool_input: dict) -> bool:
    return _blocked(_run(f"mcp__github__{name}", tool_input))


# --- gh CLI ---------------------------------------------------------------

BLOCKED_SHELL = [
    "gh pr merge 12",
    "gh pr merge 12 --squash --delete-branch",
    "gh pr merge --auto --squash 12",
    "gh pr merge 12 -R owner/repo",
    "gh pr -R owner/repo merge 12",
    "git push && gh pr merge 12",
    "echo $(gh pr merge 12)",
    "/usr/bin/gh pr merge 12",
    "gh.exe pr merge 12",
    "gh pr edit 12 --remove-label waiting-human-review",
    "gh pr edit 12 --remove-label=waiting-human-review",
    "gh pr edit 12 --remove-label bug,waiting-human-review",
    "gh pr edit 12 --remove-label 'bug, Waiting-Human-Review'",
    "gh issue edit 12 --remove-label waiting-human-review",
    "gh label delete waiting-human-review --yes",
    "gh label edit waiting-human-review --name released",
    "gh api -X PUT repos/o/r/branches/main/protection --input p.json",
    "gh api --method DELETE repos/o/r/branches/main/protection",
    "gh api -XPATCH repos/o/r/branches/main/protection/required_status_checks -f strict=true",
    "gh api repos/o/r/branches/main/protection/enforce_admins -X DELETE",
    "gh api repos/o/r/rulesets -f name=x",
    "gh api -X PUT repos/o/r/rulesets/7 --input r.json",
    "gh api -X PUT repos/o/r/pulls/12/merge",
    "gh api repos/o/r/merges -f base=main -f head=feature",
    "gh api -X DELETE repos/o/r/issues/12/labels/waiting-human-review",
    "gh api -X DELETE repos/o/r/issues/12/labels",
    "gh api -X PUT repos/o/r/issues/12/labels -f 'labels[]=bug'",
    "gh api -X DELETE repos/o/r/labels/waiting-human-review",
    "gh api -X PATCH repos/o/r/labels/waiting-human-review -f new_name=x",
    "gh api graphql -f query='mutation { mergePullRequest(input: {pullRequestId: \"X\"}) { clientMutationId } }'",
    "gh api graphql -f query='mutation { enablePullRequestAutoMerge(input: {pullRequestId: \"X\"}) { clientMutationId } }'",
    "gh api graphql -f query='mutation { removeLabelsFromLabelable(input: {labelableId: \"X\", labelIds: [\"L\"]}) { clientMutationId } }'",
    "gh api graphql -f query='mutation { updateBranchProtectionRule(input: {branchProtectionRuleId: \"X\"}) { clientMutationId } }'",
    "gh api graphql --input q.json",
    "curl -X PUT -H 'Authorization: token x' https://api.github.com/repos/o/r/pulls/12/merge",
    "curl -X DELETE https://api.github.com/repos/o/r/branches/main/protection",
    "Invoke-RestMethod -Method Delete https://api.github.com/repos/o/r/issues/12/labels/waiting-human-review",
]

ALLOWED_SHELL = [
    "gh pr view 12",
    "gh pr list --state open",
    "gh pr create --title t --body 'never run gh pr merge here'",
    "gh pr checks 12",
    "gh pr edit 12 --add-label waiting-human-review",
    "gh pr edit 12 --remove-label bug",
    "gh issue edit 12 --add-label status:owner-queue --remove-label needs-grill",
    "gh label create waiting-human-review",
    "gh label list",
    "gh api repos/o/r/branches/main/protection",
    "gh api -X GET repos/o/r/rulesets",
    "gh api repos/o/r/pulls/12/merge",
    "gh api repos/o/r/issues/12/labels -f 'labels[]=bug'",
    "gh api graphql -f query='query { repository(owner: \"o\", name: \"r\") { pullRequest(number: 1) { mergeable } } }'",
    "curl https://api.github.com/repos/o/r/branches/main/protection",
    "git merge origin/main",
    "git log --grep 'gh pr merge'",
    "gh pr create --body-file - <<'EOF'\nthen gh pr merge 12 --auto\nEOF\n",
]


@pytest.mark.parametrize("command", BLOCKED_SHELL)
def test_blocks_shell_route(command):
    assert _shell(command), command


@pytest.mark.parametrize("command", ALLOWED_SHELL)
def test_allows_shell_neighbour(command):
    assert not _shell(command), command


def test_powershell_tool_is_checked_the_same_way():
    assert _shell("gh pr merge 12", tool="PowerShell")
    assert not _shell("gh pr view 12", tool="PowerShell")


def test_unbalanced_quotes_do_not_hide_a_call():
    assert _shell("gh pr merge 12 --body 'unterminated")


# --- GitHub MCP -----------------------------------------------------------

BLOCKED_MCP = [
    ("merge_pull_request", {"owner": "o", "repo": "r", "pullNumber": 12, "merge_method": "squash"}),
    ("enable_pull_request_auto_merge", {"owner": "o", "repo": "r", "pullNumber": 12}),
    ("some_future_merge_tool", {"owner": "o", "repo": "r"}),
    ("update_branch_protection", {"owner": "o", "repo": "r", "branch": "main"}),
    ("create_repository_ruleset", {"owner": "o", "repo": "r"}),
    ("issue_write", {"method": "update", "owner": "o", "repo": "r", "issue_number": 12, "labels": ["bug"]}),
    ("issue_write", {"method": "update", "owner": "o", "repo": "r", "issue_number": 12, "labels": []}),
    ("update_pull_request", {"owner": "o", "repo": "r", "pullNumber": 12, "labels": ["bug"]}),
    ("label_write", {"method": "delete", "owner": "o", "repo": "r", "name": "waiting-human-review"}),
    ("label_write", {"method": "update", "owner": "o", "repo": "r", "name": "waiting-human-review", "new_name": "x"}),
    ("remove_issue_label", {"owner": "o", "repo": "r", "issue_number": 12, "label": "waiting-human-review"}),
]

ALLOWED_MCP = [
    ("pull_request_read", {"method": "get", "owner": "o", "repo": "r", "pullNumber": 12}),
    ("list_pull_requests", {"owner": "o", "repo": "r"}),
    ("get_branch_protection", {"owner": "o", "repo": "r", "branch": "main"}),
    ("update_pull_request_branch", {"owner": "o", "repo": "r", "pullNumber": 12}),
    ("create_pull_request", {"owner": "o", "repo": "r", "title": "t", "head": "h", "base": "main"}),
    ("issue_write", {"method": "create", "owner": "o", "repo": "r", "title": "t", "labels": ["bug"]}),
    ("issue_write", {"method": "update", "owner": "o", "repo": "r", "issue_number": 12, "labels": ["bug", "waiting-human-review"]}),
    ("issue_write", {"method": "update", "owner": "o", "repo": "r", "issue_number": 12, "state": "closed"}),
    ("label_write", {"method": "create", "owner": "o", "repo": "r", "name": "waiting-human-review"}),
    ("label_write", {"method": "delete", "owner": "o", "repo": "r", "name": "stale"}),
    ("add_issue_comment", {"owner": "o", "repo": "r", "issue_number": 12, "body": "please merge_pull_request"}),
]


@pytest.mark.parametrize(("name", "tool_input"), BLOCKED_MCP)
def test_blocks_mcp_route(name, tool_input):
    assert _mcp(name, tool_input), name


@pytest.mark.parametrize(("name", "tool_input"), ALLOWED_MCP)
def test_allows_mcp_neighbour(name, tool_input):
    assert not _mcp(name, tool_input), name


def test_other_mcp_servers_are_not_judged_by_github_rules():
    # e.g. a database server's own `merge_branch` is not a PR merge.
    assert not _blocked(_run("mcp__db__merge_branch", {"branch_id": "b"}))


def test_non_github_tools_pass():
    assert not _blocked(_run("Read", {"file_path": "gh pr merge"}))


# --- wiring ---------------------------------------------------------------


def _guard_matchers() -> list[str]:
    config = json.loads(SNIPPET.read_text(encoding="utf-8"))
    return [
        entry["matcher"]
        for entry in config["hooks"]["PreToolUse"]
        if any("github-authority-guard.py" in h["command"] for h in entry["hooks"])
    ]


@pytest.mark.parametrize(
    "tool",
    [
        "Bash",
        "PowerShell",
        "mcp__github__merge_pull_request",
        "mcp__github__some_future_write_tool",
        "mcp__plugin_github_github__merge_pull_request",
    ],
)
def test_snippet_routes_every_route_through_the_guard(tool):
    # Claude Code matchers are full-name regexes for alternations.
    assert any(re.fullmatch(m, tool) or (m.startswith("^") and re.search(m, tool)) for m in _guard_matchers()), tool


def test_guard_commands_fail_closed_on_launch_failure():
    config = json.loads(SNIPPET.read_text(encoding="utf-8"))
    cmds = [
        h["command"]
        for entry in config["hooks"]["PreToolUse"]
        for h in entry["hooks"]
        if "github-authority-guard.py" in h["command"]
    ]
    assert len(cmds) == 2
    for cmd in cmds:
        assert re.search(r"\|\|\s*exit\s+2\s*$", cmd), cmd


def test_guard_script_is_protected_from_agent_edits():
    text = (ROOT / ".agents" / "hooks" / "protected-files.py").read_text(encoding="utf-8")
    assert '".agents/hooks/github-authority-guard.py"' in text
