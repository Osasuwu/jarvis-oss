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
doc picks. Both hold on reading the steps. The first is no longer a finding: pass 3 changed after
this run (below), and several options fitting one setup is now intended.

### Variance between runs

The two pass 1 runs covered the same examples but did not flag the same things:

- Only the seeded run caught the paraphrased quote.
- Only the clean run caught the unrun fresh-session check and the misattributed import cause.

Matching in pass 4 also varied on borderline cases:

- One run put pip's `RECORD` manifest out of scope, the other marked it missing.
- Both runs filed auto-discovered rules directories (`.claude/rules/`) under option 4. That call
  is loose.

A single run misses things another run would catch. Treat the report as a floor, not a proof.

## Pass 3 change — 2026-09-17

**What changed.** Pass 3 used to build setups that "land on an option", cite "the step that
decides", and report `option X via step K`. It now checks a section that rules options out with
checkable facts and names trade-offs among what is left: filters are facts, no filter wrongly
rules an option in or out, trade-offs and examples match the option sections, and there is a
pointer when nothing is left. The value test fails only when no realistic setup rules our option
out or makes it lose a named trade-off. More than one option fitting is stated as not a defect.

**Why.** On `docs/writing-into-user-owned-files.md` (#61, PR #62) the old wording drove six
review-and-fix rounds of ordered steps, tables and tie-breaks. Each round made the steps reach one
option and each review found new contradictions, because a real choice between options with
different costs does not reduce to one answer per setup.

**Evidence, unseeded.** Round seven ran the new checks as a prompt, on `8bc6712`. The reviewer
walked 16 setups and did not push toward one option. It found four real defects, all fixed in
`24d690a`:

- the placement check applied to one table row, though it held for three;
- nothing said what to do when every option is ruled out;
- the doc's own nvm example contradicted the row it was meant to pass;
- one option's cost left out that it can overwrite the person's values.

**Seeded run:** Run 2, below.

## Run 2 — pass 3 only, 2026-09-17

**Doc:** `docs/writing-into-user-owned-files.md` at `8a79a4d` (after #65), checked against itself.

**Setup:** three copies, each reviewed by fresh subagents that were given only the copy and the
pass 3 text. None was told errors were planted, and none read the repo or the web.

- **A, seeded:** seven errors planted in "How to choose" by the session that set up the run, one
  or more per check. Reviewed twice, to see variance.
- **B, clean:** the doc as merged.
- **C, value test:** one change, "Whatever the file, start from **option 7**", which makes our
  option apply to every setup.

What counts as a catch was written down before any result was read.

### Planted errors

| # | Planted error | Check | Run A1 | Run A2 |
|---|---|---|---|---|
| P1 | row 3 → "a marked block would not clutter the person's file" | judgement, not fact | caught | caught |
| P2 | row 5 → "the file is JSON" | rules out a buildable option | caught | caught |
| P3 | row 4 → "your content changes between versions" | leaves in one that can't be built | caught | caught |
| P4 | trade-off: "a broken include is reported" | contradicts option 4's cost | caught | caught |
| P5 | example: "conda 4, rustup 3" | contradicts options 3 and 4 | caught | caught |
| P6 | nothing-left sentence removed | no pointer when nothing is left | caught | caught |
| P7 | our choice: "by 5 elsewhere" | contradicts option 5 | caught | caught |
| V | copy C: "Whatever the file, start from option 7" | value test | `fails value test` | — |

**7 of 7 caught in both runs.** Copy C failed the value test, and copy B passed it.

### Findings on the clean doc

Run B reported ten findings. The session that set up the run checked each against the doc: eight
hold, two are wording calls, none is false. The seeded runs and run C, which share the unchanged
text, found the same defects and a few more. Those that hold:

- Seed-once writes only when the file is absent, but `applies_when` is a file that exists, so its
  row and the nothing-left fallback offer something that writes nothing. (A1, B, C)
- The split row drops "the person accepts editing there". (A1, B, C)
- Step 2, "is your tool the only one that edits it?", is out of scope by `applies_when_not`, and
  is a prediction, not a fact. (A1, B, C)
- Row 2 has no format condition, so a line edit stays in for JSON. (B, C)
- The fallback's "print" contradicts when to print, and "several one-line edits" brings back a
  ruled-out option 2. (B, C)
- "5 sets only your keys in any layout" overstates option 5's cost. (A1, A2, C)
- "Today it … appends plainly" is option 2 unguarded, not 3. (A1, B, C)
- Also, each seen in only one run: the program-slot variant touches the person's hook (B); the
  include line can be written with 5 (B); "managed by other means" is a judgement (C); "a base the
  person does not touch" cannot be checked (C); the placement check leaves out option 5 (C).

Round seven of #61 had already reported the split row and the plain append, and they were left
unfixed. The nothing-left fallback was round seven's own suggestion, and B and C now find it
flawed. The other defects are new.

### Variance between runs

- A1 and A2 caught the same seven plants, and agreed on none of the extra findings except
  "any layout".
- Five real defects on the clean text were seen in a single run only.

Every plant was a single edit that contradicts text elsewhere in the same doc. A defect that needs
outside knowledge, such as a filter that is wrong about a tool, is pass 1's job and was not planted
here.

## Limits seen

- Planted errors were the kind the planter thought of. Subtler errors, such as a true quote used
  to support a false conclusion, were not planted.
- One doc, one run. Too few for a catch rate. The table shows the skill can catch each kind, not
  how often.
- The pass 1 reviewers downloaded sources into the repo root before moving them to the
  scratchpad. Reviewers need an explicit working directory for downloads.
