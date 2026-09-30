---
applies_when: you are setting up an agent to work in a git repository on GitHub and a practice doc here asks for a branch, a pull request, a CI check, a rules file, a skill or a hook that you have not used before
applies_when_not: you already work in pull requests with CI and have written a rules file, a skill and a hook; which harness to pick or where its files live is docs/harnesses.md; whether a given practice is worth adopting is the practice doc that requires this one
signed_off:
kind: basics
---

# Basics

The five things practice docs here assume you already know. Each section says what the thing is,
why it matters once an agent writes the code, and how to tell you have it. It links the official
page instead of repeating it: the official page is the one that stays current. Anything past
that, such as which harness reads which file, is one hop away in [the harness table](harnesses.md).

## Branches and pull requests

In Git, a branch is a movable pointer to a commit, so a separate line of changes costs almost
nothing to create. `git switch -c <name>` makes one and switches to it; `git push -u origin <name>`
publishes it to GitHub. See
[Git Branches in a Nutshell](https://git-scm.com/book/en/v2/Git-Branching-Branches-in-a-Nutshell) (checked 2026-09-30)
and, for how GitHub treats branches, [Branches](https://docs.github.com/en/pull-requests/reference/branches) (checked 2026-09-30).

A pull request proposes merging the changes on one branch into another and gives a place to
review them first. See
[About pull requests](https://docs.github.com/en/pull-requests/get-started/about-pull-requests) (checked 2026-09-30).

For agent work the pull request is the review point: the agent changes a branch and you review the
diff before it is merged. Whether anything stops the agent from pushing straight to your main
branch depends on branch protection, in the next section. Most practice docs here hang a check or
a hold on the merge step.

You have it when you can open a pull request from a branch and see the diff.

## CI

CI runs your checks automatically when something happens in the repository, such as a pull
request opening. On GitHub the tool is Actions, and its workflows start on events in the
repository. See
[Understanding GitHub Actions](https://docs.github.com/en/actions/get-started/understand-github-actions) (checked 2026-09-30).

A check only stops a merge if the branch is protected and lists it as required. See
[About protected branches](https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/managing-protected-branches/about-protected-branches) (checked 2026-09-30).
Protection on a private repository needs a paid plan; on a free plan it covers public
repositories only, so read that page for yours before you rely on it. Without it a failing check
still shows on the pull request, but nothing blocks the merge: then merging only when the checks
are green is a rule you and the agent keep by hand, not enforcement.

You have it when a failing check shows on a pull request. If the branch is protected, you also
have it when the merge button is blocked until the check passes. From a terminal,
`gh pr checks` lists a pull request's checks; see
[gh pr checks](https://cli.github.com/manual/gh_pr_checks) (checked 2026-09-30).

## Rules file

A rules file is a plain markdown file of standing instructions that your agent's harness loads at
the start of each session: build commands, conventions, what to avoid. The name depends on the
harness. Claude Code reads `CLAUDE.md`, and reads `AGENTS.md` only when no `CLAUDE.md` is present;
`AGENTS.md` is a shared name, described as a README for agents, that several other harnesses read;
some harnesses use their own file name. See
[How Claude remembers your project](https://code.claude.com/docs/en/memory) (checked 2026-09-30)
and [AGENTS.md](https://agents.md) (checked 2026-09-30). The file name for your harness is in
[the harness table](harnesses.md).

The agent treats a rules file as context, not as enforcement. It can still ignore a line, which
is why a rule that must hold is enforced by something outside the model: a hook, or the harness's
permission rules.

You have it when the harness lists the file as loaded (in Claude Code, `/context` lists it under
**Memory files**) or, in an unattended run, when the file is at the path the harness reads. That
shows the file is loaded, not that the agent follows every line.

## Skills

A skill is a directory with a `SKILL.md` file: instructions for one repeatable job, which the
agent loads when the job comes up instead of on every session. Claude Code lets the agent pick a
skill when it looks relevant, or you invoke one by name. See
[Extend Claude with skills](https://code.claude.com/docs/en/skills) (checked 2026-09-30).
The file format is an open specification that several tools follow, and some optional fields vary
between tools; see the
[Agent Skills specification](https://agentskills.io/specification) (checked 2026-09-30).

Use a skill for a procedure and a rules file for a standing fact. A long procedure in the rules
file is loaded every session whether or not it is needed.

You have it when you can invoke a skill by name and the agent follows its steps.

## Hooks

A hook is a shell command your harness runs at a fixed point in the agent's work, for example
before a tool call or after a file edit. It runs whether or not the agent decides to follow an
instruction. See
[Automate actions with hooks](https://code.claude.com/docs/en/hooks-guide) (checked 2026-09-30).

That is the difference from a rules file: a rules-file line asks, a hook enforces. Practice docs
here use hooks for rules that must not depend on the agent remembering them. The cost is a script
you have to write and keep working, and one that can block legitimate work.

Not every harness or surface has hooks; check your harness's own docs. Where it has none, the
same enforcement comes from a git hook, which runs on a commit whichever tool made it (see
[Git hooks](https://git-scm.com/docs/githooks) (checked 2026-09-30)), from a CI check, or from the
harness's permission rules.

You have it when a hook blocks or changes an action in a session without the agent being asked.
Where your harness has no hooks, you have the substitute when a git hook or CI check fails on the
same action.
