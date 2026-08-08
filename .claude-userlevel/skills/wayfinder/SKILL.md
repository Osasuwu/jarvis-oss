---
name: wayfinder
description: Chart and report the frontier across a multi-milestone slice of open work — which issues are ready to start, which are blocked, which need triage — without inventing new state machinery. Use for "what's next across M-whatever", "what's blocked on what", "chart the map", "wayfinder", or when a plan is too large or too foggy for a single /grill session and needs multi-session tracking.
---

# Wayfinder

Thin, read-only orchestration at multi-session / cross-milestone altitude. It answers "what can move right now, across this whole tree" — it does not do the moving itself.

Design resolved in #1147 via `/grill`, decision `9e3584e4-1e62-4ed8-8d2a-3055bf24e204` (superseding the earlier `7085a34a` "design-only, no skill" framing). Adapted from upstream `mattpocock/skills` `engineering/wayfinder` — see [AIHERO_CREDIT.md](../AIHERO_CREDIT.md) for the adaptation summary.

## Relationship to /grill — stack, not overlap

`/grill` is single-session, one design tree, intuition-to-plan. `/wayfinder` is multi-session, scanning which nodes across the *entire* milestone tree are ready, blocked, or need triage. A `/grill` session resolves one node; `/wayfinder` tells you which node to point `/grill` at next. Never duplicate `/grill`'s interview loop here — if a node needs grilling, route to `/grill` and stop.

## No new state machinery

`/wayfinder` reuses what the tracker already has — it does not add a map file, a parent "wayfinder issue", or any persistent artifact of its own:

- **`needs-*` labels** (`needs-research`, `needs-grill`, `needs-prd`, and the newer `needs-prototype`) mark a node as not-yet-actionable and name what it's waiting on.
- **The milestone hierarchy** (`pillar → goal → milestone → slice`) is the tree being scanned — see the project's CONTEXT.md `Core entities` for the definitions.
- **Native GitHub `blocked_by` edges** (not prose) are the dependency graph. Reading them:

  ```bash
  gh api repos/<owner>/<repo>/issues/<N>/dependencies/blocked_by --jq '[.[] | {number, state, title}]'
  ```

  An issue is blocked iff this list contains at least one entry with `state != "closed"`.

### Label ownership — who sets/removes each `needs-*` label

| Label | Set by | Removed by |
|---|---|---|
| `needs-research` | `/to-tickets` §3a, or manual triage | `/research`, on completion |
| `needs-grill` | `/to-tickets` §3a, or manual triage | `/grill`, on confirmation gate pass |
| `needs-prd` | manual triage | `/to-spec`, on completion |
| `needs-prototype` | manual triage or a `/wayfinder` scan flagging a shape/behavior question | **not** removed by `/prototype` — that skill is explicitly a no-skill-chaining, throwaway-artifact producer (#1154) and does not manage labels. The operator removes `needs-prototype` by hand once the prototype session yields a decision (mirrors how a human resolves any other judgment-call label). |

`/wayfinder` never removes a `needs-*` label itself — that would make it a second writer for state another skill owns, exactly the kind of new machinery the design deliberately avoids.

## Frontier definition

Given a milestone (or an explicit set of milestones/issues), an issue is:

- **Ready (frontier)** — open, carries no `needs-*` label, no `status:blocked` label, and every `blocked_by` edge (if any) points to a closed issue.
- **Blocked** — open, no `needs-*` label, but either carries `status:blocked` or has ≥1 open `blocked_by` edge. Report what it's blocked on when a native edge exists; when only the `status:blocked` label is present with no edge, say so explicitly ("blocked, reason not machine-readable — check issue body/comments") rather than guessing. Confirmed via dry-run against live jarvis issues (#1412, #1375): `status:blocked` is applied manually today and frequently has no matching `blocked_by` edge, so the label must be checked independently of the edge, not inferred from it.
- **Needs triage** — open and carries a `needs-*` label. Report which one, so the right downstream skill (`/research`, `/grill`, `/to-spec`, or a manual `/prototype` session) is obvious.
- **Decision node, not a task** — an issue whose body is scoped to producing a decision (typically `needs-grill` or `needs-prd`) rather than shippable code. Flag these explicitly in the report and never recommend dispatching them to an AFK agent as if they were implementation work — this exact confusion is a documented upstream failure mode (Pocock field reports #625, #518: agents ignored a `needs-*`-equivalent label and "resolved" a decision ticket with code, or a decision ticket got mis-triaged as `ready-for-agent`). `/wayfinder`'s bucketing is the guard against repeating it here.

## Invocation

The principal invokes `/wayfinder` and optionally names a scope (a milestone, several milestones, or "everything open"). Default scope: all open milestones under the project's active pillar/goal tree, per CONTEXT.md `Core entities`.

1. **Gather.** For each open issue in scope: labels, milestone, `blocked_by` edges (via the API call above).
2. **Bucket.** Sort into Ready / Blocked / Needs-triage / Decision-node per the definitions above.
3. **Report.** Present as a short structured list, oldest-first within each bucket:

   ```markdown
   ## Frontier — ready now
   - #123 <title> (milestone: <name>)

   ## Blocked
   - #124 <title> — blocked on #120 (open)
   - #128 <title> — status:blocked, reason not machine-readable — check issue body/comments

   ## Needs triage
   - #125 <title> — needs-grill → route to /grill
   - #126 <title> — needs-prototype → route to a /prototype session

   ## Decision nodes (not implementation work — do not dispatch as-is)
   - #127 <title> — needs-prd, decision-only scope
   ```

4. **Stop there.** `/wayfinder` produces the report and, if the principal picks a node, names the downstream skill to invoke next (`/research`, `/grill`, `/to-spec`, `/implement`, `/delegate`). It does not invoke those skills itself (ADR-0001 — no skill-to-skill calls) and does not change any label or milestone state as a side effect of running.

## Dry-run walkthrough

Before relying on this skill, run it once against a real multi-milestone slice of the project's own open issues and confirm the frontier/blocked/needs-triage/decision-node buckets match what a manual read of the same issues would produce. Record any mismatch as a follow-up — a bucketing bug here silently misroutes downstream work across every future invocation.
