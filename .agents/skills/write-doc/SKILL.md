---
name: write-doc
description: The default shape for a practice doc under docs/ — a guide that lets a reader facing a problem pick the option that fits their own case. Follow it when drafting or reworking such a doc; add, drop or change sections when the subject needs it, and say why in the PR.
---

# Write a practice doc

A practice doc answers one question: can a reader facing this problem in their own project pick
the option that fits their case after reading only this file? A doc that explains why *we* chose
X answers a different question and fails this one, however well its frontmatter is filled.

## This is a default, not a form

The shape below is what has worked. Follow it unless the subject needs something else. A section
may be added, dropped, renamed, split or reordered when that serves the reader better. A problem
with one real answer needs no option list; a subject with two independent choices may need two
"how to choose" parts. When you depart from the shape, say what and why in the PR, so the
reviewer and the person who signs can judge the call.

What is not optional is set elsewhere: the frontmatter and size cap that
[`tests/structure_gate.py`](../../../tests/structure_gate.py) enforces, and the sign-off rules in
[`docs/SIGNOFF.md`](../../../docs/SIGNOFF.md).

## The shape

### Frontmatter

```yaml
---
applies_when: <the reader's situation, in their words, not ours>
applies_when_not: <the nearby situations this doc does not cover, and where to go instead>
signed_off:
---
```

`signed_off` stays empty in the drafting PR. A person fills it later, in a separate PR.

### Title and "The problem"

State the problem as the reader meets it, in their project, before any option. Then list the ways
it goes wrong, one line each. These failure modes are what every option is later judged against,
so a reader can see what each option handles and what it gives up.

Then say how claims are marked: which options we **tried** (and where the trace is), which are
**sourced** from a tool's docs or code, and the date the quotes were checked.

### "The options"

The whole space a reader could pick from, not only what we used. Search outside our projects:
tool docs, source code, well-known projects that solve the same problem. Each option gets:

- **How it works.** The mechanism, with at least one real tool that does it, quoted and linked.
- **Best pick when** — the situation in which this option is the right one.
- **Cost.** What it gives up, against the failure modes from "The problem".
- **Update / uninstall**, or whatever the lifecycle is for this subject.
- **Status:** tried or sourced.

Variants that share a mechanism go under one option. Something that applies to every option (a
dry run, a backup) goes in its own short section after them, not repeated in each.

### "How to choose"

Do not collapse the choice to one answer. If a rule always gave the same option, the doc would
carry no information; if it always gave one option per setup, it would hide the trade-offs that
make the choice a real one.

1. **Facts that settle it outright**, if any: a condition under which one option is the only
   sensible one. Keep these few, and only where they truly settle it.
2. **Facts that rule options out.** Each filter must be something the reader can check in their
   own setup: a format feature, a constraint of the tool, not "if it is cleaner". A table
   `Option | Fits only if` works well.
3. **Trade-offs among what is left**, named from each option's own *best pick when* and *cost*.
   Several options usually remain; that is intended.
4. **What to do when nothing is left.**
5. **Examples**: a setup or two, walked through the filters, showing that more than one option
   can pass.

### "Our own choice"

One worked example: which option our project took, which facts ruled the others out, what it
costs us, and any open gap with its issue. It is one example among the reader's, not the spine
of the doc.

## Where the rest goes

- A recorded run or a third-party case that shows an option at work → `examples/`, with `fit`
  and either `last_seen` (ours) or `source` + `verified` (theirs), and `pairs_with` pointing at
  the doc.
- Something a reader can install or copy → `resources/`, with `pairs_with`, `harnesses` and
  `cost`.

Link them from the option they illustrate. Keep the doc itself under the size cap by moving
detail there, not by dropping options.

## Before a person signs

Run [`review-doc`](../review-doc/SKILL.md) on the PR, in contexts that did not write the doc. Fix
or answer what it finds, and run its delta pass on each fix, until the loop its report section
describes ends. You wrote the doc, so you do not review it.
