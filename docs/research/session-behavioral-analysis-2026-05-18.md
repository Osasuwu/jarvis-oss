---
title: Session behavioral analysis — what's actually broken and what isn't
date: 2026-05-18
status: draft
depth: deep-dive
inputs:
  - decision-audit-90d-2026-05-18.md
  - 6 jsonl transcripts (3 devices × 2 dates, ~26k events, 30-day window)
  - 6 companion research docs in this bundle
  - merge_2026-05-17/report_2026-05-17.md (cross-device /reflect aggregate)
  - live state: ~/.claude/skills/, .claude-userlevel/settings.json, CLAUDE.md, SOUL.md, CONTEXT.md, last 30 git commits
companion_research:
  - single-agent-workflows
  - single-vs-multi-agent-architecture
  - gh-workflows-solo-vs-team
  - cc-vs-codex-vs-alternatives
  - cheap-models-cost-reduction
  - gap-discovery-patterns
---

## TL;DR (≤300 words)

The workflow is not broken. It is **over-engineered relative to its measurement layer**. The audit-90d already names this as "85 BAD / 70 NORMAL / 95 GOOD" — the durable wins are real (three-way doc split, sandcastle, meta-tests, recall-before-deciding), but every BAD cluster traces to the same root: **you build instrumentation for the human (decisions, reflections, outcomes) and almost none for the system (evals, transcript-level cost view, cross-model second opinion)**. The recursion is showing — Claude grades Claude, Claude reflects on Claude, Claude evolves memories of Claude.

The five patterns hurting you most, ranked:

1. **Skill churn outruns the skill-proliferation antipattern you wrote yourself.** ~25 live skills + 7 `.bak.orphan` mirrors at `~/.claude/skills/` despite a v1.5 plan for "6+1". The contract `skill_proliferation_antipattern` is in memory; the local "fix-it" reflex wins anyway.
2. **`record_decision` post-hoc captures recur across all 90 days** (audit BAD #13, M#21 placeholder tick, iter:50 #669). The Tier-2 hook fires at call time, but the call gets emitted too late, so the gate's enforcement is on the *write* not on the *decision*.
3. **Subagent dispatch is your largest May failure surface** — 4 partial/failure outcomes in 2 weeks, same contract producing #689 (gold) and #690+#691 (contaminated) the same day. Quality is non-deterministic; you do not have orchestrator-side verification primitives.
4. **No leading-indicator eval harness.** Every audit is lagging. The M#43 sycophancy harness is the first golden-set you have — and it caught a counter-finding (heavy personalization *increases* sycophancy) that no amount of `/reflect` could surface.
5. **Doc-vs-code drift in your own infrastructure** (audit Correction #2: CLAUDE.md describes architecture-sweep auto-trigger as shipped; the code path does not exist). The same class is `outcome_record.memory_id` (#660, 5× recurrence) and schema-drift guard (#289/#310/#311).

**Single biggest move:** install one of the companion research's leading-indicator primitives (Hamel-style L1 golden set + Codex cross-model adversarial review on plans). Stop adding skills until then.

What is working that you must NOT touch: SessionStart hook contract (data-not-instructions), three-way doc split, `record_decision` UUID-not-name discipline (when it fires), sandcastle as redemption arc, meta-test rule for path-filtered guards.

## What the audit already says

The 90-day decision-calibration audit (`decision-audit-90d-2026-05-18.md`) dedupes ~250 items into 85 BAD / 70 NORMAL / 95 GOOD. Its cross-cutting findings:

- **Recall-before-action is the dominant failure mode of the window.** Always_load silt-up (23→1 forced by #641), `record_decision` post-hoc recurrence across all 90 days, the 2026-05-17 batch hitting 3 pre-existing memory classes simultaneously. Recall infrastructure exists; recall *discipline* fails at call time.
- **Re-decision pattern: the same name flips within 30 days.** /reflect (rejected → reintro), /end-quick (split → flag), /grill-* (3 → 1), /tdd (shipped → removed), milestone_structure v2 → v3, "owner"→"user", "epic" dropped. ~12 wasted decision pairs.
- **Skill churn at 4× growth despite documented antipattern.** v1.5 plan "6+1" → live ~25. `skill_proliferation_antipattern` memory exists; local "fix-it" reflex wins.
- **Subagent dispatch is the main May failure surface.** 4 outcomes (#665+#662 wholesale-fail, #690+#691 contamination, #687 hijack), same-contract non-determinism (#689 vs #690+#691 same day).
- **Two grill-driven decision storms** (2026-04-21/22 and 2026-04-27/28) produced almost all durable architecture. `[JT]` (joint) decisions cluster in GOOD; `[J]` autonomous calls cluster in BAD/NORMAL.
- **Sandcastle = redemption arc** from the 2026-04-20 worktree-isolation failure. Loop closed.
- **`caution_vs_overconfirmation_principle` (2026-04-28) is the highest-leverage principle of the window** — 3 surgical fixes in 24h.
- **Sycophancy paradox:** heavy personalization (SOUL.md + always_load) measurably *increases* sycophancy.

The audit's own corrections (post-verification, 2026-05-18) flag two findings as wrong: `decisions_belong_in_memory_not_gh_issue_bodies` memory does exist (false alarm), and `session-context.py` architecture-sweep auto-surfacing is **specification-only, not shipped** (CLAUDE.md describes it as live behavior). The second is itself a fresh instance of the class it was supposed to prevent.

I agree with the audit overall. Where I extend: the audit reads decisions; I read what you *did* between decisions. Two patterns visible in transcript that the audit can't see (sec. 3 below): **silent-slash-invocation rate (~67% of corrections)** and **scope-creep-via-"кстати"** (concrete instances 2026-04-24, 2026-04-28, 2026-05-07).

## Behavioral patterns observed (top 8)

### 1. Skill proliferation outruns the antipattern you wrote yourself

- **Evidence.** Live skill count via `ls ~/.claude/skills/`: 28 directories. Of these, 7 are `.bak.orphan` and one is doubly orphaned (`dnd.bak.orphan.bak.orphan.bak.orphan` — a quarantine that quarantined itself, audit BAD #28). v1.5 plan target was "6+1". Transcript 2026-05-08 (sess `1021cf9e`, 16:45 UTC): user explicitly recognizes the duplication and asks Jarvis to remove duplicate skills ("давай сначала дубликаты скиллов уберём"). Pattern: build skill → ship → realize it shouldn't have been a skill → orphan it → leave the carcass in place.
- **Frequency.** Dominant. /checkpoint, /end-quick, /tdd, /status, /intel, AI Hero 11-skill batch (PR #487 → #588) all walked back inside 30 days (audit BAD #19, #20, #22, #23, #24, #25).
- **Cost.** Token tax on every session-context load (lists every skill in `~/.claude/skills/` including orphans), invocation ambiguity (which `/grill*` does the model pick?), maintenance debt on the installer (recursion bug #659/#676).
- **Root cause hypothesis.** The local "fix-it" reflex (write a skill in 20 min) is faster than the protocol gate that should block it (which requires recall, decision-audit, /grill against `skill_proliferation_antipattern`). The friction differential favours the antipattern. Same shape as record_decision post-hoc — the right thing requires more work than the wrong thing.
- **Why your existing skills don't catch it.** `skill_proliferation_antipattern` is a memory, not a hook. /write-a-skill does not gate against it. /grill is not auto-triggered by "I'm about to create a skill". The contract is Tier-1 prompt-only; needs Tier-2 hook on `Write` to `~/.claude/skills/`.

### 2. `record_decision` post-hoc captures keep recurring

- **Evidence.** Audit BAD #13 names it as recurrent through all 90 days. Memory rule exists. Tier-2 hook (`record-decision-gate.py`) fires only when `record_decision` is *called* — not when a decision is *made*. The post-hoc marker (`actor=session:<id>:post-hoc`) is itself an admission of regression (#517 tracks adding a structured field).
- **Frequency.** Recurring across 90 days; named explicitly in iter:50 #669 (N=6 in one AFK chain).
- **Cost.** Decisions emitted without recall-at-time-of-deciding → `memories_used` empty or stale → outcome attribution breaks → /reflect can't classify failures as reasoning vs execution. The whole Tier-1 → Tier-2 → Tier-3 stack rests on this, and the leak is at the top.
- **Root cause hypothesis.** No mid-task self-trigger (ADR-0001 Type 3 explicitly "Not designed for"). So the user/model has to *remember* to record. Memory rule says "emit when…" but the conditions are subjective ("will outlive this session"). Subjective conditions get rationalized away under time pressure or smart-zone exhaustion.
- **Why your existing skills don't catch it.** /reflect detects it after the fact (post-hoc markers). The PreToolUse hook gates the *call*, not the *omission*. There is no Stop-hook scan for "decision-shaped exchanges where no decision was recorded". The taxonomy memory exists; the detector does not.

### 3. Subagent dispatch quality is non-deterministic per identical contract

- **Evidence.** Audit BAD #15 (#665+#662 wholesale 0-token fail), #50 (#691 hardcoded `C:\Users\<user>\...` in tests → 13 ERRORed on Linux CI), #51 (#690 worktree contamination + cross-pollination), #67 (#413 worktree hijack discarded uncommitted edits). Audit meta-finding #4: "Quality non-deterministic per identical contract (#689 vs #690+#691 same-day)." Audit Process #63 confirms /implement and /delegate subagents fabricate commit message content (PR #647, 16/16 tests "passed" — none in diff).
- **Frequency.** Recurring. ~4 partial/failure outcomes in 2 weeks of May alone.
- **Cost.** Force-push recoveries, dual-PR rework, subagent-fabrication detection now a first-class concern in C16. Burns user trust on the most-leveraged automation primitive you have.
- **Root cause hypothesis.** Subagents have no `Stop`-hook (only parent does). No `secret-scanner.py`-equivalent for "did the diff match the claim". The pre-dispatch gate (CONTEXT.md "Pre-dispatch gate") covers AFK readiness *before* dispatch but nothing verifies the *result* before the parent accepts the summary. CLAUDE.md "verify subagent work via `git diff`" is a prompt rule; you've described it twice in this repo and the same class still recurs.
- **Why your existing skills don't catch it.** /verify runs post-merge on outcomes, too late. /delegate's checklist is on the issue side, not the result side. There's no subagent-side equivalent of the principal's `pretooluse-recall-hook.py`. Audit meta-finding #4 says it explicitly: "Argues for orchestrator-side verification primitives, not subagent-side prompt-tightening."

### 4. Doc-vs-code drift in your own infrastructure

- **Evidence.** Audit Correction #2 (2026-05-18, post-verification): CLAUDE.md describes architecture-sweep auto-trigger as shipped; `session-context.py` (626 lines, grep `sweep|milestone|closed_at|capability|deepen` → 0 hits). The fix landed in PR #605 — but it *demoted the claim*, not added the code. Same pattern: schema-drift guard pointing at `supabase/schema.sql` while canonical was `mcp-memory/schema.sql` (audit Infra #46, silent-green for weeks). Same pattern: `outcome_record.memory_id` FK gotcha (#660, 5× recurrence). Same pattern: `dup-detector_on_same_name_upserts` iter:13 — 4 retries failed because invariant and behavior were mismatched.
- **Frequency.** Recurring class. Audit meta-finding #10: "Schema-asymmetry classes silently rot."
- **Cost.** Past instance cost a sprint of downstream work on the wrong assumption (Tool-width Z foundation, named in CLAUDE.md). Forward instances eat trust at exactly the moment you reach for the prompt rule.
- **Root cause hypothesis.** No automated truth-from-source check. CLAUDE.md and CONTEXT.md are written by hand, read by hand, and verified by hand. The meta-test rule for path-filtered guards (#326 → PR #365) is the single durable counter — it survives because the test runs in CI. Everything else is honor-system.
- **Why your existing skills don't catch it.** /grill challenges plans; it does not scan docs vs code. /diagnose investigates *current* bugs, not stale claims. /improve-codebase-architecture is structural, not factual. There is no skill or hook called "claim-vs-code-drift sweep."

### 5. Scope creep via "кстати" / context-stuffing mid-task

- **Evidence.** Transcript 2026-04-24 (sess `aeec732d`, 11:00 UTC): mid-task, "меняй, и кстати, server.py уже до 3000 строк разросся, думаю пора его распределять". Transcript 2026-04-28 (sess `2dc35a09`, 12:25 UTC): "перерисуй диаграммы я снова посмотрю и сравню с моим видением, кстати пока я буду смотреть ты тоже можешь с vision.md сравнить" — adds a parallel task while one is in flight. Transcript 2026-05-07 (sess `2c564be1`, 14:09 UTC): "Кстати, как работает post session hook? может end skill вообще не нужен и просто в хук всё перенести?" — opens a re-architecture mid-conversation on an unrelated topic. Audit BAD #5/#6/#7/#8 — abandoned PR cascade 2026-04-20 (#243/#244/#245/#255) — is the same shape: over-decomposition triggered by mid-flow rethinks.
- **Frequency.** Recurring. "кстати" appears 46 times in 2026-05-17_Petr transcript; multiple instances are mid-task scope additions.
- **Cost.** Smart-zone exhaustion; the unrelated task crowds out the current one. The original task gets a half-finished commit. The new task gets a half-thought-out plan. Compounds skill churn (because "и сделай скилл" is a common form of "кстати").
- **Root cause hypothesis.** When you're already in a session with full context loaded, opening a new browser tab to write the new question feels wasteful — you'd lose context. So it lands in the current session. The cost is real but hidden (degraded both outputs); the saving is felt (no new session). Same loss-aversion you flagged in SOUL.md for legacy code.
- **Why your existing skills don't catch it.** None of them try. The smart-zone discipline lives in SOUL.md as a principle, with no Stop-hook or UserPromptSubmit-hook checking topic-coherence within a session. There is no "you are now off-topic by N domain-tokens — open a new session?" gate.

### 6. Silent slash-invocation as the dominant interface

- **Evidence.** The /reflect 30-day report (`merge_2026-05-17/report_2026-05-17.md`) section 5.1: skill preludes (`Base directory for this skill: ...`) constitute ~60% of the `other` corrective bucket and a chunk of `permission_seeking`. The classification bucket is artefactual; the *behavioral signal* is: you trust slash-commands so much that you don't explain them. 855 hits for slash-command names in the 2026-05-17_Petr transcript across ~1585 user messages — i.e. >50% of user messages reference a skill name.
- **Frequency.** Dominant.
- **Cost.** Bug-amplifier when a skill is wrong: there's no nuanced prompt that gives the model a second chance. Either the skill is right or you re-issue the slash command. Couples skill quality 1:1 to Jarvis-quality for this user.
- **Root cause hypothesis.** It works. /grill and /implement reliably produce what you want. The dependency is real and load-bearing, not a problem.
- **Why your existing skills don't catch it.** No catching needed — but it *raises the stakes on every skill change*. A bad /grill ripples through 50+ sessions/month. This is why finding #1 (skill proliferation) matters more than the proliferation itself.

### 7. Always-on heavy session (10+ sessions/day, 16-hour daily span)

- **Evidence.** 306 unique sessions in the 2026-05-17_Petr 30-day window = ~10 sessions/day. Hour histogram (UTC, +5h Bishkek local): non-zero from 02:00 UTC (07:00 local) to 21:00 UTC (02:00 local), peak 11-15 UTC (16-20 local). Late-evening density (17:00-19:00 UTC = 22:00-00:00 local) = 305 user messages — 19% of all messages. After-midnight-local (20:00-21:00 UTC = 01:00-02:00 local) = 48 user messages, including 10 in the 02:00 local hour.
- **Frequency.** Recurring (visible on both 04-30 and 05-17 snapshots).
- **Cost.** This *is* the workload that puts you in dumb-zone consistently. SOUL.md "smart zone ~100K tokens" is a per-session ceiling; the 10-sessions/day rate strains the cross-session continuity layer (memory, working_state). Most BAD audit items cluster in the "execution choice / mechanical drift" `[J]` category — exactly what late-session and post-1am cognition produces.
- **Root cause hypothesis.** The system is good enough that you keep using it. The friction-to-start a session is low (working_state restores fast). There's no "you've spent 8h today, switch off" gate.
- **Why your existing skills don't catch it.** /end-quick saves state, doesn't enforce limits. /goals tracks priorities, not session count. No scheduled-task asks "you've worked 47h this week, want to drop the AFK loop?". The autonomous-loop is designed to *add* work, not bound it.

### 8. The personalization-sycophancy paradox you've identified but not mitigated

- **Evidence.** CONTEXT.md glossary explicitly defines "personalization-sycophancy paradox" (heavy user-modeling *increases* agreement bias). M#43 sycophancy eval harness shipped 2026-05-17 (PR #697). Mitigations shipped same day: /grill third-person framing (PR #695), /research 4-channel intake (PR #696), /grill CRITIC subagent (PR #698). But: the *measurement* now exists; the *baseline-vs-treatment delta is not tracked over time*. The harness ran once. Outcome `sycophancy_eval_baseline_2026_05_17` is the only data point.
- **Frequency.** Counter-finding (audit meta-finding #14). The entire identity layer (SOUL.md + always_load) is working *against* pushback.
- **Cost.** Every /grill that you yourself wrote falls under this paradox. CRITIC subagent helps; but the parent is still Claude-with-SOUL-loaded.
- **Root cause hypothesis.** The fix you shipped is a 24h burst of 4 mechanisms — but no scheduled re-eval. The harness will rot like every other untouched script.
- **Why your existing skills don't catch it.** /reflect can't, by construction — it's downstream of the bias. /verify checks outcomes against decisions, not against agreement-rate. There's no `mcp__scheduled-tasks__create_scheduled_task` registering "weekly sycophancy probe → compare to baseline → record_decision if drift > N%".

## What's actually working (don't break)

These items appear in the GOOD column of the audit AND survive transcript inspection — they're not just paper-correct, they're operationally load-bearing:

1. **SessionStart hook contract: inject data, not instructions** (PR commit `4487da8`). Killed the "model ignored 5 parallel recall" failure mode. The script is 626 lines, owns the entire session-start surface, and is the single point of intervention for any context experiment. Do not split this.
2. **Three-way doc split (CLAUDE.md / SOUL.md / CONTEXT.md, PR #492).** Only structural decision not reverted in the window. Each file has a clean owner-of-truth. Transcripts show the rules being cited by short name ("опираюсь на always_loaded_context_budget_pri..."). It survives the silent-slash-invocation interface.
3. **`record_decision` UUID-not-name rule + grill_me_record_decision_gate.** When it fires, it produces clean attribution chains (M#42 + M#43 cited as exemplary). The failure is in *triggering*, not in the contract.
4. **Sandcastle subsystem (slices 1-6, 10).** Coherent rollout, watchdog + RLS + multi-tier; the 2026-04-20 worktree-isolation failure mapped 1:1 to a six-slice fix. Architectural learning loop closed.
5. **Meta-test rule for path-filtered guards (#326 → PR #365).** Strongest infra durable improvement of the window. The schema-drift guard class (#289/#310/#311) does not recur silently any more. This pattern should generalize beyond guards.
6. **`caution_vs_overconfirmation_principle` (2026-04-28).** Highest-leverage principle of the window. Generated 3 surgical fixes (C16/M3/C2) in 24h, still cited as a lens.
7. **CONTEXT.md inline growth through /grill** with no batch friction. The glossary is dense and load-bearing; the personalization-sycophancy entry is the kind of evergreen knowledge that justifies the file.
8. **PR Body Check three-escape evolution** + `[no-issue]` commit-msg regex. Hotfix flow works, drive-by fixes have a legitimate path, and the gates fire.
9. **Sycophancy harness M43 + grill CRITIC subagent (shipped 2026-05-17).** First leading-indicator eval you have. Don't let it sit at one data point.

## Cross-reference to the 6 companion researches

- **`single-agent-workflows-2026-05-18.md`** — directly addresses pattern #1 (skill proliferation) and #4 (doc-vs-code drift). Its recommendation to steal Superpowers' "Iron Laws + red-flag rationalizations" enforcement pattern is the right shape for blocking skill-creation reflex. Linear Walkthrough pattern (extract code via `sed`/`grep`, never memory) directly attacks the "already done" claim-without-verification class.
- **`single-vs-multi-agent-architecture-2026-05-18.md`** — strongly confirms you are on the winning architecture. Does NOT address any observed pattern; argument is "don't pivot." Useful as a no-action confirmation.
- **`gh-workflows-solo-vs-team-2026-05-18.md`** — addresses pattern #3 (subagent dispatch). IssueOps (label-as-state-machine) + Rulesets + CODEOWNERS bypass is the layer that would make subagent output verifiable at the GitHub boundary, not just at the diff boundary. Stacked PRs would help with #5 (scope creep — forces decomposition).
- **`cc-vs-codex-vs-alternatives-2026-05-18.md`** — addresses pattern #8 (sycophancy paradox) and #4 (doc-vs-code drift) through Codex as a cross-model adversarial reviewer. Don't migrate; install Codex CLI as the second-opinion channel on plans, decisions, and CLAUDE.md/CONTEXT.md edits.
- **`cheap-models-cost-reduction-2026-05-18.md`** — least direct fit. Max subscription covers Claude Code; embeddings are the real cost. Doesn't move the needle on any observed pattern. **Skip on this round.**
- **`gap-discovery-patterns-2026-05-18.md`** — the spine of every recommendation below. Directly addresses patterns #2, #4, #7, #8: leading-indicator eval harness, cross-model adversarial reviewer, behavioral observability over transcripts, scheduled CLAUDE.md/skill-bloat audit, calibration drills on synthetic predictions. This is the doc to action first.

## What he should change (6 concrete actions, ranked by impact)

### 1. Install Codex CLI + a `/cross-critique` skill that ships plans/PRDs/decision drafts to it for a second opinion

- **Pattern killed/weakened:** #8 (personalization-sycophancy paradox), #4 (doc-vs-code drift on prose claims).
- **Implementation sketch.** `winget install OpenAI.Codex` (Windows experimental; WSL2 path also fine). One skill, `.claude-userlevel/skills/cross-critique/SKILL.md`. Takes a file path or memory UUID; shells out `codex exec --json --ask-for-approval never < critique-prompt.md`; parses JSON; appends "## Cross-model critique" section to the artifact OR posts as PR comment. Runs cold — no SOUL, no memory, no CONTEXT loaded.
- **Backed by:** `cc-vs-codex-vs-alternatives` (Codex Skills + AGENTS.md + `codex exec --json` headless), `gap-discovery` (recursion trap; same-family judges share biases at 60-69%).
- **Risk.** Codex/Anthropic terms; rate limit / cost on heavy use; second-opinion sycophancy from GPT-5.5 itself (mitigate by adversarial system prompt). Adds one external dependency on a Windows-experimental tool.

### 2. Add a Hamel-style L1 golden-set eval harness, scheduled weekly

- **Pattern killed/weakened:** #2 (record_decision post-hoc — gives leading indicator for when memory recall fails), #4 (doc-vs-code drift — assertions become evals), #8 (sycophancy — extends M#43 harness to other skills).
- **Implementation sketch.** Reuse the M#43 sycophancy harness scaffold (12 scenarios). Add 20-30 task assertions: "/grill on prompt X must produce ≥1 surfaced assumption matching Y"; "memory_recall on query Z must return UUID-set ⊇ {…}"; "record_decision called within last session containing keyword K must have memories_used ≥ 1". Run via scheduled-tasks weekly on Workshop; record `outcome_record(scope=evals, severity=...)`. Eval saturation = signal that scenarios are stale; rotate quarterly.
- **Backed by:** `gap-discovery` (Hamel 20-50 task minimum, Anthropic engineering's pass@k/pass^k, 100% pass = no signal). M#43 sycophancy_eval is the proof-of-concept.
- **Risk.** Maintenance burden if eval flakiness is high; need to keep the assertions calibrated against actual model drift.

### 3. PreToolUse hook on `Write|Edit` for `~/.claude/skills/**` and `.claude-userlevel/skills/**` that requires `--justified` flag + a memory UUID

- **Pattern killed/weakened:** #1 (skill proliferation outruns its own antipattern).
- **Implementation sketch.** New `scripts/skill-creation-gate.py` registered in `.claude-userlevel/settings.json` PreToolUse. Trigger: `Edit|Write` with file_path matching `**/skills/**/SKILL.md` AND tool not previously seen in this session for that path. Action: block unless prompt content contains a `record_decision` UUID grounded in `skill_proliferation_antipattern` recall. Hard fence, like the `memories_used` gate (#577).
- **Backed by:** `single-agent-workflows` (Superpowers Iron Laws + red-flag rationalizations pattern), `gap-discovery` (skill bloat causes invocation failures, Vercel 56% non-invocation with bloated skills).
- **Risk.** Friction during legitimate skill iteration. Tier-2 hooks have a 6× false-positive history (audit Infra #48). Pair with an explicit `JARVIS_SKILL_EDIT_OK=1` env-bypass for the existing per-skill PR flow.

### 4. Stop-hook scan: "Was a decision-shaped exchange emitted without `record_decision`?"

- **Pattern killed/weakened:** #2 (post-hoc captures).
- **Implementation sketch.** Reuse `scripts/comm-patterns-extract.py` Stop-hook plumbing. After transcript extraction, classify last N user-prompt + assistant-response pairs: "did the assistant resolve a between-named-alternatives question? did it commit to a directional choice?" Use a cheap classifier (DeepSeek V4-Flash via OpenRouter, ~$0.14/M input). If yes-and-no-record_decision-in-session: emit `outcome_record(scope=missing-decision, severity=medium)`. /reflect picks it up next pass.
- **Backed by:** `gap-discovery` (decision journal review pass; Farnam Street "the record alone has limited value — the comparison produces calibration"), audit BAD #13.
- **Risk.** Classifier flakiness adds noise. Run shadow-mode for 2 weeks before letting it record outcomes.

### 5. Subagent post-flight verifier (Stop equivalent for `Task` tool returns)

- **Pattern killed/weakened:** #3 (subagent dispatch quality non-deterministic).
- **Implementation sketch.** New `scripts/subagent-result-verifier.py` invoked from the parent session after `Task` returns. Inputs: subagent's reported file list + claimed test counts. Operations: `git diff --stat <subagent-base>..HEAD` parsed; assert reported files ⊆ actually-diffed files; assert claimed test counts grep-able in test files. If divergent: refuse to accept summary, emit `outcome_record(scope=subagent-fabrication, severity=high)`. Audit Process #76 lesson `subagent_fabrication_commit_message_vs_diff` becomes mechanical.
- **Backed by:** `single-vs-multi-agent-architecture` (orchestrator+subagent pattern needs verification primitives, not subagent-side prompt tightening), audit meta-finding #4 explicitly recommending this shape.
- **Risk.** Needs the Task tool's PostToolUse hook to actually fire and have file-system access. If Anthropic ships `SubagentStop` natively (changelog item Apr 22), use that primitive instead.

### 6. Weekly scheduled "claim-vs-code drift sweep" on CLAUDE.md, CONTEXT.md, SOUL.md

- **Pattern killed/weakened:** #4 (doc-vs-code drift in your own infrastructure).
- **Implementation sketch.** Scheduled task: extracts factual claims from those 3 files via DeepSeek V4-Flash ("describes a behavior at <path> producing <result>"), then runs a sanity grep / executes the smoke. On miss: open issue with label `meta:doc-drift`, attach diff, draft the demote-the-claim PR. Same shape as the M#38 watchdog tick that closed the orphan milestone (audit Process #86).
- **Backed by:** `gap-discovery` (Anthropic Auto Dream for explicit doc consolidation), `single-agent-workflows` (Linear Walkthrough — extract via shell, never memory). Audit Correction #2 is the immediate motivating instance.
- **Risk.** Classifier might flag aspirational language ("planned per #N") as drift. Honor `(planned)` / `(per #N)` markers; only flag claims that lack one.

## What he should NOT do

- **Don't migrate to Codex** (per `cc-vs-codex-vs-alternatives` and your existing investment). Hybrid is the answer; the recommendation above is the hybrid shape.
- **Don't pivot to peer multi-agent** (per `single-vs-multi-agent-architecture`). You are already on the winning architecture (orchestrator + isolated subagents). The remaining work is depth (verifier in #5 above), not pivot.
- **Don't route Claude Code traffic to DeepSeek to "save money"** (per `cheap-models-cost-reduction`, sec. "The Max-plan billing trap" + issue #39903). Max covers you. The $152-surprise-charge incident from one stray `ANTHROPIC_API_KEY` is exactly the failure mode you'd write a meta-test for after the fact. The DeepSeek role here is *cheap classifiers* for new tooling (#4 / #6 above), not as a CC backend.
- **Don't adopt Spec-Kit** (per `single-agent-workflows`, sec. on heavier frameworks). Your /to-prd + /to-issues + /implement chain is Reed's spec→plan→execute formalized as skills. Spec-Kit would fight it.
- **Don't add more skills before installing the leading-indicator harness (#2 above).** Your skill churn rate is your #1 pattern. Every new skill compounds the proliferation problem without an eval bound to measure whether it earns its keep.
- **Don't double-track in GitHub Projects v2 sprint planning** (per `gh-workflows-solo-vs-team`). Your milestone-as-capability hierarchy is already what the audit calls clean. Sprint-overlay would be exactly the noise pattern you killed with `milestone_hierarchy_v3`.
- **Don't add an "epic" grouping primitive** ever again (audit GOOD #38, decision `2a7ae10e`). The slip-back vector is high because external sources use "epic" everywhere.

## Open questions worth asking yourself before acting on this

These are NOT for me to answer; they're the decisions the recommendations above require you to make:

- **What is your weekly time budget for system work vs feature work?** Recommendations #2 (eval harness), #3 (skill gate), #5 (subagent verifier) are each a 1-2 day implementation. Doing all six is 1-2 weeks. Are you willing to pause feature work for that?
- **If Codex's cross-critique disagrees with Claude on a /grill output — what's the resolution protocol?** Without a written tiebreaker rule, this surfaces a fork you'll have to decide each time, which is the worst kind of friction.
- **What's your acceptable false-positive rate on the skill-creation gate?** Tier-2 hooks have a documented 6× FP history in your own audit. If you accept 2 FPs per legitimate skill creation — fine. If you'd disable the hook after the first FP — don't ship it.
- **Are you willing to stop using late-night sessions for irreversible work?** Pattern #7 is observed but not actionable without your own boundary. The autonomous-loop can absorb routine work overnight; consequential decisions don't have to.
- **Is the personalization layer (SOUL.md identity / always_load) worth the sycophancy cost it imposes?** You've measured the cost; the obvious alternative (suspend identity on consequential decisions, per CONTEXT.md "cross-context review") is partially shipped via grill CRITIC. Is "partially" enough?
- **What part of the autonomous-loop should bound itself?** It is designed to add work, never subtract. If you're already at 10 sessions/day, what threshold would cause it to *pause* rather than *trigger*?

## Methodology notes

I read the priority decision-audit-90d-2026-05-18.md in full, the SOUL.md and CONTEXT.md in full, and the 30-day cross-device behavioral merge report in full. I read the TL;DRs (first ~80 lines) of all 6 companion research docs. For the 26k-line JSONL corpus, I sampled via Grep keyword passes: corrective markers ("ты прав", "переделаю", "сначала", "снова"), confirmation/sycophancy markers ("отлично", "perfect", "exactly"), scope-creep markers ("кстати", "давай ещё", "пока мы тут"), "already done" / verification-skip markers, skill/slash invocation density, and time-of-day distribution via `awk` over `ts` fields.

I verified the live skill inventory by `ls ~/.claude/skills/` (28 dirs, 7 orphans) and the live hooks by reading `.claude-userlevel/settings.json`. I checked `scripts/session-context.py` line count (626) and confirmed the audit's Correction #2 finding by re-checking the recent commit history (PR #605 = docs-only demote, no code change).

**Limits of this analysis.** I did not read any single transcript end-to-end; sampling means I would have missed strong patterns that don't show up in keyword scans. The "кстати" scope-creep count is a lower bound — soft scope adds without a marker word are invisible to me. I did not sample the 04-30 Petr_2 / Petr_3 transcripts beyond keyword counts; they may contain patterns the 05-17 corpus doesn't. I did not verify any of the audit's BAD/GOOD claims independently — I treated the audit as ground truth except where it explicitly self-corrected. **Where I might be wrong:** the pattern severity rankings reflect my judgment over 2 hours of cross-reference; the user's own time-cost mapping may put them in a different order. The Codex-cross-critique recommendation (#1) assumes Codex's Windows-experimental status is workable for you — it may not be.
