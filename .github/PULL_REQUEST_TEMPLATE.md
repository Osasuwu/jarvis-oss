<!--
No gate reads this body; it is for the human who reviews. What the gates read is the set of
changed paths, the labels and the review requests (see the Gates section of AGENTS.md).
Delete the lines that do not apply. Public repo: no personal literals, no secrets, no paths from
a private repo.
-->

## Summary

What changed and why, in a few lines.

Closes #N

## Held paths

Which of these the PR touches, so the reviewer knows which hold to expect:

- [ ] A document (`docs/` except `docs/adr/`, `incidents/`, `README.md`): `waiting-human-review` is applied
  automatically and stays red until a human removes the label. Do not remove it yourself.
- [ ] Machinery (`.github/`, `.agents/`, `.claude/`, `scripts/`, `tests/`): `machinery-guard` holds the PR the same way.
- [ ] Neither.

## Testing

What you ran and what it showed: `python -m pytest tests -v` (pass count, skips), and for each test you
added or changed, the mutation probe, as `<production file>:<line> <mutation> -> <test> red`.

## Risk

`Risk: LOW|MEDIUM|HIGH — <reason>`

## Safety

- [ ] No secret and no personal literal (name, home path, host, account) in the diff, commits or this body.
- [ ] Nothing quoted from a source I did not open.
