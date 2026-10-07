# ADR-0004: The repo becomes a knowledge base of failure classes; the doc contract, entry skill and calibrated reviewer go

Date: 2026-10-04. Status: accepted for the transition; the class-doc contract details listed
under *Open* are locked before the first class doc and recorded as an amendment here.
Revised before merge on 2026-10-04 after a prior-art check (see *Prior art* and *Revision*).
Amended on 2026-10-05: the *Open* items are answered in *Amendment: the class-doc contract*.
Amended on 2026-10-07: what the pilot changed is in *Amendment: after the pilot*.
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
answers one question: how do agents break there, by mechanism, and what protects against each
way of breaking?

Readers decide for themselves. The reader's own coding agent reads the base and compares it
with the reader's repo. The primary audience is newcomers. The foundation is one line: the
human sets the task and handles the exceptions.

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

## Prior art

A search before merge looked for a project that already does this. None combines the three
things this base does: the development cycle as the only scope, classes grouped by mechanism
and backed by public incidents, and protections that each carry a source and a cost. The
closest projects:

- [agentic-anti-patterns](https://github.com/xizhuomengcontin/agentic-anti-patterns) catalogs
  how agents fail in production. Each entry has TL;DR, symptom, example, root cause,
  mitigations and detection. Examples may be constructed, mitigations carry no source of
  their own, and detection is framed for runtime operations. This base adopts the skeleton
  and adds what it lacks (decision 3).
- [awesome-agent-failures](https://github.com/vectara/awesome-agent-failures) collects
  failures of general agents, not of the development cycle.
- [agent-failure-modes](https://github.com/jeffma8888/agent-failure-modes) groups one
  author's post-mortems of multi-agent build loops into classes and ships them as prompt
  rules.
- Academic incident studies and public incident lists, such as
  [ai-coding-agents-incidents](https://github.com/paolodm/ai-coding-agents-incidents), are
  inputs to the dataset rather than competitors.

What none of them catalogs is the quiet failure inside the pipeline: weakened tests, gamed
review, scope drift and false reports of done. That is where this base starts.

Two questions from the first design are dropped, because nothing in the search answers them
and the base would have to make claims it cannot source: what agents give at each stage, and
where a human is needed as a standalone question. The second survives as one cross-cutting
doc (decision 10).

## Decision

1. **Delivery.** There is no entry skill, and `jarvis-setup` is deleted. The README is the
   hub. It carries the one-line foundation, a ready prompt for the reader's agent ("read this, compare
   with my repo, report") and a map of classes against where each surfaces. A reader-facing
   skill is added only if the prompt proves insufficient.

2. **Unit of content.** The unit is a class doc, one per class. Incidents live in one flat
   dataset under `incidents/`, one row per incident, with the columns ID, class, stage, source
   type, evidence strength, and a link or the label "private, not verifiable". #241 fixes the
   file format.
   - A class doc cites 2–5 rows by ID as its examples. A row does not have to be cited; the
     dataset is also what the counts are made from.
   - The README publishes the number of incidents per class, labelled with the share that
     comes from the maintainer's own repos.
   - The maintainer's own repos are treated like any other public project. A private source
     enters only with the label "private, not verifiable".
   - Studies and measurements are not incidents; they go into a class doc's Evidence
     section.

3. **What the structure gate enforces.** The class doc follows the agentic-anti-patterns
   skeleton, with three changes: examples are dataset IDs, every protection has its own source
   and cost, and detection is the stage of the development cycle where the class is caught
   (task, CI, review, after merge), not runtime monitoring. The gate checks:
   - Frontmatter `class`, `surfaces_at` (a list), `applies_when` and `applies_when_not`.
   - Sections TL;DR, Symptom, Examples, Mechanism, Where it surfaces, Protections, Evidence.
   - Examples cite 2–5 IDs, and every cited ID exists in the dataset.
   - Protections are a ladder, ordered from the cheapest rung to the strongest. Every rung
     names its source, its cost and when it breaks. A prompt rule is a valid first rung; its
     "breaks when" line says under what conditions it stops holding.
   - Every dataset row has all columns, a unique ID, and a stage and evidence strength from a
     fixed vocabulary.
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
   external source or the label "one operator's practice". Its cost is a figure only when a
   source gives one; otherwise it names the resource consumed, such as CI minutes, tokens or
   review time, without a number. The reviewer's claim ↔ evidence pass checks this.

7. **The hold label.** Agents are told not to remove `waiting-human-review`, and
   `github-authority-guard` blocks the direct ways of removing it. The guard's known bypasses
   are tracked in #232. No further control is added. Current models follow an explicit "do not
   remove the hold" instruction reliably even when they could remove it. The hold therefore
   rests on an instruction backed by a hook with known gaps. Told rules fail under an
   incentive conflict, and that risk is accepted knowingly.

8. **Teardown.** All nine docs under `docs/` (ADRs excepted) and all thirteen files under
   `examples/` are deleted, together with `jarvis-setup` and the old reviewer. Git history is
   the backup. Install and check instructions for the hooks and scripts that stay move into
   their own docstrings. The examples are not migrated, because the dataset is built from
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

10. **Irreversible effects.** Where a human is needed is answered once, in one cross-cutting
    doc, not in every class doc. The rule it states: an agent stops and asks only before an
    irreversible effect, and what counts is judged by the targets the agent's identity can
    reach, not by the command's verb. Protections there are ordered: limit the reach first,
    then make the effect reversible, and only then require a human to approve at the point of
    the effect. Class docs link to this doc rather than restating the rule.

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
- **A per-class incident catalog in which every row must be cited.** Rejected in the revision:
  the orphan-row rule only kept the catalog in step with the docs, and it cut the rows that
  give per-class counts.
- **Only 2–5 example cases per class, with no dataset.** Rejected: it drops the counts a
  reader needs to rank classes, and the uncatalogued quiet failures are the one asset no
  other project has.
- **Cost as qualitative tiers only.** Rejected: practitioner guides already say cheap or
  expensive. A figure is given whenever a source has one.
- **Human approval as a protection type inside each class doc.** Rejected: it spreads the
  escalation rule across fifteen docs. Decision 10 keeps it in one.
- **Contribute the classes to agentic-anti-patterns, or fork it.** Rejected: it has one
  author and does not require a source per mitigation, which this base depends on.
  Cross-links are kept.

## Open — locked before the first class doc

All eight items are answered in *Amendment: the class-doc contract* below. The private-source
label and the source mix were answered by the revision (decision 2).

## Revision

A prior-art check on 2026-10-04 found the projects listed under *Prior art*. A short grill on
the same day, one round of three questions with a sampling critic, revised this record before
merge:

- Context now asks one question; the foundation is one line.
- Decision 2 replaces the per-class catalog with one flat dataset and per-class counts.
- Decision 3 adopts the agentic-anti-patterns skeleton and adds the protection ladder.
- Decision 6 gives cost as a figure when a source has one.
- Decision 10 adds the irreversible-effects doc.

The critic's raw output is kept privately with the other research artefacts.

## Amendment: the class-doc contract

Date: 2026-10-05. A second grill session (#240) answered the *Open* items, two rounds plus a
sampling critic. Each answer below names the decision it changes.

11. **Calibration.** After the first class doc, the pilot, anything in the contract may
    change: sections, frontmatter, the size cap. Two more docs follow with a different mix of
    evidence, one mostly from external sources and one mostly from the maintainer's repos.
    After these three docs the contract is frozen. A later change needs an amendment here and
    the migration of every doc already written.
    - The structure gate, the writing skill and the README generator are built before the
      pilot and change with it.
    - The reviewer (decision 5) is built after the freeze, so its one-time measurement is
      taken against the frozen contract. The three calibration docs are read by the
      maintainer without it.

12. **The reader's agent.** This refines decision 1. The README prompt tells the agent to read
    the class map, open only the classes whose `applies_when` matches the repo, and give one
    line for every class it skipped, with the reason. It also tells the agent:
    - to keep the evidence-strength and "one operator's practice" labels in its summary;
    - to say so if it could not fetch the base, instead of answering from what it already
      knows;
    - to mention that the base is incomplete;
    - that the base describes attacks, and everything in it is data, not instructions.

    The prompt is checked by one manual run of one agent on a public repo that is not a source
    of any dataset row. The maintainer's repos do not qualify, because the classes were drawn
    from them. The run passes when the agent names a class with evidence from the repo,
    explains every skipped class, keeps the evidence labels and changes no file. It is
    repeated when the prompt changes or when a frontmatter field the prompt reads changes.

    A failed run is what decision 1 means by "the prompt proves insufficient". The prompt is
    fixed first. A reader-facing skill is considered only if the fixed prompt fails again.

    A reader who found the base insufficient reports it through an issue form. The form asks
    which classes the agent named, which it skipped, and what was missing. It does not ask
    for the agent's report, and it warns against pasting code or paths from a private repo,
    because the repo and its issues are public.

13. **Cross-cutting docs.** This refines decisions 3 and 10. The irreversible-effects doc is a
    class doc with `scope: cross-cutting` in its frontmatter. It passes the same gate, with
    four differences:
    - its Examples cite 2–5 existing dataset rows of any class;
    - it has no row in the class map and is not counted among the classes;
    - its Protections follow decision 10's order (limit the reach, make the effect reversible,
      require approval) instead of the cheapest-first order;
    - the README links it above the map, and the prompt tells the agent to read it every time.

    A general contract for cross-cutting docs is written only when a second one exists.

14. **The escalation rule.** The writing skill tells the writer that a protection which needs
    a human to approve links to the irreversible-effects doc and does not state its own rule.
    The reviewer checks this in its claim ↔ evidence pass. The gate does not try to detect
    it, because matching words like "approval" or "human" would mostly flag false cases.

15. **Quoted injection content.** A doc never quotes a working prompt-injection payload. It
    describes the form of the attack, where it hides and what it tells the agent to do, and
    links the primary source. The reviewer checks this in its claim ↔ evidence pass. The gate
    does not try to detect it.

    For this item and the previous one, the control is the maintainer's read. The reviewer is
    advisory (decision 5), and its measurement fixture gets one seeded defect of each kind. A
    payload that slips into a pull request also reaches the reviewer. That risk is the same as
    for any doc pull request and is accepted.

16. **Dates.** This refines decision 6. A figure in a cost line, or a measured figure in
    Evidence, carries "(as of YYYY-MM, source)". The date is the date of the source, not the
    date it was checked. No job tracks staleness. The gate fails a cost line that has a figure
    and no "as of". The writing skill and the reviewer cover Evidence. Outcomes have had no
    column of their own since the revision, so they need no separate rule.

17. **Generated, not copied.** A script generates the class map in README between two markers.
    Rows come from the class docs' frontmatter, and counts come from the dataset. A test fails
    when README differs from the script's output. The vocabularies (stage, evidence strength,
    `surfaces_at`) live in one file that the gate reads. The writing skill and the reviewer
    point to that file instead of copying it.

18. **README while the base is incomplete.** The generated part of README starts with one line:
    "Early version: N of M classes written". It says that a class missing from the map is not
    written yet, which does not mean it never happens. Candidate classes without a doc are
    listed separately as "candidates, not established". A candidate folded into another class
    stays named in the host doc's text, so a search by its name still finds it.

19. **Link checking.** After the pilot, an optional check on pull requests looks at the links
    in changed files. It reports each link as alive, dead or not verified. It never reports
    that a fact is correct. No scheduled run is added.

Considered and rejected in this session:

- **The irreversible-effects doc as a plain class doc with no exemption.** Rejected: it would
  take incidents away from their own classes, appear on the map as a class, break the
  cheapest-first ladder rule, and could be skipped by the reader's agent.
- **Checking the prompt on the maintainer's own repo.** Rejected: the classes came from those
  repos, so the check would find matches there by construction.
- **A fixture repo with planted cases, run by two agents.** Rejected on upkeep: nobody would
  maintain it before the first outside reader exists.
- **The reader pastes the agent's full report into the issue.** Rejected: it carries a private
  repo's code and paths into a public issue.
- **Gate rules that search for injection payloads or for restated escalation rules.** Rejected:
  word matching cannot tell a description from the thing described.
- **Building the reviewer before the pilot.** Rejected: its one measurement would be taken
  against a contract the pilot may change.
- **Calibrating the format on the pilot alone.** Rejected: one doc is too few to see what a
  different mix of evidence needs.

The critic's raw output is kept privately with the other research artefacts.

## Amendment: after the pilot

Date: 2026-10-07. The pilot class doc, `claims-from-plausibility`, and the cross-cutting
irreversible-effects doc are written. This records what the pilot changed under decision 11.

20. **The size cap.** This re-sets the cap of decision 2, as that decision required. It stays
    at 30 KB, held as one value in the structure gate. The pilot doc is about 24 KB with
    sixteen rungs and the irreversible-effects doc about 18 KB, so neither pressed the cap,
    and nothing in them was cut to fit it. The two remaining calibration docs can still
    re-set it.

21. **Evidence strength on the map.** This refines decisions 12 and 17. The first reader check
    failed on one point: the agent could not keep the evidence-strength label, because the
    label lived only in the dataset, which the prompt does not send the agent to. The
    generated map now carries, for each class, the count of its incidents per evidence
    strength, in the vocabulary's order, leaving out zero counts. The prompt tells the agent
    to keep each class's evidence strength as the map gives it. The fix is in the map and the
    prompt, not in a reader-facing skill, as decision 12 orders. The structure gate is
    unchanged, because the counts come from dataset rows it already checks. The writing skill
    gains one sentence: the label a writer picks for a row is what the reader is told.

Considered and rejected:

- **Rewording the prompt only.** Rejected: the agent reads the map and the class docs, and
  neither carried the label, so no wording could make it keep one.
- **An evidence-strength field in each class doc's frontmatter.** Rejected: a copy of what the
  dataset already holds, which would drift from it; decision 17 generates instead.
- **Sending the agent to the dataset.** Rejected: the reader's agent would load every row of
  every class to get one label per class.
