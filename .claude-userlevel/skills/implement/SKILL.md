---
name: implement
description: This skill should be used when the principal asks Jarvis to implement a SINGLE GitHub issue directly in the current session, or says "реализуй #42", "сделай #42", "implement #X". For MULTIPLE issues that can run in parallel use /delegate instead. Do NOT trigger for viewing, triaging, or discussing issues — only for actual implementation requests.
version: 2.0.0
---

# Implement Skill

Autonomously implement a GitHub issue **inline, in the current session** — no subagents.

Use this when the work benefits from the full session context (memories just loaded, recent decisions, cross-cutting awareness) or when the issue is safety-adjacent and you can't afford a context-blind coding agent.

Memory recall and the `record_decision` contract come from user-level CLAUDE.md `### Memory & decision protocol` — they are session-wide and don't need restating here. The skill-specific gates below are what `/implement` adds on top.

## Usage

Invoke when principal says "реализуй #42", "сделай #42", "implement #X".
Single-issue by default. If multiple issues arrive but only one needs session context → implement the context-heavy one here, hand the rest to `/delegate`.

Target repo: determined from context (CWD, recent conversation, user mention). If ambiguous, ask. Read `config/repos.conf` for the full list of tracked repos.

## Contract: dispatch routing (mechanical / TDD-mode / `grill_required`)

Per ADR-0001, skills do not self-trigger mid-task ("Type 3" is rejected). `/implement` does **not** run `/grill` inline (and there is no standalone `/tdd` skill — TDD-mode is operated from `_shared/tdd/` reference docs as inline discipline). Instead it inspects two inputs at the very start of the pipeline and routes to one of three branches.

**Inputs** (run both before the dispatch table):

1. **SOUL.md `### Grill trigger checkbox`** against the issue body — fetch the body first:

   ```bash
   gh issue view <N> --repo <owner/repo> --json title,body --jq '.title + "\n\n" + .body'
   ```

   Answer:
   - Touches user-visible behavior? (not cosmetic / refactor / doc-fix)
   - Touches domain logic / algorithmics / physics?
   - Will tests be non-trivial?
   - Crosses existing non-trivial code?

