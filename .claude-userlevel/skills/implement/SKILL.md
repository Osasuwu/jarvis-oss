---
name: implement
description: Implement a SINGLE GitHub issue directly in the current session. Triggers: "реализуй #42", "сделай #42", "implement #X". Multiple parallel issues → /dispatch. NOT for viewing, triaging, or discussing issues — implementation requests only.
version: 2.0.0
---

# Implement Skill

Autonomously implement a GitHub issue **inline, in the current session** — no subagents.

Use this when the work benefits from the full session context (memories just loaded, recent decisions, cross-cutting awareness) or when the issue is safety-adjacent and you can't afford a context-blind coding agent.

Decisions and working state are logged as plain lines under native auto memory (`~/.claude/projects/<project>/memory/decisions.md`, `handoff.md`) — see §3 and §6 below for the exact format. The skill-specific gates below are what `/implement` adds on top.

## Usage

Invoke when principal says "реализуй #42", "сделай #42", "implement #X".
Single-issue by default. If multiple issues arrive but only one needs session context → implement the context-heavy one here, hand the rest to `/dispatch`.

Target repo: determined from context (CWD, recent conversation, user mention). If ambiguous, ask. Read `config/repos.conf` for the full list of tracked repos.

## Contract: dispatch routing (mechanical / TDD-mode / `grill_required`)

Per ADR-0001, skills do not self-trigger mid-task ("Type 3" is rejected). `/implement` does **not** run `/grill` inline. Instead it inspects two inputs at the very start of the pipeline and routes to one of three branches.

**Inputs** (run both before the dispatch table):

1. **Grill trigger checkbox** (canonical text: `~/.claude/reference/engineering-principles.md` → *Grill trigger checkbox*; restated verbatim below because this is where it fires) against the issue body — fetch the body first:

   ```bash
   gh issue view <N> --repo <owner/repo> --json title,body --jq '.title + "\n\n" + .body'
   ```

   Answer:
   - Touches user-visible behavior? (not cosmetic / refactor / doc-fix)
   - Touches domain logic / algorithmics / physics?
   - Will tests be non-trivial?
   - Crosses existing non-trivial code?

2. **Grill artifact for this issue** — present iff the issue body contains a heading starting with `## Decisions` (prefix match — `## Decisions`, `## Decisions & Alternatives`, etc.) AND that section states the grill's resolution inline (a plain-prose decision line, no UUID required). This is the opt-in path for manually-annotated or grill-refined issue bodies (e.g. #593/#594/#595/#596 in the TDD-wiring chain). The automated `/to-tickets` template does not yet emit this section — a separate issue tracks adding it; until then `## Decisions` in the body is treated as a deliberate annotation by the author.

**Dispatch table** — pick exactly one branch:

| checkbox | grill artifact present for this issue? | route |
|---|---|---|
| 0 yes | n/a | **mechanical-mode** → continue to §1 |
| ≥1 yes | yes (`## Decisions` section states the resolution inline) | **TDD-mode** → §4-TDD instead of §4 (rest of pipeline unchanged) |
| ≥1 yes | no | **exit `grill_required`** |

### Branch: `grill_required` exit

Emit the structured block below and stop the pipeline. No claim, no branch, no decision recorded:

```
EXIT: grill_required
issue: <owner/repo>#<N>
reason: trigger-checkbox-fired (<count>/4 yes); no grill artifact (`## Decisions` section) in issue body
next: run /grill against #<N>, then re-dispatch /implement #<N>
```

The orchestrator parses this, runs `/grill` in a fresh session (so the smart-zone budget is intact), updates the issue AC + CONTEXT.md + the issue's `## Decisions` section, then re-dispatches `/implement #<N>`. On the second run the grill artifact is present and the dispatch routes to TDD-mode.

### Branch: TDD-mode

Continue through §1–§3 (pre-flight, fetch, claim+branch+log the decision) as in mechanical-mode. Then take **§4-TDD** in place of §4. §5–§8 (commit/PR/outcome/cleanup) are shared.

No symmetric "skip TDD" override: a grill artifact is a positive commitment to red→green→refactor for this issue. If the principal disagrees with TDD-mode for a specific grilled issue, the right move is to re-grill (which may resolve to a different approach) rather than bypass the loop.

### Branch: mechanical-mode

The original flow. Most "fix typo / bump dep / move file" issues land here. Continue to §1.

