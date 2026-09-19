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
3. **Severity.** Mark each finding `blocking` or `follow-up`:
   - `blocking` — you name a setup from step 1 and what goes wrong for it: an option ruled out
     that it could build, one left in that it cannot build, nothing left with no pointer, or a
     judgement filter that the setup could answer either way, changing what is left. Also
     `blocking`: a contradiction with an option's own section, with both sides quoted, that cannot
     both be true.
   - `follow-up` — everything else: wording that could be tighter, a trade-off named more loosely
     than the option's cost, a judgement filter that sends none of your setups anywhere wrong.

   If you cannot name the setup, or quote two sides that cannot both be true, the finding is
   `follow-up`.
4. **Value test.** Find two realistic setups where the option the doc calls ours is ruled out, or
   stays in but loses a trade-off the section names. If there are none, the doc only justifies our
   choice. Report `fails value test`; it is `blocking`.

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

## Delta pass — a fix commit

A fix is new text, and new text is unreviewed. When a commit only fixes the findings of an earlier
report, review the fix instead of running every pass again. If the commit adds, drops or
renumbers an option, or rewrites "how to choose" beyond what the findings named, run the whole
skill instead.

Give one fresh reviewer, not the session that made the fix:
- the earlier report;
- the whole diff, `git diff <reviewed commit>..<fix commit>`, not only the doc and the files in
  scope;
- a checkout of the repo at the fix commit, to read around each hunk and to search;
- the PR's replies to the earlier report;
- an absolute scratch directory, as in Rule 0.

This one context runs checks from passes 1 and 3. That is the one exception to "each pass starts
fresh": the fix is small, and the reviewer sees the earlier passes only through their report.

1. **Each finding** of the earlier report gets one verdict:
   - `fixed` — after the diff, the finding no longer holds. Judge only that; anything the
     change breaks is a new finding from steps 2–4.
   - `answered` — the PR links an issue the finding was filed as, or, for any finding but a
     `follow-up`, replies to it with a reason.
   - `not fixed` — neither.
2. **Each added or changed claim** gets pass 1's checks and verdicts, from a source fetched now.
3. **Each added or changed filter, trade-off, example or choice** gets pass 3's checks and
   severity, against the option sections as they stand after the fix. If the diff changes our
   own choice, run the value test again.
4. **Leftovers.** For each fact the diff changes (a tool, a flag, a number, a quote, an option's
   name or number, a filter), search the whole repo for its old wording and for every other
   mention of it. A mention that now disagrees with the fix is a finding at its own line, even if
   that file is outside the scope: a `mismatch` if it states a fact (a tool's behaviour, a
   flag, a number, a quote, which option we use), otherwise a how-to-choose finding with its
   severity.
5. **New calls.** A call the diff adds, in the sense of pass 4, goes on a "read these closely"
   list.

Pass 2 and the earlier judgement spots stand; the delta pass does not redo them.

## The report

Post the report where the doc is reviewed, as a comment on the doc's PR. Keep it to one screen
before the collapsed parts.

```
## review-doc: <doc path> @ <commit>

**Read these closely (N):**
1. <file:line> — <why this is a call, one line>
...

**Mismatches (N):** <file:line> — <claim> → <fetched excerpt> (<source>), or the line it disagrees with
**Missing options (N):** <approach> — <source> — <why a reader could need it>
**How to choose (N blocking, N follow-up):** <file:line> — blocking | follow-up — <filter, trade-off or example> → <setup that breaks it, or both quotes>
**Value test:** passes | fails — <setup 1 → ours ruled out by <fact> | loses on <trade-off>>; <setup 2 → …>
**Unverifiable (N):** <file:line> — <why>

<details><summary>Confirmed (N)</summary> one line each, with the excerpt </details>
<details><summary>Completeness search</summary> the queries, the approaches found, how each matched </details>

Reviewed by: fresh contexts, not the writing session. Calibration: <link to CALIBRATION.md>.
```

A delta pass posts the same report, headed `## review-doc delta: <doc path> @ <reviewed
commit>..<fix commit>`. It opens with `**Findings fixed:** N of M, N answered`, where M counts
every finding of the earlier report, then lists each finding that is `not fixed`. Its "read these
closely" list holds only the calls the fix added. It has no completeness search.

The writer fixes every mismatch, every `missing` option and every `blocking` finding, or answers
each one in the PR. A `follow-up` is fixed or filed as an issue, and the PR says which. The fix
then gets the delta pass, and that report is the earlier report for the next fix. The loop ends
when no mismatch, `missing` option or `blocking` finding is left that is not fixed or answered,
and every `follow-up` is fixed or filed. The person signs only after reading the report, the
"read these closely" places, and a skim of the rest. What that signature covers is set out in
[`docs/SIGNOFF.md`](../../../docs/SIGNOFF.md).

The shape a practice doc is expected to have, and why, is in
[`write-doc`](../write-doc/SKILL.md). A doc that departs from it is not wrong for that alone;
review what it says, not which headings it has.

## Limits

- Pass 2 uses a model much like the one that wrote the doc, so both can share blind spots. The
  printed queries are how the person spots a narrow search.
- Severity rests on the setups the reviewer thought of. A judgement filter is `follow-up` until
  some reviewer builds the setup it misleads.
- A source can change after the review. The report pins the doc's commit, not the web.
- The skill only counts once calibrated. [`CALIBRATION.md`](CALIBRATION.md) records what it caught
  on planted errors and what it flagged on a clean doc. Re-calibrate after changing a pass.
