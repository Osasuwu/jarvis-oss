# review-doc calibration

The skill counts only once it has shown what it catches. This file records that for the skill as
it is on `main`. Re-calibrate after changing any pass: `SKILL.md`, the workflow, the pinned action
and the resolved model are hashed into the drift key, and a change to any of them turns the review
check red until this file carries the new key.

This file is outside the hashed path. Writing it does not change the key.

## Calibration 1 — escaped-defect corpus, k = 3 (#106)

### Method

Committed and pushed on 2026-09-22, before the first run was dispatched. Nothing in this section
is changed after a result is read. What the results make look wrong goes in the PR, not here.

**What is measured.** Whether the review-doc skill, run by `.github/workflows/doc-review.yml` as
it is on `main` at `67caf77`, finds again the defects in
[`calibration/corpus.md`](calibration/corpus.md): defects a review round missed and the next round
found. Entries are counted under [`calibration/RULES.md`](calibration/RULES.md). The corpus has 34
counted entries and 1 held-out entry. The held-out entry is not scored into the counts; it is
reported after them with its own number.

**Snapshots.** The entries sit at 9 commits: `3d896f9`, `4074b0b`, `59a27d9`, `79bc90c`,
`8067d67`, `9dbed9e`, `a6d0c01`, `af950ea`, `f677a54`. For each commit a branch `calib/<sha>` is
cut from `main` at `67caf77`. On it, the doc set under review is replaced with its content at that
commit, and nothing else changes except as below:

- the doc set is each `docs/` file that holds a corpus entry at that commit, plus every file under
  `examples/` or `resources/` that the doc links to or that names the doc in `pairs_with`: the
  review scope `SKILL.md` gives a doc. A round N review saw those files at round N.
- `calibration/corpus.md` is removed from the snapshot. It is the answer key: it quotes every
  defect, and a reviewer that greps the repo for a claim would find it. It is not in the drift
  key. `SKILL.md`, `RULES.md`, the workflow and the action stay byte-identical to `main`, so the
  key the runs compute is the key of `main`.

**How entries outside `docs/` are reviewed.** The workflow's `files` input takes `docs/*.md`
only. Round N reviewed the resource and example entries as the in-scope files of their paired doc,
and so do these runs: the paired doc is in `files`, and its resource or example is in the snapshot
at the same commit. An entry whose file is not in any reviewed doc's scope is counted as not
measurable, on its own line. The workflow is not changed to force a file in.

**Runs.** Each branch gets k = 3 `workflow_dispatch` runs, `files` set to the branch's corpus docs:
27 runs. Runs 1, 2 and 3 of a branch are its first three reviewable runs, in dispatch order. A run
that fails as unreviewable is re-run and the failure recorded; it is never dropped silently. If
failures are systemic, the calibration stops and reports instead. All 27 runs must resolve the same
model ID, or the calibration stops. The artifact of each run (`report.md`, `findings.json`, and
`comment.md`) is what is scored.

**What counts as caught.** An entry is caught in a run when that run's `findings.json` and report
name the same defect: the same claim, and the same thing wrong with it.

- The line number may differ.
- A `blocking` entry reported with the `follow-up` label counts as missed. The table shows those in
  a separate column, "caught, wrong label". The label is the one the verdict step reads: every
  mismatch (`M`) finding is `blocking`, as `scripts/doc_review.py` enforces.
