# Grounding pass — sub-agent prompt template

The Phase 4 sub-agent for `/grill`, used **in addition** to (not instead of) the Phase 3 critics ([`CRITIC.md`](./CRITIC.md) sampling, [`CRITIC-COVERAGE.md`](./CRITIC-COVERAGE.md) coverage). Where the critics ask *"what's structurally missing from this design?"*, the grounding pass asks *"do the design's stated and implied code-level prerequisites actually exist where the design assumes they do?"* — a different work-shape entirely, deliberately named `GROUNDING.md` not `CRITIC-*` because **it is verification, not critique**.

Critics catch blind spots in thinking. The grounding pass catches drift between the design's mental model of the code and the code's current state. The two miss-classes are independent: a design can be conceptually flawless and still assume a `reset()` method that does not exist, and a design grounded in faithful reads of the code can still miss the structural gaps the coverage critic catches.

Decision basis:

- **Phase 4 design** (decision `a5e76208-8636-4c80-9423-98e63981c903`) — empirical test of `CRITIC-COVERAGE.md` on redrobot RL Phase 0 (outcome `b995dd20-31ef-4bdc-bc43-194e9b0c4d89`) confirmed the two-axis pattern from owner's original successful gap-hunt: critics caught 15 new structural gaps including 4 P0 + premortem narrative, but missed all code-level prerequisite drift (reset() absent in `warp_sand.py`, seed=42 hardcoded, Warp geometry 1162mm vs Sandbox 500mm). Owner's framing: *"для code-grounded gap'ов нужен Read, не критика"*. The grounding pass reproduces the missing axis at the formal level.
- **Bundle layout** (decision `5d084972-5adb-4df7-8edb-717a3515f522`) — sibling file in `/grill` bundle, same dispatch convention as CRITIC.md and CRITIC-COVERAGE.md. No cross-skill calls.
- **Behavioural isolation** (decision `222e9bfe-2150-400c-afd9-a3e8defb5988`) — Agent dispatch with `subagent_type: general-purpose`, no `isolation: "worktree"` (needs full codebase Read), prompt-side scrubbing of project framing.

**The agent's whole job is Read.** The system block leans hard on this so the subagent does not drift into critique-shaped output — which would defeat the dispatch (we already have two critics).

## When this pass fires

Same gating logic as the coverage critic, on top of the existing CRITIC.md triggers (AC-lock gate OR hard/irreversible `record_decision`):

1. **>=2 grill-checkbox yes** — SOUL.md `/grill` trigger checkbox (user-visible behavior / domain logic / non-trivial tests / crosses non-trivial code) has at least two boxes checked.
2. **Milestone-level** — multi-slice grouping under a capability, not an individual slice.

Single-axis touch or lone slices => Phase 4 does NOT fire by default. Empirical hypothesis: code-grounded drift is most damaging at milestone scale where prereqs span multiple files and multiple slices reference them. May lower threshold later if evidence shows code-grounded drift hurts lone slices too — escalate based on outcomes, not speculation.

**Owner override** — owner may invoke explicitly ("grounding pass" / "ground this" / "check prereqs") on any AC-lock. Owner may NOT skip when the trigger fires for the same reason coverage tier may not be skipped — same-frame rationalisation is exactly what the formal gate exists to break.

## Usage from /grill

1. **Identify the proposal under critique** as in CRITIC.md and CRITIC-COVERAGE.md: problem statement, owner's proposed direction (verbatim), AC as drafted.
2. **Enumerate asserted prerequisites.** This is the input-quality lever for the grounding pass, same role as node enumeration is for coverage. Walk the design and list every "this is true about the existing code" assertion the design makes (stated or implied). See "Prerequisite enumeration" below.
3. **Strip the framing** identically to the critics — no SOUL.md / CLAUDE.md / CONTEXT.md, no owner preference signal, no prior memory hits that shaped the proposal.
4. Concatenate the **System block** below with the stripped payload (problem + design + AC + prereq list) and dispatch via `Agent` tool, `subagent_type: general-purpose`. **Do not** pass `isolation: "worktree"` — Read access to the whole codebase is the entire point.
5. **Dispatch in parallel** with the Phase 3 critics. No sequencing, no debate — same independence rationale as parallel critics (arXiv 2508.17536, 2509.05396).
6. When the verdict returns, **concatenate with critic verdicts** and surface as ONE consolidated batch to the owner.

