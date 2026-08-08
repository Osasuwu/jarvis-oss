---
title: Documentation patterns as agent contract — evidence for the three-way split
date: 2026-05-18
status: working-doc
project: jarvis
author: subagent (research)
sources_count: 18
related:
  - docs/research/deep-dive-llms-txt.md
  - docs/research/agent-dev-practices-sweep-2026-05-06.md
  - docs/research/deep-dive-spec-driven-development.md
---

## Executive summary

The three-way `CLAUDE.md / SOUL.md / CONTEXT.md` split is **partially evidence-based, partially novel**:

1. **Splitting rules from identity is supported** by Anthropic's progressive-disclosure pattern (SKILL.md frontmatter vs body) and by alexop.dev's "stop bloating" advice. The 200-line threshold is real: past ~200 lines whole blocks get ignored (Anthropic eng guidance; ETH Zurich Feb-2026 empirical study).
2. **Splitting domain glossary from process rules is supported** by DDD ubiquitous language and the 2025 Domain-Integrated Context Engineering (DICE) work — a living `domain-terms.md` is an emerging pattern.
3. **The three-way split as a named pattern has no direct precedent.** Closest analogues: Kiro's `product.md / structure.md / tech.md`; spec-kit's `requirements / design / tasks`; Cursor's `alwaysApply: true` index + on-demand `.mdc` files. Jarvis sits in the same design space but with sharper identity-vs-rules-vs-domain axes.
4. **Personality stripped, behavior rules kept** matches 2025 system-prompt research: persona controls *tone*, behavior rules control *reliability*. The two move on different timescales and benefit from different review cadences.
5. **`record_decision` over markdown ADRs is defensible but underdocumented** — every major industry guide still defaults to file-based ADRs. The queryable advantage is real; the cost is "no one but Jarvis can navigate it."
6. **Risk to flag:** the ETH Zurich Feb-2026 study found LLM-generated context files *reduced* SWE-bench success by ~3% and human-written ones improved it by only ~4%, at >20% inference cost. Jarvis's docs must justify their token rent every session.

---

## 1. llms.txt adoption state 2025-2026

### Numbers (verified)

- **~780-788 sites** in the three main directories combined (llms-text.com/directory, llmstxt.site, directory.llmstxt.cloud) as of early 2026.
- **SE Ranking survey:** 10.13% of 300k domains have an `llms.txt` (early-2026 measurement). Skewed toward docs-heavy SaaS — Cloudflare, Vercel, Stripe, Supabase, Coinbase, Anthropic, Mintlify-hosted properties.
- **Mintlify, Fern, GitBook** now auto-emit `llms.txt` + `llms-full.txt` as part of the build. This is doing most of the adoption-curve heavy lifting — owner-effort there is zero.

### Real problem or marketing?

Mixed. The original Howard pitch (a one-page annotated index at site root) addresses a **real ingestion problem** for crawlers that don't want to spider HTML. It is *not* solving anything for agents already reading a working tree — Claude Code does not natively parse it.

The `llms-full.txt` variant is closer to marketing: it duplicates content the agent could fetch directly, and its maintenance cost is non-trivial. Useful when:
- Site has heavy JS rendering (`.md` extraction is otherwise lossy).
- Audience includes RAG indexers doing one-shot ingest.

For Jarvis specifically — see `docs/research/deep-dive-llms-txt.md` §6: low cost, asymmetric upside, **ship the minimal version**.

### `<llms-ignore>` clarification

Confirmed: it's a **Mintlify extension**, not in the canonical spec at llmstxt.org. Irrelevant to a committed-file repo-root deployment.

---

## 2. CLAUDE.md / equivalents — empirical patterns

### The 200-line ceiling (load-bearing finding)

Multiple independent sources converge on the same number:

