# DOCTRINE.md — shared cross-repo norms

Loaded via `@import` from user-level `CLAUDE.md`. Subagents (Task tool) and headless `claude -p` both load `~/.claude/` by default — this file reaches them too. **Carve-out**: only cloud/scheduled task runners (fresh clone, no local file access), the built-in `Explore`/`Plan` subagent types, and the `--bare` flag skip the user-level directory — this file is interactive-session guidance there, not an enforcement mechanism; that lives in each repo's own `.github/workflows/code-review.yml` and `scripts/rework_policy.py`.

Every line clears the always-loaded bar (≥30% of tasks, decision `ff994ca2`). A repo's own tooling internals, issue history, or incident logs stay in that repo's `CONTEXT.md`, cited as e.g. "jarvis `CONTEXT.md` → *X*" — a qualified pointer fails visibly instead of misdirecting a different repo's session.

## Protocol layers

Durable behavior rules live in three tiers, each a backstop for the one above:

- **Tier 1 — durable prompt rules.** User-level `CLAUDE.md`, loaded every session. Default home for cross-skill rules — soft, judgment-based.
- **Tier 2 — mechanical hooks.** `PreToolUse`/`PostToolUse` deterministic fences for binary checks a prompt rule might be skipped on.
- **Tier 3 — skill-specific gates.** Belong to one skill; never duplicate Tier 1 content.

A rule escalates Tier 1 → Tier 2 when soft enforcement measurably fails (e.g. compliance dropping despite the rule existing) — the escalation call and its tracking are repo-specific.

## Baseline carrier selection

When a behavioral baseline needs a home (a rule, a fact, a constraint that should reliably reach the agent), the carrier is picked by **cost of the rule being violated**, not by how important the rule feels. Importance without a violation-cost story is how everything ends up `always_load` — the tag decays into a junk drawer instead of a scarce resource. The per-carrier delivery/compliance/token-cost table that grounds this order lives in `~/.claude/reference/baseline-carriers.md` — read it when placing a new baseline or arguing for a more expensive carrier than the order gives.

Selection order — pick the first that fits:

1. **Checkable at the tool-call boundary** → PreToolUse deny hook.
2. **Checkable on the produced artifact** → test / CI gate.
3. **Not mechanically checkable, always needed, stable** → a file loaded via `@import` (this file or CLAUDE.md).
4. **Not checkable, but scoped to a code area** → `.claude/rules/` + `paths:` filter.
5. **Not checkable, situational** → retrieval (`memory_recall`), never a baseline.
6. **`always_load` only** for content that is dynamic, device-scoped, or time-bounded with no natural file home (e.g. an active incident, a device-specific gotcha) — almost nothing else qualifies. If a memory has settled into something stable and general, it belongs in a file, not the tag.

See *`always_load` admission criterion* below for the cap this order motivates.

## `always_load` admission criterion

`always_load` is the worst-position, always-pays carrier — last in the order above, reserved for content that is dynamic, device-scoped, or time-bounded with no natural file home (rule 6). It is not a general-purpose "important, so tag it" bucket; unchecked growth degrades every session's prompt with lost-in-middle content that pays tokens on every turn regardless of relevance.

**Cap: 4 entries, 6000 bytes combined**, enforced at *read time* (not write time — tagging isn't blocked, but only the most recently updated entries within budget are actually injected) in both `scripts/session-context.py` (`_query_always_load`) and `scripts/eval-recall.py` (`_load_session_context`'s always_load block). Over-cap data degrades gracefully — truncated to the freshest entries/bytes, with a loud stderr warning — rather than failing the session-start hook outright.

Before tagging a memory `always_load`, walk rule 6 above: if the content has settled into something stable and general, it belongs in a file (this file, CLAUDE.md, a repo's CONTEXT.md), not the tag. Untag once the content is ported.

**Changing the cap** requires a new `record_decision` that cites the prior cap's decision UUID in `memories_used`, and the new UUID must be cited in-code at both enforcement points. Current cap set by decision `3e6594f6-27da-45d9-96d8-516a46716425` (#1252 AC3).

## status:owner-queue label

A label any owned repo can apply meaning "park this for me — not autonomous-safe right now." On a PR it fails the repo's `owner-queue-guard` CI check, making the label a hard merge block, not just an FYI. Cleared by whatever resolves the reason it was applied. Distinct from `status:ready`/`status:in-progress` (claim state) — this tracks "needs a human before automation resumes."

## Review-blind carve-out

Some PRs are structurally unreviewable by the code-review bot — most commonly one that edits the review workflow itself. Mark such a PR review-blind rather than treating a missing review as a passed one. "Gate cannot run" is not "gate ran and raised no objection" — only the former grounds the admin-merge carve-out below.

## Sanctioned stop-gap merge

Admin-merge around a gate is normalized in exactly two cases: (a) the review-blind carve-out above, or (b) a *false-failing* gate (bug in the gate, not the PR) — valid once, with a tracking issue for the root cause linked in the merge comment. A second admin-merge around the same gate means stop and fix the gate, not normalize the bypass. Never for a gate that's merely inconvenient or slow.

## Merge-freeze doctrine

Symmetric partner to the rule above: a **known fail-OPEN bug** in a required check — silently passing PRs it should block — must **suspend auto-merge (freeze) for that check-class** until fixed, not just get a tracking issue while merges keep shipping past the hole. A false-failing gate blocks you, so it's noticed; a false-passing gate is silently relied on by every merge meanwhile — "file an issue and move on" isn't enough. Freeze-then-fix is the fail-open analog of fix-don't-bypass.
