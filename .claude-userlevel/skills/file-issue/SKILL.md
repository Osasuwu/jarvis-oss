---
name: file-issue
description: File ONE well-formed follow-up issue (finding, tech-debt, bug, drive-by task) with required schema metadata, skipping the full /to-tickets pipeline. Use instead of a raw `gh issue create`/`issue_write` mid-session. Decomposing a plan/PRD into multiple slices → /to-tickets.
model: sonnet
effort: low
---

# File Issue

The lightweight path for a **single** follow-up issue. Fills in the metadata the
project's issue schema requires so the issue never lands mis-triaged and never falls
off the board. This is the middle ground between a raw `gh issue create` (no metadata
→ gets flagged / rots) and `/to-tickets` (a full plan → vertical-slice quiz, overkill
for one finding).

The issue tracker, its issue schema, and the triage-label vocabulary are defined in
the **project's CLAUDE.md** (and, where present, its issue templates under
`.github/ISSUE_TEMPLATE/` and any issue-hygiene gate). This skill applies that schema;
it does not restate the vocabulary. If the project has no defined schema, fall back to
the generic fields below.

## When to use which

| Situation | Tool |
|---|---|
| One finding / tech-debt / drive-by bug / follow-up task | **`/file-issue`** (this) |
| A feature/plan that needs breaking into multiple end-to-end slices | `/to-tickets` |
| A raw `gh issue create` mid-session | **stop — use `/file-issue`** |

If while filing you realize the "one issue" is really several vertical slices under a
theme, stop and hand off to `/to-tickets` instead of forcing it through here.

## Required metadata

Read the exact label/field vocabulary from the project's CLAUDE.md and issue templates.
The generic requirements every issue must satisfy:

1. **Type label** — the project's type marker (e.g. `task` / `bug`). Nothing else counts
   as a type.
2. **Milestone** — every issue MUST land in one, or it's invisible to milestone-scoped
   triage. Resolve in order:
   1. **Inherit from parent** — if this spins off another issue, add a `## Parent` section
      with `#NNN`. If the tracker (or a hygiene gate) auto-applies the parent's milestone,
      still set it explicitly here so it's right immediately.
   2. **Inherit from the current work** — spun off the issue/PR you're working on now → use
      its milestone.
   3. **Match by theme** — enumerate the tracker's open milestones and pick the one this fits.
   4. **No fit** — surface it and ask before leaving it orphan. Orphan is only ever a stated
      choice, never a default.
3. **Template-required fields** — whatever the project's task/bug issue-form marks `required`
   (commonly a **component/area** field and an **acceptance-criteria** section). The web form
   enforces these; `gh issue create` bypasses the form, so you must reproduce the required
   sections in the body yourself. Read them from `.github/ISSUE_TEMPLATE/`.
4. **Acceptance criteria** (tasks) — a section with literal `- [ ]` checkboxes. Literally
   verifiable, not "handles edge cases".

## Recommended (not blocking, but apply when known)

- **Priority** — the project's priority-label scheme (per CLAUDE.md — use the canonical one,
  ignore any legacy variants).
- **Domain / area** — the project's domain-label scheme. If the project auto-syncs a domain
  label from a `### Domain` body line, include it.
- **Safety / protected-zone review** — if the change touches a zone the project marks
  safety-critical or protected (per CLAUDE.md), apply the project's safety-review label.
- **`tech-debt`** (or the project's equivalent) — for workarounds / known-debt findings.

## Process

### 1. Classify (silent)

Decide: type; the milestone (walk the resolution order above); priority if you know it;
domain; whether a safety/protected zone is touched; whether it's tech-debt. If this is
really multi-slice → hand off to `/to-tickets` and stop.

### 2. Draft the body

Reproduce the project's required template sections. Generic skeleton:

<issue-template>
## Parent

`#NNN` — only if this spins off an existing issue. Omit otherwise.

<!-- Any project-template-required field, e.g. a component/area section. -->

## What

One paragraph: the finding / the behavior to build / the bug. Describe end-to-end behavior,
not file-by-file implementation. Avoid pasting file paths or code that goes stale — a pointer
to the relevant module is fine, a snippet is not, unless it encodes a decision.

## Acceptance criteria

- [ ] Literally verifiable criterion
- [ ] ...
</issue-template>

For a `bug`, replace **Acceptance criteria** with a **Repro** + **Expected** pair (and omit
task-only required fields the project's bug template doesn't ask for).

For a workaround/tech-debt finding, state in **What**: the workaround applied, and the
condition under which it should be restored/removed.

### 3. Publish with metadata in one shot

```bash
gh issue create --repo <owner>/<repo> \
  --title "<concise title in the project's glossary terms>" \
  --body-file <path> \
  --label "<type>" \
  --label "<priority>" \
  --label "<domain>" \
  --milestone "<resolved milestone title>"
```

Add the project's safety-review label if a safety/protected zone is touched, and the
tech-debt label for debt findings. Prefer `--body-file` over inline `--body` so
markdown/checkboxes survive.

### 4. Verify

Confirm the issue was not flagged for missing metadata by the project's hygiene gate
(e.g. a `needs-triage` label). If it was, read the bot comment for what's missing, fix it,
and re-verify.

```bash
gh issue view <N> --repo <owner>/<repo> --json labels,milestone \
  --jq '{labels: [.labels[].name], milestone: .milestone.title}'
```

### 5. Link back (if applicable)

If this issue is being fixed in the current branch, add `Closes #<N>` to the PR body. If it's
a spin-off you are NOT fixing now, leave it a standalone backlog item — do not modify the parent.
