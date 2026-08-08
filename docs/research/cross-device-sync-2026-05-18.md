---
title: Cross-device sync architecture for a solo-dev personal agent
date: 2026-05-18
status: working-doc
project: jarvis
scope: 3-device Windows-primary Claude-Code-native agent (dotfiles + secrets + memory)
author: subagent (research)
sources_count: 22
related:
  - docs/research/installer-hygiene-synthesis-2026-05-16-explore.md
  - docs/research/mirror-vs-source-drift-2026-05-16-explore.md
  - docs/research/cross-device-observability-2026-05-16-explore.md
  - docs/research/memory-architecture-deep-dive-2026-05-18.md
---

## Executive summary

1. **Stay with `install.ps1` + Supabase. Don't migrate to chezmoi.** chezmoi solves a problem jarvis already solved (mirror-from-source) and adds template-DSL surface area. The 52-line wrapper + python installer is *less* code than a chezmoi adoption plus its template files would be. Migration would be ~1 week for zero new capability. (`chezmoi` comparison table, `install.ps1` 52 lines).
2. **GNU `stow` is disqualified on Windows.** Symlink creation requires Developer Mode or `SeCreateSymbolicLinkPrivilege` and stow's POSIX assumptions (file modes, paths) break. `stow-rs` (hardlink-based fork) exists but is one-maintainer alpha. Drop from consideration.
3. **`sops` + `age` is the right secrets layer** if jarvis ever wants `.env` in git. Both have first-class Windows binaries (winget / scoop / chocolatey), sops v3.13.1 ships native `amd64.exe` and `arm64.exe`. Decryption is a single command, fits cleanly into installer pre-apply. Cost: $0. Doppler is overkill at $0/mo free tier capped at 3 users — you don't need a UI, audit logs, or rotation for a solo agent.
4. **Last-write-wins is the wrong default for `memory_store` updates** but the right default for `record_decision` (append-only). The current schema treats both the same way — that is the bug behind the cross-device write-conflict question, not the sync mechanism itself.
5. **Add an `updated_at` + `version int` column to mutable memory rows** and require `WHERE version = $old_version` on update. PostgreSQL's MVCC gives this for free; we just need the column. This is **optimistic concurrency** (OCC), supported by the Supabase docs and the canonical pattern for low-write-rate multi-writer Postgres.
6. **Bi-temporal pattern (event_time + ingest_time) from Graphiti** — already flagged in `memory-architecture-deep-dive`. Reiterating here: the same pattern resolves "tab open from yesterday tries to overwrite" by making "what was true at T" queryable, separate from "what we now know."
7. **CRDTs are overkill** for a single-principal 3-device agent. CRDTs solve multi-writer concurrent-edit with no central coordinator; jarvis has a central coordinator (Supabase) and one writer-at-a-time in practice. Event-sourcing-lite (append-only `episodes` table) covers the remaining cases.
8. **Don't sync `.env` at all if you can help it** — keep them device-local, document the required keys in a checked-in `.env.example`, and document recovery via `~/.claude/skills/<skill>/CONFIG.md`. This is what Obsidian's Sync docs, Cursor's settings sync, and aider's `.env` pattern all converge on: secrets stay device-local even when config syncs.
9. **`.gitattributes` with `text=auto` + per-extension overrides** is the load-bearing fix for the CRLF/LF and BOM bugs already in jarvis memory (telegram `.env` BOM regression, telegram `.env` CRLF regression). One commit, prevents recurrence on every editor save.
10. **Tailscale Funnel on the free Personal plan** is a real option for the few cases where Supabase isn't enough (e.g. exposing a local Ollama instance to a phone). $0, all 3 devices already trivially eligible, port 443/8443/10000 cap is fine.

---

## 1. Current state recap

Jarvis already has a working cross-device sync stack. Don't lose that context:

| Concern | Current mechanism | Status |
|---|---|---|
| Dotfile mirroring | `install.ps1` → `scripts/install/installer.py` from `Osasuwu/jarvis` repo | Working. 52-line PS wrapper + python installer. Source-of-truth in `.claude-userlevel/`. |
| Memory cross-device | Supabase Postgres + pgvector, `mcp_memory` MCP server | Working. THE backbone — no other mechanism needed. |
| MCP server config | `.mcp.json` checked into repo | Working. Rule: no hardcoded usernames/paths (CLAUDE.md). |
| Secrets | `.env` files, **device-local**, not synced | Working but undocumented per-key. |
| Identity / personality | `config/SOUL.md` in repo, loaded by SessionStart hook | Working. |
| Skill files | `.claude-userlevel/skills/` mirrored to `~/.claude/skills/` by installer | Working. |

