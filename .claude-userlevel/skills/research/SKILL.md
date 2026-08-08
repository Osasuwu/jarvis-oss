---
name: research
description: "Investigate a topic, validate decisions, compare options, or run autonomous discovery. Absorbs intel and nightly-research. Trigger: 'исследуй', 'research', 'изучи', 'что лучше', 'сравни'."
version: 6.0.0
---

# Research

Investigate a topic and produce a sourced, confidence-rated finding.

**Mode is determined by the argument shape, not a flag:**

- **A topic is supplied** (`/research <topic>`, or principal asks a specific question) → interactive: focused on that one query.
- **No topic supplied** (`/research`, or scheduled run) → discovery: load context, find genuine gaps, pick top 3, research each.

The pipeline below works for both. Steps that only apply to one shape are flagged inline.

## Tooling

Firecrawl is reachable again as a **claude.ai Connector (OAuth)** — the old `npx firecrawl-mcp` + API-key registration in `.mcp.json` is dead, don't restore it. Everything below was measured against the live connector on 2026-07-30; the numbers are observed, not estimated.

| Need | Call | Cost | Context |
|---|---|---|---|
| Map the source space | `firecrawl_search(query, limit=3)` | 2 cr | ~1 KB / 3 results |
| Pull one page | `firecrawl_scrape(url, formats=["summary"], onlyMainContent=true, maxAge=604800000)` | 1 cr | ~2 KB |
| Papers — Channel 3 | `firecrawl_research_search_papers(query, k, from)` | cheap | abstracts inline |
| Passages from one paper | `firecrawl_research_read_paper(paperId, question, k)` | cheap | targeted |
| Issues / PRs / READMEs — Channel 4 | `firecrawl_research_search_github(query, k)` | cheap | full issue bodies |
| Structured comparison across URLs | `firecrawl_extract(urls, schema)` | varies | schema-bounded |
| Library / framework / SDK docs | `context7`: `resolve-library-id` → `query-docs` | — | prefer over web search |
| Connector down | `WebSearch` + `WebFetch` with a narrow `prompt` | — | fallback only |

### Hard rules

**Never attach `scrapeOptions` to `firecrawl_search`.** Measured: 3 results with `scrapeOptions.formats=["summary"]` returned **233 283 chars**, because the `description` field gets the full page markdown (228 958 chars on a single HN thread) while the requested `summary` sits beside it at 1 184 chars. Search to map, then scrape the one or two pages actually worth the context.

**`firecrawl_agent` / `firecrawl_agent_status` are banned.** Verified broken 2026-07-30: the agent's execution sandbox has no network egress — `Network name resolution error` on both `news.ycombinator.com` and `anthropic.com`, 2 of 2 jobs, `creditsUsed: 0` — while a direct `firecrawl_scrape` on that same HN URL succeeded moments earlier. The dangerous part is not the failure, it's the silence: when it cannot fetch, it answers from parametric knowledge **without saying so**. Test job #1 returned a complete, well-structured, schema-conforming research report with every requested `source_url` field quietly omitted; it admitted the failure only when the prompt explicitly ordered it to report fetch errors. That output would pass this skill's formatting checks with zero grounding behind it. Before lifting the ban, re-run the seeded-URL probe below; if it ever works, still require a per-item `source_url` and discard any finding that lacks one.

```
firecrawl_agent(urls=[<known-good URL>], prompt="Quote two verbatim sentences from the page and give the exact URL. If you cannot fetch it, say so instead of answering from prior knowledge.")
```

**Oversized results land in a file, not in context.** Any result over the cap is written to `…/tool-results/*.txt`. Slice it with `python -c 'print(open(P).read()[A:B])'` or hand it to a subagent — never `Read` the whole file.

**Per-run budget:** 2–3 `firecrawl_search` + at most 2 `firecrawl_scrape`. The `firecrawl_research_*` tools are cheap and dense — they do not count against that budget, prefer them.

## 4-Channel Mandatory Intake Protocol

