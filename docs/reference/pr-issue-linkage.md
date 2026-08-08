# PR → issue linkage — full reference

Pull-only. Not `@import`ed by anything — read it when a PR absorbs more than one issue, when
you are closing a PR as superseded, or when an issue stayed open after its work shipped. Moved
out of `CLAUDE.md` by [#1418](https://github.com/Osasuwu/jarvis/issues/1418): the rule an agent
must act on is one line (*list `Closes #N` for every absorbed issue*), and everything below is
the mechanism and the failure catalogue behind it — read once, not every session.

## The mechanism

Auto-close (native GitHub *and* `pr-merged.yml`) fires from the PR's own
`closingIssuesReferences` **before merge**, and — separately — from GitHub's own commit-message
scan of whatever commit actually lands on the default branch **at merge time**. That pre-merge
list is built from closing keywords — `Closes` / `Fixes` / `Resolves #N` and their
`close/closed/fix/fixed/resolve/resolved` variants — found in **either the PR body or any commit
message on the branch**, not the body alone. Nothing else feeds it: not a linked branch name, not
the issue's own labels, and not prose without a real keyword. But a clean pre-merge read of
`closingIssuesReferences` is **not** the whole guarantee — see the squash-composition bullet
below.

Consequences, each of which has bitten at least once:

- **An absorbed issue you don't list gets neither close path.** It stays open with stale
  `sandcastle` / `in-progress` labels. This is #948 Mode 2: PR #900 shipped #845, #846, #847,
  #859 and #860 but listed only `Closes #851`, leaving five issues open.
- **Cross-repo closes are not automated.** `Closes owner/other#N` is not closed by
  `pr-merged.yml` — its `GITHUB_TOKEN` is repo-scoped, so it skips and warns. Close a foreign
  absorbed issue manually.
- **The list is frozen at merge time.** If the PR is already merged, editing its body is
  documentation-only and fires no close. Close the issues directly:
  `gh issue close #N --reason completed`.
- **Prose is invisible to the linkage — but only if it doesn't contain a real keyword+number
  pair.** The scan is a plain text match over the whole body, not code-span- or
  context-aware. A "shipped via #X" note closes nothing (no keyword). But writing the literal
  substring `Closes #N` *anywhere* — including inside backticks explaining why `Refs`, not
  `Closes`, was chosen — closes #N regardless of the surrounding sentence. Hit live on PR #1468:
  its Decisions section explained "`Refs #1274` instead of `Closes #1274`", and that spelled-out
  alternative alone put #1274 into `closingIssuesReferences` and closed it, despite the PR using
  `Refs #1274` as its actual link and #1274 having unmet AC groups. When a PR body needs to
  explain a Refs-vs-Closes decision, paraphrase the rejected keyword ("the closing keyword")
  instead of spelling out `Closes #N` literally.
- **A commit message is just as live a source as the body, and editing the body doesn't touch
  it.** `closingIssuesReferences` unions keyword hits from the body *and every commit on the
  branch*. Fixing the body while a commit message still contains the literal pair leaves the
  link intact — GitHub's own docs confirm editing the PR description cannot unlink something a
  commit-message keyword established. Hit live while opening PR #1472: its first commit's
  message explained the #1468 incident by quoting the same literal-substring mistake spelled
  out in full, now in a place a body edit can't reach.
- **`closingIssuesReferences` is sticky, not a pure live recomputation — rewriting history on
  the same PR does not reliably clear it.** The naive fix for the bullet above is "reword the
  bad commit and force-push". That is necessary but was confirmed *insufficient* on #1472: even
  after the body and the sole remaining commit message were both fully clean (verified via
  direct `gh api graphql` reads against the PR, bypassing any client-side cache), the field
  still listed #1274. The issue's own timeline (`timelineItems` via GraphQL) explains why: the
  moment a PR is first opened with a keyword+number pair present anywhere, GitHub records a
  `CrossReferencedEvent` with `willCloseTarget: true` against that PR number — and that
  historical record is never retracted by a later force-push, even though the new commit is
  independently reprocessed and correctly shows up as a non-closing reference. The association
  is anchored to the PR number's history, not to its current content. **The only working fix is
  to abandon that PR number** — branch from the now-clean commit, open a brand-new PR, and close
  the poisoned one as superseded without merging it. A PR number that ever carried the pattern,
  anywhere in its lifetime (body at any point, or any commit ever pushed to it even if later
  removed), cannot be trusted to report a clean `closingIssuesReferences` again.
- **Squash-merge composes the final commit from every commit on the branch — a clean pre-merge
  `closingIssuesReferences` does not protect against this.** GitHub's default squash-merge
  message concatenates each branch commit's own subject and body into one composed message, and
  *that* composed commit is what lands on the default branch and gets independently scanned there
  — a check that runs at merge time, later than and separate from the pre-merge
  `closingIssuesReferences` read. Hit live merging the PR that added this very file: its
  `closingIssuesReferences` was verified empty via GraphQL right up to merge, but an early commit
  on the branch — narrating, in ordinary past-tense prose, what the original #1468 mistake had
  done to #1274 — spelled out the keyword-plus-number pair while describing it, and that commit's
  body still made it into the composed squash message. Paraphrasing the *rejected* keyword when
  explaining a Refs-vs-closing-keyword choice (an earlier bullet above) is not sufficient on its
  own: **any** commit that will ever land on the default branch — including ones written and
  reviewed several commits earlier in the same branch, describing the incident itself rather than
  invoking it — needs the same scrutiny, checked against the full composed message a squash-merge
  will produce, not just the PR body or the newest commit in isolation.

## Superseded siblings

When you close a sibling PR as "superseded by #X", carry over **that sibling's own**
issue-closing keywords into #X's body. If the sibling listed `Closes #845, Closes #846`, add
those exact lines — **not** `Closes #<siblingPR-number>`, which resolves to a different object
entirely (the issue that happens to share that number).

## What counts as "absorbed"

An issue is absorbed when its **entire scope** ships in this PR — not when the PR merely
references it, touches the same file, or partially advances it. A partially-advanced issue
keeps its own PR later; listing it here closes it early and loses the remainder.
