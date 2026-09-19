---
fit: works when you run a label-based review hold with one account shared by a person and an agent, and want to see what the event log can and cannot tell you afterwards
last_seen: 2026-09-17
pairs_with: docs/publishing-discipline.md
---

# PR #70: a review hold cleared by the account the agent uses

This repo holds every pull request with a `waiting-human-review` label and a required check of the
same name that fails while the label is on
([workflow](../.github/workflows/waiting-human-review.yml)). A person removes the label after
reading.

[PR #70](https://github.com/Osasuwu/jarvis-oss/pull/70)'s timeline:

| Time (UTC, 2026-09-17) | Event | Actor |
|---|---|---|
| 14:36:57 | `waiting-human-review` label added | `github-actions[bot]` |
| 14:37:58 | label removed | `Osasuwu` |
| 14:42:34 | merged | `Osasuwu` |

The label was off 61 seconds after it went on. The pull request had no reviews.

**What cannot be told.** `Osasuwu` is both the person's account and the account whose token the
agent uses for `gh`. The timeline records the account, not whether a person clicked "remove label"
after reading or the agent called the API. Sixty-one seconds is short for reading a docs change,
but not impossible for a small one — the record supports neither reading.

**What it shows.** A hold that the agent's credential can clear is a request, not a barrier: it
works as long as the agent follows the rule not to clear it. To make the timeline mean something,
the agent needs its own identity, or clearing needs something only the person has — options 3 and
5 of [`publishing-discipline.md`](../docs/publishing-discipline.md).
