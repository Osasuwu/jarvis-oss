---
name: prototype
description: Build a cheap, rough, concrete artifact — outline, stub, or working UI/logic sketch — to raise the fidelity of a "how should this look/behave" discussion. Use when reacting to a mockup beats reacting to a description. Feeds /grill and (later) wayfinder prototype-type tickets. Triggers: "прототипируй", "накидай черновик", "покажи как это может выглядеть", /prototype.
---

# Prototype

Make something concrete to react to. When the open question is "how should X look or behave" rather than "should we build X," a rough artifact resolves ambiguity faster than another round of description — reacting to a draft is cheaper than agreeing on prose first.

Adapted from upstream Pocock v1.1's `prototype` skill (mattpocock/skills), with two Jarvis-specific changes: throwaway output location, and a query-param convention for UI variants (see Rules).

## When to use

- The discussion has stalled on shape or behavior, not on whether to build something.
- A quick, disposable artifact (a directory layout, a stubbed function, a rough UI) would let the user react concretely instead of imagining.
- **NOT** for external knowledge gathering (→ `/research`) or plan stress-testing (→ `/grill`) — prototype produces a throwaway artifact, not a sourced finding or a sharpened spec. It's the "make something to react to" step that can feed a later `/grill`, not a replacement for one.

## Rules

**Throwaway by construction.** Prototypes are never committed to the repo. Build them in the session scratchpad directory (the `Scratchpad Directory` path given in every session's Environment block). This mirrors the `docs/research/` hygiene norm (unfinished drafts, sanitize before ever considering committing) and the standing `git add -A` prohibition — a prototype that leaks into a real commit defeats the point of it being cheap and disposable.

**UI variants via query param, not branching files.** When showing alternative UI treatments side by side, use a single page/component with a `?variant=1`, `?variant=2`, ... query-param switch, not separate files per variant. Keeps the artifact singular and diffable while still letting the user compare options side by side.

**No skill-to-skill calls.** This skill produces an artifact and stops. It does not invoke `/grill` or `/reason` itself — the user (or a later turn) decides what happens with the artifact next. Skills stay independent and complementary, not chained.

## Process

1. Identify the concrete question — what specifically does the user need to react to? (a layout, an API shape, a rough algorithm, a data model)
2. Build the smallest artifact that resolves that question — not a full implementation, just enough fidelity to react to.
3. Save it under the scratchpad directory — never the repo tree.
4. For UI prototypes with multiple options: one file/page, `?variant=N` switch between them, not separate files.
5. Present it and ask what to keep, cut, or change. Stop there — don't proceed to real implementation without an explicit go-ahead.
