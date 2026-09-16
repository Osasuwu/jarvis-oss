---
applies_when: adopting Jarvis in a repo that already has some rules-file content
applies_when_not: repo has no rules file at all
signed_off: 2026-09-16
---

# Setup writes only the delta

## Problem

A one-time setup skill that touches a reader's rules file (`CLAUDE.md`, `AGENTS.md`, ...) can
fail in two different directions: overwrite what the reader already wrote, or duplicate its own
output on every re-run.

## Options tried, and why dropped

- **Full overwrite** — regenerate the whole rules file from a template on every run. Dropped:
  destroys any prior customization the reader already has in the file. Evidence: the skill's own
  stated contract, "It never overwrites what is already there" —
  [`.agents/skills/jarvis-setup/SKILL.md`](../.agents/skills/jarvis-setup/SKILL.md).
- **Blind append** — always add the Jarvis section at the end, unconditionally. Dropped:
  duplicates the section on every re-run, since nothing checks what is already present. Evidence:
  the skill instead computes what is missing before writing anything — see §3, "Compute the
  delta" — [`.agents/skills/jarvis-setup/SKILL.md`](../.agents/skills/jarvis-setup/SKILL.md).

## What settled

Read the existing rules file, judge each of the three required items (persona, autonomy tier,
the two invariants) for presence *in substance* — same commitment, any wording — and append only
what is missing. Evidence: `.agents/skills/jarvis-setup/SKILL.md` §3, "Compute the delta".

## When this applies

- Applies whenever adopting Jarvis in a repo that already has a rules file for its harness —
  any harness listed in [`harnesses.md`](harnesses.md), since the delta-computation step is
  harness-agnostic.
- Does not apply to a genuinely empty repo: there is nothing to diff against there, so the delta
  is simply all three items written as a complete new file. Evidence: `.agents/skills/jarvis-setup/SKILL.md`
  §4, "Empty repo".

## Worked example

See [`setup-delta-run.md`](../examples/setup-delta-run.md) for a concrete before/after run.
