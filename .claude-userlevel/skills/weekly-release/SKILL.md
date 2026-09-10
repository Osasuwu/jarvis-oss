---
name: weekly-release
description: "Draft a weekly GitHub release per config/repos.conf `releases=weekly` repo — readiness/semver/trust-ramp decided by scripts/weekly_release_engine.py, window+repos.conf/gh I/O by scripts/weekly_release_gather.py, release-note prose authored and fact-anchoring-linted inline. S1 (#1572): manual invocation, draft-only output — never auto-publishes. S2 (#1658): routine invocation on the Workshop host gated by config/device.json, plus a notify_text delivery step — still draft-only."
disable-model-invocation: true
---

# Weekly Release

Produces one draft GitHub release per [`config/repos.conf`](../../../config/repos.conf) repo carrying a `releases=weekly` token. S1 slice (#1572): the vertical to a first **draft**. Invoke by name (`/weekly-release`); it has no anchored chat trigger. S2 slice (#1658) adds routine scheduling (Step 0 device gate) and notification delivery (Step 5) — the draft-authoring steps below are otherwise unchanged from S1.

**Boundary — draft only, always.** Sending as the owner isn't autonomous until the "digital twin" pillar ships: this skill NEVER runs `gh release edit --draft=false` / publishes. Even when [`trust_ramp_state()`](../../../scripts/weekly_release_engine.py) returns `"auto"`, this skill still stops at a draft — trust-ramp-driven auto-publish is not this slice. The owner publishes manually; Step 5's notification exists precisely because the owner has to act on the draft themselves.

**Split.** Decision core (pure functions, no I/O) is [`scripts/weekly_release_engine.py`](../../../scripts/weekly_release_engine.py). I/O adapter (repos.conf + `gh` reads, no writes) is [`scripts/weekly_release_gather.py`](../../../scripts/weekly_release_gather.py). This skill is the only place that writes (`gh release create`/`edit --draft`, `notify_text`) — neither module touches `gh` write paths or notification delivery, mirroring the read/write split the rest of the gather/engine family uses.

## Step 0 — Routine-mode device gate (routine invocations only, #1658 AC1)

Manual invocation (the owner typing `/weekly-release`) skips this step entirely — it runs on any device, exactly as in S1. This gate applies only when the Workshop-registered weekly cron cold-boots this skill headlessly.

```bash
python -c "
import json
from scripts.weekly_release_engine import is_routine_host

with open('config/device.json') as f:
    device_config = json.load(f)

if not is_routine_host(device_config):
    print('refused: routines are host-only (config/device.json routine_host != true).')
    print('To designate this device as the routine host, set \"routine_host\": true in config/device.json.')
    raise SystemExit(1)
"
```

Same refusal pattern used elsewhere for routine-gated skills — decision `1b7ff8d1-bbca-4207-a7e4-4c1edddef67e`: one device is the sole routine host, every other device refuses rather than double-running the routine. Registering the routine itself (the cron entry on Workshop) is a manual step, not automated by this slice — see Step 6 below.

## Step 1 — Gather

No MCP tool is registered for this yet (same registration-ceiling situation the now-retired `mcp-morning` server used to hit) — call `gather()` in-process:

```bash
python -c "
import json
from scripts.weekly_release_gather import gather
print(json.dumps(gather().to_dict(), indent=2, default=str))
"
```

Returns a `WeeklyReleaseGatherResult`: one `RepoWindowResult` per `releases=weekly` repos.conf entry, each with `prior_releases` (most-recent-first, `published`/`edited_after_publish` flags), `window_entries` (merged PRs + standalone closed issues, deduped, Dependabot-flagged), `window_refs` (valid PR/issue numbers for the linter), `remaining_issues` (open milestone issues with movement), `remaining_refs` (those issues' own numbers, for linting `remaining_section` — see Step 3), `window_start`/`window_end`/`window_truncated`.

If `result.errors` is non-empty, surface it and stop for that repo — a failed gather must never fall through to an authored-from-nothing release.

## Step 2 — Per repo, decide

For each `RepoWindowResult`:

1. **Readiness** — `is_release_worthy(window_entries)`. `False` (dep-bumps/CI-fixes-only week, or empty window) → skip this repo, no release, no draft touched. State this explicitly in your final report ("o/r: no release — window was dep-bump/CI only").
2. **Semver bump** — `classify_bump(window_entries)` → `"patch"` or `"minor"`, never `"major"` (no breaking-change signal exists in any form; trust ramp is the only human gate — do not escalate on a `!` marker or `BREAKING CHANGE:` body yourself).
3. **Version string** — apply the bump to the latest tag from `prior_releases` (semver arithmetic is a skill-runtime step; no helper computes it, `weekly_release_engine.py` only classifies the bump). Zero prior releases → first version is a per-repo decision recorded when the repo was onboarded (e.g. redrobot: `v0.1.0`, target branch `master`, per issue #1572's own text — not a default this skill invents for a new repo).
4. **Trust ramp** — `trust_ramp_state(repo_result.prior_releases)`. Informational only in S1 (see Boundary above) — record it in the report; it does not change what this skill does.
5. **Draft-aware anchor** — `repo_result.window_truncated` is already computed by `compute_window()` inside `gather()`. `format_window_disclosure(repo_result.window_start, repo_result.window_end, repo_result.window_truncated)` returns the disclosure line when truncated, `""` otherwise — carry its result into Step 4's `disclosure_section`; don't hand-author this line, it's a structural formatter like `format_retraction_section` (#1668).
6. **Existing pending draft** — scan `prior_releases` for an entry with `published: False` (an unpublished draft). If one exists for this repo, you are **updating it in place** (`gh release edit`), not creating a second one — this is what "draft-aware anchor" means operationally, not just windowing.
7. **Retractions** — `repo_result.retractions` (#1659): already extracted by `gather()` from every merged PR in the window whose body carries a `Reverts #<M>` marker (`_reverted_pr_number()`), each entry `{"original_ref", "revert_ref", "title"}`. Empty list is the common case — most weeks have no reverts. Non-empty → this window discloses at least one retraction; carry the list into Step 3.

## Step 3 — Author the notes (agent-written prose, then linted)

Write `notes_body`: one line per substantive `window_entries` item, each citing its PR/issue number (`#NNN`) so `lint_release_notes` can validate it against `window_refs`. `lang=ru,en` on the repos.conf entry (`repo_result.lang`) → append an English `<details>` block translating the same cited facts, no new claims.

Write `remaining_section` as prose over `repo_result.remaining_issues` (LLM rewrites the open-milestone-issue list into prose; the issues themselves are the source of truth, this step only rewrites into readable text under a `## Осталось` heading — omit the heading entirely if there's nothing to say).

**Lint gate — mandatory, not advisory:**

```python
from scripts.weekly_release_engine import lint_release_notes
notes_violations = lint_release_notes(notes_body.splitlines(), repo_result.window_refs)
remaining_violations = lint_release_notes(
    remaining_section.splitlines(), repo_result.window_refs | repo_result.remaining_refs
)
violations = notes_violations + remaining_violations
```

`remaining_section` is linted against `window_refs | remaining_refs`, not `window_refs` alone: it describes `repo_result.remaining_issues` (open milestone issues), whose numbers are never in `window_refs` (built only from merged/closed activity, `weekly_release_gather.py`) — linting it against `window_refs` alone would fail every non-empty `remaining_section` by construction, since its own source-of-truth numbers could never validate. `remaining_refs` (one string set per `RepoWindowResult`, mirroring `window_refs`) exists precisely to make those citations valid — found via e2e testing against real redrobot data, no prior unit test exercised this combination.

Non-empty `violations` → rewrite the offending lines (add a real citation from the applicable ref set, or drop the uncited claim). Never bypass this by loosening a claim's wording to dodge the digit check — fix the citation or cut the claim.

**Retraction section — do not author, do not lint.** If `repo_result.retractions` is non-empty, call `format_retraction_section(repo_result.retractions)` — this is the *only* step for the "Отозвано" section. It is a structural formatter, not prose you write: each bullet is built from `original_ref`/`revert_ref`/`title` already sourced from a real PR body, so it is citation-correct by construction. Do **not** hand-write this section, and do **not** pass it (or its lines) through `lint_release_notes` in Step 3 above (AC2 of #1659: the section exists only when `retractions` is non-empty; an empty list means the formatter returns `""` and the section is omitted entirely, not emitted blank).

## Step 4 — Assemble and create the draft

```python
from scripts.weekly_release_engine import (
    assemble_release_body,
    format_retraction_section,
    format_window_disclosure,
)

retraction_section = format_retraction_section(repo_result.retractions)
disclosure_section = format_window_disclosure(
    repo_result.window_start, repo_result.window_end, repo_result.window_truncated
)
body = assemble_release_body(
    notes_body,
    remaining_section,
    full_changelog_url,
    footer="Опубликовано ботом",
    retraction_section=retraction_section,
    disclosure_section=disclosure_section,
)
```

`full_changelog_url` = `https://github.com/<repo>/compare/<last_tag>...<new_version>` (or omit the compare range for a repo's first-ever release — there is no prior tag to diff against).

Then, via `gh` (write step — the one thing this skill does that the gather module deliberately doesn't):

- **No existing pending draft** → `gh release create <version> --repo <owner/repo> --target <branch> --title <version> --draft --notes-file <path-to-body>`
- **Existing pending draft found in Step 2.6** → `gh release edit <existing-tag> --repo <owner/repo> --notes-file <path-to-body>` (update in place, do not create a second draft for the same window)

Report the draft URL(s) back to the owner. Do not publish. Do not send anything as the owner beyond creating the draft itself (the draft is a proposal artifact, not outbound communication).

## Step 5 — Notify (routine invocations only, #1658 AC2/AC3/AC4)

**KNOWN GAP (#1802):** `agents/notify.py` (the `notify_text`/`resolve_notifier` transport this step calls below) was demolished along with the rest of reactive-core in #1802 — it has no replacement yet. Until a replacement notification path lands, routine invocations cannot reach the owner via this step; fall back to reporting the draft URL(s) in chat output as if manually invoked, and flag the gap to the owner.

Manual invocation skips this step too — the draft URL(s) already reported in Step 4's chat output are the notification when the owner is present. Routine invocations have no one watching chat, so this step is what actually reaches the owner.

For each repo, `status` is `"draft"` when Step 4 created or updated a draft for it, or `"none"` when the repo was skipped in Step 2.1 (no release this week) — these are the only two cases that reach this step, and they must map to different `status` values, not the same one: `weekly_release_notification_for` treats any `status == "draft"` as "a draft exists, notify the owner," so passing `"draft"` for the skipped case would send a false notification for a non-existent draft. `"none"` (any value outside `{"draft", "published"}`) is what makes the function return `None` below. This skill never sets `status="published"` per the draft-only boundary above; that value exists in `weekly_release_notification_for` for a future publish-delivery slice, not this one.

```python
import os

from agents.notify import notify_text
from scripts.weekly_release_engine import weekly_release_notification_for

# repo, new_version, status come from Steps 2-4 above, per repo processed this run.
notification = weekly_release_notification_for(repo, new_version, status)
if notification is not None:
    subject, body = notification
    notify_text(subject, body, env=os.environ)
```

`weekly_release_notification_for` returns `None` for a no-release week (`status` outside `{"draft", "published"}`) — that `None` is the "stay silent" signal (AC3): skip `notify_text` entirely rather than sending an empty notification, so a quiet week doesn't become weekly spam. `notify_text` resolves the transport via `resolve_notifier(env)` and is a no-raise call — read `os.environ` once here, at this step's own boundary, and pass it down as `env`; no other code in this skill or in `weekly_release_engine.py`/`weekly_release_gather.py` reads `os.environ` directly (issue #1658's own requirement). Quiet-hours suppression (AC4, `NOTIFY_QUIET_HOURS`) is handled inside `notify_text` itself — nothing extra to do here.

## Step 6 — Register the weekly Routine (one-time, Workshop host only)

Not part of a normal `/weekly-release` invocation — run once to set up the routine (or again to re-register after a device migration), never on every draft run.

**Gate**: the same `is_routine_host(device_config)` check as Step 0, against the same `config/device.json`. This is deliberate defense-in-depth, not redundancy — Step 0 protects a routine invocation that somehow reaches the wrong device (a stale cron copied off-host) from actually running; this gate protects *registration itself* from happening on the wrong device in the first place. Refuse with the same message pattern as Step 0 if `routine_host` isn't `true` here.

**Registration surface**: the local `create_scheduled_task` MCP tool (`mcp__scheduled-tasks__create_scheduled_task`, Workshop-only per the gate above) — confirmed callable in-session. This is explicitly **not** the cloud `/schedule` Routines surface: that surface runs on Anthropic's hosted infrastructure with no access to this repo's local `gh` auth or `scripts/` modules, so it cannot execute this skill.

Register the row below, verbatim:

| Task ID | Cron | Prompt |
|---|---|---|
| weekly-release | `0 6 * * 0` | Run `/weekly-release` — draft this week's GitHub release for each `config/repos.conf` `releases=weekly` repo, then `notify_text` the owner per repo (#1658 S2). Registration is this table's own manual step — the skill's own Step 0 device gate (`is_routine_host`) is a defense-in-depth re-check, not a substitute for it. |

**Idempotent registration**: call `list_scheduled_tasks` first — present and `enabled=true` → skip (already registered); present and `enabled=false` → `update_scheduled_task(taskId, enabled=true)`; absent → `create_scheduled_task` with the cron + prompt above.

## Failure modes

- `gather()` returns `errors` for a repo → skip that repo, surface the error, do not author a release from an incomplete gather.
- `lint_release_notes` keeps failing after a rewrite attempt → stop for that repo and report the unresolved violation; never ship a release with an uncited quantitative claim.
- `gh release create`/`edit` fails (auth, branch not found, tag collision) → surface the `gh` error verbatim; do not retry with `--force` or improvise a different tag.
- Repo has `releases=weekly` but zero prior releases and no explicit first-version decision on record → do not guess a version number; ask the owner (this is exactly the redrobot `v0.1.0` case — settled once per repo, not invented per run).
