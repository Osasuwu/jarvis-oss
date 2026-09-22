# Calibration corpus for review-doc

Each entry is a defect that a review-doc round missed and the next round found. Entries are
labelled and counted under [`RULES.md`](RULES.md), which was committed before this file. Where a
term here is unclear, `RULES.md` defines it.

The figures below are a floor on same-model agreement, not a recall figure. The writer and the
reviewer are the same model. A defect that both missed in every round is not here.

This file sits outside the drift key, which hashes `SKILL.md` only. Adding entries does not start a
new calibration window.

No seeded defects are included. If any are added, they go in a separate file and stay out of these
counts.

## How entries were checked

- **Escaped.** Round N is the latest round that had the defective text in scope. For every entry,
  the defective lines at round N are absent from `git diff <round N> <round N+1> -- <doc>`: the
  same lines, byte for byte, are present at both commits. No round N report names the defect.
- **Location.** The path and lines are at the round N commit (`commit`). The finding may cite
  other line numbers, because it was written against the round N+1 commit.
- **Evidence.** `escape` links the comment that posted the round N+1 finding. On PR #81, round 2's
  findings were posted only as the writer's replies, so the link goes to that reply.
- **Quoted text.** Defect lines quote the public repo only. Where a line could carry personal
  detail, it is paraphrased.
- **Held-out.** Each commit that edited the reviewer skill, or a file it loads, was checked
  against every entry, along with its PR body. One entry is named there; it is listed after the
  counts.

Scope of PR #75: several lines were new at round N (`3d896f9`), not carried from an earlier round.
They were in scope there because that round's reports are headed `@3d896f9`, it re-checked every
open finding at that commit, and its pass 4 ran at that commit.

## Entries

### 62-go

