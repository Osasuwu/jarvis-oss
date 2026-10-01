---
applies_when: you want an agent that works the way you do in your own repositories, and you are deciding which of its standing parts to set up (rules file, skills, hooks, a persona) and where to read about each
applies_when_not: you want a full ready-made persona installed for you, since `jarvis-setup` installs only a one-line persona, an autonomy tier and two invariants; you only need one harness's file names, which is docs/harnesses.md
kind: hub
---

# Your own agent

An agent that works your way is a few plain files you write yourself: a rules file it reads at the
start of each session, skills for jobs you repeat, hooks for rules that must hold, and a persona
if you want one. This page says what each part is and where to read about it. Each part is yours
to write, so the docs below help you decide what to put in it, not what to copy. This repo ships
two things you can install: the `jarvis-setup` skill, which adds whichever of a one-line persona
(a name and a role), an autonomy tier and two invariants your rules file lacks, either as a block
in it or, on Claude Code, as its own file the rules file imports; and a few optional hook scripts,
which the hooks doc below marks as ours and which run on Claude Code only, so they need access to it
([plans](#unattended-runs-and-plans)).

## Children

- [Harness table](harnesses.md)
- [What goes in the rules file, and where](rules-file.md)

## The parts

**Which harness, and what it reads.** Rules-file names, skills directories and include support
differ per harness. [The harness table](harnesses.md) has them, dated per row.

**The rules file.** The standing instructions the agent loads each session. The decision is not
only what to write but where a rule lives: always loaded, loaded for some paths, loaded on demand,
or enforced outside the model. [The rules-file doc](rules-file.md) lays out the options and how to
choose. For what a rules file is, start with [the basics](basics.md#rules-file).

**Skills.** One directory per repeatable job, loaded when the job comes up. What they are and how
to tell you have them is in [the basics](basics.md#skills). Claude Code's page is
[Extend Claude with skills](https://code.claude.com/docs/en/skills) (checked 2026-10-01), and the
format is a published specification:
[Agent Skills](https://agentskills.io/specification) (checked 2026-10-01). When a skill is worth
more than a rules-file line is covered in option 7 of
[the rules-file doc](rules-file.md#option-7-a-thin-root-file-with-the-detail-in-on-demand-docs-or-skills).

**Hooks.** A script the harness runs at a fixed point, whether or not the agent follows an
instruction. [The basics](basics.md#hooks) say what one is. For which writes to stop, and what each
hook catches and misses, see
[stopping an agent's writes before they land](agent-safety-hooks.md).

**A persona, built by hand.** A voice, a role, a way of answering. Apart from the one-line role
that `jarvis-setup` writes, nothing here ships one and nothing installs one: you write it, in
your own words, and no doc in this repo can say what is right for you. It can sit in the rules
file as plain text, whichever harness you use; tone is one of the things
[the rules-file doc](rules-file.md) lists for the personal file. Claude Code also
gives it a place of its own: an output style changes the instructions Claude Code gives Claude to
set "role, tone, and response format", and a custom style leaves out Claude Code's built-in
software engineering instructions unless `keep-coding-instructions` is `true`. The docs say the
two ways of shaping behaviour combine, but neither is enforced
([Output styles](https://code.claude.com/docs/en/output-styles) (checked 2026-10-01);
[Extend Claude Code](https://code.claude.com/docs/en/features-overview) (checked 2026-10-01)). For
a scripted run, `--append-system-prompt` sets it at the system-prompt level
([How Claude remembers your project](https://code.claude.com/docs/en/memory) (checked 2026-10-01)).
Other harnesses have their own place for it; start from their rules-file page in
[the harness table](harnesses.md). Keep a persona to a few lines. It is text the agent reads like
any other, so the failure modes in [the rules-file doc](rules-file.md#the-problem) apply to it.

## Unattended runs and plans

Claude Code itself needs a paid plan, a Console account or a cloud provider's credentials; the
setup page says "Claude Code requires a Pro, Max, Team, Enterprise, or Console account. The free
claude.ai plan does not include Claude Code access" and "You can also use Claude Code with a
third-party API provider like Amazon Bedrock, Google Cloud's Agent Platform, or Microsoft Foundry"
([Advanced setup](https://code.claude.com/docs/en/setup) (checked 2026-10-01)). The shipped hook
scripts and Claude Code's output styles need it.

A persona, like the rest of the rules file, can silently not load in an unattended `claude -p` run
that uses `--bare`, which skips hooks, skills and `CLAUDE.md`. Without `--bare` the run loads the
same context an interactive session would; with it, the parts have to be passed in explicitly
and the run needs credentials other than a subscription login
([Run Claude Code programmatically](https://code.claude.com/docs/en/headless) (checked 2026-10-01)).
[The basics](basics.md#rules-file) have the details.
