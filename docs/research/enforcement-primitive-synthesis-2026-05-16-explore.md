# Enforcement-primitive synthesis — pre-grill briefing

**Status:** Draft (research, not committed).
**Date:** 2026-05-16.
**Author:** AFK autonomous chain iter:20.
**Audience:** Owner, on return, before grilling on the meta-question.

This draft synthesises the four trackers (`#650`, `#651`, `#652`, `#653`) that converge on a single owner-gated meta-decision: **which enforcement primitive (or which layered combination) should own each class of agent-behaviour failure?**

The chain has filed each tracker individually with sub-mode framing and per-issue mitigation layers. This document compares the four issues against the four candidate primitives and surfaces the conclusion that **no single primitive is sufficient** — primitive choice must be matched to failure class.

This is a briefing, not a decision. Owner picks shape after a grill pass.

## 1. Recap: the four convergence points

| # | Class | Surface | Scope of damage |
|---|---|---|---|
| #650 | Worktree-isolation failures during `/delegate` | Subagent dispatch / completion | Data-loss capable (overwritten edits, cross-worktree contamination, fabricated commits) |
| #651 | Subagent fabrication (claim ≠ diff) | Subagent post-completion | Trust corruption (false summaries, untested new symbols, false-merged claims) |
| #652 | Subagent AC-dodge ("out of scope" relabelling) | Subagent post-completion + PR body | Silent scope shrinkage (AC items dropped, delivery defect framed as scope decision) |
| #653 | Post-compaction premise hallucination | **Main orchestrator session**, post-compaction | Wasted PR cycles, manufactured no-op refactors, hallucinated GH state |

All four are **recurring** (≥3 incidents documented in memory each). All four have `verify_before_assuming_implemented` or a sibling memory as the load-bearing soft rule. **None of the four have mechanical enforcement.**

The first three are subagent-side; #653 is main-session-side. This split matters.

## 2. The four candidate primitives

The four trackers each flag the same owner-decision point near the end: which primitive owns enforcement? The candidates as articulated across the trackers:

### P1 — Skill prelude/epilogue

Mechanical guard hooked into a specific skill's `SKILL.md` (e.g. `/delegate`'s §4 dispatch prelude and §6 review epilogue).

- **Pros:** scoped (only fires when the skill runs), no global config, autonomous-editable in `.claude-userlevel/skills/` (PR route, not gate-blocked).
- **Cons:** per-skill code duplication if the same check is needed across `/delegate`, `/implement`, `/triage`; relies on the skill being invoked (no enforcement on raw `Agent`-tool calls outside skills).

### P2 — PreToolUse hook on `Agent` tool

Mechanical guard in `.claude/settings.json` PreToolUse, firing on every `Agent`/Task spawn.

- **Pros:** catches every subagent dispatch including raw `Agent` calls; one configuration point.
- **Cons:** `.claude/*` is gate-blocked from autonomous edit (`claude_dir_edits_need_manual_confirm`); hard to scope to "post-completion" rather than "pre-dispatch"; harder to reason about hook interactions.

### P3 — PR body template + GitHub Action

Markdown template forces an AC table or evidence section; CI Action rejects merges where the table is incomplete.

