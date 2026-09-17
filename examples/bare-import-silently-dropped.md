---
fit: works when you are about to rely on an include line in an agent rules file and want to know how it fails, not just the syntax
last_seen: 2026-09-17
pairs_with: docs/writing-into-user-owned-files.md
---

# An include line that loaded nothing, for months

This project is drawn from a private operator repo. That repo keeps two always-loaded files next
to its user-level `CLAUDE.md`:

- one with the agent's identity and core safety rules
- one with shared merge norms

Both were wired in through Claude Code's `@path` import, and both imports were written **inside a
sentence**:

```
Identity lives in @FILE_A.md, installed alongside this file from … Shared cross-repo norms … live in @FILE_B.md, …
```

(The file names are replaced; the sentence shape is verbatim.)

**What happened.** Neither file reached any session or subagent.

A guard test existed, and it was named for the bare-line form. Its assertion, though, was
`assert "@FILE_A.md" in stripped`: a substring check, which the mention inside the sentence
satisfied. The change merged with every check green.

The failure was found about two months later, while probing what subagents inherit:

- A subagent with no tools was asked whether a heading from each file was in its context. It
  reported both absent.
- As a control, two imports in the project `CLAUDE.md` that were bare and at the start of their
  lines were checked the same way. Both came back present.

**The fix** had three parts:

1. Each import moved to its own line, bare: `@FILE_A.md`.
2. The guard now asserts the **form** instead of a substring: a line matching `^@<path>\s*$`
   outside code spans. It also asserts that the target file exists on disk.
3. The fix was verified in a **fresh** session. A rules-file change is read at session start, so
   the session that made it cannot verify it.

Today's user-level stub still carries the lesson as a comment: "Keep the imports below **bare and
on their own line** — that is the only form with evidence of resolving."

**What it shows for the include-line option:** the include is cheap to write, and it stays silent
when it does not load. Test that the content arrived (with `/context` or a probe), not that the
line is present. As of 2026-09-17, Claude Code's documentation does not say whether an import has
to be on its own line.
