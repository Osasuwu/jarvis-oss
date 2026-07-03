---
name: rework
description: Reactive TDD loop against PR review findings — parse structured verdict, fix CRITICAL/MAJOR issues via TDD, invoke loop-stop guard policy, emit terminal artifacts.
version: 1.1.0
---

# Rework Skill

Reactive TDD loop against a structured code-review verdict on a PR. Takes a PR
number, fetches the structured review, classifies findings (CRITICAL/MAJOR/MINOR),
applies fixes with TDD discipline per CRITICAL finding, then invokes the loop-stop
guard policy to decide whether the loop has converged or must terminate.

Realises decision `9884299d-999c-4863-8b56-235fd09ec6e2` (Q6 — separate `/rework` skill).
Loop-stop guard policy (Q4) is decision `8e757f01-839b-4be2-a98b-a479452b5ec1`, implemented
at `scripts/rework_policy.py`.

## Usage

```
/rework <PR_NUMBER>
```

Single positional argument — the GitHub PR number to rework.

## Contract

This skill deliberately **skips** the SOUL.md grill-me checkbox. Findings are
explicit reviewer signals; assumptions were already grilled (or not) when the
initial `/implement` ran. Re-litigating findings as implicit assumptions adds
no value and duplicates the review cycle.

It **reuses** `_shared/tdd/` reference docs (`tdd-loop.md`, `tests.md`,
`mocking.md`, `refactoring.md`) for the red→green→refactor discipline inside
CRITICAL-finding fixes. Load them as operating procedure per finding — do not
reinvent the TDD loop inline.

**Territory boundaries** (what the skill does NOT touch):
- PR title — never modified
- `Closes #NNN` line in the PR body — never modified
- Labels other than `status:rework-in-progress` (entry) and `status:needs-human` (terminal stuck)

## Pipeline

### 1. Pre-flight

```bash
# Validate PR exists, is open, belongs to a tracked repo
gh pr view <N> --json number,title,state,headRefName,baseRefName,body,labels

# Check per-PR lock via outcome_records
# If an outcome row with pattern_tags=['pr-<N>', 'rework', 'in_flight'] exists
# and is <2h old → abort, another rework session is active
```

- Abort if PR state ≠ `OPEN`.
- Abort if per-PR lock with `in_flight` found and TTL not expired.
- On abort: write a GitHub comment explaining the skip, then exit.

### 2. Acquire lock + set entry label

```bash
gh pr edit <N> --add-label "status:rework-in-progress"
```

Record the per-PR lock:

```
outcome_record(
  task_type: "fix",
  task_description: "Rework PR #<N> — <PR title>",
  outcome_status: "pending",
  project: "<repo>",
  issue_url: "<PR URL>",
  pattern_tags: ["pr-<N>", "rework", "in_flight"],
  lessons: ""
)
```

The lock TTL is 2 hours. The orchestrator checks `in_flight` before dispatching
a new `/rework` for the same PR and skips if the lock is live.

### 3. Fetch PR state + review verdict

```bash
gh pr view <N> --json title,body,headRefName,baseRefName,additions,deletions,files
gh pr diff <N> -- <initial_diff_files>
```

