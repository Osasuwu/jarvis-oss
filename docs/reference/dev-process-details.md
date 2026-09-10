# Development process details

Pull-only detail for CLAUDE.md → *Development process*.

## Design RFC / proposal / debate

Goes to **GitHub Discussions, not an issue and not a PR.** Approval = thread resolution by the task initiator (user if user-started; orchestrator/PM if agent-started). Stable post-decision artifacts may land in `docs/design/` via direct commit; no PR ceremony.

## Fix > track for trivial reversible (#428)

Trivial, reversible, scope-obvious change (<30 min, own repo): **fix inline**. Don't open a tracking issue you'll close in 5 minutes — that's paperwork. Issues are for things you can't finish now, want to discuss, or that will outlive this session.

- **Fix inline**: stale doc fragment (broken link, version mismatch); missing test for newly-touched code; typo/comment cleanup adjacent to other work; config drift between two files; lint warning on a file you just touched.
- **Open issue**: architectural reshape >1h; cross-cutting refactor needing coordination; behavior change user should weigh in on; anything touching another active area mid-flight; foreign-owner repo where Jarvis can't merge.

The `Fix > track` rule does **not** override the rest of the development process — fixes still go through PR review, with the `[no-issue]` commit-msg marker.

## Checking the code-review verdict before merging

The Claude code-review bot reviews every PR (via `code-review.yml`). It posts as an
**issue-comment**, not a PR review, so it does NOT appear in the Reviews tab:

```bash
gh api --paginate repos/Osasuwu/jarvis/issues/NUMBER/comments
```

Use `--paginate` so a comment past the first page isn't missed. Address valid findings with code
changes, or explain why no change is needed — don't assume the Reviews tab is the only place
feedback lands.

## Linking a sub-issue to a parent epic via API

See [`.github/github-process-runbook.md`](../../.github/github-process-runbook.md) → *5. Parent/Epic
Rules* for the `gh api .../sub_issues` call and the `-F` (not `-f`) integer-ID gotcha — not
duplicated here to avoid the two drifting apart.

## Deliberate simplifications carry a `ceiling:` marker

When you knowingly ship a shortcut with a known limit — global lock, O(n²) scan over a list
assumed small, naive heuristic, hardcoded single-device path — leave an inline `ceiling:` comment
naming *both* the limit and the upgrade path:
`# ceiling: O(n²) over labels, fine <200; switch to a set-diff if a repo crosses that`.

Not for ordinary "could be prettier" code — only for a corner cut against a limit you can name.
This is the cheap end of "tech debt must be visible" (`~/.claude/SOUL.md` → *Judgment
calibration*): `grep -rn 'ceiling:'` is the debt list, so a shortcut no longer needs an issue to
stay visible. An unnamed limit means you don't understand the shortcut well enough to ship it.

## No state in static storage

State (% done, ✅/❌ markers, "shipped in PR #X", sprint dates, "last audit YYYY-MM-DD") belongs
in GitHub Issues/Projects/PRs/commit history — not in markdown files, not in memory. Static
storage (this repo's `.md` files, `docs/**`) may hold: evergreen lessons, decisions + rationale,
reference info (API shapes, config locations), target architecture, pointers ("see #633 for
current status"). If a field would be wrong in two weeks, it belongs in GitHub, not here.

## Skills live in `.claude-userlevel/skills/`

That directory is the canonical location (rare project overrides aside); `~/.claude/skills/` is
a manually-wired mirror. The scripted installer that used to sync `.claude-userlevel/` into
`~/.claude/` was retired in #1800, so there's nothing left to revert a direct edit — but
`~/.claude/skills/` is still not the source of truth: edit
`.claude-userlevel/skills/<name>/SKILL.md`, get it reviewed and merged, then manually copy/link
the updated file into `~/.claude/skills/<name>/SKILL.md` on each device (see
[`docs/setup.md`](../setup.md)).

## Other pointers

- Decisions belong in the queryable memory store, not a markdown file (#1274).
- Path-filtered CI guards require a meta-test (#326): [`tests/ci/test_guard_test_convention.py`](../../tests/ci/test_guard_test_convention.py), detail in [`docs/reference/ci-guard-meta-tests.md`](ci-guard-meta-tests.md).
