# Context management — research draft

**Status:** DRAFT. Not ground truth. Per `docs_research_unfinished` — do not quote as authority, do not auto-commit downstream artifacts from it.
**Date:** 2026-07-27
**Scope:** the whole context-management sphere for Claude Code as it applies to this stack — loading, compaction, custom compaction instructions, recurring injection, and anti-regrowth.
**Method:** six research blocks (official canon, compaction mechanics, loading mechanics, practitioner patterns, adversarial data, field reports) plus a measured audit of this machine. Raw block outputs are in the session scratchpad; every number below is tagged MEASURED (taken off this machine), VERIFIED (quoted from official docs), or INFERRED.

---

## 0. Executive summary

The context problem here is **not** a budget problem. It is three bugs wearing a budget problem as a costume.

1. **The memory hook does not reach the model.** Hook stdout is capped at 10,000 characters (VERIFIED). `scripts/session-context.py` emits 22,390 chars on a clean start and 29,748–41,504 chars on real compact-path runs (MEASURED — the largest of them produced *during* this write-up). Between 55% and 76% is discarded. On the compact path in a long session the loss is **total**: user profile, always-load rules, glossary, working state, goals and memory catalog all arrive at 0%.
2. **SOUL.md is 889 characters from silently losing its own safety sections** (MEASURED: 9,111 / 10,000, 91.1%). It truncates from the tail, and the tail is `## External content safety` — the prompt-injection defence.
3. **Two recall hooks re-inject ~16,250 tokens per 50-turn session** (MEASURED fire rates, INFERRED sizes) with no cross-turn dedup and no dedup against each other or against session start.

Only after those three are fixed does file size become the binding constraint. And when it does, the honest finding from the field is that **cutting CLAUDE.md for adherence is a weak lever** — a confirmed, size-independent mechanism explains most "CLAUDE.md gets ignored" reports better than length does (§B.7).

**A fourth item is a settings finding, not a bug:** auto-compaction is configured to fire at 150,000 tokens (MEASURED, §A.3), i.e. 50,000 tokens *past* the ~100k smart zone this stack claims to respect. One environment variable, no code.

**Structural decisions taken** (owner, this session + the grill disposition round of 2026-07-28, 34/34 items dispositioned):

- **The Memory Catalog is deleted** (§B.9, decision `3d61f0b9`). Measured: 49 of the 50 rows it renders are types the recall hook already fetches on demand.
- **Pre-Compact Recovery is split, not deleted** (§B.10); the push is replaced by a **pointer + deterministic minimum** (`59f06783`), UUID recovery moves to a Postgres query keyed by a newly-stamped harness session id.
- **The milestone stands on two columns** (`7fe96e01`): content rewrite of the always-loaded files is a *precondition* (Invariants compression, Glossary categorization, dedup, roster diet); budget-aware assembly is the safety net, not the fix.
- **The threshold experiment (75 → 55) runs LAST**, isolated, after every other fix (`996f4da1`, amending `0dddc143`).
- **`# Compact instructions` is deferred wholesale** to a low-priority issue (`93fee0f9`) — see Part D.

---

## PART A — Map of mechanisms

### A.1 What loads, and when

| Mechanism | Timing | Notes |
|---|---|---|
| System prompt + output style | always | not part of message history |
| Project-root `CLAUDE.md`, user-level `CLAUDE.md` | always, eager | |
| `@path` imports inside CLAUDE.md | **always, eager** — *if the import line is bare* | VERIFIED, docs verbatim: splitting into imports "doesn't reduce context, since imported files load at launch". Max 4 hops. **Caveat, MEASURED 2026-08-07 (A.7):** bare line-start imports expand; the two mid-prose imports we ship (`@SOUL.md,` / `@DOCTRINE.md,` in user-level `CLAUDE.md`) do **not**. Write imports bare. |
| `.claude/rules/*.md` with `paths:` glob frontmatter | **lazy** — only when a matching file is touched | the only genuine lazy-loading lever for rules |
| Nested `CLAUDE.md` below cwd | lazy — on descent | |
| Skill descriptions (roster) | always | shared budget ≈1% of window; per-entry cap 1,536 chars; least-invoked dropped on overflow |
| Skill bodies (`SKILL.md`) | lazy — on invocation | |
| MCP tool schemas | lazy — `ENABLE_TOOL_SEARCH` default ON, ~120 tokens total | |
| Hook stdout | per hook event | **capped at 10,000 characters**; overflow spilled to file, replaced by ~2KB preview |

**Killed idea:** splitting the big `CLAUDE.md` into `@imports` buys exactly zero. This was the cheapest-looking fix and it does not work.

**The one real lever** for deferral is `.claude/rules/*.md` with `paths:` frontmatter — with a caveat in A.2.

### A.2 What survives compaction

| Artifact | Fate |
|---|---|
| System prompt, output style | unchanged |
| Project-root CLAUDE.md + unscoped rules | **re-injected from disk** |
| Auto memory (`MEMORY.md`) | re-injected |
| `paths:`-scoped rules, nested CLAUDE.md | **LOST** until a matching file is re-read |
| Invoked skill bodies | re-injected, capped 5,000 tok/skill and 25,000 total; **truncates from the file START**, so long preambles survive and tails do not |
| Skill descriptions index | **NOT re-injected** (`noSurviveCompact: true`) |

Three consequences that shape every decision below:

- **A big root CLAUDE.md is recurring rent, not a one-time cost.** It is paid again after every compaction.
- **Deferral trades start-up budget for post-compaction amnesia.** `paths:`-scoped rules are cheap at start and gone after a compact.
- **Skill routing silently degrades after a compact** while CLAUDE.md rules persist. Therefore the routing table in `CLAUDE.md` is **not redundant with the skill roster** — it is the only routing surface that survives. Canon does not resolve this tension; we resolve it in favour of keeping the table.

### A.3 Compaction triggering

- Two-stage: clears older tool outputs first, then summarizes if still needed (VERIFIED).
- Actual default threshold, from deobfuscated source posted in `anthropics/claude-code#31806`: `defaultThreshold = effectiveWindow - 13000` — a hardcoded 13,000-token buffer, **≈83.5%** of a 200k window.
- `CLAUDE_AUTOCOMPACT_PCT_OVERRIDE` is clamped `Math.min(userThreshold, defaultThreshold)` — **it can only lower the threshold, never raise it** (VERIFIED in docs, confirmed by the source-level report).
- `CLAUDE_CODE_AUTO_COMPACT_WINDOW` sets the window used for the math. Setting it **decouples the compaction threshold from the statusline's `used_percentage`**, which always uses the model's full window.
- `autoCompactEnabled: false` disables.

