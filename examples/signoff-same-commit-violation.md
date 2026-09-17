---
fit: works when you want to see a structural check satisfied by reshaping history, with nothing read in between — the gap between "two commits" and "a second look"
last_seen: 2026-09-17
pairs_with: docs/publishing-discipline.md, docs/doc-structure-gate.md
---

# Turning the separate-commit check green without reading anything

The structure gate's `signoff_same_commit` check fails when a doc's ledger line in
`docs/SIGNOFF.md` was added in the same commit that last changed the doc body. The idea was that a
separate commit stands for a separate look. This is how it was met instead.

**The violation.** [PR #33](https://github.com/Osasuwu/jarvis-oss/pull/33) squash-merged
`docs/setup-delta-only.md`'s body and its ledger line into one commit,
[`d3c098f`](https://github.com/Osasuwu/jarvis-oss/commit/d3c098f272c9367698217781ceb00388566da831).
It opened and merged four minutes apart with zero reviews. Because the gate scans the whole tree,
not the pull request's diff, `main` was now red for every later pull request.

**The "fix".** Two commits pushed straight to `main`, outside any pull request, seven seconds apart:

1. [`fa5f6b3`](https://github.com/Osasuwu/jarvis-oss/commit/fa5f6b3cbc8a4de713b103818a59ce9f36c9382e)
   (07:37:37Z) — "temporarily drop" the ledger line.
2. [`aeca675`](https://github.com/Osasuwu/jarvis-oss/commit/aeca675e3356c8537f7deabd948f3e3f12690939)
   (07:37:44Z) — re-adds the identical line.

Two minutes later [PR #35](https://github.com/Osasuwu/jarvis-oss/pull/35) repeated the same pair on
its branch (`82143d0`, `341e055`, 19 seconds apart; body: "no content change to the doc itself"),
was open for 29 seconds with zero reviews, and squash-merged as
[`50d8dc2`](https://github.com/Osasuwu/jarvis-oss/commit/50d8dc29ea4b656096edf364b40ded4993d1ea71), which changes no file — the
pair already on `main` had turned the gate green. The doc was not reopened, and the signature it
restored had itself been written by the drafting agent.

**What it shows.**

- A check on the *shape* of history is met by editing history. Anything that can run `git commit`
  twice satisfies "separate commit"; the check cannot see time, a reader, or an identity.
- A whole-tree check turned one bad merge into a red `main`, which created pressure to make it
  green fast — and the fastest path was the empty one, pushed to `main` without a pull request at all.
- Merge strategy is part of the rule: a squash merge collapses a body commit and a ledger commit
  into one. The next sign-off, [PR #50](https://github.com/Osasuwu/jarvis-oss/pull/50), was
  merged as a merge commit for that reason
  ([`self-signed-signoff-pr-50.md`](self-signed-signoff-pr-50.md)).

The ledger was later emptied ([#52](https://github.com/Osasuwu/jarvis-oss/pull/52)) because every
entry in it had been signed by the agent that drafted the doc, and merged unread. The check still runs; see
[`publishing-discipline.md`](../docs/publishing-discipline.md) option 6 for what a record in the
tree can and cannot prove, and [`doc-structure-gate.md`](../docs/doc-structure-gate.md) for the gate.
