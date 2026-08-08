# Autonomous day orchestration — research synthesis (2026-05-16)

Author: research session (this turn)
Companion docs: `docs/research/deep-dive-ralph-loop-backpressure.md` (Ralph + back-pressure already covered there — referenced, not re-derived), `docs/research/deep-dive-memory-architectures.md`, `docs/research/agent-dev-practices-sweep-2026-05-06.md`.

Scope: validate / critique Petr's ~2-day AFK plan. Manager session dispatches → subagents + sandcastle (DeepSeek) + future-self via Windows scheduled-task watchdog. Baton via `working_state_jarvis` (Supabase) + `.scratch/handoff.md`.

This doc captures specifics. Synthesis returned inline to the principal.

---

## 1. Long-horizon agent architectures — what they actually do

### Devin (Cognition)

- **Architecture.** Compound system: high-reasoning Planner + executor swarm + sandbox VM. Long-running session per task, no cross-session memory as of mid-2025.
- **Failure modes (from 2025 retrospectives, sitepoint + aidevdayindia).**
  - **Knowledge-gap blindness.** "Not 'agent can't do the work' — 'agent can't tell you what it doesn't know.'" Silent confidence on missing context.
  - **Architectural empathy collapse.** Patches by stacking conditionals rather than refactoring. Produces compounding "Performance Optimization Debt" and "Model-Stack Workaround Debt."
  - **Multi-file coordination degrades** as edits span >3–4 files.
  - **Completion rate** on end-to-end production tasks: single-digit to low-double-digit % per early-2025 evals.
- **Implication for Jarvis.** Don't let any single agent (manager OR subagent) own >1 capability area for longer than its smart-zone budget allows. The "knowledge-gap blindness" maps to our `verify before assuming implemented` posture — must hold under autonomous execution.

### Cursor / Cline / Aider

- **Cline:** autonomous coding agent, SDK + IDE + CLI. Has explicit `auto-approve` modes, but production reports flag scope creep when given multi-issue scope without explicit AC.
- **Aider:** smaller, surgical, single-task focus. Stays in smart-zone by design — short conversations, commit per change. Counter-pattern to Devin's "one long session."
- **Cursor agents (Composer / Background Agents):** worktree-isolated by default since late 2025. Spawn fresh-context per task is the standard mode.

### Anthropic's own multi-agent research system

Primary source: `anthropic.com/engineering/multi-agent-research-system`.

- **Topology.** Lead agent (Opus class) + N parallel subagents (Sonnet class). Subagents return condensed findings, not raw transcripts.
- **Hard numbers from Anthropic.**
  - Multi-agent uses **~15× tokens vs chat, ~4× vs single agent**. Only justified for high-value or breadth-first tasks.
  - Their multi-agent + Opus-as-lead beat single-Opus by **+90.2% on internal research eval**.
  - **Token budget alone explains 80% of performance variance** (BrowseComp). Model choice & tool calls = remaining 20%.
- **Concrete prompt patterns to prevent over-exploration.**
  - **Embed explicit scaling rules.** "Simple fact-finding: 1 agent, 3–10 tool calls. Comparisons: 2–4 subagents, 10–15 calls each. Complex: >10 subagents with **clearly divided responsibilities**." Without this, early systems spawned 50 subagents for trivial queries.
  - **Start broad, narrow progressively.** Counter the "overly long specific query returns 0 results" failure.
  - **Briefing template:** objective + output format + tool/source guidance + **explicit task boundaries**. Without boundaries: duplicate work, gap coverage, source confusion. Anthropic's quoted failure: "research the semiconductor shortage" → 1 subagent dug 2021 auto chip crisis, 2 others duplicated 2025 supply chain work.
- **Production failure modes Anthropic admitted.**
  - **Source quality bias** — agents prefer SEO content farms over authoritative-but-low-ranked sources.
  - **Non-determinism** complicates debugging: identical prompts, different runs.
  - **State complexity:** long-running agents accumulate state across many calls; minor failures cascade.

### SWE-agent / OpenHands

- Same orchestrator-worker shape. Lessons from comparative study (vulnerability false-positive filtering): silent-failure rate is the killer metric, not raw task completion. Agents will return "done" with broken output, no flag raised.

---

## 2. Context handoff — Ralph, Amp Handoff, Anthropic memory tool

