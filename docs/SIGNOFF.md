# Sign-off ledger

Tracks which docs under `docs/` have been reviewed and by when, per D26 (`Osasuwu/jarvis`
`docs/decisions/2026-Q3.md`). Sign-off provenance can't be verified via commit author (agent
commits land under the operator's account), so it's tracked two ways that must agree:

1. A `signed_off: <date>` field in the doc's own frontmatter.
2. A matching entry here, in a commit **separate** from the one that last changed the doc body.

The structure gate (`tests/structure_gate.py`) enforces both: any doc with `signed_off` set must
have a matching entry here (`signoff_missing_entry`), and that entry must not have been added in
the same commit as the doc body (`signoff_same_commit`).

## Drafted, not yet signed

`signed_off:` present but empty (`signed_off:` with nothing after the colon) is the intentional
"drafted, not yet signed" state — not a violation, and not the same as omitting the key. The gate
skips a doc with an empty `signed_off` entirely: no ledger entry is required until the value is
filled in. A doc lands with this empty value on its first PR; a later, separate PR fills in the
date and adds the matching ledger line below.

## Sign-off provenance (#53)

The two-commit rule above stops a single commit from claiming its own review, but a same-commit
check inside one PR is satisfied just as mechanically if the doc body and the ledger line land in
two different commits of that *same* PR — nothing here diffs against `main`, only against the
doc's own git history. The intended control against that is `waiting-human-review` (see
`.github/workflows/waiting-human-review.yml`): it holds any PR, sign-off or not, until a human has
looked at it. **It holds nothing until it is a required status check on `main`** — while it is
only a status check, a red run does not block the merge button (#55 merged 57 seconds after the
hold label went on, with the check red). Given that hold, the practice — not a mechanical gate — is
to re-sign a doc through a PR that touches only the `docs/SIGNOFF.md` line, with no other change
to that doc's body, merged by the author after actually reading it.

## What a signature covers (#59)

A signature covers two different checks, and the entry records who did each.

- **Reading.** The signer read the doc as its reader would: does it make sense, is it useful, does
  it match what they have tried themselves. The signer always does this. It is not delegated.
- **Facts and completeness.** Every quote, number and tool behaviour matches its source, and no
  approach a reader could need is missing. Either the signer checked this (`facts: human`), or a
  [`review-doc`](../.agents/skills/review-doc/SKILL.md) report did (`facts: <report URL>`).
  With a report, the signer reads its "read these closely" places and its mismatches, not every
  line of the doc, and skims the rest.

A report counts only if a context that did not write the doc produced it, against the commit
being signed, and every mismatch and missing option in it was fixed or answered.

## Entry format

One line per signed-off doc, alphabetical by path:

```
- `<repo-relative path to doc>`: <signed_off date, YYYY-MM-DD>; facts: <human | report URL>
```

The date must match the doc's own `signed_off` frontmatter value exactly. An entry without the
`facts:` part fails the structure gate (`signoff_missing_facts`).

## Entries

None. Every entry this ledger has ever carried was written by the agent that drafted the doc it
signed, in a PR that merged with zero reviews — see #41 and #53. They were removed rather than
left standing, because a ledger that records signatures nobody gave is worse than an empty one.
Entries return one at a time, each in its own PR, merged by the author after reading the body.
