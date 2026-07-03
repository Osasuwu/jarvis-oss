# Coverage CRITIC — sub-agent prompt template

The second-tier critic for `/grill` Phase 3, used in **addition** to (not instead of) [`CRITIC.md`](./CRITIC.md) when the design has high blind-spot surface. Where `CRITIC.md` **samples** the gap space (`<=3 risks + <=3 alternatives + 1 assumption`, narrative critique), this critic **covers** it: a Cartesian sweep of fixed guidewords across every node in the design, with mandatory explicit disposition of every cell.

Both critics dispatch in parallel as fresh-context subagents; their findings concatenate into the AC-lock loopback. The two tiers find different things — the sampling critic catches obvious load-bearing risks the proposer fluffed; the coverage critic catches the systematic blind spots both proposer and reviewer share by virtue of co-occupying the same frame.

Decision basis:

- **Two-tier design** (decision `44a72728-b622-42e3-b7b9-3a52b268b4ba`) — coverage tier added to /grill Phase 3 as second dispatch, gated separately; sampling tier preserved for normal AC-locks.
- **Mechanism** — guideword-driven coverage (HAZOP/SHARD/STPA UCA lineage) x fresh-context independent critics. Research-backed: same-session self-review measured at 64.5% blind-spot rate across 14 models (arXiv 2506.04907); fresh-context review measurably beats same-session (CCR F1 28.6 vs 24.6, arXiv 2603.12123); diversity is the active ingredient, NOT debate (debate <= majority vote in expectation, conformity degrades correct answers -1..-12%, arXiv 2508.17536 / 2509.05396). Memory: `research_unknown_unknowns_blindspot_methods_2026_05_23` (UUID 24fcc85a-6eb0-4094-a902-fef9b27e718a).

**Isolation is behavioural, not structural** — same constraint as `CRITIC.md`. The Agent tool subagent technically has access to project memory and codebase (which it needs for grounded findings); the behavioural nudge in the system block keeps it from defaulting to project-aligned framing.

## When this critic fires (in addition to CRITIC.md)

Both of the following must hold, on top of the existing CRITIC.md triggers (AC-lock gate OR hard/irreversible record_decision):

1. **>=2 grill-checkbox yes** — the SOUL.md `/grill` trigger checkbox (user-visible behavior / domain logic / non-trivial tests / crosses non-trivial code) has at least two boxes checked. Single-axis touch (e.g. pure refactor with tests) does not justify the cost.
2. **Milestone-level** — the design under critique is a milestone PRD or equivalent (multiple slices grouped under a capability), not an individual slice. Per CLAUDE.md milestone-vs-slice hygiene.

For lone slices or low-surface designs, ONLY `CRITIC.md` runs. This is intentional: coverage costs N subagents + dedup, justified only when the surface is large enough to make sampling unreliable.

**Owner override** — owner may invoke this tier explicitly on any AC-lock by saying "coverage critic" / "deep critic" before AC-lock. Owner may NOT skip it when the trigger above fires — that's exactly the rationalization (`мало изменение`, `я уже всё продумал`) the research shows is unreliable in same-frame review.

## Usage from /grill

1. Identify the proposal under critique as in `CRITIC.md`: problem statement, owner's proposed direction (verbatim), AC as drafted. **In addition**, enumerate the **nodes** of the design — the units against which guidewords will be swept. Node enumeration is the single load-bearing input quality lever; mis-enumeration here = coverage gaps in disguise.
2. **Strip the framing** identically to `CRITIC.md`. Coverage critic gets the SAME stripped payload, dispatched separately.
3. Concatenate the **System block** below with the stripped payload (including the node list) and dispatch via the `Agent` tool, `subagent_type: general-purpose`.
4. Dispatch in **parallel** with the sampling critic (`CRITIC.md`). Do not chain — debate-like sequencing would amplify conformity per the research; parallel keeps the finding distributions independent.
5. When BOTH verdicts return, surface BOTH unedited to the owner. Owner runs per-item disposition over the union of findings before AC-lock proceeds.

## Node enumeration — how to do it well

The proposer's mental decomposition of the design into nodes is itself a frame the critic must NOT inherit blindly. The dispatching grill operator picks the node taxonomy that maximises coverage, biased toward **finer rather than coarser** granularity at the cost of more empty cells (empty cells are signal: "we considered guideword G on node N and it doesn't apply"). Pick from:

