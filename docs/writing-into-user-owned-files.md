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
**sourced** (read from the tool's documentation or source). Quotes and behaviour were checked on
2026-09-17; items marked *code-derived* are a reading of source code, not a documented promise.

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
moment a person keeps their own content in the same file. The first run is the dangerous one: a
file already there is not yours. Home Manager refuses with "Existing file '$targetPath' would be
clobbered" ([check-link-targets.sh](https://github.com/nix-community/home-manager/blob/master/modules/files/check-link-targets.sh)),
unless `backupFileExtension` is set, which will "move existing files by appending the given file
extension rather than exiting with an error"
([nixos/common.nix](https://github.com/nix-community/home-manager/blob/master/nixos/common.nix)).

**Update / uninstall.** Re-render / delete the file.

Status: sourced. We rejected it for `jarvis-setup` **on fit** — a rules file is the person's.

### 2. Ensure one line

**How it works.** Find your line; add it only if it is absent.
- **By substring.** nvm's installer: `if ! command grep -qc '/nvm.sh' "$NVM_PROFILE"; then` …
  append, else `"nvm source string already in ${NVM_PROFILE}"`
  ([nvm install.sh](https://github.com/nvm-sh/nvm/blob/master/install.sh)).
- **By pattern.** Ansible's `lineinfile` "ensures a particular line is in a file, or replace an
  existing line using a back-referenced regular expression"; with `state: absent` the regexp is
  "the pattern of the line(s) to remove"
  ([lineinfile](https://github.com/ansible/ansible/blob/devel/lib/ansible/modules/lineinfile.py)).
  Puppet's `file_line` does the same with `match`
  ([file_line](https://github.com/puppetlabs/puppetlabs-stdlib/blob/main/lib/puppet/type/file_line.rb)).

**Best pick when** your content is one line in a format with no includes: a single setting, a
`source` line. By substring only if the line will never change or be removed.

**Cost.** A substring guard does not recognise a changed line as yours and counts a
commented-out copy as present; *code-derived:* nvm's re-run never updates the line and its
installer has no removal step. A pattern guard can update and remove, but the pattern is the
contract: it "should typically match both the initial state of the line as well as its state
after replacement", `lineinfile` replaces "Only the last line found", and `file_line` raises an
error on several matches unless `multiple => true`.

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

**Best pick when** the tool must install, later update, and eventually remove several lines in a
file whose format has no includes, and it ships no file of its own to point at. Common in shell
profiles (conda, mamba).

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

**Update / uninstall.** Both, by design, within one file.

Status: sourced. See [`conda-init-managed-block.md`](../examples/conda-init-managed-block.md).

### 4. Own file + one include line

**How it works.** The tool keeps its content somewhere it owns completely (option 1 on its own
file) and the person's file carries one line that loads it:
- git: "The contents of the included file are inserted immediately, as if they had been found at
  the location of the include directive" ([git-config](https://git-scm.com/docs/git-config)).
- *Code-derived:* rustup adds `. "$HOME/.cargo/env"` to shell profiles, or that path made
  absolute for a non-default `CARGO_HOME`
  ([shell.rs](https://github.com/rust-lang/rustup/blob/master/src/cli/self_update/shell.rs)).
- The loaded content can be a command's output instead of a file: starship's setup is
  `eval "$(starship init bash)"` ([starship](https://starship.rs/)), so the content updates with
  the binary.
- Claude Code: "CLAUDE.md files can import additional files using `@path/to/import` syntax",
  relative to the importing file, up to four hops deep
  ([memory docs](https://code.claude.com/docs/en/memory)).

A **drop-in directory** is this option with the include built in. systemd reads `.d/` files that
"will be merged in the alphanumeric order and parsed after the main unit file"
([systemd.unit](https://github.com/systemd/systemd/blob/main/man/systemd.unit.xml)). For agent
rules, Claude Code's `.claude/rules/`: "All `.md` files are discovered recursively", and rules
without `paths` "are loaded at launch" ([memory docs](https://code.claude.com/docs/en/memory)).

**Best pick when** the format supports includes or drop-ins and the tool's content changes
between versions. Update is a rewrite of the tool's own file; uninstall is deleting that file and
one line. The person's file stays readable — one line tells them where the rest lives.

**Cost.** The include line itself is written with option 2 or 3, and it can fail without a sound:
- git skips a missing include target silently — tried: `git config -f main.cfg --includes --get`
  with `include.path` pointing at a nonexistent file returns the other keys and exit 0 (git
  2.44).
- Claude Code: two `@path` imports written inside a sentence, each with a comma glued on, loaded
  nothing for 4–9 days while a substring guard stayed green. Bare lines fixed it. The docs show a
  mid-sentence import as valid ("See @README for project overview and @package.json for available
  npm commands"), so the glued comma may be the cause. See
  [`bare-import-silently-dropped.md`](../examples/bare-import-silently-dropped.md).
- Drop-ins have their own rule: in Cursor, "A plain `.md` file in `.cursor/rules` is ignored by
  the rules system because it has no frontmatter" ([rules](https://cursor.com/docs/rules)).
- rustup matches its line by exact text — its own source: "This is whitespace sensitive where it
  should not be."
- Where the format has neither, this option does not exist. For agent rules files, see
  [`harnesses.md`](harnesses.md).

So the check that belongs with this option is not "is the line present" but "did the content
load": in Claude Code, `/context` lists loaded memory files.

**Update / uninstall.** Both, and the tool never edits inside the person's content.

Status: tried and sourced.

### 5. Key-level structured merge

**How it works.** For structured formats, set exactly the keys you need through a parser rather
than editing text. `git config --file <f> <key> <value>` ("query/set/replace/unset options");
`npm pkg set` edits `package.json` "making sure to respect the existing indentation"
([npm pkg](https://docs.npmjs.com/cli/v11/commands/npm-pkg)). Libraries do the same inside your
own tool: jsonc-parser's "*modify* API computes edits to insert, remove or replace a property or
value in a JSON document" ([jsonc-parser](https://github.com/microsoft/node-jsonc-parser)).

**Best pick when** the file is JSON / YAML / TOML / INI and your contribution is a handful of
keys, and the editor keeps what the person keeps: comments, order, formatting.

**Cost.** Comments and ordering survive only if the editor models them. Check that it says so, as
ruamel.yaml does: "roundtrip preservation of comments, seq/map flow style, and map key order"
([PyPI](https://pypi.org/project/ruamel.yaml/)). Multi-valued keys need a decision: `git config`
exits with status 5 when you "try to unset/set an option for which multiple lines match" unless
you pass `--replace-all`. A key the person set on purpose gets overwritten unless you check first.

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
- **One config file.** dpkg asks the person only when both sides changed; its `--force-conf*`
  choices apply "If a conffile has been modified and the version in the package did change"
  ([dpkg](https://manpages.debian.org/bookworm/dpkg/dpkg.1.en.html)). `ucf` gives maintainer
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
append is option 2 without a guard.

**Best pick when** the target is prose that the person may already cover in their own words, and
a second phrasing of the same rule would do harm (two versions of one rule in an agent's rules
file invite the agent to pick between them). It is the only option here that notices an
equivalent statement written differently.

**Cost** ([`semantic-delta-real-run.md`](../examples/semantic-delta-real-run.md) records a run):
- **Not deterministic** (seen in the run). Whether "no hardcoded secrets in the repo" covers
  "secrets never land in any persistent surface" is a judgement; two reviewers can disagree.
- **Sees one file** (seen in the run). Content the person delivers through an include, or from a
  user-level file, is invisible to it, so it can add what is already loaded.
- **No update, no uninstall** when appended plainly (follows from the run: nothing marks what it
  wrote). A re-run judges old wording "present in substance" and writes nothing.

**Update / uninstall.** Only through the option that carries the delta: 3 or 4.

Status: tried.

### Across all of them: show before writing, or do not write

A switch on any option: compute the change, print it, write only on approval. `terraform plan`
"alone does not actually carry out the proposed changes"
([plan](https://developer.hashicorp.com/terraform/cli/commands/plan)); `conda init --dry-run`
will "Only display what would have been done"; `jarvis-setup` asks "trial or full" first.

Taken all the way, the tool never writes: it prints the lines and the person adds them.
Homebrew's installer ends with "Next steps:" and the `echo … >> ${shell_rcfile}` commands to run
([install.sh](https://github.com/Homebrew/install/blob/HEAD/install.sh)); rustup-init has
`--no-modify-path` ("Don't configure the PATH environment variable",
[rustup-init.sh](https://github.com/rust-lang/rustup/blob/master/rustup-init.sh)).

Show first when a person should approve each write, or the first time a tool meets a file it did
not create. Do not write when the person manages the file by other means (dotfiles repo, Nix)
and a tool's edit would be undone or unwanted. Cost: someone has to read, or paste.

## How to choose

Questions 1, 2 and 4–7 stop at the first yes. Question 3 never stops: it changes *what* you
write, and the rest decide *where*.

1. **Would the person rather paste it themselves** (file managed by a dotfiles repo or Nix)?
   Yes → **print, do not write**.
2. **Does anyone other than the tool edit this file?** No → **option 1**, refusing to replace a
   file you did not create.
3. **Is it prose the person may already say in their own words?** Yes → **option 7** decides what
   is missing. Go on.
4. **Does the format support includes or a drop-in directory, and can you verify the content
   loads?** Yes → **option 4**. See [`harnesses.md`](harnesses.md) for agent rules files.
5. **Is it structured (JSON/YAML/TOML/INI), with an editor that keeps comments and order?** Yes →
   **option 5**.
6. **Does the tool ship the whole file or scaffold, keep improving it, and expect people to edit
   their copy?** Yes → **option 6**.
7. **Is your content one line?** Yes → **option 2**: by pattern if it may change or be removed,
   by substring if never. No → **option 3**.

Then decide whether to show before writing.

| Reader setup | Lands on |
|---|---|
| CLI installer that ships an env script or `init` command, adding it to shell profiles | 4 via step 4 (rustup, starship) |
| Installer whose several profile lines have no file of their own | 3 via step 7 (conda) |
| Setting one directive in `sshd_config`, may change later | 2 by pattern via step 7 |
| Tool that needs two scripts in someone's `package.json` | 5 via step 5 |
| Company project template, CI files evolve monthly, teams customise | 6 via step 6 (copier / cruft) |
| Package that ships a default config people edit | 6 via step 6 (dpkg / ucf) |
| Agent rules for Claude Code that users may already state their own way | 7 via step 3, then 4 via step 4 |
| Agent rules for a harness with no include support, same users | 7 via step 3, then 3 via step 7 |
| Dotfiles managed in Nix or a repo | print via step 1 |
| Generated client code nobody edits | 1 via step 2 |

## Examples

- [`semantic-delta-real-run.md`](../examples/semantic-delta-real-run.md) — option 7, a recorded
  `jarvis-setup` run against a real, already-developed rules file.
- [`bare-import-silently-dropped.md`](../examples/bare-import-silently-dropped.md) — option 4's
  failure mode, from our own setup.
- [`conda-init-managed-block.md`](../examples/conda-init-managed-block.md) — option 3 in a widely
  deployed installer.

**Our own choice.** `jarvis-setup` must work on harnesses without includes and must not restate
rules a person already has. The steps above send that case to 7 then 4 on Claude Code, and to 7
then 3 elsewhere. The skill today does 7 with show-before-writing and a plain append, so it
cannot update or remove its own lines. Closing that gap is #57.

## See also

- [`harnesses.md`](harnesses.md) — which agent harnesses support includes, and their rules-file
  names.
- [`jarvis-setup-skill.md`](../resources/jarvis-setup-skill.md) — the skill behind option 7.
