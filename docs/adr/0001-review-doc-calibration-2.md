# ADR-0001: Review-doc calibration 2 — pre-registered plan, lineage split, procedure candidates

Date: 2026-09-23. Status: accepted. Tracking: #143 (slices #144, #145, #146, #147; design notes
#142). Supersedes nothing.

## Context

Calibration 1 (#106, PR #137) measured the review-doc reviewer at 1 / 31, 1 / 31, 3 / 31 per run on
the strict set of escaped defects: defects a review round missed and the next round found. The
human reads the reviewer's report before the doc, so what the reviewer misses, the human misses.
The figures also came from snapshot branches that were `main` with the doc set swapped in, which
made three entries not measurable or contaminated (#138).

The design was pressure-tested in a grill session on 2026-09-23: 114 raw critique items, 23
decision clusters, all accepted with the recommendations recorded in the plan section of
`.agents/skills/review-doc/CALIBRATION.md`.

## Decision

1. **Pre-register the plan** in `CALIBRATION.md` before any run, and edit it afterwards only to
   append results.
2. **Split the corpus by PR lineage**: dev = PR #62 (15 entries, 6 snapshots), test = PR #75 and
   #81 (16 entries, 10 blocking, 3 snapshots), one held-out, three excluded. Lineage is the split
   because two docs of one PR share files, `pairs_with` targets and links; two lineages share
   none, and a test proves it at every dev commit.
3. **Target**: median of 3 test runs ≥ 50 % of blocking test entries (5 / 10). **Gain rule**: a
   candidate reaches the test split only with mean dev catch ≥ 4.0 / 15. Test is scored at most
   twice for procedure candidates and once more for a model change.
4. **Candidates in order**: (1) per-claim restatement, verbatim excerpt and closed equivalence
   verdict, with chunked subagents whose manifests must tile every in-scope file; (2) an
   adversarial per-row pass, only if candidate 1 misses the gain rule and the A–E miss diagnostic
   points at judgement rather than coverage.
5. **Cost is part of the record**: per run, as an estimated share of the 5-hour limit of the
   subscription (Claude Max ×20 as of 2026-09-23), with a 10 % cap per doc review and one limit
   per dev iteration.
6. **Precondition**: #138 rebuilds every snapshot branch from the full tree of its commit before
   any calibration-2 run.
7. **This directory** (`docs/adr/`) is excluded from doc-review and the structure gate through one
   directory rule shared by `scripts/doc_review.py` and `tests/structure_gate.py`, the same rule
   that excludes the sign-off ledger. It stays inside the quote checks.

## Alternatives rejected

- **Random split of entries.** Two entries of one doc would land on both sides, and a dev run
  would read the test doc. Rejected for leakage.
- **No split, score the whole corpus again.** Every procedure change would be tuned on the entries
  it is scored on; the figure would be a fit, not a measurement.
- **Union of runs as the target.** The union rewards variance between runs; the reader gets one
  run. The median is the target, the union is reported beside it.
- **Cheaper model first.** A model change on an uncalibrated procedure confounds the two. It is a
  separate decision after a result on the current model.
- **Deterministic checkers instead of a procedure change.** Verbatim-quote checking is #141 and
  runs regardless; a tried-trace checker and sandboxed execution wait behind the stop rule.
- **ADRs as reviewed docs.** A decision record is a statement of what was decided, not a
  reader-facing claim about the world; the reviewer's classes do not apply to it, and the
  structure gate's frontmatter is for docs a reader chooses by `applies_when`.

## Consequences

- Every slice of #143 edits the review skill, the workflow, the scripts or the tests, so every
  PR is a machinery PR and is held for the human by `machinery-guard` and
  `waiting-human-review`.
- The README states the calibration-1 floor until the test median meets the target.
- The test split is spent after two procedure scorings; a third candidate needs a new split cut
  from escapes filed after this date.
- The drift key changes with each candidate; the single `drift-key:` line in `CALIBRATION.md` is
  replaced in the same PR, and the old key is kept as history.

## References

- `.agents/skills/review-doc/CALIBRATION.md`, section "Calibration 2 — plan (#143)".
- `.agents/skills/review-doc/calibration/corpus.md`, the `split` field.
- `.agents/skills/review-doc/calibration/RULES.md`, the click-audit draw.
- Issues #143, #144, #145, #146, #147, #142, #138, #106.
