---
class: absence-read-as-success
surfaces_at:
  - code
  - ci
  - review
  - merge
  - after-merge
applies_when: You rely on automated checks around an AI coding agent (CI jobs, required status checks, a review bot, a hook, a scanner, a test run) to tell you a change is safe to merge, and you want to know why a check can be green when it never looked at the change, and what makes a missing result count as a failure.
applies_when_not: A check that runs and reads its input but checks the wrong thing, such as a property that is easy to satisfy (that is its own class). A guard that matches the wrong spelling of a command or path (pattern-matched boundaries). Application code that swallows an error at run time. A test that runs but cannot fail. A claim of done that no check was asked to verify.
---

# Absence read as success

## TL;DR

A check has three outcomes, passed, failed and never looked, and most of the machinery around an agent folds the third into the first: a skipped job, an action that declined to run, a reviewer that posted nothing or a tool that was not installed all end green. What stops it is a gate that passes only on a positive result for this commit, tested against the ways it can be skipped, plus a heartbeat for the checks that run on a schedule. External evidence for this class is thin; most of the record is the maintainer's own.

## Symptom

A check you rely on shows green, or shows nothing at all and nobody notices, and later it turns out it never looked at the change. The CI job was skipped by a path filter or a condition, cancelled, or timed out, and the branch rule counted the skip as a pass. The review bot posted no comments because it never ran, could not fetch the diff or failed half way, and "no comments" read as "nothing found". A scanner checked an empty file list, or a list that did not name the new file. A step called a tool that was not installed and exited 0. The test runner collected no tests. Nothing failed, so nothing turned red, and the agent's report that all checks pass was true of what ran.

## Examples

- INC-044: a review gate decided from a list of file extensions whether to review at all; a pull request touching only other file types skipped the review, the verdict step found zero review comments and passed, and the pull request could merge unreviewed.
- INC-046: the same gate's verdict step read the absence of a review comment as a reviewer that was rightly skipped, so a reviewer that ran cleanly and posted nothing passed the check.
- INC-042: a reviewer in CI could not run any shell command for eight days because a sandbox dependency was missing; it rebuilt the diff from earlier comments and kept posting verdicts, and the check went green or red as if it had read the change.
- INC-049: a review action declined to run because the pull request changed its own workflow file, logged a warning and exited 0; the required verdict check found no review and passed.
- INC-060: a vendor's review command stopped early on a re-review because it had commented before, exited success and posted nothing, which looked the same as a review that found nothing.

## Mechanism