- **Software/system designs** — modules, data flows between modules, externally-visible interfaces. One node per module + one node per data flow. Interfaces with the outside world (other systems, users, time) each count as separate nodes.
- **Algorithm / ML designs** — training-time decisions (data, loss, optimiser, schedule), inference-time decisions (input preprocessing, postprocessing, runtime guards), reward / target signals, failure modes (degraded inputs, distribution shift, adversarial inputs). One node per decision class + one node per signal.
- **Process / workflow designs** — actors, decision points, state transitions, hand-offs between actors. One node per actor + one node per transition.

If the design has fewer than 4 nodes after enumeration, the design is either (a) too coarse to grill at this depth — return the design to the proposer asking for finer decomposition; or (b) genuinely too small for coverage — defer to sampling-tier only.

## What NOT to include in the dispatch

Same forbidden list as `CRITIC.md`:

- Your own analysis or which direction the owner favours.
- SOUL.md / CLAUDE.md / CONTEXT.md / always_load memory content.
- Prior memory hits that shaped the proposal.
- "Is this a good design?" framing.

Additional, specific to coverage:

- **Do not pre-fill the guideword grid.** The whole point is the subagent fills every cell from scratch with its own frame; pre-filling biases. Pass the node list, not a half-filled table.
- **Do not score subagent verdicts against each other across runs.** Each coverage pass is independent. Variance between runs is signal about which cells are robustly findings vs. noise.

---

## System block — paste verbatim into the sub-agent prompt

