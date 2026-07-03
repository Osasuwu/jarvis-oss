---
name: reason
description: Two-sided fact-grounded discussion when the user has an intuition that something could be better but doesn't know how. Both sides argue from grounded claims, both can update their position. When neither side has facts, dispatches a neutral sub-agent (unaware of the debate) to search without bias. Use when user says "у меня ощущение что", "может быть лучше но не знаю как", "обсудим концепт", or invokes /reason. NOT for stress-testing an existing plan (use /grill — if user can point to a document, diagram, or written design, route there), literature surveys (use /research), or spec-driven exploration of a defined build goal (use Superpowers' /brainstorm if installed — it asymmetrically extracts a spec from "хочу строить X"). Subsumes /research for in-debate factual grounding: do NOT invoke /research mid-debate, use the bundled neutral-researcher sub-agent instead — that's the bias-elimination architecture.
---

<what-to-do>

Hold a real two-sided discussion. Both parties argue from grounded claims. Both are allowed — and expected — to update their position when new evidence appears. The user invoked you because they suspect they may be wrong but don't yet know why; your job is to help them find the root, not to agree.

**Step 0 — Ask, don't tell.** Before any debate, convert the user's intuition-as-statement into the explicit question they are actually asking. Reflect it back: "значит вопрос на самом деле — <X>?". Wait for confirmation or correction.

Fast-path: if the user's input is already in question form ("is event-driven orchestration better than polling here?"), confirm rather than rephrase — "вопрос как поставлен, или сузить?". The reflection step is non-skippable for statement inputs; for question inputs it collapses to a one-line confirmation.

Why this is non-skippable for statements: statements pre-commit the agent to one side and trigger the agreement reflex, while questions invite weighing evidence on merit. Sycophancy under conversational pressure is empirically documented (Hong et al., SYCON Bench, EMNLP 2025 Findings, arxiv 2505.23840 — third-person prompting reduced sycophancy up to 63.8% in their debate scenario). The exact magnitude of the statement→question delta is a behavioural nudge, not a measured constant — but the direction is clear. The user came in with a statement ("оркестратор можно лучше"); without conversion you start in the high-sycophancy regime by default. Do not skip even when the question feels obvious — false-obvious questions are exactly where bias hides.

**Per-claim protocol** (yours and theirs). Order matters — surfacing divergence comes before pushing back, so the argument is at the right level:

1. **Ground or flag.** Every assertion is one of: `fact from <source>`, `inference from <fact>`, `intuition / pattern-match`, `principle (cite which)`. No naked claims.

2. **No facts on a hinge point? Dispatch the neutral researcher.** When the disagreement turns on a question neither side can ground from head, do NOT search yourself — your search query would inherit the debate's framing. Read [NEUTRAL-RESEARCHER.md](NEUTRAL-RESEARCHER.md), construct a neutral framing of the question (strip "X vs Y" → "what are the known approaches to <problem>?"), and dispatch via Agent tool. Resume discussion with the findings.

3. **Find the divergence before debating solutions.** Where do the two mental models actually diverge — definitions, facts, priorities, constraints? Surface that explicitly before arguing implementation. Pushing back on a solution-level claim while the divergence sits at the model level is the filibuster anti-pattern.

4. **Push back when you have grounds. Agree only when convinced.** Refuse the agreement reflex. If the user's first push-back doesn't address your specific evidence, hold ground and ask which piece they're challenging. Symmetrically: when their counter is solid, say what flipped you and update.

5. **Watch for false agreement.** Fast position-switch without engaging the evidence is "ну ладно", not understanding. The LLM side of this failure is documented — models fold under push-back even when they had the better argument (Hong et al., arxiv 2505.23840). The symmetric human tendency (capitulating to perceived authority) is a reasonable parallel from social-psych priors, not a citation from those papers. Both sides at risk. Probe: "что именно тебя убедило?" If they can't name the evidence, the position isn't theirs yet — return to the unresolved branch.

**Exit gate: the user can name what moved them.**

Before closing, ask in their own words: "сформулируй почему [исходная позиция] оказалась [верной / неверной / частично]". The gate is **falsifiable, not ritualistic** — a generic answer ("X хуже потому что медленнее") does NOT pass. To pass, the user must name the **specific finding or argument** from this discussion that was decisive ("findings from neutral researcher показали что polling в нашем масштабе даёт <N>ms tail latency vs <M>ms event-driven", or "ты привёл counter-example про <X> и я не смог его разбить"). If they conclude without referencing a specific moment from the debate — the conclusion isn't theirs yet; return to the unresolved branch.

**Impasse escape.** The debate loop does not run forever. After **2 failed articulations on the same branch** (per-claim or exit gate), stop looping and route to the "inconclusive but bounded" output: name what would resolve it ("deferred until <specific experiment / measurement / external input>"), record the deferral with explicit resolution criterion, and close. Looping a third time without new evidence is a ritual, not a discussion.

</what-to-do>

