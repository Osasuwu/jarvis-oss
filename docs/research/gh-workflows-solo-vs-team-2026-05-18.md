---
title: gh CLI + GitHub workflows — solo vs team patterns
date: 2026-05-18
status: draft
depth: deep-dive
sources_count: 25
adjacent_topics_flagged:
  - IssueOps as a generic automation layer (Issue forms + state machine + Actions)
  - Stacked PRs for AI-agent-generated work (each agent slice becomes one PR in the stack)
  - GitHub Projects v2 API as a personal scheduler (custom fields + filter syntax)
  - Architectural decision capture: ADR-in-repo vs Discussion-as-RFC vs decision-log-in-memory
  - "Ralph loop" pattern (Geoffrey Huntley) — single-agent issue funnel with planning/building separation
  - Solo-dev branch protection via Rulesets + bypass list (the CODEOWNERS self-approval problem)
  - Sapling / gh-stack as a viable solo workflow once features routinely exceed 200-400 LOC
  - Continuous deployment ("15 min or bust") for solo projects — feasibility vs ROI
---

## TL;DR

The user's current model — **issues-as-state-store, milestones-as-capability-units, PRs-for-code, Discussions-for-RFC, decisions-in-memory, "fix > track" for trivial reversibles, conventional-commit-ish enforcement** — is already more disciplined than what the field calls "good solo dev practice" and converges on a small set of team patterns worth keeping (CODEOWNERS, branch protection via Rulesets with bypass, ADR-style decisions, PR templates). What's missing:

- **No explicit IssueOps layer.** The user has issues + Actions but doesn't yet use issue *forms* + *labels-as-state-machine* + *comment-as-command* as an automation surface (GitHub blog: "comments, labels, state changes to kick off CI/CD, assign tasks, deploy"). For an agentic system this is the single highest-leverage missing primitive.
- **Stacked PRs not adopted.** With Copilot/Claude agents generating multi-slice work, `gh stack` (April 2026 private preview) or Graphite/Sapling will start to matter once slices routinely exceed 200-400 LOC — the size that "gets approved 3x faster" per InfoQ.
- **Branch protection is implicit.** Pre-commit hooks are not branch protection. Self-approving via CODEOWNERS is broken on classic protection — Rulesets with a bypass list is the correct primitive.
- **gh-dash + gh-poi not in the loop.** Cross-repo PR/issue dashboard + auto-prune-merged-branches would shave real friction on a 3-device, 2-repo setup.

What to **drop or not adopt**: full Git Flow, GitHub Projects v2 sprint planning (the user already uses milestones-as-capability — sprint-overlay is double-tracking), tracking-issue/epic-issue hierarchies (term "epic" is already banned and the milestone is the only grouping primitive — keep it that way), ADR-as-markdown-in-repo (the user explicitly uses `record_decision` to memory; markdown ADRs would double-track and decay).

## Landscape

### What "solo dev GitHub" actually looks like in 2026

Three flavors dominate, and most blog posts cargo-cult between them:

**1. Minimalist / "centralized workflow".** Commit straight to main, no branches, no PRs. The Solo Developer's Manifesto (fawazahmed0) takes this position: "commits on one main branch to avoid merge hell (don't keep multiple feature branch)." This is the position of people whose projects are <1KLOC hobbyware. It does not scale to anything an AI agent is generating in parallel.

