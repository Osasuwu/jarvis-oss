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

**Input:** one doc path under `docs/`. **Scope:** that doc, the examples and resources that name
it in `pairs_with`, and the repo `.md` files any of those link to, as
[Chunks and manifests](#chunks-and-manifests) lists them.

## Rule 0 — who reviews

- **Not the writer.** The session that drafted or last edited the doc must not run any pass. It
  already believes the doc, so it cannot review it.
- **Each pass starts fresh.** Run each pass in its own context: a subagent where the harness has
  them, otherwise a new session. The passes do not share notes. They meet only in the report.
- **Fetch, never recall.** A verdict rests on text the reviewer fetched during this run: a web
  page, a raw source file, a command's output, a repo file or an issue. Memory and training data
  do not count. If nothing could be fetched, the verdict is `unverifiable`.
- **Labels come from the rules file.** What `blocking`, `follow-up` and `unverifiable` mean is
  set in [`calibration/RULES.md`](calibration/RULES.md), not here. Read its sections
  [Blocking or follow-up](calibration/RULES.md#blocking-or-follow-up) and
  [`unverifiable`](calibration/RULES.md#unverifiable) before labelling a finding. Where anything
  in this file seems to say otherwise, the rules file wins.
- **Fetch into scratch.** Give every reviewer an absolute scratch directory for what it downloads.
  Nothing a reviewer fetches lands in the repo.

## Pass 1 — claims

Give the reviewer the doc and the files in scope, split into chunks as
[Chunks and manifests](#chunks-and-manifests) says. Each chunk's reviewer runs the steps below on
the lines of its chunk and writes what it finds into the chunk's manifest.

1. List every checkable claim, with its file and line:
   - a quoted text
   - a number (depth, exit code, version, count)
   - a named behaviour of a tool ("removes it with `--reverse`")
   - a link and what the doc says is behind it
   - a `tried` or `sourced` status, and every frontmatter `claim`, `source` and `verified`

   **Premises are claims.** A sentence that argues from a fact ("because the hook runs first,
   ...") rests on that fact; list the fact as its own claim and give it its own verdict. So is a
   fact the reviewer relies on to judge another claim.
2. Record each claim in this order, one field after another. The order is the point: a verdict
   written first gets argued for.
   - **Restatement.** The claim in the reviewer's own words, as one sentence a reader could
     check. A restatement that cannot be written without a guess shows an ambiguous claim.
   - **Excerpt.** The words of the doc the claim rests on, quoted verbatim.
   - **Evidence.** The fetched text that bears on it, quoted verbatim, with where it was
     fetched from; empty only when nothing could be fetched.
   - **Reasoning.** Whether the restatement and the evidence say the same thing, and where they
     differ: a narrower scope, another version, a flag with another name, a paraphrase.
   - **Verdict.** One of the closed list below, and nothing else.

   **Claim verdicts:** `confirmed`, `mismatch`, `unverifiable`, `out-of-scope`.
   - `confirmed` — the evidence says what the restatement says.
   - `mismatch` — the evidence says something else; the reasoning says what differs.
   - `unverifiable` — as the rules file defines it. Say why, and label it `blocking` or
     `follow-up` by the rules file's [`unverifiable`](calibration/RULES.md#unverifiable) section.
   - `out-of-scope` — the line states nothing a source could confirm, for one of the reasons
     below, and the reason is recorded with it.

   **Out-of-scope reasons:** `call`, `placeholder`, `definition`.
   - `call` — a judgement or recommendation; pass 3 and pass 4 review it. Its premises are
     still claims.
   - `placeholder` — an example value the doc does not assert, such as `<your-repo>`.
   - `definition` — the doc names or defines its own term.

   A free-text reason is not a verdict. A claim is never dismissed with a reason off these
   lists, such as "harmless" or "copy-edit": if it is wrong but small, it is a `mismatch`, and
   the rules file decides what that costs. Every `mismatch` and `unverifiable` becomes a
   finding in the report.
3. A quote passes only if it is **verbatim**. Whitespace and markdown are ignored. A paraphrase
   inside quotation marks is a `mismatch`.
4. **`tried` needs a trace.** Something must show we ran it: a recorded example in this repo, a
   commit, an issue, or a test. No trace, so the verdict is `mismatch`, even if the behaviour is
   right.
5. A claim marked *code-derived* is checked against the code it names. It is `confirmed` only if
   the reviewer points at the lines.

## Chunks and manifests

One reviewer reading a whole doc skims its long middle. Pass 1 therefore runs in chunks, and
each chunk leaves a manifest that shows which lines it covered.

- **In-scope files.** The doc itself; every example or resource that names it in `pairs_with`;
  and every `.md` file a repo-internal link in any of those points to, one hop out. In CI the list is
  computed before the review and handed to the reviewer; use it as given.
- **Chunks.** Split each in-scope file into contiguous ranges of at most 150 countable lines, as
  `scripts/doc_review.py` counts them (`countable_lines`: every line, blank ones included). End a
  chunk at a heading or a blank line where one is near. Each chunk gets a fresh subagent, in the
  foreground, with the whole doc as context, and reviews only its own range.
- **Boundaries.** A claim that crosses a chunk boundary belongs to the chunk that holds its first
  line. The next chunk does not record it again.
- **Manifest.** Each report lists the in-scope files first, then one manifest per chunk: the file,
  the first and last line of the range, and every claim recorded in it as pass 1 step 2 says.
  A `mismatch` or `unverifiable` claim names the ID of its finding.
- **Tiling.** For each in-scope file, the ranges cover its lines 1 to the last with no overlap and
  no gap. If they do not tile every in-scope file, or a file is left out, the run is
  `unreviewable`, not a verdict: a report that never looked at a paired example must not read as
  a pass.
- **Delta.** In a delta pass the chunks tile the new side of every hunk the diff makes in an
  in-scope file, and nothing else. A deletion adds no lines to tile.
- **Where manifests go.** Write each manifest into the output the run hands over, never in the
  directory pass 2 reads: pass 2 is blind, and a manifest lists the doc's claims.

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
   - `missing` — a reader could plausibly need it. Label it `blocking` or `follow-up` by the
     rules file; name the reader type if it is `blocking`.
   - `covered` — say which option it falls under.
   - `out of scope` — quote the `applies_when_not` clause that excludes it.

## Pass 3 — how to choose, and the value test

Give the reviewer the doc. The "how to choose" section rules options out with checkable facts and
names the trade-offs among what is left. It does not pick one option for the reader. **More than
one option fitting a setup is intended, not a defect.** Do not ask for a step that decides, and do
not report that the steps cannot reach a single option.

1. Build one realistic reader setup for each in-scope reader type, unlike the doc's own examples:
   - solo, without money;
   - solo, with money;
   - a team of up to three, without money;
   - a team of up to three, with money.

   Across these runs one more axis: the agent runs **attended** (a person is there to answer it)
   or **unattended** (a scheduled or headless run, with no one to answer). Where it changes what
   a filter rules out or what is left, walk the setup both ways. Skip a reader type the doc's
   `applies_when_not` excludes, and say which clause excludes it. Add setups beyond the four
   where `applies_when` allows. Walk each through the section: what each filter rules out, and
   what is left.
2. Report every place where:
   - a filter is a judgement, not a fact the reader can check;
   - a filter rules out an option the setup could build, or leaves in one it cannot;
   - a trade-off, an example or our own choice contradicts an option's own section: how it works,
     best pick when, cost, update and uninstall;
   - a setup ends with nothing left and the section says nothing about that case.
3. **Reasoning, then verdict.** Write each finding's reasoning before its label: the setup from
   step 1, what the section does to it, and the quotes it rests on. Only then decide the label.
   The report keeps that order.
4. **Severity.** Mark each finding `blocking` or `follow-up` by the rules file's
   [Blocking or follow-up](calibration/RULES.md#blocking-or-follow-up) section. A `blocking`
   finding names the setup, and which of the rules file's clauses (i), (ii) or (iii) the doc
   leads it into.
5. **Value test.** Find two realistic setups where the option the doc calls ours is ruled out, or
   stays in but loses a trade-off the section names. If there are none, the doc only justifies our
   choice. Report `fails value test`, and label it by the rules file like any other finding.

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
2. **Each added or changed claim** gets pass 1's checks and verdicts, from a source fetched now,
   in chunks that tile the hunks as [Chunks and manifests](#chunks-and-manifests) says.
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

**Mismatches (N):** M1 <file:line> — <claim> → <fetched excerpt> (<source>), or the line it disagrees with
**Missing options (N blocking, N follow-up):** O1 <approach> — <source> — <why a reader could need it> — blocking | follow-up
**How to choose (N blocking, N follow-up):** H1 <file:line> — <filter, trade-off or example> → <setup and what goes wrong for it, or the quotes> — blocking | follow-up
**Value test:** <setup 1 → ours ruled out by <fact> | loses on <trade-off>>; <setup 2 → …> — passes | fails
**Unverifiable (N blocking, N follow-up):** U1 <file:line> — <why> — blocking | follow-up
**Fix-induced (N):** <finding IDs>, or none

<details><summary>Confirmed (N)</summary> one line each, with the excerpt </details>
<details><summary>Completeness search</summary> the queries, the approaches found, how each matched </details>

Reviewed by: fresh contexts, not the writing session. Calibration: <link to CALIBRATION.md>.
```

Each finding has an ID: its list's letter and a number, as in the template. The fix-induced line
counts the findings that sit in text a fix commit on this PR changed, and lists their IDs. A
finding is fix-induced when its lines appear in `git diff <first reviewed commit> <this commit>`,
where the first reviewed commit is the one the PR's first report names. On a first review the line
is `**Fix-induced (0):** none`. `/doc-loop` (#109) reads this line for its stop message: it tells
the person whether the fixes are making new defects.

A delta pass posts the same report, headed `## review-doc delta: <doc path> @ <reviewed
commit>..<fix commit>`. It opens with `**Findings fixed:** N of M, N answered`, where M counts
every finding of the earlier report, then lists each finding that is `not fixed`. Its "read these
closely" list holds only the calls the fix added. It has no completeness search.

The writer fixes every mismatch and every `blocking` finding, or answers each one in the PR. A
`follow-up` is fixed or filed as an issue, and the PR says which. The fix then gets the delta
pass, and that report is the earlier report for the next fix. The loop ends when no mismatch or
`blocking` finding is left that is not fixed or answered, and every `follow-up` is fixed or
filed. The person signs only after reading the report, the "read these closely" places, and a
skim of the rest. What that signature covers is set out in
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
- The skill only counts once calibrated. [`CALIBRATION.md`](CALIBRATION.md) records what earlier
  versions caught on planted errors and what they flagged on a clean doc. It is stale since #102
  changed this file; #106 calibrates the current skill. Re-calibrate after changing a pass.
