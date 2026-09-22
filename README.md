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
