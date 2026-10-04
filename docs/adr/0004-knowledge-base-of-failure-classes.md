# ADR-0004: The repo becomes a knowledge base of failure classes; the doc contract, entry skill and calibrated reviewer go

Date: 2026-10-04. Status: accepted for the transition; the class-doc contract details listed
under *Open* are locked before the first class doc and recorded as an amendment here.
Tracking: milestone "Knowledge base — first release". Supersedes ADR-0003 and ADR-0001.
Amends ADR-0002.

## Context

Until now the repo shipped practice docs under the contract in ADR-0003. An entry skill
(`jarvis-setup`) routed a reader through the docs and configured the reader's repo. Docs were
typed by `kind`, split into numbered options that each carried a check, and stamped with
check dates. An LLM reviewer (`review-doc`) read every doc PR. A calibration programme
(ADR-0001) measured it: it caught 1, 1 and 3 of 31 known escaped defects per run.

The goal of the repo has changed. It is now an open knowledge base about developing software
with agents. It covers the development cycle only, from task to code to review to merge, and
answers three questions:

1. What do agents give at each stage?
2. Where is a human still needed?
3. How do agents break, by mechanism?

Readers decide for themselves. The reader's own coding agent reads the base and compares it
with the reader's repo. The primary audience is newcomers. The foundation is that the human
sets the task and handles exceptions, and the aim is fewer escalations. Reliability is
described per stage, with no single headline number.

The content comes from one method: primary-source incidents, grouped into failure classes by
mechanism, with protections researched per class. A research pass covered 232 incidents from
the maintainer's repos, 41 public external incidents and about 150 external sources. It
produced 15 classes and a set of candidates. The research artefacts are kept privately.

The old contract was built for routing by a skill, which the new product does not have. The
reviewer's calibration machinery had become the largest maintained part of the repo, and it
measured a catch rate too low to rely on.

