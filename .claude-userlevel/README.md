# `.claude-userlevel/` — source of truth for user-level Jarvis

This directory mirrors what ships to `~/.claude/` via
`scripts/install/installer.py`. It is the single source of truth for
user-level Jarvis — the project-scoped `jarvis/.claude/` directory only
keeps project-specific bits now (see [`.claude/README.md`](../.claude/README.md)).

## Layout

```
.claude-userlevel/
├── settings.json    # Hooks — deep-merged with user's existing (M3 #338)
├── .mcp.json        # MCP servers — deep-merged with user's existing (M3 #338)
└── skills/          # Core universal skills (M2 #337)
    ├── implement/
    ├── delegate/
    └── ...
```

`SOUL.md` is not in this tree — its canonical location is
[`config/SOUL.md`](../config/SOUL.md); the installer copies it to
`~/.claude/SOUL.md` (M4 #339). It's loaded into every session via a bare
`@SOUL.md` import in `CLAUDE.md` — same mechanism as `@DOCTRINE.md` — not a
SessionStart hook step (#1328).

"Bare" is load-bearing, not stylistic: the import must be a line whose entire
content is `@SOUL.md`. Both imports shipped mid-prose from #1328/#1315 until
#1426, resolved as nothing, and delivered neither file to any session for
months — while a guard asserting the substring `@SOUL.md` stayed green the
whole time. `tests/ci/test_soul_import_guard.py` now asserts the form.

## M3: how `settings.json` / `.mcp.json` land at `~/.claude/`

Both files use **deep-merge** (not plain copy), preserving user keys that
jarvis doesn't own:

- `settings.json` — per-event wholesale replace inside `hooks.<Event>`;
  events jarvis doesn't declare (`Stop`, etc.) stay put.
- `.mcp.json` — per-server wholesale replace inside `mcpServers.<name>`;
  user-added servers stay put.

**Known trade-off**: if a user has a *custom entry* under an event/server
jarvis owns (e.g. their own SessionStart hook, or a user-defined `memory`
server), it is replaced on apply. Backup preserves it under
`.claude.backup-<ts>/`. Users wanting extra logic for jarvis-owned events
should compose downstream (e.g. add logic inside `session-context.py`).

**ceiling: the `.env*` permission deny is a glob, so `.env.example` is denied
too.** `Read(**/.env.*)` / `Edit(**/.env.*)` in `settings.json` swallow the
secret-free template along with the real files. Permission rules are evaluated
deny → ask → allow with first-match-wins and specificity does not reorder them,
so a deny cannot carry an allowlist exception and there is no negation operator
to write one with. Accepted deliberately in #1452 — a readable `.env.backup` is
the worse failure. Upgrade path: narrow the deny only if the permission system
gains per-rule exceptions; until then consumers take env-var names from
`README` / `docker-compose*` / `.github/workflows/*`, or ask the user to paste
the template (it holds no values, so pasting is safe).

Relative paths (`scripts/...`, `config/...`) in the source templates are
rewritten to absolute paths inside the jarvis repo at install time by
`installer.py:_transform_json_paths`. So these templates stay readable as
in-repo artefacts, and the rewrite logic is the single place path-portability
concerns land.

### Hardware-bound MCP servers stay per-device

`bambu-printer` (and any future hardware-tied server) is intentionally **not**
in `.claude-userlevel/.mcp.json`: the installer registers every listed server
at user scope on *every* device, and a server whose hardware/env
(`BAMBU_IP`, …) is absent on a machine error-spams each session there.
Register such servers manually on the device that owns the hardware:

```
claude mcp add -s user bambu-printer -e BAMBU_IP=... -e BAMBU_TOKEN=... -e BAMBU_SERIAL=... -- npx -y bambu-printer-mcp
```

The installer never removes user-scope servers, so a manual registration
survives future `install.ps1 -Apply` runs.

## Why a whitelist in `install-manifest.yaml`?

An explicit whitelist means dropping a README, experiment note, or
half-finished skill into this tree doesn't auto-leak into every user's
`~/.claude/`. Add new core skills to the `skills.include` list in
`install-manifest.yaml` at the same time you add the directory here.

## Editing core skills

Edit `.claude-userlevel/skills/<name>/SKILL.md` and re-run
`install.ps1 -Apply` (or `install.sh --apply`) to propagate the change to
`~/.claude/skills/<name>/`. That's the only copy Claude Code loads — the
project-scoped `jarvis/.claude/skills/<core>/` was removed in M5 (#340).

## Path portability — status after M3

Hook command strings in `settings.json` and MCP server entries in `.mcp.json`
are **rewritten at install time** by `_transform_json_paths` (relative
`scripts/`/`config/` → absolute `<JARVIS_HOME>/scripts/...`). All jarvis
hook + MCP bootstrap scripts resolve their own root via
`Path(__file__).resolve().parent.parent`, so they work under any CWD.

**Skill body** references to `scripts/` and `config/` are still
project-CWD-relative prose (no shell invocation), and skills currently
execute in whatever CWD Claude Code was launched from. If a skill starts
shelling out with a CWD-relative path in future, prefer `$JARVIS_HOME`
or absolute paths over CWD-relative.
