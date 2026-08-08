# Engineering principles (AI Hero / Matt Pocock)

Pull-only. Installed to `~/.claude/reference/engineering-principles.md`, **not** `@import`ed —
read it when a design call turns on one of these, not on every turn. Moved out of `SOUL.md`
by jarvis#1418; SOUL keeps only the one principle that must fire unprompted (*stay in the
smart zone*), because that one governs when to stop, and an agent past the smart zone is by
definition not going to pull a reference file.

Adopted 2026-04-30. Anti-vibe-coding posture: AI raised the stakes on fundamentals, didn't
lower them. The agent's output is bounded by the codebase's architecture and feedback loops —
garbage codebase → garbage AI output. Terms below (smart zone, Plan / Execute / Clear,
vertical slice, deep module, deletion test) are defined in jarvis `CONTEXT.md` → *Glossary*.

- **Real engineering > vibe coding.** Modularity, testability, clear interfaces. Don't let LLM speed substitute for engineering discipline.
- **Stay in the smart zone.** Past it reasoning quality drops — run the Plan / Execute / Clear rhythm, and review your own work in a fresh session, never the one that wrote the code. *(This one stays inline in SOUL.md.)*
- **Vertical slices, not horizontal.** Don't do "all schema, then all API, then all UI" — feedback arrives too late.
- **Deep modules, not shallow.** Before plowing a third tiny single-purpose file for one feature, ask whether it should be one deep module; settle it with the deletion test.
- **TDD as the feedback loop.** Red → green, one test → one impl at a time; refactor is a deliberate pass over the whole green suite, not a step inside each per-test cycle. Tests verify behavior through public interfaces — they're the agent's runtime ground truth; without them it flies blind.
- **Tight automated feedback loops.** Types, tests, linters, browser, scripts — anything that gives the agent ground truth without a human in the loop. Build the right loop before debugging hard bugs (`/diagnose` Phase 1).
- **Reach shared understanding before writing the plan.** PRD is an *input* for the next phase, not a human-readable artifact. The value is alignment between you and the agent (`/grill`).
- **Don't bite off more than you can chew.** Scope to what fits the smart zone. Decompose into independently-grabbable issues with explicit dependencies. Planning depth beats task ambition.
- **Treat agents like humans with no memory.** Strict, repo-level processes (skills, playbooks, glossaries) compensate. Vibes don't.
- **Refactor adjacent legacy when it makes the change cleaner AND tests cover the touched behavior.** Don't preserve broken-but-stable. Loss-aversion in the system prompt is a bug for codebases growing out of "vibe-coded" origins. If there's no test coverage for what you'd touch — write it (TDD-style), then refactor. If you can't write a test for it — that itself is a finding (flag it).

## Grill trigger checkbox (alignment protocol)

Implicit assumptions are the #1 source of scope shrinkage. Before starting any task —
30-second self-check:

- [ ] Does it touch user-visible behavior? (not cosmetic / refactor / doc-fix)
- [ ] Does it touch domain logic / algorithmics / physics? (not pipe wiring)
- [ ] Will tests be non-trivial? (need to decide what counts as "correct")
- [ ] Does the change cross existing non-trivial code?

**≥1 yes → run `/grill` BEFORE `/to-tickets` / `/implement`.** Do NOT skip on the basis of
"small task" — small tasks are exactly where assumption land mines hide.

**0 yes → proceed with normal flow** (`/implement` directly, or just edit).

The checkbox is delivered to the place it actually fires: `/implement` and `/delegate` both
restate it verbatim in their own dispatch contracts and refuse to proceed without a grill
artifact when it triggers. That restatement — not this file, and no longer SOUL.md — is the
enforcement surface.

**Output of `/grill`** lives in three places (not one):

1. **Acceptance criteria → issue body** (jarvis `CONTEXT.md` → *Acceptance criteria (AC)*)
2. **Domain insight → `CONTEXT.md`** (inline, no batching)
3. **Architectural decision → memory** via `record_decision` (with UUIDs in `memories_used`)
