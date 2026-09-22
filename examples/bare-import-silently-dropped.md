---
fit: works when you are about to rely on an include line in an agent rules file and want to know how it fails, not just the syntax
last_seen: 2026-09-17
pairs_with: docs/writing-into-user-owned-files.md
---

# An include line that loaded nothing

This project is drawn from a private operator repo. That repo keeps two always-loaded files next
to its user-level `CLAUDE.md`:

- one with the agent's identity and core safety rules
- one with shared merge norms

Both were wired in through Claude Code's `@path` import, and both imports were written **inside a
sentence**, each with a comma glued to the path:

```
Identity lives in @FILE_A.md, installed alongside this file from … Shared cross-repo norms … live in @FILE_B.md, …
```

(The file names are replaced; the sentence shape is verbatim.)

**What happened.** Neither file reached any session or subagent.

A guard test existed, and it was named for the bare-line form. Its assertion, though, was
`assert "@FILE_A.md" in stripped`: a substring check, which the mention inside the sentence
satisfied. The change merged with every check green.

The imports went in on 2026-07-29 and 2026-08-03. The failure was found on 2026-08-07, 4 and 9
days later, while probing what subagents inherit:

- A subagent with no tools was asked whether a heading from each file was in its context. It
  reported both absent.
- As a control, two imports in the project `CLAUDE.md` that were bare and at the start of their
  lines were checked the same way. Both came back present.

**The fix** had two parts, merged the same day:

1. Each import moved to its own line, bare: `@FILE_A.md`.
2. The guard now asserts the **form** instead of a substring: a line matching `^@<path>\s*$`
   outside code spans. It also asserts that the target file exists on disk.

The issue also asked for a check in a **fresh** session, since a rules-file change is read at
session start and the session that made it cannot see it. The fixing PR flagged that check as not
run.

**Why it failed is not settled.** Two things differed from the working imports: the position
inside a sentence, and the glued comma. Claude Code's docs show a mid-sentence import as valid:
"See @README for project overview and @package.json for available npm commands for this project."
In every example in the docs the path ends at a space or a line end, so the comma is at least as likely the
cause. Both explanations take the same fix, so the guard asserts the one form known to work.

Today's user-level stub carries the lesson in the line above its imports: "Keep the imports below
**bare and on their own line** — that is the only form with evidence of resolving."

**What it shows for the include-line option:** the include is cheap to write, and it stays silent
when it does not load. Test that the content arrived (with `/context` or a probe), not that the
line is present. As of 2026-09-17, Claude Code's documentation does not say what happens to an
import whose path has punctuation glued to it.