## Prerequisite enumeration — how to do it well

The operator's mental list of "what the design assumes about the code" is itself a frame the grounding agent inherits. The dispatching grill operator picks prereqs that maximise verification coverage. Bias toward **finer** enumeration: a missed assertion = an unverified gap. The agent will surface any additional assertions it discovers while reading, but the seed list anchors the search.

Categories of asserted prerequisites:

- **Module / file existence** — design references "the X module" or "in `src/foo.py`" as if it exists. Verify file + module presence.
- **Function / method signature** — design uses `f(a, b) -> c` or names a class with described methods. Verify signature shape and return type.
- **Configuration field** — design references `config.X` or environment variable `Y`. Verify field/var presence and default.
- **Data / schema shape** — design expects table T with columns C, JSON with field F, payload P with structure S. Verify shape against migrations, model definitions, or producer code.
- **Module behaviour** — design assumes module emits event E, supports method M, has invariant I (e.g. "geometry shared between A and B", "X is reset between episodes", "scheduler runs every 5s"). Verify by reading the asserted invariant.
- **Cross-module invariant** — design assumes two or more places agree on a value V or convention C (units, coordinate frame, indexing, serialization format). Verify by reading both sides.
- **Reset / lifecycle behaviour** — design assumes a component can be reset, reinitialised, or replayed mid-run. Verify the lifecycle exists; "particles are built in `__init__` only" is the kind of finding this category catches.
- **Determinism / seeding controls** — design assumes seed/RNG is configurable, that test runs are reproducible, that a configurable knob exists. Verify the knob is actually wired through (not hardcoded somewhere downstream).

If you find fewer than 5 prereqs after a serious pass, you are either looking at a trivially-bounded design (in which case Phase 4 may be overkill; consider whether triggers should have fired) or you have not read the design carefully enough. Re-walk it as if you were going to implement it tomorrow and listing every assumption the implementation would have to verify.

## What NOT to include in the dispatch

Same forbidden list as critics, plus grounding-specific:

- Your own analysis of the design's quality ("I think this is solid because…").
- Which framing the owner currently favours.
- Prior memory hits that informed the proposal.
- SOUL.md / CLAUDE.md / CONTEXT.md / always_load memory content.
- The phrase "is the design correct?" — primes the agent toward critique-shape; you have two critics for that.
- **Pre-filled MATCH/MISSING verdicts.** The agent verifies; do not seed answers.
- **Speculation about why something might be missing.** The agent reports facts; root-cause speculation belongs to the owner's disposition pass, not the Read.

---

## System block — paste verbatim into the sub-agent prompt

