# Sign-off ledger

Tracks which docs under `docs/` have been reviewed and by when, per D26 (`Osasuwu/jarvis`
`docs/decisions/2026-Q3.md`). Sign-off provenance can't be verified via commit author (agent
commits land under the operator's account), so it's tracked two ways that must agree:

1. A `signed_off: <date>` field in the doc's own frontmatter.
2. A matching entry here, in a commit **separate** from the one that last changed the doc body.

The structure gate (`tests/structure_gate.py`) enforces both: any doc with `signed_off` set must
have a matching entry here (`signoff_missing_entry`), and that entry must not have been added in
the same commit as the doc body (`signoff_same_commit`).

## Entry format

One line per signed-off doc, alphabetical by path:

```
- `<repo-relative path to doc>`: <signed_off date, YYYY-MM-DD>
```

The date must match the doc's own `signed_off` frontmatter value exactly.

## Entries

None. Every entry this ledger has ever carried was written by the agent that drafted the doc it
signed, in a PR that merged with zero reviews — see #41 and #53. They were removed rather than
left standing, because a ledger that records signatures nobody gave is worse than an empty one.
Entries return one at a time, each in its own PR, merged by the author after reading the body.
