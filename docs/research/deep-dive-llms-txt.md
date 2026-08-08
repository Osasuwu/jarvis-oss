# Deep dive: llms.txt and LLM-friendly docs for Jarvis

**Date:** 2026-05-06
**Author:** subagent (deep-dive on item 4 of `docs/research/agent-dev-practices-sweep-2026-05-06.md`)
**Scope:** evaluate `llms.txt` + Fern-style "LLM-friendly docs" for the `jarvis` and `redrobot` repos. Decision-grade, not implementation.

> **Sourcing note.** Live web fetch (`WebFetch`, `firecrawl_scrape`, `WebSearch`) was denied in this subagent sandbox, so I could not re-verify primary sources page-by-page in this turn. The factual claims below are reconciled against:
> - the background sweep (`docs/research/agent-dev-practices-sweep-2026-05-06.md`, items 3.2 and 9.4),
> - the canonical URLs cited there (Fern post, llmstxt.org, AnswerDotAI/llms-txt),
> - and Jarvis's own training-cutoff knowledge of the spec as of late 2024 / early 2025.
> **Anything I could not independently re-verify is marked with `(unverified — re-check)` and a confidence cap of /3.** A second pass with web access should resolve the open items in §8.

---

## TL;DR

1. `llms.txt` is a one-page markdown index at site root that gives an LLM an annotated map of the site's important content. Cheap to write, narrow upside.
2. The strongest argument is the Fern data point: **HTML wraps can waste up to ~90% of tokens** vs the same content served as flat markdown. For our use case the savings sit on the *consumer* side (whoever fetches our repo via web), not on Claude Code reading the working tree.
3. **For `jarvis`: ship a tiny `llms.txt` at repo root** (≤ 50 lines) pointing at `CLAUDE.md`, `CONTEXT.md`, the design doc, and the SOUL. Confidence **4/5**. It is a 30-min job, low-maintenance, and acts as a stable contract for any future agent (web crawler, downstream MCP, contributor's Claude session) that lands on the repo without our hooks loaded.
4. **For `redrobot`: do not ship unilaterally.** Ask Sergazy. Multi-stack repo, foreign owner, he merges. Confidence **2/5** without his sign-off. A draft proposal he can review is still useful.
5. The **anti-pattern to avoid** is `llms-full.txt` for `jarvis` — it duplicates `CLAUDE.md` + `CONTEXT.md` and adds a sync-burden the solo dev will not pay.

---

## 1. The `llms.txt` spec, distilled

### File and location

- Single markdown file at the **site root**: `https://example.com/llms.txt`. For a code repo with no public site, the convention people have copied is **`/llms.txt` at the repo root** (committed file).
- Plain markdown, no frontmatter, no schema. Parsers grep for the well-known section headings.
- Sister convention: `llms-full.txt` — same location, contains the full text of every important page concatenated, so an LLM can ingest the whole site without crawling. Optional. Heavier maintenance.

### Required structure (per llmstxt.org)

```markdown
# Project Name

> One-paragraph summary of what this project is and who should care.

Optional zero-or-more paragraphs of context the model needs before
following any link below.

## Docs

- [Quickstart](https://example.com/quickstart.md): get from zero to a
  running call in 5 minutes.
- [Concepts](https://example.com/concepts.md): glossary of domain terms.

## Examples

- [Hello world](https://example.com/examples/hello.md): minimal end-to-end.

## Optional

- [Changelog](https://example.com/changelog.md): release notes by date.
```

Rules:
- `#` H1 = project name (required, exactly one).
- `>` blockquote immediately under H1 = elevator-pitch summary (strongly recommended).
- `## Docs`, `## Examples`, etc. — H2 sections, each a flat bulleted list.
- Each bullet: `[Title](URL): one-line description.` The description after the colon is the part the LLM uses to decide whether to follow.
- A `## Optional` section is parser-significant: tools may skip it under token pressure.

### `llms.txt` vs `llms-full.txt`

| | `llms.txt` | `llms-full.txt` |
|---|---|---|
| Purpose | Annotated **index** of key URLs | **Concatenated body** of all key pages |
| Size | tens of lines | tens of thousands of tokens |
| Audience | agent decides what to fetch next | agent ingests everything in one bite |
| Maintenance | low (links + descriptions) | high (rebuild on every doc change — needs a generator) |
| When useful | always, if you have docs at all | sites where users want offline / single-shot ingestion |

### `<llms-ignore>` tags `(unverified — re-check)`

I have a fuzzy memory of an `<llms-ignore>` HTML-comment-style convention for excluding a region of a page from auto-extracted markdown. **I could not verify this in the canonical spec from sandbox.** It may be a Mintlify or Fern extension rather than part of llmstxt.org. Treat as open question §8.

### How tools "parse" it (today, mid-2026)

Empirically, "parsing `llms.txt`" means three different things depending on who's asking:

- **Crawlers / scrapers** (Firecrawl, Tavily, Exa, Cursor's docs ingest) — fetch `/llms.txt` first, use it as a sitemap, follow listed URLs preferentially. Significantly cheaper than spidering.
- **IDE agents** (Cursor "@docs", Copilot's docs index) — owner pastes the URL once; the IDE keeps a local index keyed off the file.
- **Claude Code** — **does not natively parse `llms.txt`** as far as I know `(unverified — re-check)`. It reads files in the working tree (CLAUDE.md, AGENTS.md, README.md). Web pages only enter context via WebFetch / Firecrawl / Context7. So for Claude Code specifically, a *committed* `llms.txt` at repo root is read like any other markdown file — the value comes from its *structure being legible*, not from harness-level support.

This matters for §6: the cost-benefit math is different from "Cursor users."

---

## 2. Adoption — who actually ships one

I cannot enumerate this with confidence from sandbox (no web search). What I know with **/4 confidence**:

- **Anthropic** publishes one — the canonical URL pattern is `https://docs.anthropic.com/llms.txt` (and a heavyweight `llms-full.txt`). Used by Cursor and Claude Code itself when wired up. `(unverified path — re-check)`
- **Cloudflare** (developer docs), **Stripe** (developer docs), **Supabase**, **Vercel / Next.js**, **Pydantic**, **FastHTML / Answer.AI** properties — all listed in spec-adjacent directories I've seen historically. `(unverified — re-check)`
- The spec author **Jeremy Howard / Answer.AI** ships them on `fast.ai`-family properties (canonical reference implementation).
- A community **directory at `directory.llmstxt.cloud`** aggregates known deployments — useful starting point if revisited with web access.

**Tooling that generates `llms.txt`:**
- **`llms-txt`** — official Python lib at `github.com/AnswerDotAI/llms-txt`. Provides a `nbdev`-style generator and parsers.
- **Mintlify / Fern / GitBook** — major docs platforms now emit `llms.txt` and `llms-full.txt` automatically as part of their build. `(unverified — re-check)`
- **`firecrawl`** can both consume and re-emit them.
- **VitePress / Docusaurus / MkDocs** plugins exist; quality varies.

**Do not hallucinate the adopter list when implementing.** Confirm via the directory before quoting any company in a PR description.

---

## 3. Fern's "LLM-friendly docs" principles

Source: `buildwithfern.com/post/how-to-write-llm-friendly-documentation` (cited in sweep §3.2 and §9.4).

The three core claims I am confident were in that post (cross-checked against the sweep's 90%-token-waste paraphrase):

1. **Markdown beats HTML for LLM consumption — by a lot.** Their measurement: HTML chrome (nav, sidebars, scripts, ARIA wrappers, TOC widgets, footer link clouds) can constitute up to **~90%** of bytes on a typical docs page, all of which is noise to an LLM and costs tokens to ship through the context window. Same content as markdown is roughly an order of magnitude smaller. **(/4 — number recalled, page not re-fetched.)**
2. **Drop nav-chrome and "table-of-icons" UI.** Sidebars with 200 menu items, feature-grid landing pages with icon + 3-word labels, "What's new in 0.42 / 0.41 / 0.40" carousels — high information density for humans, near-zero for LLMs because the prose isn't there.
3. **Plain semantic prose > visually-clever layouts.** A boring paragraph that says *"Authentication uses bearer tokens. Pass them in the `Authorization: Bearer <token>` header. Tokens last 24h and rotate via POST /auth/refresh."* beats a 3-column comparison table with checkmark icons. The LLM can also re-format the prose into a table on demand; it cannot recover prose from a sparse icon grid.

Practical consequences they recommend:
- Serve `.md` versions at predictable URLs (`page.html` ⇒ `page.md`).
- Keep one concept per page; don't overload "the auth page" with billing.
- Avoid client-side rendered content for anything semantic.
- Provide `llms.txt` so the LLM doesn't have to guess your IA.

Token-economy implication for Jarvis: the savings accrue **on whoever fetches our docs**, not on us. The Fern argument is strongest for projects whose docs are read by external agents at scale. For `jarvis`, the audience is small (see §6), so the lever is smaller.

---

## 4. Concrete deltas — mock-ups

### 4.1 `jarvis/llms.txt` (proposal)

Repo root file. ~40 lines. Confidence **4/5**.

```markdown
# Jarvis

> Single-principal AI agent for software work, built Claude Code-native
> on top of Supabase memory and a SOUL-driven personality. Solo-dev
> project; primary "user" is the owner across 3 devices, secondary
> consumers are Claude Code sessions reading the repo and external
> agents that land here via search.

This index points at the load-bearing documents. Anything not listed is
either generated, transient (reports/, .claude/worktrees/), or external
dependency (.venv, node_modules).

## Rules and identity

- [CLAUDE.md](https://github.com/Osasuwu/jarvis/blob/main/CLAUDE.md): process rules, conventions, skill routing, what NOT to do. Read this first if you are an agent.
- [config/SOUL.md](https://github.com/Osasuwu/jarvis/blob/main/config/SOUL.md): personality, judgment calibration, working style. Loaded by SessionStart hook.
- [CONTEXT.md](https://github.com/Osasuwu/jarvis/blob/main/CONTEXT.md): domain glossary and invariants. Grown inline through /grill-me.

## Architecture

- [docs/design/jarvis-v2-redesign.md](https://github.com/Osasuwu/jarvis/blob/main/docs/design/jarvis-v2-redesign.md): canonical L0 architecture and pillar split.
- [docs/design/jarvis-architecture-c4.md](https://github.com/Osasuwu/jarvis/blob/main/docs/design/jarvis-architecture-c4.md): C4 diagrams.
- [docs/design/jarvis-flows.md](https://github.com/Osasuwu/jarvis/blob/main/docs/design/jarvis-flows.md): end-to-end flows (perception → decision → action).
- [docs/PROJECT_PLAN.md](https://github.com/Osasuwu/jarvis/blob/main/docs/PROJECT_PLAN.md): pointer index into active milestones.

## Memory and integration

- [mcp-memory/server.py](https://github.com/Osasuwu/jarvis/blob/main/mcp-memory/server.py): the only justified Python in the repo — Supabase-backed memory MCP server, shared with redrobot.
- [.mcp.json](https://github.com/Osasuwu/jarvis/blob/main/.mcp.json): MCP server config; portable across 3 devices.
- [scripts/session-context.py](https://github.com/Osasuwu/jarvis/blob/main/scripts/session-context.py): SessionStart hook — what gets injected into every Claude Code session.

## Optional

- [docs/research/agent-dev-practices-sweep-2026-05-06.md](https://github.com/Osasuwu/jarvis/blob/main/docs/research/agent-dev-practices-sweep-2026-05-06.md): annotated map of the 2026 agent-dev landscape.
- [docs/VISION.md](https://github.com/Osasuwu/jarvis/blob/main/docs/VISION.md): long-horizon ambition, beyond v2.
- [.github/copilot-instructions.md](https://github.com/Osasuwu/jarvis/blob/main/.github/copilot-instructions.md): process rules duplicated for GitHub Copilot's discovery path.
```

Notes on the mock-up:
- **Absolute URLs to `main`.** Relative paths break when an external scraper fetches the file. The cost is one chore on rename — acceptable.
- **No `## Skills` section** — those are discovered by Claude Code from `.claude/skills/`, and listing 30+ of them in `llms.txt` adds noise without unlocking any consumer.
- **No `llms-full.txt`.** Not worth the maintenance for a private-ish repo (see §7).
- **`## Optional` is real.** Polite parsers will skip it under token pressure.

### 4.2 `redrobot/llms.txt` (draft for Sergazy review — DO NOT MERGE WITHOUT HIM)

Multi-stack repo (Python + FastAPI + React + Three.js + MuJoCo). Higher coordination cost. Higher upside if external folks ever land. Confidence **2/5** — the technical mock is fine, the political question is "is this Sergazy's call to make."

```markdown
# Redrobot

> Industrial robot control stack: Python + FastAPI backend, React +
> Three.js frontend, MuJoCo physics simulation. Research project.

## Backend

- [backend/README.md](...): FastAPI service overview, endpoints, auth.
- [backend/CONTRIBUTING.md](...): dev setup, tests, deploy.

## Frontend

- [frontend/README.md](...): React + Three.js viewport.

## Simulation

- [sim/README.md](...): MuJoCo physics, scene definitions, calibration.

## Architecture

- [docs/architecture.md](...): subsystem split and contracts.
- [docs/adr/](...): decision records (if/when adopted).

## Optional

- [docs/research/](...): exploration notes.
```

**Action:** open a discussion with Sergazy: *"Want me to add an `llms.txt` so external agents (and our own Claude/Cursor sessions) get a clean entrypoint? Cost is ~30 min initial + ~5 min per major doc move."* If yes — he picks section list. If no — drop.

---

## 5. Interaction with existing artifacts

`llms.txt` is **a router, not content.** The right division:

| Artifact | Role | Does `llms.txt` duplicate it? |
|---|---|---|
| `CLAUDE.md` | Process rules for the in-repo agent | No. `llms.txt` *links* with a one-liner. CLAUDE.md remains the source of truth. |
| `CONTEXT.md` | Domain glossary | No. Linked from `## Rules and identity`. |
| `config/SOUL.md` | Identity | No. Linked. |
| `README.md` (if/when added) | Human-facing intro | Partial overlap on the elevator pitch. Acceptable — different audiences. README has install instructions, screenshots; `llms.txt` has neither. |
| `docs/adr/` (planned) | Per-decision rationale | No. ADR index gets one bullet under `## Architecture`. |
| `docs/design/*.md` | Design specs | Two or three of the most load-bearing get top-level bullets; the rest stay discoverable by the agent following links. |
| `.github/copilot-instructions.md` | Same as CLAUDE.md, different consumer | Listed in `## Optional` so consumers know it's a duplicate-by-design. |

The discipline that keeps it cheap: **`llms.txt` only stores titles + one-line descriptions + URLs.** All actual content lives elsewhere. A doc rename means editing one URL in `llms.txt`; a doc rewrite means editing zero lines in `llms.txt`.

---

## 6. Solo-dev cost-benefit

Who actually reads a `jarvis/llms.txt`?

| Consumer | Frequency | Value | Counter-argument |
|---|---|---|---|
| Owner's own Claude Code sessions | Every session (already in working tree) | **Low** — SessionStart hook already injects CLAUDE.md, CONTEXT.md, working-state. `llms.txt` is at most a short table-of-contents the agent occasionally re-reads. | Marginal |
| Owner's Cursor / Copilot sessions on the repo | Rare today, possible later | **Medium** — those harnesses *do* parse `llms.txt`. If the owner ever uses Cursor against this repo, the file pays for itself instantly. | "I don't use Cursor here" — true today, cheap insurance for tomorrow |
| External agents (Firecrawl, web scrapers, search-via-LLM users) | When repo is public + searchable | **Medium** — the repo is public on GitHub but not high-traffic. Right now this is ~0 readers. If the project ever gets a blog post or HN moment, the file is the difference between coherent ingest and chaos. | Speculative |
| Future contributors (human) running their own Claude Code | Sparse, but high stakes per event | **High per event** — one hour of orientation collapsed to "agent reads `llms.txt` and is oriented." Same upside as a good README. | This is also what CLAUDE.md does, with overlap |
| Subagents dispatched on this repo | Every `/delegate` | **Already covered** by CLAUDE.md injection. `llms.txt` adds ~zero. | Marginal |

**Verdict:** the file is not load-bearing for the owner *today*. It is load-bearing for **the agentic ingest path** (Cursor / external scrapers / future contributors). The cost is so low — 30 min once, ~10 min/quarter to keep links live — that the asymmetric option-value wins. Confidence **4/5**.

When does it earn its maintenance cost? Three triggers:
1. **First time** the owner opens this repo in Cursor and Cursor asks "what is this project?"
2. **First time** somebody discovers `jarvis` from a public source and asks Claude / ChatGPT about it.
3. **First time** a contributor lands and their AI assistant orients in seconds instead of minutes.

If none of those happen in 12 months, the file cost ~$0.

### When `llms-full.txt` would NOT pay off (for jarvis)

Hard NO. Reasons:
- The repo's "important content" (CLAUDE.md, CONTEXT.md, redesign doc, SOUL.md) is already markdown in-tree. A consumer can fetch the four URLs in `llms.txt` themselves; concatenating them adds no information.
- Any consumer that wants the full bundle can `git clone` cheaper than parsing a multi-MB text blob.
- Maintenance cost is non-trivial: every doc edit needs the bundle regenerated, or the bundle drifts.
- Audience that benefits from one-shot ingest (offline assistants, RAG indexers) is empirically zero for this project.

Revisit if `jarvis` ever ships a public docs site.

---

## 7. Bootstrap proposal — smallest worth-committing version

**Proposal:** ship the §4.1 mock-up exactly, in one PR, with the following constraints:

1. File at `/llms.txt` (repo root). Not `docs/llms.txt`.
2. ≤ 50 lines.
3. **Absolute URLs to `https://github.com/Osasuwu/jarvis/blob/main/...`** — survives external scrape.
4. ≤ 8 bullets in non-Optional sections combined. Ruthless prioritization.
5. PR body explains the spec briefly + links this research doc.
6. **No CI guard yet.** The doc is small enough that drift is low-frequency. If we add one later (issue: "fail CI if `llms.txt` links 404"), it should follow the path-filtered-CI-guards-need-meta-test rule (CLAUDE.md #326).
7. **No generator.** Hand-maintained markdown. The moment we automate this, we're paying maintenance to remove maintenance.

### Anti-patterns to avoid

- **Listing every skill in `## Skills`.** 30+ bullets, low signal, high churn.
- **Listing every ADR / every design doc.** Pick the load-bearing 2-4. The rest stays discoverable.
- **Putting RU-language descriptions.** `llms.txt` consumers are mostly English-trained tooling; mixed language hurts retrieval. Internal CLAUDE.md can stay bilingual; the public router goes English.
- **Checking in `llms-full.txt`.** See §6.
- **Coupling to a build step.** Don't gate the file on `nbdev` / `mkdocs` / anything. Plain commit.
- **Treating `llms.txt` as a replacement for CLAUDE.md.** It is the entry sign, not the manual.
- **Scope creep into "AI manifesto."** The blockquote summary is one paragraph. Not three.

---

## 8. Open questions for owner

1. **Anthropic `llms.txt` exact path** — confirm it serves at `docs.anthropic.com/llms.txt` vs `claude.com/llms.txt` vs neither. Influences the "well-known projects use it" line in any PR description.
2. **`<llms-ignore>` tag** — is it canonical, Mintlify-only, or a hallucination of mine? Affects nothing for our committed-file use case but worth knowing.
3. **Cursor's behavior on a committed `llms.txt`** — does it auto-discover the repo-root file in workspace, or does it only follow web URLs? If only web, the in-repo file is purely a stable contract for whoever fetches `raw.githubusercontent.com/.../llms.txt`.
4. **Should jarvis's `llms.txt` link to private design docs?** All listed files are already public in this public repo, so moot — but worth a one-pass scan before commit to confirm nothing in `docs/design/` references credentials, internal infra, or unredacted memory rows.
5. **Redrobot delegation** — are you comfortable opening a Discussion in Sergazy's repo with the §4.2 draft, or do you want to talk to him first via Telegram? This is a process question, not a technical one.
6. **Sprint slot** — does this fit the next sprint's milestone, or does it land as a Fix>Track inline change (CLAUDE.md #428)? My read: it's a single small file, it's the entire scope, **fix inline**. But if you want a tracking issue for the "evaluate Cursor parsing behavior" follow-up, that's the issue worth opening.
7. **Discoverability outside GitHub** — is jarvis going to get any kind of public landing page? If yes, the calculus shifts toward `llms-full.txt`. If no, ignore.

---

## Confidence summary

| Recommendation | Conf | Notes |
|---|---|---|
| Ship minimal `jarvis/llms.txt` (§4.1, §7) | 4/5 | Low cost, asymmetric upside, anti-patterns clear |
| Skip `jarvis/llms-full.txt` | 5/5 | Audience is empty, drift cost is real |
| Draft `redrobot/llms.txt` for Sergazy review, do not merge unilaterally | 2/5 | Political call, not technical |
| Adopt Fern's markdown-first / no-chrome principle for any future jarvis docs site | 4/5 | The 90% number is the real argument; cheap to honour by default |
| Treat `llms.txt` as **router**, not content (§5 division of labor) | 5/5 | Avoids the duplication trap |
| Do not auto-generate; hand-maintain | 4/5 | Generators only earn out at higher doc volume |
| Re-verify §2 adopter list and §1 `<llms-ignore>` claim with web access before quoting in any PR | 5/5 | Don't ship hallucinated names |
