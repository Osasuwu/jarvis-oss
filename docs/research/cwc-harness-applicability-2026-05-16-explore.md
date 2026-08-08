# cwc-harness applicability — pre-grill briefing

**Status:** Draft (research, not committed).
**Date:** 2026-05-16.
**Author:** AFK autonomous chain iter:21.
**Audience:** Owner, on return, before deciding which long-running-agent harness primitives Jarvis should absorb.

This draft compares the four most-cited long-running-agent harnesses surveyed in `docs/research/autonomous-day-orchestration-2026-05-16-v2.md` §3 and §6 against what Jarvis already has, and proposes a phased adoption path.

The chain has been generating evidence that several of these primitives are load-bearing (the four enforcement-primitive trackers `#650`/`#651`/`#652`/`#653` and the iter:20 synthesis converge on "Jarvis lacks mechanical analogues of fresh-context evaluator + verify-gate"). This briefing widens the lens: every harness primitive across the four candidates, mapped to Jarvis's current surface, with adopt/partial/skip verdicts.

Companion to the iter:20 enforcement-primitive synthesis. Where that synthesis answered "which primitive for *failure-class X*", this answers "which harness offers what, and in which order should Jarvis absorb pieces".

## 1. Candidates surveyed

| Harness | Source | Posture | Footprint |
|---|---|---|---|
| **`anthropics/cwc-long-running-agents`** | Anthropic reference, Code with Claude 2026 take-home | Anthropic-blessed pattern set; explicitly *not turnkey* | 3 primitives + 4-section `PROGRESS.md` + 2 hooks |
| **`Sonovore/claude-code-handoff`** | Community, production-engineered | Hook-driven automatic handoff + manual `/handoff` command | 4 hooks + 1 command + state-file family |
| **`parcadei/Continuous-Claude-v3`** | Community, maximalist | Forcing-function injection of skills/agents/hooks on every prompt | 109 skills, 32 agents, 30 hooks |
| **`SuperClaude` framework** | Community, Serena-MCP-backed | Three commands (`/sc:load`, `/sc:save`, `/sc:reflect`) + four memory types | 3 commands + Serena MCP |

Patrick Hardiman's open feature request `anthropics/claude-code#11455` is the **converged shape** of the first two — included as a fifth row for reference but not a separate harness.

## 2. Primitive inventory

Pulling out the discrete primitives, not the bundles:

