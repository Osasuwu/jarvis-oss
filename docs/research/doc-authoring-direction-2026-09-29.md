---
topic: doc-authoring-direction
date: 2026-09-29
tags: area:docs, area:skills, research
source_provenance: /research invocation, 2026-09-29, while pausing calibration 2 (#146)
confidence: 66 / 72 / 70 (the three reports below)
---

# Doc authoring direction: docs for choosing, docs that don't go stale, docs written by agents

Three research reports, run 2026-09-29, on one question: what the practice docs, `write-doc` and
`review-doc` should be for a reader who clones this repo, studies the options, picks the ones that
fit their setup, and only then configures. The synthesis comes first. The three reports follow
unedited, apart from their headings being demoted one level.

**Superseded on 2026-09-29.** The `tried` status was dropped entirely: it caused problems and
gave no value. The report implications that build on it ("tried = a dated snapshot with a
version" in R2, "an empirical gate for `tried`" in R3) no longer apply.

## Synthesis

### What the product is

- **Study, choose, configure matches the evidence.** It is the AWS decision-guide outline
  (Understand, Consider, Choose, Use) and the developer information journey: seniors select from
  pros and cons, juniors start from tutorials (R1).
- **The unit stays the single-problem practice doc.** Every Page is Page One, the Google doc logs
  and the Casdoc field study all favour self-contained, single-level units (R1).
- **A broad topic ("versioning", "skills", "data storage") becomes a hub page**, a short map that
  routes to problem docs. It is not a long doc with depth levels (R1).
- **Depth is chosen per reader, not per doc.** A reader preset filters hubs down to the applicable
  problem docs through `applies_when` (R1).
- **The first split is a checkable fact**, Azure-style; the result is a starting point, and options
  may combine (R1).
- **The first reader is an agent**: 60.5% of doc interactions in agent sessions are AGENTS.md,
  CLAUDE.md or working notes (R1). Frontmatter and explicit conditions matter.
- **Readers skip sections** and rarely open collapsed content: state the audience, duplicate
  critical facts, don't hide important ones (R1).

### What keeps docs from going stale

- **Timeless phrasing**: no "currently", "latest", "new", or anchor them to a date or version;
  lintable with Vale (R2).
- **Volatile values single-sourced**: the doc body describes the mechanism and the trade-off; prices,
  limits and tiers live in one facts file with value, source URL, verified date and volatility.
  Magnitudes are linked, not copied; only semantics are quoted verbatim (R2).
- **Deterministic drift checks first**: links, referenced files and flags, `--help` diffs, a
  two-snapshot `git grep`. LLM semantic-drift detectors are still noisy (R2).
- **Review cadence by volatility**, with the caveat that a "last reviewed" date invites
  rubber-stamping (R2).

### What agent-written docs need

- **The pipeline shape is right**: writer, fresh reviewer, human gate, as in STORM, DocAgent, GEIS
  and the drift bots (R3).
- **The weak link is the verifier's precision, which we don't measure.** First-pass false positives
  run 8–54%; a refute-or-promote pass killed 79% of 171 findings. Calibration 2 measures catch
  (recall) only (R3).
- **Missing here**: a precision stage before a finding blocks; a false-block rate beside the catch
  rate; unverifiable kept apart from false; a coherence check; recurring findings fed back into
  `write-doc`; a pass-1 reviewer from a different model family (R3).
- **Cheaper shapes**: bind each claim to its source at write time (SymGen); triage claims by risk
  so only choice-bearing claims get the expensive check (R3).
- **For the human**: ranked, span-anchored targets, not highlighting (R3).

### Gaps

- The Users channel is thin: Reddit refused scrapes.
- The Adversarial channel is thin in R1 and R2.
- Not fetched: Write the Docs, Tom Johnson, the Microsoft and Stripe style handbooks, DITA,
  Information Mapping, NN/g on progressive disclosure.
- Confidence of 66–72 is enough to set a direction, not to lock architecture without a grill.

## R1: Doc frameworks, choice docs, learning depth

Run date 2026-09-29. Tooling: Firecrawl (6 searches, 5 scrapes: one 404, one refused by Reddit) plus the research-paper index. Reddit and some specialist pages are cited from search-result excerpts only.

### Summary

None of the fetched frameworks gives "help me choose" its own document type. In Diátaxis, a how-to guide should leave out alternatives, and an explanation "can and must consider alternatives". A jarvis-oss practice doc is therefore a deliberate hybrid: an explanation-mode "how to choose" section attached to reference/how-to-mode option entries. Vendor decision guides (AWS, Azure) have converged on the same outline: purpose, covered options, last-updated date, criteria, choice, then use. The empirical literature (Google doc logs, Casdoc field study, developer journey maps) points one way: keep self-contained, single-purpose units, make audience and important facts visible without interaction, and handle depth with navigation (hubs, journeys) rather than with longer pages. It supports broad-topic hubs on top of single-problem docs, not broad docs replacing them.

### Key Findings

- **Diátaxis has no decision type; choice lands in explanation** — how-to is "action and only action" for a reader who "knows what they want to achieve" [1, Specialist]; explanation "can and must consider alternatives", "weigh up alternatives", "admit opinion and perspective" [2, Specialist]; a derivative skill: how-to "DON'T offer alternatives or options" [3, Specialist-derivative].
- **Practitioners see gaps in the Diátaxis map** — index pages/homepages/READMEs are "a different type of content… not well-represented"; teams use modes as sections within a page, not one mode per page, which most readers misread [5, Users]; forward-looking docs (designs, strategy) don't fit [7, Users]; Python core moves mixed docs to the "nearest port", explanation [4, Users].
- **Critiques** — Diátaxis repeats earlier Django/Kaplan-Moss patterns and lacks grounding in learning theory [6, Adversarial]; it "does not help with outdated docs" [7, Adversarial]; DITA "isn't focused on user (reader) need… a structure imposed on information" [8, Adversarial].
- **Every Page is Page One** — each topic is self-contained, has a limited purpose, establishes context, conforms to a type, stays on one level and links richly; the big picture is reached by links "as and when the reader is ready" [12, Specialist].
- **AWS decision guides share one outline** — Introduction / Understand / Consider (criteria) / Choose / Use / Explore, under a header of Purpose, Last updated, Covered services [10, Specialist].
- **Azure: flowchart, then tables, then "starting point"** — the first branch is a checkable fact (migrate vs build new); the output is "your starting point. Then evaluate"; if it doesn't fit, "expand your analysis"; "a complete solution can include two or more" services [11, Specialist]. Both vendors cover only their own services (my inference).
- **Google doc logs (4 products, 100k+ users)** — experienced users visit how-to/guides more than novices; many call APIs without reading reference; developers skip sections, so information living on one modular page is missed; recommended: state the target audience explicitly (strong "scent") and duplicate critical information [13, Data].
- **Casdoc field study (300+ participants)** — gradual reveal lets a doc hold more without distraction, but readers "never viewed a majority" of collapsed annotations; guidelines: don't require an action to reveal important information, and prefer intuitive structure over navigation aids (breadcrumbs and search were rarely used); over-fragmentation irritates readers [14, Data].
- **Developer information journey (25 interviews, 177 surveyed)** — stages Exploration / Understanding / Practice / Application; seniors do "technology selection" from pros/cons and product comparisons, partly to persuade teammates; juniors start with tutorials and videos because someone assigned the tool [15, Data].
- **Agents read agent-facing files, not classic docs** — 60.5% of doc interactions in 557 sessions are AGENTS.md/CLAUDE.md (35.4%) or working notes (25.1%); API reference is 1.3%; assumed "actionability/verifiability" properties lack behavioural support [16, Data].
- **Decision records drop the alternatives** — across ADRs in 550 OSS repositories, alternatives and decision drivers are under-documented [17, Data]; concise Nygard ADRs beat MADR overall, while MADR wins on structure [18, Data]; architects favour familiar or fast solutions [19, Data].
- **Choice overload is real but moderated** — choice has costs, including decision avoidance [20, Data; abstract only]; API learners report information overload and trust problems [21, Data].

### Implications for jarvis-oss

1. **Keep the single-problem practice doc as the unit.** EPPO [12], the Google logs [13] and Casdoc [14] all favor self-contained, single-purpose, single-level units that serve as entry points. A broad "versioning" doc with depth levels would break "stay on one level" and would hide important facts behind expansion, which Casdoc guideline 2 warns against.
2. **Add broad topics as hub/index pages, not as containers.** kaycebasques's point that index pages are their own content type [5] and Baker's "link to other levels" [12] both point here. A hub such as "Versioning" is a short explanation-mode map: what the area is, the list of problems it contains, and a suggested order. Depth becomes a question of which problem docs you open, not how far down one page you read.
3. **Implement presets and depth as routing, which the setup skill already does.** The journey data [15] shows stage and seniority change which document types readers need. A preset such as "solo / no paid subs / attended" can filter hubs down to the applicable problem docs using the existing `applies_when` frontmatter. That is the audience "scent" Google recommends [13]. Don't build beginner/advanced variants of the same doc.
4. **Name each section's Diátaxis mode.** Problem and "how to choose" are explanation, which may weigh alternatives [2]. Each option's "how it works / cost / update / uninstall" is reference plus a how-to stub. Use the section approach, not separate pages [5].
5. **Keep full-scope option lists; borrow Azure's mechanics.** The template maps the full option space, not just in-house options [10, 11], and the ADR findings [17] show alternatives and drivers are what practice usually drops. Borrow from Azure: open "how to choose" with the checkable fact that splits the space first, say the result is a starting point, and state explicitly when options combine [11].
6. **Put "verified date" on every option.** AWS's per-guide "Last updated" [10] is coarser. Staleness is a recurring complaint that no framework solves [7].
7. **The first reader is an agent** (the setup skill), and agents mostly read instruction-type files [16]. Frontmatter and consistent section patterns ("conform to type" [12]) serve the setup skill directly. Don't over-invest in human navigation aids [14].

### Trade-offs & Risks

- **Hub pages mean more to maintain.** Each hub is another page that can drift from the problem docs it lists.
- **Duplication vs single source.** Google recommends duplicating critical facts [13], and duplicates go stale. Keep duplicates to one-line pointers with a link.
- **Too many options:** the choice-overload review [20] suggests grouping options by the first splitting fact rather than presenting a flat list of eight or more.
- **Narrow audience:** routing by preset can hide an option a reader would have wanted. Hubs should still link every problem doc.
- **Taxonomy fights:** DITA typing is called an imposed structure [8], and Diátaxis purism is misread [5]. Treat modes as writing guidance, not filing rules.

### Alternatives

- **Type-first tree** (Tutorials / How-to / Reference / Explanation at the top level). It is the common Diátaxis reading [5], but it scatters a single problem across four trees.
- **Topic-first hierarchy with modes inside each topic.** This is Procida's "complex hierarchies" guidance. The page returned 404, so it is prior (not fetched).
- **DITA concept/task/reference typing and Information Mapping.** Prior (not fetched): no fetched source described them beyond the Reddit objection [8].
- **Interactive decision tree** (Azure-style flowchart [11]). It hides the option space behind the path taken.
- **Personalized or recommended docs** driven by usage logs [13]. It needs telemetry jarvis-oss lacks. A preset is the static equivalent.
- **Learning paths / progressive disclosure (NN/g).** Prior (not fetched). Casdoc [14] is the only fetched evidence on disclosure, and it is mixed.

### Sources

1. How-to guides — Diátaxis — https://diataxis.fr/how-to-guides/ — Specialist — how-to = action only, no alternatives
2. Explanation — Diátaxis — https://diataxis.fr/explanation/ — Specialist — explanation must weigh alternatives
3. Diátaxis framework overview (UW CALS Claude skill) — https://pages.doit.wisc.edu/cals/claude/skills/kb-with-diataxis/-/blob/main/references/framework-overview.md — Specialist-derivative — "don't offer options" in how-to
4. Diátaxis and Python documentation — https://discuss.python.org/t/diataxis-and-python-documentation/41836 — Users — "nearest port" incremental approach
5. HN: We fixed our documentation with Diátaxis — https://news.ycombinator.com/item?id=42340740 — Users — index pages not on the map; section vs page approach
6. HN: I'm not that impressed with Diataxis — https://news.ycombinator.com/item?id=42305899 — Adversarial — originality and theory critique
7. HN thread on doc structure — https://news.ycombinator.com/item?id=33708377 — Users/Adversarial — forward-looking docs; staleness not addressed
8. r/technicalwriting: Diátaxis, a pragmatic system — https://www.reddit.com/r/technicalwriting/comments/n4irfk/ — Users/Adversarial — DITA "not focused on reader need" (excerpt)
9. r/technicalwriting: Diátaxis infographic — https://www.reddit.com/r/technicalwriting/comments/1pcf47t/ — Users — Procida: Diátaxis guides "small steps", not a top-down architecture (excerpt)
10. AWS Decision Guides (index, compute, database) — https://docs.aws.amazon.com/decision-guides/ ; https://docs.aws.amazon.com/decision-guides/latest/decision-guides/choosing-aws-compute-service.html — Specialist/vendor — outline, metadata block
11. Choose an Azure compute service; Technology choices overview — https://learn.microsoft.com/en-us/azure/architecture/guide/technology-choices/compute-decision-tree — Specialist/vendor — flowchart + tables, "starting point", combinations
12. Every Page is Page One (Baker) — https://mbakeranalecta.github.io/spfe-open-toolkit/spfe-docs-essays/every-page-is-page-one.html ; https://everypageispageone.com/the-book/ — Specialist — seven topic principles
13. Understanding Documentation Use Through Log Analysis — arxiv:2310.10817 — Data — usage patterns, audience labeling, duplication
14. Non Linear Software Documentation (Casdoc) — arxiv:2311.18057 — Data — disclosure guidelines
15. Mapping the Information Journey — arxiv:2312.02586 — Data — stages, senior technology selection
16. From Agent Behaviour to Agent-Friendly Documentation — arxiv:2608.20195 — Data — agents' doc use
17. Text Mining ADRs — arxiv:2609.07375 — Data — alternatives under-documented
18. ADR template comparison — arxiv:2604.27333 — Data — concise vs structured templates
19. What rationales drive architectural decisions? — arxiv:2309.14164 — Data — familiarity bias
20. Advantages and disadvantages of choice — pmcid:PMC11111947 — Data — choice overload (abstract only)
21. On Designing Better Tools for Learning APIs — arxiv:1402.1188 — Data — information overload, trust

### Channel coverage

- **Users:** 5 (HN ×3, Reddit ×2 as excerpts, since Reddit refused scraping; plus a Python Discourse thread). Adequate, but mostly Diátaxis-specific. There is no user-voice source on decision guides.
- **Specialists:** 5 (Diátaxis ×2, Baker, AWS, Azure). **Gaps:** Write the Docs, Google and Microsoft style guides, and Tom Johnson did not surface; the search for Johnson's Diátaxis critique returned only bio pages. DITA and Information Mapping are unfetched.
- **Data:** 9. Strong.
- **Adversarial:** 3 direct sources (#6, #7, #8), plus failure evidence inside the Data sources (#14 guideline 2, #17). **Flag:** no dedicated source on failure modes of decision guides or vendor bias. The vendor-scope bias above is my own inference from #10 and #11.

### Confidence: 66/100

The data is solid on how people use docs. The prescriptions for choice docs are inferred from vendor examples plus Diátaxis, not from a study of decision guides themselves.

## R2 — Durable docs in a fast-moving domain (run 2026-09-29)

### Summary

Docs stay current through three mechanisms, which work best together. (1) **Write so the text does not age.** Avoid time-relative words and anchor anything version-bound to a date or release. (2) **Keep every volatile value in exactly one place.** Pages reference tokens or data files, or link to the vendor's page, instead of copying the value. (3) **Detect drift mechanically.** Reference checkers and executable "docs as tests" catch concrete breakage. A per-file review date enforced in CI catches everything else. The research agrees on three points: doc rot is common, referential rot is cheap to detect, and semantic rot is expensive and noisy to detect. A 2026 study found stale code references in 23% of repos' CLAUDE.md/AGENTS.md files, which is this repo's exact domain. Weak spot: I found no third-party guide to fast-moving vendor products that states a methodology. Q4 is answered from repo practice (PRs and design docs) and from vendor style guides.

### Key Findings

- **Timeless phrasing is a named style rule.** Google's developer style guide says to avoid "currently", "latest" and "new, newer". If "new" is unavoidable, the rule is to "give a reference point such as a date or version release number". TON and SAP style guides repeat the rule. [1, 2, Specialists]
- **Version history is written and then removed on a schedule.** GitLab's docs carry structured "availability details" (tier, offering, status, history) as shortcodes, not prose. Before a new major version, contributors "create merge requests to remove mentions of the last unsupported version". Tier names are linked to the pricing page, not restated with prices. [3, 4, Specialists]
- **Review-date frontmatter plus CI is a working pattern.** microsoft/hve-core puts an `ms.date` field on every Markdown file. It sets a 90-day threshold. The PR check fails only on changed files. A weekly scan opens GitHub issues for the whole tree. Their policy: "The date reflects the last review date, not necessarily the last time content changed." [5, Specialists/practice]
- **Review cadence should differ by content type, not be one number.** One framework ties reference and config docs to every release, quickstarts and how-tos to quarterly review, explanation and architecture docs to twice a year, and glossaries to once a year. It adds link checks, CI testing of code examples, visible last-updated stamps, and flagging of high-traffic stale pages. It says: "Explanation docs can remain valid for years." [6, Specialists/practice]
- **Single-sourcing volatile values in practice:** Mastra added a remark plugin that swaps `__TOKEN__` placeholders from one `models.ts`. That replaced about 340 hardcoded model strings across 138 files. The PR itself fixed stale references such as `gpt-4o` and `claude-haiku-3.5`, and AGENTS.md now tells agents to use the tokens. [7, Specialists/practice]
- **Pricing tables cite a source URL and a checked date.** One SDK design doc says pricing is "treated as the pricing source of truth on 2026-07-30". It names the vendor URL, with the risk note "Pricing can change; the checked date and source URL make updates auditable." [8, Specialists/practice]
- **Duplicated volatile tables drift apart.** One project kept model metadata "in five tables that each know part of the story". It consolidated them into per-provider manifests, with a consistency suite. [9, Specialists/practice]
- **Docs as Tests treats procedures as assertions.** A customer reporting a broken procedure is "essentially a failed test". Doc Detective parses Markdown, runs the steps, takes screenshots and validates API responses against OpenAPI. There are two ways to set it up: tests derived from the docs, or standalone Playwright/Cypress tests. [10, 11, Specialists]
- **Referential rot in agent config files is measured.** DOCER, run unmodified over 612 AI config files in 356 repos, flagged stale code references in **23.0%** of repos (95% CI 18.8–27.2%). The stale rate per reference was 1.0–1.4% across CLAUDE.md, AGENTS.md and Copilot instructions. On manual review, 64% of flags were genuine rot and 36% were false positives or ambiguous. The authors recommend a two-snapshot `git grep` in CI and note it "needs no new tooling". [12, Data]
- **Referential rot is widespread in general.** Most of 3,000+ GitHub projects had at least one outdated code-element reference at some point in their history. So did more than a quarter of the top 1,000 projects. The same authors built this into a GitHub Action that scans on every PR. [13, 14, Data]
- **Outdated content is a leading documentation defect type.** In 101 industrial documentation defects, the top types were missing docs (35), erroneous code examples (23) and outdated content (19). The authors propose generating docs dynamically and testing them automatically "from a single and robust information source". [15, Data]
- **Semantic drift detection is still noisy.** Plain LLM prompting flags 98% of functions as inconsistent with their docs. The DocPrism method cuts that to 14%, with precision around 0.63. Real inconsistencies sit at a lower bound of 11% of code–doc pairs. An LLM that predicts README updates reaches 28% user-facing accuracy. [16, 17, Data/Adversarial]
- **Execution-based checks keep false positives down.** CASCADE generates tests from the docs. It reports a mismatch only when existing code fails a test and code generated from the docs passes it. It found 13 new inconsistencies, and 10 were fixed. [18, Data]
- **Users converge on keeping docs next to the code and in the change path.** "Documentation that lives outside the code rots the fastest." One commenter makes docs an explicit opt-out: "you have to actively choose 'skip docs.'" They credit that friction with keeping about 80% of documentation current. Others hand doc upkeep to new hires or interns. [19, 20, 21, Users]
- **Doc freshness automation creates noise that becomes work.** hve-core's bot opened a stale-docs issue against its own `documentation-maintenance.md`, at 118 days against a 90-day threshold. The rule lets a reviewer clear a flag just by bumping the date. That makes rubber-stamping easy (my inference; there is no measurement of it). [5, 22, Adversarial]

### Implications for jarvis-oss

1. **Split each practice doc into two layers.** The body covers mechanism and trade-offs. The axis goes in the prose ("seat vs usage-billed", "attended vs unattended", "has a rate-limit window"), not the value. Volatile values (tier names, prices, limits, model IDs, CLI flags) go only in a `facts` data file (YAML/JSON), keyed by id, with `value`, `source_url`, `verified: YYYY-MM-DD` and `volatility: release|monthly|quarterly`. Pages reference the keys, and a build step or checker renders them, as in Mastra's token plugin [7]. When a price changes, one line changes.
2. **Price-like facts should prefer linking to quoting.** Quote a vendor verbatim only for *semantics*, such as what a flag does. For *magnitudes* (price, limit, context size), link the vendor page and at most show "as of <date>" [3, 8].
3. **Lint for time-relative words.** Add a Vale (or grep) rule for currently/latest/new/soon/now [1].
4. **Set per-file `verified` dates with a cadence by volatility,** not one global 90 days [5, 6]. Only changed files block PRs. A scheduled job files issues for the rest. Require the review PR to touch the `facts` entry or quote, not only the date, so rubber-stamping becomes visible.
5. **Add cheap deterministic checks first:**
   - A link checker over vendor URLs.
   - A two-snapshot reference check for file paths, skill names and commands named in docs and CLAUDE.md/AGENTS.md [12].
   - For documented CLI flags, a CI job that diffs them against `claude --help` / `codex --help` output where a binary is available [11].
   Defer LLM semantic drift checks until precision is shown on this repo's own corpus [16].
6. **Mark tried/sourced options with a dated snapshot.** "Tried on <date> with <version>" is a snapshot, not a claim about now. That matches Google's reference-point rule [1].

### Trade-offs & Risks

- **Indirection cost.** Tokens and data files make the raw Markdown less readable on GitHub unless rendered. Agents that read the raw file see `__TOKEN__`. Mastra dealt with this by instructing agents in AGENTS.md [7].
- **Review-date theater.** A date bump satisfies the check without anyone verifying anything [5].
- **Alert fatigue.** Freshness bots produce a steady stream of issues [22]. With one maintainer, a quarterly cadence on everything is probably not sustainable. Cadence by volatility matters here.
- **False positives.** Referential checkers ran at 36% FP/ambiguous in the agent-config study [12]. LLM checkers are worse without filtering [16].
- **Linking hands control to the vendor.** Vendor pages move and restructure, so link rot replaces value rot. A link checker is mandatory.
- **Execution-based doc tests** need the tool installed and often authenticated. Unattended CI can't always run `claude`, and paid subscriptions complicate it further.

### Alternatives

- **Generate reference sections from the source.** GraphQL-Markdown and similar tools render docs from the schema [23]. This fits only where a machine-readable source exists, such as settings JSON schemas or CLI `--help` output.
- **Standalone behavioral tests** (Playwright/pytest) kept separate from the docs, instead of tests derived from them [10].
- **LLM-in-the-loop PR advisor** ("does this PR need a doc update?"). Low precision (28%) [17], but cheap as a non-blocking hint.
- **Do nothing and accept dated snapshots.** Every page is explicitly "as of <date>" and readers re-verify. It costs almost nothing, but readers carry the verification burden.

### Sources

1. Google dev style guide: Timeless documentation — https://developers.google.com/style/timeless-documentation — Specialists — the avoid-list and the date/version reference-point rule
2. TON style guide — https://docs.ton.org/contribute/style-guide-extended — Specialists — the same timelessness rule, as a SHOULD NOT
3. GitLab Docs Style Guide — https://docs.gitlab.com/development/documentation/styleguide/ — Specialists — tier terms linked to the pricing page; shortcodes for availability
4. GitLab: Product availability details — https://docs.gitlab.com/development/documentation/styleguide/availability_details/ — Specialists — structured tier/offering/status/history; history removed on major releases
5. microsoft/hve-core: Documentation Maintenance — https://github.com/microsoft/hve-core/blob/a146d8d01ad1e38f82d5d8eebb7fa8d1d88c4092/docs/contributing/documentation-maintenance.md — Specialists (practice) — `ms.date`, 90-day threshold, PR vs weekly scan
6. anivar/developer-docs-framework: gov-freshness-cadence — https://github.com/anivar/developer-docs-framework/blob/c0e9445fcdf832681db78eaca58cdcefccd1e654/rules/gov-freshness-cadence.md — Specialists (practice) — review cadence per content type
7. mastra-ai/mastra PR #16932 — https://github.com/mastra-ai/mastra/issues/16932 — Specialists (practice) — model-name token single source; about 340 strings replaced
8. vidbyte-sdk pricing design doc — https://github.com/cerredz/vidbyte-sdk/blob/1edf9b66524ca7beff4afb85c0cac8f3d21b0e53/docs/design/openai-gpt-5-6-catalog-pricing.md — Specialists (practice) — source URL plus checked date
9. juspay/neurolink PR #1351 — https://github.com/juspay/neurolink/issues/1351 — Specialists (practice) — five drifting tables consolidated into manifests
10. Not Boring Tech Writer: Docs as Tests with Manny Silva — https://thenotboringtechwriter.com/episodes/docs-as-tests-keeping-documentation-resilient-to-product-changes-with-manny-silva — Specialists — the docs-as-tests model; Doc Detective
11. Docs as Tests — https://www.docsastests.com/ — Specialists — the methodology overview. Also a Docsie glossary search excerpt (https://www.docsie.io/blog/glossary/documentation-drift/) on diffing CLI flags against `--help` in CI
12. Treude & Baltes 2026, Context Rot — arxiv:2606.09090 — Data — 23.0% of repos have stale refs in AI config files; 64% precision
13. Tan et al., Detecting Outdated Code Element References — arxiv:2212.01479 — Data — prevalence across 3,000+ projects
14. Tan et al., Wait, wasn't that code here before? — arxiv:2307.04291 — Data — more than a quarter of the top 1,000 repos; GitHub Action
15. Towards minimizing customer-facing documentation debt — arxiv:2402.11048 — Data — defect-type counts; single-source recommendation
16. DocPrism — arxiv:2511.00215 — Data/Adversarial — 98% naive LLM flag rate; 11% inconsistency lower bound
17. LLM-Based README Maintenance — arxiv:2603.00489 — Data/Adversarial — 28% accuracy
18. CASCADE — arxiv:2604.19400 — Data — execution-based, low-false-positive detection
19. r/ExperiencedDevs "Documentation is three years out of date" — https://www.reddit.com/r/ExperiencedDevs/comments/1qejexx/ — Users — docs outside the code rot fastest (search excerpt only; the scrape was blocked)
20. r/claude "keeping documentation alive" — https://www.reddit.com/r/claude/comments/1r977gg/ — Users — opt-out "skip docs" friction
21. r/devops "documentation becoming obsolete" — https://www.reddit.com/r/devops/comments/vn0mkf/ — Users — offload to interns/onboarding
22. hve-core issue #1976 — https://github.com/microsoft/hve-core/issues/1976 — Adversarial — freshness bot flags its own maintenance doc
23. graphql-markdown intro — https://github.com/graphql-markdown/graphql-markdown/blob/1eebd6c9237bca36c9cef06490ab44e3a9d67d77/docs/intro.md — Specialists — generating docs from the schema
24. HN "Reducing technical debt by valuing comments" — https://news.ycombinator.com/item?id=34599673 — Users/Adversarial — the debate over whether comments and docs inevitably go stale (summary only; low weight)

### Channel coverage

- **Users:** 4 sources (3 Reddit, 1 HN). Thin. Reddit could not be scraped, so these are search excerpts only, and none came from Stack Overflow.
- **Specialists:** 11 sources. Google, GitLab and Docs as Tests are strong. Write the Docs, Tom Johnson (idratherbewriting), Stripe and Microsoft Learn were not fetched: a site-scoped search returned 0 web results. **Flagged:** the tech-writing-community part of this channel is missing.
- **Data:** 7 papers. Strong.
- **Adversarial:** 3–4 sources (DocPrism, README-LLM, hve-core #1976, HN), mostly inferred from noise and precision numbers. **Flagged:** I found no first-person "our docs rotted" post-mortem and no critique of docs-as-code itself.
- **Q4 (third-party guides to vendor products):** no dedicated source found. The answer is built from repo practice [7–9] and vendor style guides [1, 3]. **Flagged.**

### Confidence: 72/100

The mechanisms and the Data channel are well supported. The cadence and single-sourcing patterns are corroborated only by individual repos, not by surveys. The Specialists and Adversarial channels have gaps.

## R3 — Agent-written docs: pipelines, verification, review, human focus

### Summary

jarvis-oss is built the way the field builds these systems: one agent writes the doc, fresh-context agents review it, and a human signs off. The same split appears in STORM, DocAgent, GEIS and the doc-drift bots (Mintlify, gh-aw, Factory, Red Hat).

The weak link is the verifier:
- **Decompose-then-verify checkers fail mainly at the decompose step.**
- **Critics trade precision for recall.** They catch more than humans do, but produce many more nitpicks and hallucinated defects.

A run that returns 20–36 "blocking" findings looks like an uncalibrated critic tuned for recall. The evidence points to three changes:
- **Add a precision stage** before a finding can block.
- **Measure precision alongside catch rate,** using disagreement audits, because gold labels are noisy too.
- **Give the human a few ranked, span-anchored inspection targets.** Highlighting alone does not reduce overreliance.

### Key Findings

- **Outline-first, multi-perspective research drives completeness.** STORM gains +25% on organization and +10% on breadth over an outline-RAG baseline. Editors still flagged bias carried over from sources and unrelated facts linked together [1, Data]. Co-STORM surfaces "unknown unknowns" [2, Data].
- **Splitting writer and verifier works best against a deterministic ground truth.** DocAgent's truthfulness check is entity existence in the code graph: 95.74% vs 61.10% for ChatGPT. Only helpfulness is judged by an LLM, and the authors say human oversight remains crucial [3, Data].
- **Fold recurring findings back into the writer.** GEIS patches recurring audit findings into the writer skill: 82.90 → 86.95, with 17 of 20 topics improved [4, Data].
- **Claim-level checkers are cheap.** SAFE agrees with humans on 72% of facts and wins 76% of the disagreements (humans 19%), at $0.19 per response [5, Data]. VeriFastScore (8B) tracks VeriScore at r=0.94 per system and runs 6.6× faster [6, Data].
- **Checkers break at decomposition.** In law and medicine, over 90% of SAFE's errors come from decomposition or self-containment [5, Data]. Other papers agree:
  - Decomposition noise destabilizes pipelines [7, Data].
  - A decomposer can substitute its own belief for the source; SelfCheckGPT cannot detect this (AUC 0.51) [8, Adversarial].
  - Facts that are each true can merge into a false paragraph, which FActScore overrates by more than 10% [9, Adversarial].
  - Unverifiable claims should not count as failures [10, Data].
- **Long reports defeat both short-text verifiers and unaided experts.** On deep-research claims, PhD experts are 60.8% accurate alone and 90.9% after audit-then-score rounds; DeepFact-Eval beats SAFE by 27.5 points [11, Data]. Its error taxonomy maps onto practice docs:
  - taxonomy oversimplification (missing options);
  - conditional collapse (dropped qualifiers);
  - single-study certainty;
  - over-scope leap.

  Checking citation entailment misses uncited claims.
- **A citation that resolves is not a citation that supports.** Link validity is 94% or more, but citation fact accuracy is only 39–77%, falling about 42% as tool calls grow from 2 to 150 [12, Data]. Top agents meet fewer than 50% of expert rubrics [13, Data].
- **Critics trade precision for recall.** CriticGPT catches more bugs than contractors but produces far more nitpicks and hallucinated bugs; the right operating point is unknown [14, Data/Adversarial]. First-pass LLM false positives run 8–54%, and a second critic that re-reads the source adds 0.04–0.25 F1 [15, Data].
- **Agreement among reviewers is not verification.** An adversarial refute-or-promote pipeline killed about 79% of 171 candidates. Ten reviewers unanimously endorsed a non-existent vulnerability, and only an empirical test killed it [16, Adversarial].
- **Judge bias depends on the model family.** Style bias runs 0.10–0.76. Claude prefers concise output; Gemini and Llama prefer long [17, Data]. Self-preference bias is driven by verbosity [18, Data]. GPT-4 factuality ratings did not correlate with humans on summaries [19, Adversarial].
- **Verification aids help, but do not cure overreliance.**
  - Phrase-level factuality coding and source highlighting help validation and trust [20, Data].
  - Relation-level uncertainty cues reduce independent checking [21, Data].
  - Highlighting had no effect on overreliance in an RCT [22, Adversarial].
  - What did work: ranked inspection targets with context (U-Lens) [23, Data] and span-anchored AI comments [24, Data].
- **Industry drift bots keep the human gate and narrow the scope.**
  - Mintlify opens PRs by default ("review is safer") and batches them [25, Industry].
  - gh-aw's "safe output" is a PR [26, Industry].
  - Factory opens PRs for review and batches on a schedule [27, Industry].
  - Red Hat Code-to-Docs works review-then-update [28, Specialists].
- **Practitioners say validation becomes the job.** AI output is plausible but wrong, and the scarce skill is checking a doc's testable assertions. Paragraphs quietly disappear in AI rewrites [29, 30, Specialists].
- **Users complain about confident wrongness and verbosity.**
  - An HN maintainer: generated docs "wrong in every section", confidently promoting a non-working extension. Review works only as a "trickle, not a flood", or eyes "glaze over" [31, Users].
  - "70% complete, 10% indirect and 20% wrong" [32, Users].
  - "Overly verbose, smart sounding statements"; fake links [33, 34, Users].

### Implications for jarvis-oss

**Aligned:**
- Fresh-context reviewers separate from the writer [3, 4].
- Claims checked against fetched sources [5].
- A dedicated completeness pass, which hits the taxonomy-oversimplification defect class [1, 11].
- The how-to-choose walk tests conditional collapse [11].
- Final human sign-off [25–28].

**Missing:**
- **A precision stage.** Before a finding blocks, run refute-or-promote on it, or have a second critic re-read the source [15, 16].
- **Precision as a calibration metric, alongside catch rate.** Track a false-block rate. Audit disagreements rather than trusting a static gold set: re-auditing lifted benchmark accuracy from 84.6% to 94.7% [11].
- **"Unverifiable" as a verdict separate from "false"** [10].
- **A whole-paragraph coherence and uncited-claim check** [9, 11].
- **A pass-1 reviewer from a different model family** [17, 18].
- **Recurring findings patched into the writer skill** [4].
- **A structured human handoff.** Give a short ranked list of span-anchored targets with evidence [23, 24]. For "tried" claims, run the thing; reviewer consensus is not enough [16].

**Likely wasted effort:**
- Full-cost verification of low-stakes claims.
- Nitpicks carrying "blocking" severity.
- Relying on highlighting to steer attention [22].

For scale: SAFE costs $0.19 per response against $20–30 per jarvis run. The two are not directly comparable, but the gap argues for a cheap screen before the expensive passes [6].

### Trade-offs & Risks

- **Precision gates cost recall.** Pick the threshold against the escaped-defect corpus and record where you set it [14].
- **Too many findings produce a rubber stamp; too few invite automation bias** [22, 31].
- **An adversarial stage adds cost and can kill real defects.** How many true positives it keeps was not measured [16].
- **The calibration corpus is small and labelled by one person.** Treat its estimates as directional.
- **Bias numbers come from general tasks,** not practice docs [17].

### Alternatives

1. **Verify at write time.** The writer emits claim-to-quote bindings (SymGen-style symbolic references [35]), which the reviewer checks mechanically.
2. **Triage claims by risk.** Deep-check numbers, costs and "best for" claims; send the rest through a cheap screen [6].
3. **Rubric-based review** built from expert references for each doc type [13].
4. **Audit-then-score** on disagreements instead of a static gold set [11].
5. **Drift-bot re-check** when sources change, opening a scoped PR [25–28].
6. **An empirical gate for "tried" options** [16].

### Sources

1. STORM — https://arxiv.org/abs/2402.14207 — Data — outline-first coverage, bias carry-over
2. Co-STORM — https://arxiv.org/abs/2408.15232 — Data — unknown unknowns
3. DocAgent — https://arxiv.org/abs/2504.08725 — Data — writer/verifier split, deterministic truth check
4. GEIS — https://arxiv.org/abs/2607.11503 — Data — findings patched into writer
5. SAFE — https://arxiv.org/abs/2403.18802 — Data — accuracy, cost, decomposition errors
6. VeriFastScore — https://arxiv.org/abs/2505.16973 — Data — cheap verifier
7. Decomposition Dilemmas — https://arxiv.org/abs/2411.02400 — Data — decomposition noise
8. DI-CC — https://arxiv.org/abs/2608.10627 — Adversarial — decomposer substitutes belief
9. D-FActScore — https://arxiv.org/abs/2402.05629 — Adversarial — true facts, false whole
10. VeriScore — https://arxiv.org/abs/2406.19276 — Data — unverifiable claims
11. DeepFact — https://arxiv.org/abs/2603.05912 — Data/Adversarial — AtS, error taxonomy
12. Cited but Not Verified — https://arxiv.org/abs/2605.06635 — Data — citation fact accuracy
13. DeepResearch Bench II — https://arxiv.org/abs/2601.08536 — Data — expert rubrics
14. CriticGPT — https://arxiv.org/abs/2407.00215 — Data/Adversarial — precision/recall trade-off
15. Self-reflection coding — https://arxiv.org/abs/2601.09905 — Data — precision critic
16. Refute-or-Promote — https://arxiv.org/abs/2604.19049 — Adversarial — unanimous false positive
17. Judging the Judges — https://arxiv.org/abs/2604.23178 — Data — style/verbosity bias
18. Unreliable Judges — https://doi.org/10.64898/2026.06.15.26355670 — Data — self-preference
19. Are LLMs reliable judges? — https://arxiv.org/abs/2311.00681 — Adversarial — no human correlation
20. Factuality scores UX — https://arxiv.org/abs/2405.20434 — Data — phrase-level coding
21. Uncertainty granularity — https://arxiv.org/abs/2605.28571 — Data — cue granularity
22. Health highlighting RCT — https://arxiv.org/abs/2606.20605 — Adversarial — no overreliance effect
23. U-Lens — https://arxiv.org/abs/2607.10604 — Data — ranked inspection targets
24. AnchoredAI — https://arxiv.org/abs/2509.16128 — Data — anchored comments
25. Mintlify — https://www.mintlify.com/blog/docs-on-autopilot ; https://www.mintlify.com/docs/automations — Industry — PR-default gate
26. gh-aw docs automation — https://github.com/github/gh-aw/blob/60ff367876c6c71755b36c5e91e103dcab1fe223/docs/src/content/docs/gallery/docs-automation.md — Industry — safe-output PR
27. Factory droid docs automation — https://github.com/factory-ai/factory/blob/485a0c3b5d3d11c52d50cd2a8889e1a71e86905a/docs/guides/droid-exec/document-automation.mdx — Industry — review PR, batching
28. Red Hat Code-to-Docs (2026-04-21) — https://developers.redhat.com/articles/2026/04/21/ai-powered-documentation-updates-code-diff-docs-pr-one-comment — Specialists — review-then-update
29. Cyborg Technical Writers — https://idratherbewriting.com/blog/cyborg-model-emerging-talk — Specialists — validation is the job
30. 10 principles of the cyborg writer — https://idratherbewriting.com/blog/10-principles-of-cyborg-technical-writer — Specialists — disappearing content
31. HN thread 45884860 — https://news.ycombinator.com/item?id=45884860 — Users/Adversarial — wrong docs, trickle not flood
32. HN thread 48411510 — https://news.ycombinator.com/item?id=48411510 — Users — misses key details
33. r/ExperiencedDevs — https://www.reddit.com/r/ExperiencedDevs/comments/1v9trtm/the_best_part_about_ai_for_those_that_dont_use_it/ — Users — verbosity
34. r/ChatGPTPro — https://www.reddit.com/r/ChatGPTPro/comments/1lxa0lt/fake_links_confident_lies_contradictions_whats/ — Users — fake links
35. SymGen — https://arxiv.org/abs/2311.09188 — Data — symbolic references

### Channel coverage

- **Data: strong** (about 25 sources). Mostly read at abstract or summary level; STORM is abstract-only because full-text reads failed. Many are unreviewed 2026 preprints.
- **Specialists: adequate** (3: Tom Johnson ×2, Red Hat). The Simon Willison pages were weakly relevant and dropped.
- **Industry: adequate** (3) but vendor-reported, with no independent outcome data.
- **Users: thin** (4). Excerpts only: Firecrawl refused the Reddit scrape and HN was not scraped. Anecdotal.
- **Adversarial: strong** (8).
- **Gap:** no source measures multi-pass review of option-comparison docs specifically. Transfer to jarvis-oss is inferred.

### Confidence: 70/100

The direction replicates across independent papers: a precision deficit, decomposition failures, and anchored, ranked focus for the human. The magnitudes for jarvis-oss are inferred, several numbers come from 2026 preprints read at abstract level, and the user evidence is anecdotal.
