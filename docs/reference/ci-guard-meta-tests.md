# Path-filtered CI guards — meta-test reference

Pull-only. Not `@import`ed by anything — read it when adding, renaming, or repointing a
blocking workflow under `.github/workflows/` that carries a `paths:` filter. Moved out of
`CLAUDE.md` by [#1418](https://github.com/Osasuwu/jarvis/issues/1418); the one-line convention
stays there, the reasoning lives here.

## The convention

`.github/workflows/X-guard.yml` ⇒ `tests/ci/test_X_guard.py`. Co-located, one meta-test per
path-filtered blocking guard.

Enforced by [`tests/ci/test_guard_test_convention.py`](../../tests/ci/test_guard_test_convention.py),
which also accepts `test_X.py` and carries a `WORKFLOW_TEST_OVERRIDES` map for the cases where
neither derived name is right (`schema-drift-check.yml` predates the convention). The override
list is the reviewable record of every exception — add an entry rather than loosening the
derivation.

### Scope: PR-blocking triggers only

The guard fires on `paths:`/`paths-ignore:` under `pull_request` and `pull_request_target`, and
ignores filters on other triggers. "Blocks PRs" is the load-bearing half of the rule: a filter on
`push` gates nothing a PR waits on, so when it drifts the cost is a wasted or skipped post-merge
run — not a check that reads green because it never ran.

`gitleaks.yml` is the live example of the split, and it is inverted from what you'd guess. Its
`push` trigger is filtered on purpose (a secret scan on every doc commit to `main` is wasted
minutes) while its `pull_request` trigger is deliberately unconditional — because
`Detect secrets with gitleaks` is a **required** check, and a required check that gets skipped
does not report "skipped". GitHub parks the context at *"Expected — waiting for status"* and the
PR is blocked forever with every other gate green. That stalled PR #894; decision `da8b97e4`.
Both halves are pinned by `TestGitleaksPrTrigger` in the convention guard.

Note the asymmetry this creates. For an ordinary check, a dead filter fails **open** (silently
green). For a required check, a filter that skips fails **closed** (permanently stuck). Neither
shows up as a red X, which is why both need a test rather than vigilance.

## Why a path filter needs its own test

A `paths:` filter is a silent failure mode by construction. When the filter stops matching the
file it was meant to watch, the workflow does not error — it simply never runs, and every PR it
should have blocked goes green. Nothing in CI notices, because "did not run" and "ran and
passed" are indistinguishable at the branch-protection layer.

This is the exact class of bug behind #289 / #310 / #311: the guard watched
`supabase/schema.sql` while the canonical file was `mcp-memory/schema.sql`. It passed silently
for a sprint.

## The two dimensions a meta-test must cover

- **Config** — assert the workflow's `paths:` filter references the canonical file(s) by their
  real path. If the canonical path later moves, this assertion goes red and forces the workflow
  to move with it. That red is the entire point: it converts a silent miss into a build break.
  The convention guard checks this half mechanically, by requiring every filtered pattern to
  appear literally somewhere in the meta-test.
- **Logic** — reimplement the guard's decision rule in Python and assert it blocks and allows
  the scenarios it claims to. `schema-drift-check` is the proof-of-concept; new guards follow
  the same shape. **This half cannot be mechanized** — the convention guard can tell that a
  meta-test exists and names the right paths, not that it exercises the right rule.

## Where the meta-tests run

`.github/workflows/ci-meta.yml`, on every PR. Deliberately **not** itself path-filtered —
path-filtering the suite that exists to catch path-filter bugs would be self-undermining.

## Related

The same rename-in-lockstep hazard applies to a guard's **check name**: branch protection
references the job name, so renaming the job without updating the protection rule makes the
gate silently disappear. See `~/.claude/reference/merge-gates.md` → *Required files per repo*.
