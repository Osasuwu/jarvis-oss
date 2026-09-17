---
pairs_with: docs/agent-safety-hooks.md
harnesses: Claude Code only — `PreToolUse` hooks with a `permissionDecision: deny` exit are a harness-specific mechanism; see docs/harnesses.md for the table of what each harness does and doesn't support instead. A harness without a tool-call-boundary hook can't run this resource as-is — it needs the read-vs-write review-gate treatment `docs/harnesses.md` covers for that case.
cost: no paid API calls. Runs as a local Python subprocess per matched tool call — pure CPU/regex work, no network, no model tokens spent by the hook itself.
---

# Runnable hooks: secret scanning + protected-file enforcement

Three files, ported from the source project this practice is drawn from and scrubbed of every
project-specific literal:

- [`secret-scanner.py`](../.agents/hooks/secret-scanner.py) — scans `Bash` commands, GitHub MCP
  write-tool inputs, and file-write tool inputs (`Edit`/`Write`/`NotebookEdit`) for secret-shaped
  patterns (API key formats, private-key headers, credential assignments) and dangerous
  `.env`-exfiltration command shapes. Denies the tool call on a match.
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
   is refused; the agent sees this in its own transcript, not a separate log.
2. **`claude --debug`** (or the equivalent verbose/debug flag for your harness) prints each hook
   invocation and its exit code to stderr as it runs, including the ones that exit 0 and produce
   no other output — this is the only place a *successful, silent* run is visible at all.

Neither hook writes anywhere else — there is no dedicated log file to tail. If you want a durable
audit trail of blocks over time, that's an extension left to the reader (redirect the
`permissionDecisionReason` string to a file before returning it), not something either script
does today.

## Adapting these

Both `PROTECTED_CANONICAL`/`PROTECTED_MIRROR` in `protected-files.py` and `_SECRET_VARS`/
`SECRET_PATTERNS` in `secret-scanner.py` are marked `CUSTOMIZE` at their definition — they ship as
placeholders naming this repo's own hook files and `.gitleaks.toml`, not a claim that those are
the right files for every reader's repo. Point them at whatever your own project's review-gate
files and credential-shaped env vars actually are.