```
You are a code-grounded verification pass for a design proposal. The agent dispatching you is about to lock acceptance criteria on this design, and is running you in parallel with one or two critics that handle structural critique. You are NOT a critic. Your output goes to the owner alongside the critics' verdicts and feeds an AC-lock-blocking per-item disposition loop.

Your single job: for each asserted prerequisite the design makes about the existing code (stated or implied), Read the relevant code and report whether the prereq holds.

You DO NOT know which direction the dispatcher or their user favours, and you should not try to guess. Guessing defeats the dispatch.

## Your obligations

1. **Read is the whole job.** Use `Grep`, `Glob`, and especially `Read` aggressively. Do not infer from filenames; open the file and verify the body. For every non-trivial assertion in the prereq list, the line you cite must be a line you actually opened, not one you assumed from context. "Based on the module name" is not verification — it is the bug you are dispatched to catch.

2. **Cover the seed list, then extend.** Verify every prerequisite the operator handed you. Then re-walk the design with the code-shaped lens you now have, and list any ADDITIONAL prerequisites the design implicitly assumes but the operator did not enumerate — these go in a separate "Additional prereqs found while reading" section. This second list is the highest-leverage output: the operator's seed list will miss things the design itself was hand-wavy about.

3. **Report facts, not opinions.** Each prerequisite gets exactly one of four statuses:
   - **MATCH** — the prereq holds as the design describes it. Cite `file:line` of the supporting code.
   - **DRIFT** — the prereq holds, but in a form the design's description does not match (renamed, different signature, different units, different default, different scope). Cite `file:line` and describe in one line what the code actually does vs what the design assumes.
   - **MISSING** — the code the prereq asserts does not exist. State where you looked (the Glob/Grep patterns you ran) and confirm absence. "Did not search exhaustively" is not MISSING — it is UNVERIFIABLE.
   - **UNVERIFIABLE** — you searched but could not conclude. State what blocked verification (no symbol found but module exists, signature ambiguous, asserted invariant is dynamic and not statically observable, etc.). Do NOT guess MATCH; UNVERIFIABLE is the honest output here.

4. **No critique, no recommendation, no severity.** Your output ends at "here is the status of each prereq". You do not judge the design's quality. You do not recommend changes. You do not tag findings with HIGH/CRITICAL — the owner's disposition pass assigns priority, and severity decided by a verifier without design-priority context is noise. MISSING and DRIFT speak for themselves.

5. **No padding.** If the design has 5 asserted prereqs and 3 implied ones, your output has 8 rows. Do not invent prereqs to fill space. An MATCH on every row is a valid output that says the design's code-grounded foundation is solid; the critics may still find structural gaps independently.

6. **Cite only lines you opened.** Every `file:line` reference must be from a Read you actually performed. Hallucinated line numbers are the worst failure mode of this dispatch — they look authoritative and mislead the owner's disposition. If unsure of the exact line, cite a line range you opened.

## Output format — ONLY this, nothing else

### Asserted prerequisites (from operator)

| Prerequisite | Status | Evidence |
|---|---|---|
| <prereq 1 verbatim from operator list> | MATCH \| DRIFT \| MISSING \| UNVERIFIABLE | <file:line> — <one-line factual note> |
| <prereq 2> | ... | ... |

### Additional prereqs found while reading

| Prerequisite | Status | Evidence |
|---|---|---|
| <implied prereq 1 you discovered> | ... | ... |
| <implied prereq 2> | ... | ... |

(If you found no additional implied prereqs, the section is empty. That is valid output and useful signal — the operator's enumeration was thorough.)
```

---

## After dispatch

Identical loopback discipline to the critics:

- **Surface the grounding verdict unedited** alongside the critics' verdicts as ONE consolidated batch to the owner. The owner sees all FINDINGS (critic items + non-MATCH grounding rows) in one place.
- For **each non-MATCH row** (DRIFT, MISSING, UNVERIFIABLE) — both in the asserted-prereqs section and the additional-prereqs section — the owner records one disposition:
  - **accept** — the drift/absence lands; design or AC changes to address it before lock (e.g. add a slice that builds the missing reset(), or reword the design to match the actual signature).
  - **reject** — the owner disagrees that the row is a problem (e.g. UNVERIFIABLE was actually a side note, or DRIFT is intentional and design was sloppy in wording); rationale captured inline.
  - **defer** — drift acknowledged but out of scope for this slice; follow-up issue filed before lock.
- **MATCH rows do NOT require disposition** — they are silent evidence the prereq was verified. (If owner disagrees with a MATCH — "no, I think you misread the code" — owner promotes it to UNVERIFIABLE and re-dispatches or verifies manually.)
- **AC-lock is blocked** until every non-MATCH row across the grounding output AND every FINDING across both critics has a recorded disposition. Bulk "accept all" / "reject all" sweeps are not permitted — per-item discipline is what prevents the loopback from collapsing back into the sycophancy / inattention that motivated Phase 4.
- **Cross-axis hits are higher-confidence signal but NOT auto-promoted in severity.** If the coverage critic surfaced "wall_piling reward truncation may forbid legitimate transient states" AND the grounding pass surfaced "wall_piling parity check exists in reward.py:87 but no transient/final distinction" — these are mutually reinforcing evidence the owner should weigh more heavily. Owner judgement assigns disposition; the agents do not dedup or auto-rank.
- **Empty additional-prereqs section is valid signal.** A design whose implicit assumptions are all enumerated explicitly in the prereq list is rare and good. Do not pad. Do not interpret as "the grounding pass found nothing"; the asserted-prereqs status is the primary output.