- **Anthropic's own engineering guidance:** target <200 lines.
- **alexop.dev "Stop bloating your CLAUDE.md":** past 80 lines rules start dropping, past 200 lines large blocks get ignored, past 500 words of dense rules adherence collapses.
- **"5 patterns that make Claude Code follow your rules" (dev.to):** frontier models reliably follow ~150-200 *discrete* instructions; Claude Code's own system prompt uses ~50 slots; you get 100-150.
- **"I wrote 200 lines of rules. Claude ignored them all" (dev.to):** anecdotal but representative; the post triggered the alexop.dev response.

**Jarvis state:** `CLAUDE.md` 197 lines, `CONTEXT.md` 188 lines, `SOUL.md` 103 lines. Total 488 lines loaded by SessionStart. CLAUDE.md is **at the ceiling** — every section needs to justify its slot.

### ETH Zurich empirical study (Feb 2026, the most important data point)

"Agent READMEs: An Empirical Study of Context Files for Agentic Coding" — arxiv.org/html/2511.12884v1.

**What helps:**
- Consistent shallow hierarchy: 1 H1, 6-7 H2, 9-12 H3. Deeper nesting (H5+) is rare and ineffective.
- Concrete functional sections classify well (F1 0.89-0.94): "Build and Run", "Architecture", "Testing".
- Median word counts: CLAUDE.md 485 words, Copilot 535, Codex 335.

**What hurts:**
- Low readability — Flesch Reading Ease median 16.6 for CLAUDE.md ("very difficult, comparable to legal documents").
- Abstract categories classify poorly: Maintenance F1 0.56, Project Management 0.42, AI Integration 0.48.
- Append-only growth — 59-67% of files modified across multiple commits in short bursts, *grew through additions, never deletions*.

**The headline finding (replication of broader work):**
> LLM-generated context files reduced task success rates by about 3% vs no context file. Human-written context files improved success by ~4%. Either way, inference cost rose >20%.

**Implication for Jarvis:** every doc that ships must pay for >20% inference rent with concrete behavioral improvement. "Nice to have" content is a tax with no return.

### What mature setups actually look like

| Source | Total length | Structure |
|---|---|---|
| alexop.dev recommendation | ~50 lines | overview + commands + stack + pointers ("read X if Y") |
| Anthropic best-practices | <200 lines | sections per claude.com/docs/en/best-practices |
| ETH Zurich study median | ~485 words | 1 H1 + 6-7 H2 + concrete sections |
| Jarvis CLAUDE.md (current) | 197 lines | rules + skill routing + delegation + cadence |

The **"docs/X.md + IMPORTANT pointer in CLAUDE.md"** pattern (alexop) appears in every mature setup. Cursor calls these `globs:` rules; Anthropic calls them skills; the alexop.dev pattern just uses explicit `IMPORTANT: read docs/X.md when ...` lines.

---

## 3. Progressive disclosure for project docs

### Anthropic's pattern (the canonical reference)

Three tiers:
1. **Metadata only loaded at startup** — name + description (≤1024 chars). Median ~80 tokens per skill across Anthropic's 17 official skills = ~1700 tokens total for 17 skills.
2. **SKILL.md body loaded on demand** — when Claude decides skill is relevant.
3. **Helper assets / scripts / references** — loaded as the skill executes.

This is the **canonical progressive-disclosure spec**. Anthropic's December 2025 release of Agent Skills as an open standard saw adoption by OpenAI, Google, GitHub, Cursor within weeks.

### Applying to project-level docs

The progressive-disclosure logic translates 1:1 to project docs:

| Tier | Project-doc analogue | Jarvis today |
|---|---|---|
| 1. Always-load metadata (~80 tokens) | One-line description of each doc | SessionStart hook injects compact catalog |
| 2. On-demand load | CONTEXT.md (loaded per glob/topic), CLAUDE.md sections behind `IMPORTANT: read X if Y` | CLAUDE.md + CONTEXT.md *fully loaded* at session start |
| 3. Reference-only | `docs/design/jarvis-v2-redesign.md` etc. | Same — fetched when referenced |

