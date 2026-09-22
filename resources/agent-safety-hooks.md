---
pairs_with: docs/agent-safety-hooks.md
harnesses: Claude Code only as shipped — `PreToolUse` hooks with a `permissionDecision: deny` exit are a harness-specific mechanism. Cursor, Codex, Gemini CLI, Copilot, Kiro and OpenCode have their own pre-call hooks (option 4 of docs/agent-safety-hooks.md lists them); the scripts need their own wiring and input parsing there. A harness without a blocking pre-call hook can't run this resource at all.
cost: no paid API calls. Runs as a local Python subprocess per matched tool call — pure CPU/regex work, no network, no model tokens spent by the hook itself.
---

# Runnable hooks: secret scanning + protected-file enforcement

Three files, ported from the source project this practice is drawn from and scrubbed of every
project-specific literal:

- [`secret-scanner.py`](../.agents/hooks/secret-scanner.py) — scans `Bash` commands, the input of
  every GitHub MCP tool (reads included), and file-write tool inputs (`Edit`/`Write`/
  `NotebookEdit`) for secret-shaped patterns (API key formats, private-key headers, credential
  assignments) and dangerous `.env`-exfiltration command shapes. Denies the tool call on a match.
- [`protected-files.py`](../.agents/hooks/protected-files.py) — denies any `Edit`/`Write`/
  `NotebookEdit` targeting a path in its `PROTECTED_CANONICAL` set. Fails closed with no
  live-operator bypass, by design — see [`docs/agent-safety-hooks.md`](../docs/agent-safety-hooks.md)
  for why.
- [`settings.snippet.json`](../.agents/hooks/settings.snippet.json) — the `PreToolUse` matcher
  block that wires both hooks into `.claude/settings.json`, using the built-in
  `$CLAUDE_PROJECT_DIR` env var so the paths resolve regardless of where the repo is checked out.

## How you know it ran

Both hooks are silent by design when they don't fire — a clean tool call produces no output, no
log line, nothing to check after the fact. That silence is deliberate (the hook shouldn't add
noise to every ordinary edit), but it means "did the hook actually run, or was it never wired up"
is not something a normal session will ever surface on its own. Two ways to check:

1. **Deliberately trip it once after wiring it in.** Ask the agent to write a string matching one
   of `secret-scanner.py`'s patterns (e.g. a `sk-ant-` prefix followed by 20+ characters) to a
   scratch file, or attempt to edit a path listed in `protected-files.py`'s `PROTECTED_CANONICAL`.
   A wired-up hook returns a `permissionDecision: deny` with a `BLOCKED:` reason and the tool call
   is refused; the agent sees this in its own transcript, not a separate log. A hook that fails to
   *launch* — `python3` missing, or too old to run the script — denies the same way: each command
   in `settings.snippet.json` is `python3 "…" || exit 2`, so a launch failure exits 2 too, instead
   of the non-blocking non-zero exit the harness would otherwise see.
2. **`claude --debug`** (or the equivalent flag for your harness) writes each hook invocation
   and its exit code to a debug log (Claude Code: `~/.claude/debug/<session-id>.txt`, not the
   terminal), including the ones that exit 0 and produce no other output — this is the only place
   a *successful, silent* run is visible at all.

Neither hook writes anywhere else — there is no dedicated log file to tail. If you want a durable
audit trail of blocks over time, that's an extension left to the reader (redirect the
`permissionDecisionReason` string to a file before returning it), not something either script
does today.

## Keeping the matcher current

The GitHub MCP entry in `settings.snippet.json` matches every tool of the server
(`^mcp__github__`), not a list of write tools. A list fails open when the server renames or adds a
tool — this happened here, recorded in
[`mcp-matcher-tool-name-drift.md`](../examples/mcp-matcher-tool-name-drift.md). The cost is one
Python process per GitHub call, reads included. `extract_github_text` has the same shape of fix:
it scans every string value in `tool_input`, at any nesting depth, rather than reading a named
list of fields — a field-name whitelist missed `custom_instructions`/`rationale`
(`assign_copilot_to_issue*`) and `commit_message`/`commit_title` (`merge_pull_request`) because
they weren't on the list.
[`test_agent_safety_hooks.py`](../tests/test_agent_safety_hooks.py) pins that behavior with a
field name the scanner's source has never named, not a fixed field list to re-check. Change the
`mcp__github__` prefix if you registered the server under another name.

## Adapting these

`PROTECTED_CANONICAL`/`PROTECTED_MIRROR` in `protected-files.py` and `_SECRET_VARS` in
`secret-scanner.py` are marked `CUSTOMIZE` at their definition, and `SECRET_PATTERNS` in that
file's docstring — they ship as
placeholders naming this repo's own hook files and `.gitleaks.toml`, not a claim that those are
the right files for every reader's repo. Point them at whatever your own project's review-gate
files and credential-shaped env vars actually are.
