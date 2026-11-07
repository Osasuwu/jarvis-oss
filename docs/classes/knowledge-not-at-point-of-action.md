---
class: knowledge-not-at-point-of-action
surfaces_at:
  - task
  - code
  - ci
  - after-merge
applies_when: You work with an AI coding agent that is supposed to follow rules, conventions or lessons written down somewhere (a rules file such as CLAUDE.md or AGENTS.md, docs, a memory or notes store, a runbook, a PR template, issue history, an earlier part of a long session), and you want to know why the agent acted as if it did not know something that was written down, and what makes the right piece of knowledge present, and current, at the moment the agent acts.
applies_when_not: One fact written down in several places whose copies drift apart after a change reaches only some of them (copies of one truth drift). An agent that asserts something without checking it, where no record to check against existed (claims from plausibility). Content the agent read that told it to do something (untrusted content steers the agent). An environment that differs from the one the agent assumed (world differs from assumed). A gate that reads a missing check as a pass (absence read as success).
---

# Knowledge does not reach the point of action

## TL;DR

What the agent needed to know was written down, and it was not in front of the agent when it acted: the file was never loaded, it was cut off, it was dropped when the harness condensed the session's history into a summary (compaction), it had gone stale, or it was a sentence nobody executes. Writing the rule more clearly helps only while it stays in context. The protections that do not depend on the agent remembering move the knowledge into something that runs at the point of action, a hook, a required check, a loader that fails loudly; others check that the written record still matches what it describes; the evidence on which of them works is thin (see Evidence).

## Symptom

The agent does something the project had already decided against, and the decision is written down. A rule in the rules file says to ask before committing, and late in a long session, after a compaction, the agent commits and pushes anyway. A lesson from last week's incident sits in a notes or memory file, and the next session repeats the incident, because that session never loaded the file, or loaded an index that cut off before the line. A convention is documented, the doc went stale when the code moved, and the agent follows the doc. Context from early in a session, a constraint the user stated, the plan, the list of files not to touch, is gone after the session was compacted or a tool output was truncated, and the agent carries on without it. A rule exists only as a sentence in prose, nothing checks it, and the actor that needed it was a subagent, a CI job or a scheduled run that never read that file. Asked afterwards, the agent can find the rule and agrees it applied. The knowledge existed; it was not in front of the agent when it acted.

## Examples

The INC- codes are rows in [the incident dataset](../../incidents/incidents.csv).

- INC-653: a session-start hook injected only the first 8 KB of the project's domain-model file and cut the rest without a word; the Invariants section, past the cut, reached no session.
- INC-658: after a compaction the project's CLAUDE.md was back in context with its rule "Never create a commit the developer did not ask for.", and the summary repeated the rule; the agent committed and pushed twice without being asked, while an imperative session-start hook in the same context was followed ([anthropics/claude-code#95745](https://github.com/anthropics/claude-code/issues/95745)).
- INC-652: the same lesson about a database key was recorded in a memory store five times across sessions; the skill contract that caused the mistake was not changed, so each new session made it again.
- INC-657: a project's CLAUDE.md listed a file as the "core algorithm" after the logic had moved to other code, and agents worked on the dead file: "Both times an agent touched this area it took the Dart file for the live one." ([Osasuwu/like-current-song#161](https://github.com/Osasuwu/like-current-song/issues/161))
- INC-654: a skill said that a HIGH-risk PR waits for a human before merge, and the PR body declared HIGH; the auto-merge workflow never read the body, and the PR merged with no approval.

## Mechanism

The class splits by where between the record and the action the knowledge is lost. Four ways recur in this class's dataset rows.

