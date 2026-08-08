---
name: to-spec
description: Turn the current conversation context into a spec and publish it to the project issue tracker. Use when user wants to create a spec from the current context.
---

This skill takes the current conversation context and codebase understanding and produces a spec. Do NOT interview the user — just synthesize what you already know.

The issue tracker and triage label vocabulary should be defined in the project's CLAUDE.md.

## Process

1. **Explore** the repo to understand the current state of the codebase, if you haven't already. Use the project's domain glossary vocabulary throughout the spec, and respect any ADRs in the area you're touching.

2. **Sketch modules** — Sketch out the major modules you will need to build or modify to complete the implementation. Actively look for opportunities to extract deep modules that can be tested in isolation.

   A deep module (as opposed to a shallow module) is one which encapsulates a lot of functionality in a simple, testable interface which rarely changes.

   Check with the user that these modules match their expectations. Check with the user which modules they want tests written for.

3. **Sketch the seams** — Before writing the Testing Decisions section, identify where tests will hook into the system. A seam is a point where the test can exercise behavior through a public interface — it shapes both testability and module boundaries.

   Apply these heuristics:

   - **Prefer existing seams** — use existing public interfaces (already-exposed functions, API handlers, service boundaries) over creating new test-specific ones. Existing seams are already stable; new ones add maintenance surface.
   - **Use the highest seam** that covers the behavior — prefer the outermost interface (API endpoint, CLI command, public function) over internal helpers. This aligns with SOUL's "tests verify behavior through public interfaces, not implementation."
   - **Fewest seams** — ideal is one seam per behavior being tested. Fewer seams means fewer test entry points to maintain and less coupling between tests and structure.
   - **Confirm the seam choice with the user** — surface the proposed seams and let the user validate before committing to them.

   The seams identified here feed the spec's **Testing Decisions** section — record which seams were chosen and why. This step is about test hook points, not acceptance criteria (that is the grill's domain).

4. **Write the spec** using the template below. Before publishing, load and execute the research-pass gate:

   **Procedural source: [`../_shared/research-pass-gate.md`](../_shared/research-pass-gate.md).**

   This gate is **unconditional** — publishing artifacts always requires a research artifact. If the gate blocks, do not publish; propose `/research` on the spec topic first.

   Once the gate passes, publish to the project issue tracker. Apply the `ready-for-agent` triage label — no need for additional triage.

<spec-template>

## Problem Statement

The problem that the user is facing, from the user's perspective.

## Solution

The solution to the problem, from the user's perspective.

## User Stories

A LONG, numbered list of user stories. Each user story should be in the format of:

1. As an <actor>, I want a <feature>, so that <benefit>

<user-story-example>
1. As a mobile bank customer, I want to see balance on my accounts, so that I can make better informed decisions about my spending
</user-story-example>

This list of user stories should be extremely extensive and cover all aspects of the feature.

## Implementation Decisions

A list of implementation decisions that were made. This can include:

- The modules that will be built/modified
- The interfaces of those modules that will be modified
- Technical clarifications from the developer
- Architectural decisions
- Schema changes
- API contracts
- Specific interactions

Do NOT include specific file paths or code snippets. They may end up being outdated very quickly.

Exception: if a prototype produced a snippet that encodes a decision more precisely than prose can (state machine, reducer, schema, type shape), inline it within the relevant decision and note briefly that it came from a prototype. Trim to the decision-rich parts — not a working demo, just the important bits.

## Testing Decisions

A list of testing decisions that were made. Include:

- A description of what makes a good test (only test external behavior, not implementation details)
- Which modules will be tested
- Prior art for the tests (i.e. similar types of tests in the codebase)
- The seams identified in step 3 — which interfaces are used to hook tests in, and why those seams were chosen

## Out of Scope

A description of the things that are out of scope for this spec.

## Further Notes

Any further notes about the feature.

</spec-template>