The design was pressure-tested in a grill session on 2026-10-03/04. It produced 83 raw
findings: 4 fact corrections, 22 decision clusters and 7 follow-ups (#236). The clusters were
split into two locks. This record covers the transition lock.

## Decision

1. **Delivery.** There is no entry skill, and `jarvis-setup` is deleted. The README is the
   hub. It carries the foundation, a ready prompt for the reader's agent ("read this, compare
   with my repo, report") and a map of classes against where each surfaces. A reader-facing
   skill is added only if the prompt proves insufficient.

2. **Unit of content.** The unit is a class doc, one per class. Each class doc has an incident
   catalog, `incidents/<class>.md`, a table with one row per incident. An incident enters the
   catalog only if a class doc cites it. The maintainer's own repos are treated like any other
   public project. Studies and measurements are not incidents; they go into a class doc's
   Evidence section.

3. **What the structure gate enforces.**
   - Frontmatter `class`, `surfaces_at` (a list), `applies_when` and `applies_when_not`.
   - Four sections: Mechanism, Where it surfaces, Evidence, Protections.
   - Every incident link resolves to a row of the catalog, not only to the file.
   - A catalog row that no class doc cites fails.
   - A scanned directory that is missing fails rather than passing with nothing checked.
   - A size cap, held as one configurable value. It starts at 30 KB and is re-set after the
     pilot.

   Each rule has a fixture that violates it, and the gate's tests show that the fixture fails.

   The gate no longer enforces `kind`, `requires`, hub/children, numbered options with checks,
   `Relations:`, the paid-plan-name ban, check dates or the weekly expiry check. The
   personal-literal scrub and the quote checks stay.

4. **One path definition.** The merge hold, the reviewer and the gate all select files by the
   same definition: `docs/**` (minus `docs/adr/`), `incidents/**` and `README.md`. A test
   pins that the three agree. Before this, the hold and the reviewer looked only at `docs/` and
   `examples/`, so the hub and the catalog would have merged unread.

5. **Review.** `review-doc` is rebuilt with two passes:
   - claim ↔ evidence: a link exists, the strength of the evidence is stated, and n=1 is not
     hidden;
   - readability for a newcomer.

   The rules for running it:
   - Its findings are advisory. The merge is held by `waiting-human-review` until the
     maintainer has read the doc.
   - The maintainer reads the doc first and the report second, so the report does not frame
     the read.
   - It runs once automatically. It runs once more, on request, only if the text changed
     after the first run. There is no third run.
   - Its status is attached to the head commit. A run that fails shows red, not as a clean
     report.
   - Its catch rate is measured once, when it is built, on one fixture doc with 5–6 seeded
     defects of the two kinds it reviews. The number is recorded here. The review is a
     supplement to the human read, not a gate.

   The calibration programme is deleted, not frozen: the calibration doc, the corpus, the
   rules, the shadow ranker and the snapshot tests.

6. **Writing.** Before writing Protections, the writing agent searches external practice
   blind, that is, before reading the maintainer's own fixes. Each protection carries an
   external source or the label "one operator's practice". The reviewer's claim ↔ evidence pass
   checks this.

7. **The hold label.** Agents are told not to remove `waiting-human-review`, and
   `github-authority-guard` blocks the direct ways of removing it. The guard's known bypasses
   are tracked in #232. No further control is added. Current models follow an explicit "do not
   remove the hold" instruction reliably even when they could remove it. The hold therefore
   rests on an instruction backed by a hook with known gaps. Told rules fail under an
   incentive conflict, and that risk is accepted knowingly.

8. **Teardown.** All nine docs under `docs/` (ADRs excepted) and all thirteen files under
   `examples/` are deleted, together with `jarvis-setup` and the old reviewer. Git history is
   the backup. Install and check instructions for the hooks and scripts that stay move into
   their own docstrings. The examples are not migrated, because the catalog is written from
   their primary sources, which remain linkable.

9. **Earlier records.**
   - ADR-0003 is superseded by this record.
   - ADR-0001 is superseded, because the calibration it planned is removed.
   - ADR-0002 is amended:
     - Its decision 1, that a touched doc is reviewed in full, holds for the rebuilt reviewer.
     - Its decision 2, that pre-existing findings block, is superseded because review is
       advisory.
     - Whether to keep its decision 3, the backstop review of untouched docs, is decided when
       the reviewer is rebuilt.
     - Its 2026-10-01 scope amendment (`docs/` or `examples/`) is replaced by the path
       definition in decision 4.
   - `docs/adr/` stays outside review and the gate, as ADR-0001 set it.

## Considered options

- **Keep an entry skill that routes and configures.** Rejected: the reader's agent can read
  the base itself, and the skill was the only consumer of most of the contract.
- **Keep the old docs until the new ones exist.** Rejected: material kept "just in case" was
  never deleted and got in the way. It is better to start from zero, with git history as the
  backup.
- **Freeze the calibration rather than delete it.** Rejected: frozen machinery is still
  maintained by every change to the reviewer.
- **Measure nothing about the new reviewer.** Rejected: the review is still expected to catch
  links and facts, and with no measurement nobody would know whether it does.
- **Make review a required blocking check.** Rejected: the last measured catch rate was too
  low to block on.
- **A third, blind reviewer pass for completeness.** Rejected on cost. The blind search moved
  to the writer instead (decision 6).

## Open — locked before the first class doc

These are recorded here when decided:

- How it is checked that a reader's agent gets what it needs from the README prompt, and
  where a reader reports that the base was not enough.
- How catalog rows from private sources are labelled.
- What may change after the pilot, and on how many docs the contract is calibrated.
- Whether costs and outcomes carry an as-of date.
- Whether the hub map and the vocabularies are generated or checked.
- The contract for the cross-cutting docs.
- How the source mix is disclosed.
- What detects that the escalation rule has become smeared across class docs.
- What README shows while the base is incomplete.
- How quoted injection content is handled.
