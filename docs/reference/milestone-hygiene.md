# Milestone vs pillar hygiene

Pull-only detail for CLAUDE.md → *Development process*. Entity definitions (pillar / milestone / slice, why "epic" is not used) live in CONTEXT.md → *Core entities*. This file is the **single authoritative body** for the standing rules below — memory `milestone_hierarchy_v3` is demoted to an on-demand decision-record (rationale + history), not a duplicate source (#1157). Shape:

```
pillar (narrative only) → goal (Type A) → milestone (capability + PRD) → slice (one PR)
```

## Rules

1. **No date in milestone title.** "Skill set redesign", not "Skill set redesign — 2026-05".
2. **Milestone closes on capability shipping.** All slices merged → close. State=open with 0 open issues is a bug.
3. **PRD lives in milestone description.** No separate epic-issue layer. `/to-spec` writes to milestone description.
4. **Single slice = no milestone.** Drive-by fixes, isolated improvements: just an issue + PR, no milestone ceremony.
5. **No numerical WIP limit on active milestones.** Self-throttle by owner-attention (HITL/grill/review) load. AFK milestones (delegated to subagents/sandcastle) cost ~0 attention.
6. **Architecture sweep triggered on milestone close** when ≥3 closed slices. SessionStart surfaces "Milestone N closed — architecture sweep recommended" if no sweep ran since closed_at. (Automatic trigger not yet implemented — see *Architecture sweep at milestone close* below.)

## Mechanics not covered by the rules above

1. Retroactive — if related slices shipped without a milestone, create it, attach the issues+PRs, close it. History must be recoverable.
2. When user rushes and skips the milestone for grouped work — catch it: "milestone for these N slices?" before creating issues. Don't be a silent executor.

## Architecture sweep at milestone close

After a milestone closes (capability shipped), run `/improve-codebase-architecture` in a **fresh session**, never the one that closed the milestone (dumb zone) — mechanics live in the skill's own `SKILL.md`.

**Trigger (planned — #605):** the automatic ≥3-closed-slices SessionStart surface described in Rule 6 above is not implemented. Until #605 lands the trigger is **manual**; small milestones (1–2 slices) skip the sweep.

**Cadence:** semantic, not temporal. The sweep follows capability shipping, never a date.

**Output discipline:** 1–2 actionable refactors → child issues attached to a follow-up milestone via grill chain. Rest goes to `.out-of-scope/<topic>.md` with reason. Don't try to action everything.
