# AI Hero skills — attribution

The following skills are imported from **Matt Pocock's** [`mattpocock/skills`](https://github.com/mattpocock/skills) repository (MIT, 2026).

**Last upstream sync:** SHA `d574778f94cf620fcc8ce741584093bc650a61d3` (2026-07-10, Pocock skills v1.1 / milestone #59 / #1156).

| Skill | Original path | Adaptation |
|---|---|---|
| `caveman` | `productivity/caveman` | as-is |
| `code-review` (injected) | `engineering/code-review` | Fowler-12 + 2-axis rubric adapted; injected into `/implement` and `/rework` pre-PR step — no standalone skill. PR plugin (`code-review.yml`) stays authoritative for blocking gates. |
| `diagnose` | `engineering/diagnose` | as-is |
| `grill` | `engineering/grill-with-docs` | renamed to `grill` (M37 — single grill skill, with-docs flavor; ADR 0001). Phase 2 sharpenings (M59): confirmation gate before accept, structured facts-vs-decisions output. Phase 1/2 reshape (#1413): dependency-gated frontier rounds replace the earlier one-at-a-time cadence; see "Partially adopted" below for the upstream `productivity/grilling` lineage of the frontier-round engine. |
| `improve-codebase-architecture` | `engineering/improve-codebase-architecture` | as-is (refs CONTEXT.md / docs/adr/ — create lazily) |
| `prototype` | `engineering/prototype` | throwaway→scratchpad semantics (never committed), `?variant=` query-param convention for showing UI alternatives side by side. Landed via #1154 (A7, milestone #59). |
| `_shared/tdd/` reference docs | `engineering/tdd` | unbundled (#593/#596): standalone `/tdd` skill dropped; `tdd-loop.md`, `mocking.md`, `refactoring.md`, `tests.md` migrated to `skills/_shared/tdd/` as reference docs loaded by `/implement` and `/delegate` in TDD-mode. `deep-modules.md` and `interface-design.md` folded into `CONTEXT.md` glossary (Deep module / Deletion test / Testable interface entries). Post-green refactor-out-of-loop re-sequence: **landed** via #1153 (A6, milestone #59, PR #1428) — `tdd-loop.md` §4 now runs Refactor as a standalone post-green pass, not interleaved per-test. |
| `to-spec` | `engineering/to-spec` | replaced `/setup-matt-pocock-skills` ref → "defined in the project's CLAUDE.md". Formerly `to-prd` (M37, renamed #1150). Test-seams step added (M59): surface test-harness insertion points during spec generation. |
| `to-tickets` | `engineering/to-tickets` | replaced `/setup-matt-pocock-skills` ref → "defined in the project's CLAUDE.md". Formerly `to-issues` (M37, renamed #1150). Expand-contract pattern for wide refactors added (M59): guidance for splitting broad changes into safe-to-deploy intermediate steps. |
| `triage` | `engineering/triage` | same |
| `wayfinder` | `engineering/wayfinder` | **landed (M59, #1404)**, superseding the earlier design-only decision `7085a34a`. Thin, read-only, multi-milestone frontier scanner — reuses `needs-*` labels + milestone hierarchy + native `blocked_by` edges instead of a new map artifact; explicit guardrail against the upstream #625/#518 failure mode (decision node mistaken for dispatchable work). |
| `wizard` | `engineering/wizard` | **landed (M59, #1405)**, decision `5e46bac3-df2a-4ede-86fe-3abf4f102f5b`. `template.sh` ported verbatim (204 lines, unmodified library); only the process description in `SKILL.md` adapted — jarvis's scratchpad convention referenced for the default script location, two concrete jarvis use cases named (`proxy-network-restore`, Supabase/VoyageAI credential setup). Upstream's `agents/openai.yaml` (an OpenAI-agent-platform UI descriptor) is not applicable to jarvis's Claude Code-native skill format and was not ported. |
| `write-a-skill` | `productivity/write-a-skill` | as-is (M37). Glossary + failure-mode taxonomy folded in from upstream `productivity/writing-great-skills` (M59). |
| `zoom-out` | `engineering/zoom-out` | as-is |

## Why imported

Pocock's philosophy aligns with Jarvis's: real engineering > vibe coding, evals as unit tests, smart zone (~100K tokens) discipline, vertical slices, deep modules, TDD as feedback loop. See [`docs/research-aihero-principles.md`](../../docs/research-aihero-principles.md) for full mapping.

His repo is explicitly designed for fork-and-adapt: *"These skills are designed to be small, easy to adapt, and composable. … Hack around with them. Make them your own."*

## Deliberately not adopted

The following upstream skills at `d574778` were reviewed and excluded from adoption. Their upstream paths are listed so future sync auditors can confirm they were intentionally skipped, not forgotten.

| Upstream skill | Reason not adopted |
|---|---|
| `engineering/ask-matt` | Jarvis routes through `/reason` + `/research` + dedicated skill triggers; a general-purpose router adds indirection with no clear gap. |
| `engineering/domain-modeling` | Domain modeling happens inside `/grill` + CONTEXT.md expansion; standalone skill would fragment the same workflow. |
| `engineering/research` | Jarvis `/research` already covers this — upstream adds no novel pattern. |
| `engineering/resolving-merge-conflicts` | Low-frequency, high-context task — not a skill candidate. Handled ad-hoc when it arises. |
| `productivity/grill-me` | Superseded by `/grill` (Phase 2 confirmation gate covers the same intent). |
| `in-progress/claude-handoff`, `productivity/handoff` | Single-principal project; no handoff between agents needed. |
| `in-progress/loop-me` | Jarvis uses `/loop` (scheduled tasks / CronCreate) — no need for a looping skill. |
| `engineering/setup-matt-pocock-skills` | Adoption managed centrally via `AIHERO_CREDIT.md` + GitHub issues; a per-repo bootstrap script is redundant. |
| `productivity/teach` | Jarvis has no teaching/training workflow. |

## Partially adopted

Upstream skills where part of the design was adopted and part deliberately wasn't — listed separately from "Deliberately not adopted" because a flat exclusion would misrepresent what actually shipped.

| Upstream skill | What was adopted | What wasn't |
|---|---|---|
| `productivity/grilling` | The frontier-round engine (dependency-gated rounds, weight tags, per-round display ceiling) — adopted into `/grill` Phase 2 via #1413. | The grilling-as-model-invocable-engine skill-split itself — still rejected as over-engineered for Jarvis's single-principal use case; `/grill` stays a monolithic skill. |

## Update workflow

When Matt updates a skill upstream:

```bash
gh repo clone mattpocock/skills /tmp/pocock-skills
diff -r /tmp/pocock-skills/skills/<category>/<name>/ \
        .claude-userlevel/skills/<name>/
```

Re-apply our adaptations on top.

## License

All upstream content is MIT (Copyright (c) 2026 Matt Pocock).

- **Full license text bundled at** [`THIRD_PARTY_LICENSES/aihero-skills-MIT.txt`](../../THIRD_PARTY_LICENSES/aihero-skills-MIT.txt) — local copy that travels with the redistributed code, per MIT's "include this permission notice in all copies" requirement.
- **Upstream source LICENSE:** https://github.com/mattpocock/skills/blob/main/LICENSE
