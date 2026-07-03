# CLAUDE.md — Jarvis

Three-way split:
- **`CLAUDE.md`** (this file) — *rules*: process, conventions, skill routing, what to do, what NOT to do.
- **`config/SOUL.md`** — *identity*: personality, behavior, judgment calibration. Loaded by SessionStart hook. Per-agent at multi-agent rollout (currently single).
- **`CONTEXT.md`** — *domain model*: glossary, invariants, architectural shape. Grows inline through `/grill`. Loaded by SessionStart hook.

## Who you work for

Solo developer, 3 devices, no team. You compensate for the missing team. Push back on bad ideas — user is intermediate but growing fast.

Budget: Claude Max subscription covers all Claude Code usage (including scheduled tasks). ~$20/month for externals (Supabase, VoyageAI) — be frugal with external API calls.

## Session start context (auto-loaded)

SessionStart hook (`.claude/settings.json` → `scripts/session-context.py`) injects: user profile (compact), always-load rules, working state (if inside project), active goals, memory catalog. **Already in your window — do NOT re-fetch with MCP tools.**

- Working_state checkpoint found → one-line offer to continue.
- Hook failed (no memory block) → fall back to `memory_recall` + `goal_list`.
- During the session: use `memory_recall(query=<topic>)` for topic-specific lookups. Baseline is pre-loaded.

## Project

**Jarvis** — single-principal AI agent for software work (per redesign L0; broader personal-life scope is 1.x backlog). Repo `your-username/jarvis`. Architecture in [`docs/design/jarvis-v2-redesign.md`](docs/design/jarvis-v2-redesign.md); active scope = open GitHub milestones (capability-shipping units, see `milestone_hierarchy_v3`); `docs/PROJECT_PLAN.md` is a pointer index.

Architecture: Claude Code native (skills, hooks, MCP, subagents) + Supabase memory + SOUL.md identity.

## Definition of Done

Before marking any task complete:
1. **Integration**: does it work in context? Backend → check frontend. API → check consumers. Config → check all 3 devices (different paths/usernames).
2. **Side effects**: what else uses what you changed?
3. **Memory**: non-obvious learning or improvement idea → `memory_store` (with `source_provenance`).
4. **Tooling**: manual step that should be automated → propose or record.
5. **Tests**: end-to-end, not just in isolation.

## Engineering posture

Non-negotiable for every decision in this repo. Not in memory — these are how work happens here, not "things to recall sometimes".

- **Recall before action.** Before any non-trivial action (delegate, implement, research, save) — `memory_recall` on the topic. SessionStart context is the baseline; topic-specific lookups are required, not optional. Skill name must appear in the query so skill-contract memories surface (`memory_recall` is keyword-sensitive). If brief-mode recall surfaces a memory on-topic, `memory_get` it before building defaults from your own head.
- **Verify before assuming implemented.** Never say "this is already done" without `grep` for the actual symbol, reading the code path end-to-end, and where feasible a test that would fail if the feature were missing. Tool-width Z was missing for a month because everyone assumed otherwise — one bad foundation invalidated a month of downstream work.
- **No state in static storage.** State (% done, ✅/❌ markers, "shipped in PR #X", sprint dates, "last audit YYYY-MM-DD") belongs in GitHub Issues/Projects/PRs/commit history — NOT in markdown files, NOT in memory. Static storage may hold: evergreen lessons, decisions+rationale, reference info (API shapes, config locations), target architecture, pointers ("see #633 for current status"). If a field would be wrong in 2 weeks → GH, not here.
- **Sibling-grep on fixes.** When a reviewer flags a bug in one helper/pattern, grep for sibling patterns across the whole file AND related files before declaring the fix done. A second-round review with the same class of finding = the first fix was partial. 30 seconds of grep beats a full CI cycle of rework.
- **Skills are a contract, not a trigger.** `/implement`, `/grill`, `/end`, `/delegate` are owed when the action matches the contract — not only when the owner types the magic word. After PR merge: explicit `/implement` for next slice or `/end`, not silent continuation. Repo not having local skill files is not an exemption — skills are global at `~/.claude/skills/`.

## Project-specific rules

- **Native-first priority, not a ban.** Before writing a custom script/service (any language), check skills/MCP/hooks/subagents first; if native covers it cleanly, use native. Custom code is permitted on merit — when the native solution is awkward or incomplete. (Relaxed 2026-05-20 from the prior near-prohibition; decision `d9be0390`.) Existing justified custom code: `mcp-memory/server.py`, `src/risk_radar.py`.
- **Check native capabilities first**: Telegram → Channels; scheduling → `/loop` or scheduled tasks; background → desktop agents.
- **Cross-project impact**: `mcp-memory/server.py`, `.mcp.json`, and Supabase schema are shared with redrobot. Changes here can break redrobot.
- **MCP config portability**: `.mcp.json` must work on all 3 devices. No hardcoded usernames/absolute paths/device-specific values. Use relative paths or env vars.

