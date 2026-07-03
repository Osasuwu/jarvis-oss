---
name: goals
description: "Manage strategic goals — view, set, update, review, close. Goals drive Jarvis's priorities and autonomous decisions."
---

# Goals

Strategic goal management. Goals are NOT tasks — they are outcomes Jarvis pursues.

## Usage

- `/goals` — show all active goals with progress
- `/goals set` — create or update a goal (interactive)
- `/goals review` — review progress, suggest corrections
- `/goals close <slug>` — close a goal with outcome + lessons

## Commands

### `/goals` (default) — Dashboard

1. Call `goal_list(status="active")`
2. Display goals ordered by priority, with:
   - Title, project, priority, deadline (if any)
   - Accomplishment log (progress items marked done, with dates)
   - Risks (if any)
   - Principal focus / Jarvis focus (DB columns are still named `owner_focus`/`jarvis_focus` — schema rename is out of scope)
3. If any goals have deadline within 7 days — highlight them
4. If any P0 goals have no recent progress (no done items in last 14 days) — flag risk

### `/goals set` — Create or Update

Interactive flow:

1. Ask: "What's the goal?" (or parse from args/context)
2. Ask for or infer:
   - `slug` — auto-generate from title if not provided
   - `project` — which project? (null if cross-project)
   - `direction` — which strategic direction?
   - `priority` — P0/P1/P2
   - `why` — motivation
   - `success_criteria` — what does success look like?
   - `deadline` — when? (optional)
   - `progress` — initial milestones
   - `owner_focus` / `jarvis_focus` — division of work
3. Call `goal_set(...)` with all fields
4. Confirm creation

If a slug already exists, update the existing goal.

**From context:** If the user describes a goal in conversation, extract fields and confirm before saving. Don't force the full form — infer what you can.

### `/goals review` — Progress Review

1. Call `goal_list(status="active")`
2. For each goal:
   - Check GitHub issues/PRs related to the project (if applicable)
   - Review accomplishment log — what's been done since last review?
   - Check if deadline is at risk
   - Identify what's NOT progressing (success_criteria without matching accomplishments)
3. Output:
   - Per-goal status: recent accomplishments, gaps, risks
   - Concrete suggestions: re-prioritize? adjust scope? escalate?
4. Append any newly discovered accomplishments via `goal_update(slug=..., progress=[...])`
5. If any goal should be closed — propose it

### `/goals close <slug>` — Close a Goal

1. Call `goal_get(slug=<slug>)`
2. Ask (or infer):
   - Status: `achieved` or `abandoned`?
   - `outcome` — what actually happened?
   - `lessons` — what did we learn?
3. Call `goal_update(slug=<slug>, status=..., outcome=..., lessons=...)`
4. Confirm closure

## Goal Awareness (applies to ALL skills, not just /goals)

Active goals are loaded at session start. They guide every interaction:

- **Before any task:** Does this serve an active goal? If not, say so.
- **If higher-priority goal neglected:** Bring it up.
- **Morning brief:** Plan day around goals, not events.
- **Self-improve:** Only improve what's relevant to goals.
- **Delegate:** Prioritize by goal alignment.
- **Research:** Focus on knowledge gaps for current goals.

This is not a feature — it's the operating model.

## Output Format

```markdown
# Active Goals

## [P0] Redrobot Demo (redrobot)
Deadline: 2026-04-20 (12 days) | Direction: Redrobot production-ready
Done:
- Scenario 1 (2026-04-01)
- Scenario 2 (2026-04-08)
Remaining (from success_criteria):
- Scenario 3
- UI polish
Risks: #38 harder than expected
Principal: Scenario 3 | Jarvis: Monitor #38, infra

---

## [P1] Goals System (jarvis)
No deadline | Direction: Jarvis 2.0
Done:
- Design (2026-03-20)
- DB + MCP methods (2026-03-25)
Remaining:
- Skill
- Integration (CLAUDE.md, SOUL.md)
Principal: Review | Jarvis: Implement
```
