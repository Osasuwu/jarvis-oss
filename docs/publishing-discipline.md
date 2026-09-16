---
applies_when: publishing agent-drafted docs/examples/resources that must clear a human review point before shipping
applies_when_not: internal process notes that are never meant to leave draft status
signed_off: 2026-09-16
---

# Publishing discipline: gate what agents draft, not what they write

## Problem

Agents draft every doc, example, and resource in this repo (D9). Nothing stops an agent from
drafting well and shipping badly: a boundary clause with no evidence behind it, a private literal
that leaks into a public file, or a "reviewed" claim that nobody but the drafting agent ever made.
The publishing step needs to catch all three without requiring a human to re-read every file by
hand on every PR.

## Options tried, and why dropped

- **Sign-off tracked by commit author** — trust `git log`'s author field to tell drafted-by-agent
  apart from reviewed-by-human. Dropped: agent commits land under the operator's own account, so
  authorship alone can't distinguish a draft from a review. Evidence: [`docs/SIGNOFF.md`](SIGNOFF.md),
  "Sign-off provenance can't be verified via commit author".
- **Manual literal grep before push** — a human skims the diff for anything private before
  merging. Dropped: inconsistent, and it re-leaks the matched value into whatever log records the
  grep. Evidence: [`scripts/scrub_personal_literals.py`](../scripts/scrub_personal_literals.py)
  runs the check in CI instead, and only ever prints the file path of a hit, never the literal.
- **Ship every draft, gate nothing** — agents draft, everything merges once tests pass. Dropped:
  D15 restricts what ships to what the author has actually signed off on; a green test suite is
  not the same claim as "the author read this." Evidence: [`docs/SIGNOFF.md`](SIGNOFF.md)'s two-way
  ledger exists specifically because a single `signed_off` frontmatter field, unchecked against
  anything else, is a claim an agent could make about its own draft.

## What settled

Three checks, each catching a different failure:

1. **Frontmatter + link contract** — every doc under `docs/` carries `applies_when`,
   `applies_when_not`, and `signed_off`; every resource carries `pairs_with`, `harnesses`, `cost`;
   every example carries `fit` plus either `last_seen` (own) or `source`+`verified` (external).
   Every non-external link in a doc must resolve to a real file — an evidence pointer that goes
   nowhere fails the same way a missing frontmatter key does. Evidence:
   [`tests/structure_gate.py`](../tests/structure_gate.py), `DOC_REQUIRED_KEYS`,
   `RESOURCE_REQUIRED_KEYS`, and `boundary_evidence_unresolvable`.
2. **Two-way sign-off ledger** — a doc's own `signed_off` date must match an entry in
   [`docs/SIGNOFF.md`](SIGNOFF.md), and that entry must land in a commit *separate* from the one
   that last changed the doc body. Evidence: `tests/structure_gate.py`'s `signoff_missing_entry`
   and `signoff_same_commit` checks; see [`signoff-same-commit-violation.md`](../examples/signoff-same-commit-violation.md)
   for a real instance of the second one.
3. **CI-enforced literal scrub** — every PR is scanned for any string in the operator's private
   `PERSONAL_LITERALS` list; a hit fails the check and names only the file, never the value.
   Evidence: [`scripts/scrub_personal_literals.py`](../scripts/scrub_personal_literals.py), wired
   into [`.github/workflows/gitleaks.yml`](../.github/workflows/gitleaks.yml).

## At one developer, and at N

At one developer, the same account drafts the doc and adds the ledger line — the two-commit split
isn't a different reviewer, it's the operator choosing to look before merging the PR at all. That
is the honest amount of "review" a solo repo can have, which is why `docs/SIGNOFF.md` says
provenance can't be verified from authorship in the first place.

At N>1, the mechanism doesn't change — same frontmatter, same ledger, same separate-commit rule —
but what changes is who is allowed to make the ledger-entry commit: a teammate other than the
doc's drafter, not the drafter themself. That turns the split from a self-hold into a real
requested review, the same shift the merge-gates layer makes for its owner-shaped hold (parent
issue [#38](https://github.com/Osasuwu/jarvis-oss/issues/38)'s cross-cutting constraint, citing
its own #44 as the live case).

## When this applies

- Applies whenever a repo ships agent-drafted content that must clear a human review point before
  it's published, and the drafting account and the reviewing human aren't distinguishable from git
  metadata alone. Evidence: [`docs/SIGNOFF.md`](SIGNOFF.md)'s stated reason for the two-way ledger.
- Does not apply to content that is never meant to leave draft/internal status — a private
  operator repo with no publishing step has no structure gate to satisfy. Evidence: this repo's
  own `~/.claude/CLAUDE.md` stub points at a private operator repo that carries no equivalent of
  `docs/SIGNOFF.md` or a structure gate.

## Worked example

See [`signoff-same-commit-violation.md`](../examples/signoff-same-commit-violation.md) for a real
`signoff_same_commit` violation this repo hit on `main`, and the two-commit fix.

## Machinery

The CI wiring behind this doc: [`publishing-discipline-ci.md`](../resources/publishing-discipline-ci.md).
