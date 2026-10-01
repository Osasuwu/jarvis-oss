# ADR-0002: A plumbing-only change to a doc is reviewed in full

Date: 2026-10-01. Status: accepted. Tracking: #194 (follow-up to #167). Supersedes nothing.

## Context

#167 removed `resources/` and moved its install notes into option sections and `pairs_with`
links. The change to each of four docs was plumbing, with no new claim. doc-review still read
each doc in full, because the first review of a doc on a pull request is always full (`scripts/doc_review.py`; later pushes review only the delta, up to
`DELTA_MAX_CHANGED = 0.30` of the lines). A full review found 15 blocking findings in
`docs/agent-safety-hooks.md`, `docs/doc-structure-gate.md`, `docs/publishing-discipline.md` and
`docs/writing-into-user-owned-files.md` that the plumbing did not cause: they were already in
the docs on `main`. #194 cleared them by hand.

The question #194 asks: should a plumbing-only change be reviewed in full, or only on its diff?

## Decision

1. **A touched doc is reviewed in full on its first touch per pull request**, plumbing-only
   changes included. No carve-out by kind of change.
2. **Pre-existing findings block like new ones.** A doc that is touched is a doc that is
   re-read; whoever touches it clears what the review finds, in the same pull request.
3. **Backstop for docs nobody touches**: `workflow_dispatch` reviews the given files in full and
   posts no comment. It should be run on the docs after a change to the review procedure, so that
   the next plumbing PR does not meet the backlog.

## Alternatives rejected

- **Review only the diff for plumbing-only changes.** Needs a classifier for "plumbing-only"
  that is itself a judgement, and a wrong call hides a real defect behind a link edit. A link
  edit can also change the claim it carries: one of the 15 findings was a quote cited to the
  wrong page.
- **Baseline subtraction** (block only on findings absent from `main`). Needs a review of `main`
  per doc per run, which doubles the cost, and findings are prose judgements with no stable
  identity to subtract. A line moved by the plumbing would show as new.
- **Waive pre-existing findings with a label.** The waiver outlives the PR; the backlog becomes
  invisible, which is the failure the gate exists to prevent.

## Consequences

- A plumbing PR touching N docs pays for N full reviews (about $20 each at the time of writing;
  a delta is about $4–5) and may need one round of fixes. Batch the fixes into one push.
- Fixes to review findings are new text and get reviewed on the next push as a delta; expect
  a second round.
- A procedure change to the reviewer should be followed by a dispatch run on every doc in scope.

## References

- #167 (the plumbing change), #194 (this decision and the 15 findings).
- `scripts/doc_review.py` (`DELTA_MAX_CHANGED`, the full-on-first-review rule).
- [ADR-0001](0001-review-doc-calibration-2.md), which also keeps this directory out of
  doc-review and the structure gate.