```
You are a coverage critic running a systematic Cartesian sweep over a design proposal. The agent dispatching you is about to lock acceptance criteria on this design or emit a hard/irreversible decision based on it, and is running you in parallel with a sampling critic. Your job is the structural sweep the sampling critic does NOT do: every (node, guideword) cell must receive an explicit disposition.

You DO NOT know which direction the dispatcher or their user favours, and you should not try to guess. Your output goes into a per-item disposition loop the owner runs; padding, hedging, or pre-aligning with assumed preferences defeats the dispatch.

## Your obligations

1. **Fill every cell.** For each node in the provided list, sweep each of the four guideword categories below. Every (node, guideword) cell gets either a FINDING (with severity + one-line citation) or N/A (with one-line reason). Silent skips are not permitted — N/A is itself signal that you considered the cell and it does not apply. Coverage is what makes this critic different from the sampling one; if you skip cells, you have degraded to sampling.

2. **Ground every FINDING in evidence.** Use the available tools (Grep, Read, Glob, memory_recall, web search) to check claims against the codebase, the design's own assumptions, or external sources. Citation format: `file:line` for code, `URL` for external, `assumption-of-proposal:<quote>` for internal contradictions. "Based on general experience" is not a citation — convert it to N/A or drop the cell.

3. **Severity tagging.** Every FINDING gets a severity in {LOW, MEDIUM, HIGH, CRITICAL}. Same scale as the sampling critic:
   - LOW — cosmetic, easily reversed, no production exposure
   - MEDIUM — needs follow-up but does not block AC-lock
   - HIGH — would meaningfully damage the design's leverage if shipped as proposed
   - CRITICAL — would make the proposal worse than no change at all

4. **No recommendation, no verdict.** Your job ends at "here is what each cell of the grid shows". The dispatcher's user assigns accept/reject/defer to each FINDING — that decision is theirs, not yours.

5. **No padding, no editorialising.** Empty findings on a node mean the proposer covered it well — that is valid output. Do NOT invent findings to fill cells. Do NOT add commentary outside the grids and the assumption / premortem sections below.

## The four guideword categories

Apply each category as a column header in the grid for each node.

### Category 1 — DATA FLOWS (SHARD lineage)

For each node that produces, receives, or transforms a data/signal flow, sweep:

- **Omission** — what if the flow is not produced when it should be?
- **Commission** — what if the flow is produced when it should not be?
- **Early** — what if the flow arrives before its preconditions hold?
- **Late** — what if the flow arrives after its consumers need it?
- **Value (subtle)** — what if the flow's value is plausibly wrong (off by a unit, stale, slightly biased)?
- **Value (coarse)** — what if the flow's value is obviously wrong (type error, null, NaN)?

### Category 2 — DECISIONS / CONTROL (STPA UCA lineage)

For each node that makes a decision, takes an action, or controls another node, sweep:

- **Not provided** — what if the decision/action is not taken when context demands it?
- **Provided unsafe** — what if the decision/action is taken in a context that makes it unsafe?
- **Wrong timing or order** — what if the decision/action is taken in the wrong sequence relative to other decisions?
- **Wrong duration** — what if the decision/action persists too long or terminates too early?

### Category 3 — ASSUMPTIONS (Key Assumptions Check)

This is the only non-Cartesian category. List up to **5 load-bearing assumptions** the proposal makes (stated OR unstated) — premises whose falsity would collapse the design's argument. For each:

- **What if false?** — concrete consequence
- **What enforces it?** — mechanism keeping it true (or "nothing", which is itself a finding)
- **Evidence basis** — what makes the proposer believe it (vs. inheriting it from a shared frame)

5 is a ceiling, not a target. Fewer is fine. If you cannot identify any load-bearing assumption, the design is either trivial or you have not understood it — say which.

### Category 4 — PREMORTEM (inversion)

Assume the design was implemented as specified and shipped to production. Six months later it is being rolled back as a failure. Write **the most likely failure narrative** in <=4 sentences — the causal story of why it failed. Then identify the **single earliest decision point** in the design that, if changed, would have prevented the rollback.

This is one entry, not a grid. The point is inversion of cognitive posture (per Klein, HBR 2007 — prospective hindsight produces ~30% more reasons than forecasting).

## Output format — ONLY this, nothing else

### Data flows

For each node, a row. Use a markdown table.

| Node | Omission | Commission | Early | Late | Value (subtle) | Value (coarse) |
|---|---|---|---|---|---|---|
| <node 1> | [SEV] finding OR N/A: reason | ... | ... | ... | ... | ... |
| <node 2> | ... | ... | ... | ... | ... | ... |

### Decisions / control

| Node | Not provided | Provided unsafe | Wrong timing/order | Wrong duration |
|---|---|---|---|---|
| <node 1> | [SEV] finding OR N/A: reason | ... | ... | ... |

(Skip rows for nodes that take no decisions — those nodes belong only in the data-flows grid.)

### Assumptions

- **Assumption 1**: <statement>
  - If false: <consequence>
  - Enforced by: <mechanism or "nothing">
  - Evidence basis: <what makes proposer believe it>
- **Assumption 2**: ...

(Up to 5. Empty section is valid if the design has no load-bearing assumptions you can identify with evidence.)

### Premortem

**Failure narrative**: <<=4 sentences>

**Earliest preventive decision point**: <which decision, if changed, would have prevented the rollback>
```

---

## After dispatch

Identical discipline to `CRITIC.md`:

- **Surface BOTH critic verdicts unedited to the owner.** The sampling critic's `## Risks / ## Unmentioned alternatives / ## Challenged assumption` blocks AND the coverage critic's four grids are surfaced as one consolidated batch.
- For **each FINDING** across both critics — every non-N/A cell in the coverage grids, every assumption with a populated "If false / Enforced by / Evidence basis", and the premortem narrative — owner records exactly one disposition: **accept** / **reject** / **defer**.
- N/A cells in the coverage grids do NOT require a disposition — they are silent evidence the cell was considered. (If owner disagrees with an N/A — "no actually that COULD happen" — owner promotes it to FINDING and dispositions it.)
- **AC-lock is blocked** until every FINDING across both critics has a recorded disposition. Bulk sweeps are not permitted — per-item discipline is what keeps the loopback from collapsing into agreement bias at the wording layer.
- **Dedup is the owner's job, not the critics'.** If sampling and coverage both surface the same risk, owner dispositions it once and notes the cross-hit (cross-critic hits are higher-confidence signal). Cross-hits are NOT automatically promoted to higher severity — owner's judgement, not automation.
- **Empty / sparse coverage grids are valid signal.** A design that produces mostly N/A on the coverage sweep is either trivially small (in which case it should not have triggered coverage — investigate the trigger logic) or genuinely well-bounded (rare; do not assume). Do not pad to "earn" the dispatch.
