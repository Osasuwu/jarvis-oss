# Merge gates — full reference

Pull-only. Installed to `~/.claude/reference/merge-gates.md`, **not** `@import`ed — read it
when you are changing a gate, adding a repo, or diagnosing why a PR won't merge. Moved out of
user-level `CLAUDE.md` by jarvis#1418: the gates are enforced by branch protection (carrier 1),
so the prose describing them is explanatory, not load-bearing. `CLAUDE.md` keeps only the part
an agent must act on without reading anything — drafts, the owner-queue label, and the
private-repo manual-merge caveat.

Applies to every owned repo (this repo, any second-owner repo, and any future
personal project). Foreign-owner repos are exempt — they have their own protection rules.

**Goal:** AFK Path A loop closes by itself — `open → CI → review → automerge → rework →
escalate`. Subagent opens a PR, Jarvis flips it to ready, GitHub merges when every gate is
green. No human in the merge step *unless* a gate fires.

## The four gates

Every owned repo enforces the same set via **branch protection on the default branch** +
repo-level `allow_auto_merge=true`:

1. **`review` (Claude code-review plugin)** — the workflow runs `/code-review` and posts
   findings as a structured comment; the `Verify review verdict` post-step turns that comment
   into a pass/fail verdict, so the check signals "PR is clean", not merely "bot ran".
   Blocking set is all-caps `CRITICAL`/`MAJOR`/`BLOCKING`/`MEDIUM` (MEDIUM promoted from
   advisory, #1385 follow-up — a MEDIUM finding can genuinely corrupt state in the moment);
   `MINOR`/`NITPICK`/`LOW`/`INFO` stay non-blocking. Full semantics + rationale: jarvis
   `CONTEXT.md` → *Merge-gate vocabulary (code-review)*, *Fail-closed verdict parsing*.
2. **`owner-queue-guard`** — fails the job when the PR carries the `status:owner-queue` label
   (DOCTRINE.md → *status:owner-queue label*), turning that "park this for me" signal into a
   hard merge block. Triggered on `opened / synchronize / labeled / unlabeled` so label
   changes re-evaluate the gate.
3. **`require-linked-issue`** — PR body must reference `Closes #NNN`, OR carry the
   `priority:critical` label (hotfix bypass), OR contain the `[no-issue]` marker (drive-by
   fix-inline per jarvis#428), OR use a `refactor:` / `refactor(scope):` title prefix.
4. **Project-specific test gates** — `pytest`, `meta-tests`, `Detect secrets with gitleaks`
   in jarvis; the equivalents in any other repo. These come from the repo's own CI surface.

## Required files per repo

- `.github/workflows/code-review.yml` — carries the `Verify review verdict` post-step. Which
  heading shapes block, which pass, and the fail-closed floor are pinned by
  `tests/ci/test_code_review_verdict_guard.py` (jarvis) and explained in jarvis `CONTEXT.md` →
  *Merge-gate vocabulary (code-review)*. Don't restate the parsing rules anywhere else —
  change the workflow and its test together.
- `.github/workflows/owner-queue-guard.yml` — single job named `owner-queue-guard`, triggers
  on `opened, synchronize, labeled, unlabeled`, fails on the label.

The check name `owner-queue-guard` is what branch protection references — rename in lockstep
with the protection rule or the gate silently disappears (per the path-filtered-guard
meta-test rule, jarvis#326).

## Repo settings

Auto-merge, `delete_branch_on_merge`, branch protection and the required-check context list
are **applied from the per-repo manifest by repo-baseline**, not by hand — jarvis `CONTEXT.md`
→ *repo-baseline*, *Axis* (`auto_merge`, `branch_protection`, `required_check_contexts[]`).
Two values are load-bearing: `enforce_admins=false` keeps the escape hatch open for the two
structural cases below, and `required_pull_request_reviews=null` because the `review` check
already encodes the AI verdict — a required human review would defeat AFK Path A.

## When to break the rules

Two structural cases where a gate *cannot* run and admin-merge is the only path — the
**review-blind carve-out**: (a) a PR that modifies `code-review.yml` itself; (b) redrobot's
self-hosted runner being down (verify locally, per the
`redrobot_billing_blocked_manual_merge_protocol` precedent).

**A flaky or false-failing gate is NOT on this list** — that's a bug to fix, not a bypass to
normalize: file an issue and take at most one **sanctioned stop-gap merge**. Definitions:
DOCTRINE.md → *Review-blind carve-out*, *Sanctioned stop-gap merge*, *Merge-freeze doctrine*.
