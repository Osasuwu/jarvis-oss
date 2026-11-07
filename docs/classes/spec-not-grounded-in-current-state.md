---
class: spec-not-grounded-in-current-state
surfaces_at:
  - task
  - code
  - review
applies_when: An AI coding agent works from a task (an issue, a ticket, a spec, a plan broken into tickets, often itself written by an agent) whose acceptance criteria or premises do not match the system as it is now. A criterion cannot be reached, or two criteria contradict each other; a criterion can only be checked by watching the system after deploy, which the task's pull request cannot do; the task assumes a function, a file, a label, a workflow or a permission that nobody built, or that has since changed; the order of tickets, or which one blocks which, is wrong; a ticket is scoped narrower than the change it belongs to, so the old state survives outside its scope. You want to know why the work stalls, builds a missing piece, moves a criterion out or reports done against a criterion that was wrong from the start, and what catches the task before the work starts.
applies_when_not: A change that works where it was tried and fails where it runs (the world differs from the assumed one). Work reported done whose effect never happens, against a criterion that was right (done is not the effect). An agent that bends a sound criterion until it passes (proxy optimisation). A rule or a doc the agent read that was out of date, outside the task (knowledge does not reach the point of action). An agent that changes more than the task asked (scope overreach).
---

# A spec not grounded in the current state

## TL;DR

The task itself was written against a state that does not hold: a criterion that cannot be reached within the task's scope, one that needs an observation after deploy, an interface nobody built, a blocker that is the wrong ticket, a ticket scoped narrower than the change it belongs to. The agent stops, builds the missing piece, moves the criterion to a follow-up, or reports done against it. What stops it is reading the task against the repository before the work starts: a check of each criterion's premises against the current tree and the other open tickets, a stop the agent is allowed to take, and post-deploy criteria split off into their own tracked item.

## Symptom

The task was wrong before any code was written. The agent picks up an issue or a ticket and works to its acceptance criteria, and somewhere along the way the criteria stop making sense:

- a criterion cannot be met as written: it points at a precedent or a file that is not in the repo, asks for a check that the task's own scope cannot pass, or needs a permission the agent it was routed to does not have;
- a criterion can only be checked after deploy (a scheduled run, a label that applies only once a runner re-registers, a user's report), so the pull request merges with that criterion still open or moved to a follow-up;
- the task names a function, an endpoint, a label, a workflow or a file that the spec assumed and nobody built, or that another ticket removed after this one was written;
- a plan was broken into tickets, and which ticket blocks which is wrong, so a ticket starts before the one it depends on;
- the plan's tickets cover less of the system than the change they make together, so each ticket's criterion passes while the old state survives outside its scope.

The agent then stops and asks, or builds the missing piece, or moves the criterion to a follow-up, or reports done against the criterion as written. In each case the fault was in the task, and it was there when the task was filed.

## Examples

Each `INC-` ID is a row in [`incidents/incidents.csv`](../../incidents/incidents.csv), which links its source and says how strong that source is.

- INC-700: a criterion asked for a symlink set up the way an existing one already was; that precedent existed only on the author's machine, untracked, and the headless executor (an agent a workflow starts, with no person watching) stopped without a pull request, twice: the second time after a clarifying comment on the issue.
- INC-707: the criterion of one ticket, in a plan that among other things paused a database and removed the code that used it, was a repository-wide `grep` for that database's name returning zero; agents reviewing the implementation plan found the name live and correct in code outside that ticket's scope, so the check could not pass, and the larger removal became its own issue.
- INC-706: one ticket's criterion required the trial pull request to merge by auto-merge; another ticket of the same plan had already deleted the only workflow that enabled auto-merge, so the criterion was, in the words of the [pull request](https://github.com/Osasuwu/jarvis/pull/1843), "unreachable by construction".
- INC-708: a ticket of the same plan, removing one repository's references to retired memory tools, was scoped to one file, and its criterion checked that file with `grep` for them; two other files went on telling agents to call one of those tools, and a review bot mentioned them but did not report a finding, since it rated them below its own threshold for reporting one.
- INC-703: an issue marked safe for the unattended worker, an agent of the same kind, needed an edit to a file on that worker's list of paths it may not edit; the worker refused it, and the issue waited almost four weeks for a local agent, one started on a person's machine and not by a workflow.

## Mechanism

**A criterion records what the author believes is there, not a reading of it.** Whoever writes the task, a person or an agent cutting a plan into tickets, writes the criterion from their picture of the system. Nothing in the task says when that picture was taken or whether anyone looked. INC-700's precedent was on the author's machine and not in the repository. INC-701's issue named its decision record "ADR 0002", a number a file in the repository already had; the agent that claimed the issue found it, and the other issues that referred to the record by the old number had to be read as meaning the new one. INC-707's repository-wide check was written for a ticket whose scope covered only part of the repository.

**Tickets cut at one moment change the state each other assumed.** A plan broken into tickets is grounded, at best, in the state of the day it was cut. Each ticket that lands changes the state the others were written against. Five of the ten rows (INC-704 to INC-708) trace to tickets of one plan, cut from one locked spec within a minute on the same day, in two repositories, as each ticket's body records; three of those rows (INC-705, INC-707, INC-708) are follow-up issues filed against such a ticket:

- INC-706: the ticket that deleted fourteen workflows took with it the one that enabled auto-merge, which a later ticket's criterion relied on.
- INC-704: the deletion ticket, as first ordered, would have left the repository with no gate on code between its merge and the arrival of the replacement for the checks on code it deleted. The agent that claimed it retracted the claim eleven minutes later, with no code changed, and named a blocker; a design-review session corrected that blocker the next day to the ticket that built the replacement gate, and kept the code-review workflow out of the deletion.
- INC-705: a rewrite of the agent's instruction files pointed the step that hands an issue to the unattended worker at a label that no workflow listened for; the rewrite listed this as its first follow-up, and a later pull request built the workflow.

**A criterion needs an observation the pull request cannot make.** A pull request ends at merge. A criterion about what a deployed system does, a scheduled run, a runner that has to re-register, traffic, sits after that point. In INC-709 a review bot flagged as major that a live verification criterion was not addressed; the author answered that it could not be satisfied inside the pull request, filed a follow-up and merged (private, not verifiable). INC-706's pull request states the same limit about itself: its step runs only on an issue event, so its behaviour is confirmed by the trial run, not by the pull request's CI. A criterion of this kind is either moved to a follow-up or closed unobserved, which is where [done is not the effect](done-is-not-the-effect.md) begins.

**The task's routing and its content disagree.** INC-702 and INC-703 were labelled for the unattended worker when they were filed, and each body named a file on that worker's list of paths it may not edit. The worker refused each, and the label was swapped for one that keeps unattended agents off. INC-703's body had called the work safe for unattended authoring and left the edits to the two protected files it named to be flagged in the pull request; the worker's rules refuse an issue that needs a file on that list at all.

**The tickets cover less than the change.** One row, INC-708: the plan retired a set of memory tools, and the ticket for one repository's instructions was scoped, deliberately, to one file, with a `grep` over that file as its criterion. The check passed, and two other files in the same repository went on telling agents to call one of the tools; the pull request had scoped them out, and they got their own issue later.

**What follows depends on who reads the task.** An agent that can stop, stops and asks: a round trip, or more, as in INC-700, where the answer was posted as a comment, the second run stopped on the same reading, and the issue was moved to a local agent. An agent that goes on builds the missing piece, as INC-706's pull request did, or moves the criterion out, as INC-709's author did. No row here shows an agent bending the criterion until it passes; when one does, that is [proxy optimisation](proxy-optimisation.md).

## Where it surfaces

At the task, before any code: 7 of the 10 rows were caught there. The executor or worker that picked the task up stopped on it (INC-700, INC-702, INC-703); the agent that claimed it found the premise false (INC-701, INC-704, INC-706); agents reviewing the implementation plan found it (INC-707). In code, 1 row: the agent doing the rewrite found the missing interface and listed it as a follow-up (INC-705). In review, 2 rows: a review bot flagged the unmet criterion (INC-709, private) or mentioned the uncovered files without reporting them as a finding (INC-708). No row was caught by CI, which checks the code against itself and not the task against the repository. A row caught in review has already cost the implementation; a row caught at the task cost a stop and a question.

## Protections

Ordered by cost, from the cheapest rung to the strongest. The cheap rungs let the agent stop, or check the task's form before anyone works on it; the middle ones read the task against the plan and the repository before code; the costly ones move a criterion the pull request cannot meet out of it, or make the criterion something a machine runs. No rung replaces another, and only the ones that read the repository see a premise that is false but clearly written. A Source line that reads "one operator's practice" means the maintainer's practice, with no outside source behind it.

### Prompt: make the stop part of the task

- **Source:** on underspecified tasks, "LLMs default to non-interactive behavior without explicit encouragement." ([Vijayvargiya et al.](https://arxiv.org/html/2502.13069v3)). Telling the executor that a criterion it cannot meet as written is a stop and a comment on the issue, not a criterion to reword, is one operator's practice.
- **Cost:** tokens.
- **Breaks when:** the premise is false only against the repository and the agent does not read that far; or the run is headless and the stop waits for a person: INC-700's answer came as a comment, the next run stopped on the same reading, and the issue went to a local agent.

### Readiness: check the ticket's form before work starts

- **Source:** a Definition of Ready "avoids beginning work on features that do not have clearly defined completion criteria." ([Agile Alliance](https://agilealliance.org/glossary/definition-of-ready)); INVEST's Testable: "I understand what I want well enough that I could write a test for it." ([Wake](https://xp123.com/articles/invest-in-good-stories-and-smart-tasks/)); for a coding agent, an issue should carry "Complete acceptance criteria on what a good solution looks like (for example, should there be unit tests?)." ([GitHub Docs](https://docs.github.com/en/copilot/tutorials/coding-agent/get-the-best-results)).
- **Cost:** minutes per ticket.
- **Breaks when:** the criterion is clear and false: INC-700's precedent and INC-701's record number were stated plainly; or it is testable in principle and not inside the pull request, as INC-709's live verification was (private, not verifiable).

### Routing: compare the task with what its executor may change

- **Source:** one operator's practice: before an issue goes to the unattended worker, compare the files its criteria need with the paths the worker may not edit, and keep it off the worker if any of them is among them.
- **Cost:** one comparison per routing decision.
- **Breaks when:** the routing is set from the body's own claim instead of the comparison: INC-703's body called the work safe for unattended authoring while naming a file that worker may not edit, and the worker found it.

### Dependencies: record which ticket blocks which as data

- **Source:** "Issue dependencies let you define issues that are blocked by, or blocking, other work." ([GitHub Docs](https://docs.github.com/en/issues/tracking-your-work-with-issues/using-issues/creating-issue-dependencies)).
- **Cost:** a field per ticket, set when the plan is cut.
- **Breaks when:** the edges come from what each ticket builds and not from what it removes: INC-706's criterion relied on a workflow another ticket deleted, and INC-704's blocker was named wrong and corrected the next day; or a model writes them, since "LLMs still struggle to predict pairwise temporal dependencies" ([Yuan et al.](https://arxiv.org/abs/2308.15299)).

### Spec pass: check the spec's parts against each other

- **Source:** Spec Kit's analyze step looks for "inconsistencies, duplications, ambiguities, and underspecified items across the three core artifacts" ([Spec Kit](https://github.com/github/spec-kit/blob/main/templates/commands/analyze.md)), and its clarify step asks the author about gaps before planning, with a "Maximum of 5 total questions across the whole session" ([Spec Kit](https://github.com/github/spec-kit/blob/main/templates/commands/clarify.md)); Kiro analyses requirements for "logical inconsistencies, ambiguities, conflicting constraints, and gaps" ([Kiro](https://kiro.dev/docs/specs/feature-specs/)).
- **Cost:** one agent pass per spec, and the author's answers.
- **Breaks when:** the spec agrees with itself and not with the code, since the pass reads the spec and not the tree: nothing inside INC-700's or INC-701's issue contradicted the rest of it.

### Plan review: a critic reads the plan against the current repository

- **Source:** one operator's practice: before an issue's implementation plan is locked, critic subagents review it, one of them against the current state of the repository, and a read-only pass classifies each prerequisite the spec names as present, drifted, missing or not verifiable. Critics found INC-707's unsatisfiable check before any edit was made.
- **Cost:** a planner and several critic subagents for each ticket's plan.
- **Breaks when:** it does not run: the maintainer's workflow asks for a reviewed plan only when a change matches its trigger, a list of shared paths in INC-707's case, and of this class's 10 rows only INC-707 was caught this way; or the critic's judgement is taken as a measurement, the limit described in [LLM as measuring instrument](llm-as-measuring-instrument.md).

### Plan mode: research the repository before editing

- **Source:** "Plan mode tells Claude to research and propose changes without making them." ([Claude Code](https://code.claude.com/docs/en/permission-modes.md)); Codex's plan mode lets it "gather context, ask clarifying questions, and build a stronger plan before implementation" ([Codex](https://learn.chatgpt.com/codex/learn/best-practices)); VS Code's Plan agent "researches your project, asks clarifying questions, and creates a plan for you to review before implementation" ([VS Code](https://code.visualstudio.com/docs/agents/run/planning.md)).
- **Cost:** a planning pass and a person's review per task.
- **Breaks when:** it is not chosen, since the guidance reaches for it "If the task is complex, ambiguous, or hard to describe well" (Codex), and a task with a false premise can read as simple.

### Post-deploy check: split the criterion into its own tracked item

- **Source:** a hypothesis-driven story names its success signal: "We will know we have succeeded when", followed by a measurable signal ([Thoughtworks](https://www.thoughtworks.com/insights/articles/how-implement-hypothesis-driven-development)); canarying is "a partial and time-limited deployment of a change in a service and its evaluation." ([Google SRE workbook](https://sre.google/workbook/canarying-releases/)). Moving a criterion only a deployed system can show into a follow-up issue linked from the pull request, as INC-709's author did, is one operator's practice.
- **Cost:** one issue per such criterion, and someone who makes the observation.
- **Breaks when:** the pull request's closing keyword closes the issue that still holds the criterion: INC-706's pull request closed the issue it delivered while two of its criteria were open, and it was reopened sixteen seconds later; or the observation needs traffic the system does not get, since a canary must "receive enough traffic on the system to ensure it has handled a representative sample" (Google SRE workbook).

### Executable criteria: turn each criterion into a test

- **Source:** "Taking one example at a time, we automate it by connecting it to the system as a test." ([Cucumber](https://cucumber.io/docs/bdd/)).
- **Cost:** engineering time per criterion, and a suite to keep.
- **Breaks when:** the criterion is about the deployed system and the test cannot run before deploy (INC-706, INC-709); or the automated check covers only the ticket's scope while the change reaches further, as INC-708's one-file `grep` did.

### Interface first: agree the contract before either side is built

- **Source:** "Generating a mock API server from an OpenAPI design document allows you to prototype the API before writing any code." ([Stoplight](https://stoplight.io/openapi/design)); consumer-driven contracts, where "provider contracts emerge to meet consumer expectations and demands." ([Robinson](https://www.martinfowler.com/articles/consumerDrivenContracts.html)); [Pact](https://docs.pact.io/) checks each side against the shared contract.
- **Cost:** a contract to write and keep, and a CI job per side.
- **Breaks when:** the dependency is not an API: a label a workflow listens for (INC-705), a workflow another ticket deletes (INC-706); or the part that matters is not exercised, since by design "only parts of the communication that are actually used by the consumer(s) get tested" (Pact).

## Evidence

- Of the 1,699 SWE-bench tickets annotated by OpenAI, taking the highest severity across three annotators "leaves us with 650 tickets (38.26%) that should be ideally bounced" as underspecified (as of 2025-06, [Mathews et al.](https://arxiv.org/html/2506.17812v1)). Underspecified is missing information, not false information; the count does not separate the two.
- On SWE-bench Verified tasks with information removed, agents that could ask did better "by up to 74% over the non-interactive settings" (as of 2026-02, [Vijayvargiya et al.](https://arxiv.org/html/2502.13069v3)). The tasks were made underspecified on purpose; none rests on a false premise.
- When function-level problem descriptions were made inconsistent, ambiguous or incomplete, "more than 60% of responses from Code LLMs still generate code rather than ask questions" (as of 2024-12, [Wu and Fard](https://arxiv.org/html/2406.00215v2)). Not repository work and not agents.
- On SWE-bench Lite, issues every method resolved scored higher on all five issue-quality indicators than issues none resolved: "This suggests that issue quality significantly influences the effectiveness of resolution methods" (as of 2024-11, [Meng et al.](https://arxiv.org/html/2411.10213v1)). A comparison of two sets, not a measured effect.
- A requirements error that costs 1 unit to fix in the requirements phase cost "from 29 units to more than 1500 units" once the system was in operation, in the large aerospace projects the paper modelled (as of 2004-06, [Stecklein et al.](https://ntrs.nasa.gov/citations/20100036670)). Not agent-specific.
- The whole record is the maintainer's own: the class's 9 rows of strength `primary` link the maintainer's own repositories, and its 1 row of strength `private` says so in the link column of [`incidents/incidents.csv`](../../incidents/incidents.csv). The search for this doc found no source that measures how often an agent's task rests on a premise that is false in the current repository; the studies above measure tasks with information missing.
