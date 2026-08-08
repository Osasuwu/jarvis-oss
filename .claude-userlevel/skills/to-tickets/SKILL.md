---
name: to-tickets
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

This checklist is the upstream pair of the `/delegate` pre-dispatch gate. The gate refuses dispatch when `sandcastle` is missing — `/to-tickets` is the canonical place where the label gets applied (decision `6e753417`). Sandcastle label is **never applied manually** and **never applied by `/grill`** (slice issues don't exist at grill time).

**Q1 — protected-zone intersection (static)**: do this slice's declared-changed files intersect any glob in the per-repo path-list at [`config/protected-paths.json`](../../../config/protected-paths.json)? Mechanical check via [`scripts/to_tickets_afk_fit.py`](../../../scripts/to_tickets_afk_fit.py) — call `intersects_protected(declared_files, repo, config)`. If yes → AFK-no, q2-4 are moot. Unknown repo ⇒ no static match ⇒ fall through to LLM judgement below; flag "unknown repo, judge manually" in the slice notes.

**Q2 — session-context dependency (LLM)**: does the slice require memory or session context beyond what the issue AC literally carries — e.g. "we already decided X in last week's grill" — to be implementable? If a fresh coding session reading only the AC would diverge from intent, AFK-no.

**Q3 — mid-execution judgement call (LLM)**: does the slice need a human judgement mid-implementation that no programmatic test can verify — e.g. "pick a sensible default timeout", "match the existing visual style"? AFK-yes only when the AC fully constrains the answer.

**Q4 — cross-cutting / multi-repo / external-state (LLM)**: does the slice touch multiple repos, external services that need credentials beyond what the sandcastle image carries, or side effects (Telegram send, prod DB write, Stripe charge) that need owner confirmation? AFK-no.

**Hard constraint** (issue #642): adding a **new** repo to the system means appending one entry to `config/protected-paths.json` — never editing this SKILL.md and never editing `scripts/to_tickets_afk_fit.py`. The lookup is keyed by `owner/repo`.

Record the AFK decision per slice (yes/no + the one question that flipped it, when applicable) so the quiz in §4 can show the owner *why* a slice is HITL.

**The AFK-fit verdict is the single source of AFK-truth — for manual *and* automated emission.** The `/delegate` pre-dispatch gate is not the only consumer: any **automated task emitter** the project runs must honor the same verdict rather than trust a label blindly. In jarvis this is the reactive-core orchestrator's `emit_task` route — an orchestrator-emitted `task_queue` row carries the same AFK-fit semantics as a manually-triaged slice: AFK-safe ⇒ `assignee=sandcastle` (auto-spawned by the task-dispatch loop), AFK-unsafe ⇒ `assignee=owner` (routed for owner attention, never auto-spawned), mirroring the `status:owner-queue` landing zone where a refused `/delegate` parks. The binding (event/task state vocabulary, who enqueues with what priority) lives in the project's CLAUDE.md *Responsibility split* and CONTEXT.md `task_queue` glossary — not here, so this checklist stays project-agnostic.

### 3b. Expand-contract for wide refactors

For some change sets, normal vertical slicing can't keep the tree green between slices — typically a **mechanical refactor** with a wide blast radius (renaming a core type across 50 files, splitting a module that everything imports, changing a shared interface). In this case, reach for the **expand-contract** pattern instead of tracer-bullet vertical slices.

Reach for expand-contract when:
- The change is **purely mechanical** (rename, extract, move — no new logic)
- The **blast radius** is wide enough that any single vertical slice changes files across multiple bounded contexts
- You **cannot slice vertically** while keeping the tree green at every intermediate point

If the change adds new logic or is narrow enough for vertical slicing, use the default §3 process. Expand-contract is the exception, not the default.

#### The three moves

**1. Expand** — Add the new form beside the old. Nothing breaks; both paths work. A single ticket (one PR), typically large but safe, `blocked_by` nothing.

**2. Migrate** — Move call sites from old to new in batches. Each batch is one ticket (one PR), `blocked_by` the expand ticket. Batches are independent of each other (no ordering) and can run in parallel.

**3. Contract** — Delete the old form once all migrate batches are done. One ticket, `blocked_by` *all* migrate batches.

#### Blocked_by wiring

Wire native dependencies per §5 for every edge:

- Each migrate batch → `blocked_by` the expand ticket
- The contract ticket → `blocked_by` every migrate batch

Migrate batches do NOT block each other — they are independent siblings. The DAG is a star: expand at the center, batches as spokes, contract as the hub after all spokes converge.

#### AFK-fit consistency

Migrate batches are **typically AFK-safe** (mechanical find-and-replace, AC fully constrains the change). Run the AFK-fit checklist (§3a) per batch to confirm — but expect most batches to pass all four questions. The expand ticket may be AFK-unsafe (question 3 — "pick the right migration boundary" is often a mid-execution judgement call) and the contract ticket may be AFK-unsafe (question 2 — "is the migration truly complete?" needs session context a single-issue agent can't verify).

#### Quiz presentation

Present the full set of tickets in the §4 quiz as a **flat list** — expand ticket first, then all migrate batches, then the contract ticket. List the blocked_by for each as prose, but the native edges (§5) are the source of truth.

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
- Slice failed AFK-fit (any "yes") → do **NOT** apply `sandcastle`. The slice routes via interactive `/implement` instead of `/delegate`. **A HITL slice still needs a positive label** — apply the project's HITL/attention marker from its CLAUDE.md label vocabulary (e.g. `unsafe-for-AFK`, `status:owner-queue`, or the repo's equivalent) plus any risk marker the failing question implies (e.g. a safety-review label when the slice touches safety-critical motion). Without a positive label the slice lands with an **empty status column** on the board and is invisible to triage — the AFK-no verdict must *produce* a label, not merely be the absence of `sandcastle`.
- Slice carries unresolved scope or unclear AC discovered during §3a → apply the matching `needs-*` label (`needs-grill`, `needs-research`, `needs-prd`). The requesting skill removes its own `needs-*` label at terminal success — `/grill` removes `needs-grill`, `/research` removes `needs-research`, `/to-spec` removes `needs-prd`. `/delegate`'s pre-dispatch gate refuses any issue carrying a `needs-*` label.

**Every published issue MUST carry a starting status label** (the project's `status:ready` / `status:*` equivalent). Where the project's board is a read-only projection of `status:*` labels, an issue with no status label has an empty status column and is invisible to board-scoped triage. A slice startable now gets the "ready" status; a slice whose blockers are still open gets no "ready" status until they close (the native dependency below encodes the block).

Publish issues in dependency order (blockers first) so you can reference real issue identifiers in the "Blocked by" field.

**Wire native dependencies, not just prose** (mandatory): after publishing, encode each "Blocked by" edge as a **native issue dependency** on the tracker, not only as body text. Prose blocks decay and are invisible to the board's blocked-by view and to `/delegate`'s readiness check; the native edge is queryable and renders in the tracker UI. On GitHub, for each blocked→blocker pair:

```bash
# blocker_id is the blocker's NUMERIC REST database id — NOT the issue number, NOT the GraphQL node_id.
# Fetch it:  gh api repos/<owner>/<repo>/issues/<blockerN> --jq .id
# Then POST the edge on the BLOCKED issue. On Windows/Git-Bash, prefix MSYS_NO_PATHCONV=1 and drop the
# leading slash so the endpoint is not rewritten to a filesystem path.
# Use -F, not -f: -f sends the id as a string and the endpoint rejects it with
# 422 "Invalid property /issue_id: ... is not of type integer".
MSYS_NO_PATHCONV=1 gh api --method POST repos/<owner>/<repo>/issues/<blockedN>/dependencies/blocked_by -F issue_id=<blocker_id>
```

Keep the prose "## Blocked by" section too — it is the human-readable rationale — but the native edge is the source of truth for tooling. Set every edge the DAG requires, including transitive blockers a slice lists explicitly.

**Decision citation (mandatory, #1099)**: `/delegate`'s pre-dispatch gate requires every `sandcastle`-labeled issue's body to cite a decision UUID (or the `[no-decision]` marker) before it can be dispatched AFK. Populate the `## Decisions` section (see `<issue-template>` below) at publish time:

- If this slice's scope was informed by one or more `record_decision` episodes — from the plan/PRD's own grill trail, or from `decision_uuids[]` carried over from an upstream `/grill` session — cite every relevant UUID under `## Decisions`, one per line, each with the one-line rationale from the decision (not just the bare UUID — the gate only needs the UUID present, but a bare hex string is useless to a human reader later).
- If the slice is genuinely mechanical and no architectural decision informed it (a pure rename, a dependency bump, a doc fix), write `[no-decision]` instead of fabricating a UUID. Do not invent or reuse an unrelated UUID just to satisfy the gate — the gate now accepts the explicit marker for this case.
- This applies to every slice, HITL or AFK — but it is load-bearing only for `sandcastle`-labeled (AFK) issues, since `/delegate`'s gate is what actually enforces it.

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

## Decisions

- `<full-8-4-4-4-12-uuid>` — one-line rationale

Or `[no-decision]` if this slice is purely mechanical and no `record_decision` episode informed its scope. Required for `sandcastle`-labeled issues — `/delegate`'s pre-dispatch gate refuses dispatch without one or the other (#1099).

## Blocked by

- A reference to the blocking ticket (if any)

Or "None - can start immediately" if no blockers.

(This section is the human-readable rationale. It does NOT replace the **native issue dependency** — every edge listed here must also be wired as a native blocked_by edge on the tracker per §5.)

</issue-template>

Do NOT close or modify any parent issue.