| Primitive | cwc | Sonovore | Continuous-Claude | SuperClaude | Jarvis today |
|---|---|---|---|---|---|
| **P-progress** Standardised baton file (Done/InProgress/Next/Notes) | `PROGRESS.md` (4 sections) | `session-state.md` + `current-task.md` | YAML handoff | `/sc:save` to Serena | `working_state_jarvis` memory + `.scratch/handoff.md` (free-form) |
| **P-session-start** SessionStart hook reads baton | injected via CLAUDE.md instruction | `session-start.sh` | hook | `/sc:load` (manual command) | `scripts/session-context.py` injects memory snapshot |
| **P-session-end** SessionEnd hook writes baton | `commit-on-stop.sh` + agent self-update | `live-handoff.sh` (UserPromptSubmit, continuous) + `pre-compact-handoff.sh` | hook | `/sc:save` (manual command) | **MISSING** — agent self-updates baton at end of `/end` skill, no hook backstop |
| **P-pre-compact** Pre-compaction state dump | not explicit; relies on `PROGRESS.md` staying current | `pre-compact-handoff.sh` PreCompact hook | hook | n/a | **MISSING** — autocompact runs blind |
| **P-evaluator** Fresh-context evaluator subagent (no Write/Edit) | `evaluator.md` agent + PASS/NEEDS_WORK contract | not present | "evaluator" among 32 agents (not the same shape) | n/a | **MISSING** — `/verify` exists but same-context, not fresh-context |
| **P-verify-gate** Hook denies write-to-results without evidence-read | `verify-gate.sh` PreToolUse on `Edit`/`Write` to `test-results.json` | not present | n/a | n/a | **MISSING** — `verify_before_assuming_implemented` is prose-only |
| **P-default-fail** AC criteria start `false`; cannot flip without opening evidence | embedded in `PROGRESS.md` convention | n/a | n/a | n/a | **MISSING** — AC items in issue bodies are checkbox prose, no machine-readable state |
| **P-agent-stop** `AGENT_STOP` file = mid-run operator halt | `kill-switch.sh` PreToolUse | n/a | n/a | n/a | **PARTIAL** — AFK chain has `.scratch/AGENT_STOP` check at the top of each iteration (this kickoff), but it's a kickoff-prompt convention, not a hook |
| **P-steer** `STEER.md` = mid-run one-time operator nudge | `steer.sh` PreToolUse, file cleared after surfacing | n/a | n/a | n/a | **MISSING** |
| **P-recovery** Post-hoc transcript-mining to rebuild handoff after agent already failing | n/a | `/handoff` Recovery mode | n/a | n/a | **MISSING** — owner does this manually (paste-summary workflow) |
| **P-skill-forcing** Inject "CRITICAL skills / RECOMMENDED skills" into every prompt | n/a | n/a | core mechanism | n/a | **MISSING** — skill discovery is description-matching, not forced injection |
| **P-memory-typology** Multi-type memory schema (project/session/pattern/progress) | not present | not present | learnings extracted by daemon | 4 types in Serena | **PARTIAL** — Jarvis has `decision/feedback/reference/user/project`, 5 types but different cuts |
| **P-checkpoint-commit** Session-stop git commit of tracked files | `commit-on-stop.sh` one-liner | n/a | n/a | n/a | **MISSING** — relies on agent to commit; no hook backstop |
| **P-event-store** Every action/observation as durable event (OpenHands-style) | n/a | n/a | n/a | n/a | **out of scope** — overkill per v2 §7 |

## 3. Jarvis-gap summary

Jarvis is **strongest** on:

- Memory layer is stricter than every candidate (Supabase, `always_load` gates, `source_provenance` requirement, brief-mode UUIDs). Don't downgrade to the bare Anthropic memory tool (v2 §3 finding).
- SessionStart context loading via `scripts/session-context.py` already covers P-session-start.
- Memory typology (5 types) is wider than SuperClaude's 4 — but the *cuts* are different.
- Issue/PR discipline (`/triage`, `Closes #N`, PR Body Check) has no analogue in any of the four harnesses; this is a Jarvis-specific advantage.

Jarvis is **weakest** on mechanical enforcement primitives:

1. **No P-session-end hook** — agent writes baton only when running `/end`, which depends on the agent remembering. After context exhaustion (chain run #1 iter:12-24) the baton wasn't updated because the agent never reached `/end`.
2. **No P-pre-compact hook** — autocompaction is blind; memory `post_compaction_task_premise_verification` documents this exact failure class (tracker `#653`).
3. **No P-evaluator** with separate context — `/verify` runs in the same session as the work, which is precisely what the cwc README warns against ("trust the builder's own assessment").
4. **No P-verify-gate** — `verify_before_assuming_implemented` is prose. The iter:20 enforcement-primitive synthesis converges on "hook" as the load-bearing missing layer.
5. **No P-default-fail** — issue AC checkboxes aren't machine-readable; AC-dodge (`#652`) is enabled by this.
6. **No P-steer** — no mid-run operator nudge channel. The chain has been running ~10 iterations with the owner unable to redirect without entering the session.
7. **No P-recovery** — owner's "paste-summary" manual workflow is the worst case of the design space (v2 §6 finding).
8. **No P-checkpoint-commit hook** — one-liner from cwc; cost is zero.

## 4. Adopt / partial / skip verdicts

The bar: adopt only what addresses a confirmed-recurring failure, fits Jarvis's existing memory layer, and costs less than a milestone.

### TIER A: Adopt as-is

| Primitive | Rationale | Cost | Risk |
|---|---|---|---|
| **P-checkpoint-commit** | One-line `Stop`-event hook. Zero cost. Backstops baton durability. | 5 min | none — `commit -am` is tracked-only, no clobber |
| **P-agent-stop** | Already a convention in AFK chain kickoff. Promote to a hook so it works mid-run, not only at iteration boundary. | 30 min | none — file-presence check |
| **P-steer** | Maps onto owner's "AFK but spotted something in Telegram" workflow. PreToolUse hook reads `STEER.md`, surfaces once, deletes. | 1 h | low — single file ops |

### TIER B: Adopt with adaptation

| Primitive | Adaptation | Cost | Risk |
|---|---|---|---|
| **P-session-end hook** | Hook calls a `/end --quick` variant that just upserts `working_state_<project>` via MCP. Avoids dependency on agent reaching `/end` voluntarily. | half-day | medium — needs reliable detection of project root (multi-repo / sandcastle) |
| **P-pre-compact hook** | PreCompact hook emits a system reminder: "before compaction, save current task state to working_state". Doesn't block compaction; just forces the agent to externalise. Memory `179ee1f2` (`post_compaction_task_premise_verification`) already exists; this is its hook backstop. | half-day | low — additive |
| **P-evaluator** | Adapt cwc's `evaluator.md` (PASS / NEEDS_WORK contract) as a Jarvis subagent invoked by `/verify`. Crucially: separate model class OR explicit `--clear`-equivalent context. | day | medium — Jarvis `/verify` skill exists but currently same-context; restructure required |
| **P-verify-gate hook** | PreToolUse hook on Edit/Write to specific state files (e.g. `working_state_*`, PR-body templates) that requires a recent Read of corresponding evidence. The iter:20 synthesis's primitive P2'/P3 — confirmed as needed by trackers `#650`/`#651`/`#652`/`#653`. | day-plus | medium — false-positive risk; needs allow-list of legitimate writes |

### TIER C: Partial adopt, narrowly scoped

| Primitive | Adaptation | Cost | Risk |
|---|---|---|---|
| **P-progress 4-section baton** | Reshape `.scratch/handoff.md` + `working_state_jarvis` content to use `## Done / ## In progress / ## Next / ## Notes`. Don't change the storage layer (Supabase memory beats `PROGRESS.md` on disk for cross-device). Just the *internal structure* converges with Anthropic-blessed shape. | 1 h | none — content reshape |
| **P-default-fail** | Adopt for AFK chain only initially. Each iteration's task description starts every AC as `[ ] AC-N: <claim>`. `/verify` (or evaluator) flips to `[x]` only after evidence read. Don't generalise to every issue body yet — overkill for trivial PRs. | half-day | low |
| **P-recovery** | Port Sonovore's `extract-transcript.py` to Jarvis-style. Trigger: owner runs `/recover` when current session is failing. Don't run automatically (cost of false positives) — manual is fine. | day | low — read-only on JSONL |

### TIER D: Skip

| Primitive | Why skip |
|---|---|
| **P-skill-forcing (Continuous-Claude style)** | Jarvis already has `Skill is a contract, not a trigger` rule (CLAUDE.md). Forcing-function injection into every prompt is heavyweight and noisy; the rule + skill descriptions are working. Revisit only if `/reflect` audits show skill-skip pattern recurring. |
| **P-memory-typology (SuperClaude's 4 types)** | Jarvis's `decision/feedback/reference/user/project` typology serves a different cut (lifecycle / source / audience) than SuperClaude's (scope / domain). Re-typing memories is a major migration cost for unclear gain. Skip until a concrete query that Jarvis can't answer surfaces. |
| **P-event-store (OpenHands)** | v2 §7 finding: overkill for Jarvis's use case. Owner wants summary handoff, not 5000-event re-execution. |
| **Continuous-Claude wholesale (109/32/30)** | Too invasive; Jarvis's `.claude/*` surface is curated. Cherry-picking individual primitives (above) captures the value without the maintenance debt. |
| **SuperClaude wholesale** | Storage layer (Serena MCP) duplicates `mcp-memory/server.py`. The 4-type schema isn't compelling enough to migrate to. Skip. |

## 5. Phased adoption path

Sequencing matters: lowest-risk, highest-leverage first; each phase produces a verifiable artifact.

**Phase 1 — Tier A (free wins, ~2 hours total):**

1. Add `commit-on-stop.sh` Stop hook to `.claude/settings.json`. Tracked-files-only commit on every session end. Wire into PR per CLAUDE.md "no direct `.claude/*` edits".
2. Promote `.scratch/AGENT_STOP` check from AFK chain kickoff convention to a PreToolUse hook. Works mid-iteration, not only at boundary.
3. Add `STEER.md` PreToolUse hook: read once, surface as user-message, delete. New owner-control channel without entering session.

**Phase 2 — Tier B P-session-end + P-pre-compact (~1 day):**

4. SessionEnd hook calls `mcp-memory` MCP directly to upsert `working_state_<project>`. Removes agent-reaches-`/end` precondition. Skill `/end` continues to exist for the structured close; hook is the backstop.
5. PreCompact hook emits "externalise current task state" reminder. Doesn't block — just reminds. Couples with existing memory `179ee1f2`.

**Phase 3 — Tier B P-evaluator + P-verify-gate (~2-3 days):**

6. Restructure `/verify` skill to invoke a fresh-context evaluator subagent (no Write/Edit). Adopt the PASS / NEEDS_WORK contract verbatim.
7. PreToolUse verify-gate hook on writes to specific state files. Iter:20 synthesis P2'/P3 — gated by phase 2 because it depends on the evaluator existing to do the verifying.

**Phase 4 — Tier C content reshape + opt-in (~1-2 days):**

8. Reshape `working_state_jarvis` and `.scratch/handoff.md` to 4-section convention.
9. Adopt P-default-fail for AFK chain task descriptions (kickoff-prompt change, not codebase).
10. Port Sonovore's `extract-transcript.py` as `/recover` skill. Manual trigger.

Total cost across all phases: **~1 calendar week**. Phase 1 alone unblocks ~30% of the value at ~2 hours of work.

## 6. Trade-off summary

| Trade | Cost of adopting | Cost of skipping |
|---|---|---|
| **P-evaluator (fresh-context)** | restructure `/verify`, subagent dispatch overhead | continued false-PASS from same-session verification (trackers `#651`, `#652`) |
| **P-verify-gate hook** | risk of false-positive blocks, allow-list maintenance | iter:20 enforcement-primitive synthesis's conclusion remains "prose-only — model can ignore under load" |
| **P-session-end hook** | doubled write path (agent self-saves + hook saves) — must converge or last-write-wins is fine | baton stale every time agent OOMs / quota-exhausts (run #1 iter:12-24 was exactly this) |
| **P-pre-compact hook** | one more system-reminder noise source | autocompact strips state silently (tracker `#653`) |
| **P-checkpoint-commit** | none observed | baton on disk lost on `git clean` (issue `#648` is this exact bug for the wrapper script) |

## 7. Owner-decision points (for grill)

1. **Is the four-section convention worth the reshape?** Pure content cost, no risk. But: working_state's free-form structure has been load-bearing for 20 iterations and may be more expressive than the 4-section box.
2. **Phase 2's SessionEnd hook competes with `/end` skill.** Should `/end` become a thin wrapper that invokes the hook? Or stay independent and accept eventual divergence?
3. **Phase 3's evaluator restructure changes `/verify`'s semantics.** Owner needs to decide: replace, parallel, or `/verify --fresh-context` opt-in?
4. **Verify-gate's allow-list is the bikeshed.** Which writes are gated? Just `test-results.json` (cwc default)? Or also `working_state_*`, PR bodies, AC checkboxes? Scope is the whole question.
5. **`STEER.md` location.** `.scratch/STEER.md` (gitignored) or `.claude/STEER.md` (versioned)? Owner intervention should probably be ephemeral → `.scratch/`.
6. **Adopting `extract-transcript.py` (P-recovery) implies trust in JSONL parsing.** If the JSONL format changes upstream, recovery breaks. Owner: is this acceptable single-point-of-failure for a manual-trigger tool?
7. **Are Phase 1's three Tier-A items worth opening as one PR or three?** One PR is simpler; three is independently revertable. Sandcastle subagent fragility suggests three.

## 8. Out of scope (for this synthesis)

- The MCP-memory layer architecture itself. Jarvis's memory layer is **better** than every candidate's. Don't migrate.
- Continuous-Claude / SuperClaude wholesale adoption — see Tier D.
- OpenHands event-store — see Tier D + v2 §7.
- Hardiman issue request implementation status in upstream Claude Code — chain-opened tracker `#605` already covers milestone-close detection; session-resume upstream is a watch-only item.
- Specific hook code. This briefing is *what* and *why*; *how* is per-phase implementation work.
- Sandcastle-specific applicability. Sandcastle is a Jarvis-owned subagent dispatch surface, not a long-running-agent harness in the cwc sense. Some primitives (P-evaluator, P-verify-gate) overlap but the analysis is separate work.

## 9. Recommended owner read order

1. **§3 (gap summary)** — see what Jarvis already has vs lacks.
2. **§4 Tier A** — three free wins, ~2 hours, no controversy.
3. **§5 Phase 1** — concrete first PR.
4. **§7 grill points 1, 4, 5** — these block Phase 1.
5. Defer §4 Tiers B-C until Phase 1 lands.

## 10. Convergence with iter:20 enforcement-primitive synthesis

The iter:20 briefing concluded **no single primitive covers all 4 enforcement classes**, recommending P1 (skill epilogue) + P3 (PR template) for delegate-class and P2' (Edit/Write hook) for main-session-class. This briefing **converges with that conclusion at a different angle**:

- iter:20 P2' (Edit/Write hook) ≡ this draft's **P-verify-gate** (Phase 3, item 7).
- iter:20 P1 (skill epilogue) is **complementary** to **P-evaluator** (Phase 3, item 6) — skill text reminds, evaluator verifies.
- iter:20 P3 (PR template + Action) is the **PR-side analogue** of P-default-fail (Phase 4, item 9); both make AC machine-tracked.
- iter:20 P4 (todo schema) overlaps with **P-progress 4-section baton** (Phase 4, item 8); both standardise the task-state shape.

Reading both briefings together: the enforcement primitives the chain has been begging for *are exactly_the primitives the cwc reference harness ships*. Jarvis isn't missing a novel mechanism — it's missing the documented Anthropic-blessed implementation of mechanisms whose absence the chain has independently rediscovered four times.

**Practical implication:** when owner grills enforcement-primitive choice, the answer should leverage cwc's existing implementations (verify-gate.sh, evaluator.md, kill-switch.sh, steer.sh) rather than design fresh ones. Copy-and-adapt is the right posture.

## Appendix: artifact links

- Source: `docs/research/autonomous-day-orchestration-2026-05-16-v2.md` §3 (cwc, lines 286–485), §6 (community implementations, lines 604–725), §7 (event-store skip, lines 728–751).
- Companion: `docs/research/enforcement-primitive-synthesis-2026-05-16-explore.md` (iter:20).
- Trackers converging on the same problem-space: `#650`, `#651`, `#652`, `#653`.
- Wrapper-preservation tracker (parallel concern): `#648`.

**End of draft.** Sized for ~5-minute owner read; matrix and Tier-A list designed to be the only sections needed for the first grill.
