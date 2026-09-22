# Jarvis

[![Weekly quote check](https://github.com/Osasuwu/jarvis-oss/actions/workflows/quote-cron.yml/badge.svg)](https://github.com/Osasuwu/jarvis-oss/actions/workflows/quote-cron.yml)

> How one person runs AI coding agents day to day: docs, examples and resources, each with when it applies and when it does not.

> **2026-09-15:** `main` was rewritten from zero. Its history is unrelated to the old template. `git pull` on an old clone will fail. Clone fresh, or check out the `v0.7.0` tag for the template era.

## Install the setup skill

The setup skill lives in *this* repo but is meant to run in *yours*. From your own repo:

```bash
npx skills add Osasuwu/jarvis-oss --skill jarvis-setup
```

Or copy `.agents/skills/jarvis-setup/` by hand into your harness's skills directory — see the
Skills dir column of [`docs/harnesses.md`](docs/harnesses.md) for where that is on yours.

## Run the doc skills in this repo

`review-doc` and `write-doc` are for work on this repo's own docs. Claude Code finds project
skills in `.claude/skills/`, but the skills live in `.agents/skills/`, and `.claude/` is not
tracked. So link each one on your machine instead of copying it. A copy falls out of date, and
then the skill that runs is not the skill that was reviewed.

From the repo root, on macOS or Linux:

```bash
mkdir -p .claude/skills
ln -s "$PWD/.agents/skills/review-doc" .claude/skills/review-doc
```

`write-doc` is linked the same way: `.claude/skills/write-doc` to `.agents/skills/write-doc`.

On Windows, create `.claude/skills` first, then pick one of two:

- **A native symlink**, from Git Bash:
  `MSYS=winsymlinks:nativestrict ln -s "$PWD/.agents/skills/review-doc" .claude/skills/review-doc`.
  It needs Developer Mode on, or an elevated shell. Without `nativestrict`, Git Bash quietly makes
  a copy instead of a link, which is the problem this step exists to avoid.
- **A directory junction**, from `cmd`:
  `mklink /J .claude\skills\review-doc .agents\skills\review-doc`. It needs neither Developer
  Mode nor elevation, but it works only for a directory on a local volume.

Check the result with `ls -l .claude/skills` (Git Bash, macOS, Linux) or `dir .claude\skills`
(`cmd`): each entry should show as a link or a junction, not as a plain directory.

## Support

- Works on my machines. It may work on yours.
- Versions are content snapshots. There is no compatibility between them.
- Questions go to Discussions.
- Issues accept doc errors — a wrong fact, a bad quote, a broken link — through the
  [doc error template](.github/ISSUE_TEMPLATE/doc-error.yml). There is no turnaround promise.
- There is no update schedule.
- The badge at the top is a weekly check that every quote in the docs is still in its source. A
  grey badge means the check is off, not green: "In a public repository, scheduled workflows are
  automatically disabled when no repository activity has occurred in 60 days"
  ([GitHub Docs](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#schedule),
  checked 2026-09-22).
