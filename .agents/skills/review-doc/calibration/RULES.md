# Calibration rules for review-doc

These rules say how a defect the review missed is labelled and counted. They were committed before
the corpus, so no rule here was fitted to a result. The corpus will be `corpus.md` in this
directory (#101). Seeded defects, planted to test the reviewer, go in a separate file and never mix
with found ones.

This file sits outside the drift key, which hashes `SKILL.md` only. Editing it does not start a new
calibration window. That is why it is guarded as machinery (#97) and changed only as described in
the last section.

Where this file and `SKILL.md` disagree on `blocking` or `unverifiable`, this file wins for corpus
labels. `SKILL.md` will point here once #102 lands.

## Escaped

A round is one full review of a doc, plus the fix commits and delta passes that answer it.

A defect escaped round N when it is found in round N+1 in text unchanged since round N.

- Round N is the latest round that had the defective text in scope: a full review, or a delta pass
  whose diff touched those lines.
- "Unchanged" is checked with `git diff <round N commit> <finding commit> -- <doc>`. The defective
  lines must not appear in it.
- If round N reported the defect, it was caught, not escaped, even if the fix was wrong.
- If the text changed after round N, the defect was introduced later. It is not an escape from
  round N; it counts against the round that next had the changed text in scope.

## Defect classes

Each entry gets exactly one class. If two fit, take the first in this list.

- `status` — a wrong `tried` or `sourced` status: `tried` with no trace of the run, `sourced` with
  no source that says it, or the reverse.
- `quote` — a quotation that is not verbatim in its source, or is credited to the wrong source.
- `plan` — the doc leads a reader type to an option that is not available on that reader's plan:
  a paid feature for a reader without money, a team feature for a solo reader, an attended step in
  an unattended run.
- `fact` — a number, version, limit, tool behaviour or link target that disagrees with its source,
  outside a quotation.
- `dead-end` — a reader type the doc covers is left with no next step.
- `missing-option` — a real option for an in-scope reader type is not in the doc.
- `how-to-choose` — the options are right but the doc's way of choosing between them is wrong or
  missing for a reader type.
- `other` — anything else the value test catches. Each `other` entry says in one line why no class
  above fits. Three `other` entries of one kind mean this list needs a new class.

## Blocking or follow-up

Every entry is labelled `blocking` or `follow-up`: the label its finding should have had if the
round had caught it.

`blocking` means the doc leads an in-scope reader type to an option that (i) is unavailable on
that reader's plan, (ii) contradicts a quoted source, or (iii) leaves the reader with no next
step; plus every claim-check `mismatch`; plus every wrong `tried` / `sourced` status.

A `missing` option is a follow-up unless it triggers (iii).

So:

- `status`, `quote` and `fact` entries are always `blocking`: each is a claim-check `mismatch`
  or a wrong status.
- `plan` is clause (i) and `dead-end` is clause (iii); both are `blocking`.
- `missing-option` is `follow-up`, unless the missing option was the only next step for some
  reader type. Then it is (iii) and `blocking`.
- `how-to-choose` and `other` are `blocking` only if the entry names the in-scope reader type and
  shows how the doc leads it into (i), (ii) or (iii). Otherwise `follow-up`.

In-scope reader types are the ones the doc's `applies_when` and scope section cover: solo or a
team of up to three, with or without money, attended or unattended.

## When a fallback line satisfies (iii)

A doc often ends a section with a fallback: "if none of these fit, do X". A fallback line
satisfies (iii) only if it gives each reader type that would otherwise have nothing a concrete next
step that is available on that reader's plan: a named option, tool or action, and where to read
more.

It does not satisfy (iii) when:

- it is generic: "it depends", "consult the docs", "ask your team", "evaluate your needs";
- the step it names is unavailable on that reader's plan (that is clause (i) instead);
- it covers some reader types and silently drops another.

## `unverifiable`

At review time, a claim is `unverifiable` when its source cannot be checked: the page is
unreachable, it is paywalled, or the doc names no source. A `tried` status with no trace is not
`unverifiable`; it is a `mismatch`.

- An `unverifiable` claim is `blocking` when it is load-bearing: it is the stated reason an option
  is ruled in or out for a reader type. The writer then links a source that can be fetched, marks
  the claim as unsourced, or drops it.
- Any other `unverifiable` claim is a `follow-up`.

In the corpus, an `unverifiable` finding is not an entry by itself. It becomes one only if the
claim is later shown to be wrong. The entry then takes the class of what was wrong. It counts as
caught if that round's `unverifiable` finding was `blocking`, and as missed if it was a
`follow-up` or absent.

## Held-out

A defect that drove an edit to the reviewer skill is held out. The reviewer was tuned on it, so
counting it would overstate the reviewer.

- An entry is held out when the PR, commit or issue that edits the reviewer skill (`SKILL.md` or
  anything it loads) names the defect, its corpus entry or its finding.
- The entry is flagged `held_out: yes` with `held_out_by:` set to that PR or commit.
- A held-out entry is never un-flagged, even if the skill edit is later reverted.
- Held-out entries are excluded from the published counts: caught, missed and n. They are listed
  after the counts, with their own number, so nothing is hidden.

Published figures are a floor on same-model agreement, not a recall figure.

## Selection source

Each entry records how the defect was found, as exactly one of:

- `model` — a review round found it: a full review or a delta pass, on any later commit.
- `click-audit` — the human found it while checking a drawn claim (next section).
- `reader` — a reader's `doc-error` report, or any human find outside the draw. It enters the
  corpus only after the human opens the source and confirms it.

A defect found twice keeps the source of the earliest find.

## Corpus entry fields

`corpus.md` (#101) holds one entry per defect with:

- source PR and the commit the defect was in;
- location: doc path and line at that commit;
- class, from the list above;
- label: `blocking` or `follow-up`;
- `held_out: yes` or `no`, and `held_out_by:` when yes;
- selection source;
- escape evidence: the round N commit and a link to the finding in round N+1.

## Click-audit

A click-audit is a human check of claims drawn at random. It guards clauses (i) and (ii), which a
same-model reviewer can miss the same way the writer did.

1. **When.** Once per doc, on the PR where the doc passes review, before the human merges it.
2. **How many.** k = 5 claims, or all of them if there are fewer.
3. **The draw.** The claims are drawn by a script seeded with the PR head SHA:

   ```
   gh pr view <n> --json headRefOid --jq .headRefOid
   python .agents/skills/review-doc/calibration/draw_click_audit.py docs/<doc>.md --sha <head SHA>
   ```

   The candidates are every quotation, every `tried` / `sourced` status, and every other paragraph
   that links a source. Each is ranked by SHA-256 of the SHA, the doc path and the claim; the k
   lowest are drawn and the next k are reserves. Anyone can rerun it and get the same list. The
   agent may run the command and post its output, but does not choose, drop or reorder claims. If
   the head SHA moves before merge, the draw is redone on the new SHA.
4. **The human.** For each drawn claim, the human opens each source link and compares the quote,
   or the claim, with what the source says. Each claim is marked `holds`, `miss`, or `unreachable`.
   An `unreachable` claim is replaced by the next reserve.
5. **The record.** The results go in a PR comment, one line per claim. A miss goes into the corpus
   with selection source `click-audit`, the head SHA as its commit, and its class and label from
   the rules above. The writer then fixes it on the PR.
6. **Revisiting k.** k is revisited after the first audited miss. A dated line under this list
   records whether k stays at 5 or changes, and why, before the next audit.

Limits of the draw:

- A claim with no quotation, no status and no link is not a candidate. Pass 2 of the review still
  checks it.
- The draw covers the doc file only, not the examples it links to.
- A new commit changes the draw. The audit that counts is the one on the head SHA that is merged.

## Changing this file

A change to these rules is its own PR, merged by the human. The PR lists every corpus entry whose
label, class or count would change under the new rule, and relabels them in the same PR, so the
diff shows what the change did to the figures.
