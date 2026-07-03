# GitHub Process Runbook (Jarvis)

This runbook defines the development process for the Jarvis repository itself.

**Important**: this is a dev process document, not a Jarvis feature spec. Jarvis features are skills and runtime code in `skills/` and `src/`.

Architecture: [`docs/design/jarvis-v2-redesign.md`](../docs/design/jarvis-v2-redesign.md). Live sprint: GitHub milestones. `docs/PROJECT_PLAN.md` is a pointer index.

## 1. Daily Development

When working on this repo:
- Check open issues by status and priority.
- Finish in-progress work before starting new tasks.
- Ensure each task has parent linkage, area label, priority label, and valid status.

## 2. Planning Rules

- Work starts from milestone goals and epics.
- Epic children are execution items (`task`/`bug`), not nested epics.
- Each task should map to one PR.
- Large tasks must be split before implementation.

## 3. Implementation Rules

- Branch naming:
  - `feature/<issue-number>-<slug>`
  - `fix/<issue-number>-<slug>`
  - `chore/<issue-number>-<slug>`
- Default base branch for task work is `main`.
- Use epic integration branches only when child tasks depend on unreleased epic code.
- PR body must include: `Closes #NNN` (or `Fixes`/`Resolves`).
- One PR should not close multiple unrelated tasks.

## 4. Validation Rules

- Test skills against real GitHub projects before PR.
- CI must pass before merge.
- Any behavior-changing PR must include risk and rollback notes.
- Keep `main` always releasable.

## 5. Parent/Epic Rules

- Use GitHub sub-issues to link child tasks/bugs to each parent epic.
- Keep `Parent: #NNN` in child issue body only as optional context.
- Parent epics are not auto-closed.
- When all children close, workflow posts a verification comment on the parent.
- Human owner verifies DoD and closes epic manually.

### Linking sub-issues via API

```bash
# Get internal ID (not issue number), then link to parent
CHILD_ID=$(gh api repos/OWNER/REPO/issues/CHILD_NUMBER --jq '.id')
gh api repos/OWNER/REPO/issues/PARENT_NUMBER/sub_issues \
  --method POST \
  -F sub_issue_id="$CHILD_ID"
```

Use `-F` (not `-f`) — the ID must be sent as integer, not string.

## 6. Scope Guardrails

Current scope: Claude Agent SDK + MCP migration, then PM and research skills expansion.

See [`docs/design/jarvis-v2-redesign.md`](../docs/design/jarvis-v2-redesign.md) §L0 for scope and non-goals.

Any issue that introduces out-of-scope work must be flagged and explicitly approved before execution.
