# Jarvis Copilot Instructions

Instructions for AI agents working in this repository.

## Product Goal

Jarvis is a universal personal AI agent built on Claude Agent SDK + MCP. This repository contains:
- Custom Jarvis skills (in `skills/`)
- SOUL.md personality configuration
- Agent runtime setup and configuration
- Project documentation

Current priority: PM skills for managing multiple GitHub projects (triage, reporting, issue health).

Next: research skills (web research, topic analysis, learning assistance).

## What This Repo Is NOT

The `.github/` workflows (CI, PR checks, issue validation) are development tools for this repository — they are NOT Jarvis features. Jarvis features are skills and runtime code in `skills/` and `src/`.

## Operating Model

Single human owner, agent-assisted development.

1. Plan in issues and epics.
2. Execute through small PRs linked to one issue.
3. Skills are the deliverable — each skill is a directory with SKILL.md.
4. Test skills on real projects before considering them done.

## Git Rules

- Branch naming: `feature/<issue-number>-<short-desc>`, `fix/<issue-number>-<short-desc>`, `chore/<issue-number>-<short-desc>`.
- PR body must include linked issue line: `Closes #<issue-number>` (or `Fixes`/`Resolves`).
- Do not merge directly to `main`.
- Keep one task per PR.
- Default branch base is `main`.
- Create an epic integration branch only when child tasks depend on unreleased epic changes.
- If there is no dependency, create task branches directly from `main`.

## Issue Hierarchy

Milestone -> Epic -> Task/Bug

- Every task must be linked to parent epic using GitHub sub-issues.
- `Parent: #NNN` in body is optional and informational only.
- Every epic must have a `Children` section with markdown checkboxes.
- Epic children must be `task`/`bug` issues, not `epic` issues.
- Epics are closed manually after DoD verification.

### Linking sub-issues via API

To link a child issue to a parent epic programmatically:

```bash
# Get the internal ID of the child issue (NOT the issue number)
CHILD_ID=$(gh api repos/OWNER/REPO/issues/CHILD_NUMBER --jq '.id')

# Add it as a sub-issue to the parent
gh api repos/OWNER/REPO/issues/PARENT_NUMBER/sub_issues \
  --method POST \
  -F sub_issue_id="$CHILD_ID"
```

Note: use `-F` (not `-f`) so the ID is sent as integer.

## Labels

Type labels:
- `epic`, `task`, `bug`

Priority labels:
- `priority:critical`, `priority:high`, `priority:medium`, `priority:low`

Status labels:
- `status:ready`, `status:in-progress`, `status:review`, `status:blocked`

Area labels:
- `area:skills`, `area:config`, `area:docs`, `area:quality`, `area:infrastructure`

## Quality Gates

For code/skill changes:
- test skills against real GitHub projects before PR,
- keep CI green,
- document what the skill does and its limitations in SKILL.md.

For process changes:
- ensure issue templates and workflows remain consistent,
- preserve parent-child traceability.

## PR Reviews

The Claude code-review bot reviews every PR (via `code-review.yml`); Copilot is no longer used. Before merging:
1. Check the review comment: `gh api --paginate repos/{owner}/{repo}/issues/NUMBER/comments`. The bot posts as an **issue-comment**, not a PR review, so it does NOT appear in the Reviews tab (`--paginate` so it isn't missed past the first page).
2. Address valid findings with code changes, or explain why no change is needed.
3. Check ALL reviewers — don't assume the Reviews tab is the only place feedback lands.
