---
name: dispatch
description: Labels GitHub issues agent:dispatch for headless pickup via claude-code-action. Triggers: "делегируй #X #Y", "раскидай на агентов", "параллельно реализуй #X #Y #Z". Single issue with heavy context → /implement instead. Never spawns a subagent in-session — applies a label, reports dispatched/refused.
version: 4.0.0
---

# Dispatch Skill

Renamed from `/delegate` (#1085 Slice 2), retargeted off the old queue-and-claim driver stack onto a GitHub-native trigger: applying the `agent:dispatch` label to an issue is meant to fire `claude-code-action` on that repo's `issues.labeled` event, which does the actual headless work. This skill's job ends at the label + report — it never spawns a subagent in-session and never enqueues anything itself.

**Disclosure — executor workflow not yet shipped.** As of this rewrite, no workflow in this repo (or its tracked siblings) actually listens for `issues.labeled` on `agent:dispatch` and invokes `claude-code-action`. Applying the label today marks the issue but nothing picks it up until that workflow ships — treat every "dispatched" outcome below as "labeled, pending the executor workflow" until a follow-up issue lands it.

Memory recall and the decision-logging discipline come from user-level CLAUDE.md `### Memory & decision protocol` plus `/implement`'s own §3/§6 (append a line to `decisions.md`). The skill-specific gates below are what `/dispatch` adds on top.

## When to /dispatch vs /implement

**Prefer /implement (inline, current session):**
- Single issue
- Task touches safety-critical zones (`driver/`, `planning/`, `mujoco/`)
- Task needs cross-cutting awareness (spans multiple projects, shared infra)
- Issue description alone isn't enough — requires the current session's reasoning trail or just-loaded memory context

**Prefer /dispatch (label-routed headless execution):**
- Multiple independent issues (any order works)
- Each issue description is self-contained — the executor re-derives everything from the live issue at spawn, with no session context
- Tasks touch disjoint files / areas

**Mixed batch — split the work:**
- Keep context-heavy or safety-critical issues for yourself (inline via /implement flow)
- Dispatch the rest
- Report the split reasoning briefly to the principal

**Jarvis judgment overrides the principal's "параллельно":** The principal has explicitly delegated this call to Jarvis. If a task looks deceptively complex or a headless worker will struggle (needs session context, cross-file reasoning, recent-decisions awareness), keep it inline even if asked to dispatch. Don't silently downgrade — tell the principal "keeping #X inline because <reason>".

## Contract: advisory readiness gate (runs before labeling, per issue)

Every issue passes through the same readiness check before this skill applies `agent:dispatch` — an **advisory** courtesy filter that catches an obviously-not-ready issue before it gets labeled for headless pickup, and gives the operator the refusal message immediately instead of after the executor workflow eventually runs (or, until that workflow ships, never runs) and fails silently.

**Five conditions, all required:**

0. **Issue's repo matches `GITHUB_REPO`** (stopgap, #1651 — checked first,
   short-circuits before the readiness conditions below). Label-triggered
   execution runs per-repo (the workflow lives in the issue's own repo), so a
   foreign-repo issue would need cross-repo labeling this skill doesn't do.
   Refused with a message pointing at milestone 58 (#959) S3 (#1119)/S4a
   (#1121) — the slices where real per-row repo resolution belongs. Remove
   this condition once that ships.
1. Issue has **no** `needs-*` label (`needs-grill`, `needs-research`,
   `needs-prd`, `needs-refactor`, …). Each requesting skill removes its own
   label at terminal success.
2. Issue body contains an `## Acceptance criteria` heading (case-insensitive
   prefix match).
3. Issue body cites at least one decision reference (a `decisions.md` line,
   a decision UUID from before the native-memory rewrite, or the explicit
   `[no-decision]` marker for slices that legitimately have none).
4. Issue does **not** carry the `afk:3-human` label. Per `/triage`'s AFK-fit
   classification (`/to-tickets` §3a) `afk:3-human` is a hard refusal from
   headless dispatch, not just a plan-review speed bump like `afk:2-plan` —
   this condition is what makes that refusal actually bind at the one place
   headless execution is triggered, instead of only living in triage
   documentation. No new API call: `labels` is already fetched for
   condition 1.

**Invocation** (per issue, before labeling):

```bash
gh issue view <N> --repo <owner/repo> --json number,title,body,labels
```

Check condition 0 against the fetched repo, conditions 1 and 4 against `labels`, conditions 2–3 against `body` directly — no external script; the checks are simple enough to run inline against the fetched JSON.

**On refusal** (any one or more of the five conditions fail):

1. `gh issue edit <N> --add-label "status:owner-queue"` — surfaces in the next `status_digest` read.
2. Append one line to `~/.claude/projects/<project>/memory/decisions.md`: `- YYYY-MM-DD — refused dispatch of #<N> — <verbatim gate message> — #<N>`.
3. Report to the principal in the batch summary: `#N refused — <verbatim gate message>`.
4. The issue is **not** labeled `agent:dispatch`, not claimed, no label churn beyond the owner-queue flag. `/grill` / `/research` / fixing the cited gap are the unblock paths — once fixed, re-dispatch flips the route.

**No Telegram escalation** even on repeat refuses — last-resort rule. Owner discovers via a `status_digest` read.

**Interactive `/implement` is NOT gated by this check.** The gate guards
*label-routed* dispatch where no operator is present at execution time.
Inline `/implement` keeps the grill trigger checkbox as its in-skill
backstop and can run on any issue (including `status:owner-queue`-tagged
ones) — the operator IS the gate.

## Contract: label (replaces the old queue-enqueue driver)

Per issue that passed the readiness gate, and that has no existing PR/branch (pre-flight §1 above already checked this):

```bash
gh issue edit <N> --add-label "agent:dispatch"
```

**Outcomes:**

- **Label applied successfully** → **dispatched**. Report `#N dispatched (agent:dispatch)`. Per the disclosure at the top of this skill, this means "labeled, pending the executor workflow" until `claude-code-action` is actually wired to `issues.labeled` for this label in this repo — do not claim the work is running.
- **Issue already carries `agent:dispatch`** → **already dispatched**, treat as a no-op collision: report `#N already dispatched — skipping` and don't re-apply.

**Disclosed accepted trade-off — this is check-then-act, not CAS (#931 residual risk).** The "already carries the label?" check above is a plain read, and `gh issue edit --add-label` is a plain write — the same TOCTOU shape #931's RCA found in the old queue/branch model, not a locally-designed no-race guarantee. What's actually different from #931's incident is the *resource* being raced: #931's dispatch created a brand-new branch/PR per attempt, so two concurrent dispatches produced two distinct competing artifacts; here, both racing calls converge on attaching the *same* label token to the *same* issue — a set-membership add, not a new-resource create. GitHub's label-attach is an idempotent state transition (attaching a label the issue already has is a no-op at the data layer, and `issues.labeled` fires only on the actual absent→present transition, not on every API call) — so even if two `/dispatch` runs both pass the read-check concurrently, at most one attachment transition happens and at most one downstream `claude-code-action` trigger should fire. This reliance on GitHub's own idempotent label semantics (rather than an application-level CAS) is unverified in practice — no workflow listens for `agent:dispatch` yet (see disclosure at the top of this skill), so the double-fire behavior can't be empirically confirmed until the executor workflow ships. Tracked in the #1793 follow-up (#1815) as an explicit item to verify once that workflow exists, rather than assumed safe indefinitely.

Claiming (`status:in-progress` + comment) is the executor's job once it actually starts work, not this skill's — same separation of concerns the old queue model used (enqueue ≠ claim).

## Never spawns in-session

Unlike the retired `/delegate`, this skill's job ends at labeling + report.
The `claude-code-action` workflow triggered by `issues.labeled` (once it
exists — see the disclosure above) is the only thing that spawns work.
No `Agent(subagent_type="coding", ...)` call exists anywhere in this skill,
no worktree isolation setup here, no diff review, no merge decision — those
belong to the executor workflow. Post-merge audit of dispatched work has no
dedicated skill as of milestone #70; until one exists, treat it as a manual
review step, same as any other merged PR.

## Pipeline

### 1. Classify each issue: dispatchable or inline

For each issue in the batch:

1. Run pre-flight (5 checks — same as `/implement` §1: assignees, `status:in-progress` label, "Claimed by" comments, existing PR, existing branch).
2. Classify:
   - **Dispatchable** → issue body is self-contained; a headless executor can act on it with no operator present.
   - **Inline** → needs session context / safety review / cross-cutting peripheral vision (route through `/implement`).

Produce a short split plan for the principal before acting. Example:

> Batch: #604, #613, #617.
> - #604 (uncertainty map) → **dispatch** — self-contained, single module.
> - #613 (swept path) → **inline** — safety-adjacent, `planning/`.
> - #617 (docs tweak) → **dispatch** — trivial.

### 2. Advisory readiness gate

Per §Contract above, for every issue routed to **dispatch**. Refused issues exit immediately (owner-queue label, decisions.md line, excluded from the labeling step).

### 3. Label

Per §Contract above, for every issue that passed the gate. Collect dispatched/already-dispatched verdicts per issue.

### 4. Record decision

Append one line to `~/.claude/projects/<project>/memory/decisions.md`: `- YYYY-MM-DD — dispatched batch <#N, #M, ...> — <one-clause why> — #<primary issue>`. One line covers the batch — which issues dispatched, which refused, which stayed inline, why. Batch dispatch always warrants a line (issue implementation is being routed, even though the routing target is a not-yet-shipped executor per the disclosure above).

### 5. Implement inline issues (parallel with dispatched issues waiting on the executor)

For anything kept inline, use the `/implement` pipeline. The two streams are independent — this skill does not block on the executor workflow.

### 6. Report

Batch summary to the principal:

```
#604 dispatched (agent:dispatch)
#613 inline — dispatched via /implement instead
#617 dispatched (agent:dispatch)
```

No further action from this skill. Outcome recording for *dispatched* issues
happens post-merge (manual review — see *Never spawns in-session* above),
once an executor workflow actually exists to produce a merge to review.

## Safety rules
- All `/implement` safety rules apply.
- Never spawn an `Agent` subagent from this skill — that path is retired.
- Never add `status:in-progress` at labeling time — claiming is the executor's job at actual spawn.
- If principal says "параллельно все" but one task is unfit for headless execution → keep it inline and tell the principal why.

## Recovery playbook

See `docs/security/recovery-playbook.md`. Label-trigger specific:

- **Issue labeled `agent:dispatch` but nothing happens** — expected today. No `claude-code-action` workflow yet listens for `issues.labeled` on this label in this repo (see the disclosure at the top of this skill). This is not a stuck row to unstick; it's a missing executor to build. Check the follow-up issue tracking that build; if it's closed and the workflow shipped but the label still does nothing, that's a real bug — file it via `/file-issue`.
- **Issue refused at the readiness gate** — fix the cited gap on the issue (missing AC heading, `needs-*` label still present, no decision reference), then re-run `/dispatch #<N>`. Labels are idempotent, so there's no attempt-suffix bookkeeping to manage the way the old `delegate:<N>:r<k>` idempotency key required.
- **Issue already carries `agent:dispatch` and needs re-dispatch** (e.g. the executor failed and the work needs to run again) — remove the label (`gh issue edit <N> --remove-label "agent:dispatch"`) then re-add it to re-trigger `issues.labeled`, once an executor workflow exists to react to that.
