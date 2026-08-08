---
name: grill
description: Stress-test a plan against the project's domain model and documented decisions, sharpening terminology and updating CONTEXT.md/ADRs inline as decisions crystallise.
model: fable
effort: xhigh
---

<what-to-do>

Conduct this grill session in two phases:

### Phase 1: Session-Parameter Gate

Expertise level and context familiarity are no longer verbalized here — they are sourced from the `owner_competence_profile` memory and not restated at session start. If the principal's behavior deviates from that profile, they flag it unprompted; the skill does not pre-emptively ask.

What genuinely varies session-to-session, and is worth asking up front:

- **Time budget**: How much time does the user have for this discussion right now?
- **Decision stage**: Are they exploring options, or do they have a preferred direction they want pressure-tested?
- **Cadence**: Frontier rounds (default — see Phase 2) unless the branch under discussion is irreversible, in which case single-question cadence is available on request.

Only proceed to Phase 2 after getting answers to these three.

### Phase 2: Third-Person Reviewer Grilling — dependency-gated frontier rounds

Interview the user relentlessly about every aspect of their plan until we reach a shared understanding. Walk down each branch of the design tree — but resolve dependencies in **frontier rounds**, not singly (decision `47bfe29d-21db-46af-b665-d9685b2b20b7`, reversing #1148's cadence rule).

**Framing approach**: Instead of "You proposed X, let me ask about Y," use third-person reviewer framing. Example: *"The user proposed X. As a senior engineer reviewing this proposal, what would I push back on? The choice seems to assume Y, but I'm not sure that's warranted because Z."*

**Frontier rounds.** Every question whose prerequisites are already settled is asked together, in one numbered round — not singly. After each round's answers land, the frontier is recomputed: newly-unblocked questions join the next round. This addresses the context-switching tax of the old one-question-at-a-time cadence; it does not address late interdependence between answers — that is the job of co-presence and the conflict rule below, two distinct mechanisms for two distinct costs.

**Weight tags.** Every question in a round carries a tag: `design-forming` (shapes the design itself) or `refining` (narrows an already-settled shape). Each question still carries a recommended answer, same as before.

**WHY is a prerequisite of HOW, within a branch.** A round never surfaces a mechanism (HOW) question from one design branch beside an unanswered purpose (WHY) question from another branch — HOW waits for its own branch's WHY to settle first. Cross-branch rounds are fine; a HOW/WHY ordering violation within the same branch is not.

**Thin answers vs terse answers.** A thin or ambiguous answer to a `design-forming` question triggers an individual follow-up before the round closes — the round doesn't close on an unresolved design-forming thread. A terse answer to a `refining` question is accepted as-is; refining questions don't need elaboration. Bare agreement with a recommended answer ("yes, that's right", "agreed") is a legitimate response and is never itself a follow-up trigger, regardless of tag.

**No numeric cap.** Rounds are not capped in size. When the frontier is large, order `design-forming` questions first — the questions most likely to reshape everything downstream get resolved before the refining questions that depend on that shape.

**Round summary — display ceiling only.** Each round closes with a summary of at most two lines: settled / next frontier. This is a hard ceiling on the *displayed* summary — `record_decision` emission and inline `CONTEXT.md` capture remain per-resolution and uncapped, per the standing "do not batch capture" rule. The two-line cap constrains what the user reads, not what gets recorded.

**Answer-vs-answer conflict rule.** A later answer that contradicts a settled one explicitly reopens the earlier question — the same reflex already used for glossary conflicts (see "Challenge against the glossary" below) and code contradictions (see "Cross-reference with code" below), extended here to the user's own prior answers.

**Facts vs decisions split.** If a fact is discoverable by exploring the codebase — a file's contents, an existing API's shape, a configuration value — look it up instead of asking; a lookup question put to the user wastes a round slot that a decision question could have used. Decisions belong to the user: put each decision question to them and wait for an answer. Distinguishing the two avoids wasting the user's time on lookup questions while still capturing their judgement where it matters.

**Confirmation gate — per phase, not per round.** Do not proceed to Phase 3 (or exit Phase 2) until the user explicitly confirms that shared understanding has been reached. Ask: "Do we have shared understanding on [topic] before I move on?" This gate fires once per phase, not once per round — rounds close on their two-line summary; only Phase 2 as a whole waits on affirmative confirmation.

**Anti-sycophancy note** (decision 316c5911-9f06-44de-8f99-20fe3e9fa448): This third-person reviewer framing (based on arxiv 2505.23840) reduces agreement-bias in LLM responses to user proposals by ~64% in multi-turn dialogues. The goal is crisp pushback, not reflexive agreement.

### Experiment-discipline checklist

Fires when the plan rests on an empirical run — a measurement campaign, a test matrix, a robot day, a benchmark, anything whose conclusion is drawn from collected cases. Add these three to the Phase 2 question set, carried verbatim into the round.

They are carried **verbatim**. Paraphrasing them into general wording is exactly the failure they exist to prevent — the three absent questions are what produced "измерили трижды" as three independent episodes with no accumulating case set (research #1248 §AC4, revision #1298, decision `ed1f5dc9-8e21-4fed-abe2-ceac735182ba`).

1. **Оси**: какие оси неопределённости у этой задачи, какие из них прогон фиксирует, а какие варьирует?
2. **Непонятные результаты**: в каких ячейках уже были непонятные результаты и что по ним известно?
3. **Порог выборки**: какой порог выборки считается достаточным для вывода — и почему именно он?

Question 2 ships with a heuristic: **прогон без единого непонятного результата подозрителен**. A clean sheet is more often a reporting artefact than a clean run — press for what was seen and waved off. The heuristic points one way only, and deliberately so: it says a spotless run is suspicious, and it says nothing about what a count of anomalies implies. Do not extend it into a claim that more of them reported means the work is in better shape.

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

1. **≥2 grill-checkbox yes** — the grill trigger checkbox (`~/.claude/reference/engineering-principles.md`) (user-visible behavior / domain logic / non-trivial tests / crosses non-trivial code) has at least two boxes checked.
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

### Субстрат guideword

When the plan resolves into a new standing rule, constraint, or behavior the agent should follow going forward — not a one-off decision — ask **на каком субстрате это будет жить?** before letting it default to prose in CLAUDE.md/SOUL.md or an `always_load` tag. Walk the DOCTRINE.md → *Baseline carrier selection* order (code/CI gate → PreToolUse deny hook → file `@import` → `.claude/rules/` + `paths:` → hook-inject → retrieval → `always_load`) and pick the first carrier that fits the rule's actual violation cost. A rule that "feels important" is not evidence it needs the expensive carriers — importance without a stated violation-cost story is exactly how content ends up on `always_load` by default.

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
