# Deep dive: Ralph Wiggum loop + back-pressure (Huntley)

Date: 2026-05-06
Author: main session (subagent #5 was sandbox-blocked; doc written inline)
Companion: `docs/research/agent-dev-practices-sweep-2026-05-06.md` §2.6, §2.7

## Sourcing note

WebSearch from the main session worked. Primary sources read at search-snippet depth (no full scrapes — Firecrawl perms not yet propagated). Specific layer-counts and exact prompt-file shapes tagged `[unverified]` where they would otherwise depend on a full read of `ghuntley.com/loop/` or `ghuntley.com/pressure/`. The structural conclusions (when Ralph wins, when it doesn't, fit for Jarvis) hold regardless.

## 1. Ralph Wiggum loop — distilled

**Provenance.** Pattern coined by Geoffrey Huntley (Sourcegraph Amp engineer, ex-Canva). Went viral late 2025; "year of the Ralph Loop" framing emerged Q1 2026. Named after the Simpsons character — "clueless yet relentlessly persistent." Source: `ghuntley.com/ralph/`, `ghuntley.com/loop/`.

**Core shape.** A bash `while` loop that re-invokes a coding agent (Amp / Claude Code / Copilot CLI) on the same task until a completion criterion is met. Progress does **not** live in the LLM context — it lives in **files and git history**. Each iteration is a fresh agent instance with clean context that *reads* the prior diff.

**The clever bit (load-bearing).** "Progress doesn't persist in the LLM's context window — it lives in your files and git history." The loop is **stateless on the model side**, **stateful on the filesystem side**. This is what makes long-horizon work possible without context decay. Maps directly onto AI Hero P/E/C — every loop iteration *is* a Clear.

**One-thing-per-loop rule.** Huntley: "ask Ralph to do one thing per loop. Only one thing." Multi-task iterations drift; single-task iterations converge.

**Termination.** Either (a) a sentinel emitted by the agent (`<promise>COMPLETE</promise>` style), (b) a checklist file with all items checked, (c) max-iteration cap, or (d) external killswitch. The Ralph plugin for Claude Code uses `--completion-promise "COMPLETE" --max-iterations 50`.

**Existing implementations:**
- `snarktank/ralph` — original-pattern reference implementation
- `ghuntley/how-to-ralph-wiggum` — Huntley's tutorial repo
- `frankbria/ralph-claude-code` — Claude Code-specific with intelligent exit detection
- `ralph-wiggum.ai` — branded plugin/site

[unverified] Exact prompt-file structure — `paddo.dev/blog/ralph-wiggum-autonomous-loops/` and `humanlayer.dev/blog/brief-history-of-ralph` likely have it. Skipped here.

## 2. Back-pressure — distilled

**Provenance.** `ghuntley.com/pressure/` "Don't waste your back pressure" (2024). Independent commentary at `banay.me/dont-waste-your-backpressure/`.

**Core claim.** *"Projects that are able to setup structure around the agent itself, to provide it with automated feedback on quality and correctness, have been able to push them to work on longer horizon tasks."* Long-horizon agent work is bounded **not by model capability, but by the density of automated feedback** the agent receives between actions.

**What counts as back-pressure (composite — sweep §1.6 + Huntley + commentary):**
1. **Type checks** — fast, deterministic, contradicts wrong code in seconds
2. **Linters** — style + simple semantic errors
3. **Unit tests** — behaviour spec at function level
4. **Integration tests** — behaviour spec across seams
5. **Property-based tests** — generative, catches edge cases the agent would never write tests for
6. **Pre-commit hooks** — block obviously broken commits before they pollute history
7. **CI builds** — environment-clean reproduction
8. **Schema migrations / contract checks** — DB and API surface
9. **Runtime observability** — actual deployed-app feedback
10. **Change-data-capture / audit trails** — for when the agent touches state

[unverified] Huntley's specific enumeration count — "the 7 layers" framing in the brief was speculative. The list above is reconstructed from independent commentary and the AI Hero canon.

**The asymmetric load-bearing claim.** Without back-pressure, an agent in a Ralph loop **converges to plausible-but-wrong** ("agent suicide by context"). With back-pressure, the same agent converges to **correct** because every wrong path is rejected within seconds. **Quality of long-horizon agent output ≈ density × speed of automated feedback.**

**Engineering implication.** "An increasing part of engineering will involve designing and building back pressure in order to scale the rate at which contributions from agents can be accepted." Reading: the senior dev's leverage shifts from writing code to **designing the constraint surface around agent-written code**.

## 3. Comparison matrix

Axes: (T) typical task horizon, (O) observability of intermediate state, (R) reversibility, (C) cost shape, (F) failure mode.

| Pattern | T | O | R | C | F |
|---|---|---|---|---|---|
| **Ralph loop (autonomous)** | hours–days | high (every iter is a commit) | high (revert any commit) | $/iteration × N iterations; can blow up if loop doesn't terminate | infinite churn on bad acceptance criteria; can submit plausible-wrong code if back-pressure is shallow |
| **/delegate worktree subagent** | minutes–hours | low until return | high (worktree merge or discard) | one big chunk; bounded by single agent context budget | fabrication, scope-shrinkage, "out-of-scope" relabeling — sweep memory `subagent_acceptance_criteria_dodged_as_out_of_scope` |
| **/loop scheduled (CronCreate)** | days–weeks | medium (one tick per N hours) | high | spread over time; cheap per tick | drift / forgotten goal |
| **Human-in-loop /implement** | minutes–hour | very high (live transcript) | very high | most expensive per step in human-time | bottlenecked on owner attention |

**Key delta vs `/delegate`.** Subagent is a **single execution** with single context. Ralph is **N executions** stitched by filesystem. Ralph wins when: task exceeds single-context budget AND back-pressure can be made dense AND failure-of-iteration is cheap to catch. `/delegate` wins when: task fits in one context AND verification can be done by reading the diff.

## 4. Where Ralph would earn its keep in Jarvis

Concrete candidates, ranked:

### 4.1 Sprawling refactor with high test coverage — STRONG fit (4/5)
Example: "deepen all shallow modules in `mcp-memory/` per Ousterhout"; "rename all skill outputs to consistent schema"; "add provenance arg to every memory_store callsite repo-wide."
- Back-pressure available: pytest suite + ruff + meta-tests
- Single-context insufficient: 50+ files
- Iteration cheap: each file is its own commit, revertable
- Termination: checklist of files

### 4.2 Cross-language / cross-repo migration — STRONG fit (4/5)
Example: porting a Python memory-utility to TypeScript for a future redrobot front-end; migrating a deprecated MCP API across all servers.
- Huntley's flagship use-case (codebase porting, Amp blog)
- Back-pressure: builds + types in target language

### 4.3 Systematic dependency / API upgrade — MEDIUM fit (3/5)
Example: bump pydantic v1 → v2 across the codebase; migrate from one MCP SDK rev to another.
- Risk: silent semantic shifts that pass tests but change behaviour at runtime
- Needs property-based or integration tests, not just unit

### 4.4 "Write 30 similar tests" — STRONG fit (4/5)
Example: write integration test per skill in skill catalog.
- Back-pressure: pytest pass = test exists and runs
- Trivially parallelizable; low ambiguity

### 4.5 New-feature implementation — POOR fit (2/5)
Skip Ralph here — `/grill-me → /to-prd → /to-issues → /tdd` is the canonical chain. Ralph thrives on **mechanical** convergence; net-new features need design judgment that doesn't converge under loop-pressure.

### 4.6 Bug diagnosis — POOR fit (1/5)
Use `/diagnose`. Ralph would brute-force change code until tests pass — exactly the failure mode Beck described ("agent deletes tests so they pass").

## 5. Required scaffolding *before* turning Ralph loose on Jarvis

Hard prerequisites — not ship-blockers, but must exist:

| Layer | Status today | Needed for Ralph |
|---|---|---|
| Ruff + mypy/pyright on `mcp-memory/` and `scripts/` | partial | yes — fast feedback |
| pytest with reasonable coverage on touched modules | partial | yes |
| Pre-commit secret-scanner | yes (`scripts/secret-scanner.py`) | yes — Ralph commits a lot |
| Pre-commit `[no-issue]` regex (CLAUDE.md #329) | yes | yes — Ralph commit messages must comply |
| Branch protection on `main` | unclear | yes — Ralph never touches main |
| Iteration sandbox (worktree or feature branch) | yes via `/delegate` infra | yes — reuse |
| Termination criterion file (PRD checklist) | new | yes — Ralph's "done" gate |
| Max-iteration killswitch | new | yes — bound runaway cost |
| Per-iteration cost telemetry | partial | nice — catches infinite churn |

The blocker for the first Ralph pilot is **not capability** — it's **back-pressure density** on whatever subdir Ralph touches. Pick a directory with strong tests for the pilot.

## 6. Bootstrap proposal — first pilot

**Scope.** Ralph pilot for §4.4 ("write similar tests at scale"). Lowest blast radius, clearest convergence criterion.

**Concrete task.** Author one `tests/skills/test_<skill>_outcome.py` per skill in the catalog that asserts the skill exits cleanly on a no-op input and produces a parseable trace artifact.

**Loop.**
```bash
# pseudocode
while ! all_skills_have_tests; do
  claude code -p "Read evals/skill_test_checklist.md. Pick the FIRST unchecked skill. \
    Write tests/skills/test_<that-skill>_outcome.py per the template in tests/skills/_template.py. \
    Run pytest on the new test. If green: check off the skill in the checklist and commit. \
    If red: fix and re-run. Output 'NEXT' when done with this iteration."
done
```

**Back-pressure layers active for this pilot:**
1. pytest on the new test must pass
2. ruff on the new file must pass
3. The template enforces structure
4. Each iteration commits — catastrophic churn shows up immediately in `git log`
5. Max-iteration cap = (number of skills) × 1.5

**Termination.** Checklist file empty.

**Safety:**
- Ralph runs on `chore/ralph-pilot-skill-tests` branch only, never on `main`
- No DB writes (skill tests are no-ops on stub inputs)
- Owner watches `git log` periodically; can `git reset` to pre-Ralph if drift detected
- Stop hook with cost ceiling

## 7. Anti-patterns

- **Ralph drifts into churn** when acceptance criteria are vague ("make the code better") or when back-pressure is theatre (tests pass via tautology).
- **Back-pressure as theatre** — passing tests on the wrong thing. Mitigated by behaviour tests over implementation tests (sweep §1.9), not via more Ralph iterations.
- **Single-flag termination on a spec the agent can write itself.** Ralph emits `COMPLETE`; checking that the spec was actually met is a separate skill (this is where SDD §2.1 / `/spec-validate` becomes load-bearing — Ralph + SDD compose).
- **Touching `main` directly.** Always feature branch.
- **Optimizing Ralph's cost at the expense of back-pressure depth.** Cheaper fast loops with shallow checks lose to slower loops with deep checks every time.
- **Using Ralph for design decisions.** Ralph executes; humans decide. The grill-me checkbox (SOUL.md) still gates the pilot scope itself.

## 8. Open questions for owner

1. Pilot greenlight — is the §6 skill-tests task the right first Ralph? Alternatives: deepen-shallow-modules sweep, or `record_decision` provenance backfill across mcp-memory.
2. Who owns the killswitch when Ralph runs unattended overnight? (3-device problem — Ralph on Main PC at night, owner on laptop next morning.)
3. Should `/ralph-loop` become a Jarvis skill, or stay a one-off bash? Skill gives discoverability and Jarvis-aware safety hooks; bash is faster to ship.
4. SDD interaction — does Ralph consume `specs/<feature>/spec.md` (deep dive #2 §4) as PRD, or does Ralph need its own checklist format?
5. Telemetry — capture per-iteration cost + diff stats into Supabase (Pillar 4 outcomes), or filesystem-only?
6. When does a Ralph pilot graduate to a `/ralph` skill in the catalog vs stay a one-shot script?
7. Does this conflict with the existing `claude_dir_edits_need_manual_confirm` rule? Ralph likely touches `.claude/` skills if it's deepening the catalog.

## Summary (5 bullets)

- **Ralph = stateless agent + stateful filesystem in a `while` loop.** The pattern's load-bearing trick is offloading state to git, not the model. Maps cleanly onto AI Hero P/E/C — every iter is a Clear.
- **Back-pressure dictates Ralph's ceiling.** Long-horizon quality ≈ density × speed of automated feedback. Without it Ralph converges plausible-wrong; with it, correct.
- **Sweet spot for Jarvis: §4.1 (sprawling refactors) and §4.4 (mechanical N-of-similar). Poor fit: net-new features (§4.5) and bug diagnosis (§4.6) — those stay in `/grill-me`+`/tdd` and `/diagnose`.**
- **Pilot proposal: skill-test scaffolding under `chore/ralph-pilot-skill-tests`. Strong back-pressure (pytest+ruff+template), trivial convergence (checklist), zero blast radius (no main, no DB writes).**
- **Compose with SDD (deep dive #2): the Ralph completion criterion should consume an executable spec, not just a `<promise>COMPLETE</promise>` sentinel — that closes the "Ralph optimizes the wrong thing" failure mode.**

## Sources

- https://ghuntley.com/ralph/ — canonical Ralph essay
- https://ghuntley.com/loop/ — "everything is a ralph loop"
- https://ghuntley.com/pressure/ — back-pressure essay
- https://ghuntley.com/agent/ — "how to build a coding agent" workshop (300 LOC)
- https://github.com/ghuntley/how-to-ralph-wiggum
- https://github.com/snarktank/ralph
- https://github.com/frankbria/ralph-claude-code
- https://www.humanlayer.dev/blog/brief-history-of-ralph
- https://linearb.io/blog/ralph-loop-agentic-engineering-geoffrey-huntley
- https://banay.me/dont-waste-your-backpressure/
- https://dev.to/alexandergekov/2026-the-year-of-the-ralph-loop-agent-1gkj
- https://www.leanware.co/insights/ralph-wiggum-ai-coding
- https://paddo.dev/blog/ralph-wiggum-autonomous-loops/
