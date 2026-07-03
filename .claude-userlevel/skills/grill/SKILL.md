---
name: grill
description: Grilling session that challenges your plan against the existing domain model, sharpens terminology, and updates documentation (CONTEXT.md, ADRs) inline as decisions crystallise. Use when user wants to stress-test a plan against their project's language and documented decisions.
model: opus
effort: xhigh
---

<what-to-do>

Conduct this grill session in two phases:

### Phase 1: Assumption Verbalization

Before asking any WHY/HOW questions, calibrate expectations by writing out your assumptions about the user:

- **Expertise level**: What's your estimate of the user's experience in this domain?
- **Time budget**: How much time do you think the user has available for this discussion?
- **Context familiarity**: How much relevant context do you assume the user already has?
- **Decision stage**: Are they exploring options, or do they have a preferred direction they want pressure-tested?
- **Scope constraints**: Are there organizational, technical, or deadline constraints you should assume?

Ask the user: **"Are these assumptions right, or should I adjust? Anything I'm off base about?"**

Only proceed to Phase 2 after getting feedback.

### Phase 2: Third-Person Reviewer Grilling

Interview the user relentlessly about every aspect of their plan until we reach a shared understanding. Walk down each branch of the design tree, resolving dependencies between decisions one-by-one. For each question, provide your recommended answer.

**Framing approach**: Instead of "You proposed X, let me ask about Y," use third-person reviewer framing. Example: *"The user proposed X. As a senior engineer reviewing this proposal, what would I push back on? The choice seems to assume Y, but I'm not sure that's warranted because Z."*

Ask the questions one at a time, waiting for feedback on each question before continuing.

If a question can be answered by exploring the codebase, explore the codebase instead.

**Anti-sycophancy note** (decision 316c5911-9f06-44de-8f99-20fe3e9fa448): This third-person reviewer framing (based on arxiv 2505.23840) reduces agreement-bias in LLM responses to user proposals by ~64% in multi-turn dialogues. The goal is crisp pushback, not reflexive agreement.

### Research-pass gate (precondition to Phase 3)

Before entering the CRITIC subagent phase, check whether a recent 4-channel
research artifact exists for the current topic. The gate fires only for
**high-stakes** decisions — those whose `reversibility` is `{hard, irreversible}`
OR `confidence < 0.7`.

**Procedural source: [`../_shared/research-pass-gate.md`](../_shared/research-pass-gate.md).**

Load and execute the procedure there. If the gate blocks:
- Propose running `/research` on the current topic first
- Do not proceed to Phase 3 until research completes or owner explicitly waives

Low-stakes decisions (reversible AND confidence >= 0.7) skip this gate entirely.

### Phase 3: Cross-context review (CRITIC subagents)

Single-agent self-critique grades its own exam. Personalisation measurably increases sycophancy (MIT 2026, ICLR 2026); same-session self-review has a 64.5% blind-spot rate across 14 models (arXiv 2506.04907); fresh-context review measurably beats same-session (CCR F1 28.6 vs 24.6, arXiv 2603.12123). Phase 3 dispatches sibling subagent(s) — each operating as a **role-isolated critic** without SOUL.md, always_load memory, or project calibration in its prompt — to critique the proposal cold.

Two tiers exist; they target different blind-spot classes and may both run on the same AC-lock:

- **Sampling tier** — narrative critique with a fixed ceiling (≤3 risks + ≤3 alternatives + 1 assumption). Catches obvious load-bearing risks the proposer fluffed. Template: [`CRITIC.md`](./CRITIC.md).
- **Coverage tier** — Cartesian guideword sweep with mandatory per-cell disposition (SHARD data-flow guidewords × every node, STPA UCA decision guidewords × every node, Key Assumptions Check, Premortem inversion). Catches the systematic blind spots that proposer and reviewer share by virtue of co-occupying the frame. Template: [`CRITIC-COVERAGE.md`](./CRITIC-COVERAGE.md). Rationale and research basis in that file.

When both tiers fire on the same AC-lock, dispatch in **parallel** (independent subagents, no debate chain — debate ≤ majority vote in expectation per arXiv 2508.17536, and conformity degrades correct answers per arXiv 2509.05396).

#### Triggers

**Sampling tier (CRITIC.md) — exactly two** (decision c29c2b00-e9e1-43d1-93ff-ada5820c434c):

1. **AC-lock gate** — immediately before the grill session would commit acceptance criteria to the issue body / CONTEXT.md / record_decision chain. This is the highest-leverage gate; most critique value lands here.
2. **`record_decision` with `reversibility ∈ {hard, irreversible}`** — every hard or irreversible decision the grill is about to emit. Catches architectural calls the AC-lock gate alone would miss when the decision precedes AC formation.

