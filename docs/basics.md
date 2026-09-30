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

A branch is a separate line of changes in your repository. A pull request proposes merging the
changes on one branch into another and gives a place to review them first. See
[About pull requests](https://docs.github.com/en/pull-requests/collaborating-with-pull-requests/proposing-changes-to-your-work-with-pull-requests/about-pull-requests) (checked 2026-09-30).

For agent work the pull request is the review point: the agent changes a branch, and nothing
reaches your main branch until the pull request is merged. Most practice docs here hang a check or
a hold on that step.

You have it when you can open a pull request from a branch and see the diff.

## CI

CI runs your checks automatically when something happens in the repository, such as a pull
request opening. On GitHub the tool is Actions, and its workflows start on events in the
repository. See
[Understanding GitHub Actions](https://docs.github.com/en/actions/get-started/understand-github-actions) (checked 2026-09-30).

A check only stops a merge if the branch is protected and lists it as required. See
[About protected branches](https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/managing-protected-branches/about-protected-branches) (checked 2026-09-30).
Whether protection is available depends on your repository and plan, so read that page for yours.

You have it when a failing check shows on a pull request and the merge button is blocked until it
passes.

## Rules file

A rules file is a plain markdown file of standing instructions that your agent's harness loads at
the start of each session: build commands, conventions, what to avoid. Claude Code reads
`CLAUDE.md`; other harnesses read `AGENTS.md`, described as a README for agents. See
[How Claude remembers your project](https://code.claude.com/docs/en/memory) (checked 2026-09-30)
and [AGENTS.md](https://agents.md) (checked 2026-09-30). The file name for your harness is in
[the harness table](harnesses.md).

The agent treats a rules file as context, not as enforcement. It can still ignore a line, which
is why a rule that must hold belongs in a hook.

You have it when a fresh session behaves as the file says without you repeating it.

## Skills

A skill is a directory with a `SKILL.md` file: instructions for one repeatable job, which the
agent loads when the job comes up instead of on every session. Claude Code lets the agent pick a
skill when it looks relevant, or you invoke one by name. See
[Extend Claude with skills](https://code.claude.com/docs/en/skills) (checked 2026-09-30).
The file format is shared across tools; see the
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
here use hooks for rules that must not depend on the agent remembering them.

You have it when a hook blocks or changes an action in a session without the agent being asked.
