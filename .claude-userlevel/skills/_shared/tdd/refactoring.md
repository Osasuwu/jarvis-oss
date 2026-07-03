<!--
Adapted from Pocock's tdd skill (engineering/tdd/refactoring.md, upstream
mattpocock/skills @ 733d312884b3878a9a9cff693c5886943753a741).
Upstream:
https://github.com/mattpocock/skills/blob/733d312884b3878a9a9cff693c5886943753a741/skills/engineering/tdd/refactoring.md
Jarvis adaptations: none (verbatim).
MIT — see THIRD_PARTY_LICENSES/aihero-skills-MIT.txt.
-->

# Refactor Candidates

After TDD cycle, look for:

- **Duplication** → Extract function/class
- **Long methods** → Break into private helpers (keep tests on public interface)
- **Shallow modules** → Combine or deepen
- **Feature envy** → Move logic to where data lives
- **Primitive obsession** → Introduce value objects
- **Existing code** the new code reveals as problematic