WHY→HOW and HOW→AC mid-session checkpoints were considered and **rejected as ceremony** in the same decision — they add critique cost without distinct leverage past the two triggers above. Do not add them as triggers.

**Coverage tier (CRITIC-COVERAGE.md) — fires in addition when BOTH hold** (decision 44a72728-b622-42e3-b7b9-3a52b268b4ba):

1. **≥2 grill-checkbox yes** — the SOUL.md `/grill` trigger checkbox (user-visible behavior / domain logic / non-trivial tests / crosses non-trivial code) has at least two boxes checked.
2. **Milestone-level** — the design under critique is a milestone PRD or equivalent grouping of slices, not an individual slice. Per CLAUDE.md milestone-vs-slice hygiene.

Single-axis touch or lone slices ⇒ sampling tier only. Owner may invoke coverage tier explicitly ("coverage critic" / "deep critic") on any AC-lock but MAY NOT skip it when the trigger fires — that's exactly the same-frame rationalization the coverage tier exists to break.

#### Context scrubbing — behavioural, not structural

Dispatch each critic via the `Agent` tool with `subagent_type: general-purpose`. **Do not** pass `isolation: "worktree"` — that would block the codebase + memory tools the critic needs to ground its critique. Instead, scrub by **what you put in the prompt**, mirroring the precedent in [`reason/NEUTRAL-RESEARCHER.md`](../reason/NEUTRAL-RESEARCHER.md):

- Forward: the problem statement, the owner's proposed direction (verbatim), the acceptance criteria as drafted. **For the coverage tier additionally**: the node enumeration (see CRITIC-COVERAGE.md "Node enumeration" section).
- Omit: which side of any disagreement the operator favours, prior memory hits used to shape the proposal, SOUL.md / CLAUDE.md / CONTEXT.md content, any "I think…" framing.
- The behavioural nudge in each critic's system block does the rest. Isolation here is **behavioural, not structural** — a known limitation, sufficient for routine bias prevention (same trade-off as NEUTRAL-RESEARCHER; worktree isolation would lose access to project memory the critic still needs for grounded critique).

#### Loopback — forced per-item disposition blocks AC-lock

Sampling critic returns ≤3 risks + ≤3 alternatives + 1 assumption (see CRITIC.md). Coverage critic returns Cartesian grids + assumptions list + premortem narrative (see CRITIC-COVERAGE.md). When both tiers run, **surface BOTH verdicts unedited to the owner as one consolidated batch**.

For **each FINDING** across both critics — every item the sampling critic returned, plus every non-N/A cell in the coverage grids, plus each populated assumption, plus the premortem narrative — owner records one of three dispositions:

- **accept** — owner agrees the critique lands; the proposal/AC changes to address it before lock.
- **reject** — owner explicitly disagrees with the critique; rationale captured inline.
- **defer** — owner acknowledges the critique is valid but out of scope for this slice; a follow-up issue is filed before lock.

N/A cells in coverage grids do NOT require disposition — they are silent evidence the cell was considered. (Owner may promote an N/A to FINDING if they disagree.)

Per-item disposition is **mandatory** and **blocks AC-lock**: the grill cannot proceed to commit AC, write CONTEXT.md updates, or emit `record_decision` until every FINDING across both critics has a recorded disposition. Bulk "accept all" / "reject all" sweeps are not permitted — the per-item discipline is what keeps the loopback from collapsing back into sycophancy at the wording layer.

Cross-critic hits (sampling and coverage both surface the same risk) are higher-confidence signal but are NOT automatically promoted in severity — owner's judgement applies. Dedup is the owner's job, not the critics'.

When Phase 4 (Grounding pass) also fires on the same AC-lock, its non-MATCH rows fold into the same per-item disposition pass — see Phase 4 below.

### Phase 4: Grounding pass (code-grounded verification)

Critics in Phase 3 are spec-bound by construction: they ask *"what is structurally missing or unexamined in this design?"* They cannot reliably catch the second class of blind spot — drift between the design's mental model of the codebase and the codebase's current state. Owner framing: *"для code-grounded gap'ов нужен Read, не критика"* (see outcome `b995dd20-31ef-4bdc-bc43-194e9b0c4d89` — empirical test of Phase 3 alone on redrobot RL Phase 0 caught 15 structural gaps including 4 P0 + premortem narrative, but missed all code-level prereq drift: `reset()` absent in `warp_sand.py`, `seed=42` hardcoded, Warp 1162mm vs Sandbox 500mm geometry mismatch).

Phase 4 dispatches a **separate Read-shaped subagent** (template: [`GROUNDING.md`](./GROUNDING.md)) that verifies the design's asserted and implied code-level prerequisites: for each, reports MATCH / DRIFT / MISSING / UNVERIFIABLE with `file:line` citation. Verification is a different work-shape from critique — the file is deliberately `GROUNDING.md`, not `CRITIC-*`, because mixing the two dilutes both.