## Related projects

| Project | Repo | Description |
|---|---|---|
| redrobot | `your-username/your-second-repo` | Industrial robot control — Python + FastAPI + React/Three.js + MuJoCo |

## Delegation

**Model selection**: complex reasoning / architecture / multi-file → stronger model. Simple edits / searches → lighter. User uses Opus for redrobot — match when delegating redrobot tasks.

**Subagents deliver end-to-end**: backend task → verify frontend or flag what's missing; feature → tests + error handling; config → check all 3 devices; can't complete → document what's left. Don't return "done" if feature only works in isolation.

**Verification (non-negotiable)**: after any agent completes, run `git diff` in the agent's working directory. Never trust agent self-reports on files edited — agents hallucinate when files don't exist. Agent reports N edited + diff shows 0 → work was fabricated.

## Memory

- **Supabase** = cross-device, source of truth. `memory_store` / `memory_recall` via MCP (local); `execute_sql` via Supabase connector (cloud tasks — `.mcp.json` not loaded there).
- **File-based** (`~/.claude/projects/…/memory/`) = device-local, does NOT sync. Not for important things.
- **Save immediately** after: decision, preference, architectural discussion, new fact, rejected approach (with why), working-style observation. Don't batch.
- **Every `memory_store` MUST include `source_provenance`** (namespaced: `skill:<name>`, `session:<YYYY-MM-DD>`, `hook:<name>`, `user:explicit`, `episode:<id>`, or URL/`external:<system>`). Server rejects writes without it.
- **Working state**: save to `working_state_jarvis` at natural breakpoints; `memory_delete` when done. After context compression → `memory_recall(query="working state")` first, then targeted file reads.

## Skill routing

Use skills — don't reinvent with raw tools.

