# review-doc calibration

The skill counts only once it has shown what it catches and what it wrongly flags. This file
records that. Re-calibrate after changing any pass.

## Run 1 — 2026-09-17

**Doc:** `docs/writing-into-user-owned-files.md` at `c1325d3` (merged in #58), with its three
examples and one resource.

**Setup:** two copies of the doc set.

- **Seeded:** seven errors planted by the session that set up the run. No reviewer was told about
  them.
- **Clean:** the doc as published.

Passes 1, 3 and 4 ran once per copy. Pass 2 is blind, so it ran once and both copies' pass 4
used its result. That makes seven fresh subagents in total, none of which had written the doc.

### Planted errors

| # | Planted error | Kind | Caught by |
|---|---|---|---|
| E1 | git include quote changed to "inserted at the end of the including file" | altered quote | pass 1 (mismatch) |
| E2 | Claude Code import depth "four hops" → "five hops" | number | pass 1 (mismatch) |
| E3 | Ansible `state: absent` → `state: removed` | tool behaviour | pass 1 (mismatch) |
| E4 | nvm guard `'/nvm.sh'` → `'nvm.sh'` in a quoted code line | altered quote | pass 1 (mismatch) |
| E5 | option 5 status `sourced` → `tried`, no trace | status | pass 1 (mismatch, no trace) |
| E6 | `git config` exit status 5 → 3 | number | pass 1 (mismatch, and a local run) |
| E7 | a whole option removed (three-way merge: copier, cruft), with steps, table and resource renumbered to hide the gap | completeness | pass 2 found copier; pass 4 marked it `missing` |

**7 of 7 caught.**

### False positives on the clean doc

Every mismatch the clean run reported was checked against its source by the session that planted
the errors. All five were real:

- The rules file in `examples/semantic-delta-real-run.md` has 59 lines, not 57.
- "For months" was 4–9 days: the imports went in on 2026-07-29 and 2026-08-03, and the fix
  merged on 2026-08-07.
- The example says the fix was verified in a fresh session. The fixing PR says that check was
  not run.
- The doc names line position as why `@import` dropped and says the docs are silent. The memory
  docs use a mid-sentence import as their own example, and the fixing PR names a glued trailing
  comma as at least as likely.
- rustup writes `$HOME/.cargo/env` or an absolute path, never the literal `$CARGO_HOME`.

**0 false positive mismatches.** The two `unverifiable` items were fair: one claim cites no
source, and one run is recorded only in the example.

The seeded run's pass 1 also flagged all four of those real defects except the fresh-session one.
It added one more: a sentence in quotation marks that paraphrases its source.

Judgement spots and `missing` options are calls, not facts, so they cannot be false positives in
the same sense. On the clean doc, pass 3 and pass 4 found that the "how to choose" steps cannot
reach one table row, and that they send the doc's own case to a different option than the one the
doc picks. Both hold on reading the steps.

### Variance between runs

The two pass 1 runs covered the same examples but did not flag the same things:

- Only the seeded run caught the paraphrased quote.
- Only the clean run caught the unrun fresh-session check and the misattributed import cause.

Matching in pass 4 also varied on borderline cases:

- One run put pip's `RECORD` manifest out of scope, the other marked it missing.
- Both runs filed auto-discovered rules directories (`.claude/rules/`) under option 4. That call
  is loose.

A single run misses things another run would catch. Treat the report as a floor, not a proof.

## Limits seen

- Planted errors were the kind the planter thought of. Subtler errors, such as a true quote used
  to support a false conclusion, were not planted.
- One doc, one run. Too few for a catch rate. The table shows the skill can catch each kind, not
  how often.
- The pass 1 reviewers downloaded sources into the repo root before moving them to the
  scratchpad. Reviewers need an explicit working directory for downloads.