**Jarvis violates tier-2 progressive disclosure** by loading CONTEXT.md (188 lines, domain glossary) at every session start. The "always-load" justification was "/grill needs it". The 200-line ceiling argument cuts against this.

### Cursor's `.cursor/rules/*.mdc` (the production example)

- `.cursorrules` is deprecated (since 2025).
- New format: `.cursor/rules/*.mdc` with frontmatter `alwaysApply: true|false` + `globs:`.
- Pattern: one **index file** with `alwaysApply: true` (lightweight nav), specialised files with `alwaysApply: false` triggered by globs.

This is the cleanest production example of progressive disclosure for project docs. Jarvis's three-way split could borrow the **glob-triggered loading** mechanic without restructuring the files themselves.

---

## 4. Three-way split vs single-file

### Evidence base

| Pattern | Origin | Files | Evidence quality |
|---|---|---|---|
| `CLAUDE.md` only | Anthropic default | 1 | Empirical (60k+ repos) |
| `AGENTS.md` only | Sourcegraph/OpenAI 2025 → Linux Foundation | 1 | Empirical (60k+ repos) |
| `CLAUDE.md` + `.claude/skills/*/SKILL.md` | Anthropic | 1 + N | Empirical (Anthropic's own skills) |
| `product.md / structure.md / tech.md` | Kiro (AWS) | 3 | Empirical (Kiro shipping) |
| `requirements.md / design.md / tasks.md` | Spec-kit (GitHub) + Kiro phases | 3 (per feature) | Empirical (spec-kit shipping) |
| `CLAUDE.md / SOUL.md / CONTEXT.md` | Jarvis | 3 | **Novel** |
| `domain-terms.md` (living glossary) | DICE 2025 (Spec Ambiguity Resolver) | +1 | Emerging |

### What mature setups split, what they keep monolithic

**Universally split:**
- Domain glossary vs process rules (DDD ubiquitous language → `domain-terms.md`).
- General rules vs spec/task (Kiro, spec-kit — but per-feature, not per-project).
- User-level vs project-level (CLAUDE.md hierarchy, Cursor user vs workspace).

**Universally monolithic:**
- Process rules + identity together. Most setups bundle "personality" into CLAUDE.md as tone instructions. Jarvis's split here is **novel**.

### Verdict on the Jarvis split

| Dimension | Verdict | Evidence |
|---|---|---|
| `CLAUDE.md` (rules) | Standard | Anthropic, alexop, ETH study |
| `CONTEXT.md` (domain) | Aligned with DDD/DICE | Martin Fowler, port.io, DICE 2025 |
| `SOUL.md` (identity/behavior) | Novel — no direct precedent | — |
| All three always-loaded | Risk: total ~488 lines exceeds 200-line ceiling × 2.4 | ETH Zurich, alexop, Anthropic |

**Strong evidence for splitting CLAUDE.md from CONTEXT.md.** Glossary changes on different cadence than process rules — the `/grill` glossary-growth model is well-aligned with DDD.

**Weak-but-defensible evidence for SOUL.md as a separate file.** The split helps when:
- Identity needs to survive `/end --quick` reconciliation (it does — that's load-bearing).
- Personality vs behavior need different review cadences (the 2026-05-18 strip suggests yes).
- Multi-agent rollout is on the roadmap (per CLAUDE.md "single currently").

**Risk:** total context load is 2.4× the empirically-validated ceiling. Either tier the loading (only CLAUDE.md always-load; CONTEXT.md on `/grill` trigger; SOUL.md on identity-relevant turns) or accept some sections will be silently ignored.

---

## 5. Docs-as-code with drift detection

### Production tools (2025-2026)

| Tool | Approach | Verdict for Jarvis |
|---|---|---|
| **Drift** (Fiberplane) | Tree-sitter AST fingerprint anchored to git commit; CI gate via `drift check`; symbol-level `#Name` anchors | **Highest fit.** Aligns with "docs claim X about code path Y" pattern jarvis already uses. |
| **FreshProbe** | HTTP/data freshness verification before agent acts | Tangential — solves data freshness, not doc-vs-code. |
| **Agent Skill Creator** | Per-skill `last_reviewed` + `review_cadence`; staleness checker | Pattern fits SKILL.md frontmatter style. Trivial to add. |
| **Cortex TMS** | Git-based: compare doc mtime vs code mtime in same path | Cheap signal; high false-positive rate on its own. |
| **Vale** | Prose linter (style/grammar) | Not drift detection — different problem. |

### Anthropic's "Auto Dream" pattern (claim → grep)

Not productized but referenced in agent-best-practices guides 2025. Pattern:
1. Extract claims from docs (LLM classifier).
2. For each claim, derive a grep/ast query against code.
3. Run the query; if zero matches, the claim is suspect.

**Implementation cost:** non-trivial. Drift (Fiberplane) is the productized version of this idea, scoped to "anchored code snippets".

### Recommendation pattern for Jarvis

Three-tier signal:
- **Cheap (free):** git age — flag any always-load doc untouched >90d while related code churned. One cron task.
- **Medium:** Drift anchors on load-bearing sections — anchor `CONTEXT.md` glossary terms to the code symbols they describe.
- **Expensive:** Claim-extract + grep — overkill for solo-dev volume; revisit if doc volume 3×.

---

## 6. Glossary discipline as agent grounding

### Linguistic foundation

DDD's **ubiquitous language** (Eric Evans 2003, Fowler bliki) is the canonical answer: one shared vocabulary across code, docs, conversation, in a bounded context. The 2025-2026 "DICE" framing (Domain-Integrated Context Engineering, port.io) extends this to agents: glossary is what *grounds* an agent so it doesn't drift into generic LLM phrasing.

### Production 2025-2026 patterns

- **Spec Ambiguity Resolver (sdd-glossary skill on skills.rest):** living `domain-terms.md` as single source of truth; LLM flags ambiguous terms; human resolves; resolutions become anchors for future artifacts.
- **DICE:** connects domain glossary to operational metadata (service status, ownership) so agents act with current state.
- **Cynefin** (not in 2025 search) — the framing-vs-glossary distinction maps roughly onto "CONTEXT.md describes the complex/complicated domain we are in."

### Jarvis CONTEXT.md vs the field

CONTEXT.md (188 lines, glossary + invariants + architectural shape, grown via `/grill`) matches the production pattern almost exactly. The **`/grill` inline-grow mechanism is novel and well-aligned** — it's the human-in-the-loop step that DICE/Spec-Ambiguity-Resolver formalize as "human clarifies the intent."

**One gap:** no claim-anchoring. CONTEXT.md says e.g. "milestones group ≥2 slices" — but no automated check that the codebase enforces this. Drift (Fiberplane) is the productized fix.

---

## 7. ADRs vs queryable decision log

### Industry default (still): markdown ADRs

ADRs are the dominant pattern. AWS, Google Cloud, Microsoft Azure, Martin Fowler — all default to `docs/adr/NNNN-title.md`. Tooling:
- **adr-tools** (Nat Pryce, bash) — most-cited, lowest-friction.
- **log4brains** (thomvaill, Node) — adds web publishing; January 2025 evidence of continued adoption (Commanded Ecosystem ADR).
- **adr-viewer**, **adr-log** — single-purpose ancillary tools.

**Queryable advantage:** none of these answer "what decisions did we make about X in the last 90 days" without grep. Append-only by convention.

### Jarvis's choice: `record_decision` MCP

**Pros:**
- Queryable by topic, date, scope, project — `memory_recall(query="X")` returns brief-mode UUIDs.
- UUIDs become foreign keys — outcome→decision joins (per user-level CLAUDE.md §3) catch reasoning vs execution failures.
- No per-decision file commit ceremony.

**Cons:**
- **No one but Jarvis can query it.** A future contributor with Claude Code can — anyone else cannot.
- **No human review surface.** Markdown ADRs get PR review; `record_decision` calls don't.
- **No web publication path.** log4brains-style read-only browsing is gone.

### Which actually gets queried by agents in practice?

Empirically (alexop.dev, dev.to articles, ETH Zurich findings): **markdown ADRs rarely get re-read by agents** after writing. They serve as commit-message-with-rationale for humans. The 200-line ceiling + auto-loading conflict means agents skim them shallowly.

`record_decision` calls being explicit lookups via `memory_recall` likely have **higher actual usage** in Jarvis sessions than `docs/adr/` would have. Whether that's sustainable depends on hook discipline (recall-before-deciding gate).

### Recommendation

Keep `record_decision` as primary. Add one cheap hedge: **a quarterly dump-to-markdown** — script that exports recent decisions to `docs/decisions/YYYY-QN.md`. Cost: 30 min once. Benefit: human-reviewable archive, search engine indexable, survives Supabase outage.

---

## 8. In-file vs reference docs

### The tradeoff

| Choice | Cost | Benefit |
|---|---|---|
| Inline in CLAUDE.md | Every-session token rent; ceiling pressure | Always available; no fetch cost mid-task |
| Pointer to `docs/X.md` | Fetch cost when needed; risk of "skill never triggers" | No per-session rent; richer content |
| Skill (SKILL.md) | ~80 tokens per-skill metadata cost; auto-activation risk | True progressive disclosure |

### Empirical anchor (alexop.dev)

> Skills were never invoked in 56% of test cases, producing zero improvement over baseline. (Cited Vercel evals.)

Implication: **`IMPORTANT: read docs/X.md when Y` directives in CLAUDE.md outperform skill auto-activation for predictable loading.** Skills are better when "Claude should *decide* to use this"; pointers are better when "Claude must always check this in scenario Y."

### Jarvis decision tree

```
Will rule fire in >50% of sessions?           → inline in CLAUDE.md
Will rule fire on specific keyword/path?      → pointer with IMPORTANT directive
Will rule fire after explicit user trigger?   → skill
Will rule never auto-trigger, only reference? → docs/ standalone, no pointer
```

Jarvis's current bias is toward inline (everything in CLAUDE.md). Move 2-3 sections to pointers if the 200-line ceiling bites.

---

## 9. Doc maintenance signals

### Signal hierarchy (cheap → expensive)

1. **Git mtime delta** — `doc_mtime` vs `related_code_mtime`. Free. High false-positive rate alone.
2. **Last-touched-by-agent age** — record when SessionStart hook last surfaced the doc. >30 sessions without re-read = candidate for removal.
3. **Claim count vs grep-confirmed count** — extract assertions, verify against code. Anthropic Auto Dream pattern. Expensive but high signal.
4. **Outcome-attribution joins** — `record_decision.memories_used` → outcome failures. If decisions repeatedly cite a doc and outcomes are bad, that doc is misleading.
5. **Drift (Fiberplane)** — AST-fingerprint anchors. Mid-cost, deterministic.

### What Jarvis already has

- `record_decision.memories_used` → outcome→decision joins (per user-level CLAUDE.md §3). Signal 4 is **operational**.
- SessionStart hook surfaces always-load + topic-recall. Could log "doc X surfaced N times last week" (signal 2). Currently not done.

### What's missing

- No code-vs-doc mtime check (signal 1). Two lines of cron.
- No claim-extraction (signal 3). Skip until volume justifies.
- No Drift anchors (signal 5). Could add to `CONTEXT.md` glossary terms first — they have the strongest claim-to-code mapping.

---

## 10. Multi-project doc reuse

### Patterns

| Hierarchy | Source | Mechanism |
|---|---|---|
| User-global → project-local | Claude Code | `~/.claude/CLAUDE.md` + repo `CLAUDE.md`, both merged at session start. Project overrides user on conflict. |
| User-global → workspace → folder | Cursor | `~/.cursor/rules/` → `.cursor/rules/` → globbed `.mdc` |
| Team → user → repo | GitHub Copilot | Custom instructions, three-level hierarchy |
| Org → project | AGENTS.md | Single file, but symlink/imports per repo. No org-level mechanism. |

### Jarvis pattern

User-level mirror approach: `<jarvis-repo>/.claude-userlevel/CLAUDE.md` is source of truth; `install.ps1 -Apply` propagates to `~/.claude/CLAUDE.md` (per the user-level CLAUDE.md header). This is **idiosyncratic but defensible** — it treats user-level config as a tracked artifact, which is rare and a strength.

Risk: install.ps1 must run on every device after every edit. The "don't edit the mirror" rule is necessary because there's no enforcement.

### Reference comparison

- **Cursor's** approach: user-level rules in `~/.cursor/rules/`, syncs across devices via Cursor's cloud. No artifact in repo.
- **Jarvis's** approach: artifact in repo, manual propagation. Higher Git hygiene, lower auto-sync.

Trade-off is reasonable for solo dev across 3 devices. Probably not how a team would do it.

---

## 11. Doc-driven agent identity — personality vs behavior

### Research finding (2025)

The ACL 2025 paper "Dynamic Personality in LLM Agents" + the "Architecting Prompts for Agentic Systems" 2025 work converge on:

**Personality elements** (tone, role, formality) shape *style*. Behavior rules (guardrails, error-handling, escalation triggers) shape *reliability*.

> Effective system prompts require both personality definition and explicit behavioral rules working in concert.

But the two have different sensitivity profiles:
- **Personality changes**: small prompt changes → large tone shifts. Sensitive but visible.
- **Behavior-rule changes**: small phrasing changes → silent reliability shifts. Sensitive *and invisible*. Production systems version & A/B test these.

### Constitutional AI vs system prompt

Constitutional AI (Anthropic 2022→2025) bakes high-level principles into training. System prompts then steer at runtime. The **2024 Deliberative Alignment critique** of CAI: principles can conflict, leading to bad behavior — the spec isn't encoded *in* the model, only its training data.

Implication: behavior rules in SOUL.md are **runtime steering on top of training-baked priors.** They're effective at the margin, not at overriding strongly-trained behaviors. (Anyone who's tried to make Claude swear knows this.)

### Strip-the-personality decision (2026-05-18)

**Supported by:**
- 2025 system-prompt research: personality is *style*, behavior is *reliability*. They benefit from different review cadences.
- alexop.dev anti-bloat: persona prose is the single biggest CLAUDE.md bloater outside style-rule duplication.
- 200-line ceiling: personality prose tends to grow without producing reliability improvements.

**Risk:**
- Some "personality" entries are actually behavior rules in disguise ("push back on bad ideas" reads as personality but functions as a reliability constraint). Audit before stripping.

**Recommendation:**
- Keep an explicit `Behavior rules` section in SOUL.md.
- Move surviving personality lines to a sub-section `Tone (advisory, not load-bearing)`.
- Cross-link from CLAUDE.md only to the behavior section, not the tone section.

---

## PROPOSALS

| # | Proposal | Source | Priority hint | Notes |
|---|---|---|---|---|
| P1 | Add quarterly dump-to-markdown script: export `record_decision` rows to `docs/decisions/YYYY-QN.md` | §7 | medium | 30 min once. Human-reviewable archive, search-engine indexable, survives Supabase outage. |
| P2 | Audit CLAUDE.md against 200-line ceiling; move 2-3 sections to pointer-pattern (`IMPORTANT: read docs/X.md when Y`) | §2, §8, alexop.dev | high | Currently 197 lines = at the ceiling. Engineering posture section is a candidate to extract. |
| P3 | Tier CONTEXT.md loading: split "always-load invariants" (≤50 lines) from "domain glossary" (loaded on `/grill` or term mention) | §3, §4 | high | Total session load 488 lines = 2.4× empirical ceiling. CONTEXT.md is the heaviest extraction candidate. |
| P4 | Add Drift (Fiberplane) anchors to CONTEXT.md glossary terms pointing to code symbols | §5, §9 | medium | Tree-sitter AST fingerprints catch glossary→code drift in CI. Skill auto-install supports Claude Code. |
| P5 | Audit SOUL.md split: separate `Behavior rules` (load-bearing) from `Tone (advisory)`; cross-link only to behavior | §11 | high | Aligns with 2026-05-18 decision; prevents tone drift from leaking into reliability. |
| P6 | Add cheap mtime-drift signal: cron compares doc mtime vs related code mtime, flags >90d delta | §9 | low | Free; high false-positive but cheap to skim weekly. |
| P7 | Treat `llms.txt` as router-only; ship the minimal version (≤50 lines) per existing deep-dive | §1, deep-dive-llms-txt.md | low | Already decided; just execute. ~30 min job. |
| P8 | Adopt SKILL.md frontmatter `last_reviewed` + `review_cadence` for load-bearing project docs | §5 (Agent Skill Creator) | low | Trivial schema add; surfaces staleness without claim-extraction cost. |
| P9 | Consider `AGENTS.md` symlink to `CLAUDE.md` for cross-tool reach (Cursor, Codex, Copilot can read same rules) | §4, hivetrail.com, blink.new | low | Hedge against future tool-switch. One symlink, zero drift. Skip if multi-tool isn't planned. |
| P10 | Add CONTEXT.md ↔ code-symbol bidirectional links for glossary terms (in code: `# domain-term: <name>`; in CONTEXT: code path pointer) | §6, DDD/DICE | medium | Mechanical bidirectional grounding; helps grep-based verification and human-onboarding both. |

---

## Don't-do list

1. **Don't ship `llms-full.txt`.** Audience is empty for jarvis; maintenance is real; the four key docs are already markdown in-tree.
2. **Don't restate decisions in PR/issue bodies.** Decisions go to `record_decision`; PR bodies decay; the queryable log doesn't. (Already in CLAUDE.md — restated here because it's the single biggest doc-drift accelerator.)
3. **Don't auto-generate `llms.txt` or `CONTEXT.md`.** Generators earn out at higher doc volume than jarvis has. Hand-maintained is cheaper at this scale, and avoids "fix the generator" rabbit holes.
4. **Don't grow CONTEXT.md past 200 lines without tiering.** Empirical ceiling is real. If `/grill` keeps adding, split by domain (`context/memory.md`, `context/orchestration.md`) before crossing.

---

## Sources

### llms.txt and adoption
- [llms.txt directory](https://directory.llmstxt.cloud/) — community directory by industry
- [Who is Using llms.txt? Adoption in 2025 (llms-text.com)](https://www.llms-text.com/blog/sites-using-llms-txt) — 784+ verified sites
- [State of llms.txt in 2026 (aeo.press)](https://www.aeo.press/ai/the-state-of-llms-txt-in-2026)
- [llms.txt skepticism (Mintlify)](https://www.mintlify.com/blog/what-is-llms-txt)
- [Is llms.txt Dead? (llms-txt.io)](https://llms-txt.io/blog/is-llms-txt-dead)

### CLAUDE.md / AGENTS.md / SKILL.md
- [Stop Bloating Your CLAUDE.md (alexop.dev)](https://alexop.dev/posts/stop-bloating-your-claude-md-progressive-disclosure-ai-coding-tools/)
- [Best practices for Claude Code (Anthropic)](https://code.claude.com/docs/en/best-practices)
- [Memory & hierarchy (Anthropic)](https://code.claude.com/docs/en/memory)
- [I Wrote 200 Lines of Rules and Claude Ignored Them (dev.to)](https://dev.to/minatoplanb/i-wrote-200-lines-of-rules-for-claude-code-it-ignored-them-all-4639)
- [5 Patterns That Make Claude Code Follow Your Rules (dev.to)](https://dev.to/docat0209/5-patterns-that-make-claude-code-actually-follow-your-rules-44dh)
- [AGENTS.md vs CLAUDE.md: 60,000+ Real Repos (The Prompt Shelf)](https://thepromptshelf.dev/blog/agents-md-vs-claude-md/)
- [Agent READMEs: Empirical Study of Context Files (arXiv 2511.12884)](https://arxiv.org/html/2511.12884v1) — ETH Zurich et al, Feb 2026
- [Evaluating AGENTS.md (arXiv 2602.11988)](https://arxiv.org/html/2602.11988v1)
- [New Research Reassesses AGENTS.md (InfoQ, Mar 2026)](https://www.infoq.com/news/2026/03/agents-context-file-value-review/)

### Progressive disclosure / skills
- [Agent Skills: Progressive Disclosure (swirlai newsletter)](https://www.newsletter.swirlai.com/p/agent-skills-progressive-disclosure)
- [Claude Agent Skills: First Principles Deep Dive (leehanchung)](https://leehanchung.github.io/blogs/2025/10/26/claude-skills-deep-dive/)
- [Anthropic: Equipping agents with skills](https://www.anthropic.com/engineering/equipping-agents-for-the-real-world-with-agent-skills)
- [Cursor Rules vs Skills vs Commands (ibuildwith.ai)](https://www.ibuildwith.ai/blog/cursor-rules-skills-and-commands-oh-my-when-to-use-each/)

### Spec-driven / context engineering
- [Understanding Spec-Driven Development: Kiro, spec-kit, Tessl (martinfowler.com)](https://martinfowler.com/articles/exploring-gen-ai/sdd-3-tools.html)
- [Kiro: agentic SDD](https://kiro.dev/blog/kiro-and-the-future-of-software-development/)
- [Context Engineering (LangChain blog)](https://blog.langchain.com/context-engineering-for-agents/)
- [Karpathy-inspired wiki pattern (MindStudio)](https://www.mindstudio.ai/blog/karpathy-llm-wiki-knowledge-base-pattern)

### Drift / docs-as-code
- [Drift documentation linter (Fiberplane)](https://fiberplane.com/blog/drift-documentation-linter/)
- [Cortex TMS (github)](https://github.com/cortex-tms/cortex-tms)

### DDD / glossary
- [Ubiquitous Language (Martin Fowler bliki)](https://martinfowler.com/bliki/UbiquitousLanguage.html)
- [sdd-glossary skill (skills.rest)](https://skills.rest/skill/sdd-glossary)
- [DICE: Domain-Integrated Context Engineering (port.io)](https://www.port.io/glossary/domain-integrated-context-engineering-dice)
- [Removing Ambiguity with Spec-Driven Development (Daniel Schleicher)](https://www.danielschleicher.com/software/engineering,/ai,/spec-driven/development/2026/01/04/removing-ambiguity-with-spec-driven-development.html)

### ADRs
- [Architectural Decision Records (adr.github.io)](https://adr.github.io/)
- [log4brains (GitHub)](https://github.com/thomvaill/log4brains)
- [AWS Prescriptive Guidance on ADRs](https://docs.aws.amazon.com/prescriptive-guidance/latest/architectural-decision-records/adr-process.html)
- [bliki: Architecture Decision Record (Martin Fowler)](https://martinfowler.com/bliki/ArchitectureDecisionRecord.html)

### Personality vs behavior / Constitutional AI
- [Architecting Prompts for Agentic Systems (Medium / JB)](https://medium.com/@jbbooth/architecting-prompts-for-agentic-systems-aligning-ai-behavior-with-human-expectations-25b689b3b8f6)
- [Dynamic Personality in LLM Agents (ACL 2025)](https://aclanthology.org/2025.findings-acl.1185.pdf)
- [Claude's Constitution (Anthropic)](https://www.anthropic.com/news/claudes-constitution)
- [Claude 4 System Card (May 2025)](https://www.anthropic.com/claude-4-system-card)

### Related Jarvis research
- `docs/research/deep-dive-llms-txt.md` (2026-05-06)
- `docs/research/agent-dev-practices-sweep-2026-05-06.md`
- `docs/research/deep-dive-spec-driven-development.md`
