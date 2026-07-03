# Shared protocol lives in global CLAUDE.md, not duplicated across skills

The memory recall protocol (`always_load` gates, skill-name-in-query, brief-mode UUIDs) and the `record_decision` contract (memories_used as UUIDs, alternatives_considered, post-hoc marker, etc.) live in user-level CLAUDE.md, loaded into every session. Skills do not restate them.

**Source of truth is in this repo at `.claude-userlevel/CLAUDE.md`**; the live file `~/.claude/CLAUDE.md` is a mirror, propagated by `install.ps1 -Apply` per the existing `install-manifest.yaml` pattern (same as SOUL.md, settings.json, skills). Never edit the mirror directly — edits drift on next install. Add a new `claude_md` group to the manifest as part of the migration.

This is the **Tier 1** layer. Two backstops exist:

- **Tier 2 — hooks.** Mechanical enforcement that can't be skipped. Example: `PreToolUse` on `record_decision` blocks calls with empty `memories_used`.
- **Tier 3 — skill-specific gates.** Things that genuinely belong to one skill — e.g. `/grill`'s completeness gate, `/implement`'s already-done audit. These stay in the skill file.

**Why this matters.** The previous arrangement smeared the protocol across 5+ skills with light variations, which was the dominant source of (a) drift between skills, (b) cognitive load when reading any one skill, and (c) the "all skills look the same" feeling that triggered this redesign. Centralising the protocol lets each skill be thin and distinct.

**Tracking.** Existing recall-audit aggregates (`empty_memories_used_pct`, `decision_text_no_recall`, `store_no_recall`) already roll across the last ~20 sessions. After migration, watch these. If the empty-`memories_used` rate rises, the Tier 1 prompt-rule isn't being honoured and we escalate the relevant rule into a Tier 2 hook.

## Considered options

- **Keep protocol in each skill.** Rejected — current state, source of the rewrite pressure.
- **Tier 2 only (hooks for everything).** Rejected — too brittle for soft rules; hooks are best for binary checks, not nuanced "recall before deciding" judgement.
- **Move to SOUL.md instead of CLAUDE.md.** Rejected — SOUL.md is identity/personality; CLAUDE.md is rules/process. Memory protocol is a process.

## Consequences

- Skills shrink. `/implement`, `/delegate`, `/grill`, `/to-prd`, `/to-issues` lose their memory/decision sections.
- Global CLAUDE.md grows. The canonical file lives at `.claude-userlevel/CLAUDE.md` in this repo (PR-reviewed, git-versioned). Cross-device sync uses the existing installer flow — no new mechanism.
- New skills inherit the protocol for free. Authoring a skill no longer requires copy-pasting the recall preamble.
