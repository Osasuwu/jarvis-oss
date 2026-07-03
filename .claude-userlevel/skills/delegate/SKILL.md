---
name: delegate
description: This skill should be used when the principal asks Jarvis to dispatch one or more GitHub issues to coding subagents (typically multiple issues in parallel), or says "делегируй #X #Y", "раскидай на агентов", "параллельно реализуй #X #Y #Z". Also used by autonomous-loop to hand off a single subagent-scoped job (e.g. CI debug). For a single issue the main session will do itself, use /implement instead. Jarvis's own judgment on task complexity OVERRIDES blind delegation — if a task is unfit for a subagent (needs session context, cross-cutting reasoning, safety review), keep it inline even if principal said "раскидай".
version: 2.0.0
---

# Delegate Skill

Dispatch **multiple** GitHub issues to coding subagents running in parallel.

The main session stays as orchestrator: it reviews each subagent's diff, resolves scope drift, and decides merges. **Subagents NEVER merge.** They push the PR and stop.

Memory recall and the `record_decision` contract come from user-level CLAUDE.md `### Memory & decision protocol`. The skill-specific gates below are what `/delegate` adds on top.

## When to /delegate vs /implement

**Prefer /implement (inline, current session):**
- Single issue
- Task touches safety-critical zones (`driver/`, `planning/`, `mujoco/`)
- Task needs cross-cutting awareness (spans multiple projects, shared infra)
- Issue description alone isn't enough — requires the current session's reasoning trail or just-loaded memory context

**Prefer /delegate (subagent dispatch):**
- Multiple independent issues (any order works)
- Each issue description is self-contained — a fresh coding session could act on it without Jarvis's context
- Tasks touch disjoint files / areas

**Mixed batch — split the work:**
- Keep context-heavy or safety-critical issues for yourself (inline via /implement flow)
- Delegate the rest to subagents
- Report the split reasoning briefly to the principal

**Jarvis judgment overrides the principal's "параллельно":** The principal has explicitly delegated this call to Jarvis (memory: this decision). If a task looks deceptively complex or a subagent will struggle (needs memory context, cross-file reasoning, recent-decisions awareness), keep it inline even if asked to delegate. Don't silently downgrade — tell the principal "keeping #X inline because <reason>".

## Contract: pre-dispatch gate (binary AFK-readiness check, runs FIRST)

Before any other routing, every issue in the batch passes through the
pre-dispatch gate. The gate is a binary AFK-readiness check whose failure
modes are mechanical (label present / absent, heading present / absent,
regex match) — not judgement calls. Issue #642 introduced it because
headless `/delegate` has no operator to grill the AC in real time, so the
work of verifying AFK-readiness must happen *at* dispatch entry, not
inside the dispatched sandcastle agent.

**Four conditions, all required** (canonical implementation:
[`scripts/delegate_predispatch_gate.py`](../../../scripts/delegate_predispatch_gate.py)):

1. Issue has label `sandcastle` (applied by `/to-issues` per the AFK-fit
   checklist at slice creation — never manually, never at grill time).
2. Issue has **no** `needs-*` label (`needs-grill`, `needs-research`,
   `needs-prd`, `needs-refactor`, …). Each requesting skill removes its own
   label at terminal success.
3. Issue body contains an `## Acceptance criteria` heading (case-insensitive
   prefix match — `## Acceptance criteria`, `## ACCEPTANCE CRITERIA (brief)`,
   etc. all match).
4. Issue body cites at least one decision UUID (regex
   `\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b`).

**Invocation** (per issue, before classification or claim):

```bash
gh issue view <N> --repo <owner/repo> --json number,body,labels \
  | python scripts/delegate_predispatch_gate.py
# exit 0 ⇒ allow; exit 1 ⇒ refuse, message on stdout names each missing element
```

**On refusal** (any one or more of the four conditions fail):

