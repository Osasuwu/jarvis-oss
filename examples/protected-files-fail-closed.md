---
fit: works when you want a real instance of a fail-closed trade-off being accepted deliberately, not just the rule's abstract description
last_seen: 2026-09-16
pairs_with: docs/agent-safety-hooks.md
---

# A real fail-closed trade-off, accepted on purpose

The private source project this practice is drawn from runs two copies of its protected-file
hook, by its own account (not public): one scoped to an interactive session, one scoped to the
project (so it also runs unmodified in CI). The project-scoped copy — ported here as
[`.agents/hooks/protected-files.py`](../.agents/hooks/protected-files.py) — carries this in its
own top-of-file docstring:

> Consequently it is NOT principal-aware — unlike a user-level hook, it always blocks edits to
> protected files, with no "live owner" bypass. This is an intentional fail-closed trade-off for
> the CI surface... The accepted consequence is that this project hook also blocks the live
> owner's own local interactive edits to canonical protected files, even where a still-present
> user-level hook alone would allow them.

That's a team accepting a real, named cost — the project's own operator loses the ability to edit
`.gitleaks.toml` directly, even at their own keyboard, without opening a PR — in exchange for the
same hook code running correctly, unmodified, in a context (CI/Actions) where there is no live
operator to have earned an exception in the first place. The alternative (branch the hook's
behavior on whether a human is present) is ruled out in the same docstring: there's no reliable
signal inside a hook subprocess to make that branch on, since it always receives piped stdin. The
trial behind that reasoning ran in the private source project and is not public.

See [`agent-safety-hooks.md`](../docs/agent-safety-hooks.md) for the practice this example
evidences.
