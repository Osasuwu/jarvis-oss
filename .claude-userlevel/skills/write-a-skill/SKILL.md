---
name: write-a-skill
description: Create new agent skills with proper structure, progressive disclosure, and bundled resources. Use when user wants to create, write, or build a new skill.
---

# Writing Skills

## Process

1. **Gather requirements** - ask user about:
   - What task/domain does the skill cover?
   - What specific use cases should it handle?
   - Does it need executable scripts or just instructions?
   - Any reference materials to include?

2. **Draft the skill** - create:
   - SKILL.md with concise instructions
   - Additional reference files if content exceeds 500 lines
   - Utility scripts if deterministic operations needed

3. **Review with user** - present draft and ask:
   - Does this cover your use cases?
   - Anything missing or unclear?
   - Should any section be more/less detailed?

## Skill Structure

```
skill-name/
├── SKILL.md           # Main instructions (required)
├── REFERENCE.md       # Detailed docs (if needed)
├── EXAMPLES.md        # Usage examples (if needed)
└── scripts/           # Utility scripts (if needed)
    └── helper.js
```

## SKILL.md Template

```md
---
name: skill-name
description: Brief description of capability. Use when [specific triggers].
---

# Skill Name

## Quick start

[Minimal working example]

## Workflows

[Step-by-step processes with checklists for complex tasks]

## Advanced features

[Link to separate files: See [REFERENCE.md](REFERENCE.md)]
```

## Description Requirements

The description is **the only thing your agent sees** when deciding which skill to load. It's surfaced in the system prompt alongside all other installed skills. Your agent reads these descriptions and picks the relevant skill based on the user's request.

**Goal**: Give your agent just enough info to know:

1. What capability this skill provides
2. When/why to trigger it (specific keywords, contexts, file types)

**Format**:

- Max 1024 chars
- Write in third person
- First sentence: what it does
- Second sentence: "Use when [specific triggers]"

**Good example**:

```
Extract text and tables from PDF files, fill forms, merge documents. Use when working with PDF files or when user mentions PDFs, forms, or document extraction.
```

**Bad example**:

```
Helps with documents.
```

The bad example gives your agent no way to distinguish this from other document skills.

## When to Add Scripts

Add utility scripts when:

- Operation is deterministic (validation, formatting)
- Same code would be generated repeatedly
- Errors need explicit handling

Scripts save tokens and improve reliability vs generated code.

## When to Split Files

Consider splitting into separate files when any of the below applies — these are heuristics, not hard limits:

- SKILL.md grows past ~100 lines AND the extra content is reference material or examples (not core flow)
- Content has distinct domains (finance vs sales schemas)
- Advanced features are rarely needed and would hide the common path

A meta-skill (like this one — `write-a-skill`) that *is itself* a reference document can reasonably be longer; the heuristic targets task-execution skills where every line is read in-flight.

## Review Checklist

After drafting, verify:

- [ ] Description includes triggers ("Use when...")
- [ ] SKILL.md is as short as the workflow allows (split reference/examples once growth threatens readability)
- [ ] No time-sensitive info
- [ ] Consistent terminology
- [ ] Concrete examples included
- [ ] References one level deep

## Glossary

Shared vocabulary used across the skill suite. Consistent terminology makes skills more predictable and maintainable.

### Model-invoked vs User-invoked

Controls whether the agent sees the `description` and can fire the skill autonomously.

- **Model-invoked**: `disable-model-invocation` is absent or `false`. The description sits in agent context every turn (context load). Agent can fire it on trigger; other skills can reach it. Use when the agent needs autonomous access.
- **User-invoked**: `disable-model-invocation: true`. No description in agent view — zero context load. Only the human can invoke it by typing the name. Use when the skill fires only on explicit request.

When user-invoked skills multiply, a **router skill** (one user-invoked skill cataloging the others) reduces cognitive load.

### Leading Word

A compact concept the model already knows (e.g. *fog of war*, *tracer bullets*, *tight*) that anchors a region of behaviour by recruiting existing priors. Better than a made-up term that must be defined from scratch. Serves predictability twice: in the body it anchors execution; in the description it anchors invocation when the same word appears in user prompts.

### Description

The frontmatter `description` field — the trigger surface for model-invoked skills. Rules: front-load the leading word, one trigger per distinct branch, collapse synonyms. Every word is a context cost, so descriptions need more pruning than body text.

### Completion Criterion

What "done" means at the end of a step. A sharp, checkable criterion resists the agent declaring done prematurely. The strongest criteria are both checkable and exhaustive ("every modified model accounted for" vs "produce a change list").

### Branch

A distinct invocation path through a skill — different runs take different routes. A linear skill has no branches. Progressive disclosure is licensed by branching: inline what every branch needs, push branch-specific material behind context pointers.

### Granularity

How finely skills are divided. Each split spends either context load (model-invoked) or cognitive load (user-invoked). Two justified cuts: by **invocation** (distinct leading word justifies its own model-invoked skill) or by **sequence** (hide post-completion steps to prevent rushing).

### Progressive Disclosure

Moving reference material behind context pointers (linked files) so SKILL.md stays legible. Disclose what only some branches need; keep inline what every run needs. See "When to Split Files" above — same principle, formalised vocabulary.

## Failure-Mode Taxonomy

Common skill defects, how to recognise them, and how to fix them.

| Failure mode | Symptoms | Fix |
|---|---|---|
| **Premature Completion** | Agent ends a step early; attention slips to "being done" | Sharpen the completion criterion first (cheap, local). If still fuzzy and rushing persists, hide post-completion steps by splitting. |
| **Duplication** | Same meaning in multiple places — repeated prose, restated rules | Consolidate to one **single source of truth** per meaning. A leading word is the deliberate exception (repeat the *token*, not the meaning). |
| **Sediment** | Stale layers accumulate because "adding feels safe, removing feels risky" | Regular pruning. Check every line for **relevance** — does it still bear on what the skill does? Delete what doesn't. |
| **Sprawl** | Skill too long even when every line is live — hurts readability, wastes tokens | Push reference behind context pointers; split by branch or sequence so each path carries only what it needs. |
| **No-Op** | Instruction changes nothing because the model already does it by default | Test each line: does it change behaviour vs default? Delete it or sharpen with a stronger **leading word**. |
| **Negation** | Steering by prohibition — "don't do X" — which makes X *more* available | Rewrite as positive guidance: describe the target behaviour so the banned one is never mentioned. |

### Map to existing Jarvis rules

- **No skill-to-skill calls / no shared abstractions** (`skills_independent_complementary`) — a Duplication variant where skills share logic instead of each being self-contained.
- **Anchored routing** (CLAUDE.md `/status` precedent) — prevents over-triggering, a Premature Completion variant at invocation level.
- **Description discipline** (one trigger per branch, front-load leading word) — counters No-Op and Duplication in descriptions.
- **Progressive disclosure via sub-files** — counters Sprawl. Already encoded in "When to Split Files" section.

The cure order: **sharpen the criterion** (local, cheap) before **splitting** (structural, expensive).
