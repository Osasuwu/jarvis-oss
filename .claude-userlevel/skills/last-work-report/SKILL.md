---
name: last-work-report
description: "Summarise the most-recently-closed milestone (or one named explicitly) before running `/improve-codebase-architecture`. Type 2 (intent-triggered). SKELETON — body intentionally minimal until `/improve-codebase-architecture` consumes it; triggers on \"last sprint report\", \"what did we ship\", \"milestone closeout\", or \"/last-work-report\"."
disable-model-invocation: true
model: sonnet
effort: low
---

# Last Work Report

Type 2 skill. Reads the recently-shipped capability slice (one closed milestone) and emits a structured summary the architecture-sweep skill can consume cold, without re-loading the session that closed it.

**Status: SKELETON.** Per #606 acceptance: contract + trigger phrasings are pinned here; the gathering/rendering logic is deliberately deferred until [`/improve-codebase-architecture`](../improve-codebase-architecture/SKILL.md) wires it in. Invoking the skill today should print this section and exit.

## Why

The milestone-close architecture sweep is meant to run in a **fresh session** (per `CLAUDE.md` → "Architecture sweep at milestone close"). A fresh session has no recollection of what just closed — `/improve-codebase-architecture` either explores the repo blind, or the owner mentally reconstructs the last sprint before invoking it. Both modes lose nuance.

`/last-work-report` is the explicit-entry alternative to the auto-surface in `scripts/session-context.py` (#605). When that detection proves brittle, the owner says "last sprint report" and gets the same payload `session-context.py` would inject.

## Inputs

- **Default:** the most-recently-closed milestone in `your-username/jarvis` (highest `closed_at`).
- **Explicit:** `/last-work-report <milestone-number-or-title>` — disambiguate when multiple closed recently.
- **Scope:** single milestone per invocation. Cross-milestone analysis is `/improve-codebase-architecture`'s job, not this skill's.

## Outputs

A markdown block matching the contract `/improve-codebase-architecture` consumes. Shape (TBD-locked once that skill is updated to read it):

```markdown
# Milestone <N> — <title>

Closed <YYYY-MM-DD>. <one-line capability shipped>.

## Slices (closed issues)
- #<n>: <title> — PR #<p> (merged <date>) — <one-line outcome>

## Decisions recorded
- <decision-uuid>: <one-line> (memory: <name>)

## Outcomes recorded
- <task_outcomes-id>: <status> — <one-line>
- <task_outcomes-id>: <status> — <one-line>

## Open follow-ups (not closed by this milestone)
- #<n>: <title> — <status:* label>
```

Field rules:

- **Slices** — issues with `milestone == <N>` and `state == closed`, ordered by `closed_at`. Include the merging PR if linked.
- **Decisions recorded** — `decision_made` episodes with `created_at` between milestone `open_at` and `closed_at`, scoped to the project. Include UUID so `/improve-codebase-architecture` can pull rationale on demand without re-rendering it here.
- **Outcomes recorded** — `task_outcomes` rows with the same time window. `outcome_status` (success/partial/failure) belongs in the line — failure clusters are exactly what the sweep cares about.
- **Open follow-ups** — issues created during the milestone window that weren't closed by it, labelled `status:*`. Signals scope leakage and incomplete capability shipping.

## Trigger phrasings

Owner says any of:

- `/last-work-report` (canonical slash)
- `/last-work-report <milestone-number-or-title>`
- "last sprint report"
- "what did we ship"
- "milestone closeout"

All four must match the same skill — the description field above carries them so the model matches at session start (Type 2 per ADR-0001).

## What this skill is NOT

- **Not a `/status` replacement.** That's `/status-record` + `memory_recall(query="status-snapshot")`. This skill is post-milestone, structured for architecture-sweep input — not an ambient state dump.
- **Not a `/reflect`.** No comms-pattern analysis, no calibration. Just "what shipped, what we decided, what failed".
- **Not analysis.** Reports facts. The sweep does the thinking.

## Boundary with #605

Issue #605 wires `scripts/session-context.py` to auto-surface "Milestone N closed — architecture sweep recommended" at SessionStart. This skill is the **manual-trigger fallback**. Both deliver the same payload shape; #605 is the path of least friction (owner sees the surface, runs the sweep), this skill is the explicit-ask path (owner forgot, or the auto-detect missed).

If #605 ships first and proves reliable for ≥3 milestone closes, this skill stays a fallback. If #605 is brittle, this skill becomes primary.

## Implementation notes (for the future PR that fleshes this out)

When the body is filled in:

- Read `closed_at` from `gh api repos/your-username/jarvis/milestones?state=closed --jq 'max_by(.closed_at)'`.
- Fetch slices via `gh issue list --milestone <N> --state closed --json number,title,closedAt,timelineItems`.
- Fetch decisions via `memory_recall(query="<milestone-title>", type="decision", project="jarvis")` PLUS a direct query on `episodes` filtered by time window (decisions don't always tag the milestone).
- Fetch outcomes via `outcome_list(project="jarvis", since=<milestone open_at ISO>)` filtered to the close window.
- Render the markdown block in Step 4. Single tool call.
- No memory write — this is a synchronous read-and-render skill, not a recorder. (Cross-reference: `/status-record` writes snapshots; this skill emits to the caller.)

Until the body is fleshed out, this skill prints the "SKELETON" notice in its second paragraph and exits.

## Refs

- #606 (this issue) — skeleton.
- #605 — SessionStart auto-surface (sibling).
- #548 (grill Q5 fallback) — original motivation.
- #568 — parent (split).
- [`/improve-codebase-architecture`](../improve-codebase-architecture/SKILL.md) — downstream consumer.
- ADR-0001 — Type 2 trigger model.
