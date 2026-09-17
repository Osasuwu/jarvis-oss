---
fit: works when you want a real signoff_same_commit violation and its two-commit fix, not just the rule's abstract description
last_seen: 2026-09-16
pairs_with: docs/publishing-discipline.md
---

# A real `signoff_same_commit` violation, and its fix

**The violation** — [PR #33](https://github.com/Osasuwu/jarvis-oss/pull/33) squash-merged
`docs/setup-delta-only.md`'s body and its `docs/SIGNOFF.md` ledger entry into one commit on
`main` ([`d3c098f`](https://github.com/Osasuwu/jarvis-oss/commit/d3c098f272c9367698217781ceb00388566da831)).
`tests/structure_gate.py` scans the whole tree, not a PR's diff, so every later PR silently
inherited this violation the moment branch protection turned the gate into a required check.

**The fix** — [PR #35](https://github.com/Osasuwu/jarvis-oss/pull/35), two commits landed via a
merge commit rather than a squash, so both survive as distinct commits on `main`:

1. [`fa5f6b3`](https://github.com/Osasuwu/jarvis-oss/commit/fa5f6b3cbc8a4de713b103818a59ce9f36c9382e) — removes the ledger line.
2. [`aeca675`](https://github.com/Osasuwu/jarvis-oss/commit/aeca675e3356c8537f7deabd948f3e3f12690939) — re-adds it, unchanged.

Net ledger content is identical to before; only the commit boundary changed. `_check_signoff`
now resolves `docs/setup-delta-only.md`'s last touching commit (`d3c098f`) and its ledger-entry
commit (`aeca675`) as two different hashes, so the check passes.

**Why it recurs** — any PR that adds both a doc's `signed_off` frontmatter and its ledger entry
reproduces this the moment that PR is squash-merged, because squash collapses the two commits
back into one. This repo's fix for it (PR #35) and this doc/example/resource triple's own PR
both use a merge commit for exactly that reason.

See [`publishing-discipline.md`](../docs/publishing-discipline.md) for the practice this example
evidences.