**Override**: if the principal explicitly says "skip grill, just implement" on a checkbox-fired issue with no artifact, proceed via mechanical-mode — but record the override in the §3 decision rationale with lowered confidence.

### Re-entry is stateless

Every `/implement` entry re-runs the checkbox and re-reads the issue body's `## Decisions` section. There is no `tdd_mode` flag carried in from the orchestrator. This means: when `/grill` finishes and the orchestrator re-dispatches `/implement #N`, the route flips from `grill_required` → TDD-mode automatically because the grill populated the `## Decisions` section. Same code path, different input state.

## Exploratory tasks

Dispatch to this section is by **task type**, never by repo path — a slice is exploratory when the issue frames a hypothesis with an acceptance criterion pre-registered before the run, not when it happens to touch a particular directory. Skills stay issue-agnostic: nothing below names a repo-specific path. This section runs alongside the §Contract dispatch above, not instead of it — an exploratory issue still routes through mechanical/TDD-mode/`grill_required` for its *implementation* half; this section governs the *experiment* half.

**Recognizing an exploratory task**: the issue reads as a question or hypothesis ("does X improve Y", "is Z the cause of W") rather than a spec, and states — before any run happens — what result would confirm or refute it. If the acceptance criterion is written or revised *after* looking at a run's output, the slice is not pre-registered and does not qualify; register the criterion first, then run.

### AFK-fit: objective oracle required

An exploratory slice is AFK-eligible **if and only if** the hypothesis's pre-registered acceptance criterion has an objective, machine-checkable oracle. No human judgment call in the loop — the run either resolves the criterion the way a script can check it, or it doesn't, and if it doesn't the slice is interactive, not AFK.

The vocabulary for "objective machine-checkable oracle" (SLR arxiv:1804.01954) is exactly one of:

- **pseudo-oracle** — a second, independent implementation whose output the run is checked against
- **analytical solution** — a closed-form or derived expected value, computed independently of the run
- **metamorphic relation** — a relation that must hold between two related runs when no single-run oracle exists
- **property invariant** — a property that must hold regardless of input, checked mechanically against the run's output
- **golden run** — a previously-validated reference run the new run is compared against

If none of these five apply to the hypothesis's acceptance criterion, the slice has no oracle from this vocabulary — it is interactive, and stays with a human in the loop. External practice draws the same line: a pipeline with an objective oracle runs unattended, while a pipeline that only produces a report or analysis for a person to judge still gets that output reviewed — a human judgment call is not an objective enough oracle to run AFK.

### Negative results are mandatory journal entries

A negative result — the hypothesis is not confirmed, the run doesn't reproduce, the experiment fails — is **not** a reason to skip the record. Losing the experimental intent behind a run is the primary driver of irreproducibility (arxiv:2506.16051); an unrecorded negative result is a lost experiment, not a saved one. Every exploratory run, positive or negative, must leave a journal entry: what the pre-registered criterion was, what the run actually produced, and whether the criterion resolved true or false. Do not defer this to "only write it up if it works."

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

### 3. Claim, branch, log the decision

```bash
gh issue edit <N> --add-label "status:in-progress"
gh issue comment <N> --body "Claimed by Jarvis. Branch: feat/<N>-<slug>"
git checkout master && git pull
git checkout -b feat/<N>-<slug>
```

Then append one line to `~/.claude/projects/<project>/memory/decisions.md`: `- YYYY-MM-DD — <decision> — <why, one clause> — #<N>`. Issue implementation always gets a line — the append is non-optional, no UUID required.

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

#### 3b. Plan-gate trigger (interactive lane, #1688)

Runs **after** claim+branch, **before** any §4/§4-TDD edit. This is the interactive lane's ex-ante half of two-point plan-review classification — the CI diff-gate (#1687) is the ex-post, fail-closed backstop that re-classifies from the real diff, so this step does not need an exact diff, only the files-to-touch and a rough churn estimate already known from §2's analysis.

```python
from agents.implement_plan_gate import evaluate_trigger
from agents.plan_review_config import load_plan_review_config

config = load_plan_review_config("config/plan_review.yaml")
result = evaluate_trigger(
    config,
    paths=<estimated files-to-touch from §2>,
    labels=<issue's current labels>,
    churn_lines=<rough estimate — line count of the planned diff>,
)
```

`evaluate_trigger` reuses `agents.plan_classifier.classify_task_row` — the one named policy entry point every consumer (interactive lane, drain, container pick, CI diff-gate) calls — so the thresholds live in `config/plan_review.yaml`, not here.