All non-trivial research must include explicit coverage from **all four channels**. Memory recall does not substitute for external channels (per decision [`6fd2df1d-defc-440d-ba30-71880409e533`](https://memory.example/decisions/6fd2df1d-defc-440d-ba30-71880409e533)). Research is incomplete if any channel is empty without explicit owner waiver.

### Channel 1: Users
**End-user experience** — how people actually feel using the thing, what they trip over.

**Where to look:** Reddit (r/Python, r/MachineLearning, r/devops), Hacker News discussions, Medium posts, dev blogs, Twitter/X technical discussions, Stack Overflow threads, community Discord/Slack.

**Call:**
```
firecrawl_search(query="<tool X> real experience pain points",
                 includeDomains=["reddit.com", "news.ycombinator.com", "stackoverflow.com"],
                 limit=3)
```
`includeDomains` and `excludeDomains` are mutually exclusive — pick one. Then `firecrawl_scrape(formats=["summary"])` on at most the single densest thread; comment threads are where the 200 KB blowups live.

**Example queries:** `"tool X" real experience` · `"tool X" pain points OR frustrations` · `"I tried X and..." OR "X broke our..."`

### Channel 2: Specialists
**Domain specialist opinion** — expert blogs, engineering posts from labs, thoughtful retrospectives.

**Where to look:** 
- Expert individual blogs: Dan Luu, Julia Evans, Will Larson, David Beazley, Nora Codes
- Lab/company engineering blogs: Anthropic, OpenAI, DeepMind, Latent Space guest posts, Eugene Yan, David Pocock, Simon Willison
- Conference talks + write-ups
- Technical RFC or discussion threads from maintainers

**Call:**
```
firecrawl_search(query="<topic X> lessons learned",
                 includeDomains=["simonwillison.net", "danluu.com", "jvns.ca", "lethain.com", "anthropic.com"],
                 tbs="qdr:y", limit=3)
```
Swap the domain list per topic — it is the highest-leverage knob in this channel. If the topic is a library / framework / SDK / CLI, hit `context7` **first** (`resolve-library-id` → `query-docs`): live version-current docs beat a blog post and beat your own parametric memory.

**Example queries:** `"topic X" engineering blog OR "lessons learned"` · `"topic X" practitioner retrospective`

### Channel 3: Data
**Quantitative data / research** — arxiv papers, conference papers, benchmark numbers, published metrics.

**Where to look:** arxiv.org, ACM Digital Library, Papers With Code, GitHub repo benchmarks, published research, performance comparisons with citations.

**Call — do not use `site:arxiv.org` web search for this, it is strictly worse:**
```
firecrawl_research_search_papers(query="<topic X> empirical evaluation", k=5, from="YYYY-MM-DD")
firecrawl_research_read_paper(paperId="<arxiv id>", question="<the specific number you need>", k=5)
firecrawl_research_related_papers(paperId="<arxiv id>")   # citation-graph walk from the best hit
```
`search_papers` returns canonical arxiv IDs with **full abstracts inline** — usually enough to close the channel with one call, no scrape needed. Reach for `read_paper` only when you need a specific figure or number out of the body.

**Example queries:** `"topic X" benchmark OR measurement study` · `"topic X" empirical study OR systematic review`

### Channel 4: Adversarial
**Failure modes & criticism** — post-mortems, GitHub issues, "X considered harmful" pieces, retrospectives, why something failed.

**Where to look:** Post-mortem archives (PostmortemDB, GitHub issue threads), retrospectives, "lessons learned" + negative outcomes, abandoned projects + why, critical technical discussions, conference talks on failures.

**Call — this is the strongest tool in the whole set, lead with it:**
```
firecrawl_research_search_github(query="<verbatim error string or symptom>", k=5)
```
It returns **full issue and PR bodies**, so someone else's reproduction and root-cause analysis arrive intact rather than as a search snippet. Query it with the literal error text, not a paraphrase. Proven in the field: this channel resolved the standing `tools.143.custom.input_schema does not support oneOf/allOf/anyOf` blocker in one call by surfacing three independent repos hitting the same MCP-server schema bug.

Web search still covers the non-GitHub half:
```
firecrawl_search(query="<topic X> postmortem OR 'considered harmful' OR 'why we moved off'", limit=3)
```

**Example queries:** `"topic X" post-mortem OR "what went wrong"` · `"topic X" considered harmful OR critique` · `"no longer using" OR deprecated "topic X"`

## Scope

**New research only.** This 4-channel protocol applies to research initiated after this decision. Existing research-pass-style memories (e.g. `research_aihero_principles`, `research_deep_dive_synthesis_2026_05_06`) are grandfathered — no retrofit required. They serve as reference; future research on related topics will apply the protocol fresh.

## Pipeline

### 1. Determine the question(s)

**Topic supplied:** classify it.
- **Decision validation** → focus on trade-offs, risks, real-world experience.
- **Knowledge gap** → authoritative explanations, practical examples.
- **Comparison** → objective criteria, benchmarks, community consensus.

**No topic (discovery):** load context first.

```
memory_recall(type="decision", limit=10)
memory_recall(query="working_state", type="project", limit=3)
```

Also scan recent GitHub issues for each repo in `config/repos.conf`.

Find genuine gaps from that context:
- Problems flagged as unsolved
- Decisions made without sufficient research
- Patterns that keep breaking
- Capabilities mentioned but not explored

Score `impact × urgency`. Pick **top 3**. Each becomes a specific, concrete research question. Bad: "research AI agents". Good: "how do iterative planners detect premature convergence?"

### 2. Search

Two layers, in this order. **You orchestrate them — there is no tool that does this step for you** (see the `firecrawl_agent` ban under Tooling).

**Layer A — the dense channels, first.** They answer more per call and cost less context:

```
firecrawl_research_search_papers(query=..., k=5)     # Channel 3
firecrawl_research_search_github(query=..., k=5)     # Channel 4
context7 resolve-library-id → query-docs             # if the topic names a library
```

**Layer B — web search, to close Channels 1 and 2.** Bare `firecrawl_search` with a per-channel `includeDomains`, 2–3 queries at different angles. Never with `scrapeOptions`.

Then scrape at most **2 pages per topic**, and only where the search result genuinely didn't settle the question:

```
firecrawl_scrape(url=<url>, formats=["summary"], onlyMainContent=true, maxAge=604800000)
```

`formats:["summary"]` — not `["markdown"]`. Measured ~2 KB vs a full page, and the condensation quality is high enough for every use in this pipeline. `maxAge` accepts a week-old cached copy for 0 additional cost; drop it only when the page's freshness is itself the finding.

Prioritize: official docs, primary sources, maintainer threads, benchmarks with methodology.
Skip: SEO spam, listicles, anything >2 years old on a fast-moving topic.

Fallback if the connector is down: `WebSearch` + `WebFetch` with a narrow `prompt` — then say so in the report and let the research-pass gate record the `infrastructure-blocked` waiver rather than pretending to full coverage.

### 3. Analyze

1. Claims in multiple independent sources = strong signal
2. Note contradictions between sources
3. Distinguish facts (documented, benchmarked) from opinions
4. For technical decisions: real-world usage at similar scale
5. **Channel gaps:** if a channel yields <2 sources, flag in output and propose owner waiver or extended search

### 4. Output

One report per topic (single report when topic supplied, three when discovery):

```markdown
## Summary
One paragraph, lead with recommendation if it's a decision.

## Key Findings
- **Finding** — explanation [source]

## Trade-offs & Risks
- **Risk** — when it matters, mitigation

## Alternatives
- **Alternative** — why rejected or when better

## Sources
1. Title — URL — what it contributed

## Confidence: N/100
One sentence: what makes this confident or uncertain.
```

### 5. Save

Save to Supabase if finding is significant:

```
memory_store(type="reference", name="research_{slug}", description="...", content="...", source_provenance="skill:research")
```

If finding is actionable → create GitHub issue in appropriate repo:

```
gh issue create --repo <R> --title "[RESEARCH] <topic>" --body "..."
```

**Discovery-only**: also write a dedup marker so the next scheduled run doesn't repeat topics:

```
memory_store(type="project", name="research_last_run", content="{date} — topics: {t1}, {t2}, {t3}", source_provenance="skill:research")
```

Check for duplicate research-spawned issues before creating new ones.

### 6. Remove `needs-research` on success

When `/research` was triggered against a specific issue carrying the `needs-research` label and the research produces an actionable answer (recommendation written into the issue, decision recorded, or follow-up issue created), remove the label as the final terminal step:

```bash
gh issue edit <N> --repo <owner/repo> --remove-label "needs-research"
```

This is the contract that lets `/delegate`'s pre-dispatch gate (issue #642) trust that an unlabelled issue is genuinely research-clean. Skipping the removal leaves the issue stuck in `status:owner-queue` forever. If `/research` exits without a confident answer (confidence <50), leave the label in place — the issue still needs work.


## Quality rules

- Non-trivial claims: 3+ independent sources when available
- Every finding references specific sources, with channel attribution
- Source conflicts → list both sides
- Call out unknowns and weak evidence
- If confidence <50 → recommend follow-up

**Grounding rule.** Every finding must trace to a URL that *this run actually retrieved*. A claim you recognize as true but did not fetch is not a finding — it goes in the report as an explicitly-labelled prior, or it doesn't go in. The structure of this report is trivially satisfiable from parametric knowledge alone; the format proves nothing. That is precisely how `firecrawl_agent` failed its test, and the same failure mode is available to you.

**Субстрат guideword.** If a finding is actionable *because it should become a standing rule the agent follows* (not just a one-off fix), the report's recommendation must name the intended carrier — same order as DOCTRINE.md → *Baseline carrier selection* (code/CI gate → PreToolUse deny hook → file `@import` → `.claude/rules/` + `paths:` → hook-inject → retrieval → `always_load`). "Add to memory" or "add to CLAUDE.md" is not itself a recommendation — say why that carrier, not a cheaper one, fits the rule's violation cost.
