# Vendored plugin pins

`.claude/marketplace/.claude-plugin/marketplace.json` vendors exactly one plugin via a
`git-subdir` source: **`code-review`** (`Osasuwu/claude-plugins-official`, fork of the
upstream Anthropic plugin), pinned to an immutable **git tag** rather than `ref: main` —
it gates every PR's `review` required check, so a change there must be a deliberate,
reviewed bump, not something that silently rides in on the next merge to the fork's
`main` (#1209). It stays a fork because it carries a jarvis-specific fix: a binding
"SYNCHRONOUS EXECUTION" directive (no backgrounded sub-reviewers, all results in hand
before turn end) that upstream's version lacks — without it, a backgrounded reviewer's
completion can arrive after the headless `claude -p` process has already exited at
`end_turn`, silently dropping review results (incident jarvis#1239 / PR #1237).

**Pin with a tag, never a raw commit SHA** — the plugin installer resolves `ref` via
`git clone --branch <ref>`, which only accepts branch/tag names. A bare 40-char SHA
fails with `Remote branch <sha> not found in upstream origin` and breaks `review` for
every PR in the repo until fixed (#1520 — regression from the first attempt at this pin,
#1517).

**The other 5 plugins previously vendored here — `pr-review-toolkit`, `session-report`,
`hookify`, `claude-md-management`, `mcp-server-dev` — are no longer vendored** (#1797).
They were unmodified upstream Anthropic plugins with no jarvis-specific behavior riding
on them, so mirroring them locally added maintenance surface (this doc, the manifest
entry, `scripts/setup-device.py`'s `CLAUDE_PLUGINS`) with no corresponding benefit. They
now install directly from the official `claude-plugins-official` marketplace (registered
once via `claude plugins marketplace add claude-plugins-official`); see
[`docs/setup.md`](../setup.md) for the install checklist.

## Bumping the pin

1. Review what changed on the fork's `main` since the current pinned tag's commit:
   ```bash
   gh api repos/Osasuwu/claude-plugins-official/compare/<current-tag>...main --jq '.commits[].commit.message'
   ```
2. If the change is safe to adopt, create a new tag on the fork pointing at the commit to
   pin (`gh api repos/Osasuwu/claude-plugins-official/git/refs -f ref='refs/tags/<name>' -f sha='<commit-sha>'`)
   — do **not** put the raw SHA directly in `ref`, see above.
3. Update `ref` in `marketplace.json` to the new tag name.
4. Open a normal PR — the ref bump is reviewed like any other change, since it can alter
   the review pipeline's own behavior.

**Note on this PR's own review**: `.github/workflows/code-review.yml` triggers on plain
`pull_request` (not `pull_request_target`), gates on
`github.event.pull_request.head.repo.full_name == github.repository`, and checks out via
`actions/checkout@v7` with only `fetch-depth: 0` — no explicit `ref:`, so it checks out the
PR head. `plugin_marketplaces: ./.claude/marketplace` is a relative local path that
`claude-code-action@v1`'s `base-action/src/install-plugins.ts` (`isLocalPath()`,
`validateMarketplaceInput()`, `addMarketplace()`) hands straight to
`claude plugin marketplace add` in that checked-out tree — there is no restore-from-`main`
step anywhere between checkout and the action. A same-repo PR that edits this manifest is
therefore reviewed against its own changed content, not `main`'s — it is **not** review-blind
on that basis (unlike editing `code-review.yml` itself, which changes the reviewer's own
invocation and stays in that review-blind class).