- **`result.requires_plan is False`** (ordinal 1, or ordinal 3 mechanical, or `priority:critical` carve-out on `afk:2-plan`) → proceed straight to §4/§4-TDD, no planner involved.
- **`result.requires_plan is True`** (`afk:2-plan`, no carve-out) → before any edit:
  1. Invoke the `Agent` tool with `subagent_type: "planner"` (contract: `.claude/agents/planner.md`), passing the issue number, body, and the same `paths`/`churn_lines` estimate.
  2. The planner returns a locked `## Plan` section (hash-locked per `agents/plan_lock.py`'s grammar) once its critic panel reaches consensus. Write that section verbatim to the issue body — `gh issue edit <N> --body "$(gh issue view <N> --json body --jq .body)$(printf '\n\n')<plan section>"` (append, don't overwrite the existing body) — **before** the first §4/§4-TDD file edit. The CI diff-gate reads this lock on PR push; an edit landing before the lock is written risks an `afk:2-plan` diff with no verifiable plan, which the gate blocks.
  3. **Unresolved blocking objection** (AC5): if the critic panel does not reach consensus after one revision cycle, the planner subagent stops and returns the unresolved objections instead of forcing a plan through (`.claude/agents/planner.md`'s own escalation clause). At that point **the operator decides** — do not silently proceed past an unresolved blocking objection or invent a plan yourself. Post the objections as an issue comment and pause the pipeline; resume only once the principal weighs in (approve a revised plan, override the objection, or redirect scope).

**`priority:critical` carve-out** (AC3): the label skips only the plan requirement, not the rest of the flow — `evaluate_trigger` still reports the true `classification` (visible for audit in the decision record), it just sets `requires_plan=False`. Rationale: hotfixes need to ship fast; the CI diff-gate still re-classifies the real diff post-hoc and blocks if the `afk:2-plan` trigger holds with no plan lock, so skipping the interactive-lane plan stage is not skipping the safety net — it's deferring it to the fail-closed backstop.

### 4. Implement

**Protected files — policy depends on who is editing.** The canonical list (repo-level + user-level `~/.claude/*`) lives in [`docs/security/agent-boundaries.md`](../../../docs/security/agent-boundaries.md). Don't duplicate it here — check that file before editing.

- **Subagent dispatch (`/dispatch`)** — never edits protected files. If the task requires it, escalate to inline `/implement`.
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

**The loop, inlined (this is now the sole source — nothing external to load):**

1. **Red** — write one failing test for the current AC item. Run it, confirm it fails for the expected reason (not a typo/import error).
2. **Green** — write the minimal implementation that makes that test pass. Nothing more.
3. **Mutation probe** — corrupt the line(s) you just wrote (flip a comparison, off-by-one a bound, drop a branch) and confirm the test you just wrote reddens. Record the evidence line (`<file>:<line> corrupted → <test name> reddened`). If it doesn't redden, the test is not actually exercising the behavior — fix the test before moving on.
4. **Refactor** — deferred: not per AC item (see the anti-horizontal-slicing rule below), only as the single whole-suite pass after all AC items are green.

**Anti-horizontal-slicing rule**: do not write all the tests first and then all the implementation, and do not batch several AC items' RED states before going GREEN on any of them. One AC item goes red→green→(mutation probe) before the next item starts. Horizontal slicing defeats the point of the loop — it turns TDD into "write tests after" with extra steps.

**Operating discipline:**

- §4a (already-done audit) still runs first — TDD-mode is no excuse to skip it. Symbols from the issue AC drive the grep; if the behavior already exists with tests, stop and close as `not-planned`.
- Iterate one acceptance-criterion bullet at a time. Per AC item: write a failing test → confirm RED → write the minimal implementation → confirm GREEN → next AC item. The inner loop is strictly red→green — do **not** refactor between AC items. Do **not** write all tests first then all code either (the anti-horizontal-slicing rule above is binding).
- Every test must trace back to an AC bullet. If a test does not, the test is either out of scope or evidence the AC is incomplete — in the latter case stop and escalate (re-grill, do not invent AC inline).
- Once every AC item's test is green, run **one** refactor pass over the whole green suite (step 4 above) before moving to §5. Refactor permission is scoped to code freshly covered by a passing test in this session. Adjacent untested code is not in refactor scope — either write a characterization test first (then it is in scope) or flag a follow-up issue and leave it.
- §4c (E2E smoke) still applies before marking the outcome `success` when the change touches I/O / schema / hooks / subprocess areas.
- ADR-0001 compliance: do not invoke `/grill` or any other skill mid-task. The loop above is inline discipline, not a skill invocation.
- After each AC item's GREEN, run the mutation probe (step 3 above) before starting the next item — survival blocks progress, and it is a manual per-test discipline, never an automated score/gate.

**Mutation-probe gate — final pass before §5, both parts required:**

1. Run the full test suite for the touched module(s), not just the AC-tied tests. Green suite is a precondition for opening the PR.
2. Enumerate the AC list from the issue body and, for each item, name the mutation-probe evidence line (`<file>:<line> corrupted → <test name> reddened`) recorded for it during §3. **An AC item with no line here means the probe was skipped, not that it's exempt — go back and run it now, before touching §5.** This enumeration is what gets pasted into the PR's `## Testing` section; do not write the PR body from memory of "yeah I think I did those" — build it from this list.

Do not proceed to §5 until every AC item's line is accounted for.

### 5. Commit & PR

```bash
git add <specific files>
git commit -m "<type>(<scope>): <description> (#N)"
git push -u origin feat/<N>-<slug>
```

**PR body must be rich and informative** — this is the primary context for reviewers (human and the Claude code-review bot). Use this template:

**Retraction marker**: if this PR reverts previously-shipped, previously-*released* behavior (not just an unreleased change on the same branch), add a `Reverts #<M>` line — naming the PR or issue whose behavior is undone — alongside `Closes #<N>`. This is the machine-readable marker `/weekly-release` reads (`_reverted_pr_number()`) to populate the "Отозвано" section; a revert without this marker is invisible to that disclosure (jarvis `CONTEXT.md` → *Weekly release* → *Retraction marker*).

```markdown
## Summary
<what changed, 2-3 sentences — the "what">

## Why
<problem being solved, link to issue — the "why">
Closes #<N>
Reverts #<M>  <!-- only if this PR undoes previously-released behavior -->

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
- <TDD-mode only: mutation-probe evidence, one line per probed test — `<file>:<line> corrupted → <test name> reddened`>

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

### 6. Log the outcome

After PR creation (or failure at any step), append one line to `~/.claude/projects/<project>/memory/decisions.md`:

```
- YYYY-MM-DD — outcome <success|partial|failure> — #N — <PR> — <one-clause lesson>
```

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

Waiting for manual review on every LOW-risk PR is the anti-pattern.

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
- **Scope fit**: does the file list match the issue scope? Unrelated files → revert them before pushing
- Files that shouldn't have been modified (especially protected files)
- Debug code, `console.log`, `print` statements left behind
- Unrelated changes that crept in
- Secrets or credentials in any form
- **Symmetric patterns**: when fixing a class of bug, grep for sibling instances across the file AND other files — not just the one the reviewer flagged

#### Self-review checklist (cheap catch — PR plugin is authoritative)

Quick scan through two lenses before opening the PR:

**Standards** — Fowler's 12 code smells:
- [ ] **Mysterious Name** — naming clear and self-documenting?
- [ ] **Duplicated Code** — repeated logic to unify?
- [ ] **Feature Envy** — method belongs more to another class?
- [ ] **Data Clumps** — items that travel together → own object?
- [ ] **Primitive Obsession** — small type where primitives are used?
- [ ] **Repeated Switches** — same conditional in multiple places?
- [ ] **Shotgun Surgery** — one change touches many files?
- [ ] **Divergent Change** — file changes for multiple reasons?
- [ ] **Speculative Generality** — unused abstraction / dead code?
- [ ] **Message Chains** — long `.a().b().c()` traversal chains?
- [ ] **Middle Man** — delegation without added value?
- [ ] **Refused Bequest** — subclass ignoring inherited members?

**Spec** — faithfulness to the originating issue:
- [ ] All acceptance criteria addressed?
- [ ] No scope creep beyond the issue?

This is a **cheap catch**, not a merge gate. The Claude code-review plugin is the authoritative reviewer; missing items will be caught there. The goal is to catch obvious errors before the PR opens, not to replace the review.

If the diff looks wrong, fix it before pushing.

## Recovery playbook

See `docs/security/recovery-playbook.md` for how to handle:
- Broke a file → revert from main
- Corrupted decisions.md/handoff.md → restore from the file's own edit history, or accept the loss and note it in the PR
- Created bad PR → close + delete branch
- Committed to wrong branch → cherry-pick + reset
