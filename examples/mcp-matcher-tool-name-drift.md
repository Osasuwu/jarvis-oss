---
fit: works when you want a real case of a pre-call hook going silent because its matcher names tools the server no longer has — no error, no block, nothing to notice
last_seen: 2026-09-17
pairs_with: docs/agent-safety-hooks.md
---

# A hook matcher that named retired tools

The secret-scanner resource in this repo shipped on 2026-09-16 with this matcher for GitHub MCP
writes in [`settings.snippet.json`](../.agents/hooks/settings.snippet.json):

```
mcp__github__(create_or_update_file|push_files|create_pull_request|create_issue|add_issue_comment|update_issue_comment|create_pull_request_review|add_comment_to_pending_review)
```

Checked on 2026-09-17 against the
[github-mcp-server README](https://github.com/github/github-mcp-server/blob/main/README.md):
`create_issue` and `create_pull_request_review` are not in its tool list. Issues are now created
and edited through `issue_write` ("Create or update issue/pull request"), reviews through
`pull_request_review_write`, and pull request edits through `update_pull_request`, which the old
list never named.

So a secret pasted into a new issue body, a review, or an edited pull request description reached
GitHub without the scanner running. Nothing reported it: a matcher alternative that names no existing
tool simply never matches, and a hook that never matches produces no warning. The port copied an
older list; that the private source project had already moved to the new names is known only
from that project, not public.

What caught it was reading the server's tool list while rewriting
[`agent-safety-hooks.md`](../docs/agent-safety-hooks.md), not any run of the hook. A first fix
listed the current write tools, and a review of that fix found three more it had missed
(`discussion_comment_write`, `create_pull_request_with_copilot`, `sub_issue_write`) before it
merged — the same failure again. So the matcher now takes every tool of the server,
`^mcp__github__`. The scanner was first given Copilot's `problem_statement` field too; since #74
it scans every string in the input at any nesting depth, so there is no field list to re-check.
[`test_agent_safety_hooks.py`](../tests/test_agent_safety_hooks.py) checks that a tool name it has
never seen is still matched, and that a secret in a field name it has never seen is blocked.