| Trigger | Skill |
|---|---|
| "реализуй #42" — implement single issue inline | `/implement` (TDD-mode auto-engages via SOUL.md grill-me checkbox + working_state UUIDs) |
| "делегируй #X #Y" — dispatch multiple issues to parallel subagents | `/delegate` (TDD-mode auto-engages via SOUL.md grill-me checkbox + working_state UUIDs) |
| "проверь результаты" / scheduled post-delegation | `/verify` |
| "что я делаю не так", "проанализируй сессии", "паттерны общения", weekly behavioral audit | `/reflect` (cross-session comms audit; old outcome-verification scope migrated to `/verify` + `/self-improve` per #510) |
| "исследуй", "research", "сравни" | `/research` |
| "улучши себя", self-improvement | `/self-improve` |
| "цели", "приоритеты" | `/goals` |
| New device bootstrap, "scheduled tasks setup" | `/setup-tasks` (Workshop-only as of 2026-05-26 per `1b7ff8d1`; refuses on other devices, offers `--cleanup` for legacy entries) |
| ~~Daily scheduled tick~~ | ~~`/autonomous-loop`~~ — **SUPERSEDED**, pre-M44 catch-up baseline only. **Do not invoke for new flows.** No live cron registration since Medium-scope cleanup 2026-05-26. Will be deleted when reactive-core M44 (`wake_driver` + `orchestrator`) ships. |
| "end" / "end quick" | `/end` (full) / `/end --quick` (fast) |
| Vague intuition (no written plan yet) / "у меня ощущение что", "может быть лучше но не знаю как", "обсудим концепт"; subsumes /research for in-debate factual grounding | `/reason` |
| Stress-test plan / "grill me" / before non-trivial implementation | `/grill` |
| Conversation context → PRD on issue tracker | `/to-prd` |
| Plan / PRD → vertical-slice issues | `/to-issues` |
| "diagnose this", bug repro, perf regression | `/diagnose` |
| PR rework needed / negative review received | `/rework` (skips grill-me checkbox; reactive TDD per CRITICAL finding, MAJOR fixes, loop-stop guard policy) |
| "improve architecture", find shallow modules, refactoring opportunities | `/improve-codebase-architecture` |
| "почисти память", "memory hygiene", /curate, after 2+ recall complaints | `/curate` (owner-invoked weekly; M45 S3 — host-only, deterministic surfacer, per-row owner confirm) |
| "review memories", "drain queue", "проверь кандидаты", weekly memory-review, volume-event fire, /learn --status | `/learn` (M42 always-gate review surface; drains classifier + Deriver/Dreamer queues, hard cap 20, idempotent, no defer/accept_all) |
| "last sprint report", "what did we ship", "milestone closeout", pre-sweep brief | `/last-work-report` (skeleton — #606) |
| `статус` / `status` / `статус <repo>` — **anchored, only these exact triggers** | `/status` (deterministic L3 render over `status_digest`, 0 LLM tokens by default; `--deep` adds full picture + LLM narration; milestone #53, #1018) |
| "zoom out", unfamiliar code area, need higher-level map | `/zoom-out` |
| Issue triage / state machine / "ready for agent" | `/triage` |
| Author/edit a skill | `/write-a-skill` |
| "be brief", "caveman", token compression | `/caveman` |

Rules:
- GitHub issue work → /implement or /delegate, no exceptions. Raw Agent loses PR structure and verification.
- Multiple tasks → /delegate, but **Jarvis decides** what's subagent-suitable vs inline (context-heavy / cross-cutting / safety-critical stay inline). User trusts this call.
- **Grill trigger checkbox is mandatory** — every `/implement` and `/delegate` invocation runs the SOUL.md checkbox at start. ≥1 yes ⇒ `/grill` first, no exceptions on "small task" basis. Output goes to AC + CONTEXT.md + memory.
- **`/reason` (optional, intuition-stage) → `/grill` → `/to-prd` → `/to-issues` → `/implement` (or `/delegate`)** is the canonical chain for new features. TDD-mode engages inside `/implement` and `/delegate` per the SOUL.md grill-me checkbox — there is no standalone `/tdd` skill. Each phase in a fresh session if context is heavy. Skip `/reason` when you already have a plan to validate ("оркестратор можно лучше — не знаю как" → start with `/reason`; "вот план X, проверь" → skip to `/grill`).
- If unsure → use the skill. Overhead near zero, cost of skipping is lost structure.
- **`/status` is anchored routing — bare/unrelated uses of the word do NOT fire it.** Only the exact triggers `статус`, `status`, or `статус <repo>` (the word as a standalone command, optionally naming a tracked repo) route to `/status`. A sentence that merely contains the word — "какой статус у PR #123", "статус деплоя в логах", "status code 500", a quoted error string — is a normal request, answered in-context; it must NOT be treated as a command to run a repo-state investigation. This anchoring closes the original failure mode where a bare "статус" was over-eagerly read as "go investigate everything".

### Responsibility split — interactive · `/delegate` · reactive-core orchestrator

Three places work can land. Pick by **who's present** and **how the work was triggered**, not by what the work is.

- **Interactive `/implement` (you, in this session).** Owner is present. One issue, judgment-heavy, working through together. Full SOUL loaded. SOUL.md grill-checkbox is the in-skill backstop for AFK-readiness.
- **`/delegate` (you, dispatching subagents).** Owner is present and chose to fan out. Multiple AFK-eligible issues → parallel sandcastle subagents. The pre-dispatch gate (CONTEXT.md → *Pre-dispatch gate*) refuses dispatch if the `sandcastle` label, AC, decision-UUID, or a `needs-*` blocker is missing. `/delegate` is **not** the orchestrator — it's operator-driven parallel dispatch.
- **Reactive-core orchestrator (M44, no operator).** Events arrive (`ci_failure`, `review_negative`, `quota_pressure`, etc.) → `wake_driver` `LISTEN/NOTIFY` cold-boots the orchestrator → it triages **one** event with **three dispositions**: (1) one-shot inline tool call, (2) emit a `task_queue` row → `executor.spawn(task)` runs `claude -p` in sandcastle, (3) enqueue a cloud decision-task. After spawn, the loop is closed by **external GitHub workflows** (Path A — open → CI → review → automerge → rework → escalate, results re-enter as fresh events), not by internal code.

Boundaries:

- **Orchestrator-emitted TASK rows carry the same AFK-fit/sandcastle semantics** as manually-triaged ones — `/to-issues`'s checklist applies regardless of who emits the task. An AFK-unsafe TASK row gets routed for owner attention (no auto-spawn), same as the `status:owner-queue` landing zone for `/delegate` refuses.
- **The orchestrator is a router, not the principal.** It runs routing-policy only (no full SOUL load). Full SOUL is for interactive `/implement` and for the `claude -p` subagents the executor spawns — both are the principal in their lane (CONTEXT.md → *SOUL is shared across interactive + autonomous lanes*).
- **`/autonomous-loop` (SUPERSEDED 2026-05-26)** — cron entry removed from `setup-tasks` bootstrap; existing cron jobs on each device must be unregistered manually. The skill file is retained as an opt-in pre-M44 catch-up baseline ONLY. **Do not invoke for new flows; do not reference it in routing as if alive.** New AFK paths route through events + reactive-core orchestrator (M44). When M44 ships, the skill itself is deleted.

## Autonomous work

User often leaves Jarvis to work alone. Core loop comes from §Engineering posture above (recall before action, verify before assuming implemented) + SOUL §Personality (quality over speed) + SOUL §Goal awareness. "Aligned plans = standing orders" — when a multi-step plan was discussed and signed off, the alignment IS the approval; don't re-confirm at each checkpoint.

Project-specific addition — **transform tasks into verifiable goals**: "Fix bug" → write failing test → make it pass. "Add validation" → tests for invalid inputs → make them pass. "Refactor X" → tests pass before and after.

## Development process

- Branches from `main`. **PRs are for code, not for discussions.**
  - Code change → one issue, one PR; body includes `Closes #NNN`. Drive-by fixes without parent → create post-factum issue-bucket (see #183).
  - **Consolidation / batch PR** (bundles or supersedes the work of multiple issues — e.g. a stacked refactor that absorbs sibling branches, or a PR whose siblings get closed as "shipped via #X") → the body MUST carry a `Closes #N` line for **every** absorbed issue (here "absorbed" = its entire scope ships in this PR, not merely referenced), not just the umbrella or the one whose branch you happened to be on. Auto-close (native *and* `pr-merged.yml`) fires only from the PR's own `closingIssuesReferences`, built solely from this PR body's closing keywords (`Closes/Fixes/Resolves #N` and their `close/closed/fix/fixed/resolve/resolved` variants) — an absorbed issue you don't list gets neither close path and silently stays open with stale `sandcastle`/`in-progress` labels (this is #948 Mode 2: PR #900 shipped #845/#846/#847/#859/#860 but listed only `Closes #851`, leaving 5 open). Cross-repo: `Closes owner/other#N` is **not** auto-closed by `pr-merged.yml` (its `GITHUB_TOKEN` is repo-scoped — it skips+warns); close a foreign absorbed issue manually. When you close a sibling PR as "superseded by #X", carry over **that sibling's own** issue-closing keywords into #X's body — e.g. if the sibling listed `Closes #845, Closes #846`, add those exact lines (NOT `Closes #<siblingPR-number>`, which resolves to the wrong issue). Caveat: `closingIssuesReferences` is frozen at merge time — if **#X is already merged**, editing its body is documentation-only and fires no close; close the absorbed issues directly with `gh issue close #N --reason completed`. Never rely on a prose "shipped via #X" note — it is invisible to the linkage.
  - Hotfix → label `priority:critical` (PR Body Check honors the label per #424; no linked issue required); commit-msg uses `[no-issue]` when there's no parent issue (per `.pre-commit-config.yaml` regex from #329).
  - Refactor → PR title with conventional-commit prefix `refactor:` / `refactor(scope):` auto-bypasses the PR Body Check linked-issue requirement (structural cleanup rarely has a parent issue; `[no-issue]` marker still works but isn't required).
  - Design RFC / proposal / debate → **GitHub Discussions, not an issue and not a PR.** Approval = thread resolution by the task initiator (user if user-started; orchestrator/PM if agent-started). Stable post-decision artifacts may land in `docs/design/` via direct commit; no PR ceremony.
  - Final decisions go to memory (`record_decision` / `memory_store`) — that is the queryable source of truth, not a markdown file.
- Check the Claude code-review comment before merging (forked `code-review` plugin via `code-review.yml`). Copilot is no longer used — the plan lapsed and Claude review supersedes it (decision 2026-05-22). Still check ALL reviewers, not just one (the Claude bot posts as an issue-comment, not a PR review).

### Architecture sweep at milestone close

After a milestone closes (capability shipped), run `/improve-codebase-architecture` in a **fresh session** (not the one that closed the milestone — that's already in dumb zone). The skill:

1. Reads `CONTEXT.md` + ADRs + repo state.
2. Surfaces numbered list of *deepening opportunities* (shallow → deep modules, friction points, untested seams).
3. Grills you on selected candidates → architectural decisions → child issues attached to a follow-up milestone (or as standalone slices).

**Trigger mechanism (planned — see #605):** intent is for `scripts/session-context.py` to surface "Milestone N closed — architecture sweep recommended" in SessionStart context when a milestone closed with **≥3 closed slices** AND no sweep has run since `closed_at`. Until #605 lands, the trigger is **manual** — run `/improve-codebase-architecture` in a fresh session after a capability-shipping milestone closes. Small milestones (1–2 slices) skip the sweep.

**Cadence:** semantic, not temporal. The sweep follows capability shipping, never a date.

**Output discipline:** 1–2 actionable refactors → child issues attached to a follow-up milestone via grill chain. Rest goes to `.out-of-scope/<topic>.md` with reason. Don't try to action everything.

### Fix > track for trivial reversible (#428)

Trivial, reversible, scope-obvious change (<30 min, own repo): **fix inline**. Don't open a tracking issue you'll close in 5 minutes — that's paperwork. Issues are for things you can't finish now, want to discuss, or that will outlive this session.

- **Fix inline**: stale doc fragment (broken link, version mismatch); missing test for newly-touched code; typo/comment cleanup adjacent to other work; config drift between two files; lint warning on a file you just touched.
- **Open issue**: architectural reshape >1h; cross-cutting refactor needing coordination; behavior change user should weigh in on; anything touching another active area mid-flight; foreign-owner repo where Jarvis can't merge.

The `Fix > track` rule does **not** override the rest of the development process — fixes still go through PR review, with `[no-issue]` in commit message when there's no parent issue (per `.pre-commit-config.yaml` regex from #329).

### Path-filtered CI guards require a meta-test (#326)

Any workflow under `.github/workflows/` with a `paths:` filter that blocks PRs must ship with a co-located fixture test in `tests/ci/test_<name>_guard.py`. Convention: `.github/workflows/X-guard.yml` ⇒ `tests/ci/test_X_guard.py`.

The test covers two dimensions:
- **Config** — assert the workflow's `paths:` filter references the canonical file(s). If the canonical path changes, red CI forces the workflow to move with it. This is the exact class of bug that produced #289/#310/#311 (guard watched `supabase/schema.sql`, canonical was `mcp-memory/schema.sql` — guard silently passed for a sprint).
- **Logic** — reimplement the guard's decision rule in Python, assert it blocks/allows the scenarios it claims to. `schema-drift-check` is the proof-of-concept; new path-filtered guards follow the same pattern.

The meta-test suite runs via `.github/workflows/ci-meta.yml` on every PR (not itself path-filtered — that would be self-undermining).

### Milestone vs pillar hygiene

Authoritative model lives in memory: `milestone_hierarchy_v3` (always_load). Summary:

```
pillar (narrative only) → goal (Type A) → milestone (capability + PRD) → slice (one PR)
```

- **Pillars** live in memory, never close — multi-milestone capability areas. Don't treat a pillar as done after one milestone closes (memory: `pillar_is_not_one_task`).
- **Milestones** group ≥2 capability-coherent slices. Description carries the PRD. Close on capability shipping — **no date in title**, no time-boxing. 0 open issues + state=open is a bug.
- **Slices** = one PR each. A single independent slice (no inter-deps) ships **without** a milestone. No ceremony for one-offs.
- **No numerical WIP limit.** Self-throttle by HITL/grill/review attention load. AFK milestones (running through subagents/sandcastle) cost ~0 attention — opening another unrelated milestone is fine when prior ones are AFK.

Mechanics:
1. New work needing grouping → create milestone *first*, write description (PRD), then attach slices. `/to-prd` writes to milestone description.
2. Capability shipped → close milestone in the same action as closing the last slice.
3. Retroactive — if related slices shipped without a milestone, create it, attach the issues+PRs, close it. History must be recoverable.
4. When user rushes and skips the milestone for grouped work — catch it: "milestone for these N slices?" before creating issues. Don't be a silent executor.

Term **"epic"** is **not used** — milestone is the only grouping primitive (decision: `2a7ae10e-afc3-4523-b0bc-c4b90ddbe1a5`).

## Token economy

- Don't pay LLM tokens to run shell commands — fetch first, send only data.
- Prefer editing existing files over creating new ones.
- Use lighter models for mechanical tasks.

## Key files

| What | Where |
|---|---|
| Personality | `config/SOUL.md` |
| Device config | `config/device.json` |
| MCP config | `.mcp.json` |
| Memory server | `mcp-memory/server.py` |
| Session context loader | `scripts/session-context.py` |
| Memory recall hook | `scripts/memory-recall-hook.py` |
| Process rules | `.github/copilot-instructions.md` |

If this file needs a change — propose it and explain why.