**2. "Solo as a fake team".** Branches per feature, PRs even when no one will review, self-merge after CI. Jonathan Hall ([Solo DevOps](https://jhall.io/posts/solo-devops/)) is the canonical advocate: "creating branches for each change and using pull requests, even as a solo developer ... provides benefits like cleaner git history, self-code review opportunities, and the ability to rework commits before merging." This is also the GitHub Pro for Solo Devs Medium post position: "Treat your projects like production software." The user's setup is this flavor.

**3. Agent-oriented loop.** Issue → assigned to coding agent → draft PR → human reviews and merges. GitHub Copilot Coding Agent (GA Sept 2025), Devin, Claude Code with `/implement`, and Mitchell Hashimoto's Ghostty workflow are all variations. Hashimoto: "Most mornings, he sends new GitHub issues to an AI agent for a first pass, with a hit rate hovering around 10 to 20 percent" ([Pragmatic Engineer](https://newsletter.pragmaticengineer.com/p/mitchell-hashimoto)). The user is here — and the issue/PR shape has to be *agent-readable* (small slices, explicit AC, linked decision rationale).

### Where solo and team genuinely differ

| Concern | Team | Solo | Why |
|---|---|---|---|
| **Code review** | Required reviewer ≠ author | Self-review via draft PR + Copilot/CodeRabbit | No second human to recruit; outsource the second pair of eyes to a bot |
| **CODEOWNERS** | Routes review load by ownership area | Single owner = noop on classic branch protection (you can't approve your own PR) | GitHub Discussion #14866 documents this exact gap. Rulesets with bypass is the workaround |
| **ADR / RFC** | Asynchronous decision socialization across timezones | Memory aid for future-you ("CHANGELOG is a love letter to the future maintainer, even if that maintainer is just you in nine months") | Same artifact, very different reader |
| **Milestones** | Release planning, often date-boxed | Capability units, semantic close (user's `milestone_hierarchy_v3` is exactly this) | Solo has no release calendar pressure |
| **PR size discipline** | Reviewer attention budget | Bisect/revert ergonomics, agent context window | Different forcing functions, same answer: small PRs |
| **Branch protection** | Compliance, audit, blast radius | Mostly prevents accidental `git push --force` to main | Solo benefits exist but smaller |

### Where solo cargo-cults team patterns

Things solo devs adopt because "professionals do it" but that often add friction without value:

- **Squash-merge by default.** Mitchell Hashimoto's [gist](https://gist.github.com/mitchellh/319019b1b8aac9110fcfb1862e0c97fb) argues against this for 9/10 PRs: merge commits preserve WIP history, enable `git revert -mN`, and aid bisecting. Solo devs lose nothing by keeping merge commits since there's no "ugly history in front of reviewers" problem.
- **Full Conventional Commits with scopes.** Releasepad et al. note the convention pays off for changelog automation. But strict scopes/footers add typing overhead. The user's `feat(grill):`, `docs(claude.md):` style is the sweet spot.
- **GitHub Projects v2 sprint planning.** Bitovi article: "Backlog table + Kanban board with Ready/In Progress/Done." For a solo dev with milestones-as-capability, this is double-tracking. Projects v2 *is* useful for cross-repo views and custom fields — but as a *roll-up*, not a sprint board.
- **Issue templates with 10 fields.** Issue forms are powerful (`assignees`, `labels`, structured data for IssueOps automation) but most solo repos copy them from a public-project template and never trigger off the fields.

### Where solo *under*-adopts team patterns

The flip side. Things teams do that solo devs benefit from but rarely set up:

- **IssueOps (label-as-state-machine).** The GitHub blog [IssueOps post](https://github.blog/engineering/issueops-automate-ci-cd-and-more-with-github-issues-and-actions/) defines issues as a control surface: "Instead of switching between tools or manually triggering actions, you can use issue comments, labels, and state changes to kick off CI/CD pipelines, assign tasks, and even deploy applications." For an agentic system this is the right abstraction layer for `/implement`, `/delegate`, `/verify`.
- **Branch protection that actually protects.** Classic branch protection blocks self-approval. Rulesets with a CODEOWNER bypass list ([community discussion #14866](https://github.com/orgs/community/discussions/14866)) is the documented workaround — "only the CODEOWNERS can push without review while everyone else needs a review." For a solo dev with AI agents pushing branches, this matters: agents are "everyone else."
- **ADR-style decision records.** Teams do this for socialization across people. Solo devs do it for socialization across *sessions* (and across humans+agents). The user's `record_decision` MCP tool is functionally ADRs in a queryable store — better than markdown.
- **Stacked PRs once features grow.** [InfoQ on gh-stack](https://www.infoq.com/news/2026/04/github-stacked-prs/): "Features exceed 400 lines of code ... 200-400 line PRs get approved three times faster." Solo devs miss the "faster approval" framing because they self-approve — but they still benefit from the *cognitive* split into reviewable chunks and the fact that AI agents can break features into logical layers if you let them.

### What the data actually says

The honest answer to "what works for solo devs" is mostly anecdotal. Hard data points:

- **Greptile 2025 benchmarks**: AI reviewers vary wildly. Greptile 82% catch rate vs Copilot mid-50s vs CodeRabbit 44%. Higher catch rate = more false positives. Solo devs end up tuning for signal/noise rather than absolute catch rate.
- **GitHub's own data on PR size** (cited by InfoQ): 200-400 line PRs merge ~3x faster than larger ones. Same forcing function applies to solo devs trying to keep their *own* context window manageable.
- **Charity Majors' "15 minutes or bust"**: any merge → automatic deploy to prod in <15 min. For solo SaaS this is achievable; for solo OSS libraries it's mostly irrelevant (release cadence is different).
- **Hashimoto's 10-20% agent hit rate** on Ghostty issues: useful baseline for what "agent triages issues for me overnight" actually returns.

### The agentic shift

The most important 2025-2026 shift: GitHub stopped being primarily a *human collaboration tool* and started being a *primary control surface for AI agents*. Mitchell Hashimoto, Geoffrey Huntley ("Ralph Wiggum technique" / "thousands of automated AI robots that autonomously maintain codebases"), and the entire Copilot Coding Agent product line all converge on:

1. **Issue = task spec** (machine-parseable AC, explicit decision links).
2. **Draft PR = checkpoint** (CI runs, human can intervene at any granularity).
3. **Discussions = the RFC layer** where humans still drive direction.
4. **Memory / decision log** = the "why" layer that doesn't live in any of the above.

The user's split — issues for state, PRs for code, Discussions for RFC, memory for decisions — *is* this shape. The implementation is a question of how thoroughly to mechanize each boundary with IssueOps + Actions + bots.

## Concrete patterns / recipes

### 1. Simon Willison's daily-planner issue automation

- **Source**: [til.simonwillison.net/github-actions/daily-planner](https://til.simonwillison.net/github-actions/daily-planner)
- **How it works**: Issue template (`.github/ISSUE_TEMPLATE/day.yml`) defines a structured daily-planning form. GitHub Action rebuilds `issue-titles.json` (deployed to GitHub Pages) on every issue create. A client-side JS page checks today's date against the JSON: if today's issue exists, redirect; otherwise open the form pre-populated. Result: one keyboard shortcut opens or creates today's planner.
- **Fit**: Pattern is "GitHub issues as queryable personal database, with a static-site router on top." Generalizes to any repeating task class (weekly review, deploy log, decision log). For the user: the same approach could host a `status-snapshot` dashboard.

### 2. Mitchell Hashimoto's "warm start" triage

- **Source**: [Pragmatic Engineer newsletter](https://newsletter.pragmaticengineer.com/p/mitchell-hashimoto), [Serenities AI summary](https://serenitiesai.com/articles/mitchell-hashimoto-ai-workflow)
- **How it works**: Overnight job sends new Ghostty issues to an AI agent for a first pass. Morning, he reads the agent's triage notes (`gh` CLI reports) and prioritizes. Multiple agents run in parallel on planning tasks; he hand-merges their best parts. If the agent writes code he can't reproduce manually, he stops and learns.
- **Fit**: Maps directly to `/autonomous-loop` + `/delegate` chain. The "learn what the agent did" discipline is the missing piece in most agent setups. For the user: an explicit "if you can't recreate the diff, halt" gate inside `/implement`.

### 3. Geoffrey Huntley's Ralph Wiggum loop

- **Source**: [ghuntley.com/ktlo/](https://ghuntley.com/ktlo/), [how-to-ralph-wiggum repo](https://github.com/ghuntley/how-to-ralph-wiggum)
- **How it works**: "3 Phases, 2 Prompts, 1 Loop." Phase 1: PLANNING prompt does gap analysis (specs vs code), outputs prioritized TODO. Phase 2: BUILDING prompt picks from TODO, implements, runs tests (back-pressure), commits. Loop until done. Subagents spawn dedicated context windows for build/test ("async/await state machines for LLMs").
- **Fit**: The user's `/grill → /to-prd → /to-issues → /implement` chain *is* a richer version of this loop with HITL gates. Worth importing: the "PLANNING does no commits, BUILDING does no planning" hard separation, and the "fail loud and re-enter loop" pattern instead of single-shot perfectionism.

### 4. IssueOps as the agentic primitive

- **Source**: [github.blog IssueOps post](https://github.blog/engineering/issueops-automate-ci-cd-and-more-with-github-issues-and-actions/), [issue-ops.github.io](https://issue-ops.github.io/docs/introduction/issues-and-prs)
- **How it works**: Issues become finite state machines. Labels track state (`triage` → `ready` → `in-progress` → `verifying` → `done`). Comments are commands (`/approve`, `/deploy`, `/retry`). Issue forms collect structured data. Actions trigger on label/comment events.
- **Fit**: Highest-leverage missing piece in the user's setup. Concrete first cut: define `ready:agent` label that triggers a workflow which `gh issue assign`s to a coding agent. Comment-driven commands (`/verify`, `/grill`) become Action triggers. State transitions become audit log.

### 5. Stacked PRs (gh-stack / Graphite / Sapling)

- **Source**: [InfoQ on gh-stack](https://www.infoq.com/news/2026/04/github-stacked-prs/), [Graphite stacked diffs guide](https://graphite.com/guides/stacked-diffs), [Codex blog: Stacked PRs Meet Coding Agents](https://codex.danielvaughan.com/2026/04/16/stacked-prs-coding-agents-gh-stack-sapling-codex-skill/)
- **How it works**: Break a large change into a stack of dependent PRs, each <400 LOC. `gh stack sync` auto-rebases the stack when an earlier PR merges. Branch protection runs against each PR as if targeting main directly.
- **Fit**: Becomes relevant when `/implement` slices grow beyond 200-400 LOC, or when a milestone's slices have hard dependency order (slice 2 needs slice 1's API). Skip for the user today (slices look small from recent commit log), revisit when a milestone has 4+ ordered slices.

### 6. gh-dash + gh-poi power-user combo

- **Source**: [gh-dash repo](https://github.com/dlvhdr/gh-dash), [gh-poi repo](https://github.com/seachicken/gh-poi), [awesome-gh-cli-extensions](https://github.com/kodepandai/awesome-gh-cli-extensions)
- **How it works**: `gh-dash` = customizable terminal dashboard of PRs/issues across repos, vim keys, supports multiple config files for different "personas" (work, side-project, agent-supervision). `gh-poi` = safely deletes local branches that are merged, including squash-merges (which lack traditional merge history).
- **Fit**: For a solo dev across 3 devices and 2+ repos (jarvis + redrobot), `gh-dash` gives a single cross-repo view that replaces tab-shuffling. `gh-poi` solves the "30 stale local branches after a sprint of `/delegate`" problem cleanly. Both install in seconds.

### 7. Rulesets with bypass for self-approval

- **Source**: [community discussion #14866](https://github.com/orgs/community/discussions/14866), [GitHub branch protection docs](https://docs.github.com/en/repositories/managing-your-repositorys-settings-and-features/customizing-your-repository/about-code-owners)
- **How it works**: Classic branch protection blocks code owners from approving their own PRs. Rulesets (new branch protection primitive) support a *bypass list* — listed actors can push without review while everyone else still needs review. Solo dev bypasses; AI agents go through the gate.
- **Fit**: If/when the user wants real branch protection that distinguishes "me hotfixing on main" from "agent pushing untested code," this is the primitive. Cost: ~5 min to configure per repo. Benefit: prevents `git push --force` to main accidents, gates agent commits behind required-checks. Don't bother unless agent commits are routinely landing without human review.

## What this user should consider given his context

**Audit against the field:**

- *Milestones as capability units* — correct, matches Hashimoto/Huntley framing of "ship-when-capability-shipped." Field has no better pattern; the user's `milestone_hierarchy_v3` (pillar → goal → milestone → slice) is more rigorous than typical.
- *Issues as state store* — correct, the user's "no state in static storage" rule aligns with everything from IssueOps to ADR-as-decision-log. The user is more disciplined than most.
- *Discussions for RFC* — correct and aligned with both team practice (ADRs-via-Discussions Action) and his own `record_decision` flow.
- *"Fix > track" for trivial reversibles* — well-supported by the field (Mergify, Jonathan Hall on solo DevOps). Most solo devs over-issue.
- *Pre-commit hook enforcement of linked issue or `[no-issue]` label* — this is *team-level* discipline ported correctly. Without it, the agent stream would generate orphan commits.

**Worth importing from teams (high-leverage, low cost):**

1. **IssueOps layer.** Label-as-state-machine + comment-as-command + issue-forms-with-structured-data. Concrete first move: pick one workflow (e.g. `/verify` runs when label `needs-verify` is added) and wire one Action. This is the single biggest unrealized leverage in his setup.
2. **gh-dash + gh-poi.** Cross-repo PR/issue dashboard + auto-prune merged branches. ~10 min setup, daily payoff. Install on the most-used device first.
3. **Rulesets with bypass** — only if/when agents are pushing without HITL. Today probably overkill; mark as planned.
4. **Stacked PRs (`gh stack` when it leaves preview, or Sapling now)** — defer until a milestone needs 4+ ordered slices. Not urgent.

**Worth *not* importing (would double-track):**

- ADR markdown files in `docs/adr/`. He uses `record_decision` already — markdown ADRs would decay against memory.
- GitHub Projects v2 sprint planning. Milestones already do this; Projects v2 only helps for *cross-repo* roll-ups (jarvis + redrobot).
- Conventional Commits with strict scopes/footers. His current `feat(grill):` discipline is the right level.
- Tracking/meta/epic issue terminology. He's already banned "epic" — keep milestones as the only grouping primitive.

**Calibration on agent loops:** Hashimoto's 10-20% hit rate is the realistic baseline; if the user's `/delegate` shows materially better, it's because of the grill chain and TDD mode (and the verification gate). Worth measuring: % of agent PRs merged unchanged vs needing rework. That's the metric that tells you whether the grill chain is paying for itself.

## Adjacent topics worth deeper research

- **IssueOps catalog**: what state machines work for `/implement`, `/delegate`, `/verify`? Concrete YAML for the first three workflows would be its own research bundle.
- **gh-stack vs Sapling vs Graphite for AI-agent-generated work**: which tool's stack-rebase semantics tolerate agents force-pushing mid-stack? Likely worth a hands-on bake-off.
- **Decision-log as memory vs ADR-as-markdown**: the user picked memory. The trade-off (queryable but invisible to GitHub UI) deserves a 90-day audit — how often does `decision_uuids[]` actually get queried from a PR body vs just from a session?
- **"Ralph loop" for KTLO maintenance**: Huntley's vision of autonomous codebase maintenance roombas. The user's `/autonomous-loop` is the seed. Question: what KTLO tasks (CHANGELOG updates, stale doc detection, sibling-grep on accepted fixes) are safe to fully automate?
- **Cross-device gh CLI state sync**: gh-dash configs, custom aliases, extensions — 3 devices, how is state kept in sync? `install.ps1 -Apply` mentioned in user-level CLAUDE.md handles dotfiles; does it cover gh config?

## Sources

1. [Simon Willison — GitHub Actions, Issues and Pages to build a daily planner](https://til.simonwillison.net/github-actions/daily-planner)
2. [Pragmatic Engineer — Mitchell Hashimoto's new way of writing code](https://newsletter.pragmaticengineer.com/p/mitchell-hashimoto)
3. [Serenities AI — Mitchell Hashimoto's AI Workflow](https://serenitiesai.com/articles/mitchell-hashimoto-ai-workflow)
4. [Mitchell Hashimoto — Merge vs. Rebase vs. Squash gist](https://gist.github.com/mitchellh/319019b1b8aac9110fcfb1862e0c97fb)
5. [Geoffrey Huntley — I dream of roombas (KTLO automation)](https://ghuntley.com/ktlo/)
6. [Geoffrey Huntley — how-to-ralph-wiggum repo](https://github.com/ghuntley/how-to-ralph-wiggum)
7. [Bitovi — Project Management for One: GitHub Projects for Solo Developers](https://www.bitovi.com/blog/github-projects-for-solo-developers)
8. [fawazahmed0 — Solo Developers Manifesto](https://github.com/fawazahmed0/the-solo-developers-manifesto)
9. [Jonathan Hall — Solo DevOps](https://jhall.io/posts/solo-devops/)
10. [Vignaraj Ravi — GitHub Pro for Solo Devs](https://medium.com/@vignarajj/github-pro-for-solo-devs-automate-like-a-team-publish-like-a-studio-ship-without-stress-bd5cab2e649c)
11. [dasroot.net — Git Workflows for Solo Developers and Content Creators](https://dasroot.net/posts/2026/03/git-workflows-solo-developers-content-creators/)
12. [Keno Kivabe — GitHub CLI for Power Users](https://blogs.kenokivabe.com/article/github-cli-for-power-users)
13. [GitHub Blog — New GitHub CLI extension tools](https://github.blog/developer-skills/github/new-github-cli-extension-tools/)
14. [GitHub Blog — IssueOps: Automate CI/CD with GitHub Issues and Actions](https://github.blog/engineering/issueops-automate-ci-cd-and-more-with-github-issues-and-actions/)
15. [GitHub Blog — From idea to PR: Copilot agentic workflows](https://github.blog/ai-and-ml/github-copilot/from-idea-to-pr-a-guide-to-github-copilots-agentic-workflows/)
16. [GitHub Blog — Why write ADRs](https://github.blog/engineering/architecture-optimization/why-write-adrs/)
17. [InfoQ — GitHub Targets Large Merge Problem with Stacked PRs](https://www.infoq.com/news/2026/04/github-stacked-prs/)
18. [Graphite — Stacked diffs guide](https://graphite.com/guides/stacked-diffs)
19. [Codex blog — Stacked PRs Meet Coding Agents](https://codex.danielvaughan.com/2026/04/16/stacked-prs-coding-agents-gh-stack-sapling-codex-skill/)
20. [GitHub Community — Allow code owners to review their own PRs (#14866)](https://github.com/orgs/community/discussions/14866)
21. [GitHub Docs — About code owners](https://docs.github.com/en/repositories/managing-your-repositorys-settings-and-features/customizing-your-repository/about-code-owners)
22. [gh-dash repo](https://github.com/dlvhdr/gh-dash)
23. [gh-poi repo](https://github.com/seachicken/gh-poi)
24. [kodepandai — awesome-gh-cli-extensions](https://github.com/kodepandai/awesome-gh-cli-extensions)
25. [Charity Majors — observability + continuous deployment ("15 minutes or bust")](https://charity.wtf/category/observability/)