**Skipped and neutral count as passed.** GitHub treats a skipped or neutral check run as "a success for dependent checks in GitHub Actions", and "A job that is skipped will report its status as "Success". It will not prevent a pull request from merging, even if it is a required check." ([GitHub status checks](https://docs.github.com/en/pull-requests/reference/status-checks)). A required check passes with a `successful`, `skipped` or `neutral` status ([about protected branches](https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/managing-protected-branches/about-protected-branches)). A job skipped by a condition reports success, and a job that depends on a failed job "is skipped and may not block merging" ([troubleshooting required status checks](https://docs.github.com/en/pull-requests/how-tos/merge-and-close-pull-requests/troubleshooting-required-status-checks)). A whole workflow skipped by a path filter is the exception: its check stays pending and blocks. A hosted review whose check run is closed as neutral after a timeout, with no review written, is a pass by the same rule (INC-061, INC-062).

**A step that does nothing exits 0.** An action that decides not to run logs a warning and succeeds (INC-049, INC-063). A review plugin whose tool calls were denied finished "with a green check, making it impossible to tell that the review output was lost" ([anthropics/claude-code#32459](https://github.com/anthropics/claude-code/issues/32459), INC-059). Whether a tool with nothing to check fails is the tool's default: pytest exits with code 5 when "No tests were collected" ([pytest exit codes](https://docs.pytest.org/en/stable/reference/exit-codes.html)), while Jest has a flag that "Allows the test suite to pass when no files are found" ([Jest CLI](https://jestjs.io/docs/cli)). In a shell, a failure early in a pipeline is lost unless `pipefail` is set, and bash leaves that option off by default ([Bash manual](https://www.gnu.org/software/bash/manual/bash.html)).

**A verdict built from counted findings cannot tell silence from a clean result.** A gate that passes when the reviewer left no blocking comment also passes when the reviewer never ran, crashed or could not post. In INC-044 the review was skipped and "the verdict step then finds zero review comments and exits" with success ([Osasuwu/jarvis#1073](https://github.com/Osasuwu/jarvis/issues/1073)). In INC-046 the step had one branch for zero comments, and two different states reached it: a reviewer that was rightly skipped and one that ran and said nothing.

**A degraded environment is quiet.** A Claude Code hook that exits with any code other than 2 "doesn't block on its own for most hook events" ([Claude Code hooks](https://code.claude.com/docs/en/hooks.md)), so a hook whose interpreter is missing lets every call through (INC-041). A reviewer that lost its shell still wrote verdicts, and "the check still goes green or red as if it had read it" ([Osasuwu/jarvis-oss#214](https://github.com/Osasuwu/jarvis-oss/issues/214), INC-042). A guard pointed at the wrong path passes every pull request, because nothing it watches ever changes (INC-043).

**Nobody watches for what did not happen.** A slow test lane that runs only at night, is cancelled by the next merge, is killed by a timeout or never runs on pull requests produces no red, and the regressions it would catch sit unseen (four of the private rows). On GitHub, "In a public repository, scheduled workflows are automatically disabled when no repository activity has occurred in 60 days" ([GitHub events that trigger workflows](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows)).

**The agent repeats the rollup.** An agent asked whether the change is ready reads the checks list and reports that all checks pass. That is true of what ran. Nothing in the rollup says what was skipped for this change, and the agent has no reason to ask.

**Fixes swing between this class and false blocks.** A gate made to fail on absence also fails on the legitimate cases that produce nothing: in the maintainer's record, a hook launcher made to fail closed (the fix for INC-041) then blocked every tool call on machines where `python3` was a store stub ([Osasuwu/jarvis-oss#181](https://github.com/Osasuwu/jarvis-oss/issues/181)). The relief for a false block, a carve-out or a merge around the gate, is a new path where nothing ran and the merge went through.

## Where it surfaces

Mostly after merge. In 12 of this class's 23 dataset rows the gap was found after the change had merged, by someone reading the reviewer's reports, by a later failure, or by an audit of the gate itself; the gate had been green throughout. In review, it shows as a pull request with a green check and no review on it, noticed by a person who expected comments (INC-059 to INC-063). In CI, it is caught only where a step already fails on a missing result, as in INC-047, where twelve reviewers ran and posted nothing and the verdict step failed closed. At merge, the sign is a check list that says skipped or neutral where a pass was expected; the merge button does not distinguish them.

## Protections

### Prompt: report each check as ran, skipped or missing, not as passed

- **Source:** one operator's practice: the agent is told that a gate that cannot run has not passed, and its report names, for each required check, whether it ran on the head commit and what it read.
- **Cost:** tokens, and the agent's time to read each check's run rather than the rollup.
- **Breaks when:** the agent reads only the rollup, or the run's own log does not say it skipped. In the maintainer's record, four rules written as prose or template checkboxes for this class were followed by three recurrences; the fourth was too new to tell.

### Platform: know which results your platform counts as passed

- **Source:** [GitHub status checks](https://docs.github.com/en/pull-requests/reference/status-checks) and [troubleshooting required status checks](https://docs.github.com/en/pull-requests/how-tos/merge-and-close-pull-requests/troubleshooting-required-status-checks) list skipped and neutral as successful. On GitLab, "Pipelines must succeed" "requires the existence of a successful pipeline, not the absence of failed pipelines", unless "Skipped pipelines are considered successful" is turned on ([GitLab auto-merge](https://docs.gitlab.com/user/project/merge_requests/auto_merge/)); a job with `allow_failure` fails while "the pipeline is successful" ([GitLab CI/CD YAML](https://docs.gitlab.com/ci/yaml/)).
- **Cost:** reading time.
- **Breaks when:** knowing changes no setting, or a hosted bot's outcome (neutral after a timeout) is one the docs do not mention.

### Branch rule: make the check required, and read the required list back

- **Source:** with branch protection, "all required status checks must pass before collaborators can merge" ([about protected branches](https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/managing-protected-branches/about-protected-branches)), so a required check that is cancelled, times out or never reports blocks. Comparing the live required list against a checked-in copy on a schedule is one operator's practice.
- **Cost:** minutes per repository; a short scheduled script for the read-back.
- **Breaks when:** the job is skipped by a condition or its dependency failed (both pass), the check was never added to the list, or a renamed job silently drops out of it. Several of the private rows are repositories with no branch protection at all.

### Workflow: do not require a skippable job; aggregate with a job that always runs

- **Source:** GitHub: "Avoid requiring workflows that can be skipped." and "Use always() with needs for required checks that depend on other jobs." ([troubleshooting required status checks](https://docs.github.com/en/pull-requests/how-tos/merge-and-close-pull-requests/troubleshooting-required-status-checks)). [re-actors/alls-green](https://github.com/re-actors/alls-green) is one required job that fails when any job it needs did not succeed, with the skips allowed listed by name; it is "a must to have the job always run, otherwise GitHub will make it skipped when any of the dependencies fail". A vendor notes that GitHub's workaround for path filters, a second workflow of the same name that only reports success, is itself a pass with nothing checked, and argues "A merge gate has to be a positive signal" ([Mergify](https://mergify.com/blog/path-filters-are-not-a-ci-gate)); Mergify sells a merge product.
- **Cost:** one job, seconds of CI per run.
- **Breaks when:** a job is left out of the aggregator's `needs` list, or a job succeeds while doing nothing inside (the next rungs).

### Tools: make each tool fail when it has nothing to check

- **Source:** pytest exits non-zero when no tests are collected ([pytest exit codes](https://docs.pytest.org/en/stable/reference/exit-codes.html)); Jest passes on no tests only with `--passWithNoTests` ([Jest CLI](https://jestjs.io/docs/cli)); Mocha fails on zero tests only with `--fail-zero` ([Mocha CLI](https://mochajs.org/running/cli/)); ESLint treats an unmatched file pattern as an error unless `--no-error-on-unmatched-pattern` is set ([ESLint CLI](https://eslint.org/docs/latest/use/command-line-interface)); a pre-commit hook with `always_run` "will run even if there are no matching files" ([pre-commit](https://pre-commit.com/)); Codecov's action fails the job on an upload error only with `fail_ci_if_error` ([codecov-action](https://github.com/codecov/codecov-action)).
- **Cost:** a flag per tool; occasional red on a change that really has nothing to check.
- **Breaks when:** someone silences that red by mapping the exit code back to 0, or the tool has no such option and reports success on an empty input.

### Shell: run steps fail-fast

- **Source:** in GitHub Actions a step with `shell: bash` runs as `bash --noprofile --norc -eo pipefail {0}`, while an unspecified shell on Linux runs `bash -e {0}`, without `pipefail` ([GitHub workflow syntax](https://docs.github.com/en/actions/reference/workflows-and-actions/workflow-syntax)). `pipefail` makes a pipeline return the status of "the last (rightmost) command to exit with a non-zero status" ([Bash manual](https://www.gnu.org/software/bash/manual/bash.html)).
- **Cost:** a line per workflow.
- **Breaks when:** the step runs under `cmd` or PowerShell, whose fail-fast differs; a failure is inside a condition or a command substitution that `set -e` ignores ([BashFAQ/105](https://mywiki.wooledge.org/BashFAQ/105)); or a command fails quietly with exit 0.

### Preflight: fail the job when a tool the check needs is missing

- **Source:** POSIX `command -v` reports the path a command name resolves to, and fails when there is none ([POSIX command](https://pubs.opengroup.org/onlinepubs/9799919799/utilities/command.html)). For a Claude Code hook, the only exit code that blocks is 2 ([Claude Code hooks](https://code.claude.com/docs/en/hooks.md)), so a launcher that turns its own failure into exit 2, and a CI step that fails when the reviewer's sandbox cannot start, are one operator's practice.
- **Cost:** a check per dependency; a red job when the runner image changes.
- **Breaks when:** the tool is present but degraded (a reviewer that loses one capability can still write a confident verdict), or the fail-closed launcher blocks legitimate work, as the maintainer's did on machines with a stub interpreter.

### Verdict: pass only on a positive result from the expected source, for this commit

- **Source:** a required check can be pinned to "a status check from a specific GitHub App" ([troubleshooting required status checks](https://docs.github.com/en/pull-requests/how-tos/merge-and-close-pull-requests/troubleshooting-required-status-checks)); OWASP's rule is to design a mechanism "so that a failure will follow the same execution path as disallowing the operation" ([OWASP, Fail securely](https://owasp.org/www-community/Fail_securely)). Requiring every legitimate no-op path (a draft, a docs-only change) to write its own explicit nothing-to-review result is one operator's practice.
- **Cost:** hours to a day per gate; false blocks until every no-op path writes its result.
- **Breaks when:** the reviewer is degraded but still writes a result (INC-042), unless the result records that its tools worked; a no-op path writes nothing and the gate deadlocks, and the way around the deadlock becomes a new unreviewed merge.

### Canary: run the gate against the ways it can be skipped

- **Source:** one operator's practice: fixtures feed each gate a skipped job, an empty review, a cancelled run and a changed path it should watch, and assert red; they run on every change to the gate and on a schedule.
- **Cost:** hours per gate, CI minutes on a schedule, and fixtures that change with the gate.
- **Breaks when:** a fixture asserts something that cannot fail (the guard of INC-043 was followed by fixtures that compared a value with itself), or a skip path nobody wrote a fixture for (INC-048 found four).

### One copy: run the same gate code in every repository

- **Source:** one operator's practice, using GitHub's [reusable workflows](https://docs.github.com/en/actions/how-tos/reuse-automations/reuse-workflows) as the mechanism. INC-049 is a fix made in one of the maintainer's repositories and never carried to another.
- **Cost:** a shared workflow repository and its versioning.
- **Breaks when:** repositories pin different versions, or one keeps a local copy.

### Heartbeat: alert when a check has not reported a result in time

- **Source:** a dead man's switch: "When Healthchecks.io does not receive the HTTP request at the expected time, it notifies you" ([Healthchecks.io](https://healthchecks.io/docs/monitoring_cron_jobs/)). Watching the review volume the same way (no positive review verdict in N days while pull requests merged) is one operator's practice.
- **Cost:** free for 20 monitored jobs (as of 2026-10, [Healthchecks.io pricing](https://healthchecks.io/pricing/)); an hour of setup; tuning the window.
- **Breaks when:** one pull request's missing check falls inside the window; it finds the gap after the merge, not before.

### Merge queue: run the slow checks on the exact merge before it lands

- **Source:** a merge queue runs the required checks on the merged result before it lands; a workflow must also trigger on `merge_group`, or "the required status check will not be reported" and the merge fails ([GitHub merge queue](https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/configuring-pull-request-merges/managing-a-merge-queue)). Uber reports its mainline kept green this way ([Ananthanarayanan et al., EuroSys 2019](https://dl.acm.org/doi/pdf/10.1145/3302424.3303970)).
- **Cost:** every merge waits for the queue's run, so slow-lane minutes multiply.
- **Breaks when:** flaky tests block the queue and get retried until green, or the slow tests are skipped inside the queue too.

## Evidence

- External evidence for this class is thin. A blind search found no public record of an agent reporting that all checks pass where the checks provably never ran. The five external rows are all hosted or vendor review bots that ended green, neutral or silent with no review, from the vendors' own trackers (2026-03 to 2026-09). Gate failures are seldom filed as public incidents, so this is a reporting gap, not evidence that the class is rare. 18 of the class's 23 rows come from the maintainer's repositories, 9 of them private.
- A study of 142,387 CI jobs across 81 industrial projects describes "silent failures, where build jobs are marked as successful but fail to complete all or part of their tasks", and finds that "11% of successful jobs are rerun, with 35% of these reruns occurring after more than 24 hours" (as of 2025-09, [Aïdasso et al.](https://arxiv.org/abs/2509.14347v1)). The jobs are not agent-written.
- The same failure without an agent: a large open-source project found that "we haven't been running the tests for most of CI in CI for like three months now" (as of 2024-09, [Mesa merge request 30978](https://gitlab.freedesktop.org/mesa/mesa/-/merge_requests/30978)).
- Adjacent: of 3,111 test-disabling changes across 15 Java systems, "41% of disabled tests are never brought back" (as of 2021-08, [ESEC/FSE 2021](https://2021.esec-fse.org/details/fse-2021-papers/78/How-Disabled-Tests-Manifest-in-Test-Maintainability-Challenges-)).
- In the maintainer's record, the gap went unseen for between three days and six weeks per incident. Of the fixes there, every rule written as prose was followed by a recurrence or is too new to judge, while all seven mechanical gate changes held on their own incident; four left a sibling path open and one blocked legitimate work (as of 2026-10, one operator's audit, private, not verifiable).
- Every incident cited here is a row in [`incidents/incidents.csv`](../../incidents/incidents.csv); nine of the class's rows are private sources and say so in the link column.
