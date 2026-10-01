# ADR-0003: The doc contract, delivery by a persistent clone, and a shadow-mode reviewer

Date: 2026-10-01. Status: accepted. Tracking: #159. Decided in the 2026-09-29 grill that locked
the slice-1 acceptance criteria (#158–#179). Issue bodies from that grill call this record
"ADR 0002 from #159"; the number moved because ADR-0002 (#194) was written first.

## Context

The repo's product is a set of practice docs plus an entry skill that walks a reader through
them and configures their repo. Before slice 1, a doc had a free shape. Its facts were copied
magnitudes such as prices and limits. A `Status: tried` / `Status: sourced` marker said where
a claim came from. The skill reached the reader by an install step that fetched files.

Three questions came up together:

1. Which shape must a doc have, so that the skill can route by it and a gate can check it?
2. How do the skill and the docs reach the reader, so that both are current and from one commit?
3. Who reads a new doc, given that review-doc is not yet calibrated to replace a human reading?

Each question has an answer below, with the alternatives the grill rejected. Where the answer
is enforced, the enforcing code is named. This record states why the answers hold, not how the
code implements them.

## Decision

### 1. The doc contract

Enforced by [`tests/structure_gate.py`](../../tests/structure_gate.py) (#160, #162), described
for writers in [`write-doc`](../../.agents/skills/write-doc/SKILL.md) and for readers in
[`doc-structure-gate.md`](../doc-structure-gate.md). `docs/adr/` is outside it.

1. **`kind: practice | hub | basics`.** This is the doc's type, a closed set of three. It is not
   a migration marker. Docs migrate one at a time. A doc that declares `kind:` meets the new
   checks, and a doc that does not stays under the old ones until the last contract PR makes
   `kind` required.
2. **`requires: basics#ci, basics#hooks`.** This is a flat, comma-separated list read by the
   existing frontmatter parser. Each anchor must resolve to a heading in its target file. The
   same resolver checks anchor links in Relations lines and in the doc body.
3. **`hub: <hub doc>`.** A child names its hub. The gate fails unless the hub's `## Children`
   list equals the set of docs that name it. A hub needs no options section.
4. **A fixed option heading prefix.** A practice doc has `## The options`, and each option is
   `### Option <N>. <title>`. The gate keys on this prefix. The rest of the doc's shape is free.
5. **Check.** Each option section has a `#### Check` block. It holds steps that tell the reader
   whether the option is in place, or `None — <reason>`. A check ends `pass`, `fail` or
   `unverified`, and `unverified` is never shown as green.
6. **Relations.** Each option section has one `Relations:` line with `needs:`, `excludes:` and
   `trade-off:` clauses, or `Relations: none`. The anchors in it must resolve. The entry skill
   orders options by `needs` and shows each `trade-off` when the reader chooses.
7. **The plan-name ban.** Plan names, meaning subscription tiers and forge plans, appear only in
   `docs/harnesses.md`. That file holds the one mapping from plan to capability. Every other
   doc states the capability it needs in open phrasing, and there is no registry.
8. **Facts are a link plus a check date.** An external link is followed by
   `(checked YYYY-MM-DD)`, or `(checked YYYY-MM-DD, volatile)` for a fact that changes often.
   A doc does not copy prices or limits. The date expires after about 90 days for a volatile
   fact and about 365 for a stable one, and a weekly job lists expired dates (#174). A doc holds
   the step skeleton: what to do, in which file or setting, and its Check. The link supplies
   the current syntax.
9. **No `Status:` marker.** A claim cites its source. A claim that we ran something is a
   sourced claim whose source is a trace in this repo: an example, a commit, an issue or a test.

### 2. Delivery

Built in #161.

1. `npx skills add` installs a thin shim. On its first run, the shim clones jarvis-oss into a
   persistent per-user location. On every later run it runs `git pull` there. It then hands off
   to the clone's `SKILL.md`.
2. The skill and the docs it reads come from one commit, and the skill records which one.
3. There is no curl fallback and there are no installer scripts. The README lists Node and git
   as requirements. It states that a harness without a shell is unsupported, and documents the
   one permission prompt for reading outside the working directory.

### 3. The shadow-mode reviewer

1. **Shadow mode.** During slice 1, the operator reads every new doc in full before clearing
   `waiting-human-review`. That reading is the acceptance: the merge, once the hold is cleared,
   accepts the doc (#217 removed the separate sign-off). In parallel, a ranker (#164) extracts
   the doc's judgement claims at the PR head SHA and ranks them, and its top k is what a human
   would read closely. Each human finding gets a row in the shadow record
   ([`RULES.md`](../../.agents/skills/review-doc/calibration/RULES.md#shadow-record)). The row
   says whether the finding was blocking, where it came from (`author-read` or `click-audit`),
   and whether it fell in the ranker's top k on that SHA.
2. **The switch rule, registered before any data.** Reading switches from every doc in full to
   the top k only when the share of blocking findings that fell in the top k has a Wilson 95%
   lower bound of at least 0.7, at n ≥ 10. With every finding a hit, the smallest n whose bound
   clears 0.7 is 9. The cut at 10 is a round margin above that, not a computed value.
3. **Metrics.** Before the switch, an escape is a human finding outside the top k. After it,
   escapes come from a random audit sample below k plus reader reports. The ranker's claim
   extraction and k are frozen in CALIBRATION.md before the first slice-1 doc is ranked, and a
   false-block rate (precision) is reported next to the share.
4. **Calibration 3 starts from real defects.** Its pool takes the defects shadow mode records
   first, and seeded defects only if the pool is short by a date set in advance. The
   pre-registration sets a USD ceiling, $300 by default (#177).
5. **review-doc changes once.** Every review-doc `SKILL.md` and `RULES.md` edit that slice 1
   needs lands in #159, so the drift key changes once. During slice 1 a `drift` verdict is
   accepted: doc-review is not a required check, and its report is still produced.

## Alternatives rejected

**Contract**

- **Leave the gate red until the last doc migrates.** The structure gate is a required check
  with `enforce_admins`, so a red gate on the whole tree blocks every merge, including the
  migration PRs.
- **A closed capability registry.** It needs a new entry every time a doc meets a new
  capability.
- **Plan names in `applies_when`.** A plan change would then force edits across docs instead
  of one row in `harnesses.md`.
- **Nested YAML for `requires`.** It adds a parser dependency for one key. A YAML library comes
  in only when a second parser need appears.
- **A fully fixed option shape.** Only the option heading prefix is what the gate and the skill
  need to find options. The rest of the shape stays the writer's call.
- **Copying magnitudes such as prices and limits into the doc.** A plan change then forces a
  rewrite, and a stale copy reads as current.
- **Our own step-by-step instructions with full syntax.** This is the text that goes stale
  fastest. The link supplies current syntax, and our doc keeps the skeleton and the Check.
- **Keeping `Status: tried / sourced`.** A `tried` claim is a claim whose source is our own
  trace, so a separate marker only adds a second thing to keep true, plus a claim class and a
  click-audit branch to check it.

**Delivery**

- **A model-backed fetch, where the agent reads the docs over the web.** The agent gets a
  paraphrase, and the skill and the docs can come from different commits.
- **A manual clone by the reader.** It is one more step for the reader, and the clone goes
  stale.
- **A temporary `git clone --depth 1` with a curl fallback.** This was the grill's earlier
  answer. The fallback is a second delivery path to keep working, and a temporary clone is
  fetched again on every run.
- **Installer scripts.** They rot on every change to a harness or a plan.

**Reviewer**

- **Top-k-only reading from day one.** Nothing was measured yet. The ranker's top k would
  decide what a human never reads, on no evidence.
- **Seeded defects first.** Planted defects are not the defects real readers find. They stay a
  fallback for a pool that is short.
- **Recalibrating review-doc after each slice-1 edit.** Each edit voids every recorded verdict.
  One change with drift accepted costs one calibration, not one per edit.

## Consequences

- A slice-1 doc PR gets a red `doc-review` (`drift`) until calibration 3 records a new key.
  The red verdict blocks nothing, because doc-review is not a required check, and the report
  still lists findings. `test_committed_key_matches_the_current_inputs` is marked as an
  expected failure (strict) for the same window. It goes red the day a recalibration makes
  the key match, and that is when the mark comes off.
- The operator's full reading is the acceptance for every slice-1 doc, so it costs time. Shadow
  mode is the only way that cost ends, by meeting the switch rule.
- Status leaves review-doc here. The docs, the examples, the corpus wording and write-doc follow
  in #166.
- A doc written before the contract is under the old rules until it declares `kind:`.
