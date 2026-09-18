---
fit: works when you hold a denylist as a CI secret, or any check whose input can be absent, and want to see what a missing input looks like from the outside — green, every time
last_seen: 2026-09-17
pairs_with: docs/private-literal-scrub.md, docs/agent-safety-hooks.md
---

# A literal scrub that reported clean with no literals

This repo added a personal-literal scrub on 2026-09-16 (pull request #34): a step in
[`gitleaks.yml`](../.github/workflows/gitleaks.yml) that reads a newline-separated list from the
`PERSONAL_LITERALS` repository secret and fails if any entry appears in the checkout. It merged
75 seconds after it was opened, with no review.

**What happened.** The secret was not created until 2026-09-17; earlier that day `gh secret list`
for the repository returned nothing. Every run of the workflow from its first, on #34, until the
secret was set — 33 of them — was green, and each run's log contains the line the script printed when it found
no hits:

```
Scrub clean — no personal literals found in the tree.
```

An unset secret expands to an empty string. The script split it into zero literals, looked for
each of the zero in every file, found none, and said so.

**Why nobody saw it.** Everything visible said the check worked: a required job, green, with a
log line saying "clean". Nothing in the output told a checked tree from an unchecked one. The
agent-safety-hooks guide cited the scrub as "tried" on this repo on the strength of those runs.

**What changed.** The script now refuses an empty list — "Nothing was checked, so this step
fails." — and a clean run prints how many literals it checked:

```
Scrub clean — checked N literal(s); none found in the tree.
```

The cost is real: with the secret unset, the gitleaks job fails on every pull request, and on
fork pull requests it will unless the fork edits the workflow, since forks get no secrets. The secret was set later on
2026-09-17, and a run after that logged `Scrub clean — checked 22 literal(s)`.

**What to take from it.**

- A check whose input can be missing must fail when it is, not pass. Test that case: run it with
  the input unset.
- A green check should say what it checked — a count, a list of files — so "checked nothing" is
  visible in the log.
- A check that ran is not a check shown to catch anything. The quickest proof is a planted hit with a canary — a meaningless string added to the list for that purpose, never
  a real entry, since the branch is public once pushed.

See [`private-literal-scrub.md`](../docs/private-literal-scrub.md), option 5.