### Ralph (already covered in deep-dive, summary here)

- Loop: `while :; do cat PROMPT.md | claude-code ; done`. Each iteration **fresh context**.
- **State lives in files + git, not in LLM context.** Fresh-context-per-iteration defeats autoregressive failure.
- **Persistent baton files** (Huntley's set):
  - `@fix_plan.md` — prioritized incomplete-work list, updated each loop.
  - `@AGENT.md` — discovered commands, build/test, env quirks.
  - `@specs/` folder — searched before creating new code (prevents duplicate implementations).
  - `PROMPT.md` — instruction set, refined through observation.
  - Git history — source of truth on what was done.
- **One thing per loop.** Multi-task iterations drift; single-task converges.
- **Hard ceiling Huntley reports:** ~147–152K usable tokens before quality drops on Claude (out of advertised 200K).
- **Huntley's economic data point:** delivered a $50K-equivalent MVP for **$297** of Claude API spend using this pattern.

### Amp Handoff (Sourcegraph)

Primary source: `ampcode.com/news/handoff`.

- **Built explicitly as compaction replacement.** Compaction is lossy; "summary on top of summary" stacks, encourages "long meandering threads," produces worse outcomes than fresh threads.
- **Handoff produces:** a prompt to start the new thread + a list of relevant files. **User-editable draft** before the new thread launches — so the operator controls exactly what crosses the boundary.
- **Design intent:** "promote focused threads, because that's how agents yield the best results."
- **No token threshold specified** — handoff is manual / on-demand.

### Anthropic memory tool

- Client-side `/memories/` directory the model reads/writes between turns. **Workspace-scoped**, not session-scoped — survives session death.
- Managed-Agents memory store (April 2026 beta): `/mnt/memory/` directory mounted in agent container, persists across runs. Netflix / Rakuten reported 97% reduction in first-pass errors with structured memory bootstrap.
- **Pattern that survives compaction + fresh-start:** memory files bootstrapped at session start (read first), updated at decision/finding points (not batched at end), structured so a fresh agent can "pick up exactly where the last one left off."

### Canonical baton structure (synthesis across Ralph + Amp + Anthropic memory + Jarvis prior art)

What every successful baton has:
1. **Goal** — single-sentence outcome the next session must achieve.
2. **Done so far** — bullet list of completed sub-steps with artifact links (issue #, PR #, file paths). Not narrative.
3. **Next action** — the literal first command / first file to read.
4. **Open questions** — explicit, dated. If empty, say "none."
5. **Don't-touch list** — files / branches / decisions that are settled.
6. **Recovery hint** — if the next session gets confused, where to look first (specific memory UUID or doc path).

Petr's plan already has #1–4 via `working_state_jarvis` + `.scratch/handoff.md`. **Add #5 and #6** — they're the highest-leverage adds.

---

## 3. Smart-zone / dumb-zone — concrete thresholds

Aggregated from claude-codex.fr, paddo.dev, amittiwari.substack, agents-squads.com, ampcode.com:

| Model / window | Smart zone | Dumb zone starts | Notes |
|---|---|---|---|
| Claude Opus 4.7 / Sonnet 4.6 (200K standard) | 0–80K | ~80–100K | Reliable reasoning ends here. Common practitioner heuristic. |
| Claude Opus 4.7 / Sonnet 4.6 (200K) | 0–40% (~80K) | beyond 40% fill | "Dumb zone hits 60K–150K on 200K window." Wider band reflects task-type sensitivity. |
| Claude Opus 4.7 / Sonnet 4.6 (1M beta) | first ~200–256K | beyond 256K | Anthropic engineering acknowledged: 1M is "container, not capability." 750K is marketing. |
| Huntley's observed practical ceiling | up to ~147–152K | sharp drop after | Loop-style, agent-only consumption. |

**Practitioner heuristics, ranked by how often they appeared:**

1. **Hard handoff at ~70% fill** (i.e. ~140K on 200K). Default for most practitioners.
2. **Soft handoff prep at ~40–50% fill** — write the baton early while the model still has clear recall. Compact-or-handoff produces better output here than at 90%.
3. **Compact between distinct tasks within a session** even when not near the limit. "Single session for five different tasks" is the dominant practitioner-cited anti-pattern.
4. **Fresh session per task** beats long sessions on quality, even at total-token-cost parity. Cited consistently across Ralph, Amp, Anthropic blog.

**Petr's numbers (70K soft / 100K hard) are conservative and correct.** Sits comfortably in the soft-prep zone with margin. Many practitioners push later — but for autonomous, no-human-in-loop, conservative is the right call. Don't loosen.

---

## 4. Watchdog / supervisor — guarantees against primary-agent death

Aggregated from cipherbuilds.ai, dev.to/bobrenze, openclaw issue tracker, Medium continuity-planning piece.

### Failure modes the watchdog must cover

1. **Zombie task** — process alive, heartbeat normal, work has actually stopped (rate-limit pause, infinite tool-call loop, model returned empty). Hardest to detect.
2. **Hard crash** — process dies, scheduled task didn't fire, or fired and exited 1.
3. **Context exhaustion mid-task** — session hits limit without writing baton.
4. **Compaction collapse** — auto-compact ran but lost the load-bearing context. Session continues but quality dropped silently.
5. **State corruption** — `working_state_jarvis` written but malformed; next session fails to bootstrap.

### Pattern that handles all five

- **Separate process for monitoring vs execution** — independent failure modes. Petr's plan has this (Windows scheduled task is independent of the live session). ✓
- **Heartbeat ≠ progress.** Don't rely on "did the task fire" — rely on "did the artifact advance." Watchdog should grep recent commits / issue updates / memory writes; if nothing in N hours, assume zombie.
- **State externalisation at every decision point**, not at task end. Crash mid-task ⇒ baton already captures latest known good state. Maps onto the existing `record_decision` cadence Jarvis has.
- **Independent durable store** (Supabase) — file-based state on the dying machine is not recoverable. Petr has this. ✓
- **Loop-cycling cron** — periodic forced restart (e.g. every 3h) even if session looks healthy. Short clean sessions > marathon sessions. Worth considering for the manager session; standard for sandcastle.

### Where Petr's plan has gaps

- **No staleness detector.** Hourly `/autonomous-loop` is good. But: what if `/autonomous-loop` itself wedges? Need a 2nd-order check — e.g. "if no commit / no memory write / no issue update in 6h, force-restart manager session." Trivial to add via the same scheduled task: tail recent git log + memory writes; if all empty over window N, kill + relaunch.
- **No baton schema validation.** If session writes a malformed `working_state_jarvis`, next session bootstraps into garbage. Add: schema check at write time (the MCP server already validates `source_provenance`; extend to baton fields).
- **No "I'm about to die" signal.** When context hits the hard threshold, the dying session should set a memory flag (`handoff_in_progress=true` + ETA + baton location). Next session reads this first.

---

## 5. Manager-worker dispatch hygiene — known pitfalls

### Worktree contamination

- **Default mode is dangerous.** Parallel subagents without worktree isolation = silent overlap. One agent's write blasts another's; neither raises a flag.
- **Solve.** Claude Code now supports `isolation: worktree` in subagent frontmatter, or "use worktrees for your agents" instruction at dispatch. Each agent owns its branch + diff; manager reviews and picks/merges.
- **Already-bitten by Jarvis** (per Petr's plan) — this should be a hard rule, not a default, in the manager's dispatch routine.

### AC drift / "out of scope" silent declarations

- **Recurring pattern** across Devin, Cline, autonomous Claude Code agents: subagent decides part of the AC is "out of scope" and returns "done" anyway. No flag.
- **Counters that work:**
  - **AC as a checklist in the dispatch prompt.** Each item gets a yes/no in the return. Missing items = visible.
  - **Verification step before merge.** Manager re-greps for the symbol the AC promised. If absent, AC failed regardless of agent self-report. Petr's `git diff` rule + "verify before assuming implemented" posture already cover this — keep enforcing.
  - **Don't trust agent self-reports on files edited.** Run `git diff` in the agent's working dir. Diff empty ⇒ work was fabricated. (Already in Petr's CLAUDE.md.)

### Context saturation on token-heavy areas

- **Sandcastle is the canonical hot zone for Jarvis.** Big repo, many concurrent threads, dense logs.
- **Counter:** keep token-heavy work out of the manager's context. Manager dispatches "do X in sandcastle, return PR# + 3-line summary" — never reads the full sandcastle log. The orchestrator-bottleneck failure (Anthropic blog, Addy Osmani's "Code Agent Orchestra") is real: if all subagent findings flow through the orchestrator in full, the orchestrator dies first.

### Parallel-write contention

- **Worktree isolation removes file-level contention.** Doesn't remove resource contention (DB rows, scheduled-task slots, MCP server connection limits, Supabase rate limits).
- **Counter:** explicit serialisation primitive on shared resources. Petr's `mcp-memory/schema.sql` is shared with redrobot — parallel writers could collide. Status today probably fine due to low write volume, but worth checking under autonomous load.

### Subagent self-coordination is poor

- Anthropic explicit: "LLM agents are not yet great at coordinating and delegating to other agents in real time."
- **Implication.** Don't let subagents spawn sub-subagents during the AFK window. Single layer of delegation. Manager-to-worker, not manager-to-foreman-to-worker.

---

## 6. Things the plan probably misses

Opinionated, ranked by how likely they bite within 48h:

### a. The "first-restart" problem

Most plans test the happy path (manager runs, dispatches, exits cleanly). The first time the watchdog has to **cold-restart from a baton** is the moment the design gets tested. If the baton format has any ambiguity, the recovery session does the wrong thing — and now you have 47 hours of compounding error.

**Mitigation.** Before going AFK, **dry-run the cold start.** Kill the manager session, let the watchdog launch a fresh one, watch it bootstrap from `working_state_jarvis` + `.scratch/handoff.md`, verify it picks up the right next action. If it doesn't, the baton schema needs work, not the watchdog.

### b. Time-budget per task is missing from the plan

Petr's plan lists priorities but no time budgets. Without explicit budgets, manager will happily spend 20h on sandcastle hardening and skip everything else. The autonomous-loop research synthesis already showed that token budget is the dominant lever — same applies to wall-clock time in this context.

**Mitigation.** Per priority, set a soft cap ("max 2 manager sessions on sandcastle in any 12h window"). The autonomous-loop skill should enforce; if it can't, the priority list goes from "ranked by importance" to "ranked by what manager happens to find tractable" — which is a known failure mode (Anthropic's early agents).

### c. Compaction will fire whether you want it or not

Even at 70K soft handoff, if the manager session reads a long PR diff or a research doc, it can vault past 100K before the next checkpoint. Auto-compact will fire. Result: load-bearing baton context summarized to mush, session continues, downstream decisions made on lossy summary.

**Mitigation.** Disable auto-compact during AFK runs if Claude Code allows it (recent versions do — `/config` toggle). Force handoff-or-die rather than compact. If not configurable, narrow the soft threshold to 60K so even a +30K spike stays under hard limit.

### d. Sandcastle (DeepSeek) ≠ Claude — different failure shape

DeepSeek-Coder has different smart-zone characteristics. Treat its output with **higher skepticism**, not equal trust. Manager verification should be more aggressive on sandcastle-produced PRs than on Claude-subagent-produced PRs. The "no LOW-risk merges" rule already implies this; make it explicit.

### e. The "ready for human" parking lot is a memory black hole

Plan parks non-LOW-risk PRs for human review. Without a structured queue, by hour 36 there's a pile of 8 PRs, all stale, half conflicting with each other, all needing rebase. Mitigation: parked PRs go on a single GH project board column with explicit `parked-for-human` label + a memory record. When Petr returns, `/status` surfaces the parking lot as top of dashboard. Without this, on return Petr has to discover the parking lot by hand.

### f. No outbound to humans includes Telegram — but does it include GitHub PR comments?

PR comments are technically outbound. If subagent writes "@petr please review" on a PR, that's an alert. Decide explicitly: silent (no @-mentions, plain comments only), or noisy (@-mention on parked PRs only). Default to silent for AFK.

### g. Sibling-grep on fixes — under autonomous load

This is one of Petr's CLAUDE.md rules. Under AFK, it gets skipped because no human is enforcing. The autonomous-loop / verify steps need to re-state it explicitly in their prompts, or it doesn't happen. Pattern from Anthropic blog: behaviour must be in the prompt, not assumed.

### h. Memory write storm

Many small `memory_store` calls under autonomous load can hit Supabase rate limits or duplicate-detect failures. Worth checking the MCP server's batching behaviour before the AFK run starts.

### i. The autonomous-loop running hourly may itself eat all the budget

Hourly invocation × 48h = 48 manager sessions. Each consumes recall + bootstrap + dispatch tokens. If each is even 30K tokens average, that's 1.4M tokens just on coordination overhead. Worth measuring one cycle and projecting.

---

## 7. Concrete deltas (the punch list, all from above)

These are the diffs to Petr's plan, ranked by leverage.

1. **Dry-run a cold restart before going AFK.** (a) Single highest-impact validation.
2. **Add a 2nd-order staleness detector.** "No git/memory/issue activity in 6h → kill + relaunch manager." Cheap, prevents the silent-zombie scenario.
3. **Disable auto-compact for the AFK window** OR tighten soft threshold to 60K.
4. **Extend baton schema:** add `don't-touch list` and `recovery hint` (memory UUID + doc path). One-line additions.
5. **Per-priority time budgets** in the autonomous-loop config. Hard cap on sandcastle-area sessions in any 12h window so the priority list isn't theoretical.
6. **Structured parking lot.** Single GH label + project column for `parked-for-human`. `/status` reads it on Petr's return.
7. **Single layer of delegation.** No subagent spawning sub-subagents during AFK. Add to dispatch prompt explicitly.
8. **Re-state CLAUDE.md rules in autonomous-loop prompts.** Especially: sibling-grep, verify-before-assuming-implemented, git-diff-after-subagent. Don't assume the model "knows" — Anthropic explicitly says prompt-engineer the behaviour.

## 8. One thing to kill

**The "watchdog runs `/autonomous-loop` hourly" cadence is too aggressive.** Hourly × 48h = 48 cold-starts × bootstrap cost. Most cycles will have nothing meaningful to do (waiting on CI, sandcastle still running). The cycles burn tokens just to re-read context and discover there's no work. **Switch to event-driven or every-3-hours.** Triggers: CI completes, PR comment arrives, sandcastle finishes a slice, or 3h elapsed. Loop-cycling research (cipherbuilds, openclaw) consistently shows 3h as the sweet spot, not 1h.

---

## Sources

- Anthropic — How we built our multi-agent research system: https://www.anthropic.com/engineering/multi-agent-research-system
- Anthropic — Multi-agent coordination patterns: https://claude.com/blog/multi-agent-coordination-patterns
- Anthropic — When to use multi-agent systems (and when not to): https://claude.com/blog/building-multi-agent-systems-when-and-how-to-use-them
- Anthropic — Memory tool docs: https://platform.claude.com/docs/en/agents-and-tools/tool-use/memory-tool
- Geoffrey Huntley — Ralph Wiggum: https://ghuntley.com/ralph/
- Geoffrey Huntley — Everything is a Ralph loop: https://ghuntley.com/loop/
- Sourcegraph Amp — Handoff (no more compaction): https://ampcode.com/news/handoff
- Claude Codex — Context rot: https://claude-codex.fr/en/prompting/context-rot/
- AgentPatterns.ai — Context Window Dumb Zone: https://agentpatterns.ai/context-engineering/context-window-dumb-zone/
- Amit Tiwari — Smart Zone Problem: https://amittiwari.substack.com/p/the-smart-zone-problem-why-your-ai
- Paddo — Context Stops Being Scarce: https://paddo.dev/blog/million-token-context/
- Cipherbuilds — Why your AI agent crashes at 3 AM: https://cipherbuilds.ai/blog/ai-agent-crash-recovery-patterns
- Dev.to (bobrenze) — Stalled tasks and timeouts: https://dev.to/bobrenze/how-ai-agents-handle-stalled-tasks-and-timeouts-lessons-from-my-production-failure-1jj9
- Sitepoint — Devin in production: https://www.sitepoint.com/devin-ai-engineers-production-realities/
- Dev.to — Long-Horizon Agents (Maxim Saplin): https://dev.to/maximsaplin/long-horizon-agents-are-here-full-autopilot-isnt-5bo7
- Addy Osmani — Code Agent Orchestra: https://addyosmani.com/blog/code-agent-orchestra/
- Claude Code — Worktrees docs: https://code.claude.com/docs/en/worktrees
- SurePrompts — Devin AI Prompting Guide: https://sureprompts.com/blog/devin-ai-prompting-guide
