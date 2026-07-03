---
name: to-issues
description: Break a plan, spec, or PRD into independently-grabbable issues on the project issue tracker using tracer-bullet vertical slices. Use when user wants to convert a plan into issues, create implementation tickets, or break down work into issues.
---

# To Issues

Break a plan into independently-grabbable issues using vertical slices (tracer bullets).

The issue tracker and triage label vocabulary should be defined in the project's CLAUDE.md.

## Process

### 1. Gather context

Work from whatever is already in the conversation context. If the user passes an issue reference (issue number, URL, or path) as an argument, fetch it from the issue tracker and read its full body and comments.

### 2. Explore the codebase (optional)

If you have not already explored the codebase, do so to understand the current state of the code. Issue titles and descriptions should use the project's domain glossary vocabulary, and respect ADRs in the area you're touching.

### 3. Draft vertical slices

Break the plan into **tracer bullet** issues. Each issue is a thin vertical slice that cuts through ALL integration layers end-to-end, NOT a horizontal slice of one layer.

Slices may be 'HITL' or 'AFK'. HITL slices require human interaction, such as an architectural decision or a design review. AFK slices can be implemented and merged without human interaction. Prefer AFK over HITL where possible.

<vertical-slice-rules>
- Each slice delivers a narrow but COMPLETE path through every layer (schema, API, UI, tests)
- A completed slice is demoable or verifiable on its own
- Prefer many thin slices over few thick ones
</vertical-slice-rules>

### 3a. AFK-fit checklist (apply per slice, decides `sandcastle` label)

For each slice, answer four questions. Any "yes" ⇒ slice is **NOT** AFK-safe ⇒ do **NOT** apply the `sandcastle` label ⇒ the slice routes through interactive `/implement` rather than `/delegate`. All four "no" ⇒ apply `sandcastle`.