1. `mcp__memory__outcome_record(task_type="delegation", outcome_status="failure", outcome_summary="pre-dispatch gate refused: <message>", project="<repo>", issue_url=...)` — recorded so `/reflect` and `/self-improve` can spot patterns.
2. `gh issue edit <N> --add-label "status:owner-queue"` — surfaces in next `/status` run so the owner sees the backlog of issues needing a manual touch.
3. Report to the principal in the batch summary: `#N refused — <verbatim gate message>`. The message names each missing element so the owner knows exactly what to fix.
4. The issue is **not** claimed (no `status:in-progress`, no branch). `/grill` / `/research` / sandcastle-label-add are the unblock paths — once fixed, re-dispatch flips the route.

**No Telegram escalation** even on repeat refuses — last-resort rule (decision `e9b9cfb8`). Owner discovers via `/status`.

**Interactive `/implement` is NOT pre-dispatch-gated.** The gate guards
*subagent* dispatch where no operator is present. Inline `/implement` keeps
the SOUL.md grill-checkbox as its in-skill backstop and can run on any
issue (including `status:owner-queue`-tagged ones) — the operator IS the
gate. This is intentional asymmetry, not an oversight.

**Known fragility** (decision `6b0a5bf7`): the gate runs once at
`/delegate` entry and is **not re-validated** inside the sandcastle
container. Pipeline changes that bypass `/delegate` entry (e.g. a future
shortcut that hands work directly to a subagent) would silently bypass
the gate. Re-surface this risk in any architectural change that restructures
the dispatch chain.

## Contract: dispatch-dedup (in-flight skip, runs before claim/spawn)

Issue #931. An issue that already has an **open PR** or an **in-flight branch**
is being worked — dispatching a second subagent onto it duplicates effort and
races two branches to the same `Closes #N`. The check is network-fed but the
predicate is the same pure `check_in_flight` the autonomous drain uses
([`scripts/delegate_predispatch_gate.py`](../../../scripts/delegate_predispatch_gate.py)),
so interactive `/delegate` and the reactive-core drain skip identically.

**Per issue that survived the pre-dispatch gate, before §2 claim:**

1. **Fetch in-flight evidence — explicit pagination** (a truncated first page
   silently misses later PRs/branches and re-dispatches a duplicate):

   ```bash
   gh pr list --repo <owner/repo> --state open --limit 200 \
     --json number,body,headRefName
   gh api --paginate "repos/<owner/repo>/branches" --jq '.[].name'
   ```

2. **Check task_queue live rows** — a sibling autonomous drain may already be
   running this issue with no PR yet. Query the `claimed`/`running` rows
   (`agents.task_queue.list_active`) and match the issue number out of each
   `goal`. A live sibling row for `#N` counts as in-flight.

3. **Run the predicate** — feed the issue number + the fetched `open_prs`
   (`number`/`body`/`headRefName`) + `open_branches` (names) into
   `check_in_flight`. Verdicts:
   - `live_pr` (closing keyword `#N` in a PR body, or a PR head branch
     `^[a-z]+/<N>-`) **or a live sibling row** → **SKIP**.
   - `stale_branch` (a `feat/<N>-` branch with no PR) → owner-attention route,
     not an auto-dispatch.
   - `clear` → proceed to claim.

**On SKIP:**

1. Report a **pointer** in the batch summary: `#N skipped — already in flight:
   <PR #M / branch / live task row>`. No further routing.
2. **No label mutation** — do NOT add `status:in-progress` or
   `status:owner-queue`; the issue is already owned by the in-flight work.
3. `mcp__memory__outcome_record(task_type="delegation", outcome_status="unknown",
   outcome_summary="dispatch-dedup skip: <pointer>", project="<repo>",
   issue_url=..., pattern_tags=["delegation","dispatch-dedup","skip"])` —
   low-severity, for `/reflect` pattern-spotting. Not a `failure`: the issue
   isn't broken, it's already being handled.

**Atomic claim before spawn** — closes the residual race where two `/delegate`
runs both read `clear` in the same window. Push an **empty** branch ref as the
claim *before* spawning the subagent:

