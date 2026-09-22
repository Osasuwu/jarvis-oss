---
pairs_with: docs/private-literal-scrub.md
harnesses: the pre-push hook is plain git and works under any harness, or none; the literal-gate hook is a Claude Code PreToolUse hook as shipped (its matchers name Claude Code tools), and another harness needs its own wiring to call the same script (see docs/harnesses.md)
cost: two local hooks per device, installed by hand; an environment variable holding the literal list on each device; a Python start per push and per Bash or GitHub MCP call; with the list unset, every push and every GitHub write is blocked until it is set
---

# Pre-push leak gate and literal-gate hook

The two local checks that [`private-literal-scrub.md`](../docs/private-literal-scrub.md) adds to
option 3 since #100. Both read the same list as the CI scrub, match the same variants (case, the
separators `-`, `_`, space and none, and path forms), and never print a matched value.

## What each one scans

- **`scripts/pre_push_leak_gate.py`**, a git `pre-push` hook. For each ref being pushed, it
  scans the commits the remote does not have yet. For each commit it checks the message, the
  names of the files it touches, and the lines it adds. It also checks the ref names and the
  names and messages of annotated tags. A literal that is added and then removed within the same
  push still blocks, because the commit that added it is being published. Removed lines and
  author identity are not scanned. It exits 1 on a hit, and on any git error.
- **`.agents/hooks/literal-gate.py`**, a Claude Code PreToolUse hook. On the GitHub MCP server
  (matcher `^mcp__github__`) it checks every string in the tool input, at any depth. On `Bash`
  it checks only the `gh` commands that send text: `pr` and `issue` create, edit, comment,
  review, merge, close and reopen; `release` and `gist` create and edit; and `gh api` with a
  field, an input file or a write method. It reads the files named by `--body-file`, `-F` or
  `--input` and scans their contents. It blocks when a body file cannot be read, and when a body
  comes in on a pipe. It exits 2 on a hit.

With no usable list (`PERSONAL_LITERALS` unset, empty, or with no letter or digit in it), both
block and say that nothing was checked. A Bash call that sends nothing to GitHub is let through.

## Install, per device

Do it once on each device that pushes to, or writes pull requests on, a public repository.

1. **The list.** Keep it outside every repository, one literal per line, for example in
   `~/.config/personal-literals.txt`. Export it as `PERSONAL_LITERALS`:
   - Windows (PowerShell; new shells and a restarted Claude Code pick it up):
     `[Environment]::SetEnvironmentVariable("PERSONAL_LITERALS", (Get-Content -Raw "$HOME\.config\personal-literals.txt"), "User")`
   - Linux and macOS, in `~/.profile` or your shell's rc file:
     `export PERSONAL_LITERALS="$(cat "$HOME/.config/personal-literals.txt")"`

   Rerun it when the list changes. It is the same list as the `PERSONAL_LITERALS` repository
   secret the CI scrub reads, so update both.
2. **The git hook.** Create `.git/hooks/pre-push` in the clone:

   ```sh
   #!/bin/sh
   exec python3 "$(git rev-parse --show-toplevel)/scripts/pre_push_leak_gate.py" "$@"
   ```

   On Linux and macOS, run `chmod +x .git/hooks/pre-push`. On Windows, Git for Windows runs it
   through its own `sh`. Use `python` instead of `python3` there if `python3` does not start.
   All worktrees of a clone share the hooks directory, so one install covers them all.
3. **The harness hook.** Merge the two entries from
   [`literal-gate.snippet.json`](../.agents/hooks/literal-gate.snippet.json) into
   `.claude/settings.local.json`, next to anything already under `hooks.PreToolUse`. If
   `python3` does not start on the device, change it to `python` in both commands. Each command
   ends in `|| exit 2`, so a missing interpreter blocks every Bash call rather than letting it
   through.

## How to tell they ran

- A clean push prints `pre-push leak gate: clean - checked N literal(s) across M commit(s).`
  Seeing no line at all means the hook is not installed.
- The harness hook prints nothing when a call is clean. Use the canary below.

## Canary check

Run it after installing, and again after any change to the list or the hooks. `canary-7f3c9a`
is a made-up string. Use your own.

1. Add `canary-7f3c9a` as a line to the list and export it again.
2. Git hook: on a throwaway branch, commit a file containing `CANARY_7F3C9A` and push. The
   push must be blocked with `added content in <file>`. Then delete the branch.
3. Harness hook, from the repository root:

   ```sh
   echo '{"tool_name":"Bash","tool_input":{"command":"gh pr comment 1 --body canary-7f3c9a"}}' | python .agents/hooks/literal-gate.py; echo $?
   ```

   It must print `2`.
4. Remove the canary from the list and export it again.

## Not covered

- `git push --no-verify` skips the git hook.
- A device where either hook is not installed, or where the list is not set. The CI scrub still
  runs, but only on file contents, and only after the push.
- Text that reaches GitHub another way: `curl`, the browser, another harness, or a Claude Code
  session with `disableAllHooks` set.
- Commit author and committer names and emails.
- Typos, split literals, and encoded forms such as URL encoding or base64.
