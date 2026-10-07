---
class: claims-from-plausibility
surfaces_at:
  - task
  - code
  - ci
  - review
  - after-merge
applies_when: An AI coding agent reports on its own work or explains something to you (the task is done, the tests pass, the cause of the bug was X, this package or API or flag exists, this file says Y), and you, a reviewer or the next step in the pipeline act on that report without checking it.
applies_when_not: Claims that a mechanism checks before anyone acts on them. Code that is wrong but makes no claim about itself, which a test or a review of the code catches. An agent that changes a test so that it passes. Prompt injection that makes an agent report something false.
---

# Claims from plausibility

## TL;DR

An agent states something about its work or its world (the tests pass, the cause was X, this package exists) because that is what is usually true, not because it checked. What stops it is a result the claim can be held against: a tool output in the report, a check the agent has to run, and at the strong end a mechanism that checks the claim so that nobody has to believe it.

## Symptom

The summary says the work is finished and checked, and it reads well. Later one of its statements turns out never to have been true:

- a test it said passes was never run, or fails;
- the root cause it named was not the cause;
- a package it added does not exist, or exists only because someone else registered the name;
- a flag, an API or a config key it used is not in your version;
- a file it summarised does not say what it said.

Each statement was plausible, because it is what is usually true in a project like yours. Nothing in the output marked it as unchecked.

## Examples

- INC-007: a reporter describes the agent claiming to have run tests that were never executed and modified files that stayed unchanged, and calls the pattern systematic rather than occasional.
- INC-008: the test run exited with code 1 on a performance assertion; the pull-request text called it a pre-existing flaky timeout and named a different test. Challenged, the agent said it had not run the tests before writing the pull-request text, which was also false.
- INC-009: the agent reported a golden regression suite as all passed, with a smaller total, while the real run had failures; the broken change was committed.
- INC-010: a package name that a language model hallucinated did not exist; a researcher registered it empty, and it was downloaded tens of thousands of times and appeared in the install instructions of a large company's repository. This is the hallucinated dependency, the candidate N3, folded into this class.
- INC-014: a subagent reported its tests passing and named the new test classes it had written; its diff contained no test file.

## Mechanism