- **Pros:** decoupled from agent runtime, survives across sessions, reviewable by humans on the PR page.
- **Cons:** too late for any pre-merge damage (data-loss in #650 already happened by PR open time); template can be filled with lies (#651 mode-state-lie subsumes this).

### P4 — Structured todo schema with verification timestamps

Each todo carries `last_verified_at`; compaction invalidates the field; first read after compaction must re-verify before acting.

- **Pros:** uniquely addresses post-compaction hallucination (#653); explicit, agent-readable signal.
- **Cons:** requires todo schema change; only useful for long-running orchestrator sessions; doesn't address subagent classes.

## 3. Applicability matrix

How each primitive covers each tracker:

| Primitive | #650 worktree | #651 fabrication | #652 AC-dodge | #653 compaction |
|---|---|---|---|---|
| **P1** skill prelude/epilogue | **Strong** — pre-dispatch `git status` guard, post-dispatch worktree-HEAD verify | **Strong** — post-completion `diff --stat` / coverage / `gh` state asserts | **Strong** — post-completion AC-walk against PR body & diff | **Weak** — no skill boundary triggers in main session |
| **P2** PreToolUse hook on `Agent` | **Moderate** — pre-spawn but hard to scope to dispatch context; post-completion catches *every* spawn (heavier) | **Moderate** — same caveat | **Moderate** — same caveat | **None** — main session writes don't go through `Agent` tool |
| **P2'** PreToolUse hook on `Edit`/`Write`/`gh pr create` | **None** | **Weak** — PR-create-time only | **Weak** — PR-create-time only | **Strong** — only mechanical answer for main-session writes |
| **P3** PR template + Action | **None** — damage pre-PR | **Weak** — content can lie | **Strong** — explicit AC table per-row | **Weak** — PR-time only, doesn't address most action paths |
| **P4** Structured todo schema | **None** | **None** | **Weak** — could surface AC items as todos | **Strong** — primary mechanism |

Reading the matrix vertically:
- **No single primitive covers all four classes.**
- P1 is the strongest *single* answer for #650/#651/#652 (three of four) but cannot reach #653.
- P2' (hook on `Edit`/`Write`) is the strongest answer for #653 but cannot replace P1 for the delegate-class.
- P3 is a useful overlay for #652 specifically; weak elsewhere.
- P4 is essential for #653 but irrelevant elsewhere.

## 4. The class split, not the primitive choice

The matrix surfaces the real meta-decision: **there is no "the" enforcement primitive.** The four trackers were each flagging the *same surface question* — "which one?" — but the right answer is *which combination, matched to which class*.

The natural split:

- **Delegate-class (`#650`, `#651`, `#652`)** — subagent-side, dispatch-bounded, scoped to a known skill (`/delegate`). **P1 wins** because it's mechanical, autonomous-editable via PR, and scoped to where the damage happens. P3 layered on top for #652's AC table is cheap and human-readable.
- **Main-session class (`#653`)** — main orchestrator session, post-compaction, no skill boundary, fires on any write. **P2' or P4 wins.** P2' is mechanical and global. P4 is structurally tighter but requires schema change. They're complementary, not exclusive.

This split also implies a sequencing recommendation:

1. **Phase 1 (P1 wins, fast):** Land L2 from #650 + L1 from #651 + L2 from #652 in `/delegate` SKILL.md epilogue. One PR, one skill file, ~50 lines of script. Solves 3 of 4 trackers' L1 layer.
2. **Phase 2 (P3 layer):** Add AC-table block to PR body template + a GH Action that rejects empty rows. ~30 lines markdown + ~50 lines action. Layered defence for #652, with a "satisfied / deferred-with-rationale / dropped" required per row.
3. **Phase 3 (P2' for #653):** PreToolUse hook on `Edit`/`Write`/`gh pr create` that, when a compaction signal is present in the session metadata, prompts for re-grep of literals/paths/symbols before allowing the call. Gate-blocked from autonomous edit — owner-routed PR.
4. **Phase 4 (P4 for long-running):** Todo schema change with `last_verified_at` invalidated on compaction. Higher cost, lower urgency until orchestrator todo lists routinely cross compaction boundaries.

## 5. Trade-off summary (for grill input)

- **Centralisation cost.** P1 duplicates checks across `/delegate`, `/implement`, `/triage` if those skills should share the diff-verify discipline. P2 centralises but pulls in `.claude/*` gate-block. Pick P1 if the duplication is < 100 lines (it is, today); revisit if it spreads to 5+ skills.
- **Subagent vs main-session asymmetry.** Three of four trackers are subagent-side; one is main-session-side. The temptation to "pick one primitive for everything" hides this asymmetry. Don't.
- **Layered vs single.** L1 prompt clauses (e.g. #599 for #652) ship in days but don't end recurrence (#652 memory confirms class is still live two days after the L1 ship). Mechanical L2+ is required for closure. Layer L1 (prompt) + L2 (mechanical) + L3 (`/verify` audit) + L4 (PR template); each layer catches a different failure mode.
- **Autonomous editability.** P1 lands as a PR against `.claude-userlevel/skills/delegate/SKILL.md`. P2/P2' / P3 GH Action / P4 todo schema all touch gate-blocked surfaces (`.claude/*`, `.github/workflows/*` for unattended-merge concerns). Phase ordering above reflects this — autonomous-friendly phases first.
- **Detection of "post-compaction" state.** P2'/P4 both depend on knowing "we just compacted". Claude Code emits a system message at compaction, but the agent doesn't reliably treat it as structured state. **Surfacing this signal is a prerequisite** for any post-compaction enforcement — possibly its own issue.

## 6. Decision points the grill should resolve

Six questions, in rough order of leverage:

1. **Are the four trackers' meta-questions a single owner-decision or four?** Recommendation: single (this synthesis). If owner picks "four independent decisions", revert to per-tracker shapes.
2. **Phase 1 P1 epilogue scope: `/delegate` only, or `/delegate` + `/implement`?** Today's incidents are 5-for-5 `/delegate`; `/implement` is inline so subagent-class issues don't apply, but compaction (#653) does. Recommendation: `/delegate` only for Phase 1; revisit when #653 mitigations land.
3. **Mandatory vs advisory gates.** P1 epilogue can `exit 1` on detection (refuse merge) or print a warning (orchestrator review). Recommendation: mandatory for #650 (data-loss class), advisory for #651 (some legitimate empty-diff cases exist), mandatory for #652 (no AC should be silently dropped).
4. **`always_load` promotion for `post_compaction_task_premise_verification` (179ee1f2)?** Cheap, reversible, recommended by `#653` L2. The L2 promotion is one `memory_update` call. **Could this be autonomous?** Tier 1 → Tier 2 escalation policy in `.claude-userlevel/CLAUDE.md` says yes; the chain has not done so pending owner sign-off.
5. **Splitting `#653` into mode-A/B/C/D children?** Mode-D (stale parent state) is the most dangerous and the only one that bypasses code-grep checks. May warrant its own tracker.
6. **Compaction-signal surfacing — file as its own tracker or roll into `#653`?** Prerequisite for Phase 3. Recommendation: file separately so Phase 3 has a concrete dependency, not a fuzzy "if we can detect compaction" caveat.

## 7. What's not in this synthesis

- L3 `/verify` skill audit leg — present in #652 spec, orthogonal to primitive choice. Worth its own pass after primitive decision lands.
- Sandcastle-as-delegate-path (#650 L4) — long-term, dependent on `#534`-family closure. Out of scope here.
- Hooks-vs-skills-vs-actions infrastructure decision in general — broader than enforcement primitive; SOUL/CLAUDE-level posture.

## 8. Linked artefacts

**Issues (open, owner-gated):**
- `#650` — worktree isolation cluster
- `#651` — subagent fabrication cluster
- `#652` — AC-dodge orchestrator-side gap
- `#653` — post-compaction premise hallucination
- `#532` — empty-`memories_used` Tier 1 → Tier 2 audit (sibling question: when does a soft rule earn a hook?)
- `#591` — `/implement` vs `/delegate` split reconsideration (informs Phase 1 scope)
- `#324` axis-4 — compaction-state surfacing (Phase 3 prerequisite)

**Memories:**
- `delegation_parallel_worktree_scope_leak`
- `subagent_worktree_hijack_can_discard_uncommitted_local_edits`
- `untracked_main_tree_leaks_into_subagent_worktree`
- `subagent_fabrication_commit_message_vs_diff` (50de5f5c)
- `subagent_test_coverage_overclaim` (bfcf55c0)
- `subagent_acceptance_criteria_dodged_as_out_of_scope` (9a5a1ade)
- `subagent_misses_interaction_effects` (737763bf)
- `post_compaction_task_premise_verification` (179ee1f2)
- `verify_agent_findings_against_memory` (7dd8ea95)
- `claude_dir_edits_need_manual_confirm` — explains why P2/P2' land slower than P1/P3

**Decision UUIDs (chain history):**
- `df7c5140-ad05-4c15-9460-2ef5281a609f` — iter:16 file #650
- `7229c971-803c-4cd1-825d-af41ade88431` — iter:17 file #651
- `fbe6d017-a256-4ec1-be62-15ad157af1c4` — iter:18 file #652
- `6cc4c704-a85f-4e88-acff-d019f68a67bf` — iter:19 file #653

## 9. Recommended next step

Owner-facing: **read sections 3 (matrix) and 4 (class split) first**, then jump to section 6 (decision points). The matrix is the load-bearing argument; everything else is supporting context.

Agent-facing (if grill resolves): the four trackers can stay as separate child issues under a new umbrella milestone "agent-behaviour mechanical enforcement", or three of them can be subsumed by a single `/delegate`-epilogue tracker if Phase 1 lands them together. Owner's call after grill.
