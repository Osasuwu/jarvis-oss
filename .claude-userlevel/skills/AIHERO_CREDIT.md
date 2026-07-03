# AI Hero skills — attribution

The following skills are imported from **Matt Pocock's** [`mattpocock/skills`](https://github.com/mattpocock/skills) repository (MIT, 2026).

**Last upstream sync:** SHA `733d312884b3878a9a9cff693c5886943753a741` (2026-05-10, M37 / #528).

| Skill | Original path | Adaptation |
|---|---|---|
| `caveman` | `productivity/caveman` | as-is |
| `diagnose` | `engineering/diagnose` | as-is |
| `grill` | `engineering/grill-with-docs` | renamed to `grill` (M37 — single grill skill, with-docs flavor; ADR 0001) |
| `improve-codebase-architecture` | `engineering/improve-codebase-architecture` | as-is (refs CONTEXT.md / docs/adr/ — create lazily) |
| `_shared/tdd/` reference docs | `engineering/tdd` | unbundled (#593/#596): standalone `/tdd` skill dropped; `tdd-loop.md`, `mocking.md`, `refactoring.md`, `tests.md` migrated to `skills/_shared/tdd/` as reference docs loaded by `/implement` and `/delegate` in TDD-mode. `deep-modules.md` and `interface-design.md` folded into `CONTEXT.md` glossary (Deep module / Deletion test / Testable interface entries) |
| `to-issues` | `engineering/to-issues` | replaced `/setup-matt-pocock-skills` ref → "defined in the project's CLAUDE.md" |
| `to-prd` | `engineering/to-prd` | same |
| `triage` | `engineering/triage` | same |
| `write-a-skill` | `productivity/write-a-skill` | as-is |
| `zoom-out` | `engineering/zoom-out` | as-is |

## Why imported

Pocock's philosophy aligns with Jarvis's: real engineering > vibe coding, evals as unit tests, smart zone (~100K tokens) discipline, vertical slices, deep modules, TDD as feedback loop. See [`docs/research-aihero-principles.md`](../../docs/research-aihero-principles.md) for full mapping.

His repo is explicitly designed for fork-and-adapt: *"These skills are designed to be small, easy to adapt, and composable. … Hack around with them. Make them your own."*

## Update workflow

When Matt updates a skill upstream:

```bash
gh repo clone mattpocock/skills /tmp/pocock-skills
diff -r /tmp/pocock-skills/skills/<category>/<name>/ \
        .claude-userlevel/skills/<name>/
```

Re-apply our `setup-matt-pocock-skills` adaptations on top.

## License

All upstream content is MIT (Copyright (c) 2026 Matt Pocock).

- **Full license text bundled at** [`THIRD_PARTY_LICENSES/aihero-skills-MIT.txt`](../../THIRD_PARTY_LICENSES/aihero-skills-MIT.txt) — local copy that travels with the redistributed code, per MIT's "include this permission notice in all copies" requirement.
- **Upstream source LICENSE:** https://github.com/mattpocock/skills/blob/main/LICENSE