- A finding on the same claim that names a different thing wrong is not a catch.
- An `unverifiable` finding on the entry's claim is scored by
  [RULES.md's `unverifiable` section](calibration/RULES.md#unverifiable): caught if it is
  `blocking`, missed if it is `follow-up`.
- One finding may catch more than one entry, if it names each one's thing wrong.
- A catch whose report gives the corpus, or the round N+1 comment that reported the defect, as its
  evidence is still scored by the rule, and is listed as contaminated.
- Scoring is done in a fresh context, against this rule only. Every borderline call is listed in
  the PR with its reasoning. The scoring worksheet (entry × run → verdict, the matching finding ID
  or "none", and a one-line reason) goes in the PR.

**What is recorded.** Per class, for each of runs 1–3: caught, missed and n, the "caught, wrong
label" count, and the spread between runs. Rows with n = 0 or caught = 0 stay in the table. Then the
held-out entries, the not-measurable entries, links to all 27 runs, the drift key, the date and
the `main` commit. There is no threshold and no pass mark.

**What the figures are.** A floor on same-model agreement: every corpus entry was found by the same
model that missed it in the round before, so the corpus holds only defects this model can find.
A defect the model misses every time is not in it. The figures are not a recall figure.

### Results

Runs dispatched and finished on 2026-09-22, against `main` at `67caf77`. All 27 runs were
reviewable on the first attempt: none was re-run. Every run resolved the model `claude-opus-5`,
and every run computed the drift key below. Scoring was done in fresh contexts, one per
snapshot, against the Method only; the worksheet and every borderline call are in the PR.

Each cell is caught / missed / n. "Missed" includes "caught, wrong label", which has its own
column.

| Class | Run 1 | Run 2 | Run 3 | Caught, wrong label (runs 1 / 2 / 3) | Spread of caught |
|---|---|---|---|---|---|
| status | 0 / 1 / 1 | 0 / 1 / 1 | 0 / 1 / 1 | 0 / 0 / 0 | 0 |
| quote | 0 / 5 / 5 | 0 / 5 / 5 | 2 / 3 / 5 | 0 / 0 / 0 | 2 |
| plan | 0 / 2 / 2 | 0 / 2 / 2 | 0 / 2 / 2 | 0 / 0 / 0 | 0 |
| fact | 2 / 18 / 20 | 1 / 19 / 20 | 1 / 19 / 20 | 1 / 0 / 0 | 1 |
| dead-end | 0 / 0 / 0 | 0 / 0 / 0 | 0 / 0 / 0 | 0 / 0 / 0 | 0 |
| missing-option | 0 / 0 / 0 | 0 / 0 / 0 | 0 / 0 / 0 | 0 / 0 / 0 | 0 |
| how-to-choose | 0 / 6 / 6 | 1 / 5 / 6 | 2 / 4 / 6 | 0 / 0 / 0 | 2 |
| other | 0 / 0 / 0 | 0 / 0 / 0 | 0 / 0 / 0 | 0 / 0 / 0 | 0 |
| **all** | 2 / 32 / 34 | 2 / 32 / 34 | 5 / 29 / 34 | 1 / 0 / 0 | 3 |

Six of the 34 entries were caught in at least one run (`62-go`, `62-ansible`, `62-today`,
`62-systemd`, `75-mkdocs`, `75-gs`); one, `62-today`, in all three. No catch cited the corpus
or the round N+1 comment. The table above is the pre-registered figure: all 2 / 34, 2 / 34,
5 / 34.

**Held out.** 1 entry, `75-ghd` (quote, `blocking`): missed in runs 1, 2 and 3. Not in the
counts above.

**Post-hoc audit.** Everything in this block was found after scoring. It does not change the
table; it says how far the table can be trusted.

- **The snapshots were not the repo as it was.** Each `calib/<sha>` branch is `main` at
  `67caf77` with only the doc set replaced, so every other file is at `main`'s state:
  `.gitleaks.toml`, examples and resources added later, and
  `.agents/skills/jarvis-setup/SKILL.md`, which is at post-#69/#70 `main`, not at `8067d67`.
- **Not measurable: `81-gitleaks`, `62-tried`.** `81-gitleaks` is a claim that `.gitleaks.toml`
  exists, and `main` added it in `67caf77`, so in the snapshot the claim is true. `62-tried` is
  a claim with no trace in the repo, and `examples/git-include-missing-target-silent.md`
  (added in `9803c6c`) is that trace; the runs found it.
- **Contaminated: `62-today`.** It was caught in all three runs, but each run's M1 cites lines
  of `.agents/skills/jarvis-setup/SKILL.md` that exist only in `main`'s version of that file.
- **Borderline, leans missed: `75-gs` run 3.** Its H10 is about the MkDocs example at `:281`;
  the entry's claim is row 3 at `:247`.
- **Coverage gap: `81-heredoc`.** `examples/heredoc-stripping-boundary-bug.md` was not reviewed
  in any of the three `af950ea` runs. It is missed under the Method, but it is a coverage
  failure, not a judgement miss.

Corrected counts, both post-hoc:

- **Strict**, dropping `81-gitleaks`, `62-tried`, `62-today` and `75-gs` run 3: 1 / 31, 1 / 31,
  3 / 31.
- **Lenient**, dropping only `81-gitleaks` and `62-tried`: 2 / 32, 2 / 32, 5 / 32.

**Runs.**

| Snapshot | Docs reviewed | Run 1 | Run 2 | Run 3 |
|---|---|---|---|---|
| `calib/3d896f9` | `docs/doc-structure-gate.md`, `docs/private-literal-scrub.md`, `docs/publishing-discipline.md` | [35736148530](https://github.com/Osasuwu/jarvis-oss/actions/runs/35736148530) | [35736250894](https://github.com/Osasuwu/jarvis-oss/actions/runs/35736250894) | [35736367646](https://github.com/Osasuwu/jarvis-oss/actions/runs/35736367646) |
| `calib/4074b0b` | `docs/writing-into-user-owned-files.md` | [35736162076](https://github.com/Osasuwu/jarvis-oss/actions/runs/35736162076) | [35736265300](https://github.com/Osasuwu/jarvis-oss/actions/runs/35736265300) | [35736381347](https://github.com/Osasuwu/jarvis-oss/actions/runs/35736381347) |
| `calib/59a27d9` | `docs/writing-into-user-owned-files.md` | [35736169824](https://github.com/Osasuwu/jarvis-oss/actions/runs/35736169824) | [35736272869](https://github.com/Osasuwu/jarvis-oss/actions/runs/35736272869) | [35736395640](https://github.com/Osasuwu/jarvis-oss/actions/runs/35736395640) |
| `calib/79bc90c` | `docs/agent-safety-hooks.md` | [35736183371](https://github.com/Osasuwu/jarvis-oss/actions/runs/35736183371) | [35736286349](https://github.com/Osasuwu/jarvis-oss/actions/runs/35736286349) | [35736403886](https://github.com/Osasuwu/jarvis-oss/actions/runs/35736403886) |
| `calib/8067d67` | `docs/writing-into-user-owned-files.md` | [35736198111](https://github.com/Osasuwu/jarvis-oss/actions/runs/35736198111) | [35736301523](https://github.com/Osasuwu/jarvis-oss/actions/runs/35736301523) | [35736417592](https://github.com/Osasuwu/jarvis-oss/actions/runs/35736417592) |
| `calib/9dbed9e` | `docs/writing-into-user-owned-files.md` | [35736207257](https://github.com/Osasuwu/jarvis-oss/actions/runs/35736207257) | [35736315402](https://github.com/Osasuwu/jarvis-oss/actions/runs/35736315402) | [35736431149](https://github.com/Osasuwu/jarvis-oss/actions/runs/35736431149) |
| `calib/a6d0c01` | `docs/writing-into-user-owned-files.md` | [35736215411](https://github.com/Osasuwu/jarvis-oss/actions/runs/35736215411) | [35736329506](https://github.com/Osasuwu/jarvis-oss/actions/runs/35736329506) | [35736439821](https://github.com/Osasuwu/jarvis-oss/actions/runs/35736439821) |
| `calib/af950ea` | `docs/agent-safety-hooks.md` | [35736228290](https://github.com/Osasuwu/jarvis-oss/actions/runs/35736228290) | [35736344055](https://github.com/Osasuwu/jarvis-oss/actions/runs/35736344055) | [35736454778](https://github.com/Osasuwu/jarvis-oss/actions/runs/35736454778) |
| `calib/f677a54` | `docs/writing-into-user-owned-files.md` | [35736242385](https://github.com/Osasuwu/jarvis-oss/actions/runs/35736242385) | [35736352452](https://github.com/Osasuwu/jarvis-oss/actions/runs/35736352452) | [35736463954](https://github.com/Osasuwu/jarvis-oss/actions/runs/35736463954) |

**What these figures are.** A floor on same-model agreement, as the Method says: not a recall
figure. They carry no threshold and no pass mark.

**Drift key**, of the workflow, the action pin, the model and `SKILL.md` on `main` at `67caf77`;
the key every one of the 27 runs computed:

drift-key: ffc526393a0108fe609ed6c8771c7b4a03c3f2e48b593715e69d318034fc90d2 (model: claude-opus-5)

**Drift keys.** Every key the line above has held, newest first. The line above is the only one
`scripts/doc_review.py` reads; this table is the history the plan below promises: a calibration-2
candidate replaces the line in its own PR and fills in the old row's `Until` (#145). A row with
`Until` set matches no run.

| Key | Model | Inputs on `main` at | Since | Until |
|---|---|---|---|---|
| `ffc526393a0108fe609ed6c8771c7b4a03c3f2e48b593715e69d318034fc90d2` | `claude-opus-5` | `67caf77` | 2026-09-22, #137 | — |

## Calibration 2 — plan (#143)

Committed on 2026-09-23, before any candidate run is dispatched. Everything below is fixed now;
after the runs, this section is edited only to append results, as `### Records` says. The decision
is recorded in [`docs/adr/0001-review-doc-calibration-2.md`](../../../docs/adr/0001-review-doc-calibration-2.md).

**Why.** Calibration 1 measured 1 / 31, 1 / 31, 3 / 31 per run on the strict set. A reviewer at
that level does not protect the reader from the writer's mistakes, and the human reads the
reviewer's report before the doc. Calibration 2 changes the procedure and measures again, on
entries the changed procedure was never tuned against.

### Split

Every corpus entry carries `split`, documented in
[`calibration/corpus.md`](calibration/corpus.md). The split is by PR lineage: a dev doc and a
test doc never share a file, a `pairs_with` target or a link, and
`tests/test_calibration_corpus.py` proves that at every dev commit with `git show`.

| Split | Lineage | Entries | Snapshots | Ceiling |
|---|---|---|---|---|
| dev | PR #62 | 15, all `blocking` | 6: `8067d67`, `59a27d9`, `4074b0b`, `f677a54`, `a6d0c01`, `9dbed9e` | 14 / 15: `62-npm` needs the command run |
| test | PR #75, #81 | 16: 10 `blocking`, 6 `how-to-choose` follow-ups | 3: `3d896f9`, `79bc90c`, `af950ea` | 9 / 10: `81-heredoc` needs the command run |
| held-out | PR #75 | 1: `75-ghd` | reported, not scored | — |
| excluded | — | 3: `81-gitleaks`, `62-tried`, `62-today` | none | — |

The dev runs tune the procedure and may be read as often as needed. The test runs are scored at
most twice for procedure candidates and once more only for a model change; after that the test
split is spent and a new one is cut before any further scoring. An entry that #138's
re-measurement shows contaminated moves to `excluded`; nothing else moves between splits. New
escapes go to dev and test alternately, in filing order, unless lineage forces the side.

### Target and gain rule

- **Target.** The median over 3 test runs of per-run catch on `blocking` test entries is at least
  50 %: 5 / 10. The union over the 3 runs is reported alongside, never in place of the median.
- **Dev gain rule.** A candidate goes to the test split only if its mean per-run catch on the dev
  split is at least the calibration-1 mean on the same 15 entries plus 3: 1.0 + 3 = 4.0 / 15.
  Below that, the candidate is not scored on test, and the A–E diagnostic below decides the next
  candidate.
- **k = 3** on both splits: 18 dev runs (6 snapshots × 3) and 9 test runs (3 snapshots × 3).
- **Model.** The family alias `claude-opus` the workflow already passes. A resolved model ID that
  differs between runs of one campaign stops the campaign, as in calibration 1. A cheaper model is
  a separate decision, taken only after a result on this one.

### Candidates, in order

1. **Candidate 1 (#146).** Per claim: a restatement, the load-bearing excerpt verbatim, and an
   equivalence verdict from a closed list, with the reasoning before the verdict. Chunked
   subagents of about 150 countable lines with tiling manifests that must cover every in-scope
   file, so a report that skips a file is `unreviewable` rather than a pass. The verdict step runs
   `if: always()`.
2. **Candidate 2 (#147).** An adversarial per-row pass, opened only if candidate 1 misses the dev
   gain rule and the diagnostic points at judgement, not coverage.

**A–E miss diagnostic.** Every dev miss is put in one cell: A, the claim's lines were never
examined (no manifest covers them); B, examined and judged correct; C, examined and marked
`unverifiable`; D, a finding on the same lines names a different problem; E, needs execution or a
sandbox. Mostly A: the coverage change did not work, and candidate 1 is reopened. Mostly B or D:
candidate 2. E entries stay outside every candidate until an execution lever exists.

### Runs

- **Precondition.** #138 is merged: every `calib/<sha>` branch is rebuilt from the full tree of
  its commit, not `main` with the doc set swapped in. No run of calibration 2, dev or test, is
  dispatched on the calibration-1 overlay branches.
- **Unreviewable runs** are re-dispatched until a snapshot has 3 reviewable runs, at most 2 extra
  dispatches per snapshot; every failure is recorded in the runs table. A snapshot that cannot
  reach 3 stops the campaign.
- **Dry run.** Before the dev campaign, one run on `calib/af950ea` with the candidate sets
  `--max-turns` and the job timeout: the measured turns and minutes × 1.5, rounded up, written
  here when measured. The same run's execution file is read for per-model usage and subagent tool
  use, to confirm how subagent cost is folded into the run's cost.
- **Re-dispatch, drift.** The candidate's `SKILL.md` and workflow edits produce a new drift key.
  The single `drift-key:` line above is replaced in the same PR, the new key is the first row of
  the **Drift keys** table under it, and the old key's row gets its `Until`. `scripts/doc_review.py`
  and this file are outside the key, so a change to either alone keeps it: #145 changed only
  those and the key stayed `ffc5263…`. If the family alias resolves to a new model ID, the key
  changes with it; the campaign restarts from the dry run and the old runs are kept as history.

### Scoring

- **Caught**, as calibration 1's Method defines it, extended for sub-claims: a finding on one of
  the entry's sub-claims that names the entry's thing wrong is a catch; a finding on a sub-claim
  that names a different thing wrong is not. A `blocking` entry reported as `follow-up` is missed
  and shown in the "caught, wrong label" column.
- Scoring is done in a fresh context against this rule only, with the worksheet (entry × run →
  verdict, finding ID or none, one-line reason) in the PR. Test scorings are also written as one
  line each into the sign-off ledger `docs/SIGNOFF.md`, dated, with the run links, so a scoring
  cannot be quietly redone.
- **Budget.** At most 2 test scorings for procedure candidates and 1 for a model change. A third
  procedure candidate needs a new test split.

### Cost

Cost is recorded per run as an estimated share of the 5-hour usage limit of the subscription the
runs bill to: Claude Max ×20 as of 2026-09-23. The numerator is the cost in USD the verdict step
reads from the action's execution file and writes into the report and the PR comment, beside the
wall-clock duration and the turn count (#145); a run whose execution file lacks a value carries
`unknown` there and is still a run. The share is estimated because the limit is not published in
USD; its denominator is measured per campaign:

- **Readings.** Before the first dispatch and after the last verdict of a campaign, read the
  5-hour usage percentage from the usage UI of the account the runs bill to, and record both
  readings under **Denominator readings** below, each with date and time. The pair is valid only
  if nothing else billed to the subscription between the readings and the 5-hour window did not
  reset between them; otherwise the pair is recorded as void, the campaign's shares stay
  `pending`, and the next campaign reads again.
- **Denominator** = the sum of the campaign's run costs in USD ÷ (after − before), in USD per
  100 % of the limit. Each run's share = its cost ÷ the denominator of its campaign, rounded to
  0.1 %. A run whose cost is `unknown` has share `unknown`; a run of a campaign without a valid
  pair has share `pending`, and its USD column stays the record.

The caps are checked by hand on the runs table under **Records**, not in the workflow:

- A doc review whose median run is over 10 % of the limit is rejected as a candidate, whatever
  its catch.
- One dev iteration (18 runs plus the dry run) may not exceed one 5-hour limit.
- A cheaper model is not tried until a candidate has a result on this model.

**Denominator readings.** None yet. Format: `<campaign>: <before %> at <date time> → <after %> at
<date time>; denominator <USD> per 100 %` or `void: <reason>`.

### Records

After the runs, appended here and nowhere else in this section: the rows of the runs table below,
the per-class table for dev and test, the A–E diagnostic for every dev miss, the held-out result,
and the date and `main` commit. The README floor is updated to the test median with its n and
date, or left at the calibration-1 floor if the target is not met, and says which.

**Runs.** One row per dispatch, the dry run and every unreviewable run included, copied from the
run's verdict comment; the share follows the **Cost** procedure above.

| Campaign | Split | Snapshot | Run | Result | Cost (USD) | Duration | Turns | cost (share of limit, est.) |
|---|---|---|---|---|---|---|---|---|

**Click-audit.** The human click-audit stays on every doc with a `blocking`-class claim while the
target is unmet, and after it, until a later calibration says otherwise. It is the draw
[`calibration/RULES.md`](calibration/RULES.md) defines: k = 5 claims, or all if there are fewer,
drawn by `draw_click_audit.py` seeded with the PR head SHA over every quotation, every `tried` or
`sourced` status and every other source-linking paragraph, whatever the claim's class.

## History

Everything below was measured under the old scheme: seeded errors planted in one doc, and runs by
hand, before the corpus, the drift key and the CI workflow existed. Every run below calibrated
`SKILL.md` as it was before #102. That change took the `blocking` and `unverifiable` labels from
[`calibration/RULES.md`](calibration/RULES.md), put pass 3's reasoning before its verdict, named
the four reader types pass 3 walks, and added a fix-induced line to the report. None of these
results describes the current skill; Calibration 1 above does.

### Run 1 — 2026-09-17

**Doc:** `docs/writing-into-user-owned-files.md` at `c1325d3` (merged in #58), with its three
examples and one resource.

**Setup:** two copies of the doc set.

- **Seeded:** seven errors planted by the session that set up the run. No reviewer was told about
  them.
- **Clean:** the doc as published.

Passes 1, 3 and 4 ran once per copy. Pass 2 is blind, so it ran once and both copies' pass 4
used its result. That makes seven fresh subagents in total, none of which had written the doc.

#### Planted errors

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

#### False positives on the clean doc

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

#### Variance between runs

The two pass 1 runs covered the same examples but did not flag the same things:

- Only the seeded run caught the paraphrased quote.
- Only the clean run caught the unrun fresh-session check and the misattributed import cause.

Matching in pass 4 also varied on borderline cases:

- One run put pip's `RECORD` manifest out of scope, the other marked it missing.
- Both runs filed auto-discovered rules directories (`.claude/rules/`) under option 4. That call
  is loose.

A single run misses things another run would catch. Treat the report as a floor, not a proof.

### Pass 3 change — 2026-09-17

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

### Run 2 — pass 3 only, 2026-09-17

**Doc:** `docs/writing-into-user-owned-files.md` at `8a79a4d` (after #65), checked against itself.

**Setup:** three copies, each reviewed by fresh subagents that were given only the copy and the
pass 3 text. None was told errors were planted, and none read the repo or the web.

- **A, seeded:** seven errors planted in "How to choose" by the session that set up the run, one
  or more per check. Reviewed twice, to see variance.
- **B, clean:** the doc as merged.
- **C, value test:** one change, "Whatever the file, start from **option 7**", which makes our
  option apply to every setup.

What counts as a catch was written down before any result was read.

#### Planted errors

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

#### Findings on the clean doc

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

#### Variance between runs

- A1 and A2 caught the same seven plants, and agreed on none of the extra findings except
  "any layout".
- Five real defects on the clean text were seen in a single run only.

Every plant was a single edit that contradicts text elsewhere in the same doc. A defect that needs
outside knowledge, such as a filter that is wrong about a tool, is pass 1's job and was not planted
here.

### Pass 3 severity and delta pass — 2026-09-18

**What changed.** Pass 3 marks each finding `blocking` or `follow-up`. A finding is `blocking` only
when the reviewer names a setup and what goes wrong for it, or quotes both sides of a
contradiction that cannot both be true; otherwise it is `follow-up`. The writer must fix or answer
every `blocking` one; a `follow-up` is fixed or filed. A fix commit now gets a delta pass: one
fresh reviewer, given the earlier report and the diff, says which findings are fixed or answered,
checks what the fix added, and searches the repo for mentions the fix left disagreeing.

**Why.** PR #75 and PR #81 each drew 10–15 findings per round, all weighted the same, and neither
converged: every fix round was reviewed ad hoc, and two rounds of #75 went on defects the fixes
had brought in. Most of those findings were wording a signer can live with.

### Run 3 — pass 3 severity and the delta pass, 2026-09-18

**Doc:** `docs/writing-into-user-owned-files.md` at `7e7ee20`.

**Setup:** fresh subagents, none told that errors were planted. The session that set up the run
planted the errors and wrote down what counts as a catch, and as a severity miss, before reading
any result.

- **A, seeded, pass 3:** five edits in "How to choose", three meant `blocking` and two meant
  `follow-up`. Reviewed twice, given only the copy and the new pass 3 text.
- **B, clean, pass 3:** the doc as merged.
- **C, delta:** a git repo with the seeded doc set as the reviewed commit, and a fix commit on top
  of it. The reviewer got an earlier report of five findings, the diff, the repo to read and
  search, and the web. Run twice: C1 with a draft of the delta pass, C2 with the skill as of the
  PR's first review fixes.

#### Pass 3 plants

| # | Planted error | Meant | Run A1 | Run A2 |
|---|---|---|---|---|
| B1 | row 3 → "the format has `#` comments to use as markers" | blocking | blocking | blocking |
| B2 | trade-off: "4 never touches the person's file" | blocking | blocking | blocking |
| B3 | example: "A key in `package.json` passes 2 and 5" | blocking | blocking | blocking |
| F1 | trade-off: "6 costs a stored base and some upkeep" | follow-up | not reported | follow-up |
| F2 | row 6 → "keeping its base sensibly outside what the person edits" | follow-up | blocking | blocking |

**Scored as written down before the run:** the three `blocking` plants were caught and marked
`blocking` in both runs, 6 of 6. The `follow-up` plants scored 1 of 4: F1 was missed once, and
F2 was marked `blocking` both times, a severity miss by the pre-set rule.

On reading the results, F2's two misses look like planter error, not reviewer error. Both runs
named the same setup: copier keeps its answers file inside the repo, so "sensibly" can be
answered either way, and the answer changes whether 6 is left. That is the rule applied as
written. This reading came after the results, so it does not change the score.

Both runs also marked as `blocking` that our own choice's fallback, option 3, is now ruled out
for formats without `#`. That follows from B1, and holds. A2 marked one more as `blocking`: our
choice uses option 3, whose status is `sourced`. That is a status question for pass 1, not a
contradiction with option 3's section, so it should have been `follow-up`. Every other extra
finding was `follow-up`. The seeded doc passed the value test in both runs.

#### Clean doc

Run B reported ten findings: **2 `blocking`, 8 `follow-up`.** For comparison, Run 2's pass 3,
which had no severity, drew ten findings of one weight on an earlier commit (`8a79a4d`).

- Both `blocking` findings are judgement filters, each with a named setup: "Prose the person may
  already state their own way" decides whether option 7 applies to a linter's line in
  `AGENTS.md`, and "the person accepts editing" decides whether the split row is left for git's
  second config file. Both hold on reading the doc. They are tracked in #90.
- The eight `follow-up` findings are wording, or trade-offs named more loosely than an option's
  cost. None names a setup that goes wrong. None is false.

#### Delta plants

The fix commit fixed one finding correctly and left one unfixed. The other three fixes each
brought in an error: a wrong reason, a reworded quote, and our own choice changed from option 3
to 2 in the doc only.

| # | In the fix commit | Expected | C1 | C2 |
|---|---|---|---|---|
| — | finding 1 (row 3) fixed correctly | `fixed`, no new finding | `fixed`, no new finding | `fixed`, confirmed |
| D1 | finding 3 fixed with "a bare line can break YAML" | mismatch: `package.json` is JSON | caught | caught, npm docs fetched |
| D2 | finding 4 (option 6's cost) left as it was | `not fixed` | `not fixed` | `not fixed` |
| D3 | finding 5: our choice moved from option 3 to 2 in the doc only | leftovers | caught in the resource, the example and `jarvis-setup`'s SKILL.md | the same, and a test |
| D4 | finding 2 fixed with a git quote reworded to "as if it had been found at the end of the including file" | pass 1 mismatch | caught | caught, git-config fetched |

**Both runs caught all four plants, with 0 false positives.** In C2 all three leftover files, and
`tests/test_jarvis_setup_skill.py`, came from the whole-repo search the skill now asks for. C1
found the `jarvis-setup` file by going past its draft, which searched only the files in scope.
Both runs also reported the defects the changed choice made, which were not planted:

- row 2, "a line or two", rules our own choice out for a block of rules;
- "both carry an update and an uninstall step" contradicts option 2's "Substring: neither";
- option 7 says "With 3 or 4", but our choice now carries it by 2.

C2 scored finding 5 `fixed` and reported the damage as new findings, as the skill now says. C1,
which had no rule for this, called it `not fixed`. C2 also put the reversed design choice on its
"read these closely" list.

#### Variance between runs

- A1 and A2 gave the same severity to every plant both reported. A1 did not report F1; A2 did.
- Of the extra findings, the two runs agreed only on the one that follows from B1.
- C1 and C2 caught the same plants and the same three unplanted defects. C2 filed the leftover
  files as `mismatch` and option 7's line as a how-to-choose `follow-up`.

#### Limits of this run

- C1 ran on a draft: it had pass 1's text and the report format, but not pass 3's. C2 had the
  whole skill, before the second round of review fixes to the delta pass.
- The planter chose which edits were meant to be harmless, and one of the two arguably was not.
  Severity plants need a second reader before the run.
- Every run used the same doc as Runs 1 and 2, and the delta plants and fix are one commit pair.

### Limits seen

- Planted errors were the kind the planter thought of. Subtler errors, such as a true quote used
  to support a false conclusion, were not planted.
- One doc, one run. Too few for a catch rate. The table shows the skill can catch each kind, not
  how often.
- The pass 1 reviewers downloaded sources into the repo root before moving them to the
  scratchpad. Reviewers need an explicit working directory for downloads.