This checklist is the upstream pair of the `/delegate` pre-dispatch gate. The gate refuses dispatch when `sandcastle` is missing — `/to-issues` is the canonical place where the label gets applied (decision `6e753417`). Sandcastle label is **never applied manually** and **never applied by `/grill`** (slice issues don't exist at grill time).

**Q1 — protected-zone intersection (static)**: do this slice's declared-changed files intersect any glob in the per-repo path-list at [`config/protected-paths.json`](../../../config/protected-paths.json)? Mechanical check via [`scripts/to_issues_afk_fit.py`](../../../scripts/to_issues_afk_fit.py) — call `intersects_protected(declared_files, repo, config)`. If yes → AFK-no, q2-4 are moot. Unknown repo ⇒ no static match ⇒ fall through to LLM judgement below; flag "unknown repo, judge manually" in the slice notes.

**Q2 — session-context dependency (LLM)**: does the slice require memory or session context beyond what the issue AC literally carries — e.g. "we already decided X in last week's grill" — to be implementable? If a fresh coding session reading only the AC would diverge from intent, AFK-no.

**Q3 — mid-execution judgement call (LLM)**: does the slice need a human judgement mid-implementation that no programmatic test can verify — e.g. "pick a sensible default timeout", "match the existing visual style"? AFK-yes only when the AC fully constrains the answer.

**Q4 — cross-cutting / multi-repo / external-state (LLM)**: does the slice touch multiple repos, external services that need credentials beyond what the sandcastle image carries, or side effects (Telegram send, prod DB write, Stripe charge) that need owner confirmation? AFK-no.

**Hard constraint** (issue #642): adding a **new** repo to the system means appending one entry to `config/protected-paths.json` — never editing this SKILL.md and never editing `scripts/to_issues_afk_fit.py`. The lookup is keyed by `owner/repo`.

Record the AFK decision per slice (yes/no + the one question that flipped it, when applicable) so the quiz in §4 can show the owner *why* a slice is HITL.

**The AFK-fit verdict is the single source of AFK-truth — for manual *and* automated emission.** The `/delegate` pre-dispatch gate is not the only consumer: any **automated task emitter** the project runs must honor the same verdict rather than trust a label blindly. In jarvis this is the reactive-core orchestrator's `emit_task` route — an orchestrator-emitted `task_queue` row carries the same AFK-fit semantics as a manually-triaged slice: AFK-safe ⇒ `assignee=sandcastle` (auto-spawned by the task-dispatch loop), AFK-unsafe ⇒ `assignee=owner` (routed for owner attention, never auto-spawned), mirroring the `status:owner-queue` landing zone where a refused `/delegate` parks. The binding (event/task state vocabulary, who enqueues with what priority) lives in the project's CLAUDE.md *Responsibility split* and CONTEXT.md `task_queue` glossary — not here, so this checklist stays project-agnostic.

### 4. Quiz the user

Present the proposed breakdown as a numbered list. For each slice, show:

- **Title**: short descriptive name
- **Type**: HITL / AFK
- **Blocked by**: which other slices (if any) must complete first
- **User stories covered**: which user stories this addresses (if the source material has them)

Ask the user:

- Does the granularity feel right? (too coarse / too fine)
- Are the dependency relationships correct?
- Should any slices be merged or split further?
- Are the correct slices marked as HITL and AFK?

Iterate until the user approves the breakdown.

### 5. Publish the issues to the issue tracker

For each approved slice, publish a new issue to the issue tracker. Use the issue body template below.

**Label application at publish time**:

- Slice passed AFK-fit checklist (all four "no") → apply the `sandcastle` label. This is the canonical place the label is set — see §3a, decision `6e753417`.
- Slice failed AFK-fit (any "yes") → do **NOT** apply `sandcastle`. The slice routes via interactive `/implement` instead of `/delegate`.
- Slice carries unresolved scope or unclear AC discovered during §3a → apply the matching `needs-*` label (`needs-grill`, `needs-research`, `needs-prd`). The requesting skill removes its own `needs-*` label at terminal success — `/grill` removes `needs-grill`, `/research` removes `needs-research`, `/to-prd` removes `needs-prd`. `/delegate`'s pre-dispatch gate refuses any issue carrying a `needs-*` label.

Publish issues in dependency order (blockers first) so you can reference real issue identifiers in the "Blocked by" field.

**Milestone assignment (every published issue MUST land in a milestone)**:

An issue with no milestone falls off the board — it is invisible to milestone-scoped triage and rots. Never publish milestone-less. Resolve the milestone per slice, in this order:

1. **Inherit from parent** — if the slice has a `## Parent` reference, read that issue's milestone and apply the same one. The `## Parent` body link is text only; the issue tracker does NOT propagate the milestone, so you must set it explicitly (e.g. `gh issue edit <N> --milestone "<title>"`). If the parent itself is milestone-less, fix the parent first (assign it, then inherit) rather than propagating the gap.
2. **Inherit from source** — if the slices came from an existing issue/PRD passed as the argument, use that source's milestone.
3. **Match by theme** — no parent and no source: pick the open milestone whose theme the slice fits (enumerate the tracker's open milestones first). Fold this into the §4 quiz — show the proposed milestone per slice and let the user correct it.
4. **No fit** — if genuinely nothing matches, surface it in the quiz and ask whether to create a new milestone or leave it orphan by explicit choice. Orphan is only ever a deliberate, stated decision — never a default.

This applies to follow-up / tech-debt slices too: a spin-off inherits the milestone of the issue it spun off from (rule 1), which is the single most common case and the one that was silently dropping issues off the board.

<issue-template>
## Parent

A reference to the parent issue on the issue tracker (if the source was an existing issue, otherwise omit this section).

## What to build

A concise description of this vertical slice. Describe the end-to-end behavior, not layer-by-layer implementation.

Avoid specific file paths or code snippets — they go stale fast. Exception: if a prototype produced a snippet that encodes a decision more precisely than prose can (state machine, reducer, schema, type shape), inline it here and note briefly that it came from a prototype. Trim to the decision-rich parts — not a working demo, just the important bits.

## Acceptance criteria

- [ ] Criterion 1
- [ ] Criterion 2
- [ ] Criterion 3

## Blocked by

- A reference to the blocking ticket (if any)

Or "None - can start immediately" if no blockers.

</issue-template>

Do NOT close or modify any parent issue.