**The threshold on this machine — MEASURED, and it is set deliberately.** Both variables are present in the environment (an earlier draft of this file checked only the two `settings.json` files and wrongly concluded no override existed — wrong layer):

```
CLAUDE_AUTOCOMPACT_PCT_OVERRIDE = 75
CLAUDE_CODE_AUTO_COMPACT_WINDOW = 200000
```

→ `min(0.75 × 200,000, 200,000 − 13,000) = min(150,000, 187,000)` = **compaction fires at 150,000 tokens.** The override is below the default, so it wins; the remembered "70%" was 75%.

**The consequence matters more than the number.** The smart zone is ~100k. Compaction rescues at 150k. **Every session therefore runs 50,000 tokens inside the degradation zone before the mechanism engages at all.** If the smart-zone premise in SOUL.md is taken seriously, the override belongs at ~50–55% (100–110k), not 75%. That is a one-variable change with no code touched, fully orthogonal to every other fix here — which makes it the correct *first* experiment (see C.0).

**Folklore audit** (routes: HN Algolia verbatim re-fetch, GitHub issues, named engineering blogs; Reddit blocked):

| Claim | Verdict |
|---|---|
| auto-compact at 60% | FOLKLORE — no primary source; traces to one user's personal override setting |
| ~45k tokens reserved | FOLKLORE, contradicted — real figure is **13,000**; "45k" appears only in an unbylined blogspam page that admits the number is unverified |
| CLAUDE.md under 150 lines | FOLKLORE as a hard rule — closest source is a reporter's own untested suggestion inside a bug report about a *400*-line file |
| smart zone ~100k tokens | **NOT folklore** — Dex Horthy (HumanLayer), based on conversations with ~100 practitioners, independently echoed. But it is consensus-of-anecdote, not a measured benchmark. Worth keeping in SOUL.md, worth labelling honestly. |

### A.4 Custom compaction instructions

**Answer to the long-deferred question: yes, persistent custom compaction instructions exist.**

- One-off: `/compact <instructions>`.
- Persistent: a section in **project-root `CLAUDE.md`**. Doc-verbatim example:

  ```
  # Compact instructions

  When you are using compact, please focus on test output and code changes
  ```

- The header is **free-form, not a magic string** — the compactor matches on intent (VERIFIED).
- **No settings key, no env var, and no hook can inject summarizer instructions** (VERIFIED by absence; the hooks doc explicitly states "No context injection" for `PreCompact`).
- It must live in project-root CLAUDE.md, not a nested or `paths:`-scoped file — those are lost at compaction.

**Honest caveat:** Block 8 found **zero field reports** of anyone testing whether this actually steers the summarizer. The feature is documented; its efficacy is unverified by anyone, anywhere in the searched corpus. That makes it our measurement to make. It is cheap and fully reversible, so it is worth shipping and observing rather than debating.

### A.5 Compaction hooks

- `PreCompact` (matchers `manual` | `auto`) — can **block** (exit 2 or `{"decision":"block"}`), **cannot inject** context.
- `PostCompact` — exists, no decision control.
- **`SessionStart` with `"matcher": "compact"` is the only re-injection lever.** Its stdout is added to context after a compaction — which is exactly the 10,000-char channel we are currently overflowing by 3×.

### A.6 Not the problem — stop looking here

- **MCP tools.** With `ENABLE_TOOL_SEARCH` on (default), the whole deferred surface costs ~120 tokens. Our large MCP footprint is nearly free.
- **Skill count.** 27 local skills = 7,884 chars of roster (MEASURED), all descriptions under the 1,536-char cap (max: `reason`, 917). Canon explicitly supports "100+ available Skills" and states "No context penalty for large files" for skill bodies. But see D.3 — the roster is at ~98.6% of its ~1% budget from jarvis alone, before plugin entries.
- **Shell-output filtering proxies (e.g. rtk).** Evaluated and rejected for this layer: it optimizes tool output, which auto-compact stage 1 already clears and never re-injects — a consumable, not recurring rent. Also a third-party binary intercepting every shell call on a credentialed machine.

### A.7 What subagents inherit — MEASURED (resolves Open Q3)