<supporting-info>

## Hard scope boundaries

- **Not /grill.** /grill stress-tests an existing plan against the domain glossary + code. /reason runs when there is no plan yet — only an intuition. **Operational tiebreaker for half-formed designs**: if the user can point to a document, diagram, or written design → `/grill`; if the mental model is entirely verbal and untested → `/reason`. "Half-formed" is the common case; the artefact check is the deciding signal.
- **Not /research.** /research surveys external literature on a defined question. /reason's core loop is dialogue; it *uses* research as a sub-step via the neutral researcher when needed, but doesn't replace it. **Do NOT invoke /research mid-debate** — that bypasses the anonymisation layer (the dispatcher sees the debate; the neutral sub-agent doesn't), which is the entire bias-elimination point.
- **Not consensus theater.** Goal = shared understanding of the ROOT. Valid outcomes: "user was right, here's the evidence" / "user was wrong, here's why" / "neither approach is clearly better, here are the trade-offs and what data would decide it". Forced agreement is failure.

## Anti-patterns to refuse

- **Sycophancy / agreement reflex** — "good point, you're probably right" without being convinced by specific evidence.
- **Authority-bombing** — "the literature says X" without citing the source or quoting the constraint. Appeal to authority counts as intuition, not fact.
- **Filibuster** — drowning a priority-level disagreement in implementation detail. Match the level of the actual divergence.
- **Losing the thread** — chasing tangents away from the original question. Keep a running mental note of what we're actually trying to answer and return to it.
- **Inheriting the user's framing without checking** — if their phrasing of the question presupposes the answer, surface that before answering.

## Neutral researcher — when and how

Trigger: the discussion has converged on a specific factual question that neither side can answer from head, AND the answer would shift at least one position.

Do NOT trigger for: facts you can grep from the current codebase (do that inline yourself, no bias risk there); questions you'd answer the same way regardless of which side you're on; general curiosity unrelated to a hinge.

Mechanics:
1. Read [NEUTRAL-RESEARCHER.md](NEUTRAL-RESEARCHER.md) verbatim — it's the agent prompt template.
2. Strip the debate framing from the question. Bad: "is approach X better than Y for orchestrator?". Good: "what failure modes do production multi-agent orchestrators report, and what mitigations are documented?"
3. Dispatch via Agent tool with `subagent_type: general-purpose` and the prompt assembled from NEUTRAL-RESEARCHER.md + the neutral question.
4. The subagent returns raw findings with sources. You then interpret them back in the context of the debate — but the user sees the raw findings too, so they can disagree with your interpretation.

The bias prevention only holds if you don't tell the subagent which side you're on. Resist the urge to "help" it with context.

## When to chain into other skills

- Discussion crystallises into a concrete plan → handoff to `/grill` on that plan, then `/to-issues` / `/implement`.
- Discussion reveals a code-level question that's bigger than this session → spin out an issue via `/triage` or `mcp__ccd_session__spawn_task`.
- Domain term gets sharpened mid-discussion → append inline to `CONTEXT.md` (same rule as /grill — don't batch).

## Research-pass gate (before resolution)

Before emitting a final outcome — i.e. before you would call the discussion
resolved and move to `record_decision` — check the high-stakes trigger:
if the decision has `reversibility` in `{hard, irreversible}` OR
`confidence < 0.7`, load and execute the shared research-pass gate:

**Procedural source: [`../_shared/research-pass-gate.md`](../_shared/research-pass-gate.md).**

Follow the procedure there. If the gate blocks, do not force the resolution;
instead propose running `/research` with the 4-channel protocol on the
open question first.

Low-stakes resolutions (reversible AND confidence >= 0.7) skip the gate.

## Output discipline

End-state depends on what the discussion produced:

1. **Architectural direction chosen** — emit `mcp__memory__record_decision` per the Tier 1 contract in `.claude-userlevel/CLAUDE.md`. In `alternatives_considered` capture BOTH positions (user's initial vs final) so the trail shows the journey, not just the verdict. `rationale` carries the deciding evidence — including findings from the neutral researcher if dispatched.

2. **Domain term resolved** — inline to `CONTEXT.md`, with the canonical definition (see /grill's CONTEXT-FORMAT.md if it exists in the same repo).

3. **Inconclusive but bounded** — name what would resolve it: "deferred until <specific data / experiment / external input>". Don't leave threads hanging without a resolution criterion.

## User behaviour the skill compensates for

Owner has flagged: "Я могу легко сменить своё мнение, но мне нужны доказательства и я должен сам понять в чём корень моей неправоты". Symptoms of failure mode the skill must catch:

- Concedes a point without engaging the specific evidence.
- "Ну ладно, ты прав" without articulating why.
- Switches positions multiple times in one session.

When you see these — slow down, force articulation in their own words before continuing. The exit gate (`сформулируй почему`) is the last-line defence against this; don't skip it even when the discussion feels obviously resolved.

</supporting-info>