```bash
git push origin "$(git rev-parse origin/main)":refs/heads/feat/<N>-<slug>
# non-zero exit (ref already exists) ⇒ another run claimed it ⇒ treat as SKIP
```

A rejected push means a concurrent claimer won — SKIP as above. This makes the
branch namespace the lock; the pre-claim window (between the read and the push)
is the only residual race, and it is documented in the gate module docstring.

## Contract: dispatch routing (per issue: mechanical / TDD-mode / `grill_required`)

Per ADR-0001, skills do not self-trigger mid-task ("Type 3" is rejected). `/delegate` does **not** run `/grill` inline (and there is no standalone `/tdd` skill — TDD-mode is dispatched as inline operating discipline carrying `_shared/tdd/` reference docs). For **each** issue that **passed** the pre-dispatch gate above, inspect two inputs and route to one of three branches. Routing is per-issue: a single batch can mix mechanical-mode and TDD-mode dispatches (and exclude grill-failing issues).

**Pre-dispatch gate dominance**: when the gate's four artefacts are present
(sandcastle label + no needs-* + `## Acceptance criteria` heading + decision
UUID), the SOUL.md grill-checkbox below is **skipped** — the artefacts'
presence is itself evidence the issue has been grilled and refined. The
checkbox runs only as a **legacy backstop** for pre-#642 issues that have
no artefacts and no `needs-grill` label (e.g. early milestones whose slices
were authored before the AFK-fit checklist). New issues from `/to-issues`
land with artefacts in place and bypass the checkbox entirely.

**Inputs** (per issue — fetch the body first):

```bash
for N in <N1> <N2> ...; do
  gh issue view $N --repo <owner/repo> --json title,body --jq '.title + "\n\n" + .body'
done
```

1. **SOUL.md `### Grill trigger checkbox`** — answer per issue:

   - Touches user-visible behavior? (not cosmetic / refactor / doc-fix)
   - Touches domain logic / algorithmics / physics?
   - Will tests be non-trivial?
   - Crosses existing non-trivial code?

2. **Grill artifact for this issue** — present iff *either* of the following holds:

   - **(a) working_state** — `memory_get(name="working_state_<project>", project="<project>")` where `<project>` is the short project slug (`jarvis`, `redrobot`), matching the convention in `scripts/session-context.py`. If the returned record references this issue number alongside one or more decision UUIDs, the artifact is present. The exact key shape inside the record is project-controlled — accept any structure where a decision UUID is reachable from the issue number. If working_state has no entry for this issue, fall through to (b).
   - **(b) issue body** — the issue body contains a heading starting with `## Decisions` (prefix match — `## Decisions`, `## Decisions & Alternatives`, etc.) AND that section cites at least one decision UUID. This is the opt-in path for manually-annotated or grill-refined issue bodies. The automated `/to-issues` template does not yet emit this section — a separate issue tracks adding it; until then `## Decisions` in the body is treated as a deliberate annotation by the author.

**Dispatch table** — per issue, pick exactly one branch:

| checkbox | grill artifact present for this issue? | route |
|---|---|---|
| 0 yes | n/a | **mechanical-mode** dispatch (current flow, no AC-completeness clause) |
| ≥1 yes | yes (UUIDs in working_state OR cited in issue body) | **TDD-mode** dispatch (subagent prompt gains AC-completeness clause; see §4) |
| ≥1 yes | no | **exit `grill_required`** (issue excluded from batch) |

### Branch: `grill_required` exit (per issue)

Emit the structured block below per affected issue and exclude them from dispatch. They are NOT claimed in §2 — claim happens after this exclusion:

```
EXIT: grill_required
issue: <owner/repo>#<N>
reason: trigger-checkbox-fired (<count>/4 yes); no grill artifact in working_state or issue body
next: run /grill against #<N>, then re-dispatch /delegate (or /implement) #<N>
```

Continue dispatching the rest of the batch in the same call — partial dispatch is fine. Report to the principal: "issues #X, #Y exited `grill_required`; #A dispatched mechanical, #B dispatched TDD-mode". The orchestrator handles re-dispatch in fresh sessions after `/grill`.

