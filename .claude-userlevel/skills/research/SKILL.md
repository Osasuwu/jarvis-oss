---
name: research
description: "Investigate a topic, validate decisions, compare options, or run autonomous discovery. Absorbs intel and nightly-research. Trigger: 'Ð¸ÑÑÐ»ÐµÐ´ÑÐ¹', 'research', 'Ð¸Ð·ÑÑÐ¸', 'ÑÑÐ¾ Ð»ÑÑÑÐµ', 'ÑÑÐ°Ð²Ð½Ð¸'."
version: 5.1.0
---

# Research

Investigate a topic and produce a sourced, confidence-rated finding.

**Mode is determined by the argument shape, not a flag:**

- **A topic is supplied** (`/research <topic>`, or principal asks a specific question) â interactive: focused on that one query.
- **No topic supplied** (`/research`, or scheduled run) â discovery: load context, find genuine gaps, pick top 3, research each.

The pipeline below works for both. Steps that only apply to one shape are flagged inline.

## 4-Channel Mandatory Intake Protocol

All non-trivial research must include explicit coverage from **all four channels**. Memory recall does not substitute for external channels (per decision [`6fd2df1d-defc-440d-ba30-71880409e533`](https://memory.example/decisions/6fd2df1d-defc-440d-ba30-71880409e533)). Research is incomplete if any channel is empty without explicit owner waiver.

### Channel 1: Users
**End-user experience** — how people actually feel using the thing, what they trip over.

**Where to look:** Reddit (r/Python, r/MachineLearning, r/devops), Hacker News discussions, Medium posts, dev blogs, Twitter/X technical discussions, Stack Overflow threads, community Discord/Slack.

**Example queries:**
- Site:reddit.com "tool X" real experience
- "tool X" pain points OR frustrations site:news.ycombinator.com
- "I tried X and..." OR "X broke our..." (dev blogs)

### Channel 2: Specialists
**Domain specialist opinion** — expert blogs, engineering posts from labs, thoughtful retrospectives.

**Where to look:** 
- Expert individual blogs: Dan Luu, Julia Evans, Will Larson, David Beazley, Nora Codes
- Lab/company engineering blogs: Anthropic, OpenAI, DeepMind, Latent Space guest posts, Eugene Yan, David Pocock, Simon Willison
- Conference talks + write-ups
- Technical RFC or discussion threads from maintainers

**Example queries:**
- Site:pocock.com OR site:willison.io "topic X"
- "topic X" engineering blog OR "lessons learned" site:anthropic.com OR site:openai.com
- "topic X" expert OR specialist OR practitioner (time-limited to last 6 months)

### Channel 3: Data
**Quantitative data / research** — arxiv papers, conference papers, benchmark numbers, published metrics.

**Where to look:** arxiv.org, ACM Digital Library, Papers With Code, GitHub repo benchmarks, published research, performance comparisons with citations.

**Example queries:**
- Site:arxiv.org "topic X" (YYYY-YYYY)
- "topic X" benchmark OR performance evaluation OR measurement study
- "topic X" empirical study OR systematic review
- Site:paperswithcode.com "topic X"

### Channel 4: Adversarial
**Failure modes & criticism** — post-mortems, GitHub issues, "X considered harmful" pieces, retrospectives, why something failed.

**Where to look:** Post-mortem archives (PostmortemDB, GitHub issue threads), retrospectives, "lessons learned" + negative outcomes, abandoned projects + why, critical technical discussions, conference talks on failures.

**Example queries:**
- "topic X" post-mortem OR postmortem OR "what went wrong"
- "topic X" considered harmful OR critique OR "lessons learned from failure"
- Site:github.com "topic X" wontfix OR closed issues (looking for patterns)
- Abandoned "topic X" OR "deprecated" OR "no longer using"

## Scope

**New research only.** This 4-channel protocol applies to research initiated after this decision. Existing research-pass-style memories (e.g. `research_aihero_principles`, `research_deep_dive_synthesis_2026_05_06`) are grandfathered — no retrofit required. They serve as reference; future research on related topics will apply the protocol fresh.

## Pipeline

### 1. Determine the question(s)

**Topic supplied:** classify it.
- **Decision validation** â focus on trade-offs, risks, real-world experience.
- **Knowledge gap** â authoritative explanations, practical examples.
- **Comparison** â objective criteria, benchmarks, community consensus.

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

Score `impact Ã— urgency`. Pick **top 3**. Each becomes a specific, concrete research question. Bad: "research AI agents". Good: "how do iterative planners detect premature convergence?"

### 2. Search

Primary: `firecrawl_search(query="<topic>", limit=3)`.
Fallback: `WebSearch` if Firecrawl unavailable.

Per topic: 2-3 searches with different angles. Preview results with `head -c 4000`.

Prioritize: official docs, reputable blogs, GitHub discussions, benchmarks.
Skip: SEO spam, articles >2 years old for fast-moving topics.

For highly relevant results: `firecrawl_scrape(url=<url>, formats=["markdown"], onlyMainContent=true)` â max 2 scrapes per topic.

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
- **Finding** â explanation [source]

## Trade-offs & Risks
- **Risk** â when it matters, mitigation

## Alternatives
- **Alternative** â why rejected or when better

## Sources
1. [Title](URL) â what it contributed

## Confidence: N/100
One sentence: what makes this confident or uncertain.
```

### 5. Save

Save to Supabase if finding is significant:

```
memory_store(type="reference", name="research_{slug}", description="...", content="...", source_provenance="skill:research")
```

If finding is actionable â create GitHub issue in appropriate repo:

```
gh issue create --repo <R> --title "[RESEARCH] <topic>" --body "..."
```

**Discovery-only**: also write a dedup marker so the next scheduled run doesn't repeat topics:

```
memory_store(type="project", name="research_last_run", content="{date} â topics: {t1}, {t2}, {t3}", source_provenance="skill:research")
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
- Source conflicts â list both sides
- Call out unknowns and weak evidence
- If confidence <50 â recommend follow-up
