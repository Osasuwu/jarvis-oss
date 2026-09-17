---
name: review-doc
description: Reviews an agent-written practice doc for facts and completeness in contexts that did not write it, and produces a short report so a human reads only the flagged places closely and skims the rest. Run it on a doc PR before a person signs the doc.
---

# Review a doc

An agent drafts a doc. A person signs it, and a sign-off should mean something. Reading every
line against every source is slow, and people do it badly. An independent agent does it well.
Judging whether the doc reads well and helps is a person's job.

This skill does the agent's part and hands the person a report. The report says which few places
need close reading. Everything else can be skimmed.

**Input:** one doc path under `docs/`. **Scope:** that doc, plus every example and resource it
links to or that names it in `pairs_with`.

## Rule 0 — who reviews

- **Not the writer.** The session that drafted or last edited the doc must not run any pass. It
  already believes the doc, so it cannot review it.
- **Each pass starts fresh.** Run each pass in its own context: a subagent where the harness has
  them, otherwise a new session. The passes do not share notes. They meet only in the report.
- **Fetch, never recall.** A verdict rests on text the reviewer fetched during this run: a web
  page, a raw source file, a command's output, a repo file or an issue. Memory and training data
  do not count. If nothing was fetched, the verdict is `unverifiable`.
- **Fetch into scratch.** Give every reviewer an absolute scratch directory for what it downloads.
  Nothing a reviewer fetches lands in the repo.

## Pass 1 — claims

Give the reviewer the doc and the files in scope.

1. List every checkable claim, with its file and line:
   - a quoted text
   - a number (depth, exit code, version, count)
   - a named behaviour of a tool ("removes it with `--reverse`")
   - a link and what the doc says is behind it
   - a `tried` or `sourced` status, and every frontmatter `claim`, `source` and `verified`
2. Check each claim against its source and give one verdict:
   - `confirmed` — quote the fetched excerpt that matches.
   - `mismatch` — quote the fetched excerpt, and say what differs.
   - `unverifiable` — say why: source unreachable, paywalled, or the claim names no source.
3. A quote passes only if it is **verbatim**. Whitespace and markdown are ignored. A paraphrase
   inside quotation marks is a `mismatch`.
4. **`tried` needs a trace.** Something must show we ran it: a recorded example in this repo, a
   commit, an issue, or a test. No trace, so the verdict is `mismatch`, even if the behaviour is
   right.
5. A claim marked *code-derived* is checked against the code it names. It is `confirmed` only if
   the reviewer points at the lines.

## Pass 2 — completeness, blind

Give the reviewer **only**:
- the doc's `applies_when` and `applies_when_not`
- its problem section, which states the problem as the reader meets it

It must not see the option list, the examples or anything else in the repo.

1. Search the web: tool docs, source code, well-known projects that solve this problem. List every
   distinct approach, with one primary source for each.
2. Record the search queries you ran. The report prints them, so the person can see how wide the
   search was.
3. Hand over the list and the queries. Matching them against the doc happens in pass 4, in a
   context that did not run the search. For each approach the doc lacks, pass 4 gives a verdict:
   - `missing` — a reader could plausibly need it.
   - `covered` — say which option it falls under.
   - `out of scope` — quote the `applies_when_not` clause that excludes it.

## Pass 3 — how to choose, and the value test

Give the reviewer the doc. The "how to choose" section rules options out with checkable facts and
names the trade-offs among what is left. It does not pick one option for the reader. **More than
one option fitting a setup is intended, not a defect.** Do not ask for a step that decides, and do
not report that the steps cannot reach a single option.

1. Build at least four realistic reader setups from `applies_when`, unlike the doc's own examples.
   Walk each through the section: what each filter rules out, and what is left.
2. Report every place where:
   - a filter is a judgement, not a fact the reader can check;
   - a filter rules out an option the setup could build, or leaves in one it cannot;
   - a trade-off, an example or our own choice contradicts an option's own section: how it works,
     best pick when, cost, update and uninstall;
   - a setup ends with nothing left and the section says nothing about that case.
3. **Value test.** Find two realistic setups where the option the doc calls ours is ruled out, or
   stays in but loses a trade-off the section names. If there are none, the doc only justifies our
   choice. Report `fails value test`.

## Pass 4 — judgement spots, and the report

Give the reviewer the doc and the results of passes 1–3. It matches pass 2's list against the
doc's options, as described in pass 2, and writes the report below.

Then it lists the places where the doc makes a **call**, not a fact:
- a verdict another careful reviewer could reverse
- a recommendation that rests on a trade-off
- a sentence that says "usually", "cleaner" or "the standard answer"
- anything passes 1–3 marked `unverifiable` that still carries weight in the doc

At most **seven**, ranked by how much a wrong call would mislead a reader. These are what the
person reads closely.

## The report

Post the report where the doc is reviewed, as a comment on the doc's PR. Keep it to one screen
before the collapsed parts.

```
## review-doc: <doc path> @ <commit>

**Read these closely (N):**
1. <file:line> — <why this is a call, one line>
...

**Mismatches (N):** <file:line> — <claim> → <fetched excerpt> (<source>)
**Missing options (N):** <approach> — <source> — <why a reader could need it>
**How to choose (N):** <file:line> — <filter, trade-off or example> → <setup that breaks it>
**Value test:** passes | fails — <setup 1 → ours ruled out by <fact> | loses on <trade-off>>; <setup 2 → …>
**Unverifiable (N):** <file:line> — <why>

<details><summary>Confirmed (N)</summary> one line each, with the excerpt </details>
<details><summary>Completeness search</summary> the queries, the approaches found, how each matched </details>

Reviewed by: fresh contexts, not the writing session. Calibration: <link to CALIBRATION.md>.
```

The writer fixes every mismatch and every `missing` option, or answers each one in the PR, and
then the skill runs again. The person signs only after reading the report, the "read these
closely" places, and a skim of the rest. What that signature covers is set out in
[`docs/SIGNOFF.md`](../../../docs/SIGNOFF.md).

The shape a practice doc is expected to have, and why, is in
[`write-doc`](../write-doc/SKILL.md). A doc that departs from it is not wrong for that alone;
review what it says, not which headings it has.

## Limits

- Pass 2 uses a model much like the one that wrote the doc, so both can share blind spots. The
  printed queries are how the person spots a narrow search.
- A source can change after the review. The report pins the doc's commit, not the web.
- The skill only counts once calibrated. [`CALIBRATION.md`](CALIBRATION.md) records what it caught
  on planted errors and what it flagged on a clean doc. Re-calibrate after changing a pass.