### Branch: mechanical-mode

Most "fix typo / bump dep / move file" issues land here. Subagent prompt template (§4) is the canonical form **without** the AC-completeness clause.

### Branch: TDD-mode

Subagent prompt template (§4) gains an additional AC-completeness clause directing the subagent to follow `_shared/tdd/tdd-loop.md`, cover every AC bullet with at least one test, and escalate (not silently drop) any AC that does not fit.

The TDD-mode clause is **/delegate-specific** — it is not present in `/implement`'s TDD-mode (which relies on Operating discipline in the skill body) because the failure mode it targets (subagent AC-dodging via "out of scope" relabeling, per memories `subagent_acceptance_criteria_dodged_as_out_of_scope` and `subagent_test_coverage_overclaim`) only manifests in subagent dispatch.

### Subagents never run `/grill` themselves (and there is no `/tdd` skill to invoke)

Their dispatch prompt carries the grill-refined AC verbatim and (in TDD-mode) the TDD operating clause inline. First subagent action is to confirm the AC is verifiable from the issue body alone — if not, post a comment and stop, escalating back to the main session.

### Re-entry is stateless

Every `/delegate` entry re-runs the checkbox and re-reads `working_state_<project>` per issue. There is no `tdd_mode` flag carried in from the orchestrator. When `/grill` finishes and the orchestrator re-dispatches `/delegate <N>`, the route flips from `grill_required` → TDD-mode automatically because the grill populated the artifact. Same code path, different input state.

### No "skip grill" override at the batch level

Unlike `/implement` (where the principal can say "skip grill, just implement" for a single issue), `/delegate` does not offer a one-shot override. Per-issue grilling is the gate that keeps subagents from drifting on under-specified AC, and silently overriding it for an entire batch is exactly the failure mode this contract prevents. If the principal wants a triggered issue dispatched anyway, route it through `/implement` with the explicit single-issue override.

## Pipeline

### 1. Classify each issue: delegatable or inline

For each issue routed to **mechanical-mode** or **TDD-mode** by the §Contract dispatch table (issues that exited `grill_required` are already excluded):

1. Run pre-flight (5 checks — same as /implement §1).
2. Classify:
   - **Delegatable** → fresh subagent can handle it from the issue body alone (subagent prompt = template + TDD-mode block if route == TDD-mode)
   - **Inline** → needs session context / safety review / cross-cutting peripheral vision (route through /implement, which re-runs its own dispatch)

Produce a short split plan for the principal before acting. Example:

> Batch: #604, #613, #617.
> - #604 (uncertainty map) → **delegate** — self-contained, single module.
> - #613 (swept path) → **inline** — safety-adjacent, `planning/`.
> - #617 (docs tweak) → **delegate** — trivial.

### 2. Claim all dispatchable issues

Claim every issue that survived **both** the pre-dispatch gate (§Contract: pre-dispatch gate) **and** the per-issue `grill_required` check (label `status:in-progress` + comment), including the ones staying inline. Issues refused by the pre-dispatch gate are NOT claimed — they receive `status:owner-queue` and exit immediately. Issues that exited `grill_required` are also NOT claimed — they go back to the orchestrator for `/grill` + re-dispatch in a fresh session, which will run its own pre-flight and claim then. Claiming up front prevents races between concurrent Jarvis instances and the principal forgetting to route delegatable issues.

```bash
for N in <N1> <N2> ...; do
  gh issue edit $N --add-label "status:in-progress"
  gh issue comment $N --body "Claimed by Jarvis. Branch: feat/$N-<slug>"
done
```

### 3. Record decision

Apply the `record_decision` contract from user-level CLAUDE.md. One call covers the batch — which went to subagents, which stayed inline, which exited `grill_required`, why. Issue dispatch satisfies trigger #1 (issue implementation) — non-optional.

### 4. Dispatch subagents

For each delegatable issue, spawn a coding subagent:

```
Agent(
  subagent_type="coding",
  isolation="worktree",  # REQUIRED — each agent in its own worktree
  description="Implement #<N>: <title>",
  prompt="<self-contained prompt — see template below>",
  run_in_background=true,  # parallel execution
)
```

**Subagent prompt template** (self-contained — subagent does NOT share main-session memory). The `<TDD-mode block>` placeholder below is filled differently depending on the per-issue dispatch routing (§Contract):

- **mechanical-mode** → omit the block entirely (no AC-completeness clause; trivial issues stay terse).
- **TDD-mode** → insert the AC-completeness clause verbatim, exactly as specified in [issue #595](https://github.com/your-username/jarvis/issues/595).

```
Implement GitHub issue #<N> in repo <owner/repo>.

Title: <title>
Body: <full issue body>
Acceptance criteria: <enumerated from issue>
Files likely to change: <list>

Branch name: feat/<N>-<slug>   (MUST use exactly this — branch-name race mitigation)
Target repo CWD: <absolute path to worktree>

<TDD-mode block — present iff per-issue route == TDD-mode, see §Contract>

Follow the /implement skill pipeline (loaded in your session). Specifically:
- §4 Implement — read existing code first, check if already done, lint, test
- §5 Commit & PR — use the rich PR body template, include "Closes #<N>"
- §6 Record outcome — always record, pattern_tags=["delegation", "subagent", "<area>"]

HARD RULES for you (subagent):
- You operate as `JARVIS_PRINCIPAL=subagent` (#426). Hooks classify your tool calls as constrained — protected-file edits and protected-file mirrors will block. Do not try to bypass.
- Do NOT merge the PR. Open it, push it, record outcome, stop.
- **"Open it" means YOU run `gh pr create` from this session.** Returning "PR creation will be handled by the orchestrator" or "I have pushed the branch and the PR is ready to be opened" violates the dispatch contract. The required call sequence is literal: `git push -u origin feat/<N>-<slug>` then `gh pr create --title "..." --body "..."` then capture the returned URL into the outcome record. No orchestrator handoff at this step. (Recurring lesson: `delegate_subagent_pr_step_skipped_and_absolute_path_2026_05_17` — 3+ recurrences as of 2026-05-19 tick).
- Do NOT modify protected files (.mcp.json, CLAUDE.md, etc — see docs/security/agent-boundaries.md)
- Do NOT send messages as the principal
- Do NOT change values, defaults, or constants that are not explicitly named as targets in the issue body. Centralization / refactoring tasks are structural only — IK seeds, default timeouts, magic numbers, tuple constants must be preserved exactly unless the issue says to change them. If the issue is unclear, preserve the value and flag in the PR body.
- If you hit a blocker you can't resolve, record a "partial" outcome with a clear note about what's missing

Report back: PR URL + 2-line summary of what you did.
```

**TDD-mode block (insert verbatim when the per-issue route is TDD-mode):**

```
TDD-mode active for this issue.

Operating discipline:
- Follow .claude-userlevel/skills/_shared/tdd/tdd-loop.md: pick one AC, write failing
  test, confirm red, write minimal impl, confirm green, refactor if useful, next AC.
- Every item in the issue's acceptance criteria MUST have at least one corresponding
  test. Marking an AC item as "out of scope" is a delivery defect, not a scope
  decision — escalate to the orchestrator instead of dropping the item.
- Refactor permission extends to code freshly covered by a passing test in this
  session. Code without test coverage is NOT in your refactor scope.
- **Deliberate divergences must be surfaced.** If you depart from the AC's literal
  signature, parameter names, values, default constants, or interpretation for any
  reason (cleaner interface, stricter rule, fewer args, renamed field) — add a
  `## Deliberate divergences` section to the PR body listing each change as
  `<what diverged> — <why> — <impact>`. Silent design drift is a delivery defect
  even when the divergence is reasonable. The orchestrator must be able to weigh
  it without re-reading the diff. (Lesson from #634 outcome 2026-05-19: subagent
  silently reshaped `decide(...)` from 4 args to 3 and picked stricter
  no_convergence rule than the AC suggested.)
- **Drive-by edits: remove means remove, not replace.** When an issue or instruction
  says "remove stale X" or "delete the line about Y", you DELETE — you do NOT
  rewrite the line with new content. If a replacement is genuinely needed, that is
  a separate scope question to escalate, not a drive-by reinterpretation. Before
  inserting any text in a drive-by neighborhood, grep ±3 lines around the change
  to confirm the addition isn't duplicating an existing nearby line. (Lesson from
  #662 outcome 2026-05-19: subagent REPLACED a stale `skills/sprint-report/`
  bullet with new `agents/` text that duplicated the next existing line;
  orchestrator pushed a collapsing fix commit.)
