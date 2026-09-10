# Upstream sync procedure

How this template is refreshed from the private upstream jarvis repo. One
release per sync; the template's history stays **squashed** — one commit per
release, never the upstream commit history.

## Anchors

Each release records the upstream commit it corresponds to. The next sync
diffs upstream from the previous anchor to the new one.

| Template release | Template commit | Upstream anchor |
|---|---|---|
| v0.5.0 (root) | `54e38f27` | `9bd4b1a` |
| v0.6.0 | `dd4f9bc5` | `961aae13a147f9f01bd2e81d12f0b0ed1b97d803` |
| v0.7.0 | *(this release)* | `4f3ad22a5f8f8245ece6288891ab90d6f0a30a36` |

## Recipe (graft + merge, squash finalize)

1. Clone this repo into a scratch dir; add upstream as a remote
   (`git remote add jarvis <path-or-url>`); fetch.
2. Branch `sync` from `main`. Graft the histories: `git replace --graft
   <old-anchor> <template-root>` (or merge with `--allow-unrelated-histories`
   using a base), then `git merge <new-anchor>`.
3. Resolve conflicts **in favour of upstream content**, re-applying the
   template's standing adaptations (see below).
4. Run the de-personalization pass and the full test suite.
5. Finalize as ONE commit parented on `origin/main` — never push the merge
   history:

   ```bash
   git add -A
   TREE=$(git write-tree)
   COMMIT=$(git commit-tree "$TREE" -p origin/main -m "sync: upstream <new-anchor> (vX.Y.Z)")
   git push origin "$COMMIT":main
   git tag vX.Y.Z "$COMMIT" && git push origin vX.Y.Z
   ```

6. Create the GitHub release with user-facing notes; record the new anchor in
   the table above as part of the same commit.

## Exclusion set

Upstream paths deliberately absent from the template (do not re-introduce on
merge): `.hex-skills/runtime-artifacts/`, `docs/decisions/`, `.out-of-scope/`,
personal bench/proposal docs, upstream-owner repo-baseline manifests and
snapshots (the template ships `example-owner__example-repo` fixtures instead),
and tests bound to those excluded fixtures.

## De-personalization tolerance bar

The template must connect to **none** of the upstream author's services —
bring-your-own Supabase, tokens, and repos. Concretely:

**Must be genericized** (live surfaces):

- code defaults (repo slugs in `os.environ.get(..., fallback)`, sandcastle
  `SANDCASTLE_REPO` fallback);
- device names → the `JARVIS_SCHEDULED_HOST` env pattern;
- local usernames and machine paths (upstream drive paths → `C:\repos\...` /
  `<repos-root>` in examples);
- portfolio repo slugs in live config, workflows, and comments referencing
  files that don't exist here;
- bot identity: `jarvis-ci[bot]` (upstream uses its own).

**Tolerated** (kept as-is):

- upstream issue/PR links (`github.com/Osasuwu/jarvis/...`) — they are the
  design history and resolve publicly;
- doc-prose mentions of upstream project names in narrative/history context;
- names inside pure test fixtures and scrubber test data (synthetic IPs);
- `docs/research/` verbatim captures (allowlisted by the drift guard).

Rule of thumb: if a fresh clone would *execute or resolve* the value, it must
be generic; if it merely *narrates history*, it may stay.

## Verification

- Full `pytest` must pass (on Windows, a small set of upstream path-separator
  failures is pre-existing and identical in upstream itself; CI runs Linux).
- Tree-wide marker sweep must be clean outside the tolerated locations:
  device names, usernames, machine paths, second-owner slugs, tailnet IPs.
- `tests/ci/test_agents_stack_drift_guard.py` allowlist must match the shipped
  tree (`test_allowlist_entries_are_live`).
