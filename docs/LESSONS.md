# LESSONS.md — hard-won operating lessons

Distilled from an agent's accumulated task outcomes. These are the *transferable*
lessons — the project-specific incidents that produced them are stripped out. Read this
for the "why things are done a certain way" without needing anyone's private history.

They're written for an autonomous coding agent, but most are just good engineering
discipline. Nothing here is a live dependency; treat it as accumulated judgment.

---

## Diagnosis

- **Measure before you rewrite.** When a bug issue states a mechanism ("the solver
  fails to converge", "axis X is off"), verify that mechanism against the code *and* a
  direct measurement before writing the fix. Repeatedly, the stated culprit was the clean
  component and the real defect was elsewhere. The fix must target the axis/module that
  actually deviates, not the one the issue named. When measurement contradicts the issue's
  premise, escalate and reformulate the acceptance criteria rather than force-fitting a
  test to a false premise.

- **Trust the checked-out SHA, not your theory of the build.** When CI throws a
  `NameError`/`ImportError` on a symbol, first `git show HEAD:<file>` to confirm the symbol
  actually exists at the commit CI checked out — *before* theorizing about stale caches,
  dirty virtualenvs, or runner state. A rebase across a commit that *relocated* a helper
  (dropped the old `def`, kept your new call site) is the far likelier cause. A misdiagnosis
  that ships a "hardening" fix for the wrong cause leaves misleading comments behind that
  someone later has to un-write.

- **A misdiagnosis costs twice.** Once to chase the wrong cause, again to correct the
  false trail it left in docs/commit messages. Prove your theory reproduces (or fails to)
  before committing to it.

## Acceptance criteria & verification

- **Trace every AC to a test that fails if the wiring is absent.** "Produces the cache"
  and "the consumer reads the cache back" are *separate* acceptance criteria that need
  *separate* proof. A "detector ships" PR that only tests the producing half will merge
  with the consuming half silently unwired. If removing the feature wouldn't fail any test,
  the feature isn't verified.

- **The 30-second already-done grep pays off constantly.** Before implementing an AC,
  grep whether its symbols/markers already exist. When an AC offers "fix root cause **or**
  isolate", an isolation already in place counts as done — don't re-do it.

- **Full-suite + real-launch smoke is non-optional for entrypoints and servers.** Unit
  tests run from the repo root with dependencies and `sys.path` already satisfied — they
  never exercise the *real* launch (the venv interpreter, the script's own directory as
  `sys.path[0]`, cross-module global-name collisions). "6 unit tests green" is not "the
  server starts". Always run the assembled suite and a real process smoke for anything that
  gets launched as a process.

## Fixes that stick

- **Sibling-grep: fix the class, not the instance.** When a reviewer flags one bug,
  grep for the same pattern across the file and related files before declaring done. Adding
  a new enum/terminal state often trips a sibling invariant elsewhere that a single-site fix
  misses. A second review round with the same class of finding means the first fix was
  partial.

- **Scope discipline in refactors.** Consolidate only *named*, copy-pasted helpers.
  Leave inline behavior-carrying code (hot loops, safety-critical paths) untouched unless a
  test covers the touched behavior — needless churn is behavior risk. A re-export alias
  (`from x import new_name as _old_name`) lets you delete a duplicate body while preserving
  existing imports.

- **Prefer fixing at the non-critical instantiation site.** When a default is wrong,
  change it at the caller/wiring site rather than in a widely-used core default — you keep
  the critical zone untouched and don't disturb every other consumer.

## CI gates & review loops

- **Distinguish a broken gate from a real red.** A required check failing because the
  review bot errored, posted no comment, or evaluated a stale tree is a *broken gate* — the
  fix is to re-run the job, not to touch code, and not to weaken the check. A check failing
  because it found a genuine defect is a *real red*. Never normalize admin-merging around a
  red gate: knowing a gate is broken and routinely merging past it silently disables the
  protection for every future change. Fix the gate; admin-merge the one blocked change only
  with a tracking issue linked.

- **Fail closed, and split "no data" from "failure".** Data-gathering functions must
  distinguish `None` (transport/fetch failed → not OK) from `[]` (queried fine, empty
  result → OK). Conflating them hides an outage behind "nothing to report". Verdict/health
  renderers should refuse a green line unless freshness is positively proven, so a malformed
  input can't read as "all clear".

- **Review gates can evaluate a stale commit.** When polling a review gate, check which
  SHA the verdict actually reviewed before treating a finding as actionable — a gate lagging
  the pushed head re-surfaces already-fixed findings as fresh. Re-poll after each push;
  convergence lags the code by a round.

- **Don't count bot noise as complexity.** "32 commits" where 28 are auto-rebase merges
  is ~4 real rework rounds. And when a PR hits 3+ genuine rework rounds, that's the signal
  to stop and decide — merge-as-is with known minor issues, or close and rewrite with a
  better design. Stale PRs compound cost: every day unmerged risks a needs-rebase that
  burns tokens on the next round.

## Platform gotchas

- **stdin inheritance hangs pipes.** A stdio subprocess-spawning server must pass
  `stdin=DEVNULL` (not just stdout/stderr) to every subprocess, or a grandchild inherits the
  pipe and hangs. This class of bug only reproduces inside the real host process, not from a
  shell — test tools in their actual runtime.

- **Windows console encoding crashes on non-ASCII.** A CLI that prints emoji/Unicode
  crashes under a legacy code page (e.g. cp1251). `sys.stdout.reconfigure(encoding="utf-8")`
  in `main()` is the fix — and only a real subprocess smoke catches it, never a unit test
  that captures stdout differently.

- **OS-specific subprocess/signal code is high-interaction.** Windows tree-kill, pipe
  handles, POSIX process-group signals — each fix exposes the next corner case. This class
  belongs in design/grill *before* implementation, not in rework after.

- **Some GitHub API mutations are destructive by name.** Editing a Project single-select
  option set *regenerates every option ID* — it does not preserve IDs by name — so any stored
  option IDs go stale and must all be re-fetched, and the full desired set must be re-sent in
  the mutation. Paginated list fields that you query with a fixed page size silently truncate
  once the collection exceeds it; a board at exactly the page limit is a warning sign.

## Resuming across context compaction

- **Re-verify your working branch before you commit.** After a context compaction (or any
  long-running session), run `git branch --show-current` immediately before `git commit` —
  don't trust the session-start snapshot. A claim/checkout step from a pre-compaction context
  can leave you on a drifted branch, and a commit lands on the wrong one.

- **`git log` vs the base branch, not just `git status`.** Changes committed in a prior
  session context won't show in `git status`. Check the log against the base branch before
  assuming the working tree is the whole picture.

## Memory hygiene

- **A memory-row reference must be an actual memory-row ID.** Don't pass a decision/episode
  UUID where a memory-row foreign key is expected — it will be rejected (or silently break the
  join). Provenance, not UUID shape, is what discriminates a memory row from an episode row.
  When a skill's prose prescribes an exact API literal, that prose *is* executable spec: a
  wrong literal reproduces the same failure in every session until the text is fixed.

- **Schema/DB migrations are a separate authorization from code changes.** A CHECK-constraint
  or schema change against a shared production database is a deploy step to document and gate,
  not something to auto-apply alongside a normal repo-file change.

- **Synchronous DB queries stop being cheap.** A clustering/aggregation query that timed out
  (`statement_timeout`) as data grew is a signal it needs an index, a smaller batch, a raised
  per-RPC timeout, or server-side pagination — not a client retry.