- Save the initial PR diff file set and LOC count — these feed the scope-creep guard.
- Count existing `## Rework history` sections in the PR body to determine the attempt
  number. Attempt 1 has zero existing sections.
  - **Ordering contract:** the orchestrator MUST append attempt *k*'s `## Rework history`
    section (issue #638) *before* it emits the next `review_negative` dispatch — otherwise
    the count under-reads and the STUCK_ATTEMPTS / STUCK_NO_CONVERGENCE guards silently
    fail to fire, letting the loop run past its maximum. As a cross-check, also count this
    branch's `fix(rework): attempt`/`converged` commits on `headRefName` and take the max
    of the two.
  - **Race fallback:** if `## Rework history` count is 0 but the per-PR lock (from step 2)
    already exists, query `outcome_records` for rows with `pattern_tags LIKE '%rework%'`
    and `issue_url = "<PR URL>"` to infer the attempt number. This handles the case where
    attempt 2 is dispatched before attempt 1's history section was appended.

Fetch structured Claude code-review verdict (the most recent review comment
with a structured finding list). Parse findings per reviewer. Expected format:

```
## Findings

### CRITICAL
- **Description**: ...
  **File**: `path/to/file.py`
  **Line**: 42

### MAJOR
- **Description**: ...
  **File**: `path/to/file.py`
  **Line**: 85

### MINOR
- **Description**: ...
```

If no structured verdict exists on the PR, there is nothing to rework. **Do NOT
exit ad-hoc here.** Before exiting, execute the cleanup block from **§9d (Edge:
no structured findings)** to remove the `status:rework-in-progress` label, release
the per-PR lock, and record the `no-findings` outcome. Exiting without 9d cleanup
leaks the lock + label acquired in step 2, blocking re-dispatch for the full 2h TTL.

#### 3a. Reality-check prelude (before classification)

A structured verdict can be internally consistent yet audit a *different tree
snapshot* than the PR branch — most commonly because a salvage/rebase landed on
`main` after the PR branch was cut, and the reviewer was pointed at `main`
instead of `headRefName`. Applying such a verdict overwrites correct branch
content with hallucinated findings (M41 smoke test on PR #784, 2026-05-26 —
outcome `2c2a357a-957c-4998-aac8-91b404f050b6`, lesson: dispatcher-era branch
audited as executor-era).

After parsing findings into a list (with file/line metadata) but **before**
classification and counting, verify each CRITICAL finding against the PR
branch tree:

```bash
git fetch origin <headRefName>
git checkout <headRefName>

# Per claimed file in CRITICAL findings:
for f in <files claimed by CRITICAL findings>; do
  git ls-tree -r HEAD --name-only | grep -Fx "$f" || echo "MISSING: $f"
  if [ -f "$f" ]; then wc -l "$f"; fi
done
```

For each CRITICAL finding:

- **File existence**: `git ls-tree -r HEAD --name-only` on `headRefName` MUST
  contain the claimed path. Missing → mismatch.
- **Cardinality**: if the finding cites LOC ("function at line 320 in a 500-line
  file") or function count, check `wc -l` and `grep -c '^def\|^class\|^function'`
  (or language-equivalent) on the actual file. Off by >2× → mismatch.
- **Symbol presence**: if the finding cites a named symbol, `grep -nE
  '\b<symbol>\b' <file>` on `headRefName`. Zero hits where the finding claims
  presence (or vice versa) → mismatch.

If **≥1 CRITICAL finding** contradicts reality on `headRefName` → trigger
**§9e (VERDICT_MISALIGNED)** and exit. Do not proceed to classification, do
not attempt fixes, do not push.

If all CRITICAL findings pass the reality check (or there are zero CRITICAL
findings), continue to classification.

#### 3b. Design-decision oscillation guard

Root cause of PR #1256's 7 rework rounds: the AI reviewer re-litigated the same architectural design question across rounds 3–7 instead of accepting a recorded decision. `record_decision` is the circuit-breaker.

Before classifying any **design-level finding** (API shape, algorithmic approach, architectural pattern, naming convention — anything that is a style or design choice rather than a correctness bug):

1. Check the decision log:
   ```
   memory_recall(query="<PR title> <topic keyword> design decision", type="decision", brief=true, limit=5)
   ```

2. **If a `record_decision` entry exists for this exact design question** → mark the finding `DESIGN_LOCKED`:
   - Do NOT apply the fix
   - Post a PR comment using this format (the HTML comment is machine-readable by the code-review plugin's step 3.5):
     ```
     <!-- DESIGN_LOCKED: <UUID> | <one-line summary> -->
     Skipping design finding "<one-line summary>" — decision <UUID> already resolved this:
     "<one-line rationale from the decision record>".
     Re-opening this design question requires the owner to update the decision record first.
     ```
   - Remove the finding from the classification pipeline entirely

3. **If this is the FIRST time this design question surfaces** AND it is a genuine architectural choice (not a bug) → emit `record_decision` NOW with the chosen approach (the one the PR implements), then treat the finding as `DESIGN_LOCKED` for this and future rounds:
   ```
   record_decision(
     decision: "<one-line: chosen approach>",
     rationale: "<why this was the right call given the PR context>",
     alternatives_considered: [{"alternative": "<reviewer suggestion>", "rejected_because": "<why not"}],
     reversibility: "reversible",
     confidence: 0.7,
     actor: "session:rework:pr-<N>",
     project: "<repo>"
   )
   ```

This guard must run before step 3's classification loop — a DESIGN_LOCKED finding is invisible to the CRITICAL/MAJOR/MINOR count and does not trigger a rework commit.

Classify each finding into CRITICAL / MAJOR / MINOR. Count totals:
`n_critical`, `n_major`, `n_minor`.

Extract `reviewer_kind` from the review-comment author (e.g. `claude-code-review`
for the Claude code review bot, or the login of the human reviewer). This populates
the `reviewer_kind` field in the stuck-event payload (§9c); without it that field
carries a junk literal. If the author is a bot account like `github-app[bot]`,
use its app name; if a human user, use their login.

### 4. Reactive TDD per CRITICAL finding

For **each** CRITICAL finding, in order of appearance:

1. **RED** — Write a failing test that captures the finding. The test should fail
   with the current code to demonstrate the bug/issue.

   Load `_shared/tdd/tdd-loop.md` as the procedure. Follow vertical-slice
   discipline: one finding → one test → one fix at a time.

2. **GREEN** — Implement the fix. Run the test to confirm green.

2b. **Symmetric scan** — before REFACTOR, grep for the same anti-pattern across every file in the PR diff (not just the specific line flagged). Root cause of multiple rework rounds: the reviewer flags instance A, the fixer fixes A, round 2 flags instance B in the same file, etc.

   ```bash
   # Identify all files this PR touches
   git diff <base>...HEAD --name-only > /tmp/pr_files.txt

   # For each CRITICAL finding's pattern, grep every PR file
   cat /tmp/pr_files.txt | while read f; do
     grep -n "<anti_pattern_from_finding>" "$f" 2>/dev/null && echo "  ^ in $f"
   done
   ```

   Fix ALL instances found across the diff in the same commit. Document extra instances in the commit message: `"also fixed 3 sibling instances in X, Y, Z"`. A CRITICAL finding is a class of bug, not a single line.

3. **REFACTOR** — Clean up only what is under green coverage. Do not refactor
   adjacent untested code (per the refactor-permission clause in `tdd-loop.md`).

Record each fix's file:line in the attempt's `conflicts` map for the conflict
detection guard.

### 5. Apply MAJOR fixes

For each MAJOR finding:
- Apply the fix directly. No test mandate, but adding or updating tests around
  the touched code is encouraged.
- If a MAJOR finding touches code already covered by CRITICAL TDD tests, verify
  those tests still pass.
- Record each fix's file:line in the attempt's `conflicts` map.

### 6. Flag out-of-scope findings

Any CRITICAL or MAJOR finding that the skill judges outside the scope of the
original PR diff (step 3 only produces CRITICAL / MAJOR / MINOR):

```bash
gh pr comment <N> --body "Out-of-scope finding flagged: <description> — not addressed in this rework loop."
```

Do not silently drop non-obvious CRITICAL/MAJOR findings. MINOR findings never
gate the loop (two-gate model, #989): they are neither a rework trigger nor a
convergence blocker. But while already in context for a CRITICAL/MAJOR-triggered
round, sweep MINOR findings **best-effort** — apply the cheap, in-scope ones in
the same commit; skip any that would grow the diff or touch files outside the PR
scope (those would trip the scope-creep guard). Never start or extend a rework
round for MINOR findings alone — a minor-only review is a converged PR.

### 7. Diff statistics

```bash
git diff <base>...HEAD --stat
```

Compute:
- `files_touched`: set of file paths changed in this attempt (union of CRITICAL
  and MAJOR fix files).
- `loc_delta`: total lines changed (additions + deletions from diff stat).

### 8. Invoke loop-stop guard policy

Load the history of all attempts (from `## Rework history` sections in the PR
body, plus the current attempt):

```python
from scripts.rework_policy import decide, PolicyResult

result: PolicyResult = decide(
    attempts=<current_attempt_number>,
    history=[
        {
            "attempt": 1,
            "n_critical": <int>,
            "n_major": <int>,
            "files_touched": {"set", "of", "paths"},
            "loc_delta": <int>,
            "conflicts": {"file.py": {42, 85}},
        },
        # ... per attempt
    ],
    initial_files={"set", "of", "files", "from", "initial", "PR", "diff"},
)
```

The policy returns one of:
- `CONTINUE` — loop may proceed to next attempt
- `CONVERGED` — targets met (n_critical==0, n_major==0; two-gate, #989)
- `STUCK_ATTEMPTS` — ≥3 attempts without convergence
- `STUCK_SCOPE` — LOC delta >50% or files outside initial diff
- `STUCK_NO_CONVERGENCE` — critical+major not strictly decreasing
- `STUCK_CONFLICT` — same file:line touched in multiple attempts

### 9. Act on verdict

#### 9a. CONTINUE

```bash
git add <specific files>
git commit -m "fix(rework): attempt <N> — <brief summary>"
git push origin <headRefName>
```

Remove `status:rework-in-progress` label. **Release the per-PR lock** by calling:

```
outcome_update(
  id: <lock_record_uuid>,
  outcome_status: "success"
)
```

This removes the `in_flight` tag from the per-PR lock. The orchestrator checks
`in_flight` before re-dispatching — leaving the lock live blocks the next attempt
for the full 2h TTL and stalls the loop. CONTINUE is the expected multi-attempt
path, so this release is load-bearing. Leave the PR open for the next rework
dispatch (orchestrator re-dispatches on the next `review_negative` event).

Exit cleanly.

#### 9b. CONVERGED

Converged means the findings targets are met (`n_critical==0, n_major==0`;
two-gate model, #989 — no merge-blocking finding remains).

**Commit and push the fixes first** — steps 4–5 changed the working tree; without
this block those fixes are abandoned on exit and the next review re-flags the same
unfixed code:

```bash
git add <specific files>
git commit -m "fix(rework): converged — <brief summary>"
git push origin <headRefName>
```

The caller (sandcastle / orchestrator) handles the `## Rework history` PR body
append (see issue #638 for the format).

The skill records the outcome:

```
outcome_record(
  task_type: "fix",
  task_description: "Rework PR #<N> — converged",
  outcome_status: "success",
  outcome_summary: "Loop converged: n_critical=0, n_major=<count> after <ATTEMPT_COUNT> attempts",
  project: "<repo>",
  issue_url: "<PR URL>",
  pr_url: "<PR URL>",
  tests_passed: (n_major_remaining == 0),
  pattern_tags: ["pr-<N>", "rework", "terminal"],
)
```

`tests_passed` reflects whether all applied fixes (CRITICAL + MAJOR) have green
coverage. CONVERGED now fires only at n_major==0 (two-gate, #989), so every
MAJOR was fixed this loop; MAJOR fixes still carry no hard test mandate (§5).
Set it `true` only when every CRITICAL fix has a green test AND all applied
MAJOR fixes are tested or were zero; set it `false` whenever any fix lacks green
coverage, so downstream `/verify` and calibration do not over-trust the row as
fully test-validated.

Remove `status:rework-in-progress` label.
Do NOT merge. The PR stays open for human merge.
Release the per-PR lock by updating the `in_flight` outcome record.

#### 9c. STUCK_* (any stuck verdict)

Emit `rework_stuck` event via Supabase `events_canonical` table. Insert with:

```sql
INSERT INTO events_canonical (trace_id, actor, action, payload)
VALUES (
  gen_random_uuid(),
  'sandcastle:agent',
  'rework_stuck',
  jsonb_build_object(
    'pr', <N>,
    'verdict', '<loop_decision>',
    'reason', '<policy_reason>',
    'attempts', <ATTEMPT_COUNT>,
    'reviewer_kind', '<reviewer_kind from step 3>'
  )
);
```

Alternatively, if the sandcastle anon key does not have INSERT on
`events_canonical`, use `memory_store` as a fallback:

```
memory_store(
  type: "project",
  name: "rework_stuck_pr_<N>",
  description: "Rework stuck signal for PR #<N>",
  content: "Verdict: <decision>. Reason: <reason>. Attempts: <N>.",
  project: "jarvis",
  tags: ["rework", "stuck", "pr-<N>"],
  source_provenance: "skill:rework"
)
```

Apply terminal labels and comment:

```bash
gh pr edit <N> --add-label "status:needs-human"
gh pr comment <N> --body "## Rework loop terminated\n\n**Verdict**: <decision>\n**Reason**: <policy_reason>\n\n### Attempts\n\n| Attempt | CRITICAL | MAJOR | Verdict |\n|---|---|---|---|\n| 1 | <crit> | <major> | <verdict> |\n| 2 | <crit> | <major> | <verdict> |\n| 3 | <crit> | <major> | <verdict> |\n\nThis PR needs human review."
```

Record the outcome:

```
outcome_record(
  task_type: "fix",
  task_description: "Rework PR #<N> — stuck (<decision>)",
  outcome_status: "partial",
  outcome_summary: "Loop stuck: <decision> — <policy_reason>",
  project: "<repo>",
  issue_url: "<PR URL>",
  tests_passed: false,
  pattern_tags: ["pr-<N>", "rework", "terminal"],
)
```

Remove `status:rework-in-progress` label.
Release the per-PR lock by updating the `in_flight` outcome record.

#### 9d. Edge: no structured findings

If step 3 finds no structured review verdict at all:

```
outcome_record(
  task_type: "fix",
  task_description: "Rework PR #<N> — no findings",
  outcome_status: "failure",
  outcome_summary: "No structured review verdict found on PR #<N>; nothing to rework",
  project: "<repo>",
  issue_url: "<PR URL>",
  tests_passed: false,
  pattern_tags: ["pr-<N>", "rework", "terminal", "no-findings"],
)
```

Remove `status:rework-in-progress` label.
Release lock.

#### 9e. VERDICT_MISALIGNED (reality check failed)

Triggered by **§3a**: ≥1 CRITICAL finding contradicts the PR's `headRefName`
tree at the file-existence, cardinality, or symbol-presence level. The verdict
may be internally consistent — it just doesn't describe this branch.

The skill has been midway through reality-checking; the working tree may carry
RED-test scratch from a partial step-4 attempt. **Hard-reset before any other
action** so the diagnostic comment reflects the actual branch state, not a
contaminated tree:

```bash
git reset --hard HEAD
git clean -fd  # remove untracked scratch from RED test attempts
```

**Do NOT push commits.** This terminal path never produces a commit on the PR
branch — the whole point of §9e is that the verdict was unsafe to apply.

Post a diagnostic comment on the PR with a branch-vs-main divergence table.
Template:

```bash
gh pr comment <N> --body "$(cat <<'EOF'
## Rework loop terminated — verdict misaligned

The most recent code-review verdict on this PR contradicts the actual tree on
`<headRefName>`. The verdict appears internally consistent but audits a
different snapshot — most commonly because a salvage/rebase landed on `main`
after this branch was cut.

### Reality check (on `<headRefName>`)

| CRITICAL finding | Claimed file | On branch? | Cardinality match? | On `main`? |
|---|---|---|---|---|
| <one-line summary> | `<path>` | <yes/no> | <yes/no/n-a> | <yes/no> |
| ... | ... | ... | ... | ... |

### What this means

Applying the verdict verbatim would overwrite correct branch content with
fixes for code that does not exist on this branch. The `/rework` loop has
refused to proceed; no commits have been made.

### Recovery

Pick one:

1. **Rebase + re-review** — rebase `<headRefName>` onto current `main`, push,
   re-trigger Claude code-review. The new verdict will audit the rebased tree.
2. **Close PR** — if the work was superseded (e.g. salvaged into a different
   PR that already merged), close this PR; track any remaining behavior in a
   fresh issue.

This PR has been labelled `status:needs-human`. The rework loop will not
re-fire until the label is removed and a fresh review is posted.
EOF
)"
```

Apply the terminal label:

```bash
gh pr edit <N> --add-label "status:needs-human"
```

Record the outcome with `pattern_tags` including `verdict_misaligned`:

```
outcome_record(
  task_type: "fix",
  task_description: "Rework PR #<N> — verdict misaligned",
  outcome_status: "partial",
  outcome_summary: "Step-3 reality check found ≥1 CRITICAL finding contradicting headRefName tree. No commits pushed. Owner choice: rebase + re-review, or close PR.",
  project: "<repo>",
  issue_url: "<PR URL>",
  pr_url: "<PR URL>",
  tests_passed: false,
  pattern_tags: ["pr-<N>", "rework", "terminal", "verdict_misaligned"]
)
```

Remove `status:rework-in-progress` label.
Release the per-PR lock by updating the `in_flight` outcome record.

Why a separate terminal verdict (vs §9c STUCK_*): §9c requires loop iterations
to have run; §9d requires step-3 to have found zero findings. The misaligned
case is neither — findings exist and are internally consistent, but contradict
observable branch reality. Routing it through §9c would falsify the `history`
that the loop-stop guard policy decides on; routing it through §9d would lose
the diagnostic signal the owner needs to choose between rebase and close.

## Safety rules

- Never modify PR title, `Closes #NNN` line, or labels other than
  `status:rework-in-progress` (entry) and `status:needs-human` (terminal stuck).
- Never merge the PR — leave it open for human or orchestrator merge.
- Never invoke the SOUL.md grill-me checkbox or any `/grill` skill.
- Never push to `main` / `master` — only to the PR's `headRefName` branch.
- If CI fails after a rework push, comment on the PR with the failure details
  but do not revert. The orchestrator or owner handles CI failures.

## Diff review (before commit)

Before each commit:

```bash
git diff <base>...HEAD --stat
git diff <base>...HEAD
```

Check for:
- Scope fit: do changed files match the findings being addressed?
- Debug code, console.log, print statements left behind
- Unrelated changes that crept in
- Secrets or credentials in any form
- Symmetric patterns: when fixing a class of finding, grep for sibling instances
  across the file AND related files

## Recovery playbook

- **Push rejected** (force-push protection, non-fast-forward) → `git pull --rebase`
  and retry push.
- **Lock collision** (another session holds `in_flight` lock with TTL not expired)
  → post a comment and exit. The stale lock is the orchestrator's problem.
- **Test failure on a fix that worked locally** → check environment mismatch
  (Python version, database state, fixture data). If the test failure is real,
  fix it; if environmental, skip the test and note in the commit.
- **Mid-session context loss** → push whatever commits exist, remove label, release
  lock, mark outcome as `partial` with summary "context lost mid-rework".