2. **Grill artifact for this issue** — present iff *either* of the following holds:

   - **(a) working_state** — `memory_get(name="working_state_<project>", project="<project>")` where `<project>` is the short project slug (`jarvis`, `redrobot`), matching the convention in `scripts/session-context.py`. If the returned record references this issue number alongside one or more decision UUIDs, the artifact is present. The exact key shape inside the record (`decision_uuids[]` keyed by issue, an episodes list, free-form notes) is project-controlled — accept any structure where a decision UUID is reachable from the issue number; if `/grill` populated working_state for this issue, the link will be there. If working_state has no entry for this issue, fall through to (b).
   - **(b) issue body** — the issue body contains a heading starting with `## Decisions` (prefix match — `## Decisions`, `## Decisions & Alternatives`, etc.) AND that section cites at least one decision UUID. This is the opt-in path for manually-annotated or grill-refined issue bodies (e.g. #593/#594/#595/#596 in the TDD-wiring chain). The automated `/to-issues` template does not yet emit this section — a separate issue tracks adding it; until then `## Decisions` in the body is treated as a deliberate annotation by the author.

**Dispatch table** — pick exactly one branch:

| checkbox | grill artifact present for this issue? | route |
|---|---|---|
| 0 yes | n/a | **mechanical-mode** → continue to §1 |
| ≥1 yes | yes (UUIDs in working_state OR cited in issue body) | **TDD-mode** → §4-TDD instead of §4 (rest of pipeline unchanged) |
| ≥1 yes | no | **exit `grill_required`** |

### Branch: `grill_required` exit

Emit the structured block below and stop the pipeline. No claim, no branch, no decision recorded:

```
EXIT: grill_required
issue: <owner/repo>#<N>
reason: trigger-checkbox-fired (<count>/4 yes); no grill artifact in working_state or issue body
next: run /grill against #<N>, then re-dispatch /implement #<N>
```

The orchestrator parses this, runs `/grill` in a fresh session (so the smart-zone budget is intact), updates the issue AC + CONTEXT.md + memory, then re-dispatches `/implement #<N>`. On the second run the grill artifact is present and the dispatch routes to TDD-mode.

### Branch: TDD-mode

Continue through §1–§3 (pre-flight, fetch, claim+branch+record_decision) as in mechanical-mode. Then take **§4-TDD** in place of §4. §5–§8 (commit/PR/outcome/cleanup) are shared.

No symmetric "skip TDD" override: a grill artifact is a positive commitment to red→green→refactor for this issue. If the principal disagrees with TDD-mode for a specific grilled issue, the right move is to re-grill (which may resolve to a different approach) rather than bypass the loop.

### Branch: mechanical-mode

The original flow. Most "fix typo / bump dep / move file" issues land here. Continue to §1.

**Override**: if the principal explicitly says "skip grill, just implement" on a checkbox-fired issue with no artifact, proceed via mechanical-mode — but record the override in the §3 decision rationale with lowered confidence.

### Re-entry is stateless

Every `/implement` entry re-runs the checkbox and re-reads `working_state_jarvis`. There is no `tdd_mode` flag carried in from the orchestrator. This means: when `/grill` finishes and the orchestrator re-dispatches `/implement #N`, the route flips from `grill_required` → TDD-mode automatically because the grill populated the artifact. Same code path, different input state.

## Pipeline

### 1. Pre-flight checks (parallel work protocol)

Before claiming ANY issue, run 5 checks:
1. `assignees` — someone already assigned?
2. `status:in-progress` label?
3. Comments with "Claimed by"?
4. `gh pr list` — existing PR for this issue?
5. `git branch -r | grep feat/<N>-` — existing branch?

If ANY check positive → issue is taken, skip it.

### 2. Fetch & analyze

```bash
gh issue view <N> --repo <owner/repo> --json number,title,body,labels,milestone
```

Read the issue body carefully. Check parent issue/epic for context.
Identify: files to change, acceptance criteria, safety implications.

**Safety-critical zones** (`driver/`, `planning/`, `mujoco/`):
- Post analysis + plan as comment
- Wait for principal approval before implementing
- Do NOT dispatch to subagents (keep inline — this skill is the right tool)

### 3. Claim, branch, record decision

```bash
gh issue edit <N> --add-label "status:in-progress"
gh issue comment <N> --body "Claimed by Jarvis. Branch: feat/<N>-<slug>"
git checkout master && git pull
git checkout -b feat/<N>-<slug>
```

Then emit `mcp__memory__record_decision` per the contract in user-level CLAUDE.md `### 3. record_decision contract`. Issue implementation always satisfies trigger #1 — the call is non-optional. `memories_used` carries UUIDs from the session-start recall map.

#### 3a. Process preflight gate (mandatory — run before opening PR)

Process findings that surface in code-review round 1 inflate the rework count with zero-value cycles. Fix them before the PR opens:

```bash
# Milestone set?
gh issue view <N> --repo <owner/repo> --json milestone --jq '.milestone.title // "MISSING"'

# Domain label present? (software/hardware/integration/safety for redrobot; equivalent for others)
gh issue view <N> --repo <owner/repo> --json labels --jq '[.labels[].name] | join(",")'

# Branch name matches convention?
git branch --show-current  # must be feat/<N>-<slug>

# PR body draft will have Closes #<N> on its own line?
# Verify by checking the PR template / your draft
```

Fix any gap now:
- Missing milestone → `gh issue edit <N> --milestone "<name>"`
- Missing domain label → `gh issue edit <N> --add-label "software"` (or whichever applies)
- Wrong branch name → rename before first push: `git branch -m feat/<N>-<correct-slug>`

Process issues are not code bugs — they take 10 seconds to fix here vs. a full rework round if caught by the reviewer.

### 4. Implement

**Protected files — policy depends on who is editing.** The canonical list (repo-level + user-level `~/.claude/*`) lives in [`docs/security/agent-boundaries.md`](../../../docs/security/agent-boundaries.md). Don't duplicate it here — check that file before editing.

- **Subagent dispatch (`/delegate`)** — never edits protected files. If the task requires it, escalate to inline `/implement`.
- **Inline `/implement` with explicit principal approval in-session** — MAY edit protected files. Document the change prominently in the PR body (mark the file `[PROTECTED]` in the §Files Changed list + rationale) so the principal sees it before merge.
- **Inline `/implement` without explicit approval** — document the needed change in the PR body and leave the file untouched for the principal.

#### 4a. Already-done audit (mandatory gate)

Before writing any code, enumerate acceptance-criteria symbols from the issue body and grep each:
- `rg -n "<symbol>"` for functions, classes, flags, constants, test names — scoped to likely files
- Read the hits — confirm the existing code actually satisfies the criteria, including test coverage

Three outcomes:
- **All present + tests cover them** → STOP. Comment on the issue with `file:line` evidence, close as `not-planned` referencing the implementing PR/commit, record `success` outcome with `lessons` noting pre-existing implementation. No branch, no PR.
- **Partial** → proceed, but narrow scope to what is actually missing. Note the partial starting state in the PR body Summary.
- **None** → proceed with full scope.

Why this is a gate, not a suggestion: recurring pattern (#237 closed as dup of #209; #656 partial — only tie-breaker missing; tool-width Z absent for a month under shared assumption it existed; multi-run batch where 2 of 6 issues were already done). 30-second grep pays for itself every time.

#### 4b. For each change

- Read existing code first (Read tool)
- Check patterns in the codebase (Grep/Glob)
- Edit existing files, don't create new ones unless needed
- Run lint: `ruff check --fix && ruff format` (Python), `npx tsc --noEmit` (TS)
- Run relevant tests: `pytest tests/test_<module>.py -x -q`
- Build frontend: `npm run build`
- **Normalize EOL** (Windows only, before first commit): `git add --renormalize .` — prevents CRLF phantom diffs from appearing as changes on the Linux CI runner and triggering spurious re-reviews

#### 4c. E2E smoke before claiming done (I/O-heavy / schema-touching work)

Unit tests systematically miss integration bugs on:
- File I/O — Windows CRLF inflating byte budgets, path separators, encoding (#281)
- Network/DB I/O — composite unique constraints, PostgREST quirks (#281, #284)
- DB schema changes — migration + live row verification (#288)
- Hook registration — SessionStart, PreCompact, etc. (#281 settings.json)
- Subprocess invocation — APScheduler pickling, env propagation (#304, #298)
- Import-target scripts with venv re-exec guards (#313)

Rule: if the change touches any of the above, run one real-input smoke **before** marking the outcome `success`:
- File I/O — operate on a real file (not a synthetic fixture) and byte-compare output
- DB writes — run the write against live Supabase and verify the row shape
- Schema — apply the migration to a branch DB and run the affected smoke
- Hooks — trigger the hook event manually and confirm the side effect
- Subprocess/scheduler — run in a separate shell long enough to confirm restart / pickle behavior

If unit tests green but smoke fails → outcome is `partial`, and the `lessons` field must describe the gap so a future session knows what the unit tests did not catch.

#### 4d. Post-implementation AC gate (mandatory — run before §5)

Root cause of PR #1011's 5 rework rounds: acceptance criteria were present in the issue but the implementation was stubbed — call sites existed nowhere outside the tests. This gate catches "written but not wired" before the PR opens.

For each acceptance criterion bullet in the issue body:

1. **Symbol grep** — extract the key symbol (function, class, flag, field) and verify it exists:
   ```bash
   rg -n "<symbol>" --type py   # or --type ts
   ```

2. **Call-site check** — if the AC says "X is invoked when Y", verify call sites exist outside test files:
   ```bash
   rg -n "<symbol>" --type py | grep -v "^tests/"
   ```
   Zero hits outside tests → the feature is unreachable; fix before pushing.

3. **AC test pass** — run the test(s) specifically covering this AC:
   ```bash
   pytest tests/ -k "<ac_keyword>" -x -q
   ```

If ANY AC fails this triple-check → fix now (takes minutes) vs. rework round (takes hours).

Also: for any new function or class added, run a minimal call/instantiation sanity check:
```bash
python -c "from <module> import <Symbol>; <Symbol>()  # or whatever the simplest valid call is"
```
A `TypeError` or `AttributeError` on first call is the most embarrassing rework trigger. It takes 5 seconds to catch here.

### 4-TDD. Implement in TDD-mode

Engaged when the §Contract dispatch table routes here. Replaces §4 — but §4a (already-done audit), §4b (per-change hygiene), and §4c (E2E smoke) above all still apply; the constraints they impose are restated in Operating discipline below.

**Procedural source: [`.claude-userlevel/skills/_shared/tdd/tdd-loop.md`](../_shared/tdd/tdd-loop.md).** Load it as your operating procedure for this issue. Do not duplicate the loop here — read the file and follow it. Related references in the same directory: [tests.md](../_shared/tdd/tests.md), [mocking.md](../_shared/tdd/mocking.md), [refactoring.md](../_shared/tdd/refactoring.md).

**Operating discipline:**

- §4a (already-done audit) still runs first — TDD-mode is no excuse to skip it. Symbols from the issue AC drive the grep; if the behavior already exists with tests, stop and close as `not-planned`.
- Iterate one acceptance-criterion bullet at a time. Per AC item: write a failing test → confirm RED → write the minimal implementation → confirm GREEN → refactor only what is now under green coverage → next AC item. Do **not** write all tests first then all code (the anti-horizontal-slicing rule in `tdd-loop.md` is binding).
- Every test must trace back to an AC bullet. If a test does not, the test is either out of scope or evidence the AC is incomplete — in the latter case stop and escalate (re-grill, do not invent AC inline).
- Refactor permission is scoped to code freshly covered by a passing test in this session. Adjacent untested code is not in refactor scope — either write a characterization test first (then it is in scope) or flag a follow-up issue and leave it.
- §4c (E2E smoke) still applies before marking the outcome `success` when the change touches I/O / schema / hooks / subprocess areas.
- ADR-0001 compliance: do not invoke `/grill` or any other skill mid-task. The reference docs in `_shared/tdd/` are read as files, not as skill invocations.

Final pass before §5: run the full test suite for the touched module(s), not just the AC-tied tests. Green suite is the precondition for opening the PR.

### 5. Commit & PR

```bash
git add <specific files>
git commit -m "<type>(<scope>): <description> (#N)"
git push -u origin feat/<N>-<slug>
```

**PR body must be rich and informative** — this is the primary context for reviewers (human and the Claude code-review bot). Use this template:

```markdown
## Summary
<what changed, 2-3 sentences — the "what">

## Why
<problem being solved, link to issue — the "why">
Closes #<N>

## Decisions & Alternatives
- **Chose X because Y** (alternative Z was rejected because...)
- Trade-offs: <what we gained, what we gave up>
- <any non-obvious choices that a reviewer would question>

## Risk Assessment
- **LOW**: <cosmetic, imports, naming — safe to auto-apply>
- **MEDIUM**: <refactors, new helpers — review recommended>
- **HIGH**: <logic changes, safety-adjacent — must review manually>
- **CRITICAL**: <data loss risk, security, breaking API — block merge until reviewed>

## Testing
- <commands run: pytest, ruff, tsc, npm run build>
- <what was verified: specific scenarios, edge cases>

## Files Changed
- `file.py` — <why this file, what changed>
```

Create PR:
```bash
gh pr create --title "<type>(<scope>): <description>" --body "$(cat <<'EOF'
<filled template above>
EOF
)"
```

**Why this matters**: the Claude code-review bot and human reviewers see reasoning inline, not just diff. HIGH/CRITICAL risks are flagged before review starts. No back-and-forth asking "why did you do X?"

### 6. Record outcome

After PR creation (or failure at any step), record the outcome for the Outcome Tracking & Learning pillar:

```
outcome_record(
  task_type: "delegation",
  task_description: "<issue title> (#N)",
  outcome_status: "success" | "partial" | "failure",
  outcome_summary: "<what happened — PR created, tests passed/failed, etc.>",
  goal_slug: "<related goal if known>",
  project: "<repo name>",
  issue_url: "<issue URL>",
  pr_url: "<PR URL if created>",
  memory_id: "<primary informing memory id>",   # see rule below
  tests_passed: true/false,
  lessons: "<anything non-obvious learned>",
  pattern_tags: ["delegation", "inline", "<area>"]
)
```

**Rule — primary informing memory**: pass the first element of the §3 `record_decision` call's `memories_used` that is a **memory-row UUID** — an id that came from `memory_recall`/`memory_get` (the recall map). `memories_used` may legitimately also carry decision-**episode** UUIDs (a grill decision cited in the issue's `## Decisions` section, a `record_decision` return value) — those are NOT valid here: the FK `task_outcomes.memory_id → memories(id)` rejects them with 23503 (bitten 2026-07-02, #971 outcome). Provenance is the discriminator, not shape — both are UUIDs; only where you got it tells them apart. If no element is a memory-row UUID (or `memories_used` was empty), omit `memory_id`. Never pass multiple — the FK is a single UUID and `memory_calibration` joins on one memory per outcome; richer attribution belongs at the view layer, not the row.

**Always record**, even on failure — failed outcomes are the most valuable for learning.

### 7. Batch (if multiple issues kept inline)

When implementing multiple related issues back-to-back:
- Group into one branch if they touch the same files
- Separate branches for independent changes (can be merged independently)
- Address Claude code-review findings promptly

### 7.5. Merge policy

**The current session (the one running /implement) CAN merge — no permission needed for routine PRs:**
- Tests green + Claude code-review comment checked & addressed + LOW/MEDIUM risk → **merge without asking**
- HIGH/CRITICAL risk or safety-critical zone (`driver/`, `planning/`, `mujoco/`) → wait for principal explicit approval
- CI infra-blocked (billing failure, empty `steps` array — not *failing* tests) → merge if local tests green AND Claude code-review clean
- Claude code-review findings are advisory — address substantive ones, ignore style nits. The bot posts as an **issue-comment** (`gh api repos/<owner>/<repo>/issues/<n>/comments`), not a PR review — check it explicitly; "no Copilot review" is no longer a valid merge basis.

Waiting for manual review on every LOW-risk PR is the anti-pattern. See memories: `pm_autonomy_redrobot`, `copilot_review_advisory_only`, `no_confirm_commits_pushes_merges`.

### 8. Post-merge cleanup

After a PR is merged (or when returning to a previously merged branch):
```bash
git checkout master && git pull
git branch -d feat/<N>-<slug>
```

This prevents stale branch accumulation. If the branch has unmerged work, `-d` will refuse — that's correct, don't force it.

## Safety rules
- Check `git status` before branching — abort if dirty
- Never force-push to `master` / `main` or a shared branch
- If change fails tests or breaks build, fix before pushing
- Safety-critical code (`driver/`, `planning/`, `mujoco/`): analyze and comment, don't implement without approval
- Merge policy: see §7.5 — LOW/MEDIUM routine merges are autonomous, HIGH/CRITICAL wait for principal

## Diff review (before marking done)

Before creating the PR, review your own changes:
```bash
git diff main...HEAD --stat
git diff main...HEAD
```

Check for:
- **Scope fit**: does the file list match the issue scope? Unrelated files → revert them before pushing (memory `check_pr_scope_fit_at_open_time`)
- Files that shouldn't have been modified (especially protected files)
- Debug code, `console.log`, `print` statements left behind
- Unrelated changes that crept in
- Secrets or credentials in any form
- **Symmetric patterns**: when fixing a class of bug, grep for sibling instances across the file AND other files — not just the one the reviewer flagged (memory `feedback_symmetric_fixes`)

If the diff looks wrong, fix it before pushing.

## Recovery playbook

See `docs/security/recovery-playbook.md` for how to handle:
- Broke a file → revert from main
- Corrupted memory → `memory_restore`
- Created bad PR → close + delete branch
- Committed to wrong branch → cherry-pick + reset