**Triggers** (decision `a5e76208-8636-4c80-9423-98e63981c903`) — same gating logic as Phase 3 coverage tier, on top of the base CRITIC.md triggers:

1. **≥2 grill-checkbox yes**
2. **Milestone-level** design (not lone slice)

Owner may invoke explicitly ("grounding pass" / "ground this" / "check prereqs"); may NOT skip when the trigger fires. Same same-frame rationalization concern that gates coverage tier.

**Dispatch**: parallel with Phase 3 critics, `subagent_type: general-purpose`, **without** `isolation: "worktree"` (Read access to the whole codebase is the entire point). Operator forwards the same stripped payload as the critics PLUS an enumerated list of asserted prerequisites (see GROUNDING.md "Prerequisite enumeration" section — this is the input-quality lever for Phase 4, mirroring node enumeration's role for coverage tier).

**Loopback**: Grounding output (asserted-prereqs table + additional-prereqs-found-while-reading table) consolidates into the SAME owner disposition pass as Phase 3 verdicts. Each non-MATCH row (DRIFT, MISSING, UNVERIFIABLE) requires one of `accept` / `reject` / `defer` before AC-lock can proceed. MATCH rows are silent evidence the prereq was verified — no disposition required. Cross-axis hits (a coverage critic finding reinforced by a grounding DRIFT, or vice versa) are higher-confidence signal but NOT auto-promoted in severity — owner judgement, owner dedup.

</what-to-do>

<supporting-info>

## Domain awareness

During codebase exploration, also look for existing documentation:

### File structure

Most repos have a single context:

```
/
├── CONTEXT.md
├── docs/
│   └── adr/
│       ├── 0001-event-sourced-orders.md
│       └── 0002-postgres-for-write-model.md
└── src/
```

If a `CONTEXT-MAP.md` exists at the root, the repo has multiple contexts. The map points to where each one lives:

```
/
├── CONTEXT-MAP.md
├── docs/
│   └── adr/                          ← system-wide decisions
├── src/
│   ├── ordering/
│   │   ├── CONTEXT.md
│   │   └── docs/adr/                 ← context-specific decisions
│   └── billing/
│       ├── CONTEXT.md
│       └── docs/adr/
```

Create files lazily — only when you have something to write. If no `CONTEXT.md` exists, create one when the first term is resolved. If no `docs/adr/` exists, create it when the first ADR is needed.

## During the session

### Challenge against the glossary

When the user uses a term that conflicts with the existing language in `CONTEXT.md`, call it out immediately. "Your glossary defines 'cancellation' as X, but you seem to mean Y — which is it?"

### Sharpen fuzzy language

When the user uses vague or overloaded terms, propose a precise canonical term. "You're saying 'account' — do you mean the Customer or the User? Those are different things."

### Discuss concrete scenarios

When domain relationships are being discussed, stress-test them with specific scenarios. Invent scenarios that probe edge cases and force the user to be precise about the boundaries between concepts.

### Cross-reference with code

When the user states how something works, check whether the code agrees. If you find a contradiction, surface it: "Your code cancels entire Orders, but you just said partial cancellation is possible — which is right?"

### Update CONTEXT.md inline

When a term is resolved, update `CONTEXT.md` right there. Don't batch these up — capture them as they happen. Use the format in [CONTEXT-FORMAT.md](./CONTEXT-FORMAT.md).

Don't couple `CONTEXT.md` to implementation details. Only include terms that are meaningful to domain experts.

### Remove `needs-grill` on success

When `/grill` resolves an issue's open questions and the AC is updated with verifiable bullets + decision UUIDs (whether inline or via working_state), remove the issue's `needs-grill` label as the final terminal step:

```bash
gh issue edit <N> --repo <owner/repo> --remove-label "needs-grill"
```

This is the contract that lets `/delegate`'s pre-dispatch gate (issue #642) trust that an unlabelled issue is genuinely grill-clean. Skipping the removal leaves the issue stuck in `status:owner-queue` forever. If `/grill` exits without resolution (owner walks away mid-session), leave the label in place — the issue still needs work.

### Offer ADRs sparingly

Only offer to create an ADR when all three are true:

1. **Hard to reverse** — the cost of changing your mind later is meaningful
2. **Surprising without context** — a future reader will wonder "why did they do it this way?"
3. **The result of a real trade-off** — there were genuine alternatives and you picked one for specific reasons

If any of the three is missing, skip the ADR. Use the format in [ADR-FORMAT.md](./ADR-FORMAT.md).

</supporting-info>
