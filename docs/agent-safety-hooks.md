---
applies_when: an agent's tool call (a file write or an MCP write) can reach git history or an external system before a human looks at it, and git metadata alone can't tell a drafted change from a reviewed one
applies_when_not: every change already goes through PR + CI + human review with no autonomous-merge path, and none of the files an agent can reach would themselves weaken that review if edited
signed_off:
---

# Agent safety hooks: enforcement at the tool-call boundary

## Problem

A rule written in prose — "don't touch this file", "don't let a secret reach git" — only holds if
the agent reads it, remembers it, and isn't the thing currently going wrong. That's an acceptable
bet for most changes: send them through a PR, let CI and a human catch what's wrong before merge.
It stops being an acceptable bet for two narrow categories: files whose own compromise would
weaken that review process (the scanner config, the scanner script itself — a bad edit there
before review sees it defeats every later check), and literal secret values, which can't be
un-leaked from git history or an external system after the fact just by fixing the prompt. Those
two need something that fires whether or not the agent read any rule.

## Options tried, and why dropped

- **Prose instruction only, no mechanical check** — rejected **on fit** for the two categories
  above, not on merit: PR + CI + human review is the accepted, sufficient policy for ordinary code
  changes in the same source repo this practice is drawn from, and stays the right pick for a
  reader whose repo has no autonomous-merge lane, or whose "protected" files carry no
  review-bypass risk if edited on a branch — the narrow rationale below doesn't hold for them.
  Evidence: [`.agents/hooks/protected-files.py`](../.agents/hooks/protected-files.py)'s own
  top-of-file policy comment, ported verbatim from the source project's reasoning.
- **Auto-detecting an interactive session to pick a safe default** — rejected **on merit**: hook
  subprocesses always receive piped stdin, so a terminal-check based fallback misclassifies every
  interactive session the same way it classifies a headless one, silently picking the wrong
  default in the case that matters most. Evidence: own project, private source repo, own
  provenance — no public file to point to, since the reverted code no longer exists in either
  repo; recorded here as the reason a "detect and branch" approach was tried once and dropped.
- **Extending a live-operator bypass to the project-scoped copy of the hook** — rejected **on
  merit**: this hook has to run unmodified in unattended CI as well as an interactive session, with
  no seam that tells the two apart from inside the hook. A bypass meant for a human at the keyboard
  would silently also apply in CI, where no human is there to have earned it — so the project-scoped
  copy accepts the cost of also blocking the operator's own local interactive edits to these files,
  rather than risk that gap. Evidence:
  [`.agents/hooks/protected-files.py`](../.agents/hooks/protected-files.py)'s own docstring.

## What settled

1. **Two standalone `PreToolUse` hooks**, each a single file with no shared library or
   session-detection import, so each runs the same way regardless of harness plumbing around it.
   Evidence: [`.agents/hooks/secret-scanner.py`](../.agents/hooks/secret-scanner.py),
   [`.agents/hooks/protected-files.py`](../.agents/hooks/protected-files.py).
2. **The secret-scanning hook matches three tool surfaces**, not just local file writes:
   `Edit|Write|NotebookEdit`, `Bash`, and a regex over GitHub MCP write tools — the third one
   specifically so a literal secret pasted into an issue body, PR description, or comment gets
   caught even though it never touches the local filesystem. Evidence:
   [`.agents/hooks/settings.snippet.json`](../.agents/hooks/settings.snippet.json).
3. **The protected-file hook matches only `Edit|Write|NotebookEdit`** — not `Bash`, not the GitHub
   MCP matcher. That's a real asymmetry, not an oversight glossed over here: a file only becomes
   "protected" through a file-write tool, so the current threat model didn't need the other two
   surfaces for this hook. A reader adapting this should re-check that assumption against their own
   threat model rather than assume it always holds. Evidence: same settings snippet above.
4. **Both hooks fail closed with no bypass for the live operator** on the project-scoped copy —
   the direct consequence of the third dropped option above.

## At one developer, and at N

At one developer, the hook still blocks the operator's own interactive edits to a protected file
exactly the same way it blocks an agent's — that symmetry is the entire point of "no live-operator
bypass" (see the third dropped option). Nothing about the mechanism itself changes at N>1: same
two hooks, same matchers, same fail-closed default. What changes is who is on the other end of a
block message: at one developer, the person reading "must go through a PR + review, even for the
live operator" and the person who opens that PR are the same account; at N>1 that's a second,
distinct reviewer — the same shift [[publishing-discipline]]'s sign-off ledger makes for its own
self-hold, not a new one invented here.

## When this applies

- Applies whenever an agent's tool call can reach git history or an external system before a
  human has looked at it, and the repo can't distinguish an agent-authored change from a
  human-authored one from git metadata alone — the same condition
  [`publishing-discipline.md`](publishing-discipline.md) names for its own gate.
- Does not apply where every change already goes through PR + CI + human review with no
  autonomous-merge path, and where none of the files an agent can reach would themselves weaken
  that review if edited on a branch — see the first dropped option above.

## Worked examples

- [`protected-files-fail-closed.md`](../examples/protected-files-fail-closed.md) — the real
  fail-closed trade-off this practice accepted, recorded in the source hook's own docstring.
- [`heredoc-stripping-boundary-bug.md`](../examples/heredoc-stripping-boundary-bug.md) — a real bug
  in *what part of the input* the secret-scanner hook looked at, not in its pattern list — the
  other place a mechanical boundary check can quietly go wrong.

## Machinery

The runnable hooks behind this doc: [`agent-safety-hooks.md`](../resources/agent-safety-hooks.md).
