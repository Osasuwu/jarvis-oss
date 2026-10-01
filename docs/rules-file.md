---
applies_when: you are deciding what goes in the standing-instructions file your coding agent loads at session start, where it lives, or whether to have one at all, and the agent has repeated a mistake that a line of text might have prevented
applies_when_not: the rule must hold every time and a wrong write cannot be undone, which is docs/agent-safety-hooks.md; you only need to know which file name your harness reads, which is docs/harnesses.md; you want a persona or voice for the agent, which docs/own-agent.md covers
signed_off:
kind: practice
hub: own-agent
requires: basics#rules-file
---

# What goes in the rules file, and where

## The problem

A rules file is the cheapest lever you have on an agent: a markdown file it reads at the start of
every session. It is also the easiest to get wrong, because every failure is quiet. Nothing errors
when a line is not loaded, ignored, stale or contradicted. What can go wrong:

- **Not loaded** — the file has the wrong name for the harness, a file with a different name takes
  precedence over it, a flag skips it, or an include line resolved to nothing. The agent behaves
  as if the file did not exist, and nothing says so.
- **Loaded but ignored** — the harness treats the file as context, not enforcement. Claude Code's
  docs say it treats such files as "context, not enforced configuration"
  ([How Claude remembers your project](https://code.claude.com/docs/en/memory) (checked 2026-10-01)).
  A factorial study of 1,650 Claude Code sessions found compliance fell by about 5.6% in odds for
  each further function the agent wrote in a session, and found no detectable effect from how the
  file was structured ([arXiv 2605.10039](https://arxiv.org/abs/2605.10039) (checked 2026-10-01)).
- **Too long, or not worth its tokens** — every line is paid for on every request. Claude Code's
  docs say longer files "consume more context and reduce adherence"
  ([memory](https://code.claude.com/docs/en/memory) (checked 2026-10-01)). Studies disagree on
  whether the file pays back. One reports that context files "does not generally improve task success
  rates" and raises inference cost by over 20% on average, with repository overviews "not helpful"
  although the instructions in the files are well followed ([arXiv 2602.11988](https://arxiv.org/abs/2602.11988) (checked 2026-10-01)). Another,
  on 124 pull requests in 10 repositories, reports lower median runtime (28.64%) and fewer output
  tokens (16.58%) where the file was present
  ([arXiv 2601.20404](https://arxiv.org/abs/2601.20404) (checked 2026-10-01)). Neither settles it
  for your repo; measure.
- **Rot** — a study of 2,303 context files found they "evolve like configuration code through
  frequent, small additions" ([arXiv 2511.12884](https://arxiv.org/abs/2511.12884) (checked 2026-10-01)).
  Lines nobody deletes start to contradict each other, and Claude Code's docs say that when two
  instructions conflict, Claude "may pick one arbitrarily"
  ([memory](https://code.claude.com/docs/en/memory) (checked 2026-10-01)).
- **A hole where the important rule should be** — the same study found security requirements in
  only 14.8% of files. And a rules file is an input that someone else can change: hidden Unicode in
  a rules file can carry instructions a reviewer does not see
  ([Pillar Security](https://www.pillar.security/blog/new-vulnerability-in-github-copilot-and-cursor-how-hackers-can-weaponize-code-agents) (checked 2026-10-01)).
- **One copy per tool** — the same rules in `CLAUDE.md`, `.cursor/rules`, `.github/copilot-instructions.md`
  and a fourth file drift apart.
- **Lost mid-session** — an instruction that was only said in chat does not survive compaction;
  one that is in a nested file may not have loaded yet.

**Who this is for.** A solo developer or a small team using one or more coding agents in a git
repository, attended or unattended. It assumes you know what a rules file is and which file name
your harness reads ([basics](basics.md#rules-file)). A rule that must hold every time is not a
rules-file question; see [stopping an agent's writes](agent-safety-hooks.md).

Each option is marked **tried** (a trace in this repo, linked) or **sourced** (read from the
tool's documentation, not run here). Facts about each tool were checked on 2026-10-01; tools
change these files often, so each link carries its date.

## The options

The options are not exclusive. Most setups use two or three, and the question is which rule goes
where. Where a rule lives is itself the choice: always loaded, loaded only for some files, loaded
only on demand, or enforced outside the model. Options 2 to 7 and 9 to 11 are places a rule can
sit. Option 8 is the one that is not a request to the agent at all.

### Option 1. No rules file

**How it works.** The agent starts each session with the code and the harness's defaults, and you
correct it in chat. Anything it can read in the repository, it can learn there.

**Fits only if** the repo follows its ecosystem's conventions, the build and test commands are the
obvious ones, and you cannot name a line the agent got wrong twice.

**Best pick when** you have just started and do not yet know what the agent gets wrong. The
2602.11988 abstract above reports that context files do not generally help, so an empty file is a
fair baseline to measure the next option against.

**Cost.** You re-type each correction every session, and a teammate's agent never gets it.

**Lifecycle.** Nothing to maintain. Status: sourced.

#### Check

1. Run the task you care about in a fresh session with no rules file loaded (in Claude Code,
   `/context` lists no **Memory files**).
2. If the agent gets a command or convention wrong a second time, move to option 2.

Relations: none

### Option 2. One short hand-written file, always loaded

**How it works.** One markdown file in the repo root that you write and prune by hand: build and
test commands, conventions that differ from the tool's defaults, pitfalls the code does not show.
Claude Code's docs say to keep to facts "Claude should hold in every session" and to target under
200 lines per file ([memory](https://code.claude.com/docs/en/memory) (checked 2026-10-01)). Write each line
concretely enough to verify: "Use 2-space indentation", not "Format code properly".

**Fits only if** you can say, for each line, why the agent could not work it out from the code.
The 2602.11988 abstract says context files help with custom coding practices, not with repository
overviews.

**Best pick when** one harness reads the repo and a handful of non-obvious facts account for most
corrections.

**Cost.** Every line is read on every request. It rots unless someone deletes lines
(see "Keeping it alive"). It is a request, not a guarantee.

**Lifecycle.** Add a line when the agent errs twice; delete a line when the agent follows it
without being told. Status: sourced.

#### Check

1. Start a fresh session. The harness lists the file as loaded (in Claude Code, `/context`, under
   **Memory files**).
2. Ask a question only the file answers, such as "what command runs the tests here?", and compare
   the answer with the file.
3. Count the lines. Over 200 in Claude Code, split or prune.

Relations: none

### Option 3. A generated file, kept as it came

**How it works.** The harness writes the file for you from the code: Claude Code's `/init` "analyzes
your codebase and creates a file with build commands, test instructions, and project conventions it
discovers", and "suggests improvements rather than overwriting" an existing one
([memory](https://code.claude.com/docs/en/memory) (checked 2026-10-01)). Other harnesses have a
similar command.

**Fits only if** you treat the output as a first draft and delete from it. As a final file, it is
mostly directory layouts and dependency lists, which the 2602.11988 abstract found not helpful. Claude
Code's own `/doctor` check proposes cutting content "Claude can derive from the codebase" and keeps
"pitfalls, rationale, and conventions that differ from tool defaults".

**Best pick when** you have no file yet and want a starting point to prune in ten minutes.

**Cost.** It bakes in what the agent could read anyway and goes stale as the code moves.

**Lifecycle.** One-off, then option 2. Status: sourced.

#### Check

1. For each line in the generated file, ask whether the agent could learn it by reading the repo.
   Delete those lines.
2. What is left is your option 2 file. If nothing is left, you were in option 1.

Relations: none

### Option 4. One AGENTS.md shared by several harnesses

**How it works.** `AGENTS.md` is a shared file name that several harnesses read
([AGENTS.md](https://agents.md) (checked 2026-10-01)). Codex, Cursor and Copilot read it directly.
Claude Code reads `AGENTS.md` only when there is no `CLAUDE.md`, `.claude/CLAUDE.md` or
`CLAUDE.local.md` in the working directory or above; with one, you choose between an `@AGENTS.md`
import in a `CLAUDE.md`, a symlink, or the **Project instructions** setting
`claude-md-and-agents-md`. Gemini CLI reads `AGENTS.md` once you list it under `context.fileName`
([GEMINI.md](https://github.com/google-gemini/gemini-cli/blob/main/docs/cli/gemini-md.md) (checked 2026-10-01)).
Cursor documents `AGENTS.md` as "a simple markdown file for defining agent instructions"
([rules](https://cursor.com/docs/context/rules) (checked 2026-10-01)), and Copilot reads "the nearest
`AGENTS.md` file in the directory tree"
([repository instructions](https://docs.github.com/en/copilot/how-tos/configure-custom-instructions/add-repository-instructions) (checked 2026-10-01)).

**Fits only if** more than one harness works in the repo, or you expect to switch. Prefer the
`@AGENTS.md` import to a symlink when anyone on Windows clones the repo: the Claude Code docs warn
that a committed symlink is checked out there as a one-line text file, and that its Edit and Write
tools refuse to write through a symlink.

**Best pick when** the rules really are the same for every tool.

**Cost.** You get the lowest common denominator. Path-scoped rules and tool-specific features
need each tool's own file. A `CLAUDE.local.md` that you add later switches off the default
`AGENTS.md` reading for you, silently. And an include line can fail without any message: see the
[trace of an include line that loaded nothing](../examples/bare-import-silently-dropped.md).

**Lifecycle.** One file to edit. Verify after any change to the harness's version or settings.
Status: sourced; the include-line failure is tried, in the trace above.

#### Check

1. In a fresh session of each harness you use, ask for a line that only `AGENTS.md` contains.
2. In Claude Code, `/context` lists the file you expect under **Memory files**, and no entry
   appears twice.
3. Put an `@AGENTS.md` import alone on its own line, and confirm by step 1 that it loaded.

Relations: trade-off: [basics](basics.md#rules-file)

### Option 5. Nested files per directory

**How it works.** A file in a subdirectory carries the rules for that part of the tree. Claude
Code loads those "when Claude reads files in those directories", not at launch, and
`claudeMdExcludes` lets you skip other teams' files in a large monorepo
([memory](https://code.claude.com/docs/en/memory) (checked 2026-10-01)). Codex walks from the git
root down to the working directory, checking each level for `AGENTS.override.md`, then
`AGENTS.md`, then the names in `project_doc_fallback_filenames`, and stops adding files at a
combined 32 KiB by default
([AGENTS.md guide](https://learn.chatgpt.com/docs/agent-configuration/agents-md) (checked 2026-10-01)).
Gemini CLI scans a directory and its ancestors when a tool touches a file there.

**Fits only if** the repo has parts with different rules, such as a monorepo with a frontend and
a data pipeline, and the rules for each part are short.

**Best pick when** a root file has grown past a screen and most of it is about one directory.

**Cost.** The rule is not in context at the start of a task that has not read a file there yet.
Files closer to the working directory come later in the prompt, but conflicts are still resolved by
the model. Discovery rules differ per tool, so one layout can work in one harness and not another.

**Lifecycle.** One file per directory to keep alive. Status: sourced.

#### Check

1. In a fresh session, ask the agent to read a file in the nested directory, then ask for a line
   from the nested rules file.
2. In Claude Code, the `InstructionsLoaded` hook can log which files loaded, when and why.
3. If you work in Codex, add the sizes of the files on the path from the root; over 32 KiB the
   later ones are not read.

Relations: none

### Option 6. Path-scoped rules

**How it works.** A rule file says which paths it is for, and the harness loads it only then.
Claude Code: `.claude/rules/*.md` with a `paths:` list in the frontmatter, loaded "when Claude
works with matching files", and rules without `paths` load at launch
([memory](https://code.claude.com/docs/en/memory) (checked 2026-10-01)). Cursor: `.mdc` files in `.cursor/rules` with four
types, among them "Apply to Specific Files" and "Apply Intelligently"
([rules](https://cursor.com/docs/context/rules) (checked 2026-10-01)). Copilot:
`.github/instructions/NAME.instructions.md` with `applyTo` globs. Each tool has its own format, and
none of these is shared between harnesses.

**Fits only if** the rule follows a file type or directory (for example, validate input in `src/api/**`)
rather than a task, and you have two or more such groups.

**Best pick when** the always-loaded file is long because of one language or layer.

**Cost.** In Claude Code, path-scoped rules trigger when a file matching the pattern is read, not
on every tool use, so a rule can arrive after the agent has already started writing. A bad glob
matches nothing, silently. Cursor ignores a plain `.md` file in `.cursor/rules` unless it has
frontmatter. Every tool needs its own copy.

**Lifecycle.** One file per topic. Status: sourced.

#### Check

1. In a fresh session, open a file the glob should match and confirm the rule loaded, with
   `/context` or the `InstructionsLoaded` hook in Claude Code.
2. Open a file the glob should not match and confirm it did not load.

Relations: none

### Option 7. A thin root file, with the detail in on-demand docs or skills

**How it works.** The always-loaded file stays short and points at material the agent reads only
when the task needs it: a skill, or a doc in the repo. Claude Code's docs split it this way:
"Put it in CLAUDE.md if Claude should always know it", and "put it in a skill if it's reference
material Claude needs sometimes" or a workflow you start with `/<name>`
([Extend Claude Code](https://code.claude.com/docs/en/features-overview) (checked 2026-10-01)). A skill's
description loads every request and its body only when used. An `@path` import does not do this:
imported files load at launch, so it helps organization but not context cost.

**Fits only if** you have procedures or reference material of more than a few lines, and the
agent can be trusted to look something up when the task calls for it.

**Best pick when** most of the root file is "how to do X" rather than "always do Y".

**Cost.** It depends on the agent deciding to load the skill. Vague or overlapping descriptions
mean the wrong skill is loaded, or none is. So a rule that must be followed cannot rest only here.

**Lifecycle.** A skill to write and revise. Status: tried. This repo keeps its procedures in skills,
for example [the write-doc skill](../.agents/skills/write-doc/SKILL.md).

#### Check

1. Start a fresh session and give the task without naming the skill. Check that the agent loads
   it; if it does not, rewrite the description.
2. Invoke the skill by name and check that the agent follows its steps.

Relations: needs: [basics](basics.md#skills)

### Option 8. Move the rule out of the file

**How it works.** The rule becomes something the model does not decide: a hook that blocks the
action, a deny rule in settings, a required CI check on a protected branch. Claude Code's docs:
"To block an action regardless of what Claude decides, use a PreToolUse hook instead"
([memory](https://code.claude.com/docs/en/memory) (checked 2026-10-01)), and an instruction like "never edit `.env`" in a rules file or skill
"is a request, not a guarantee" ([Extend Claude Code](https://code.claude.com/docs/en/features-overview) (checked 2026-10-01)).
For an organization, managed settings are the technical enforcement layer, and a managed
`CLAUDE.md` is for behavioral guidance.

**Fits only if** breaking the rule costs more than writing and maintaining the check, because a
check is a script that can block legitimate work. The cost of a violation, not how important the
rule feels, decides where it goes: a secret pushed to a public repo belongs here, a naming
preference does not.

**Best pick when** the mistake cannot be undone by review, such as something that becomes public
or permanent the moment it is written.

**Cost.** A script to keep working, false positives, and per harness: another tool needs its own.
A check that is not wired fails silent the same way an unloaded file does.

**Lifecycle.** Code with tests. Status: tried. This repo's hooks, and what each one catches and
misses, are in [stopping an agent's writes before they land](agent-safety-hooks.md).

#### Check

1. Make the agent attempt the forbidden action in a throwaway branch. The action is blocked or
   changed, without the agent being asked to follow a rule.
2. Remove the check and repeat; the action goes through. That is the evidence the check, not the
   instruction, did the work.

Relations: needs: [hooks](basics.md#hooks); trade-off: [stopping an agent's writes before they land](agent-safety-hooks.md)

### Option 9. Personal and layered files

**How it works.** Rules that are yours, not the team's, go in a file the repo does not carry.
Claude Code: `~/.claude/CLAUDE.md` for every project, and a git-ignored `CLAUDE.local.md` in a
project; all discovered files are concatenated, not overridden
([memory](https://code.claude.com/docs/en/memory) (checked 2026-10-01)). Codex checks `~/.codex`
first, Gemini CLI reads `~/.gemini/GEMINI.md` for "default instructions for all your projects"
([GEMINI.md](https://github.com/google-gemini/gemini-cli/blob/main/docs/cli/gemini-md.md) (checked 2026-10-01)).
An organization can deploy a managed file that individual settings cannot exclude.

**Fits only if** your preferences differ from the team's (tone, editor, your own sandbox URLs), or
you manage devices and need an org-wide baseline.

**Best pick when** you work alone across several repos and the same process rules apply to all.

**Cost.** Layers all load, and when a personal and a project rule conflict "Claude may follow
either one" ([memory](https://code.claude.com/docs/en/memory) (checked 2026-10-01)). A git-ignored local file exists only in the worktree where you created it. A
`CLAUDE.local.md` also stops Claude Code reading a project's `AGENTS.md` by default. Personal files
differ per machine, so a setup is only as portable as the habit of copying it. The
[include-line trace](../examples/bare-import-silently-dropped.md) is from a user-level file that
imports others.

**Lifecycle.** Edit on every device. Status: sourced; the include-line failure is tried, in the
trace above.

#### Check

1. In a fresh session in a project that has its own file, ask for a line that exists only in the
   personal file.
2. Look for a rule that appears in both layers with different wording and fix one of them.

Relations: none

### Option 10. Auto memory, written by the agent

**How it works.** The agent keeps its own notes: Claude Code's auto memory saves what it learns from
your corrections, per repository, in `~/.claude/projects/<project>/memory/`. "The first 200 lines
of `MEMORY.md`, or the first 25KB, whichever comes first, are loaded at the start of every
conversation", and detailed notes live in topic files it reads on demand
([memory](https://code.claude.com/docs/en/memory) (checked 2026-10-01)). It is on by default, and Claude skips what it can
derive from the code or what your CLAUDE.md already says.

**Fits only if** you work alone on one machine, you are content for the agent to choose what to
keep, and you will read the folder from time to time. The files are machine-local, not shared
across machines or cloud environments.

**Best pick when** the knowledge is about you and the way you work, not about the repo.

**Cost.** Nobody reviews it, it is not in git, and a wrong note repeats until someone finds it.
It is Claude Code's, so another harness does not read it.

**Lifecycle.** Browse and prune the folder with `/memory`. Status: sourced.

#### Check

1. Run `/memory` and open the auto memory folder. Read every note.
2. Delete or fix a note that is wrong or about a one-off task, then check a fresh session no longer
   acts on it.

Relations: none

### Option 11. A configurable name, a read-only flag, or a system-prompt flag

**How it works.** Where a tool has no native rules file, or a run must not depend on one, you
name the file yourself. Gemini CLI: `context.fileName` takes a list such as `AGENTS.md`,
`CONTEXT.md`, `GEMINI.md`. Aider: `aider --read CONVENTIONS.md`, or `read:` in `.aider.conf.yml`;
the file "is marked as read-only, and cached if prompt caching is enabled"
([conventions](https://aider.chat/docs/usage/conventions.html) (checked 2026-10-01)). In Claude Code,
`--append-system-prompt` puts instructions at the system-prompt level, which suits scripts better
than interactive use ([memory](https://code.claude.com/docs/en/memory) (checked 2026-10-01)). In an unattended `claude -p` run, `--bare`
skips `CLAUDE.md`, so a rules file needs to be passed in this way.

**Fits only if** the tool lacks a native file, or the run is scripted and you need the rules to
load whatever the directory holds.

**Best pick when** you move between tools and want one file for all of them, or when a scripted
call has to carry its rules explicitly.

**Cost.** The flag has to be set on every machine and every run, and forgetting it fails silent.
A system-prompt flag is also one more place the rules live.

**Lifecycle.** A line in a config file or a script. Status: sourced.

#### Check

1. In the run that matters, ask for a line that only the file contains.
2. Run the same command without the flag. The line is missing, so you know the flag did it.

Relations: none

## How to choose

Ask these in order; each answer moves a rule to a different place.

1. **What does a violation cost, and can it be undone?** If it becomes public or permanent before
   anyone reads it, do not write a rule: build the check (option 8). If it is a style preference,
   a line is enough, or nothing.
2. **Can the agent learn it from the code?** Then it does not belong in a file (option 1, or
   the pruning in options 2 and 3).
3. **Is it true of every task, or only of some?** Every task: always loaded (options 2 and 4).
   One part of the tree or one file type: option 5 or 6. A procedure you run now and then:
   option 7.
4. **How many harnesses touch the repo?** One: use its native file. More than one: option 4, and
   path-scoped rules in each tool's own format.
5. **Is it the team's or yours?** Yours: option 9 or 10. The team's: in the repo, where it is
   reviewed.
6. **Unattended?** Then confirm the file loads at all (option 11, and the `--bare` note in
   [basics](basics.md#rules-file)).

A common result is two or three of these together: a short root file, a few path-scoped rules, one
or two skills, and a check for the one rule whose violation costs most. If your answers point to
more than that, start with the cheapest and add the next only after the agent has made the same
mistake twice.

Test the file, not your belief in it. The studies above disagree on whether a context file pays
for itself. Run one task you care about with and without the file, and keep the lines that change
the result.

## Our own choice, as one example

This repository has no root `AGENTS.md` or `CLAUDE.md`. What its agents are told lives in skills
under `.agents/skills/`, loaded when the job comes up (option 7), and the rules that must hold are
checks under `.agents/hooks/` and in CI (option 8), described in
[the safety-hooks doc](agent-safety-hooks.md). The maintainer's personal rules are a user-level
file that imports others (option 9), which is where the
[include-line trace](../examples/bare-import-silently-dropped.md) comes from. That is one answer
to the questions above for a solo maintainer with one harness. A team on several harnesses
would answer question 4 differently.

## Keeping it alive

1. Add a line when the agent makes the same mistake a second time, or when a review comment
   repeats. Claude Code's docs give the same triggers.
2. Delete a line when the agent follows it without being told. Re-run the with-and-without test
   when you change the model or harness.
3. In Claude Code, `/doctor prompt-audit` reads your instruction files for contradictions,
   references to files or commands that no longer exist, and instructions written for older
   models; it proposes edits and changes nothing until you ask.
4. Put notes for maintainers in an HTML comment: block-level comments are stripped before the
   file reaches the agent, so they cost no context.
5. Review changes to a rules file like code, and look at the raw bytes of a file you did not
   write: hidden Unicode does not show in a rendered diff.
