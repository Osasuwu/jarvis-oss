---
name: write-doc
description: How to write a class doc under docs/classes/ and the incident rows it cites — the contract the structure gate enforces, plus the rules the gate cannot check (blind external search, the protection ladder, dated figures, escalation, injection payloads). Follow it when drafting or reworking a class doc.
---

# Write a class doc

A class doc describes one failure class of agent-assisted development: what it looks like, why it
happens, where in the development cycle it is caught, and the ladder of protections against it,
each with a source and a cost. One doc per class. The incidents behind it are rows in
`incidents/incidents.csv`, not prose in the doc.

Two things decide whether the doc is accepted. [`tests/structure_gate.py`](../../../tests/structure_gate.py)
checks the structure and fails the build. A reviewer and then the maintainer check what the gate
cannot: that each claim has its source, that the search was blind, that nothing dangerous is
quoted. This file covers both. Read the gate when a rule below is unclear; it is the source of
the structure, and this file follows it.

## The contract the gate enforces

**Frontmatter**: `class` (a lowercase slug, the same value as in the dataset's `class` column and
the doc's file name without `.md`),
`surfaces_at` (a list), `applies_when` and `applies_when_not`. `applies_when` is the reader's
situation in their words; `applies_when_not` names the neighbouring situations the doc does not
cover. The values `surfaces_at` accepts are in `docs/vocabularies.json`; take them from there,
do not copy them into the doc or into this file.

**Seven sections**, as `## ` headings: TL;DR, Symptom, Examples, Mechanism, Where it surfaces,
Protections, Evidence.

**Examples** cite 2–5 incidents by dataset ID (`INC-` and digits). Every cited ID must exist in
`incidents/incidents.csv`, and its `class` must be this doc's class.

**Protections** are `### <rung>` headings. Each rung has three labelled lines, each with a value:
`Source`, `Cost`, `Breaks when`.

**Cost lines**: a cost line that contains a digit must carry `(as of YYYY-MM, source)`.

**Links**: a relative link must resolve to a file. External links are not fetched by the gate.

**Size**: the file, as stored, stays under 30,000 bytes. When it does not fit, move detail into a
linked file; do not drop protections to fit.

The gate checks nothing else. Ladder order, dates in Evidence, the blind search and quoted
payloads are yours to get right, and the reviewer checks them.

## The incident dataset

Add the rows before the doc, so Examples can cite them.

- One row per incident, with the gate's columns: `id`, `class`, `stage`, `source_type`,
  `evidence_strength`, `link`. `id` is `INC-` followed by at least three digits and is unique.
  `stage` and `evidence_strength` come from `docs/vocabularies.json`. Every column has a value.
  `stage` is where in the development cycle the incident was caught (or, if it never was, where
  it did its damage). `source_type` is free text for what the link is (an issue, a pull request,
  a post-mortem, a talk). `evidence_strength` says how close the link is to the incident; pick
  the vocabulary value that fits, and use the private one only for a private source.
- The maintainer's own repos are treated like any other public project: one incident, one row,
  same link rule, no special label.
- A private source enters only with the link column set to "private, not verifiable".
- A study or a measurement is not an incident. It goes in the doc's Evidence section, not in the
  dataset.
- A row does not have to be cited by a doc. The dataset is also what the per-class counts are
  made from.

## Write it in this order

1. **Frontmatter and the problem.** Fill `class`, `applies_when` and `applies_when_not`. Write
   the Symptom as the reader meets it, in their project, before any cause.
2. **Search external practice, blind.** Do this before you read the maintainer's own fixes, so
   the doc reflects how the field handles the class and not how this repo happens to.
   Run the search in a fresh context (a subagent, or a new session), never in the session that
   writes the doc, and give it only the frontmatter and the Symptom. It returns every protection
   it finds, each with one primary source, and the queries it ran. Put the queries in the PR; the
   reviewer runs them again, so that they find nothing new.
3. **Then read our own fixes.** A fix of ours that no external source describes is a rung with
   the Source line "one operator's practice". It is a rung like any other, and it is not
   promoted to a better label.
4. **Write the rest** from the skeleton below.

## Section rules

**Where it surfaces** names the stage of the development cycle at which the class is caught:
task, CI, review, after merge. It is not runtime monitoring and it is not a list of tools.
`surfaces_at` in the frontmatter lists the same stages, written exactly as
`docs/vocabularies.json` spells them; that file is the whole list, so a stage the prose names
that is spelled differently there is written the way the file spells it.

**Protections are a ladder**, ordered from the cheapest rung to the strongest. Each rung carries:

- **Source**: an external source, linked (a tool's or vendor's documentation counts, as does a
  study or a public write-up), or the label "one operator's practice".
- **Cost**: a figure only when a source gives one, with `(as of YYYY-MM, source)`. Otherwise the
  resource the rung consumes (CI minutes, tokens, review time) with no number.
- **Breaks when**: the condition under which the rung stops holding.

A prompt rule is a valid first rung. Its Breaks-when line says when it stops holding (a long
session, a context the rule is not in, an agent that does not read it). A ladder that starts at
a hard control and has no prompt rung is fine when no prompt rule helps; do not invent one.

**Dates.** A figure in a Cost line, and a measured figure in Evidence, carries
`(as of YYYY-MM, source)`. The date is the date of the source, not the date you checked it. The
gate fails a Cost line with a figure and no date; for Evidence it is on you and the reviewer.

**Escalation.** When a protection needs a human to approve something, do not restate when an
agent must stop and ask. Link the irreversible-effects doc (a cross-cutting doc in
`docs/classes/`) and say which rung depends on it. The rule is stated once, there. If that doc
does not exist yet, name it in prose without a link: the gate fails a link that does not resolve.

**Injection payloads.** Never quote a working prompt-injection payload, not even to illustrate.
Describe the form of the attack, where it hides, and what it tells the agent to do, and link the
primary source. A payload in a pull request reaches every agent that reads the pull request.

**Cross-cutting docs.** A doc about a rule that applies across classes (the irreversible-effects
doc is the first) sets `scope: cross-cutting` in its frontmatter and nothing else as a value for
`scope`. It passes the same gate, with four differences: its Examples cite 2–5 rows of any class;
it has no row in the class map and is not counted among the classes; its Protections follow the
order of its own rule instead of cheapest first (for irreversible effects: limit the reach, make
the effect reversible, then require approval); and the README links it above the map.

**Folded candidates keep their names.** Some candidate classes were folded into others.
N3 (folded into C7), N4 (into C5), N-C (into C11) and N-D (into N-B) keep their names inside the host doc, so a search for the name lands there.
When you write a host doc, put the folded name in its text where the doc covers it.

## The skeleton

Replace every `<...>`. Keep the headings and the three rung labels as written. The gate accepts
this file's structure when the placeholders are filled and the cited IDs exist in the dataset.

```markdown
---
class: <class-slug>
surfaces_at: [<a stage from docs/vocabularies.json>]
applies_when: <the reader's situation, in their words>
applies_when_not: <the neighbouring situations this doc does not cover>
---

# <Class name>

## TL;DR

<One or two sentences: what goes wrong and what stops it.>

## Symptom

<What the reader sees in their project, before any cause.>

## Examples

- INC-<digits>: <what happened in this incident, one line>
- INC-<digits>: <what happened in this incident, one line>

## Mechanism

<Why it happens. Claims about a tool or a study carry their link.>

## Where it surfaces

<The stage of the development cycle at which it is caught, and what shows it there.>

## Protections

### <Cheapest rung, a prompt rule is valid>

- **Source:** <an external source, linked, or: one operator's practice>
- **Cost:** <the resource consumed, no number unless a source gives one; with a number add (as of YYYY-MM, source)>
- **Breaks when:** <the condition under which this rung stops holding>

### <A stronger rung>

- **Source:** <an external source, linked, or: one operator's practice>
- **Cost:** <the resource consumed>
- **Breaks when:** <the condition under which this rung stops holding>

## Evidence

<Studies and measurements, each figure with (as of YYYY-MM, source), the date being the source's.>
```

## Before review

The reviewer should find nothing you could have found yourself. Check these before you open the
PR for review, on the doc and on the files it links to:

1. **The gate.** `pytest tests/test_structure_gate.py` runs the fixtures; run the gate on the
   tree itself as the structure-gate workflow does. A failure names the rule and the file.
2. **Quotes.** Run `python scripts/check_quotes.py <doc> <each of those files>`. For each quote it
   fetches the pages linked from the quote's paragraph, or from the paragraph right before it if
   that one has none, and says whether the quoted text is there. Fix every `NOT FOUND`: the
   wording is off, or the link points at a page that does not have the text (link the page that
   does). Check every `found elsewhere`, `unfetchable` and `no source` by hand. CI runs the same
   check on every markdown file a PR changes
   ([`quote-check.yml`](../../../.github/workflows/quote-check.yml)) and fails on `NOT FOUND`
   only.
3. **Scope words.** A claim that says *always*, *never*, *only*, *every*, *all* or *everywhere*
   needs a source that says the same. If the source says less, narrow the claim.
4. **Each fact in one version.** For every claim you wrote or changed, search the doc and the
   files it links to for the same fact (a tool, a flag, a number) and make every mention agree.
5. **The three rules the gate cannot see.** No rung without a source or the operator label; no
   restated escalation rule; no quoted payload.

## When review finds something

A fix is new text, and new text is unreviewed. For each finding:

1. Fetch the source yourself and read the passage. Do not write from the reviewer's summary of it.
2. Change what the finding needs and nothing more. Every sentence you add is a claim the next
   round has to check.
3. Run every step of "Before review" again, then the review.

## Before a person merges

Have the PR reviewed in a context that did not write the doc, and fix or answer what it finds
until a round finds nothing new. You wrote the doc, so you do not review it. A pull request that
touches `docs/classes/` or `incidents/` is held with `waiting-human-review`; do not remove the
label. The maintainer does.
