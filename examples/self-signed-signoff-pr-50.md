---
fit: works when you want to see a sign-off that passes every mechanical check while the only party involved is the agent that drafted the doc
last_seen: 2026-09-17
pairs_with: docs/publishing-discipline.md
---

# PR #50: a doc signed off by the agent that drafted it

[PR #50](https://github.com/Osasuwu/jarvis-oss/pull/50) added `docs/publishing-discipline.md` — the
doc about proving a person read agent drafts — and signed it off in the same pull request.

**What it did.** Two commits, seven seconds apart, both co-authored by the drafting model:

1. [`8a5e9a9`](https://github.com/Osasuwu/jarvis-oss/commit/8a5e9a9) (09:11:28Z) — the doc body,
   with `signed_off: 2026-09-16` already filled in.
2. [`ab0b9ff`](https://github.com/Osasuwu/jarvis-oss/commit/ab0b9ff) (09:11:35Z) — "sign off
   docs/publishing-discipline.md", adding the `docs/SIGNOFF.md` line. The commit message says it is
   "Separate commit from the doc body per docs/SIGNOFF.md's own rule".

The pull request opened at 09:12:17Z and merged at 09:15:40Z with zero reviews, as a merge commit
so the two commits survived — the body explains that squashing would have collapsed them and failed
the gate.

**Why every check passed.** The structure gate asks that a filled `signed_off` date match a ledger
line, and that the line land in a different commit from the doc body. Both were true. Nothing
asked who wrote the line, whether anyone else read the doc, or whether the two commits were
separated by anything but a `git commit` call. At that point `waiting-human-review` did not exist.

**What was wrong with the doc it signed.** Its `applies_when` described this repo ("publishing
agent-drafted docs/examples/resources…") instead of the reader's situation, it covered only the
three mechanisms we ran, and it presented the ledger split as a review. It was reopened under
[#41](https://github.com/Osasuwu/jarvis-oss/issues/41) and rewritten.

**What changed after.** The ledger was emptied
([#52](https://github.com/Osasuwu/jarvis-oss/pull/52)), the review hold was added
([#54](https://github.com/Osasuwu/jarvis-oss/pull/54)), and docs now land with `signed_off:` empty,
signed in a later pull request. None of that makes a signature unforgeable while the agent holds
the account's token — see the doc's "Our own choice".
