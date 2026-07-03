You are analysing a Claude Code session transcript. Extract memory-worthy
insights that the user's AI agent should remember for future sessions.

For each insight, determine the following fields:

- **type** — ``user`` (personal trait, habit, preference, communication
  style) or ``feedback`` (project-specific lesson, process improvement,
  tooling preference, architecture decision).
- **project** — null for ``user`` type (always global).  For
  ``feedback``, one of ``"jarvis"``, ``"redrobot"``, or null if the
  insight is cross-project.
- **name** — short kebab-case slug that uniquely identifies this
  insight (e.g. ``"prefers-early-return-over-nested-if"``).
- **description** — one-line summary (under 100 chars).
- **content** — 2–5 sentences with enough context to be useful without
  rereading the transcript.  Include the *why* behind the insight, not
  just the *what*.
- **tags** — 1–5 lowercase tags (single words, e.g.
  ``["coding-style", "python"]``).

Return **only** a valid JSON array of objects.  Maximum **5** objects.
If nothing is worth remembering, return an empty array: ``[]``.

--- Transcript follows ---

{transcript}
