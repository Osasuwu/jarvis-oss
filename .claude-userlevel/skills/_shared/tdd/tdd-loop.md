<!--
Adapted from Pocock's tdd skill (engineering/tdd/SKILL.md, upstream
mattpocock/skills @ 733d312884b3878a9a9cff693c5886943753a741).
Upstream:
https://github.com/mattpocock/skills/blob/733d312884b3878a9a9cff693c5886943753a741/skills/engineering/tdd/SKILL.md
Jarvis adaptations: (1) anti-horizontal-slicing rule kept verbatim; (2) new
refactor-permission clause in §Refactor (Jarvis-specific extension, #593).
MIT — see THIRD_PARTY_LICENSES/aihero-skills-MIT.txt.
-->

# TDD Loop — Red → Green → Refactor

Reference doc for `/implement` and `/delegate` when they engage TDD-mode (see CONTEXT.md → "TDD-mode" glossary entry). Procedure, not a skill — there is no `/tdd-loop` invocation. The host skill drives the loop; this file is what it consults.

## Philosophy

**Core principle**: tests verify behavior through public interfaces, not implementation details. Code can change entirely; tests shouldn't.

**Good tests** are integration-style: they exercise real code paths through public APIs. They describe _what_ the system does, not _how_ it does it. A good test reads like a specification — "user can checkout with valid cart" tells you exactly what capability exists. These tests survive refactors because they don't care about internal structure.

**Bad tests** are coupled to implementation. They mock internal collaborators, test private methods, or verify through external means (like querying a database directly instead of using the interface). The warning sign: your test breaks when you refactor, but behavior hasn't changed. If you rename an internal function and tests fail, those tests were testing implementation, not behavior.

See [tests.md](tests.md) for examples and [mocking.md](mocking.md) for mocking guidelines.

## Anti-Pattern: Horizontal Slices

**DO NOT write all tests first, then all implementation.** This is "horizontal slicing" — treating RED as "write all tests" and GREEN as "write all code."

This produces **crap tests**:

- Tests written in bulk test _imagined_ behavior, not _actual_ behavior
- You end up testing the _shape_ of things (data structures, function signatures) rather than user-facing behavior
- Tests become insensitive to real changes — they pass when behavior breaks, fail when behavior is fine
- You outrun your headlights, committing to test structure before understanding the implementation

**Correct approach**: vertical slices via tracer bullets. One test → one implementation → repeat. Each test responds to what you learned from the previous cycle. Because you just wrote the code, you know exactly what behavior matters and how to verify it.

```
WRONG (horizontal):
  RED:   test1, test2, test3, test4, test5
  GREEN: impl1, impl2, impl3, impl4, impl5

RIGHT (vertical):
  RED→GREEN: test1→impl1
  RED→GREEN: test2→impl2
  RED→GREEN: test3→impl3
  ...
```

## Workflow

### 1. Planning

When exploring the codebase, use the project's domain glossary (`CONTEXT.md`) so test names and interface vocabulary match the project's language, and respect ADRs in the area you're touching.

Before writing any code:

- [ ] Confirm what interface changes are needed (the AC from the grilled issue is your source of truth — don't redo the grill here)
- [ ] Confirm which behaviors to test (prioritize by AC ordering)
- [ ] Identify opportunities for deep modules (small interface, deep implementation)
- [ ] Design interfaces for testability
- [ ] List the behaviors to test (not implementation steps)

**You can't test everything.** Focus testing effort on critical paths and complex logic, not every possible edge case — the AC bounds the scope.

### 2. Tracer Bullet

Write ONE test that confirms ONE thing about the system:

```
RED:   Write test for first behavior → test fails
GREEN: Write minimal code to pass → test passes
```

This is your tracer bullet — proves the path works end-to-end.

### 3. Incremental Loop

For each remaining behavior:

```
RED:   Write next test → fails
GREEN: Minimal code to pass → passes
```

Rules:

- One test at a time
- Only enough code to pass current test
- Don't anticipate future tests
- Keep tests focused on observable behavior
- Each test links back to one acceptance-criterion bullet from the issue body — if a test does not, it is either out of scope or evidence the AC is incomplete (return to `/grill`)

### 4. Refactor

After all tests pass, look for [refactor candidates](refactoring.md):

- [ ] Extract duplication
- [ ] Deepen modules (move complexity behind simple interfaces)
- [ ] Apply SOLID principles where natural
- [ ] Consider what new code reveals about existing code
- [ ] Run tests after each refactor step

**Never refactor while RED.** Get to GREEN first.

**Refactor permission (Jarvis extension):** code enters your refactor scope only once you have written and greened a test for it, not before. Untested adjacent code is *not* refactor territory — touching it during the refactor phase silently expands scope past what the AC bounds and past what your test suite can catch. If a refactor would improve adjacent untested code, the correct move is either (a) write a characterization test for that code first (then it is in scope), or (b) leave it and flag a follow-up issue. This rule is the operational counterpart to SOUL.md's "Refactor adjacent legacy when it makes the change cleaner AND tests cover the touched behavior."

## Checklist Per Cycle

```
[ ] Test describes behavior, not implementation
[ ] Test uses public interface only
[ ] Test would survive internal refactor
[ ] Test maps to a specific AC bullet
[ ] Code is minimal for this test
[ ] No speculative features added
```