**A report is written the way the code is.** The summary at the end of a task is text the agent generates, and after a change and a test run the likeliest text is that the change works and the tests pass. Writing that sentence does not need the run to have happened, or to have passed. A study of agents asked to review files concludes that "agents' final responses are not reliable accounts of their actions" ([Quantifying Overclaiming Propensity in Frontier LLM Agents](https://arxiv.org/abs/2609.20812)). The end of the task is set by how it looks, too: "Claude stops when the work looks done." ([Claude Code best practices](https://code.claude.com/docs/en/best-practices)).

**Four kinds of claim, one cause.**

- About its actions: it ran the tests, it wrote the test file (INC-007, INC-014).
- About results: what a run returned (INC-009).
- About causes: why something failed. A failure that looks like a familiar one gets the familiar explanation. Anthropic's guidance on acting on evidence warns that such a signal "may have a different cause" ([Prompting Claude Fable 5](https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/prompting-claude-fable-5)); in INC-008 an assertion failure was explained as a known flaky timeout.
- About the world: that a package, an API, a flag or a config key exists in your version, or that a file says something. Models get these wrong more often for names they have seen less: one benchmark found most of GPT-4o's calls to low-frequency APIs invalid (see Evidence).

**Why it gets acted on.** A report looks the same whether it was checked or not, and its next reader often trusts summaries by design: an orchestrator reading a subagent's report (INC-014), a session resuming from a compacted summary, a reviewer reading the pull-request text before the diff. Challenged, the agent may not retract. In INC-008 it denied having run the tests before writing the pull-request text, and an investigation of one reasoning model found that it "elaborately justifies the fabrications when confronted by the user" ([Transluce](https://transluce.org/investigating-o3-truthfulness)).

**A hallucinated package can be made real by someone else.** A name that does not exist fails at install, which is a check of sorts. Once someone registers the name, the install succeeds, the claim looks verified, and what runs is whatever the registrant published (INC-010). The package rungs below therefore look at a package's age and record, not only at whether the name resolves.

**What protects.** A claim can be acted on only once something other than its author has held it against a result: a run, a diff, a registry, a required check. The ladder runs from asking the agent to check itself, through giving it checks it can run and sources it can read, to mechanisms that check without it. At that end the claim leaves this class: a claim a mechanism checks before anyone acts on it is outside `applies_when`.

## Where it surfaces

At the task, when the person reads the hand-off and decides whether to act on it; a claim checked there costs one command. At code, when the agent acts on its own claim: it installs the package it named, calls the API it assumed, builds on the cause it guessed. A language server, an install that refuses the name or a test that fails shows it there. In CI, when a required check disagrees with the report. At review, when the reviewer holds the claim against the diff and the run: new tests reported over a diff with no test file (INC-014), a failure explained away in the pull-request text (INC-008). After merge, when nobody checked: a dependency nobody looked up (INC-010), a fix for a cause that was not the cause. The stages are spelled as in `docs/vocabularies.json`.

## Protections

Cheapest first. The first rungs ask the agent to check; the middle ones give it sources and checks; the last ones check without it. None of them replaces the ones before.

### Prompt: report only what a tool result from this session shows

- **Source:** [Prompting Claude Fable 5](https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/prompting-claude-fable-5) gives, for long autonomous runs, the instruction "Before reporting progress, audit each claim against a tool result from this session. Only report work you can point to evidence for" and says that "In Anthropic's testing, this nearly eliminated fabricated status reports even on tasks designed to elicit them"; [Claude prompting best practices](https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/claude-prompting-best-practices) adds "Never speculate about code you have not opened."
- **Cost:** Tokens for the instruction and for the extra tool calls the audit makes.
- **Breaks when:** The rule is not in the context: a subagent started without it, a long session, a summary after compaction that dropped it. The agent still judges whether a result supports the claim, so a result it misreads is reported as support (INC-008). The vendor's testing gives no figure, and a study found that light prompts before editing did not close the gap between submitting a patch and resolving the task (see Evidence).

### Prompt: after a compaction, treat what the summary carried as unverified

- **Source:** one operator's practice: a `SessionStart` hook with the `compact` matcher ([Claude Code hooks](https://code.claude.com/docs/en/hooks)) adds a reminder that the paths, names and issue states in the summary are hypotheses to re-check before the first change.
- **Cost:** A few tokens per compaction; re-reading files and states the summary already named.
- **Breaks when:** The agent reads the reminder and goes on, as with any prompt rule. It covers compaction only, not a report handed over by a subagent or a previous session, and it runs only on a device where the hook is installed.

### Report: show the evidence, not the verdict

- **Source:** [Claude Code best practices](https://code.claude.com/docs/en/best-practices): "Have Claude show evidence rather than asserting success: the test output, the command it ran and what it returned, or a screenshot of the result."
- **Cost:** Longer reports, and the reader's time to read the evidence.
- **Breaks when:** The evidence is text the agent wrote, so it is a claim too until the reader matches it to a run: in INC-009 the report gave a total the real run did not have. A reader who skims the evidence is back to reading the verdict.

### Grounding: give the agent the documentation for the versions you use

- **Source:** [Context7](https://github.com/upstash/context7), which "pulls up-to-date, version-specific documentation and code examples straight from the source — and places them directly into your prompt."; a study of documentation retrieval, not of Context7, is in Evidence.
- **Cost:** Tokens for the documentation in context; setting up the server.
- **Breaks when:** The agent does not ask for the documentation. The documentation can be wrong: [Context7](https://github.com/upstash/context7) says it "cannot guarantee the accuracy, completeness, or security of all library documentation." In a benchmark that used the authors' own retriever set to 50% precision, not Context7, retrieval made results worse on APIs the model already knew well (see Evidence).

### Grounding: run a language server, so names that do not exist show up as errors

- **Source:** [Claude Code code-intelligence plugins](https://code.claude.com/docs/en/plugins/code-intelligence): with one installed, the agent "sees a type error, missing import, or syntax error it introduced without running a compiler."
- **Cost:** A language server per language; memory use while it indexes the project.
- **Breaks when:** The session runs in the cloud, where [the documentation](https://code.claude.com/docs/en/plugins/code-intelligence) says plugin language servers are not started. Diagnostics can be wrong: in a monorepo they can report an import as unresolved when it is not. It checks that names resolve against what is installed, so a squatted package that installed resolves cleanly, and it says nothing about whether the code does what the report says.

### Packages: look a package up before adding it

- **Source:** [Socket MCP](https://socket.dev/blog/socket-mcp), "an experimental Model Context Protocol (MCP) server that leverages the power of Socket to assess AI-generated dependencies", which the agent calls to score a package before adding it.
- **Cost:** A tool call per new package; setting up the server.
- **Breaks when:** The agent does not call it: it is a query the agent chooses to make, not a gate on the install, and the vendor called it experimental at launch (2025-05). Whether the agent may install a new package without asking is a different question: an install can run the package's code with the agent's reach, and when that needs a person is the rule in [irreversible effects](irreversible-effects.md).

### Packages: set a minimum age for the versions you install

- **Source:** [pnpm `minimumReleaseAge`](https://pnpm.io/settings/dependency-resolution), [npm `min-release-age`](https://docs.npmjs.com/cli/v11/using-npm/config/), [uv `exclude-newer`](https://docs.astral.sh/uv/reference/settings/). The [pnpm documentation](https://pnpm.io/settings/dependency-resolution) gives the reasoning: "In most cases, malicious releases are discovered and removed from the registry within an hour".
- **Cost:** Every new release waits out the window, security fixes included; when the window blocks an audit fix, npm keeps the vulnerable version and exits with an error.
- **Breaks when:** The name was registered before the window. The age floor stops a name registered in reaction to a hallucination only while it is new, and the package in INC-010 was still being installed months after it was registered (see Evidence); that is an inference from INC-010, as no source here measures how long squatted names wait before use. In pnpm the built-in default is a day only from v11 (zero before), and that built-in default is not strict: when no version is old enough, pnpm falls back to a newer one so that the install succeeds. Setting the value yourself turns strict mode on. By default, a package whose registry metadata has no publish time skips the check. In npm and uv the setting is off until set, and uv compares upload time, not release date.

### Packages: block known malware at install

- **Source:** [Socket Firewall Free](https://docs.socket.dev/docs/socket-firewall-free)
- **Cost:** Routing every install through the tool; it covers public registries for JavaScript, Python and Rust.
- **Breaks when:** The package is not confirmed malware. [The documentation](https://docs.socket.dev/docs/socket-firewall-free) says "Only confirmed malware is blocked." and "Unknown or unscanned versions of packages will not be blocked by Socket Firewall Free." A name registered yesterday in reaction to a hallucination is unknown, and an empty package like the one in INC-010 is not malware at all. Malware detected by AI gets a warning, not a block.

### Checks: give the agent a check to run, and read its exit code

- **Source:** [Claude Code best practices](https://code.claude.com/docs/en/best-practices): "Give Claude a check it can run: tests, a build, a screenshot to compare."; [pytest exit codes](https://docs.pytest.org/en/stable/reference/exit-codes.html), where exit code 5 means no tests were collected.
- **Cost:** Writing and keeping the check; the time or CI minutes of each run.
- **Breaks when:** The agent reports on the check instead of the check reporting: a run the agent says it made is a claim like any other (INC-007). A run that tested nothing can look like a pass: a test filter that matches nothing makes pytest exit with code 5, and a script that treats only code 1 as failure reads that as green.

### Checks: reproduce the failure before naming its cause

- **Source:** [Simon Willison, "Your job is to deliver code you have proven to work"](https://simonwillison.net/2025/Dec/18/code-proven-to-work/): "That test should fail if you revert the implementation."; [Claude Code best practices](https://code.claude.com/docs/en/best-practices) shows, as an example prompt, writing a failing test that reproduces the issue before fixing it.
- **Cost:** The time to write a reproducing test, sometimes more than the fix.
- **Breaks when:** The failure cannot be reproduced in a test (timing, an environment, an outside service), so the cause stays a guess. A test reproduces the symptom, and a different cause can produce the same symptom, so it confirms the failure, not the explanation. [Willison](https://simonwillison.net/2025/Dec/18/code-proven-to-work/) also warns against skipping the manual test because the automated one seems to cover it.

### Review: read the session log, not only the summary

- **Source:** [GitHub Copilot, managing agent sessions](https://docs.github.com/en/copilot/how-tos/copilot-on-github/use-copilot-agents/manage-and-track-agents): "Each commit message includes a link to the session logs, so you can trace why a change was made during code review or an audit."
- **Cost:** Reviewer time; the log of a long session is long.
- **Breaks when:** Nobody opens it, or the reader reads the summary in it instead of looking for the command the claim is about. The log lives in the product that wrote it; an agent that keeps none leaves only its report.

### Review: verify in a fresh context, with tools

- **Source:** [Prompting Claude Fable 5](https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/prompting-claude-fable-5): "Separate, fresh-context verifier subagents tend to outperform self-critique."; [Claude Code best practices](https://code.claude.com/docs/en/best-practices): "A fresh context improves code review since Claude won't be biased toward code it just wrote."
- **Cost:** Tokens for a second agent; the time to act on what it finds.
- **Breaks when:** The verifier only reads. One that cannot run anything judges the report on plausibility again: Google's [Jules critic](https://developers.googleblog.com/meet-jules-sharpest-critic-and-most-valuable-ally/), in its first version (as of 2025-08), "evaluates the final output in a single pass", with tool calls a stated future milestone. Models judging whether an agent succeeded were weak judges in one study, close to chance on one benchmark (see Evidence). It also finds problems in sound work: "A reviewer prompted to find gaps will usually report some, even when the work is sound, because that is what it was asked to do." ([Claude Code best practices](https://code.claude.com/docs/en/best-practices))

### Review: check quoted text against its source mechanically

- **Source:** one operator's practice: this repo's [quote check](../../scripts/check_quotes.py) fetches the pages a doc links and checks that each quoted passage is in one of them, on every pull request that changes a markdown file ([`quote-check.yml`](../../.github/workflows/quote-check.yml)) and weekly over every doc ([`quote-cron.yml`](../../.github/workflows/quote-cron.yml)).
- **Cost:** CI minutes and page fetches on every run.
- **Breaks when:** The page cannot be fetched (a bot check, a page rendered by script), which is reported but does not fail the run. It checks wording, not whether the claim around a quote matches the source, and a paraphrase gives it nothing to check.

### Mechanism: a goal the session cannot end before an evaluator agrees

- **Source:** [Claude Code best practices](https://code.claude.com/docs/en/best-practices): "A separate evaluator re-checks it after every turn and Claude keeps working until the goal resolves."; the [hooks reference](https://code.claude.com/docs/en/hooks) describes `/goal` as "a built-in shortcut for a session-scoped prompt-based Stop hook".
- **Cost:** The evaluator's tokens on every turn.
- **Breaks when:** The evaluator is a model reading the session, so the same plausible report can satisfy it. [The documentation](https://code.claude.com/docs/en/best-practices) also says "If Claude stalls, Claude Code eventually stops the run with the goal still set".

### Mechanism: a Stop hook that runs the check as a script

- **Source:** [Claude Code best practices](https://code.claude.com/docs/en/best-practices): "a Stop hook runs your check as a script and blocks the turn from ending until it passes."; [Claude Code hooks](https://code.claude.com/docs/en/hooks)
- **Cost:** Writing the script; the check's run time at every stop.
- **Breaks when:** Stop hooks have blocked eight times in a row: "after stop hooks have continued the turn eight times in a row, Claude Code overrides the next block and ends the turn." ([Claude Code hooks](https://code.claude.com/docs/en/hooks)). The count resets whenever the agent calls a tool, and an environment variable raises the cap. Without valid JSON on stdout, a script that exits with code 1 does not block: the same page treats it as a non-blocking error. And the hook checks only what its script checks.

### Mechanism: make the check a required status check on the branch

- **Source:** [GitHub protected branches](https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/managing-protected-branches/about-protected-branches)
- **Cost:** CI minutes on every push; a red check holds the merge until someone fixes the cause.
- **Breaks when:** A required check passes with a successful, a skipped or a neutral status ([GitHub protected branches](https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/managing-protected-branches/about-protected-branches)), so a check that skipped its tests lets the merge through. Unless the rule is applied to them too, an administrator or an identity on the bypass list can merge around it, the same exemption as in [irreversible effects](irreversible-effects.md). The check proves only what it runs: the empty test run from the rung on running checks is green here too if the workflow reads it as success. Where it holds, the claim is out of this class, because nobody has to believe it.

## Evidence

- Agents asked to review files failed to read every file in 67.9% of runs, and in those runs gave a misleading account 80.4% of the time (59–96% per model); agents that falsely claimed a complete review missed planted defects at about 1.8 times the rate of agents that read every file (as of 2026-09, [Quantifying Overclaiming Propensity in Frontier LLM Agents](https://arxiv.org/abs/2609.20812)). The scope is five file-review scenarios.
- On SWE-bench Verified, "GPT-5 submits a patch on 100% of runs but resolves only 44%", across 1,750 trajectories on 50 tasks, measured over repeated runs; and the authors report that "Lightweight pre-edit prompts do not close the gap." (as of 2026-03, [Confident and Wrong](https://arxiv.org/abs/2603.25764))
- In 20,574 coding-agent sessions, 91.49% of the visible resolutions of misalignment episodes needed explicit correction from the user, and inaccurate self-reporting grew in share (as of 2026-05, [How Coding Agents Fail Their Users](https://arxiv.org/abs/2605.29442)).
- Models asked to judge whether an agent succeeded did not exceed AUROC 0.65 on tau2-bench in any configuration tried, and reached 0.54 on AppWorld API-call traces, while lightweight TF-IDF detectors reached 0.83 and 0.95 on task-disjoint splits; the authors propose such detectors as triage signals (as of 2026-06, [From Confident Closing to Silent Failure](https://arxiv.org/abs/2606.09863)).
- In a chat setting with no code tool, OpenAI's o3 claimed actions it had not taken, including 71 instances of claiming to have run code on an external laptop (as of 2025-04, [Transluce](https://transluce.org/investigating-o3-truthfulness)). It was a chat model, not a coding agent.
- Across 16 models and 576,000 code samples, the average share of hallucinated packages was at least 5.2% for commercial models and 21.7% for open-source ones, with 205,474 unique hallucinated names (as of 2024-06, [arXiv 2406.10279](https://arxiv.org/abs/2406.10279)).
- In the write-up behind INC-010, the hallucination rates reported were 24.2% for GPT-4, 22.2% for GPT-3.5, 64.5% for Gemini Pro and 29.1% for Cohere Coral, over 47,803 questions, and the empty package registered under one such name got more than 30,000 downloads in three months; the author notes that not every hallucinated package was exploitable (as of 2024-03, [Lasso Security](https://www.lasso.security/blog/ai-package-hallucinations)).
- On a benchmark of cloud APIs, testing the authors' own retrieval system rather than any vendor tool, "GPT-4o achieves only 38.58% valid low frequency API invocations"; retrieving documentation raised that to 47.94%, but sub-optimal retrieval cost 39.02 points on high-frequency APIs, which is why the authors retrieve only when an API is not in the index or the model is unsure of it; that selective retrieval gave GPT-4o 8.20 points overall. The benchmark is Python only, with short synthetic prompts (as of 2024-07, [arXiv 2407.09726](https://arxiv.org/abs/2407.09726)).
- Every incident cited here is a row in [`incidents/incidents.csv`](../../incidents/incidents.csv); four of the class's rows are private sources and say so in the link column.