```

ADR-0001 compliance: the TDD-mode block is **inline operating discipline**. There is no standalone `/tdd` skill (dropped in #596). The subagent does not call `/grill` or any other skill mid-task — the reference doc `_shared/tdd/tdd-loop.md` is read as a file.

**Principal env propagation note (#426)**: The Agent tool inherits parent env, so `JARVIS_PRINCIPAL` set in the parent session carries to the subagent. Auto-injection of `JARVIS_PRINCIPAL=subagent` is deferred — the parent is `live` (principal-driven dispatch), and subagents are already constrained by the worktree isolation and skill-level rules above. If a future code path runs `/delegate` autonomously (e.g. dispatcher hands work to a subagent), revisit and inject `JARVIS_PRINCIPAL=subagent` explicitly via the spawn wrapper.

### 4a. Worktree-isolation caveat

**`isolation: "worktree"` is advisory only, not guaranteed.** Documented failures: #295, #640 v1, #640 v2, and 2026-04-20 parallel-delegate contamination (memory `parallel_delegate_worktree_isolation_failed_2026_04_20`). Observed modes:
- Branch-name races (two agents picked the same branch)
- Cross-worktree file contamination (writes from agent A showed up in agent B's worktree)
- Worktree not created at all — agent worked in the main repo directly

**Mitigations:**
- Branch names are explicit, per-issue, and unique (`feat/<N>-<slug>`) — encode in the prompt
- Cap parallelism: **2-3 agents concurrently** is the safe band; 5+ is a red flag
- **Review the diff in the main repo tree as the authoritative check** — `cd <main repo> && git fetch && git diff origin/main...origin/feat/<N>-<slug>`. Treat the agent's worktree as a staging area, not a trusted sandbox.
- If you see contamination, abort and re-dispatch sequentially

### 5. Implement inline tasks (parallel with the subagents)

While subagents run, use the /implement pipeline for anything you kept for yourself. The two streams are independent.

### 6. Review each subagent's diff

When a subagent reports done, review **in the main repo tree, not the agent's worktree** (§4a — worktree isolation is advisory):

```bash
cd <main repo>
git fetch origin
git diff origin/main...origin/feat/<N>-<slug> --stat
git diff origin/main...origin/feat/<N>-<slug>
```

Run the following checks in order — do NOT short-circuit:

- **PR exists?** `gh pr list --head feat/<N>-<slug> --state all --json number,url`. If the subagent reported "done" but no PR row comes back, the dispatch contract was violated (see HARD RULES "Open it means gh pr create"). Recover: orchestrator opens the PR manually from the pushed branch, and note the contract violation in the outcome `lessons` field so the recurrence count increments. Do NOT silently paper over — the lesson telemetry is what closes the loop.
- **Scope fit**: file list matches issue scope? Unrelated files → revert
- **Protected files** untouched?
- **Deliberate divergences declared?** If the diff diverges from the AC's literal signature/values/interpretation and the PR body has no `## Deliberate divergences` section, treat as silent design drift: either revert the divergence or push the section yourself (with the subagent's reasoning if recoverable, else flagged as orchestrator-reconstructed). Recurrence increments the lesson telemetry the same way as a missed PR creation.
- **Value-change audit**: grep the diff for numeric literals, default parameter values, seed arrays, tuple constants, timeouts, thresholds. For each value that changed, confirm the change is explicitly mandated by the issue body. Silent replacements are scope drift and must be reverted.
  - Lesson #648: subagent silently replaced IK seeds `[0,-30,30,0,-60,0]` with `ready_position` — same shape, different semantics (optimizer convergence inputs, not motion targets). Silent behavior change the subagent didn't flag.