The user's stated gap is **not** "rebuild this" — it's "audit the alternatives so I know whether I'm leaving wins on the table." Most of this doc is therefore *defensive*: documenting why the current stack is correct, with a handful of *additive* fixes.

---

## 2. Dotfile manager comparison

### The matrix

Verified against the [official chezmoi comparison table](https://www.chezmoi.io/comparison-table/) and ad-hoc evidence:

| Tool | Windows native | Encryption | Templating | Single binary | Symlinks required | Verdict for jarvis |
|---|---|---|---|---|---|---|
| **`install.ps1` (current)** | ✅ native | via `.env` device-local | python jinja-style if wanted | ❌ (python + ps1) | ❌ copy-based | Keep |
| **chezmoi** | ✅ native | ✅ age/gpg/sops | ✅ Go text/template | ✅ | ❌ | Migration not justified |
| **yadm** | partial (git wrapper) | ✅ gpg/transcrypt | needs external (envplt/j2cli — unmaintained) | shell script | ❌ | No — templating story broken |
| **GNU stow** | ❌ (POSIX symlinks) | ❌ | ❌ | ❌ Perl | ✅ required | Disqualified on Windows |
| **stow-rs** | ✅ (hardlinks) | ❌ | ❌ | ✅ Rust | hardlinks | One-maintainer fork; not stable enough |
| **dotbot** | ✅ | ❌ | ❌ | ❌ python pkg | ✅ required | Lower feature ceiling than installer.py |
| **nix-darwin / home-manager** | ❌ (Linux/macOS only; WSL2 workaround) | via sops-nix | ✅ Nix lang | ❌ heavy | n/a | Disqualified — no native Windows path |

### Why migration to chezmoi is NOT justified

The classic case for chezmoi over a custom installer is *templates* + *encryption*. Jarvis doesn't need either:
- **Templates:** the only per-device data is `config/device.json` + `.env`, both already loaded by separate mechanisms.
- **Encryption:** secrets stay device-local; nothing in `~/.claude/` is sensitive.

Net effect of migration: rewrite installer in chezmoi DSL, lose ability to write Python pre/post hooks (or wrap them via `run_once_` scripts which is awkward on Windows for `.ps1`), gain nothing operational. **Skip.**

The installer-hygiene synthesis (`installer-hygiene-synthesis-2026-05-16-explore.md`) already concluded the same — quarantine-orphan + dry-run pattern is the actual ROI, not the wrapper choice.

### When chezmoi WOULD be the answer

If jarvis ever expands beyond a single principal (team scenario), the per-developer templating (`{{ .chezmoi.hostname }}`-style branching) starts paying. Until then — no.

Sources: [chezmoi comparison table](https://www.chezmoi.io/comparison-table/), [chezmoi Windows guide](https://www.chezmoi.io/user-guide/machines/windows/), [HN: I evaluated Stow and tried Chezmoi, settled on YADM](https://news.ycombinator.com/item?id=39975247), [stow-rs](https://github.com/0xErgod/stow-rs), [BigGo: YADM vs Chezmoi vs Nix debate](https://biggo.com/news/202412191324_dotfile-management-tools-comparison).

---

## 3. Secrets management

### What you actually have

`.env` files are device-local. The two regressions in jarvis memory are *encoding* bugs (BOM, CRLF) — not sync bugs.

### sops + age (the right add when you need it)

If a future skill genuinely needs the *same* secret on 3 devices and you don't want to type it three times, **commit it encrypted**. The workflow is well-documented:

1. `age-keygen -o ~/.config/age/jarvis.key` once per device (private key never leaves device).
2. `.sops.yaml` at repo root lists the public keys of all 3 devices.
3. `sops -e -i .env.shared` produces an encrypted file safe to commit.
4. Installer adds a pre-apply step: `sops -d .env.shared > .env.decrypted` if the file exists.

Verified 2026 state: **sops v3.13.1 (May 16, 2026)** ships native Windows binaries (`sops-v3.13.1.amd64.exe`, 50.6 MB). **age** installable via `winget install FiloSottile.age`, `scoop install age`, or `choco install age.portable`. CNCF Sandbox project, active maintenance.

Cost: $0, no SaaS. Risk: one private key per device — losing all 3 = lose access (mitigated by backup of one key to a password manager, OR by adding a recovery age recipient like a YubiKey).

### Doppler (don't)

Doppler free tier covers 3 users / 5 projects with CLI access. Looks attractive, but:
- Adds a SaaS dependency for something local files do for free.
- Doppler's free tier was *reduced* in the past year (CyberSecTool 2026 breakdown) — counterparty risk.
- Adds an extra failure mode: agent decides to do something at 3 AM, Doppler API is throttled, secret unavailable.

Match the cost profile: solo dev, 3 devices, ~$20/mo external budget. sops+age fits, Doppler doesn't.

Sources: [Using SOPS with Age and Git like a Pro](https://devops.datenkollektiv.de/using-sops-with-age-and-git-like-a-pro.html), [Secure Your Environment Files with Git, SOPS, and age (Aug 2025)](https://blog.cmmx.de/2025/08/27/secure-your-environment-files-with-git-sops-and-age/), [getsops/sops releases](https://github.com/getsops/sops/releases), [Doppler pricing](https://www.doppler.com/pricing), [Doppler vs SOPS (Infisical Top Tools 2026)](https://infisical.com/blog/best-secret-management-tools), [age on Windows: winget/scoop/choco](https://github.com/FiloSottile/age).

---

## 4. Memory write-conflict scenarios

### What Supabase / Postgres gives free

PostgreSQL MVCC means **no readers ever block writers and vice versa** — that part is free. What is NOT free:
- **Lost-update on concurrent UPDATE:** A reads row at v1, B reads row at v1, both UPDATE — second write wins, first write is silently lost. This is the bug behind the user's "tab from yesterday overwrites" scenario.
- **Out-of-order recall during compose:** B's update hasn't replicated to A's session view yet.

### Four patterns evaluated

| Pattern | Fit for jarvis | Why |
|---|---|---|
| **LWW (status quo)** | OK for append-only `episodes`, WRONG for `memories` rows that get edited | Existing default. Append-only tables suffer no lost-update problem by definition. Mutable rows do. |
| **OCC w/ version column** | ✅ **Recommended for mutable tables** | One `version int` column + check on UPDATE. Postgres-canonical, zero external dependency. Conflict raises an error the agent can re-read+retry on. |
| **Vector clocks / CRDTs** | ❌ Overkill | Designed for no-central-coordinator multi-writer. Supabase IS the coordinator. |
| **Event sourcing (append-only)** | ✅ Already used implicitly via `record_decision`/`episodes` | Decisions can't be edited — only superseded. This is correct. |

### Concrete OCC migration

The `memory-architecture-deep-dive` already proposed a `memory_supersedes` edge for explicit supersession. Combine with:

```sql
ALTER TABLE memories ADD COLUMN version int NOT NULL DEFAULT 1;
ALTER TABLE memories ADD COLUMN updated_at timestamptz NOT NULL DEFAULT now();

-- On update from MCP server:
UPDATE memories
SET content = $new, version = version + 1, updated_at = now()
WHERE id = $id AND version = $expected_version
RETURNING version;

-- 0 rows returned → conflict. Re-read, ask user / log to outcome, retry or abort.
```

This is the [Supabase OCC pattern](https://dev.to/arturampilogov/concurrency-and-row-versioning-part-1-39hn) used by RxDB's [Supabase replication plugin](https://rxdb.info/replication-supabase.html). One DDL migration; the MCP server change is small.

Conflict handling policy for the agent: on a 0-row response, run `memory_recall` for the current version, surface diff to user (or auto-merge if the conflict is on disjoint fields like `tags[]` vs `content`).

Sources: [Concurrency and Row Versioning Part 1](https://dev.to/arturampilogov/concurrency-and-row-versioning-part-1-39hn), [PostgreSQL MVCC docs](https://www.postgresql.org/docs/current/mvcc.html), [Supabase native object versioning discussion #40482](https://github.com/orgs/supabase/discussions/40482), [CRDTs vs Event Sourcing (Nov 2025)](https://medium.com/@optimzationking2/crdts-vs-event-sourcing-the-architecture-war-that-will-define-the-next-10-years-ae8245cd2ac9), [The CRDT Dictionary (Nov 2025)](https://www.iankduncan.com/engineering/2025-11-27-crdt-dictionary/).

---

## 5. Patterns from comparable systems

| System | Sync mechanism | What jarvis can steal |
|---|---|---|
| **Obsidian** (Sync / iCloud / Syncthing) | File-based; central server (paid) or P2P | Conflict-keeps-both-with-suffix pattern (Obsidian-iCloud plugin, March 2026). Apply to `memory_store` on OCC conflict: write the loser as `<id>.conflict.<ts>` so it isn't lost. |
| **Cursor** | Built-in settings sync via account; agent chat **does not** sync | Mirrors current jarvis split — config syncs, working state stays device-local. Validates the decision. |
| **Continue.dev** | Local `.continue/rules/` (in-repo, git-synced); Hub config (SaaS) | The `.continue/rules/` model is exactly what jarvis already does with `.claude-userlevel/skills/`. Validation, no change needed. |
| **aider** | `.aider.conf.yml` checked into repo, `.env` device-local | Matches the jarvis posture. Reinforces "don't sync secrets." |
| **Syncthing** | P2P, no central server, free, open source | Useful for a *future* case (large blobs, e.g. local LLM weights) where Supabase row-storage is wrong. Not needed for current memory workload. |
| **Tailscale Funnel** | Public-tunnel-to-local-service on free Personal plan | Useful when a skill needs to expose a local-only service (Ollama, a local web UI) to the agent's other devices or to a phone. Ports 443/8443/10000 only, free, TLS-only. |

Sources: [Sync Obsidian Across All Your Devices](https://www.stephanmiller.com/sync-obsidian-vault-across-devices/), [Obsidian-iCloud plugin](https://github.com/mnott/Obsidian-iCloud), [Cursor sync limitations forum](https://forum.cursor.com/t/sync-or-export-import-agent-chat-history-across-devices/152507), [Continue.dev rules docs](https://docs.continue.dev/customize/deep-dives/rules), [Syncthing.net](https://syncthing.net/), [Tailscale Funnel KB](https://tailscale.com/kb/1223/funnel), [Tailscale Pricing](https://tailscale.com/pricing).

---

## 6. Windows-specific failure modes

### BOM / encoding (recurring bug class in jarvis memory)

Two memories already in the catalog: `telegram_mcp_env_crlf_breaks_token`, `telegram_channel_env_bom_breaks_token`. Pattern: Windows editors (Notepad, VS Code with default settings, PowerShell `Out-File`) re-save as UTF-16 LE BOM or insert a UTF-8 BOM, breaking naive regex-based `.env` parsers.

**Root-cause class:** the parser is naive, but the editor environment is also the bug source. Fix at both layers.

PowerShell 5.1 default encoding for `Out-File` and `Set-Content` is **UTF-16 LE BOM**. PowerShell 6+ defaults to UTF-8 *without* BOM. Most cmdlets default differently from one another (e.g. `>` redirect ≠ `Out-File` ≠ `Set-Content` ≠ `Add-Content` historically).

**Defensive measures:**
1. `.gitattributes` `* text=auto eol=lf` + explicit `*.env eol=lf -text` (treat as binary so git doesn't touch BOM).
2. Installer health-check that scans `~/.claude/**/*.env` for BOM and CRLF and warns.
3. Documented `-Encoding utf8NoBOM` for any `Set-Content`/`Out-File` in installer-owned scripts (PowerShell 6+; PS 5.1 needs `[System.IO.File]::WriteAllText`).

### CRLF / LF

Without a `.gitattributes`, `core.autocrlf` is per-developer config and inconsistent across the 3 devices (especially if any device runs git via WSL). Canonical fix is a checked-in `.gitattributes` with `* text=auto`; binaries marked `-text`; shell scripts forced `eol=lf`; PowerShell forced `eol=crlf` (since `.ps1` execution policy historically prefers CRLF on Win PS 5.1).

### Symlinks

Windows symlinks require either Developer Mode on or `SeCreateSymbolicLinkPrivilege`. This is **the** reason `stow` doesn't work; chezmoi's docs explicitly call this out. Jarvis avoids symlinks entirely via copy-based install — keep that.

### File permissions

Windows NTFS ACLs don't map cleanly to POSIX modes. Skills shipping `chmod 755 *.sh` blindly will throw on Windows. Installer already handles this; rule: scripts shipping in `~/.claude/skills/` should not assume POSIX mode bits.

Sources: [PowerShell about_Character_Encoding (MS Learn)](https://learn.microsoft.com/en-us/powershell/module/microsoft.powershell.core/about/about_character_encoding?view=powershell-7.6), [PowerShell – UTF8 and BOM (Camille Debay)](https://debay.blog/2019/10/03/powershell-utf8-and-bom/), [GitHub Docs: Configuring Git to handle line endings](https://docs.github.com/en/get-started/git-basics/configuring-git-to-handle-line-endings), [gitattributes docs](https://git-scm.com/docs/gitattributes), [chezmoi Windows symlinks guide](https://www.chezmoi.io/user-guide/machines/windows/).

---

## 7. Concrete proposals

Numbered for the master table. Effort is wall-clock for one focused dev.

### [B7-1] Add `.gitattributes` with `text=auto` + explicit `.env eol=lf -text` (P0, 5min)
- One commit, prevents the CRLF/BOM class of bug from recurring.
- Add a line: `*.env -text` to make git treat `.env` as opaque bytes (no CRLF substitution).
- Source: [GitHub docs](https://docs.github.com/en/get-started/git-basics/configuring-git-to-handle-line-endings).
- Risk: existing CRLF-saved files in the repo need a one-time normalisation pass (`git add --renormalize .`). Low.

### [B7-2] Installer health-check: scan `~/.claude/**/*.env` for BOM + CRLF (P0, 1h)
- Add to `installer.py` post-apply phase, surface as a warning, don't auto-fix.
- Targets the *recurring* failure mode in memory. Both telegram bugs would have surfaced here pre-incident.
- Risk: false positives on `.env` that legitimately need BOM (rare on POSIX-style configs). Mitigate with allowlist.

### [B7-3] OCC migration: `version int` + `updated_at` on mutable memory tables (P1, 4h)
- Migrate `memories` (and any other mutable table). Append-only tables (`episodes`, `decisions`) keep LWW since they don't update.
- Update MCP `memory_update` to require `expected_version`; return conflict on mismatch.
- Source: [Supabase OCC pattern](https://dev.to/arturampilogov/concurrency-and-row-versioning-part-1-39hn).
- Risk: existing in-flight tooling that does blind upserts breaks; needs a 2-phase rollout (column added, then check enforced).

### [B7-4] Conflict-keeps-both side-table for OCC losers (P1, 2h)
- When OCC update returns 0 rows, write the *loser* version to a `memory_conflicts(memory_id, content, attempted_at, actor)` table.
- Inspired by Obsidian-iCloud "keep both versions" mode. Loser is never dropped silently.
- Risk: tiny — strict additive. Sized by conflict frequency, expected to be near-zero in practice.

### [B7-5] Adopt sops + age for any future cross-device shared secret (P2, 1d setup)
- Don't migrate existing `.env`. Add the pipeline so it's available when a skill needs it.
- `.sops.yaml` at repo root, `age-keygen` once per device, README section in installer.
- Source: [datenkollektiv SOPS+Age+Git guide](https://devops.datenkollektiv.de/using-sops-with-age-and-git-like-a-pro.html), [sops v3.13.1 Windows binary](https://github.com/getsops/sops/releases).
- Risk: key loss — mitigate by adding a recovery age recipient (e.g. password-manager-stored backup key).

### [B7-6] Document each `.env` key in `.env.example` per skill (P2, 1h)
- Currently undocumented per-skill. New device bootstrap is painful because keys are discovered by failure.
- `~/.claude/skills/<skill>/.env.example` with comments — checked into repo.
- Risk: drift if a skill adds a key without updating the example. Mitigate with installer health-check that diffs `.env` vs `.env.example`.

### [B7-7] Reject migration to chezmoi (P0 decision, 0 effort to *not* do it)
- Document the decision so it stops re-surfacing. `record_decision` with rationale: `install.ps1` + python is less code than chezmoi+templates, no template DSL surface area, no new dependency.
- Source: [chezmoi comparison table](https://www.chezmoi.io/comparison-table/) — features we don't need.
- Risk: false economy if a team appears. Reversibility: easy — migrate later if a team appears.

### [B7-8] Add bi-temporal columns (`event_time`, `ingest_time`) to memory tables (P1, 4h)
- Already proposed in `memory-architecture-deep-dive`. Restating here because it's the structural fix for "stale tab from yesterday."
- Source: [Graphiti/Zep bi-temporal pattern (arxiv 2501.13956)](https://arxiv.org/abs/2501.13956).
- Risk: schema migration touches the join queries; pair with B7-3.

### [B7-9] Tailscale Funnel as the path for local-service exposure (P3, 1h to verify)
- Don't deploy it now. Document as the answer to "I need device-B to reach a local service on device-A" — current implicit answer is "open a port + DDNS" which is worse.
- $0 on Personal plan, port 443/8443/10000.
- Source: [Tailscale Funnel KB](https://tailscale.com/kb/1223/funnel), [Tailscale pricing](https://tailscale.com/pricing).
- Risk: bandwidth cap is "non-configurable" per docs — fine for control-plane, wrong for video. Document the limit.

### [B7-10] Sibling-grep skill audit for `Set-Content`/`Out-File` without explicit encoding (P1, 30min)
- One-time grep across `scripts/install/`, `mcp-memory/`, all installer-touched scripts. Find places that emit text files without `-Encoding utf8NoBOM` (PS6+) or `[System.IO.File]::WriteAllText` (PS 5.1).
- Engineering-posture rule from CLAUDE.md: sibling-grep on fixes. The two telegram bugs are sibling instances of the same class.
- Risk: zero — read-only audit.

### [B7-11] Document the "secrets stay device-local" decision as a project invariant (P1, 15min)
- Add to CONTEXT.md / CLAUDE.md: secrets-policy is "device-local in `.env`, encrypted via sops+age only when same secret is genuinely needed on >1 device." Currently implicit; making it explicit closes the door on accidental commits.
- Source: pattern convergence — Obsidian, Cursor, aider, Continue.dev all separate config-sync from secrets-sync.
- Risk: zero — documentation only.

### [B7-12] Reject CRDT adoption explicitly (P3 decision, 0 effort)
- Periodic recurring temptation. `record_decision` so it stops surfacing in design discussions.
- CRDTs are for no-central-coordinator multi-writer. Supabase IS the coordinator. OCC + append-only is the right tool.
- Source: [CRDTs vs Event Sourcing (Nov 2025)](https://medium.com/@optimzationking2/crdts-vs-event-sourcing-the-architecture-war-that-will-define-the-next-10-years-ae8245cd2ac9).
- Risk: zero — documenting a rejection.

---

## 8. Don't-do (anti-patterns argued against)

1. **Don't adopt nix-darwin / home-manager.** No native Windows path — only works via WSL2, which means the agent's own dotfiles live inside a Linux VM and Windows-native Claude Code can't see them. The whole appeal of nix (reproducibility) breaks the moment you straddle WSL/native. Source: [NixOS Wiki — Nix Installation Guide](https://nixos.wiki/wiki/Nix_Installation_Guide).

2. **Don't adopt GNU stow.** Symlink farm manager assumes POSIX symlinks. On Windows requires Developer Mode + admin privilege bumps. `stow-rs` (hardlink fork) is a single-maintainer alpha. Source: [chezmoi Windows symlinks guide](https://www.chezmoi.io/user-guide/machines/windows/).

3. **Don't add Doppler / cloud secrets manager.** $0 free tier exists but adds a SaaS hop + counterparty risk for a problem `.env` files + (optionally) sops+age already solve at $0 with no network dependency. Source: [Doppler pricing](https://www.doppler.com/pricing), [Infisical Top Tools 2026](https://infisical.com/blog/best-secret-management-tools).

4. **Don't sync `.env` files via the dotfiles repo without sops.** Even private repos leak via forks, GitHub support staff access, machine compromise. If you sync, encrypt first. The existing "`.env` is device-local" rule is correct — preserve it.

5. **Don't implement CRDTs for memory.** Designed for no-central-coordinator, multi-writer, eventual-consistency scenarios. Supabase IS the central coordinator. Optimistic concurrency + append-only event logs cover every realistic jarvis scenario for an order of magnitude less code. Reach for CRDTs only if Supabase is removed from the architecture (it isn't going to be). Source: [The CRDT Dictionary (Ian Duncan, Nov 2025)](https://www.iankduncan.com/engineering/2025-11-27-crdt-dictionary/).

6. **Don't migrate from `install.ps1` to chezmoi.** The work-to-payoff ratio is bad. Templates and encryption — the two reasons to adopt chezmoi — aren't needed at current scope. Source: [chezmoi comparison table](https://www.chezmoi.io/comparison-table/).

---

## Sources (full list)

- [chezmoi homepage](https://www.chezmoi.io/)
- [chezmoi comparison table](https://www.chezmoi.io/comparison-table/)
- [chezmoi Windows user guide](https://www.chezmoi.io/user-guide/machines/windows/)
- [SOPS homepage](https://getsops.io/)
- [getsops/sops releases (v3.13.1, May 16 2026)](https://github.com/getsops/sops/releases)
- [Using SOPS with Age and Git like a Pro (datenkollektiv)](https://devops.datenkollektiv.de/using-sops-with-age-and-git-like-a-pro.html)
- [Secure your environment files with Git, SOPS, age (Aug 2025)](https://blog.cmmx.de/2025/08/27/secure-your-environment-files-with-git-sops-and-age/)
- [FiloSottile/age (Windows install via winget/scoop/choco)](https://github.com/FiloSottile/age)
- [Doppler pricing](https://www.doppler.com/pricing)
- [Infisical: Best Secret Management Tools 2026](https://infisical.com/blog/best-secret-management-tools)
- [Tailscale pricing](https://tailscale.com/pricing)
- [Tailscale Funnel KB article 1223](https://tailscale.com/kb/1223/funnel)
- [Syncthing homepage](https://syncthing.net/)
- [Supabase concurrency discussion #40482](https://github.com/orgs/supabase/discussions/40482)
- [Concurrency and Row Versioning Part 1 (DEV)](https://dev.to/arturampilogov/concurrency-and-row-versioning-part-1-39hn)
- [PostgreSQL MVCC docs](https://www.postgresql.org/docs/current/mvcc.html)
- [RxDB Supabase replication plugin](https://rxdb.info/replication-supabase.html)
- [CRDTs vs Event Sourcing — TheOptimizationKing, Nov 2025](https://medium.com/@optimzationking2/crdts-vs-event-sourcing-the-architecture-war-that-will-define-the-next-10-years-ae8245cd2ac9)
- [The CRDT Dictionary — Ian Duncan, Nov 2025](https://www.iankduncan.com/engineering/2025-11-27-crdt-dictionary/)
- [PostgreSQL audit trigger wiki](https://wiki.postgresql.org/wiki/Audit_trigger_91plus)
- [Continue.dev rules docs](https://docs.continue.dev/customize/deep-dives/rules)
- [Cursor sync forum thread](https://forum.cursor.com/t/sync-or-export-import-agent-chat-history-across-devices/152507)
- [Obsidian sync options (Stephan Miller)](https://www.stephanmiller.com/sync-obsidian-vault-across-devices/)
- [Obsidian-iCloud plugin (mnott)](https://github.com/mnott/Obsidian-iCloud)
- [PowerShell about_Character_Encoding (MS Learn)](https://learn.microsoft.com/en-us/powershell/module/microsoft.powershell.core/about/about_character_encoding?view=powershell-7.6)
- [GitHub: configuring git line endings](https://docs.github.com/en/get-started/git-basics/configuring-git-to-handle-line-endings)
- [gitattributes docs](https://git-scm.com/docs/gitattributes)
- [NixOS Wiki: Nix Installation Guide](https://nixos.wiki/wiki/Nix_Installation_Guide)
- [stow-rs (cross-platform stow alternative)](https://github.com/0xErgod/stow-rs)
- [HN: I evaluated Stow + Chezmoi, settled on YADM](https://news.ycombinator.com/item?id=39975247)
- [BigGo: YADM vs Chezmoi vs Nix debate](https://biggo.com/news/202412191324_dotfile-management-tools-comparison)