**The record is never loaded by the actor.** Rules files are loaded by one harness into one kind of session. A subagent "doesn't see your conversation history" ([Claude Code subagents](https://code.claude.com/docs/en/sub-agents)), so a constraint stated only in the conversation does not reach it. A CI workflow or a bot reads no rules file at all: a policy written in a skill or a PR template binds the agent that reads it and nothing that merges (INC-654). A rules file that tells the agent in words to read another file loads that file only if the model decides to open it ([Claude Code memory](https://code.claude.com/docs/en/memory)). Loaders have caps: Claude Code loads the first 200 lines or 25KB of its memory index, and "Content beyond that threshold is not loaded at session start." ([Claude Code memory](https://code.claude.com/docs/en/memory)); Codex "stops adding files once the combined size reaches the limit", 32 KiB by default ([Codex AGENTS.md guide](https://learn.chatgpt.com/docs/agent-configuration/agents-md)). When the cut is silent, the session believes it has the whole file (INC-653, INC-661). When new entries are appended at the end, the cut drops the newest ones first, so a correction is lost while the entry it corrects stays loaded (INC-660).

**The record is lost during the session.** Compaction replaces the conversation with a summary. Claude Code re-injects the project-root CLAUDE.md, auto memory and the plan file from disk, while path-scoped rules and nested rules files are loaded into message history, "so compaction summarizes them away with everything else", and context that hooks added earlier is "Summarized with the rest of the conversation" ([Claude Code context window](https://code.claude.com/docs/en/context-window)). A summary can also carry a premise that was never agreed, and the agent acts on it as a fact (INC-650). In one report the re-injected copy of the rules file was older than the file on disk, two edits behind, for the rest of a long turn (INC-659).

**The record is loaded and stale.** A rules file written early describes the architecture as designed; the code moved and the file did not (INC-657, INC-662). A commit message states a result that the live database does not show, and the next session that reads the commit message is told the wrong state (INC-651). In both cases the record was already in the agent's context and the live code or database was one tool call away, and the agent took the record.

**The record is present and not followed.** Claude Code's documentation says it plainly: "Claude treats them as context, not enforced configuration." ([Claude Code memory](https://code.claude.com/docs/en/memory)). A rule in context competes with the task, the summary and the recent pattern of actions, and it can lose (INC-658). A practice added to skill text was used in none of the next eight PRs (INC-655). A prompt that told a review model which tools to use was not followed, and the run kept failing on permission denials (INC-656, private, not verifiable). In the terms of the hierarchy of controls, a written rule is an administrative control, one that works through what people do; NIOSH ranks it below engineering controls, which "reduce or prevent hazards from coming into contact with workers" ([NIOSH](https://www.cdc.gov/niosh/hierarchy-of-controls/about/index.html)).

**Boundaries.** A stale doc is often a second copy of a fact the code holds. When the question is why a change reached the code and not the doc, the incident belongs to [copies of one truth drift](copies-of-one-truth-drift.md); that doc covers a deleted check still described as live (its INC-159). When the question is why the agent acted on the record instead of on the live state, or never saw the record, it belongs here. An agent that asserts a fact nobody recorded is [claims from plausibility](claims-from-plausibility.md); in this class the knowledge to check against existed and was kept somewhere the agent did not look, or was cut before it arrived.

## Where it surfaces

Mostly late. Of the 13 rows of this class, 6 were caught after merge, 4 during the task, 2 while code was being written and 1 in CI (as of 2026-11, this dataset). The task-stage rows are user reports against agent harnesses, written when the user noticed in the session that the agent did not follow a written rule (INC-658, INC-659, INC-660, INC-661). The after-merge rows from one operator's repositories were found by the agent itself in a later audit or a later session: a commit message that stated the wrong state (INC-651), a lesson recorded five times (INC-652), a truncated loader (INC-653), a merge that ignored a declared risk (INC-654) and a practice nobody used (INC-655). One row surfaced in CI: the prompt of a review job told the model which tools to use, the model used others, and the job kept failing on permission denials (INC-656, private, not verifiable). In one operator's private audit of the 8 incidents of this class from their own repositories, the agent found 6 of them later, the user 1 and CI 1 (as of 2026-10, private audit).

Nothing in the diff shows the class. The knowledge that was missing is not in the change, and the change passes its own tests; the miss shows when someone compares what the agent did with what was written down.

## Protections

The rungs run from making the record more likely to be read, to delivering it at the right moment, to making it unnecessary for the agent to remember it at all. In one operator's record of 8 incidents of this class, the patches that stayed at the prose or memory level recurred in 3 of 3 cases, and the patches that put the knowledge into something executed were followed by no recurrence of their incident in 3 of 3, each with a cost or a caveat: a false block; sibling loaders left unguarded; a memory system later removed, so that incident had nowhere left to happen (as of 2026-10, private audit).

### Prompt: short, concrete rules in the file that always loads

- **Source:** [Claude Code memory](https://code.claude.com/docs/en/memory) recommends a target "under 200 lines per CLAUDE.md file" and says "Longer files consume more context and reduce adherence."; [Cursor rules](https://cursor.com/docs/context/rules) and the [Codex AGENTS.md guide](https://learn.chatgpt.com/docs/agent-configuration/agents-md) give their own size guidance.
- **Cost:** context tokens in every session; curation time to keep the file short.
- **Breaks when:** the rule is in context and the agent does not follow it (INC-658); the session is long: in one study compliance fell as the agent generated more code ([McMillan](https://arxiv.org/abs/2605.10039), in Evidence); the actor is not an agent that loads this file (INC-654).

### Deliver the rule where the actor starts

- **Source:** [Claude Code subagents](https://code.claude.com/docs/en/sub-agents): if a rule must reach a subagent, "restate it in the prompt you give Claude when delegating"; [Claude Code memory](https://code.claude.com/docs/en/memory) tells you to replace a sentence that says "read AGENTS.md" with an import, because a file named in prose is read only if the model decides to open it.
- **Cost:** tokens per delegation; knowing which rule the delegated task needs.
- **Breaks when:** the delegating agent does not know which rule applies; the actor is a workflow or a bot that reads no prompt and no rules file (INC-654), which needs the last rung, "Enforce on the artifact in a required check".

### Scope rules to the files they govern

- **Source:** [Claude Code path-scoped rules](https://code.claude.com/docs/en/memory) "only apply when Claude is working with files matching the specified patterns"; nested AGENTS.md files apply nearest-first ([agents.md](https://agents.md/)); [GitHub Copilot path-specific instructions](https://docs.github.com/en/copilot/how-tos/configure-custom-instructions/add-repository-instructions) do the same with `applyTo`.
- **Cost:** authoring the scopes; it saves always-loaded tokens.
- **Breaks when:** compaction summarizes the scoped rule away until the trigger file is read again ([Claude Code context window](https://code.claude.com/docs/en/context-window)); the rule governs an action that is not tied to a file, such as a commit or a merge; a mode that loads a rule only when the model judges it relevant leaves the choice to the model ([Cursor rules](https://cursor.com/docs/context/rules)).

### Know the loader's limits and make a cut visible

- **Source:** the caps are documented: Claude Code's memory index ([Claude Code memory](https://code.claude.com/docs/en/memory)) and Codex's combined AGENTS.md size, whose guide tells you to raise the limit "or split large files across nested directories to keep critical guidance intact" ([Codex AGENTS.md guide](https://learn.chatgpt.com/docs/agent-configuration/agents-md)). Claude Code's `InstructionsLoaded` hook can log which rules files loaded, when and why ([Claude Code memory](https://code.claude.com/docs/en/memory)). A loader you write yourself prints how many bytes it dropped, inside the injected text: one operator's practice (INC-653).
- **Cost:** a size check per loaded file, or a logging hook; nearly no tokens.
- **Breaks when:** the cut is silent and nobody measures it (INC-653, INC-661); entries are appended at the end of a capped index, so the cut takes the newest first (INC-660); the hook does not fire for every load path: the same page says `InstructionsLoaded` does not fire for an AGENTS.md read through the project-instructions setting.

### Put context back after compaction

- **Source:** [Claude Code hooks guide](https://code.claude.com/docs/en/hooks-guide): compaction "can lose important details", and a `SessionStart` hook with the `compact` matcher re-injects context after every compaction; `PreCompact` and `PostCompact` hooks run around it ([hooks reference](https://code.claude.com/docs/en/hooks)); `/compact` with a focus keeps what you choose ([Claude Code context window](https://code.claude.com/docs/en/context-window)).
- **Cost:** a hook script; its output in tokens after each compaction.
- **Breaks when:** the re-injected text is itself summarized at the next compaction, as context hooks added earlier is ([Claude Code context window](https://code.claude.com/docs/en/context-window)); the re-injected copy is older than the file on disk (INC-659); the rule is back in context and is still not followed (INC-658).

### Keep state on disk and read the live source before acting

- **Source:** [Anthropic, effective context engineering](https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents) describes structured note-taking, where "the agent regularly writes notes persisted to memory outside of the context window", and just-in-time retrieval of data by identifier instead of loading it all up front; [Anthropic, effective harnesses for long-running agents](https://www.anthropic.com/engineering/effective-harnesses-for-long-running-agents) keeps a progress file and a feature list across context windows.
- **Cost:** tool calls and tokens for each re-read.
- **Breaks when:** the agent acts on a premise from the summary without re-reading (INC-650); the narrative is cheaper to read than the system of record, and the agent reads the narrative (INC-651); a summary keeps the conclusions and loses which files changed, the weakest dimension for every method in one evaluation ([Factory](https://factory.com/news/evaluating-compression), in Evidence).

### Give docs an owner and a date, and check what they reference

- **Source:** Google attaches freshness dates and owners to docs: "Documents without owners become stale and difficult to maintain." ([Software Engineering at Google, ch. 10](https://abseil.io/resources/swe-book/html/ch10.html)); [lychee](https://lychee.cli.rs/) checks links in CI; [Treude and Baltes](https://arxiv.org/html/2606.09090v1) ran a stale-reference detector (DOCER) over AI configuration files and flagged code elements that a file names and that no longer exist.
- **Cost:** a CI step and owner time; the unmodified detector's flags were 64% genuine on a 50-item sample (as of 2026-06, [Treude and Baltes](https://arxiv.org/html/2606.09090v1)), so it needs an allowlist.
- **Breaks when:** the referenced file still exists and no longer describes what runs: the detector checks existence only, so the dead "core algorithm" of INC-657 would pass; a freshness date passes and nobody reviews the doc.

### Route each lesson into what runs at the failure point

- **Source:** [TRACE](https://arxiv.org/abs/2606.13174v1) (renamed TellOnce in the paper's second version) turns a user's corrections to a coding agent into rules plus runtime checks where the check can be verified reliably, instead of only storing them as memory, and reports fewer violations than a memory baseline (in Evidence).
- **Cost:** writing and testing a check per lesson; triage time to decide which lessons can be checked.
- **Breaks when:** the lesson cannot be checked mechanically, so it stays a prose rule; nobody does the routing, and the lesson is recorded again instead (INC-652, five times).

### Enforce at the tool call

- **Source:** [Claude Code best practices](https://code.claude.com/docs/en/best-practices): "If something has to happen every time, make it a hook, a deny rule, or a managed setting.", and if emphasis on a CLAUDE.md line keeps failing, "that's a sign the line belongs in a hook"; "To block an action regardless of what Claude decides, use a PreToolUse hook instead." ([Claude Code memory](https://code.claude.com/docs/en/memory)).
- **Cost:** writing and maintaining the hook; it adds no tokens.
- **Breaks when:** the rule requires a step instead of forbidding an action, so skipping it makes no tool call to intercept, as with INC-655's practice of checking a test by breaking the code under it, which no PR ran; the hook matches only some spellings of the action, as a merge guard did that recognised the subcommand only in first position ([pattern-matched boundaries](pattern-matched-boundaries.md), its INC-036); the hook is wired to one tool and the action goes through another, as a secret scanner did that ran on one shell tool and not the other (the same doc, its INC-035). Where the rule exists to stop an action that cannot be undone, this rung and the next depend on [irreversible effects](irreversible-effects.md).

### Enforce on the artifact in a required check

- **Source:** Google ships static checks as build errors or not at all: "We have found repeatedly that developers ignore compiler warnings." ([Software Engineering at Google, ch. 20](https://abseil.io/resources/swe-book/html/ch20.html)); on a protected branch "all required status checks must pass before collaborators can merge changes" ([GitHub, about protected branches](https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/managing-protected-branches/about-protected-branches)), so a required check that reads the PR body or diff turns a policy written in a template into something that can stop the merge (INC-654).
- **Cost:** CI minutes; a person for each false block; Google's criterion is under 10% effective false positives (as of 2020-03, [Software Engineering at Google, ch. 20](https://abseil.io/resources/swe-book/html/ch20.html)).
- **Breaks when:** the check's parser misreads the artifact and blocks good PRs, so people route around it; the check is written and never wired into a required job: in one operator's record the script meant to enforce INC-655's practice still had no caller a month later (as of 2026-10, private audit); the policy cannot be read off the artifact.

## Evidence

The evidence that agents miss written knowledge is measured, mostly on general instruction-following and on rules files. Evidence on which protection works is thin: one study tests compiling corrections into checks, and the search for this doc, which was not aimed at evidence on protections (web search, arXiv and the agent harnesses' issue trackers; the queries are listed in [Osasuwu/jarvis-oss#326](https://github.com/Osasuwu/jarvis-oss/pull/326)), found none that tests a re-injection hook or a doc-freshness practice.

- **Compliance falls with the number of instructions.** On IFScale, "even the best frontier models only achieve 68% accuracy at the max density of 500 instructions", with a bias towards earlier instructions (as of 2025-07, [Jaroslawicz et al.](https://arxiv.org/abs/2507.11538)).
- **Within a coding session, compliance falls as the agent writes more.** Across 1,650 Claude Code sessions, none of four file-structure variables (size, instruction position, architecture, contradictions in adjacent files) had a detectable effect, while each additional function the agent generated was associated with about 5.6% lower odds of compliance; the effect was found during analysis, not pre-specified, and the paper reports it as not constant from one function to the next (as of 2026-05, [McMillan](https://arxiv.org/abs/2605.10039)).
- **Rules that tell the agent to stop are not followed from prose.** On repository contribution rules, two kinds of rule tell the agent not to finish on its own: Refuse, where the repository bans AI-generated contributions, and Handoff, where a protected step must go to a human. The share of compliant runs was zero for both: "Refuse and Handoff sit at a uniform 0% across all four models"; in unaided runs on tasks whose policy was not in a file the agent loads automatically, the agent opened the policy file in 12 of 347 runs, 3.5% (as of 2026-07, [Yang, He and Zhou](https://arxiv.org/html/2607.26819v1)).
- **Context files cost more than they return on average.** Providing repository context files "does not generally improve task success rates" and raised inference cost by over 20% on average, while the instructions in them were well followed (as of 2026-02, [Gloaguen et al.](https://arxiv.org/abs/2602.11988)).
- **Compiled checks beat memory on corrections.** In the paper's first version, TRACE reduced held-out preference violations from 100.0% to 37.6% in-distribution and to 2.0% out-of-distribution, while a memory baseline (Mem0) left 57.5% of applicable checks violated (as of 2026-06, [Zhou et al.](https://arxiv.org/abs/2606.13174v1)).
- **Rules files go stale.** A stale-reference detector found stale code element references in 23.0% of the repositories analysed; the authors read it as a feasibility signal, not a precise prevalence (as of 2026-06, [Treude and Baltes](https://arxiv.org/html/2606.09090v1)).
- **Compaction loses track of files.** Over 36,611 production messages, three compression methods scored 3.35 to 3.70 of 5 overall, and "Artifact trail is the weakest dimension for all methods, ranging from 2.19 to 2.45." (as of 2025-12, [Factory](https://factory.com/news/evaluating-compression)). The same search, also not aimed at that, found no controlled study of what compaction loses for rules.
- **One operator's patches.** In a private audit of 8 incidents of this class, prose and memory patches recurred in 3 of 3 cases and executed patches were followed by no recurrence of their incident in 3 of 3, one of them partly because the system it guarded was removed (as of 2026-10, private audit). The audit is not verifiable; its one private row is in the dataset as "private, not verifiable".