- **Interaction audit**: for every non-trivial edit, trace data flow outward — what callers depend on the pre-existing behavior? what fallbacks or post-processors run after the changed code? Ask "does this still compose correctly?" not just "does the code do what it says?"
  - Lesson #649 v2: after 6-DOF IK fires for in-place rotation, the pre-existing J6-pin fallback would clobber J6 back to its previous value, silently undoing the rotation. The subagent's diff was locally correct but interacted incorrectly with existing code.
- **Tests added + passing?** Run them from the main repo on the branch (`git checkout feat/<N>-<slug> && pytest ...`).
- **PR body** rich? (matches /implement §5 template)
- **Debug code / secrets** leaked?
- **Symmetric patterns**: did the subagent fix only the one instance, or apply to siblings too?

If issues found:
- Push a fix yourself (faster than re-prompting for small stuff)
- Or use `SendMessage` to the agent with specific revision instructions
- If the diff contains ANY silent value change or broken interaction → revert those hunks before merge decision, even if the rest is fine

### 7. Decide merge (orchestrator only)

Per each PR (subagent's or your own):
- Tests green + Claude code-review clean + LOW/MEDIUM risk → **merge** (see /implement §7.5)
- HIGH/CRITICAL or safety-critical → wait for principal
- CI infra-blocked (billing, not failing tests) → merge if local tests pass + Claude code-review clean

### 8. Record outcomes

One `outcome_record` per issue (delegated or inline). Distinguish:
- Subagent: `pattern_tags: ["delegation", "subagent", "<area>"]`
- Inline: `pattern_tags: ["delegation", "inline", "<area>"]`

**Also pass `memory_id`** — the primary informing memory id from the per-task `record_decision` episode. Rule: the first element of `memories_used` that is a **memory-row UUID** (an id from `memory_recall`/`memory_get` — the recall map). Decision-**episode** UUIDs also appear in `memories_used` (grill decisions cited in issue bodies, `record_decision` returns) and are NOT valid — the FK `task_outcomes.memory_id → memories(id)` rejects them with 23503; provenance, not shape, tells them apart. For batch-level inline work, use the inline task's own decision_made memories_used; for subagent work, the subagent's record_decision episode is the source. If no memory-row UUID is present (or `memories_used` was empty at decision time), omit `memory_id`.

In `lessons`, note anything non-obvious: subagent misread the issue, scope drift, worktree contamination, etc. These make future delegation decisions smarter.

### 9. Post-merge cleanup

For each merged PR:
```bash
git checkout master && git pull
git branch -d feat/<N>-<slug>
# if this was a subagent job:
git worktree remove <worktree-path>
```

Stale worktrees and branches accumulate fast with multi-issue batches. Clean up at the end.

## Safety rules
- All /implement safety rules apply
- **Subagents must NEVER**: merge PRs, force-push, modify protected files, send messages as principal
- Orchestrator reviews **every** subagent diff before merge
- Parallelism > 3 concurrent → red flag; prefer sequential or smaller batches
- If principal says "параллельно все" but one task is unfit → keep it inline and tell the principal why

## Recovery playbook

See `docs/security/recovery-playbook.md`. Subagent-specific:
- Subagent edited wrong files → revert in its worktree, re-prompt with stricter scope
- Subagent broke its worktree → `git worktree remove --force <path>` + reclaim branch in fresh worktree
- Two subagents raced on same file → abort both, re-dispatch sequentially
- Subagent silently merged → revert merge on `main` immediately, open incident note, add to agent-boundaries.md