- source_pr: [#62](https://github.com/Osasuwu/jarvis-oss/pull/62)
- commit: `8067d67`
- location: `docs/writing-into-user-owned-files.md:38`
- class: quote
- label: blocking
- held_out: no
- selection_source: model
- escape: round N `8067d67` → [round N+1 finding at `59a27d9`](https://github.com/Osasuwu/jarvis-oss/pull/62#issuecomment-5712324855)
- defect: The Go generated-file header rule is quoted as "before the first non-comment text"; Go's source says "non-comment, non-blank".

### 62-puppet

- source_pr: [#62](https://github.com/Osasuwu/jarvis-oss/pull/62)
- commit: `8067d67`
- location: `docs/writing-into-user-owned-files.md:67`
- class: fact
- label: blocking
- held_out: no
- selection_source: model
- escape: round N `8067d67` → [round N+1 finding at `59a27d9`](https://github.com/Osasuwu/jarvis-oss/pull/62#issuecomment-5712324855)
- defect: "Puppet's `file_line` does the same with `match`": with `ensure => absent`, `match` is ignored unless `match_for_absence` is set, so removal by pattern does not follow.

### 62-ansible

- source_pr: [#62](https://github.com/Osasuwu/jarvis-oss/pull/62)
- commit: `8067d67`
- location: `docs/writing-into-user-owned-files.md:110-111`
- class: quote
- label: blocking
- held_out: no
- selection_source: model
- escape: round N `8067d67` → [round N+1 finding at `59a27d9`](https://github.com/Osasuwu/jarvis-oss/pull/62#issuecomment-5712324855)
- defect: The `blockinfile` repeated-insertion quote is credited to Ansible's notes; it is in the `marker` parameter description, and the wording differs.

### 62-exit5

- source_pr: [#62](https://github.com/Osasuwu/jarvis-oss/pull/62)
- commit: `8067d67`
- location: `docs/writing-into-user-owned-files.md:186-187`
- class: fact
- label: blocking
- held_out: no
- selection_source: model
- escape: round N `8067d67` → [round N+1 finding at `59a27d9`](https://github.com/Osasuwu/jarvis-oss/pull/62#issuecomment-5712324855)
- defect: git config exit status 5 applies "unless you pass `--replace-all`"; that flag covers set only, unset needs `--unset-all` or a value pattern.

### 62-today

- source_pr: [#62](https://github.com/Osasuwu/jarvis-oss/pull/62)
- commit: `8067d67`
- location: `docs/writing-into-user-owned-files.md:310`
- class: fact
- label: blocking
- held_out: no
- selection_source: model
- escape: round N `8067d67` → [round N+1 finding at `59a27d9`](https://github.com/Osasuwu/jarvis-oss/pull/62#issuecomment-5712324855)
- defect: "The skill today does 7 with show-before-writing and a plain append"; the jarvis-setup skill also offers an optional `@import` route (option 4).

### 62-bare

- source_pr: [#62](https://github.com/Osasuwu/jarvis-oss/pull/62)
- commit: `8067d67`
- location: `docs/writing-into-user-owned-files.md:153`
- class: fact
- label: blocking
- held_out: no
- selection_source: model
- escape: round N `8067d67` → [round N+1 finding at `59a27d9`](https://github.com/Osasuwu/jarvis-oss/pull/62#issuecomment-5712324855)
- defect: States that switching to bare import lines fixed the load failure; the linked example records that the confirming check was not run.

### 62-substr

- source_pr: [#62](https://github.com/Osasuwu/jarvis-oss/pull/62)
- commit: `59a27d9`
- location: `docs/writing-into-user-owned-files.md:77`
- class: fact
- label: blocking
- held_out: no
- selection_source: model
- escape: round N `59a27d9` → [round N+1 finding at `4074b0b`](https://github.com/Osasuwu/jarvis-oss/pull/62#issuecomment-5712717663)
- defect: "A substring guard does not recognise a changed line as yours", with nvm as the example; nvm's guard matches any line containing `/nvm.sh`, so it does.

### 62-equiv

- source_pr: [#62](https://github.com/Osasuwu/jarvis-oss/pull/62)
- commit: `4074b0b`
- location: `docs/writing-into-user-owned-files.md:128`
- class: fact
- label: blocking
- held_out: no
- selection_source: model
- escape: round N `4074b0b` → [round N+1 finding at `f677a54`](https://github.com/Osasuwu/jarvis-oss/pull/62#issuecomment-5712985215)
- defect: "if the person already has an equivalent line outside the block, the block adds a second one"; conda, the lead example, comments out earlier equivalent lines first.

### 62-subst

- source_pr: [#62](https://github.com/Osasuwu/jarvis-oss/pull/62)
- commit: `4074b0b`
- location: `docs/writing-into-user-owned-files.md:265`
- class: quote
- label: blocking
- held_out: no
- selection_source: model
- escape: round N `4074b0b` → [round N+1 finding at `f677a54`](https://github.com/Osasuwu/jarvis-oss/pull/62#issuecomment-5712985215)
- defect: "present in substance" is in quotation marks; the skill says "already states it in substance".

### 62-nvmq

- source_pr: [#62](https://github.com/Osasuwu/jarvis-oss/pull/62)
- commit: `f677a54`
- location: `docs/writing-into-user-owned-files.md:70`
- class: quote
- label: blocking
- held_out: no
- selection_source: model
- escape: round N `f677a54` → [round N+1 finding at `a6d0c01`](https://github.com/Osasuwu/jarvis-oss/pull/62#issuecomment-5713336602)
- defect: The quoted nvm message drops the leading `=> ` of the source string, so it is not verbatim.

### 62-persist

- source_pr: [#62](https://github.com/Osasuwu/jarvis-oss/pull/62)
- commit: `f677a54`
- location: `docs/writing-into-user-owned-files.md:257`
- class: quote
- label: blocking
- held_out: no
- selection_source: model
- escape: round N `f677a54` → [round N+1 finding at `a6d0c01`](https://github.com/Osasuwu/jarvis-oss/pull/62#issuecomment-5713336602)
- defect: "secrets never land in any persistent surface" differs in case from the quoted jarvis-setup SKILL.md line.

### 62-res

- source_pr: [#62](https://github.com/Osasuwu/jarvis-oss/pull/62)
- commit: `f677a54`
- location: `resources/jarvis-setup-skill.md:4`
- class: fact
- label: blocking
- held_out: no
- selection_source: model
- escape: round N `f677a54` → [round N+1 finding at `a6d0c01`](https://github.com/Osasuwu/jarvis-oss/pull/62#issuecomment-5713336602)
- defect: The cost line claims "a yes/no confirmation before the full write"; the skill asks one trial-or-full question at the start and has no confirmation before the write.

### 62-tried

- source_pr: [#62](https://github.com/Osasuwu/jarvis-oss/pull/62)
- commit: `a6d0c01`
- location: `docs/writing-into-user-owned-files.md:157`
- class: status
- label: blocking
- held_out: no
- selection_source: model
- escape: round N `a6d0c01` → [round N+1 finding at `9dbed9e`](https://github.com/Osasuwu/jarvis-oss/pull/62#issuecomment-5713920022)
- defect: A `tried:` run of `git config -f main.cfg --includes --get` against a missing include target has no trace in the repo; the behaviour is right, the status is not.

### 62-rustup

- source_pr: [#62](https://github.com/Osasuwu/jarvis-oss/pull/62)
- commit: `a6d0c01`
- location: `docs/writing-into-user-owned-files.md:139`
- class: fact
- label: blocking
- held_out: no
- selection_source: model
- escape: round N `a6d0c01` → [round N+1 finding at `9dbed9e`](https://github.com/Osasuwu/jarvis-oss/pull/62#issuecomment-5713920022)
- defect: Link target: the rustup claim links `shell.rs`, but the append it describes is in `unix.rs`.

### 62-systemd

- source_pr: [#62](https://github.com/Osasuwu/jarvis-oss/pull/62)
- commit: `a6d0c01`
- location: `docs/writing-into-user-owned-files.md:147`
- class: fact
- label: blocking
- held_out: no
- selection_source: model
- escape: round N `a6d0c01` → [round N+1 finding at `9dbed9e`](https://github.com/Osasuwu/jarvis-oss/pull/62#issuecomment-5713920022)
- defect: "systemd reads `.d/` files"; systemd merges only `.conf` files from a drop-in directory.

### 62-backup

- source_pr: [#62](https://github.com/Osasuwu/jarvis-oss/pull/62)
- commit: `a6d0c01`
- location: `docs/writing-into-user-owned-files.md:283`
- class: fact
- label: blocking
- held_out: no
- selection_source: model
- escape: round N `a6d0c01` → [round N+1 finding at `9dbed9e`](https://github.com/Osasuwu/jarvis-oss/pull/62#issuecomment-5713920022)
- defect: "keep a backup, as Ansible's `template` does"; the module's `backup` option is off by default.

### 62-npm

- source_pr: [#62](https://github.com/Osasuwu/jarvis-oss/pull/62)
- commit: `9dbed9e`
- location: `docs/writing-into-user-owned-files.md:192`
- class: fact
- label: blocking
- held_out: no
- selection_source: model
- escape: round N `9dbed9e` → [round N+1 finding at `8bc6712`](https://github.com/Osasuwu/jarvis-oss/pull/62#issuecomment-5714260724)
- defect: `npm pkg set` is offered as keeping formatting ("respect the existing indentation"); a re-run showed it rewrites untouched content, failing the row that requires formatting kept.

### 75-ledger

- source_pr: [#75](https://github.com/Osasuwu/jarvis-oss/pull/75)
- commit: `3d896f9`
- location: `resources/review-hold-and-signoff-ledger.md:4`
- class: fact
- label: blocking
- held_out: no
- selection_source: model
- escape: round N `3d896f9` → [round N+1 finding at `8238d21`](https://github.com/Osasuwu/jarvis-oss/pull/75#issuecomment-5726299527)
- defect: "one structure-gate run per pull request"; a workflow `on: pull_request:` with default types runs on every opened, synchronize and reopened event.

### 75-pro

- source_pr: [#75](https://github.com/Osasuwu/jarvis-oss/pull/75)
- commit: `3d896f9`
- location: `docs/doc-structure-gate.md:231-232`
- class: plan
- label: blocking
- held_out: no
- selection_source: model
- escape: round N `3d896f9` → [round N+1 finding at `8238d21`](https://github.com/Osasuwu/jarvis-oss/pull/75#issuecomment-5726299772)
- defect: "Paying for Pro, or making the repo public, turns this to yes"; Pro is a personal-account plan, so a private organisation repo on Free needs Team. Reader: a private repo owned by an organisation on Free.

### 75-reauth

- source_pr: [#75](https://github.com/Osasuwu/jarvis-oss/pull/75)
- commit: `3d896f9`
- location: `docs/publishing-discipline.md:249`
- class: plan
- label: blocking
- held_out: no
- selection_source: model
- escape: round N `3d896f9` → [round N+1 finding at `8238d21`](https://github.com/Osasuwu/jarvis-oss/pull/75#issuecomment-5726299527)
- defect: Row 5 lists "re-authentication" with no plan condition; the GitLab re-authentication approval setting needs a paid tier. Reader: GitLab Free.

### 75-mkdocs

- source_pr: [#75](https://github.com/Osasuwu/jarvis-oss/pull/75)
- commit: `3d896f9`
- location: `docs/doc-structure-gate.md:240-241`
- class: how-to-choose
- label: follow-up
- held_out: no
- selection_source: model
- escape: round N `3d896f9` → [round N+1 finding at `8238d21`](https://github.com/Osasuwu/jarvis-oss/pull/75#issuecomment-5726299772)
- defect: MkDocs' and Docusaurus' link checking "counts as 7", but both can fail a build on a front-matter rule (an MkDocs hook raising `PluginError`, Docusaurus `parseFrontMatter`), so option 5 is reachable. Setup: MkDocs with a review-date rule.

### 75-madr

- source_pr: [#75](https://github.com/Osasuwu/jarvis-oss/pull/75)
- commit: `3d896f9`
- location: `docs/doc-structure-gate.md:236-238`
- class: how-to-choose
- label: follow-up
- held_out: no
- selection_source: model
- escape: round N `3d896f9` → [round N+1 finding at `8238d21`](https://github.com/Osasuwu/jarvis-oss/pull/75#issuecomment-5726299772)
- defect: Step 3 offers the Structured MADR action for any docs with fixed headings; it checks MADR records only and runs only as a GitHub Action. Setup: runbooks on GitLab.

### 75-gs

- source_pr: [#75](https://github.com/Osasuwu/jarvis-oss/pull/75)
- commit: `3d896f9`
- location: `docs/doc-structure-gate.md:247`
- class: how-to-choose
- label: follow-up
- held_out: no
- selection_source: model
- escape: round N `3d896f9` → [round N+1 finding at `8238d21`](https://github.com/Osasuwu/jarvis-oss/pull/75#issuecomment-5726299772)
- defect: Row 3 sends owner and review-date key rules to option 3, but that option's built-in rule checks its own fixed field names only.

### 75-row1

- source_pr: [#75](https://github.com/Osasuwu/jarvis-oss/pull/75)
- commit: `3d896f9`
- location: `docs/doc-structure-gate.md:245`
- class: how-to-choose
- label: follow-up
- held_out: no
- selection_source: model
- escape: round N `3d896f9` → [round N+1 finding at `8238d21`](https://github.com/Osasuwu/jarvis-oss/pull/75#issuecomment-5726299772)
- defect: Row 1 requires "docs are few and one person reviews all of them"; option 1's own section says "or". Setup: a five-person team with one reviewer.

### 75-mail

- source_pr: [#75](https://github.com/Osasuwu/jarvis-oss/pull/75)
- commit: `3d896f9`
- location: `docs/publishing-discipline.md:268-271`
- class: how-to-choose
- label: follow-up
- held_out: no
- selection_source: model
- escape: round N `3d896f9` → [round N+1 finding at `8238d21`](https://github.com/Osasuwu/jarvis-oss/pull/75#issuecomment-5726299527)
- defect: Calls the mailing-list reply the proof, which conflicts with row 6 (it needs option 2, 3 or 5) and with the earlier section on what counts as proof.

### 75-pls1

- source_pr: [#75](https://github.com/Osasuwu/jarvis-oss/pull/75)
- commit: `3d896f9`
- location: `docs/private-literal-scrub.md:243-245`
- class: how-to-choose
- label: follow-up
- held_out: no
- selection_source: model
- escape: round N `3d896f9` → [round N+1 finding at `8238d21`](https://github.com/Osasuwu/jarvis-oss/pull/75#issuecomment-5726330075)
- defect: Step 1 and row 1 drop option 1's condition that the agent does not need the private strings for the public work. Setup: a CI agent that summarises private incident notes.

### 81-gitleaks

- source_pr: [#81](https://github.com/Osasuwu/jarvis-oss/pull/81)
- commit: `79bc90c`
- location: `docs/agent-safety-hooks.md:136-138`
- class: fact
- label: blocking
- held_out: no
- selection_source: model
- escape: round N `79bc90c` → [round N+1 finding at `af950ea`](https://github.com/Osasuwu/jarvis-oss/pull/81#issuecomment-5740465546)
- defect: Names `.gitleaks.toml` among "here" review-gate files outside the sandbox default; the repo has no `.gitleaks.toml`.

### 81-expires

- source_pr: [#81](https://github.com/Osasuwu/jarvis-oss/pull/81)
- commit: `79bc90c`
- location: `docs/agent-safety-hooks.md:178`
- class: fact
- label: blocking
- held_out: no
- selection_source: model
- escape: round N `79bc90c` → [round N+1 finding at `af950ea`](https://github.com/Osasuwu/jarvis-oss/pull/81#issuecomment-5740465546)
- defect: "Tokens multiply, and each expires"; the source allows tokens with no expiry.

### 81-openhands

- source_pr: [#81](https://github.com/Osasuwu/jarvis-oss/pull/81)
- commit: `79bc90c`
- location: `docs/agent-safety-hooks.md:245-247`
- class: fact
- label: blocking
- held_out: no
- selection_source: model
- escape: round N `79bc90c` → [round N+1 finding at `af950ea`](https://github.com/Osasuwu/jarvis-oss/pull/81#issuecomment-5740465546)
- defect: A classifier "approves or blocks" risky calls, citing OpenHands' security analyzer; OpenHands' page says its policy confirms rather than blocks.

### 81-res

- source_pr: [#81](https://github.com/Osasuwu/jarvis-oss/pull/81)
- commit: `79bc90c`
- location: `resources/agent-safety-hooks.md:66-70`
- class: fact
- label: blocking
- held_out: no
- selection_source: model
- escape: round N `79bc90c` → [round N+1 finding at `af950ea`](https://github.com/Osasuwu/jarvis-oss/pull/81#issuecomment-5740465546)
- defect: Says the four `CUSTOMIZE` constants ship as placeholders naming this repo's hook files and `.gitleaks.toml`; one ships empty and two list key formats and variable names instead.

### 81-files

- source_pr: [#81](https://github.com/Osasuwu/jarvis-oss/pull/81)
- commit: `af950ea`
- location: `docs/agent-safety-hooks.md:92-93`
- class: fact
- label: blocking
- held_out: no
- selection_source: model
- escape: round N `af950ea` → [round N+1 finding at `86ad376`](https://github.com/Osasuwu/jarvis-oss/pull/81#issuecomment-5740465682)
- defect: "Claude Code's rules reach commands that name a file"; the source covers recognised file commands and redirects only.

### 81-steer

- source_pr: [#81](https://github.com/Osasuwu/jarvis-oss/pull/81)
- commit: `af950ea`
- location: `docs/agent-safety-hooks.md:332`
- class: fact
- label: blocking
- held_out: no
- selection_source: model
- escape: round N `af950ea` → [round N+1 finding at `86ad376`](https://github.com/Osasuwu/jarvis-oss/pull/81#issuecomment-5740465682)
- defect: "a crafted input can steer it" is not in the linked post, and the vendor says tool results are stripped from classifier requests.

### 81-cost

- source_pr: [#81](https://github.com/Osasuwu/jarvis-oss/pull/81)
- commit: `af950ea`
- location: `docs/agent-safety-hooks.md:332-333`
- class: fact
- label: blocking
- held_out: no
- selection_source: model
- escape: round N `af950ea` → [round N+1 finding at `86ad376`](https://github.com/Osasuwu/jarvis-oss/pull/81#issuecomment-5740465682)
- defect: "Each call costs a model request"; reads and in-directory edits skip the classifier.

### 81-heredoc

- source_pr: [#81](https://github.com/Osasuwu/jarvis-oss/pull/81)
- commit: `af950ea`
- location: `examples/heredoc-stripping-boundary-bug.md:9-11`
- class: fact
- label: blocking
- held_out: no
- selection_source: model
- escape: round N `af950ea` → [round N+1 finding at `86ad376`](https://github.com/Osasuwu/jarvis-oss/pull/81#issuecomment-5740465682)
- defect: "a heredoc's content is text being written, not a command being executed"; false when the heredoc feeds `bash`, `sh`, `python` or `ssh` (reproduced).

## n per class

Held-out entries are not counted here.

| class | n |
|---|---|
| status | 1 |
| quote | 5 |
| plan | 2 |
| fact | 20 |
| dead-end | 0 |
| missing-option | 0 |
| how-to-choose | 6 |
| other | 0 |
| total | 34 |

By label: 28 `blocking`, 6 `follow-up`. By source PR: #62 17, #75 9, #81 8. Every entry has
selection source `model`. No human-found defect (`click-audit` or `reader`) exists yet.

## Held-out entries (n = 1)

Held-out entries are excluded from caught, missed and n above.

The call on 75-ghd has low confidence. PR #88 edits the write-doc skill, which review-doc's
`SKILL.md` links to, and its body names GHD012 and GHD063. Whether a linked skill counts as
"anything it loads" is a reading of the rule. The entry is held out because that is the reading
that cannot overstate the reviewer.

### 75-ghd

- source_pr: [#75](https://github.com/Osasuwu/jarvis-oss/pull/75)
- commit: `3d896f9`
- location: `docs/doc-structure-gate.md:166-167`
- class: quote
- label: blocking
- held_out: yes
- held_out_by: PR #88 (c10f7b2)
- selection_source: model
- escape: round N `3d896f9` → [round N+1 finding at `8238d21`](https://github.com/Osasuwu/jarvis-oss/pull/75#issuecomment-5726299772)
- defect: GHD012 and GHD063 are quoted as sentences; the source is a table row, so neither quote is verbatim.

## Excluded

These candidates were examined and left out. Each one fails "escaped" or fails to show a defect.

PR #62:

- Augeas lenses: the claim is true. Only the example formats are missing from the linked page.
- The legacy `git config` form: it is still valid, so the claim is not wrong.
- A hard-coded path claim: the text changed between the rounds, and it is private-derived.
- The four defects that `562fc30` names from round seven: "How to choose" was rewritten in
  `8bc6712`, so each was found in the first round that saw its text. They were caught, not missed,
  so `562fc30` holds nothing out.

PR #75:

- Astro `reference()`: the reviewer's own confidence was about 0.85, the build was not run, and the
  doc quotes Astro saying it errors. It was never shown to be wrong.
- Hugo `ref` / `relref`: these fail on an unresolved target by default, so the claim is not clearly
  wrong.
- "entries" read as non-blank lines: a reasonable reading.
- The environment-reviewer line, one proof-example item, one row-6 item and three private-literal-
  scrub items: a partner line the defect depends on was changed between the rounds.
- The "5 with an admin token" example: round 2 reported the same defect at step 2, so it was
  caught.
- Four items the reviewer marked "(low)", or judgement calls with no named reader: no reader is
  led into (i), (ii) or (iii), and none is shown wrong.
- Items that ask the reader to "read closely": judgements, not defects.
- The lychee preamble: flagged in round 2 and changed later.
- A `pull_request_target` claim: its source changed after the round.
- A missing "purge support" option: `missing-option`, see below.
- Fifteen further items: the defective text was introduced or changed after round N, so each
  counts against a later round, which caught it.

PR #81:

- `allowUnsandboxedCommands`, "Requires GitHub Secret Protection", table rows 377 and 378, and
  lines 42, 288 and 293: changed between the rounds.
- A resource note (lines 36-38): the block holds; only its message differs.
- A heredoc example line (32-33): too close to a round 1 `unverifiable` finding on the line
  above to count as a separate miss.
- Row 9 "can wait": a judgement, and the original finding text is not public.
- Round 3's findings at lines 161-162, 269-270 (with the linked example's run counts), 353-354,
  380-381 and 400-401: changed between the rounds.
- A docstring in a hook script: code, not doc text.

Other PRs:

- #63, #70, #71: no earlier round of the doc.
- #66, #90: pass-3-only runs, which are not full rounds.
- #68: its items were changed after the earlier round, or are 62-tried found again. The earliest
  find wins.
- #72, #74: checks run after merge, not review rounds.
- #73, #77: the text was never reviewed.
- #76: missing options only.
- #94: the round reported them as follow-ups, so they were caught.
- #115: the `NOT FOUND` items were found by a script, and `RULES.md` defines no selection source
  for a script find.

## Judgement notes

- **missing-option is 0 by method, not by absence.** Several rounds reported missing options.
  None became an entry: an absent option has no defective lines, so "text unchanged since round N"
  cannot be tested. Counting them would need a rule for what "unchanged" means for an absence.
  That is a change to `RULES.md`, which this corpus does not make.
- **how-to-choose entries are follow-ups.** Each names a setup, but none shows the reader led
  into (i), (ii) or (iii). They were screened only where the partner lines were also unchanged.
- **62-rustup** is `fact` because a link target is named in that class.
- **62-tried** was found again in PR #68. The earliest find, in PR #62, sets the source.
