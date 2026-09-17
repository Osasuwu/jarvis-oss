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

Each option is marked **tried** (we ran or maintain it; the example says where) or
**sourced** (read from the tool's documentation or source). Quotes and behaviour were checked on
2026-09-17; items marked *code-derived* are a reading of source code, not a documented promise.

## The options

### 1. Full overwrite / regenerate

**How it works.** The tool renders the whole file and writes it, every run. By convention the
file says so at the top; Go's generator convention is a line matching
`^// Code generated .* DO NOT EDIT\.$` before the first non-comment, non-blank text
([go generate](https://github.com/golang/go/blob/master/src/cmd/go/internal/generate/generate.go)).

**Best pick when** the tool owns the file outright and people's changes belong in the tool's
inputs, not its output. Simplest option by far.

**Cost.** Any hand edit is lost on the next run; the header is a warning, not a guard. The first
run is the dangerous one: a file already there is not yours. Home Manager refuses with
"Existing file '$targetPath' would be clobbered" ([check-link-targets.sh](https://github.com/nix-community/home-manager/blob/master/modules/files/check-link-targets.sh)),
unless `backupFileExtension` is set, which will "move existing files by appending the given file
extension rather than exiting with an error"
([nixos/common.nix](https://github.com/nix-community/home-manager/blob/master/nixos/common.nix)).

A **seed-once** variant writes the file only if it is absent and never again: Puppet's
`replace => false` "allows file resources to initialize files without overwriting future changes"
([file.rb](https://github.com/puppetlabs/puppet/blob/main/lib/puppet/type/file.rb)). From then on
the file is the person's, so update and uninstall are given up on purpose.

A **split** variant keeps the tool's file whole and moves the person's changes into a second file
the reader also loads. Docker Compose reads `compose.yaml` and `compose.override.yaml`: "If both
files exist on the same directory level, Compose combines them into a single configuration"
([merge](https://docs.docker.com/compose/how-tos/multiple-compose-files/merge/)). In Claude Code,
"`CLAUDE.local.md` is appended after `CLAUDE.md`"
([memory docs](https://code.claude.com/docs/en/memory)). It works only where the format already
reads such a file and the person accepts editing there.

**Update / uninstall.** Re-render / delete the file. Seed-once: neither.

Status: sourced. We do not use it for `jarvis-setup`: a rules file is the person's.

### 2. Ensure one line

**How it works.** Find your line; add it only if it is absent.
- **By substring.** nvm's installer: `if ! command grep -qc '/nvm.sh' "$NVM_PROFILE"; then` …
  append, else `"=> nvm source string already in ${NVM_PROFILE}"`
  ([nvm install.sh](https://github.com/nvm-sh/nvm/blob/master/install.sh)).
- **By pattern.** Ansible's `lineinfile` "ensures a particular line is in a file, or replace an
  existing line using a back-referenced regular expression"; with `state: absent` the regexp is
  "the pattern of the line(s) to remove"
  ([lineinfile](https://github.com/ansible/ansible/blob/devel/lib/ansible/modules/lineinfile.py)).
  Puppet's `file_line` does the same with `match`; removing by it also needs
  `match_for_absence => true`
  ([file_line](https://github.com/puppetlabs/puppetlabs-stdlib/blob/main/lib/puppet/type/file_line.rb)).

**Best pick when** your content is a line or two: a setting, a `source` line. By substring only if
the line will never change or be removed.

**Cost.** A substring guard counts any line holding the substring as present, a commented-out
or edited copy too, so it can neither update nor restore it; *code-derived:* nvm's re-run never
updates the line and its installer has no removal step. A pattern guard can update and remove,
but the pattern is the contract: it "should typically match both the initial state of the line as
well as its state after replacement", `lineinfile` replaces "Only the last line found", and
`file_line` raises an error on several matches unless `multiple => true`.

**Update / uninstall.** Substring: neither. Pattern: both.

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
mamba's shell init uses the same shape with its own markers. In source code a single anchor
comment can stand in for the pair: *code-derived:* JHipster's generators insert before
`jhipster-needle-*` comments and skip content already present
([needles.ts](https://github.com/jhipster/generator-jhipster/blob/main/generators/base-core/support/needles.ts)).

**Best pick when** the tool installs several lines it may later update or remove, and either the
format has no includes or the tool keeps no file of its own. Shell profiles can `source` a file, so
there it is a choice: conda and mamba made it, rustup chose option 4.

**Cost.**
- Edits a person makes *inside* the block are overwritten on the next run (code-derived for
  both tools).
- Markers are load-bearing. Ansible's `marker` docs: a custom marker without `{mark}` "may
  result in the block being repeatedly inserted on subsequent playbook runs", and multi-line
  markers "will". *Code-derived:* if a person deletes one marker line, `blockinfile` inserts a
  fresh block rather than repairing the old one.
- Duplicates are not cleaned up: conda's source carries
  `# TODO: maybe remove all but last of replace_str, if there's more than one occurrence`.
- It judges only its own lines. *Code-derived:* conda comments out its older lines outside the
  block; `blockinfile` leaves an equivalent line there and adds a second copy.

**Update / uninstall.** Both, by design, within one file.

Status: sourced. See [`conda-init-managed-block.md`](../examples/conda-init-managed-block.md).

### 4. Own file + one include line

**How it works.** The tool keeps its content somewhere it owns completely (option 1 on its own
file) and the person's file carries one line that loads it:
- git: "The contents of the included file are inserted immediately, as if they had been found at
  the location of the include directive" ([git-config](https://git-scm.com/docs/git-config)).
- *Code-derived:* rustup adds `. "$HOME/.cargo/env"` to shell profiles, or the `CARGO_HOME`
  path when it is not the default
  ([shell.rs](https://github.com/rust-lang/rustup/blob/master/src/cli/self_update/shell.rs),
  [unix.rs](https://github.com/rust-lang/rustup/blob/master/src/cli/self_update/unix.rs)).
- The loaded content can be a command's output instead of a file: starship's setup is
  `eval "$(starship init bash)"` ([starship](https://starship.rs/)), so the content updates with
  the binary.
- Claude Code: "CLAUDE.md files can import additional files using `@path/to/import` syntax",
  relative to the importing file, up to four hops deep
  ([memory docs](https://code.claude.com/docs/en/memory)).

A **drop-in directory** is this option with the include built in. systemd reads `.conf` files in
`.d/` that "will be merged in the alphanumeric order and parsed after the main unit file"
([systemd.unit](https://github.com/systemd/systemd/blob/main/man/systemd.unit.xml)). For agent
rules, Claude Code's `.claude/rules/`: "All `.md` files are discovered recursively", and rules
without `paths` "are loaded at launch" ([memory docs](https://code.claude.com/docs/en/memory)).

**Best pick when** the format supports includes or drop-ins and the tool's content changes
between versions.

**Cost.** The include line itself is written with option 2 or 3, and it can fail without a sound:
- git skips a missing include target silently — re-checked: `git config -f main.cfg --includes
  --get` with `include.path` pointing at a nonexistent file returns the other keys and exit 0
  (git 2.44).
- Claude Code: two `@path` imports written inside a sentence, each with a comma glued on, loaded
  nothing for 4–9 days while a substring guard stayed green. The docs allow mid-sentence imports,
  so the comma may be the cause. See
  [`bare-import-silently-dropped.md`](../examples/bare-import-silently-dropped.md).
- Drop-ins have their own rule: in Cursor, "A plain `.md` file in `.cursor/rules` is ignored by
  the rules system because it has no frontmatter" ([rules](https://cursor.com/docs/rules)).
- Placement can decide. In `sshd_config`, "for each keyword, the first obtained value will be
  used", so an include below the person's settings loses to them
  ([sshd_config](https://man.openbsd.org/sshd_config)).
- Where the format has neither, this option does not exist. For agent rules files, see
  [`harnesses.md`](harnesses.md).

So the check that belongs with this option is not "is the line present" but "did the content
load": in Claude Code, `/context` lists loaded memory files.

Where the file is a program slot with no include, such as a git hook, the same idea runs the
other way: the tool takes the slot and calls the person's program. pre-commit installs "in a
migration mode which runs both your existing hooks and hooks for pre-commit", and
`pre-commit uninstall` "will restore your hooks to the state prior to installation"
([pre-commit](https://pre-commit.com/)). Repointing the slot instead drops the person's program:
husky sets `core.hooksPath`
([index.js](https://github.com/typicode/husky/blob/main/index.js)), and git then looks there
"instead of in `$GIT_DIR/hooks`" ([git-config](https://git-scm.com/docs/git-config)).

**Update / uninstall.** Both, and the tool never edits inside the person's content.

Status: tried and sourced.

### 5. Key-level structured merge

**How it works.** For structured formats, set exactly the keys you need through a parser rather
than editing text. `git config --file <f> <key> <value>` ("query/set/replace/unset options");
`npm pkg set` edits `package.json` "making sure to respect the existing indentation"
([npm pkg](https://docs.npmjs.com/cli/v11/commands/npm-pkg)). Libraries do the same inside your
own tool: jsonc-parser's "*modify* API computes edits to insert, remove or replace a property or
value in a JSON document" ([jsonc-parser](https://github.com/microsoft/node-jsonc-parser)).
Augeas covers other formats (`sshd_config`, `/etc/hosts`): it "parses configuration files in
their native formats and transforms them into a tree" ([Augeas](https://augeas.net/), [lenses](https://augeas.net/stock_lenses.html)).

**Best pick when** a parser can edit the file and your contribution is a handful of keys,
and the editor keeps what the person keeps: comments, order, formatting.

**Cost.** Comments and ordering survive only if the editor models them. Check that it says so, as
ruamel.yaml does: "roundtrip preservation of comments, seq/map flow style, and map key order"
([PyPI](https://pypi.org/project/ruamel.yaml/)). Multi-valued keys need a decision: `git config`
exits with status 5 when you "try to unset/set an option for which multiple lines match";
`--replace-all` and `--unset-all` act on all of them. A key the person set on purpose gets
overwritten unless you check first.

**Update / uninstall.** Set / unset the keys — but only if you recorded which keys are yours.

Status: sourced.

### 6. Merge against what you shipped last time

**How it works.** The tool remembers what it wrote last time and, on update, compares three
versions: that base, its new output, and the person's copy.
- **Whole scaffolds.** copier keeps `.copier-answers.yml`, "regenerates a fresh project from the
  current template version", and "re-applies the previously obtained diff"
  ([copier updating](https://github.com/copier-org/copier/blob/master/docs/updating.md)). cruft
  records the template commit in `.cruft.json` and applies the difference with `git apply -3`
  ([update.py](https://github.com/cruft/cruft/blob/master/cruft/_commands/update.py)).
- **One config file.** dpkg: "If both have changed their version the user is prompted about the
  problem and must resolve the differences themselves"
  ([policy](https://www.debian.org/doc/debian-policy/ap-pkg-conffiles.html)). `ucf` gives maintainer
  scripts the same, and `--three-way` merges "using diff3 during the install"
  ([ucf](https://manpages.debian.org/bookworm/ucf/ucf.1.en.html)).

**Best pick when** the tool ships a whole file or scaffold, keeps improving it, and people are
expected to edit their copy.

**Cost.** Needs a stored base the person must not touch: copier's docs say "**Never** update
`.copier-answers.yml` manually". When both sides changed, a person resolves it: a prompt, inline
markers or `.rej` files. Heavy for a few lines in a file the tool does not ship.

**Update / uninstall.** Update is the whole point. Uninstall is usually not a concept — the
output is the person's.

Status: sourced.

### 7. Semantic delta

**How it works.** Read the file, judge for each required item whether the file already states it
*in substance* — same commitment, any wording — and write only what is missing. The judge is a
model or a person, not a string match. This is what `jarvis-setup` does
([resource](../resources/jarvis-setup-skill.md)).

It decides *what* to write, not *where*: the delta still goes in with another option. Plain
append is option 2 unguarded.

**Best pick when** the target is prose that the person may already cover in their own words, and
two phrasings of one rule would drift apart.

**Cost** ([`semantic-delta-real-run.md`](../examples/semantic-delta-real-run.md) records a run):
- **Not deterministic** (follows from the run: one verdict was a close call). Whether a rule
  headed "No hardcoded secrets" covers "Secrets never land in any persistent surface" is a
  judgement; two reviewers can disagree.
- **Sees one file** (seen in the run). Content the person delivers through an include, or from a
  user-level file, is invisible to it, so it can add what is already loaded.
- **No update, no uninstall** when appended plainly (follows from the run: nothing marks what it
  wrote). A re-run finds the old wording already stated in substance and writes nothing.

**Update / uninstall.** Those of the option that carries the delta. With 3 or 4, the judge reads
the person's file minus the tool's block or file, and the tool rewrites that block or file whole.

Status: tried.

### Across all of them: show before writing, or do not write

A switch on any option: compute the change, print it, write only on approval.
`conda init --dry-run` will "Only display what would have been done"; `jarvis-setup` first asks
"Trial or full setup?".

Taken all the way, the tool never writes: it prints the lines and the person adds them.
Homebrew's installer ends with "Next steps:" and the `echo … >> ${shell_rcfile}` commands to run
([install.sh](https://github.com/Homebrew/install/blob/HEAD/install.sh)); rustup-init has
`--no-modify-path` ("Don't configure the PATH environment variable",
[rustup-init.sh](https://github.com/rust-lang/rustup/blob/master/rustup-init.sh)). A tool that
starts the reader itself can skip the file: VS Code activates shell integration "by injecting
arguments and/or environment variables when the shell session launches"
([shell integration](https://code.visualstudio.com/docs/terminal/shell-integration)).

Before replacing a file, validate the result and keep a backup, as Ansible's `template` can:
`validate` runs "before copying the updated file into the final destination"
([template](https://docs.ansible.com/ansible/latest/collections/ansible/builtin/template_module.html)).

Show first when a person should approve each write, or the first time a tool meets a file it did
not create; print only when the file is managed elsewhere or the person opted out. Cost: someone
has to read, or paste.

## How to choose

First, in order:

1. **Is the file managed by other means (dotfiles repo, Nix), or did the person opt out?** Yes →
   **print, do not write**.
2. **Is your tool the only one that edits it?** Yes → **option 1**. If a file you did not create
   is already there, back it up or show first.

**What to write.** Prose the person may already state their own way → **option 7** picks the
missing items. It has no place of its own; the option that carries them decides update and
uninstall.

**Where.** No rule picks one option; several usually fit, and the choice is the trade-off each
option's *best pick when* and *cost* describe. These checkable facts rule options out.

| Option | Fits only if |
|---|---|
| 1, split | the format loads a second file that the person edits |
| 1, seed-once | you will never update or remove what you wrote |
| 2 | your content is a line or two |
| 3 | the format has comments to use as markers |
| 4 | the format has an include or drop-in, or a program slot you can wrap |
| 5 | a parser for the format keeps comments, order and formatting |
| 6 | you ship the whole file and can store a base the person does not touch |

For 2, 3 and 4, check where yours loads: in `sshd_config` the first value wins. Among what is
left: 2 and 3 keep all in the file the person sees; 4 lets yours grow without touching theirs, but
the include can fail silently; 5 sets only your keys in any layout, but can overwrite theirs and
needs a record to uninstall; 6 keeps edits to a file you ship, at the price of a base and
conflicts to resolve. Showing first fits every option. If nothing is left: seed once, print, or
several one-line edits and a record of them.

A line for `~/.bashrc` passes 2, 3 and 4: nvm took 2, conda 3, rustup 4. A key in `package.json`
passes 2 and 5.

**Our own choice.** `jarvis-setup` must work on harnesses without includes and must not restate
rules a person already has, so it takes 7, carried by 4 where the harness has includes and by 3
elsewhere. It shows first, then writes via 4 on Claude Code or 3 elsewhere; both carry an update
and an uninstall step, documented in `jarvis-setup`'s own SKILL.md.
