# GitHub repo setup — manual checklist

Guidance for bringing a new or existing owned repo to the current GitHub-infrastructure
baseline **by hand, in one pass**. This is not enforcement and not a spec for a sync tool —
per-repo deviation is legal by design (a throwaway repo doesn't need the full jarvis gate
stack; redrobot's Free/private plan can't have some of it at all). Read it top to bottom once
per repo, tick what applies, stop there.

**Why this doc exists, not a sync mechanism**: decision `bfd16494-f2e7-46dc-9194-2b43935525f1`
retired the repo-baseline auto-sync approach (milestone #48) — every project needs a
meaningfully different shape, and what actually helps is a document a human follows to set up
a repo's infra, not an automated re-sync that fights per-repo customization. This doc is that
replacement. It describes the *current* jarvis baseline as a reference point to copy from and
adapt, not a canon that must match byte-for-byte. The repo-baseline machinery
(`scripts/repo_baseline/`, its manifests, CONTEXT.md's Repo-baseline glossary section) has
been fully torn down (#1755) — this doc is now the only process for bringing a repo to
baseline.

## 1. Labels

Group by family, not by ad-hoc naming. jarvis's current families:

- **`type`** (bare, no prefix) — `task`, `bug`, `enhancement`
- **`priority:`** — `critical` / `high` / `medium` / `low`
- **`status:`** — `ready`, `in-progress`, `review`, `owner-queue`, `rework-in-progress`
- **`area:`** — one per subsystem (`docs`, `quality`, `skills`, `config`, `infrastructure`,
  `core-agent`, `memory`, `security`, `ci-quality`, `release`, …) — pick the set that matches
  the repo's actual subsystems, don't copy jarvis's list verbatim
- **`needs-*`** — pipeline-stage gates: `needs-triage`, `needs-research`, `needs-prd`,
  `needs-grill`, `needs-plan`, `needs-rebase`
- **`afk:2-plan`** — plan-review: shared-surface/high-churn change requiring a locked
  `## Plan` section before an unattended agent may edit (pairs with `plan:locked` once
  the plan is hashed and locked). `afk:3-human` is the harder tier: true HITL, no
  unattended edit at all. (`class:2`/`class:3`/`tier:1-auto`/`tier:2-review`/`tier:3-human`/
  `unsafe-for-afk` are retired names from an earlier scheme — don't copy them into a new
  repo's label set even if you still see them on older issues here.)
- **`sandcastle`** — AFK queue: issue is safe for an unattended agent to pick up
- **`draft`** — rough idea, not ready for triage
- **`decision-made`** — research done, decision documented in the issue
- Dependabot auto-labels (don't hand-create, they appear on first Dependabot PR):
  `dependencies`, `python`/`github-actions`/etc. per ecosystem

Create labels with `gh label create <name> --description "<desc>" --color <hex>`. Keep
descriptions — they're the only in-UI documentation of what a label means.

**If migrating an existing messy label set**: rename in place (`gh label edit <old> --name
<new>`), never delete-and-recreate — deletion detaches every issue/PR association, rename
preserves it. Collisions (old name maps to an already-existing target) need a manual
merge: re-tag bearers onto the target, verify, then delete the empty source.

## 2. Milestones

Milestone is the **single grouping primitive** — there is no separate epic-issue layer above
it. A slice of work gets a milestone, not a milestone *and* a tracking issue. Use the
milestone description for the PRD/problem-statement/decision-basis content; individual issues
link to it directly (`gh issue edit <N> --milestone "<title>"`), no `Parent: #N` epic pointer
required (recommended for traceability, not enforced — see issue-schema-check below).

Close a milestone only when every issue in it is closed or explicitly deferred with a written
reason. Don't leave milestones open indefinitely as a junk-drawer — an empty or long-stale
milestone is a hygiene smell worth a sweep.

## 3. CI gates

Minimum viable gate set, each as its own workflow file under `.github/workflows/`:

| Gate (check name) | Workflow | What it blocks |
|---|---|---|
| `review` | `code-review.yml` (+ retry wrapper) | Code-gate: Layer A (`ruff check`, lint rule group 1) + Layer B (tiered LLM review) — fails closed on a `{blocking: true, findings: [...]}` verdict or a missing/malformed one |
| `owner-queue-guard` | `owner-queue-guard.yml` | PR carries `status:owner-queue` — explicit manual hold |
| `require-linked-issue` | `pr-body-check.yml` | PR body has no `Closes #N`/`Refs #N`, no `[no-issue]` marker, no `refactor:` prefix, no `priority:critical` hotfix bypass |
| test suite (`pytest`, language-equivalent) | your language's own CI workflow | Tests fail |
| `meta-tests` | `ci-meta.yml` or equivalent | Guard-fixture tests that keep CI config internally consistent (e.g. pinning a required-check name to the job that produces it) |
| gitleaks / secret scan | `gitleaks.yml` | Secret committed |

Repo-custom gates layer on top of this floor (jarvis also runs
`require-paired-migration` — a schema-drift guard specific to its Supabase dependency; its
former `pytest-db` sibling was removed with reactive-core in #1802). Add them as needed; they
don't need to match another repo's set.

**Issue-schema-check** is advisory in jarvis today, not a hard-blocking gate: `issue-checks.yml`
syncs the `area:` label from the issue body's declared area and logs a note (not a failure)
when a task/bug has no parent or milestone. If a repo wants schema violations to actually
block, that's a deliberate escalation from advisory to enforcing — do it explicitly, don't
assume the jarvis pattern already enforces it.

**Branch protection** wires these check names into
`Settings → Branches → Branch protection rules` (or `gh api -X PUT
repos/<owner>/<repo>/branches/<default>/protection`) as required status checks. Two traps:

- A required check that has never run on the branch will deadlock every PR — land the
  workflow first, let it run once, *then* add it to required checks.
- The check *name* in branch protection must exactly match the job's `name:` (or job id if
  unnamed) in the workflow — a job rename silently drops enforcement without erroring
  anywhere. A meta-test pinning name↔job is worth having if this repo will see workflow
  churn.

## 4. `config/protected-paths.json` entry

If the repo participates in AFK/agent dispatch classification, add an entry to
`config/protected-paths.json` (jarvis's copy; adapt path per repo) with two buckets:

- **`hitl`** — identity/security config; any changed file matching these globs is a hard
  refusal for autonomous agents (class 3, human-in-the-loop only). Mirrors the sensitive
  subset of your repo's own protected-file policy (SOUL.md-equivalent, CI/hook config,
  credentials config).
- **`guarded`** — shared surfaces with consumers outside this repo (a shared schema, a memory
  server, a config file another repo depends on). Any match is class 2 (plan-review required,
  AFK-eligible once a plan locks) — not a refusal, just a higher bar.

A repo with no shared surfaces and no identity-adjacent files can have both arrays empty.
Adding a new repo to this file should never require touching skill logic — it's pure data.

## 5. Dependabot config

`.github/dependabot.yml` — one `updates` entry per ecosystem the repo actually uses (jarvis:
`pip` and `github-actions`), each on a weekly Monday schedule:

```yaml
version: 2
updates:
  - package-ecosystem: pip
    directory: "/"
    schedule:
      interval: weekly
      day: monday
    target-branch: main

  - package-ecosystem: github-actions
    directory: "/"
    schedule:
      interval: weekly
      day: monday
    target-branch: main
```

**Gotcha**: `target-branch` must match the repo's actual default branch, not `main`
unconditionally — redrobot's default branch is `master`. A hardcoded `main` on a repo whose
default branch differs sends every Dependabot PR at a branch that doesn't exist, and they
silently never land. Check `gh repo view <owner>/<repo> --json defaultBranchRef` before
copying this file into a new repo.

## 6. PR template

`.github/PULL_REQUEST_TEMPLATE.md` — hand-create per repo from this shape:

```markdown
## Summary

<!-- What changed and why, 2-3 sentences -->

## Why

<!-- Problem being solved, motivation -->

Closes #

## Decisions & Alternatives

<!-- Key choices made during implementation. What alternatives were considered and why they were rejected. -->

-

## Risk Assessment

- **LOW**: <!-- cosmetic, imports, naming -->
- **MEDIUM**: <!-- refactors, new helpers -->
- **HIGH**: <!-- logic changes, safety-adjacent -->
- **CRITICAL**: <!-- data loss, security, breaking API -->

## Testing

- [ ] Unit/integration tests pass
- [ ] CI is green
- [ ] Manual verification (if needed)

## Files Changed

| File | Change |
|------|--------|
| | |
```

The Risk Assessment tiers cross-reference the #1512 risk-assessment carve-out named in
`.claude-userlevel/reference/merge-gates.md` — keep the tier names (LOW/MEDIUM/HIGH/CRITICAL)
in sync with that doc if either changes.

## 7. Issue templates

`.github/ISSUE_TEMPLATE/` — at minimum a `task.yml` and a `bug.yml` form, plus a `config.yml`
disabling blank issues (`blank_issues_enabled: false`) so every issue goes through a
structured form. Match the label vocabulary from §1 in the form's default/required fields
(e.g. an `Area` dropdown that emits `area:<x>` so `issue-checks.yml`-equivalent automation can
read it back out of the body).

## 8. Merge discipline

- **Auto-merge**: enable it repo-wide (`Settings → General → Allow auto-merge`), then turn it
  on per-PR the moment a PR opens non-draft (a small workflow calling
  `gh pr merge --auto --squash` works, or GitHub's own auto-merge checkbox for manual use). A
  PR merged with the default `GITHUB_TOKEN` gets attributed to `github-actions[bot]`, and
  GitHub's bot-recursion-prevention silently suppresses **native** linked-issue auto-close for
  *any* automated (bot or App) merge — even a GitHub App token merging doesn't restore it.
  Two pieces are both required: (1) mint and use a GitHub App token for the merge step so the
  PR's `pull_request: closed` event itself isn't suppressed, and (2) add a second workflow
  triggered on `pull_request: closed` that explicitly closes each issue in the PR's
  `closingIssuesReferences` (jarvis's `pr-merged.yml`) — this is the deterministic close path
  that replaces native auto-close, not an optional extra.
- **Draft is the manual hold** — a PR stays in draft while it's not ready for auto-merge to
  even consider it; flip to ready only once gates should start evaluating.
- **`status:owner-queue`** is the narrower hold for a PR that's content-complete and could
  pass every gate, but the owner still wants to eyeball it before it ships.

### Free-plan / private-repo caveats

A **private repo on GitHub Free** cannot have branch protection rules or paid auto-merge
gating in the same way a public or paid-plan repo can:

- `gh pr merge --auto` is rejected outright.
- Branch protection may be unavailable or limited depending on plan.

For a repo in this situation (jarvis's manifest precedent: redrobot), don't fight the
platform — treat the CI gates as advisory-only (still run them, still read their output) and
merge manually once they're green: `gh pr merge <N> --squash --delete-branch`. Don't retry
`--auto` in a loop hoping it starts working.

## 9. What this doc deliberately does not give you

- A byte-for-byte canon file to copy-paste blind — the workflow *content* above is a shape to
  adapt, not a template to stamp. Copy from an existing repo's actual `.github/workflows/`
  and cut what doesn't apply.
- Automated drift detection or re-sync. If a repo drifts from what this doc describes, that's
  either fine (the repo has its own reasons) or a manual re-pass through this checklist — no
  tooling watches for it.