Method (#1270): spawn an Agent-tool subagent under a hard **no-tool** rule and ask it to quote a distinctive marker from each surface. The no-tool rule is what makes the measurement valid — without it an agent can simply `Read` the file and report PRESENT for a surface it never received. Both probe runs reported `tool_uses: 0`.

| Surface | Carrier | Inherited by subagent? |
|---|---|---|
| User-level `~/.claude/CLAUDE.md` | file | **YES** |
| Project `CLAUDE.md` | file | **YES** |
| `docs/context/invariants.md` | bare `@import` from project `CLAUDE.md` | **YES** |
| `docs/context/glossary-index.md` | bare `@import` from project `CLAUDE.md` | **YES** |
| `~/.claude/SOUL.md` | mid-prose `@SOUL.md,` from user-level `CLAUDE.md` | **NO** — and see below |
| `~/.claude/DOCTRINE.md` | mid-prose `@DOCTRINE.md,` from user-level `CLAUDE.md` | **NO** — and see below |
| SessionStart hook `additionalContext` (memory block) | hook stdout | **NO** |

The table records the surfaces as they stood at probe time. `docs/context/glossary-index.md` has since been retired by #1418 (replaced by a pull pointer in `CLAUDE.md`); the row is kept because the measurement it reports is still the evidence for how a bare `@import` behaves.

Three findings, in ascending order of consequence:

1. **Always-loaded file surfaces are paid N times per fan-out.** Both `CLAUDE.md` levels and every *bare* `@import` under them are re-paid per subagent; hook-injected context is a one-time session cost regardless of fan-out. Budgeting implication tracked in **#1324**.

2. **`CLAUDE.md` is snapshotted at parent-session start, not re-read per spawn.** Established accidentally: a probe block appended to project `CLAUDE.md` mid-session came back ABSENT *including its own heading*, while the pre-existing content came back PRESENT. Consequence for measurement work — **you cannot test an import-form change from inside the session that made it**; it needs a fresh session.

3. **The mid-prose import form does not resolve at all.** `SOUL.md` and `DOCTRINE.md` are absent from the subagent *and* from the parent session's own `claudeMd` block, which lists a `Contents of <path>` header for every surface that did expand. Confounder excluded: both marker headings exist verbatim in the installed mirror copies, so ABSENT is non-delivery and not a marker mismatch. This means #1328's "deliver SOUL.md by `@import`" has never actually delivered. Whether the cause is the trailing comma (`@SOUL.md,` → path parsed with the comma attached) or the mid-prose position is not separable without a fresh session per finding 2 — and does not need to be, because the remedy is identical under both: **write imports bare, on their own line**, the form measured working here. Tracked separately so the byte accounting stays visible to the eviction pass.

**Follow-up (#1324 AC3) — can subagents get a stripped-down `CLAUDE.md` profile? NO, documented negative.** The harness exposes no control for it. Per [`code.claude.com/docs/en/sub-agents.md`](https://code.claude.com/docs/en/sub-agents.md): "Explore and Plan are the only subagents that omit CLAUDE.md and git status. There is no frontmatter field or per-agent setting to change which agents skip them." So the N+1 multiplier in finding 1 cannot be opted out of per agent — the only available levers are (a) shrink the inherited layer, and (b) budget it. #1324 ships (b) as `_meta.fanout_budget` in `tests/ci/fixtures/push_surface_ceilings.json`, enforced by `tests/ci/test_push_surface_guard.py::TestFanoutBudget`; #1418 is (a). The budget's *inherited set* is derived by walking bare imports from both `CLAUDE.md` roots — deliberately reusing finding 3's lesson, so a mid-prose mention is never counted as inherited weight and a newly added bare import is charged automatically.

**Standing rule this produces:** an `@import` that is believed-loaded but silently absent is worse than no import, because every file that cites it reads as satisfied. The delivery of a push surface is only established by a marker check in a fresh session — never by the presence of the import line.

---

## PART B — What is actually wrong here, measured

### B.1 ★★ The SessionStart memory hook overflows by 2.2–4.2× and fails silently

`scripts/session-context.py` prints everything it builds and exits 0. It has no idea the cap exists.

**Clean startup run (MEASURED, real script, real stdin JSON):** 22,390 chars → 12,390 discarded → **55.3% lost**. The cut lands at char 10,000 = byte 10,420, mid-word inside `working_state_jarvis`, splitting it at `proje|ct`.

| Section | Bytes | Survives 10,000-char cap? |
|---|---|---|
| banner | 186 | FULL |
| `## User Profile` | 475 | FULL |
| `## Always-Load Rules` | 1,444 | FULL |
| `## Project Context` (header) | 565 | FULL |
| `## Glossary` (CONTEXT.md body) | 7,693 | FULL |
| `## Working State` (header) | 114 | **PARTIAL — 50%** ← cut |
| Working State body | 3,204 | **LOST (0%)** |
| `## Active Goals (7)` | 1,741 | **LOST (0%)** |
| `## Mirror Drift` | 150 | **LOST (0%)** |
| `## Memory Catalog` (45 rows) | 8,029 | **LOST (0%)** |

**Real production runs are worse.** Six spilled hook outputs recovered from `tool-results/` today, all `compact`-source (MEASURED): 29,748 / 29,928 / 29,976 / 30,822 / 35,447 / 36,620 chars — **66.4% to 72.7% lost**. In the last two, the cut landed inside the snapshot's own `## Actions (43)` / `## Actions (45)` tool-call replay log, and **every** memory section after it was discarded — including the user profile.

**A seventh, larger data point arrived while this document was being written.** The session that produced this research compacted again and the hook spilled **41,504 chars → 75.9% lost**, the worst observed. The surviving 2 KB preview is the snapshot's own header and its list of user messages; the durable layer — always-load rules, working state, goals — never appeared. The failure reproduced live, on the machine, during the write-up of its own diagnosis, at exactly the scaling the slope predicts.

**The cause:** `## Pre-Compact Recovery` is emitted first and is **uncapped**. It grows ≈44.9 chars per transcript entry (MEASURED slope) and crosses the entire 10,000-char budget **by itself at ≈201 entries** (INFERRED crossing).

**The pathology is an exact inversion of intent.** Compaction is the moment the model has just lost its context and most needs the durable identity/rules/domain layer restored. That is precisely when this hook substitutes a verbose replay of transient session activity — which the compaction summary already covers — for that durable layer. And the longer the session, the more completely it does so. The failure compounds with the exact variable it should be robust to.

**Three ways it is silent:** no `wc -c` anywhere; the harness spills-and-previews rather than erroring; and the degraded behaviour shows up as diffuse "Jarvis is off today" across a whole session with no proximate cause.

**Two secondary consequences:**

- The `## Memory Catalog` — Phase 7.1's entire lazy-awareness premise ("Jarvis knows what exists and can pull full content on demand") — has **never reached the model**. Jarvis does not know what exists. (Investigating *whether it should* is what produced §B.9: it turned out to be 98% redundant, and it is now deleted rather than repaired.)
- `_touch_accessed()` bumps `last_accessed_at` for memories in sections that were never shown, feeding the ACT-R access boost with reads that did not happen. **That is a correctness bug, not a budget one.** #767 already de-biased `always_load` for exactly this reason; the reasoning was never extended here.

### B.2 ★★ SOUL.md is 889 characters from deleting its own security section

The hook is `python scripts/device-info.py && echo '' && cat ~/.claude/SOUL.md && echo ''` — a `cat` with no size guard of any kind, registered on **both** `startup` and `compact`.

| Component | Chars |
|---|---|
| `device-info.py` | 89 |
| separators | 2 |
| `~/.claude/SOUL.md` | 9,020 |
| **total** | **9,111** |
| cap | 10,000 |
| **headroom** | **889 (91.1% consumed)** |

Mirror and repo source are byte-identical (9,214 B), so there is no second copy to blame — **the repo file is what has to be gated.**

**Truncation takes the tail, and the tail is ordered maximally badly:**

1. `## External content safety` — the prompt-injection defence. Last section in the file. **First thing to be silently deleted.**
2. `## Goal & outcome awareness` — the "2+ failures → investigate root cause" rule.
3. `## Judgment calibration` — quality-over-speed and the anti-sycophancy guardrail (#759).

A security control, a safety control, and a calibration control — deleted in that order, without warning, by an unrelated commit that adds a paragraph about something else. Three of SOUL's 21 historical commits (+1,431, +2,172, +1,586 bytes) would each have blown 889 chars on their own.

**This is why the gate must be per-commit, not periodic.** SOUL grows in steps, not at a rate; crossing-date estimates from averaged growth span 2026-08-08 to 2027-10-24, which is a way of saying averaging is the wrong model here.

Also flagged: the entire identity load hangs on `&&` behind `device-info.py`'s exit code. If that script fails, SOUL.md never loads — silently.

### B.3 ★ CONTEXT.md — 89.2% never enters context, and the fix is counter-intuitive

`_CONTEXT_MAX_BYTES = 8 * 1024`, applied as `encoded[:8192]` — a slice from byte 0. No head/tail split, no section awareness, no priority.

File is 75,645 B. **The cut lands at byte 8,192 — 10.8% in**, inside line 42, mid-word in a glossary bullet.

| Heading | Byte offset | % surviving |
|---|---|---|
| `## Glossary` | 546 | **12.6%** |
| `## Invariants (domain rules that must always hold)` | 61,123 | **0% — 7.5× past the cap** |
| `## Architectural shape` | 71,932 | **0% — 8.8× past the cap** |

Both `CONTEXT.md`'s own preamble and `CLAUDE.md` advertise this file as glossary + invariants + architectural shape. **Two of the three advertised parts have never been delivered.** And what does survive is the *most redundant* text in the corpus — the Pillar/Milestone/Slice bullets, which restate `CLAUDE.md § Milestone vs pillar hygiene` and the `milestone_hierarchy_v3` memory.

**Counter-intuitive consequence — do NOT raise or remove `_CONTEXT_MAX_BYTES` to "fix" this.** The 8KB cap is simultaneously (a) the bug discarding 89% of the domain model and (b) the accidental backstop keeping everything after it alive. Without it, CONTEXT.md's 75,645 bytes would consume the 10,000-char budget seven times over and *every* later section would be lost unconditionally. **The correct move is the opposite direction — lower it.**

Existing test coverage is real but hollow: `tests/infrastructure/test_session_context_recovery.py:476-503` asserts the truncation mechanism fires and stays under the cap, using synthetic content. It asserts nothing about the real file and **would pass identically at 500 KB**.

### B.4 ★ Recurring injection — two hooks, ~16,250 tokens per 50-turn session, no dedup

`pretooluse-recall-hook.py` fires per matching **tool call**, not per turn, across 5 matchers (`Bash`, `Edit|Write|NotebookEdit`, `Task`, `mcp__memory__memory_store`, `mcp__memory__record_decision`).

Counters off disk (`~/.claude/cache/pretooluse-recall-stats.json`, 79 days, MEASURED): `fired: 3062, emitted: 543, deduped: 840` → dedup hit rate 21.5%, P(emit | matching tool call) = **13.9%**.

| Hook | Fires | Emits | Tok/emit | Session total |
|---|---|---|---|---|
| `memory-recall-hook.py` (UserPromptSubmit) | 50 turns | ~40 | ~360 | **~14,400** |
| `pretooluse-recall-hook.py` (PreToolUse ×5) | ~100 matching | ~14 | ~133 | **~1,850** |
| | | | | **≈16,250 tok** |

That is **~78% of the entire session-start surface, spent again over one session.**

**Dedup status — three questions, three answers:**

- **Against SessionStart? Partially, and the part that works doesn't matter.** `ALLOWED_TYPES = {feedback, decision, reference}` excludes `user`/`project` because those load at session start. But there is no exclusion for `always_load`-tagged memories (which *were* injected), and **every hit the recall hook can return is by construction already in the Memory Catalog.** The two mechanisms are the same index rendered twice — once recency-sorted, once query-sorted.
- **Between the two hooks? No.** Same table, same type filter, no shared state, no cross-awareness. On a topic-focused turn, overlap is the expected case, not a possibility.
- **Cross-turn, within `memory-recall-hook`? None at all — it is stateless.** A 50-turn session on one topic re-injects the same top-7 block **50 times**: ~72 KB / ~18K tokens spent restating one 1.4 KB block.

**Accidental saving grace, worth naming explicitly:** the Memory Catalog currently never arrives (B.1), so this duplication is invisible today. **Fix B.1's ordering without fixing B.4 and you convert a hidden waste into a visible one** — the catalog would arrive and then be re-quoted 40 times per session. These two fixes are coupled.

**Largest uncapped path anywhere in the system:** `check_known_unknown_gate()` can flip `brief_mode` off for any turn, raising the budget to `CHAR_BUDGET_FULL = 40,000` chars with full memory bodies and bypassing `MAX_BRIEF_ENTRIES`. **A single widened turn can inject ~10,000 tokens.**

Calibration note: `pretooluse-recall-hook.py` declares an audit target of "< 1K tokens added per typical session" in its own docstring. Measured rate puts it at **~1.9× over its own budget** at 100 matching calls, ~3.7× at 200. The target has never been checked against the counters the hook itself writes.

### B.5 Duplication across the three instruction files

≈**7,660 B / ≈1,916 tokens removable with zero information loss** — 15.3% of the three prose files. Verdicts: **10 AGREE, 2 DIVERGE, 1 CONTRADICT, 1 dead-copy.**

Top removable:

| Topic | Files | Freed |
|---|---|---|
| merge-gate CI implementation spec | user-level CLAUDE.md → pointer to the workflow + its guard test | **3,400 B** |
| milestone/pillar/slice hierarchy | CONTEXT.md + jarvis CLAUDE.md + memory | **1,400 B** |
| linked-issue / hotfix / refactor PR rules | user-level + jarvis CLAUDE.md | 620 B |
| recall-before-acting | user-level + jarvis CLAUDE.md ×2 | 500 B |
| end-to-end ownership (near-verbatim ×3) | SOUL + jarvis CLAUDE.md | 410 B |

**But the four non-AGREE rows are the valuable ones** — they free almost no bytes and each is a live correctness hazard:

- **CONTRADICT — `config/SOUL.md` contradicts itself four lines apart.** L26 lists issue/PR comments as requiring confirmation; L30 lists `comment` as autonomous. User-level policy and several skills already assume L30. **This is a live ambiguity at the boundary of the single most frequently applied rule in the instruction set.** Canon on exactly this: "if two rules contradict each other, Claude may pick one arbitrarily."
- **DIVERGE — two rotted cross-references.** `jarvis/CLAUDE.md` L134 cites "SOUL §Personality (quality over speed)" — it is in §Judgment calibration. Same sentence cites "SOUL §Goal awareness" — the actual heading is "Goal & outcome awareness". Nothing validates these pointers.
- **DIVERGE — recall type filter**: documented behaviour vs. what the hook implements.

### B.6 Growth forensics — the 2026-04-23 compression fully round-tripped in 77 days

Baseline `1396685c` / `98bba823`, 2026-04-23 ("compress always-loaded session context (~10K tokens)").

| File | Then | Now | Growth |
|---|---|---|---|
| `CONTEXT.md` | 10,431 B (created 04-30) | 75,645 B | **+625%** |
| `jarvis/CLAUDE.md` | 7,560 B | 24,024 B | **+218%** |
| `config/SOUL.md` | 3,826 B | 9,110 B | **+138%** |
| `scripts/session-context.py` | 13,466 B | 35,086 B | +160% |

Reference point from canon: Anthropic's own interactive model puts a typical project CLAUDE.md at **1,800 tokens**. Ours is ~6,000. The official written rule is "**target under 200 lines** per CLAUDE.md file. Longer files consume more context and reduce adherence" — with the important qualifier that files are loaded **in full regardless of length**, so this is an adherence rule, not a truncation rule.

Bulk dumps are 14% of commits but 56% of SOUL's growth.

**Compression with no structural change round-trips.** That is the whole case for Part D.

### B.7 The size-independent reason CLAUDE.md gets ignored

Claude Code wraps injected CLAUDE.md content in a `<system-reminder>` that says **both**:

> "These instructions OVERRIDE any default behavior and you MUST follow them exactly as written"

and, at the end of the same block:

> "this context may or may not be relevant to your tasks. You should not respond to this context unless it is highly relevant to your task"

Discovered by traffic capture (`anthropics/claude-code#18560`), independently re-confirmed 2026-07-26, and **directly observable in this session's own context right now.**

Field reports of "CLAUDE.md ignored" span 720 bytes → 100 lines → 400 lines, from different people, and most do not mention file size at all — which is itself informative about what practitioners think the salient variable is.

**Therefore: do not expect cutting CLAUDE.md to fix adherence.** Cut it for the recurring re-injection cost (A.2) and for the instruction-count budget — the strongest attributed reasoning in the field reframes the limit as ~150–200 *instructions system-wide*, noting Claude Code's own system prompt already burns ~50 before yours is added. Adherence is a different problem with a different fix: **hooks.**

### B.8 Canon's own resolution — and where it collides with us

> "An instruction like 'never edit `.env`' in CLAUDE.md or a skill is a request, not a guarantee. A PreToolUse hook that blocks the edit is enforcement. If a rule must hold every time, make it a hook rather than a prompt instruction."

Hook context cost is stated as **"Zero"**. We already run three enforcement hooks (`secret-scanner`, `protected-files`, `record-decision-gate`), so the path is proven here.

**The genuine collision.** The Opus 5 guidance says:

> "If your prompt contains explicit verification instructions … remove them" / "do not use subagents to verify or double-check your own work."

We carry a lot of this: `Definition of Done`, `Verify before assuming implemented`, `Verification (non-negotiable): after any agent completes, run git diff`.

But the Fable 5 guidance **prescribes** the opposite for a neighbouring class:

> "Before reporting progress, audit each claim against a tool result from this session… nearly eliminated fabricated status reports"

The distinction is real: canon targets **redundant extra verification passes**, not **anti-fabrication auditing**. Our `git diff`-after-agent rule is incident-backed (agents reported edits to files that did not exist) and belongs to the second class. **Reshape it into "audit claims against tool results", do not delete it.** The "use a subagent to double-check your own work" instruction is a direct hit and should go.

**The gap canon leaves open.** The flagship guidance draws no distinction between a rule written after a real incident and a rule written just in case, and applies no asymmetric-cost reasoning — a rule preventing mild annoyance and a rule preventing a dropped table are deleted on equal terms. Our `record_decision` provenance trail is an answer to a hole the canon does not see. Keep it.

### B.9 The Memory Catalog is 98% redundant — DECIDED, delete

Queried rather than argued. The catalog takes the top 50 live memories by `last_accessed_at`, scoped to jarvis-or-global, excluding `type=user`, `always_load`-tagged, and `working_state_jarvis`. That filter admits **2,358 rows**; 50 are rendered.

| type | rows rendered | fetchable by `memory-recall-hook.py` on demand |
|---|---|---|
| feedback | 22 | yes |
| decision | 14 | yes |
| reference | 13 | yes |
| project | **1** | no |

**49 of 50 are exactly the `ALLOWED_TYPES = {feedback, decision, reference}` the recall hook already queries semantically every single prompt.** The one exception is `pending_grill_local_agent_harness` (type=project, 2,611 B) — a pending-work marker whose correct home is `working_state_jarvis`, not a catalog.

Two further defects show it was mis-specified, not merely redundant:

- **The sort key is `last_accessed_at` — recency, not importance.** It was never "the important records"; it was always "the recently touched ones". Stated purpose and implementation diverged from the start.
- **It is one access-bump away from being flooded.** 1,947 `session_snapshot_*` rows pass its filter and are held out only because nothing bumps their access time. Any change that touches them evicts everything meaningful.

And it has never reached the model regardless (§B.1). At 8,029 B it is the second-largest section — not free, but *the reason other sections are lost*.

**Decision (owner, this session):** delete the section; migrate the single non-redundant row to working state. Recorded as `3d61f0b9-8755-4f93-8ac7-a97f955d7ee5`, reversible, confidence 0.9.

### B.10 Pre-Compact Recovery — one artifact, two access patterns, one of them broken

Not a failed experiment wholesale. `scripts/pre-compact-backup.py` writes a session snapshot on `PreCompact` and `SessionEnd`, and it has **two** consumers with opposite shapes:

| consumer | shape | verdict |
|---|---|---|
| `/end` skill (#280) | **pull** — explicit read at session end, after N compactions, to reconcile decisions and write the report | **correct.** Costs zero until invoked, one query when invoked. The history it needs is out of the window by definition. |
| `session-context.py` (#279) | **push** — injected into *every* compact resume, unconditionally, as the first section | **broken.** Nobody asked for it, and it destroys everything behind it. |

Confirming that pull is the intended path: snapshot rows are **deliberately excluded from `memory_recall`** by tag (`EXCLUDE_TAGS_FROM_RECALL`, #417). They can only enter context when something explicitly asks for them. The push path is the sole exception, and it is the one that fails.

**The design error is two constants that were never compared:**

```
pre-compact-backup.py:79   SIZE_BUDGET = 30_000    # producer target
                           hook stdout channel      = 10_000 chars
```

**The producer's budget is 3× the consumer's channel.** That is the whole mechanism of §B.1's compact-path total loss, in one line.

**Retention error:** `PRE_COMPACT_FRESHNESS_MINUTES = 30` — a snapshot is usable for half an hour. Accumulated: **2,097 rows / 5.787 MB since 2026-04-21, never pruned.** Everything older than 30 minutes is unreachable by design.

*(A count of 17 snapshots "ever read after write" appears in the raw data. Do not use it as evidence — the recovery path reads via a direct `.select()` and does not bump `last_accessed_at`, so recovery reads are invisible to it. The proof of failure is the 30k-vs-10k arithmetic and the six measured spilled runs, not this counter.)*

**Why push existed at all**, and why the answer is still no: after a compaction the model does not know history is missing, so it would not think to ask. That concern is real — but the answer to "doesn't know to ask" is a **pointer, not a payload**. ~200 characters: branch, cwd, path to the full transcript, pointer to `working_state_jarvis`. The harness already prints the transcript path at the end of every compaction summary, unprompted.

This is the same principle applied to the catalog in B.9: eager restatement of something already available on demand. The only difference is that the snapshot's *write* side has a legitimate second consumer, so the cut is half the mechanism, not all of it.

**Decision (owner, this session):** keep the write path and the generation counter (`~/.claude/compaction-counts/`, which lets `/end` detect compaction independently of snapshot presence); keep `/end`'s pull; **delete the push from `session-context.py`** and replace with the ~200-char pointer; add retention. `SIZE_BUDGET` needs no realignment afterwards — 30 KB goes to Postgres, where `/end` reads it in one query and the 10,000-char channel is not on the path.

---

## PART C — Concrete edits

Ordered by value. Each carries measured savings and a reversibility flag.

### C.0 Sequencing — measure one thing at a time, but not everything one at a time

Constraint from the owner: *"проверять/замерять надо отдельно от всех остальных изменений, чтобы сигнал не захлебнулся от остального."* Correct, and it splits the fix list in two — the discriminator is **what the meter is**, not how big the change is.

- **Objective fixes have a deterministic meter.** `python scripts/session-context.py < fixture.json | wc -c` answers "did it fit under 10,000" with no judgment call and no session to live through. Several such fixes **can and should batch** — batching them does not muddy the signal, because each one's contribution is separately computable from the byte counts. C.1 rows 1–3 and 5–6 are all of this kind.
- **Behavioral changes have no meter but your own judgment**, over days, with high variance. These must be **isolated** — one at a time, each given long enough to read. Compaction threshold, `# Compact instructions`, and the CLAUDE.md/SOUL.md cuts are all of this kind.

**REVISED after the grill disposition round** (2026-07-28; decisions `7fe96e01`, `996f4da1`, `93fee0f9`).

The milestone stands on **two columns**:

- **Column 1 — content rewrite** (Invariants compression, Glossary categorization + informative index, cross-file dedup, roster diet). Same information, fewer words. This is a **precondition**, not an optimization: `## Invariants` alone is 10,809 B — larger than the entire 9,500-char assembly budget — so no drop-priority ordering can deliver it uncompressed.
- **Column 2 — mechanism** (budget-aware assembly with drop-priority, ratchet ceilings, pointer payload, recall-hook fixes). The safety net that keeps badly-written files from silently breaking delivery again.

Owner's framing of the goal: nothing gets truncated *at all*, AND everything fits the recommended size — fix both sides, not one.

| step | change | kind | how it is judged | wait |
|---|---|---|---|---|
| 1 | Column 1 content rewrite: Invariants compression (precondition), Glossary categorization + index, cross-file dedup (~7,660 B), roster diet + `disable-model-invocation` unification (`a54f09df`) | objective | byte counts per file/section vs recorded ceilings | immediate |
| 2 | Column 2 code fixes: pointer payload + TTL (C.1 row 1), budget-aware assembly + drop-priority + self-log (rows 4–5), catalog deletion (row 2), section-aware CONTEXT push (row 3), `_touch_accessed` (row 6), session-id stamping (row 9) | objective | `wc -c` on hook stdout; target < 9,500 with every section present | immediate |
| 3 | Recall-hook fixes (C.1 rows 7–8) as their own batch | objective | the counters the hooks already write | immediate; separate batch for attributability |
| 4 | File cuts beyond dedup (C.2, C.3 proposals) | behavioral, **isolated** | adherence, not bytes — see B.7 | ~1 week each group |
| 5 | **FINAL, isolated:** `CLAUDE_AUTOCOMPACT_PCT_OVERRIDE` 75 → 55 | behavioral, **isolated** | do sessions degrade less; is compaction now annoyingly frequent | ~1 week of normal work |

The threshold experiment moved from step 1 to **last** (decision `996f4da1`, amending `0dddc143`): lowering the threshold before the push-path fixes shrinks the working window while compact-recovery is still broken — compaction fires more often while each one still loses 66–76% of the durable layer. After the fixes a compaction is cheap, and the experiment can be judged in isolation.

`# Compact instructions` (Part D) is **deferred wholesale** — block *and* measurement — to a low-priority follow-up issue (decision `93fee0f9`). Out of the milestone.

### C.1 Code fixes — do these first, they are pure bug fixes

| # | Change | Freed | Reversible |
|---|---|---|---|
| 1 | **Delete the Pre-Compact Recovery push** from `session-context.py`; replace with a **pointer + deterministic minimum** (~200 chars, decision `59f06783`): the `# Session Snapshot — <sid>` header line **verbatim** (`/end` extracts the session id from exactly that line — SKILL.md:35), generation counter, count of `record_decision` episodes this session, branch/cwd/transcript path. Keep the write path and `/end`'s pull untouched (§B.10). Add **TTL 30-day** retention on `session_snapshot_*` rows, enforced in `pre-compact-backup.py` at write time (currently 2,097 rows / 5.8 MB, all unreachable past 30 min) | **~12,000–34,000 chars/run** on the compact path; restores every section behind it | yes |
| 2 | **Delete the `## Memory Catalog` section** (§B.9, decision `3d61f0b9`). Migrate its one non-redundant row (`pending_grill_local_agent_harness`) into `working_state_jarvis` first | **8,029 chars/run** | yes |
| 3 | Replace the byte-0 head-slice of CONTEXT.md with a **section-aware push**: emit the compressed `## Invariants` (post-Column-1, under its own ceiling) + the Glossary **category index** (name + one line per category; bodies stay pull-only via §C.4/on-demand). Requires Column 1 as precondition | ~6,100 chars/run vs today, and the push finally carries the parts the file advertises | yes |
| 4 | **Budget-aware assembly**: build sections against a running **9,500-char** counter (units: **chars**, everywhere). The counter accounts the 186-char banner, section separators, and a reserved `dropped: <names>` line *before* assembly. Drop-priority when exhausted: Always-Load > User Profile > Working State > Goals > Glossary/CONTEXT push. Split the query layer from the assembly layer so the CI fixture **imports the real assembler** (E.1), not a reimplementation | ordering is free (a 10-line move) | yes |
| 5 | Fail loudly + **self-log**: the hook logs its own emitted size per run (stderr warning past 9,500 + a counter file). Spill observation lives here — no separate observation mechanism | converts silent 73% data loss into a visible, trendable signal | yes |
| 6 | Stop `_touch_accessed()` from bumping memories in sections that were not emitted | correctness, not bytes | yes |
| 7 | Session-scoped injected-id dedup in `memory-recall-hook.py`: key **(memory id, mode)**, dedup epoch = the existing PreCompact **generation counter** — a compaction advances the epoch, so post-compact re-injection is allowed exactly once per generation | **~18K tok per 50-turn session** — highest ROI per line of code in the audit | yes |
| 8 | **Widen-gate** the full-mode recall path: keep `CHAR_BUDGET_FULL` as-is, but cap the widened injection at ~15–20K chars or top-N entries (low-priority slice) | removes a ~10K-token single-turn spike | yes |
| 9 | **Stamp the harness session id into `record_decision` episodes.** `decision.py` stores only `actor="session:<slug>"` — no join key to the harness transcript. With the stamp, post-compact UUID recovery becomes one Postgres query keyed by session id instead of parsing truncated snapshot text (which never contained the UUIDs anyway — the parser reads text blocks only, never tool_results) | correctness/recoverability, not bytes | yes |

**Rows 1–4 together are the fix for B.1**, and they over-deliver: on the clean-startup profile (22,390 chars) deleting the catalog and shrinking the CONTEXT push alone removes ~14,000, landing under the cap with room. On the compact path row 1 is what does the work. Row 4 stays anyway, as the backstop that keeps the next 5 KB of growth from silently re-breaking it.

**The 10,000-char harness cap itself is accepted as a platform limitation** (not configurable; documented as such). The 9,500 target leaves 500 chars of headroom — that figure is a guess, to be revisited against row 5's self-log data.

### C.2 SOUL.md — PROPOSALS, your call

Not auto-edited. Each of these is a decision, not a cleanup.

| # | Change | Freed | Reversible |
|---|---|---|---|
| S1 | **Resolve the L26/L30 contradiction** on issue/PR comments — pick one, delete the other | 0 B, removes a live ambiguity | yes |
| S2 | Cut the `### Grill trigger checkbox` restatement (it also lives in `jarvis/CLAUDE.md` routing rules) | ~450 chars — **roughly doubles the remaining headroom** | yes |
| S3 | Cut `### End-to-end ownership` here or in CLAUDE.md — near-verbatim ×3, but not both | ~410 B | yes |
| S4 | Label the smart-zone figure honestly: practitioner consensus (Horthy/HumanLayer, ~100 builders), not a measured Anthropic number | 0 B | yes |
| S5 | Split `device-info.py` off the `&&` chain so a device-info failure cannot silently take SOUL.md with it | 0 B, removes a silent-failure mode | yes |

**Do not solve SOUL's headroom by moving text into CONTEXT.md or CLAUDE.md** — those land in the same window through a different door, and that door is already jammed.

### C.3 CLAUDE.md — PROPOSALS

| # | Change | Freed | Reversible |
|---|---|---|---|
| M1 | Merge-gate CI implementation spec in user-level CLAUDE.md → pointer to `.github/workflows/code-review.yml` + its guard test | **3,400 B every session, every project** | yes |
| M2 | Milestone/pillar/slice restatement → pointer to `milestone_hierarchy_v3` | 1,400 B | yes |
| M3 | Fix the two rotted SOUL cross-references (L134) | 0 B | yes |
| M4 | Apply the `/doctor` heuristic as an audit pass: **cut what Claude can derive from the codebase** (directory layouts, dependency lists, architecture overviews); **keep pitfalls, rationale, conventions that differ from tool defaults** | unmeasured; likely the largest single reduction | yes |
| M5 | Delete "use a subagent to verify your own work"; reshape `git diff`-after-agent into "audit each claim against a tool result from this session" | small | yes |
| M6 | Move must-hold-every-time rules from prose into `PreToolUse` hooks (canon's own resolution; hook context cost is Zero) | shifts cost to zero, raises enforcement from request to guarantee | yes |

**Keep** the skill routing table — it is the only routing surface that survives a compaction (A.2).

**Test protocol for any of these:** editing CLAUDE.md mid-session neither invalidates the cache nor applies. Changes load on the next `/clear`, `/compact`, or restart. Measure with `/context` in a fresh session, before and after — not by asking the model whether it feels lighter.

### C.4 Skills

- 27 skills, roster 7,884 chars ≈ **98.6% of the ~1% budget from jarvis alone**, before 20+ plugin/built-in entries. No description exceeds the 1,536 cap. On overflow, least-invoked entries are dropped **silently** — accepted as a platform limitation; the roster-share number goes in as a documented observation, not something we can instrument.
- **DECIDED (`a54f09df`): unify suppression on `disable-model-invocation: true` frontmatter.** Today two mechanisms coexist: `zoom-out` uses frontmatter; `/curate`, `/setup-tasks`, `/last-work-report`, `/caveman`, `/autonomous-loop` are suppressed via `skillOverrides: "name-only"` in **user-level** `settings.json` (device-local, invisible to review). Move all five to frontmatter (repo-controlled, installer-propagated), then delete the `skillOverrides` duplicates.
- Delete `/autonomous-loop` rather than continuing to carry a superseded skill in the roster.
- **Doc fix (drive-by slice):** project `CLAUDE.md` claims the SessionStart hook is registered in `.claude/settings.json` — it is not; the repo file is `{}` and all hook registration (SessionStart, PreCompact, SessionEnd) lives in user-level `C:\Users\<user>\.claude\settings.json`. Every CI/ceiling target in Part E must aim at the user-level registration.

---

## PART D — Candidate `# Compact instructions` — **DEFERRED**

**Status: deferred wholesale** — block *and* measurement — to low-priority follow-up issue **#1264**, indefinite horizon (owner call, decision `93fee0f9`). Not part of the milestone. Kept here as the ready-to-ship draft for whenever the issue is picked up. One wording fix noted for that time: "drop the transcript" should read "drop the intermediate output" — the compactor sees tool output, not a transcript of intent.

Original plan: add to **project-root `jarvis/CLAUDE.md`**. Free-form header; the compactor matches on intent. Unverified in the field by anyone — ship it and measure.

```markdown
# Compact instructions

When compacting, preserve in full:
- The user's own words for the current task and any constraints they stated verbatim.
- Decision UUIDs from `record_decision` calls, and any `memories_used` UUID map built this session.
- Open issue/PR numbers, branch name, and which files have been modified but not yet committed.
- Failing test names and their error output; the last command run and its exit status.
- Anything the user explicitly said not to do.

Compress aggressively:
- Tool-call replay and file-read output — keep the conclusion, drop the transcript.
- Exploratory reasoning that did not change the plan.
- Superseded plans; keep only the plan currently in force.

Never summarize away an unfinished task. If work was in progress, state exactly what
remains, not that work "was underway".
```

**Rationale for each line traces to a documented failure mode:** loss of position in the plan (reported independently by multiple practitioners: models restart completed steps or act surprised prior steps were done), lossy summarization dropping the specific detail needed, and instructions silently dropped followed by confabulated explanations for the resulting behaviour.

**Measurement plan:** add it, run a session past a compaction, and check whether decision UUIDs and modified-file lists survive the boundary. That single observation is more than the entire searched corpus currently contains.

---

## PART E — Anti-regrowth

The 2026-04-23 compression bought ~10K tokens and **fully round-tripped in 77 days with no structural change.** Cutting again without a ratchet buys another 77 days. This part is the actual deliverable.

**Finding: there is exactly one budget mechanism in the entire system** — the runtime `_CONTEXT_MAX_BYTES` truncation — and it caps *injection*, not *file growth*. Because it truncates silently from byte 0, growth past 8KB has **zero observable symptom**: CI stays green, the hook keeps printing, and the file has grown to 9.2× the cap. **The truncation did not protect the budget; it hid the overrun.** Every other always-loaded artifact has no cap at any layer. `config/SOUL.md` and `CLAUDE.md` are not even in `protected-files.py` — any agent can freely write to them.

### E.1 The ratchet — a `tests/ci/` guard

The repo already has the right convention (#326: path-filtered guards ship with a co-located fixture test; `ci-meta.yml` runs `tests/ci/` unfiltered on every PR). It has simply never been pointed at context size.

Proposed `tests/ci/test_context_budget_guard.py` (revised per grill dispositions):

1. **Assemble both SessionStart hook commands' stdout and assert < 9,500 chars** — by **importing the real assembler** (row 4 splits query from assembly precisely to make this importable), NOT by reimplementing its logic. A reimplementation drifts exactly like `test_session_context_recovery.py:476-503`, which "would pass identically at 500 KB". This is the load-bearing assertion — it covers SOUL.md and `session-context.py` simultaneously, and it is the one that would have caught B.1 and B.2. The hook commands it assembles are read from the **user-level** `settings.json` registration shape (see C.4 doc-fix — the repo `.claude/settings.json` is `{}`).
2. **Ceilings apply to PUSH SURFACES only**, stored in a checked-in JSON: `SOUL.md`, both `CLAUDE.md` files, the **pushed part** of CONTEXT.md (compressed Invariants + Glossary category index — each under its own ceiling). **`CONTEXT.md` as a file is deliberately NOT capped** — its pull-only Glossary bodies may grow freely; that is the point of the push/pull split. Lowering a ceiling is a normal commit; **raising one requires editing the fixture in the same PR**, which makes regrowth a visible, reviewable act rather than a silent accumulation.
3. **Assert the assembled output actually contains the advertised sections** — Invariants present, Glossary index present — i.e. delivery, not just size. This is the check that distinguishes "the mechanism works" from "the mechanism delivers something useful", which is exactly what the existing test misses.

The ratchet is a `pytest`, not a lint or a doc rule, because it must fail red in CI on a PR that has nothing to do with context — that is the whole point. A rule in CLAUDE.md asking future sessions to keep files small is the same class of thing that failed the first time.

### E.2 Per-commit, not periodic

SOUL grows in **steps** (three single commits of +1,431, +2,172, +1,586 B), not at a rate. A quarterly sweep would have missed each one by up to three months. A pre-commit hook asserting assembled hook stdout < 9,000 chars converts the entire failure class into a red commit at the moment of authorship.

### E.3 An eviction rule, so the ratchet has somewhere to push

A cap with no eviction policy just becomes a blocker people work around. Proposed, drawing on the `/doctor` heuristic and the twice-wrong trigger:

- **Admission:** a rule enters an always-loaded file only if Claude has made the same mistake **twice** (canon's stated trigger). One incident is a memory record, not a CLAUDE.md line.
- **Eviction, in order:** (1) anything derivable from the codebase; (2) implementation specs that belong next to their implementation (pointer instead); (3) restatements of another always-loaded file; (4) reversible decisions older than ~60 days with no recurrence.
- **Exempt from eviction:** rules with a recorded incident and a `record_decision` UUID. Canon offers no provenance convention; ours is the answer to that gap and is what keeps E.3 from deleting the rule that stops us dropping the database.
- **Preferred destination for anything that must hold every time:** a `PreToolUse` hook. Context cost Zero, and it is enforcement rather than a request.

---

## Open questions

1. ~~**Does the compaction threshold on this machine actually sit at 70%?**~~ **RESOLVED** (§A.3). Both env vars are set; the threshold is `min(0.75 × 200,000, 187,000)` = **150,000 tokens**. The open part is now a *choice*, not a fact: move it to ~55% (110k) — that is step 1 of §C.0.
2. ~~**Do custom compaction instructions steer the summarizer at all?**~~ **DEFERRED wholesale** (decision `93fee0f9`) — block and measurement together go to a low-priority follow-up issue, out of the milestone. Part D holds the ready draft.
3. ~~**Do subagents inherit user-level CLAUDE.md?**~~ **RESOLVED — yes** (§A.7, #1270). The field report claiming global `CLAUDE.md` does not propagate is **wrong for this harness**: measured 2026-08-07, both `CLAUDE.md` levels and every bare `@import` beneath them are inherited; only hook-injected context is not. Two by-products of the probe outrank the original question — `CLAUDE.md` is snapshotted at parent-session start rather than re-read per spawn, and the mid-prose import form we use for `SOUL.md`/`DOCTRINE.md` never resolved at all. Cost implication → #1324; the non-delivery defect → its own issue.
4. ~~**What is the real roster budget once plugin and built-in skills are counted?**~~ **RESOLVED — accepted as a platform limitation.** The plugin share is not observable from our side; documented as such in C.4, with the jarvis-side number (98.6% of ~1%) as the tracked observation. Mitigation is the roster diet + `disable-model-invocation` unification (`a54f09df`).
5. ~~**Should the Memory Catalog exist at all?**~~ **RESOLVED — delete** (§B.9, decision `3d61f0b9`). Measured: 49 of its 50 rows are types the recall hook already fetches on demand; the 50th belongs in working state. It also sorted by recency, never by importance.
6. ~~**Does the `## Glossary` slice still earn 2,048 bytes?**~~ **RESOLVED via the push/pull split** (C.1 row 3 + decision `37f0639e`): the pushed part becomes the compressed Invariants + a Glossary **category index** (name + one line per category, `###` anchors); Glossary bodies become pull-only, reachable via the on-demand ladder (`.claude/rules/*.md` `paths:` globs for file-scoped entries, global CLAUDE.md pull instruction + informative index for the rest). Pull-rate telemetry validates the pull level; if pull-rate is ~0 after a month, the content escalates to a deterministic push (rules-glob / hook).

## Sources

Block outputs (session scratchpad): `block1-compact-mechanics.md`, `block2-official-canon.md` (S1–S14, official Anthropic canon), `block3-loading-mechanics.md`, `block4-practitioner-patterns.md`, `block4b-adversarial-data.md`, `block5-anti-regrowth.md`, `block6-local-audit.md` (1,026 lines of local measurement), `block8-field-reports.md`.

Primary external sources of record: `anthropics/claude-code` issues #17530, #18560, #31806, #65379; Boris Cherny (Anthropic) on HN item 47740541; HumanLayer "Writing a good CLAUDE.md"; Dex Horthy on The Pragmatic Engineer and Dev Interrupted. Rejected as unattributable: `claudefa.st`, `decodeclaude.com`, and assorted SEO listicles.
