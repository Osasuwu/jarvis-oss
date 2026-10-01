---
applies_when: you are setting up an agent to work in a git repository on GitHub and a practice doc here asks for a branch, a pull request, a CI check, a rules file, a skill or a hook that you have not used before
applies_when_not: you already work in pull requests with CI and have written a rules file, a skill and a hook; which file names and directories a given harness uses is docs/harnesses.md; whether a given practice is worth adopting is the practice doc that requires this one; what can stop an agent when your plan and harness enforce nothing is docs/agent-safety-hooks.md
kind: basics
---

# Basics

The five things practice docs here assume you already know. Each section says what the thing is,
why it matters once an agent writes the code, and how to tell you have it. It links the official
page instead of repeating it: the official page is the one that stays current. Anything past
that, such as which harness reads which file, is one hop away in [the harness table](harnesses.md).

## Branches and pull requests

In Git, a branch is a movable pointer to a commit, so a separate line of changes costs almost
nothing to create, and `git switch -c <name>` makes one and switches to it; see
[Git - Branches in a Nutshell](https://git-scm.com/book/en/v2/Git-Branching-Branches-in-a-Nutshell) (checked 2026-09-30).
`git push -u origin <name>` publishes it to GitHub; see [Git - git-push Documentation](https://git-scm.com/docs/git-push) (checked 2026-10-01).
For how GitHub treats branches, see [Branches](https://docs.github.com/en/pull-requests/reference/branches) (checked 2026-09-30).

A pull request proposes merging the changes on one branch into another and gives a place to
review them first. See
[About pull requests](https://docs.github.com/en/pull-requests/get-started/about-pull-requests) (checked 2026-09-30).
From a terminal, `gh pr create` opens one; see
[gh pr create](https://cli.github.com/manual/gh_pr_create) (checked 2026-09-30). It asks for a title
and body unless you pass `--title` and `--body`, or `--fill`. It needs `gh` to be
signed in, with `gh auth login` or, in an unattended run, a `GH_TOKEN` environment variable; see
[gh auth login](https://cli.github.com/manual/gh_auth_login) (checked 2026-09-30).

For agent work the pull request is the review point: the agent changes a branch and you review the
diff before it is merged. Whether anything stops the agent from pushing straight to your main
branch depends on branch protection, in the next section. Most practice docs here hang a check or
a hold on the merge step.

You have it when you can open a pull request from a branch, in the browser or with `gh pr create`,
and see the diff.

## CI

CI runs your checks automatically when something happens in the repository, such as a pull
request opening. On GitHub the tool is Actions, and its workflows start on events in the
repository. See
[Understanding GitHub Actions](https://docs.github.com/en/actions/get-started/understand-github-actions) (checked 2026-09-30).
A workflow is a file in the `.github/workflows` directory; see
[Workflow syntax for GitHub Actions](https://docs.github.com/en/actions/reference/workflows-and-actions/workflow-syntax) (checked 2026-09-30)
for its events and steps.

A check only stops a merge if the branch is protected and lists it as required. Even then, by
default the rule does not apply to repository admins, which on your own repository means you and
an agent working with your token, until you turn on **Do not allow bypassing the above settings**. See
[About protected branches](https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/managing-protected-branches/about-protected-branches) (checked 2026-10-01).
Protection on a private repository needs a paid plan. The same page's **Who can use this
feature?** box says protected branches are available "in public repositories with GitHub Free and
GitHub Free for organizations", and "in public and private repositories with GitHub Pro, GitHub
Team, GitHub Enterprise Cloud, and GitHub Enterprise Server".
Without protection a failing check still shows in the pull request's **Checks** tab (see
[Status checks](https://docs.github.com/en/pull-requests/collaborating-with-pull-requests/collaborating-on-repositories-with-code-quality-features/about-status-checks) (checked 2026-10-01)),
but nothing blocks the merge. The check then binds only someone who looks before merging; in an
unattended run nobody does, and nothing stops the agent from merging on red. What can stop it
instead, such as a hook that blocks the merge command, is in
[Stopping an agent's writes before they land](agent-safety-hooks.md).

You have it when a failing check shows on a pull request. If the branch is protected, you also
have it when the merge stays blocked for you, an admin, until the check passes. From a terminal,
`gh pr checks` lists a pull request's checks; see
[gh pr checks](https://cli.github.com/manual/gh_pr_checks) (checked 2026-09-30).

## Rules file

A rules file is a plain markdown file of standing instructions that your agent's harness loads at
the start of each session: build commands, conventions, what to avoid. The name depends on the
harness. Claude Code reads `CLAUDE.md`. By default it reads `AGENTS.md` only when there is no
`CLAUDE.md`, `.claude/CLAUDE.md` or `CLAUDE.local.md` in the working directory or above, and a
setting on the memory page below changes that default; a personal
`~/.claude/CLAUDE.md` does not count, and reading `AGENTS.md` directly needs Claude Code v2.1.277
or later. `AGENTS.md` is a shared name, described as a README for agents, that several other
harnesses read; some harnesses use their own file name. See
[How Claude remembers your project](https://code.claude.com/docs/en/memory) (checked 2026-09-30)
and [AGENTS.md](https://agents.md) (checked 2026-09-30). The file name for your harness is in
[the harness table](harnesses.md).

The agent treats a rules file as context, not as enforcement. It can still ignore a line, which
is why a rule that must hold is enforced by something outside the model: a hook or a required CI
check.

You have it when you ask the agent what its project instructions say and it answers from the file
(in Claude Code, `/context` also lists a loaded `CLAUDE.md` under **Memory files**). In an unattended `claude -p` run the file loads as in a session, unless
`--bare` is set: that flag skips `CLAUDE.md`, hooks and skills, and the docs recommend it for
scripted calls. To keep `--bare`, pass the content in with `--append-system-prompt-file`; bare mode never reads a
subscription login, so it needs `ANTHROPIC_API_KEY`, an `apiKeyHelper`, or a cloud provider's own credentials. To load the file as in a session, leave
`--bare` off; see
[Run Claude Code programmatically](https://code.claude.com/docs/en/headless) (checked 2026-09-30).
Either way that shows the file is loaded, not that the agent follows every line.

## Skills

A skill is a directory with a `SKILL.md` file: instructions for one repeatable job, which the
agent loads when the job comes up instead of on every session. Claude Code lets the agent pick a
skill when it looks relevant, or you invoke one by name. See
[Extend Claude with skills](https://code.claude.com/docs/en/skills) (checked 2026-09-30).
The format is an open standard that a growing number of agent products have adopted; see the
[Agent Skills Overview](https://agentskills.io/home) (checked 2026-10-01). Some optional fields
vary between tools; see the
[Agent Skills specification](https://agentskills.io/specification) (checked 2026-09-30).
The specification does not say which directory a tool reads skills from; for that, the harness
table has a skills directory column. Where it says unverified, look in your harness's own docs;
if they name no skills directory, or your harness has no skills, a short procedure can go in the
rules file, at the cost described next.

Use a skill for a procedure and a rules file for a standing fact. A long procedure in the rules
file is loaded every session whether or not it is needed.

You have it when you can invoke a skill by name and the agent follows its steps. Where your harness
has no skills, you have the substitute when the agent follows the procedure in the rules file.

## Hooks

A hook is a shell command your harness runs at a fixed point in the agent's work, for example
before a tool call or after a file edit. It runs whether or not the agent decides to follow an
instruction. See
[Automate actions with hooks](https://code.claude.com/docs/en/hooks-guide) (checked 2026-09-30).
In an unattended `claude -p` run, `--bare` skips hooks and never reads a subscription login (see
Rules file), so leave it off if you rely on them.

That is the difference from a rules file: a rules-file line asks, a hook enforces. Practice docs
here use hooks for rules that must not depend on the agent remembering them. The cost is a script
you have to write and keep working, and one that can block legitimate work.

This section describes Claude Code hooks. Whether another harness has hooks is in that harness's
own docs; the harness table has no hooks column. Where you have none, the check has to run
somewhere the agent does not decide. A required CI check on a protected branch does, if the
protection binds the account the agent acts as, the agent's token cannot change the protection,
and the agent cannot, on its own, change what the check runs (see CI and below). A git hook
needs no account or paid plan. It stops an ordinary commit, but `--no-verify` skips it and it sits in each clone's own hooks
directory, so it holds against slips, not against an agent that decides to skip it; see
[Git - githooks Documentation](https://git-scm.com/docs/githooks) (checked 2026-09-30). Permission
rules and an OS sandbox, where your harness has them, are options in
[Stopping an agent's writes before they land](agent-safety-hooks.md). One option there does not
depend on the harness: not giving the agent a token that can do the forbidden thing. Without any of
these, nothing stops the agent, so keep a step that must not happen unattended out of the run: the
agent gets no credential for it, the run ends before it, and a person does it afterwards.

You have it when a hook blocks or changes an action in a session without the agent being asked.
Where your harness has no hooks, you have a substitute when something the agent cannot switch off
stops the same action: a token without that permission, or a required CI check whose protection
binds the account the agent acts as and which the agent's branch cannot change. An admin account
is exempt unless the protection binds admins (see CI). A token that can administer the repository
can switch the protection off. A check triggered by `pull_request` takes the workflow file, and
the scripts and tests it calls, from the branch, so a token that can push to that branch can make
the check pass. It holds if the agent cannot merge an edit to those files without someone else's
approval. A `pull_request_target` workflow that never checks out the pull request reads nothing
from the branch, so the branch cannot fake its result, but an edit to its files that the agent
merges changes every later run, unless the check fails any pull request that edits them; see
[Events that trigger workflows](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows) (checked 2026-10-01)
and step 4 of [How to choose](agent-safety-hooks.md#how-to-choose) in Stopping an agent's writes
before they land.
