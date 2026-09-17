---
applies_when: a tool, script, installer, generator, skill or agent you are building has to put its own content into a file a person already owns and edits — an agent rules file, a shell profile, a config file, a manifest
applies_when_not: the tool creates the file and nobody else ever edits it (regenerate it and stop reading), or it is a one-off manual edit with nothing to re-run
signed_off:
---

# Writing into files the user already owns

## The problem

Your tool needs a few lines to exist in a file someone else keeps: a `PATH` export in
`~/.bashrc`, a key in `package.json`, a section in `CLAUDE.md` or `AGENTS.md`, a setting in a
service's config. The first run is easy. What goes wrong comes later:

- **Destroying their edits** — the tool rewrites the file, or a region of it, and the person's
  own lines are gone.
- **Duplicating itself** — every re-run appends the same block again.
- **Going stale** — version 2 of the tool wants different lines, and it has no way to find and
  replace what version 1 wrote.
- **Leaving residue** — the tool is removed, and its lines stay behind because nothing marks
  which lines were its own.
- **Loading nothing** — the lines are written, but the thing reading the file never picks them
  up, and nothing reports it.

Every option below trades these off differently. Most tools only think about the first two, and
find the other three when they ship a second version.

Each option is marked **tried** (we ran or maintain it; the example says where) or
**sourced** (read from the tool's documentation or source, with the date it was checked).
Quotes and behaviour were checked on 2026-09-17; items marked *code-derived* are a reading of
source code, not a documented promise.

## The options

### 1. Full overwrite / regenerate

**How it works.** The tool renders the whole file and writes it, every run. By convention the
file says so at the top; Go's generator convention is a line matching
`^// Code generated .* DO NOT EDIT\.$` before the first non-comment text
([go generate](https://github.com/golang/go/blob/master/src/cmd/go/internal/generate/generate.go)).

**Best pick when** the tool owns the file outright and people's changes belong in the tool's
inputs, not its output. Simplest option by far: idempotent, trivially updated, removed by
deleting the file.

**Cost.** Any hand edit is lost on the next run; the header is a warning, not a guard. Wrong the
moment a person keeps their own content in the same file.

**Update / uninstall.** Re-render / delete the file.

Status: sourced. We rejected it for `jarvis-setup` **on fit** — a rules file is the person's.

### 2. Guarded append

**How it works.** Search the file for a signature of your line; append only if it is absent.
nvm's installer does exactly this: `if ! command grep -qc '/nvm.sh' "$NVM_PROFILE"; then` …
append, else `"nvm source string already in ${NVM_PROFILE}"`
([nvm install.sh](https://github.com/nvm-sh/nvm/blob/master/install.sh)).

**Best pick when** the content is one line that will never change and never needs removing.

**Cost.** The guard is a substring, so a changed line is not recognised as "yours" and a
commented-out copy counts as present. *Code-derived:* a re-run never updates an existing line,
and nvm's installer has no removal step.

**Update / uninstall.** Neither.

Status: sourced.

### 3. Managed block between markers

**How it works.** The tool writes its content between a begin and an end marker it controls, and
on every run replaces whatever sits between them. `conda init` writes
`# >>> conda initialize >>>` … `# <<< conda initialize <<<` with
`# !! Contents within this block are managed by 'conda init' !!` inside, and removes it again
with `conda init --reverse` — "Undo effects of last conda init."
([initialize.py](https://github.com/conda/conda/blob/main/conda/core/initialize.py),
[main_init.py](https://github.com/conda/conda/blob/main/conda/cli/main_init.py)). Ansible's
`blockinfile` is the general-purpose version: "insert/update/remove a block of multi-line text
surrounded by customizable marker lines", default marker `# {mark} ANSIBLE MANAGED BLOCK`, removed
with `state: absent`
([blockinfile](https://docs.ansible.com/ansible/latest/collections/ansible/builtin/blockinfile_module.html)).
mamba's shell init uses the same shape with its own markers.

**Best pick when** the tool must install, later update, and eventually remove its part of a
file whose format has no includes. The standard answer for shell profiles.

**Cost.**
- Edits a person makes *inside* the block are overwritten on the next run (code-derived for
  both tools). The "managed by" comment exists to say so.
- Markers are load-bearing. Ansible's own notes: a custom marker without `{mark}`, or a
  multi-line marker, "may result in the block being repeatedly inserted on subsequent playbook
  runs". *Code-derived:* if a person deletes one marker line, `blockinfile` inserts a fresh block
  rather than repairing the old one.
- Duplicates are not cleaned up: conda's source carries
  `# TODO: maybe remove all but last of replace_str, if there's more than one occurrence`.
- It still judges nothing: if the person already has an equivalent line outside the block, the
  block adds a second one.

**Update / uninstall.** Both, by design. The strongest option on this axis for a single file.

Status: sourced. See [`conda-init-managed-block.md`](../examples/conda-init-managed-block.md).

### 4. Own file + one include line

**How it works.** The tool writes a file it owns completely (option 1, applied to its own file)
and adds a single include line to the person's file. Examples:
- git: "The contents of the included file are inserted immediately, as if they had been found at
  the location of the include directive" ([git-config](https://git-scm.com/docs/git-config)).
- systemd drop-ins: files in a `.d/` directory "will be merged in the alphanumeric order and
  parsed after the main unit file", so "one only overrides the settings one specifically wants,
  where updates to the unit by the vendor automatically apply"
  ([systemd.unit](https://github.com/systemd/systemd/blob/main/man/systemd.unit.xml)). A drop-in
  directory is this option with the include line built in.
- rustup writes `$CARGO_HOME/env` and adds `. "$CARGO_HOME/env"` to the profile
  ([shell.rs](https://github.com/rust-lang/rustup/blob/master/src/cli/self_update/shell.rs)).
- Claude Code: "CLAUDE.md files can import additional files using `@path/to/import` syntax",
  relative to the importing file, up to four hops deep
  ([memory docs](https://code.claude.com/docs/en/memory)).

**Best pick when** the format supports includes and the tool's content changes between versions.
Update is a rewrite of the tool's own file; uninstall is deleting that file and one line. The
person's file stays readable — one line tells them where the rest lives.

**Cost.** The include itself can fail without a sound:
- git skips a missing include target silently — tried: `git config -f main.cfg --includes --get`
  with `include.path` pointing at a nonexistent file returns the other keys and exit 0 (git
  2.44).
- Claude Code: an `@path` written mid-sentence was never loaded, for months, while a guard that
  checked the substring `@FILE.md` stayed green; only a bare import on its own line was observed
  to load. The docs do not state the line-position rule or what a missing target does. See
  [`bare-import-silently-dropped.md`](../examples/bare-import-silently-dropped.md).
- rustup matches its line by exact text — its own source: "This is whitespace sensitive where it
  should not be."
- Where the format has no includes, this option does not exist. For agent rules files, which
  harnesses support includes is kept in [`harnesses.md`](harnesses.md).

So the check that belongs with this option is not "is the line present" but "did the content
load": in Claude Code, `/context` lists loaded memory files, and the `InstructionsLoaded` hook can
log them.

**Update / uninstall.** Both, and cleaner than option 3: the tool never edits inside the person's
content at all.

Status: tried and sourced.

### 5. Key-level structured merge

**How it works.** For structured formats, set exactly the keys you need through a parser rather
than editing text. `git config --file <f> <key> <value>` ("query/set/replace/unset options");
`npm pkg set` edits `package.json` "making sure to respect the existing indentation"
([npm pkg](https://docs.npmjs.com/cli/v11/commands/npm-pkg)).

**Best pick when** the file is JSON / YAML / TOML / INI and your contribution is a handful of
keys. Every other key, and usually the formatting, survives.

**Cost.** Comments and ordering survive only if the editing tool preserves them — a plain
parse-and-dump loses them. Multi-valued keys need a decision: `git config` exits with status 5
when you "try to unset/set an option for which multiple lines match" unless you pass
`--replace-all`. A key the person set on purpose gets overwritten unless you check first.

**Update / uninstall.** Set / unset the keys — but only if you recorded which keys are yours.

Status: sourced.

### 6. Three-way merge against a stored base

**How it works.** The tool stores what it generated last time (or how to regenerate it), and on
update merges "old output → new output" into the person's edited copy, the way `git merge` does.
copier keeps `.copier-answers.yml`, "regenerates a fresh project from the current template
version", diffs that against the project, updates, and "re-applies the previously obtained diff"
([copier updating](https://github.com/copier-org/copier/blob/master/docs/updating.md)). cruft
records the template commit in `.cruft.json`, renders old and new template, and applies the
difference with `git apply -3`
([update.py](https://github.com/cruft/cruft/blob/master/cruft/_commands/update.py)).

**Best pick when** the tool's output is large (many files, whole scaffolds), the template keeps
evolving, and people are expected to edit the generated result.

**Cost.** Needs git on both sides and a stored base the person must not touch: copier's docs say
"**Never** update `.copier-answers.yml` manually". Conflicts land as inline markers or `.rej` files
that a person must resolve. Heavy for a few lines in one file.

**Update / uninstall.** Update is the whole point. Uninstall is not a concept — the output is the
person's project.

Status: sourced.

### 7. Semantic delta

**How it works.** Read the file, judge for each required item whether the file already states it
*in substance* — same commitment, any wording — and append only what is missing. The judge is a
model or a person, not a string match. This is what `jarvis-setup` does
([resource](../resources/jarvis-setup-skill.md)).

**Best pick when** the target is prose that the person may already cover in their own words, and
a second phrasing of the same rule would do harm (two versions of one rule in an agent's rules
file invite the agent to pick between them). It is the only option here that notices an
equivalent statement written differently.

**Cost** — all observed in a recorded run
([`semantic-delta-real-run.md`](../examples/semantic-delta-real-run.md)):
- **Not deterministic.** Whether "no hardcoded secrets in the repo" covers "secrets never land in
  any persistent surface" is a judgement; two runs or two reviewers can disagree.
- **Sees one file.** Content the person delivers through an include, or from a user-level file,
  is invisible to it, so it can add what is already loaded.
- **No update, no uninstall.** Nothing marks what it wrote. If the tool's required wording
  changes, a re-run judges the old wording "present in substance" and writes nothing; removing the
  tool leaves its lines behind.

**Update / uninstall.** Neither, unless combined with option 3 or 4 (below).

Status: tried.

### Across all of them: show before writing

Not an alternative but a switch on any of the above: compute the change, print it, write only on
approval. `terraform plan` "alone does not actually carry out the proposed changes"
([plan](https://developer.hashicorp.com/terraform/cli/commands/plan)); `conda init --dry-run`
will "Only display what would have been done", with `--verbose` for the diff; `cruft diff` shows
local drift from the template; `jarvis-setup` asks "trial or full" before touching anything.

Turn it on when a person should approve each write, or the first time a tool meets a file it did
not create. Cost: someone has to read the output.

## How to choose

Answer in order; stop at the first yes.

1. **Does anyone other than the tool edit this file?** No → **option 1**.
2. **Does the format support includes (or a drop-in directory), and can you verify the included
   content actually loads?** Yes → **option 4**. Check [`harnesses.md`](harnesses.md) for agent
   rules files.
3. **Is it structured (JSON/YAML/TOML/INI) with a key-level editor available?** Yes → **option 5**.
4. **Is the output a large template that keeps evolving while people edit the result?** Yes →
   **option 6**.
5. **Will a later version need to change or remove what you wrote?** Yes → **option 3**. No, and
   it is one fixed line → **option 2**.
6. **Is it prose the person may already say in their own words?** Yes → **option 7** — and if
   question 5 was also yes, write the delta *inside* a managed block: judge what is missing, then
   own that region with markers so the next version can find and replace it.

Then decide separately whether to show before writing.

| Reader setup | Lands on |
|---|---|
| CLI installer adding a `PATH` line to shell profiles, shipping updates | 3 (managed block), maybe 4 if the tool also ships an env file |
| Tool that needs two scripts in someone's `package.json` | 5 (`npm pkg set`) |
| Company project template, CI files evolve monthly, teams customise | 6 (copier / cruft) |
| Agent rules for Claude Code, content will change between releases | 4 (own file + bare `@import` line), verified with `/context` |
| Agent rules for a harness with no include support, users already have rules in their own words | 7 inside 3 |
| Generated client code nobody edits | 1 |

## Examples

- [`semantic-delta-real-run.md`](../examples/semantic-delta-real-run.md) — option 7, a recorded
  `jarvis-setup` run against a real, already-developed rules file.
- [`bare-import-silently-dropped.md`](../examples/bare-import-silently-dropped.md) — option 4's
  failure mode, from our own setup.
- [`conda-init-managed-block.md`](../examples/conda-init-managed-block.md) — option 3 in a widely
  deployed installer.

**Our own choice.** `jarvis-setup` uses option 7 with show-before-writing, because it must work on
harnesses without includes and must not restate rules a person already has. This doc's question
5 shows what that costs: the skill cannot update or remove its own lines. On Claude Code option 4
would give both; elsewhere, option 7 inside option 3 would. Tracked in #57.

## See also

- [`harnesses.md`](harnesses.md) — which agent harnesses support includes, and their rules-file
  names.
- [`jarvis-setup-skill.md`](../resources/jarvis-setup-skill.md) — the skill behind option 7.
