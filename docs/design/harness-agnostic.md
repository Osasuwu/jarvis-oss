# Harness-agnostic substrate — design

**Purpose.** Jarvis is currently welded to one agent harness (Claude Code). This document
defines the seam that lets the same instance run on another harness — OpenCode, Codex CLI,
or a future one — without rewriting skills, hooks, memory, or the reactive core. It is the
architectural narrative; rationale for individual calls lives in the cited `decision_made`
episodes.

**Why now, and why YAGNI does not apply.** The counting unit for abstraction in this repo is
*readers who could depend on the code*, not implementations the principal runs
(`CLAUDE.md` → §Project, decision `855188a3-c71f-4e86-a412-9a07b76f19df`). `jarvis-oss` is a
public template whose operators do not all hold a Claude subscription; a harness-locked
template is a template only for people who already bought the same tool. Portability is also
the principal's own exit option from a single vendor's pricing. Both are freedom-of-choice
goals, which is exactly the class of goal YAGNI does not govern.

**Non-goal.** Feature parity across harnesses. Harnesses differ in what they can do at all
(see §Capability registry). The goal is that Jarvis *runs, degrades legibly, and says which
capability is missing* — not that every harness gets every behaviour.

**Read order.** `CONTEXT.md` glossary → this doc → §Slices for the shipping order.

---

## Architectural commitments

1. **One neutral core, N thin adapters.** All Jarvis logic — skills, hook script bodies,
   memory, orchestrator, dispatch — stays harness-neutral. Everything a harness needs to
   know is expressed as a *rendering* of a neutral source, produced by the installer, or as
   an *adapter call* at one of the seams below. No `if harness == "claude"` branches
   scattered through business logic.

2. **The seam is `scripts/lib/harness/`.** A single module family owns harness identity,
   home directory, binary resolution, spawn argv, permission-vocabulary translation,
   transcript location, and the capability registry. Every existing Claude-Code literal in
   code migrates behind it. Detection: `$JARVIS_HARNESS` (explicit) → autodetect → default
   `claude-code`.

3. **Missing capability degrades, never crashes.** A harness that cannot do X (statusline,
   quota probe, subagents, plugin marketplace) reports `X ∉ capabilities`; callers take a
   documented degraded path and say so. Silent no-ops are forbidden — an unavailable
   capability must be visible in the same way a failed gate is
   (`CONTEXT.md` → *Merge-gate failure axis*: absence of a signal is not a passing signal).

4. **Claude Code stays the reference implementation.** It is the harness under test on every
   CI run and the one the principal uses. A second adapter exists to prove the seam is real,
   not to be equally exercised. Adapters carry a declared support tier.

5. **Behaviour under `claude-code` is byte-identical after each slice.** Every slice is a
   refactor with respect to the current harness. Any observable change to the Claude Code
   path is a bug in the slice, and each slice's tests assert the current argv/paths verbatim.

---

## Coupling inventory (verified, not assumed)

Grepped on the branch state, 2026-08-31. Rows are call sites in live code, not mentions in prose.

| # | Seam | Where it is today | Portability today |
|---|---|---|---|
| S1 | **Instruction files** | `CLAUDE.md` (repo + `.claude-userlevel/`, collapsed by #1791 to a single bare `@AGENTS.md` import), root `AGENTS.md`, `config/SOUL.md`, `DOCTRINE.md`, `.claude/rules/*.md`, `@import` syntax | Partially solved by #1791: root `AGENTS.md` is the cross-tool standard (Linux Foundation AAIF; read by Codex, OpenCode, Cursor, Copilot, Gemini CLI, Zed, Amp) and now carries jarvis's own process rules directly, so those tools read it with no translation. `CLAUDE.md` itself, `SOUL.md`, `DOCTRINE.md`, and the `@import` syntax remain Claude Code-only. |
| S2 | **Skills** | `.claude-userlevel/skills/<name>/SKILL.md`, mirrored to `~/.claude/skills/` by `install.ps1 -Apply` | **Already portable in format.** OpenCode discovers `~/.claude/skills/`, `~/.agents/skills/`, `.claude/skills/`; frontmatter (`name`, `description`) is compatible. Residual coupling is *vocabulary* inside skill bodies (`Task tool`, `claude -p`, `/usage`, subagent type names). |
| S3 | **MCP servers** | `.claude-userlevel/.mcp.json` installed into `~/.claude.json` `mcpServers` block (`scripts/install/installer.py:384-427`) | Protocol is portable; **config shape is not**. OpenCode declares MCP under `mcp` in `~/.config/opencode/opencode.json` with a different per-server schema. |
| S4 | **Hooks** | 22 registrations across 6 Claude Code events in `.claude-userlevel/settings.json` (`SessionStart`, `UserPromptSubmit`, `PreToolUse`, `Stop`, `PreCompact`, `SessionEnd`) | **Bodies are already neutral** — Python scripts reading JSON on stdin. Coupled: (a) the registration file, (b) the event *names*, (c) the stdin/stdout **payload schema** (`hook_event_name`, `tool_name`, `tool_input`, `session_id`, `cwd` in; `permissionDecision`, `additionalContext` out). OpenCode's equivalent is a JS/TS plugin in `~/.config/opencode/plugins/` on `tool.execute.before`, `session.created`, `session.idle`, `session.compacted`. |
| S5 | **Headless spawn** | `agents/executor.py::_resolve_claude_binary` + `spawn()` argv (`claude -p <text> --permission-mode acceptEdits --allowedTools …`), `agents/task_dispatch.py:957`, `agents/plan_review_drain.py:166`, sandcastle scripts | Fully Claude-specific: binary name, flags, **and the permission vocabulary** (`Bash(git status:*)`, `Read`, `Glob` — a 28-entry allowlist). OpenCode's is `opencode run --agent --model --format json --auto` with a different permission model. |
| S6 | **Transcripts** | `~/.claude/projects/*/*.jsonl` read by `scripts/analyze-comms/extract_comms.py:12`, `scripts/comm_patterns/smoke.py:26`; deriver buffer `~/.claude/.deriver-buffer` (`scripts/deriver/pipeline.py:38`, `scripts/deriver-accumulator.py:49`); `scripts/pre-compact-backup.py`; `scripts/capture-episode.py` | Claude-specific location *and* JSONL record schema. Deepest coupling: comm-patterns, the Deriver, and compaction backup all parse it. |
| S7 | **Agent home** | Re-audited 2026-09-10: down to one inline copy, `.claude/hooks/protected-files.py:45-53` (`_user_claude_home`, used at line 71). The other four sites this row used to name are gone — `scripts/lib/recall_dedup.py` and `scripts/memory-recall-hook.py`/`scripts/pretooluse-recall-hook.py` deleted (#1865/#1870, #1800/#1801), `scripts/protected-files.py` retired in favor of the standalone `.claude/hooks/protected-files.py` (#1792/#1800), `scripts/record-decision-gate.py`'s copy removed along with its dead recall logic (#1870). A real `scripts/lib/harness/` was even built once (#1771) and then deleted along with the rest of the old hook infra (#1800). | No longer "copy-paste five times" — one site, and it's copy-paste on purpose: its docstring says "no harness seam" so it runs unmodified in CI with zero repo-internal imports. `home()` may still be worth building for S5/S6/S9's sake, but this hook may deliberately keep its own inline copy rather than adopt it. |
| S8 | **Installer** | `install.ps1` / `install.sh` / `scripts/install/installer.py`, `install-manifest.yaml` (`target_root: ~/.claude`), backup prefix `.claude.backup-`, debug-dir carve-outs | A single `target_root` already exists; it needs to become per-harness targets plus per-harness renderers for S3/S4. |
| S9 | **Quota / usage** | `scripts/sandcastle/Quota-Probe.ps1` polling `claude -p "/usage"`, `agents/usage_probe.py`, `CLAUDE_QUOTA_PRESSURE` repo variable, pre-spawn gate in `executor.spawn` | Claude-Max-specific and not emulable. The canonical *capability-absent* case (see Q1). |
| S10 | **CI review gate** | `.github/workflows/code-review.yml` → `anthropics/claude-code-action@v1` (`code-review-retry.yml`, `event-dispatch.yml`, `merge-train.yml`, `auto-merge-enable.yml` and their backing scripts were cut in #1796) | Vendor action. Portable in principle (any harness with a headless mode can post a verdict); the *verdict contract* — the comment shape parsed by the verdict guard in `code-review.yml` — is already ours and is the part that matters. |
| S11 | **Statusline** | `statusLine` key in `.claude-userlevel/settings.json` → `scripts/statusline.py` | Cosmetic; pure capability-gated feature. |
| S12 | **Model / provider literals** | `model` / `fallbackModel` in settings; `ANTHROPIC_*` / `CLAUDE_*` env sanitization in `executor._SENSITIVE_ENV_KEYS`; `scripts/lib/llm_client.py`; Ollama client | The sanitization list must become a provider-set union, not an Anthropic-only list, or a non-Anthropic harness leaks its own key into a spawned worker. |

**Two things this inventory changes about the intuition "just add interfaces everywhere".**
S2 and S3 are *cheaper than expected* — skills already port, MCP is a standard. S4 and S6 are
*more expensive than expected* — hook payload schemas and transcript formats are per-harness
data contracts, not merely paths, and three subsystems (comm-patterns, Deriver, pre-compact
backup) read the transcript format directly.

---

## Target architecture

```
        neutral source of truth                 renderers / adapters              harness
  ────────────────────────────────────    ──────────────────────────────    ──────────────────
  AGENTS.md  (+ SOUL, DOCTRINE, rules)  →  CLAUDE.md = @AGENTS.md shim   →  Claude Code
                                           AGENTS.md read natively      →  OpenCode / Codex

  .claude-userlevel/skills/**/SKILL.md  →  installer target per harness  →  ~/.claude/skills
                                                                           ~/.agents/skills

  config/mcp-servers.json               →  render_mcp(harness)           →  ~/.claude.json
                                                                           opencode.json:mcp

  config/hooks.yaml  (neutral events)   →  render_hooks(harness)         →  settings.json:hooks
   + scripts/lib/hook_io.py (payload)      + generated plugin shim          plugins/*.ts

  neutral capability spec               →  spawn_argv(harness)           →  claude -p …
   (tools + permissions, one list)                                          opencode run …
```

### The five adapter methods

`scripts/lib/harness/base.py` defines the whole runtime contract. Anything not on this list
stays in neutral code.

| Method | Returns | Consumers |
|---|---|---|
| `home()` | agent home dir (`~/.claude`, `~/.config/opencode`) | S7 — the one remaining inline copy (which may opt to stay inline; see S7 row above) |
| `binary()` | resolved executable path | S5 |
| `spawn_argv(prompt, tools, mode, model, output)` | full argv | S5 — executor, plan-review drain, sandcastle |
| `transcripts()` | root + a record-normalising reader | S6 — comm-patterns, Deriver, pre-compact |
| `capabilities()` | frozenset of capability names | S9, S11, and every degrade path |

Plus two *installer-side* renderers, which are not runtime adapter methods because they run
once at install time: `render_mcp()` (S3) and `render_hooks()` (S4).

### Capability registry

Named, checked, and reported — never inferred. Initial set:

`headless_spawn`, `subagents`, `hooks.pre_tool`, `hooks.session_start`, `hooks.user_prompt`,
`hooks.stop`, `hooks.pre_compact`, `mcp`, `skills`, `statusline`, `quota_probe`,
`permission_allowlist`, `transcript_jsonl`, `plugin_marketplace`.

`claude-code` holds all of them. `opencode` — from published docs, to be verified against a
live install in Slice 6 — holds `headless_spawn`, `subagents`, `mcp`, `skills`,
`hooks.pre_tool` (`tool.execute.before`), `hooks.session_start` (`session.created`),
`hooks.stop` (`session.idle`), `hooks.pre_compact` (`session.compacted`), and **not**
`hooks.user_prompt`, `quota_probe`, `statusline`, `permission_allowlist` (different model),
`transcript_jsonl` (different store).

`hooks.user_prompt` being absent is the one that costs real behaviour:
`memory-recall-hook.py` (UserPromptSubmit recall) has no OpenCode event with the same trigger
point. Documented degrade: recall moves to `tool.execute.before` on the first tool call of a
turn, which fires later and misses a turn that uses no tools. That is a *stated* loss, not a
silent one.

---

## Slices

One PR each, in dependency order. Slices 1–5 are refactors under `claude-code` and carry no
behaviour change; 6 onward add the second adapter.

1. **S7 — agent-home `home()`.** `scripts/lib/harness/` with `home()` + detection. Down to one
   inline `JARVIS_CLAUDE_HOME` copy now (`.claude/hooks/protected-files.py`), which may stay
   inline on purpose — it's the standalone CI-hook surface, deliberately free of repo-internal
   imports. So this slice is now about building `home()` for S5/S6/S9's sake, not about
   erasing copy-paste. (A prior attempt, #1771, built exactly this and was deleted along with
   the rest of the old hook infra in #1800 — re-check that history before re-landing it.)
   Cheapest slice, no longer the broadest. Tests: each remaining/former call site resolves
   identically with and without the override.
2. **S5 — spawn adapter.** Move binary resolution, argv construction, permission vocabulary,
   and env sanitization behind `spawn_argv()`. Neutral tool spec plus a Claude renderer that
   reproduces the current 28-entry allowlist verbatim. Tests assert argv equality against the
   current literal.
3. **S4a — hook payload normalisation.** `scripts/lib/hook_io.py`: read/write the neutral
   hook payload; the Claude Code adapter is the identity mapping. Hook scripts stop touching
   the raw schema.
4. **S1 — instruction files.** `AGENTS.md` becomes canonical at repo root and in
   `.claude-userlevel/`; `CLAUDE.md` becomes a shim that `@import`s it plus a short
   Claude-only addendum. Guard test: the shim resolves, and no rule text exists only in the
   shim.
5. **S3 + S4b — installer renderers.** `config/mcp-servers.json` and `config/hooks.yaml` as
   neutral sources; `render_mcp()` / `render_hooks()` per harness; installer grows
   `--harness`. Claude Code output must be byte-identical to today's.
6. **OpenCode adapter, verified live.** Adapter, generated plugin shim, and capability set,
   validated against a real install rather than against docs. Publishes the degrade table.
7. **S6 — transcript abstraction.** `transcripts()` reader; port comm-patterns, the Deriver
   buffer, and pre-compact backup. Largest and riskiest — deliberately last.
8. **S9/S11/S12 — capability-gated tail.** Quota probe, statusline, provider-set env
   sanitization; each becomes a declared capability with a degrade path.
9. **S10 — CI review gate parameterisation.** Only after 1–8; the verdict contract is already
   ours, the action is the swappable part.

Ordering rationale: S7 and S5 come first — S7 no longer for breadth (down to one call site that
may stay inline), but because both create the module every later slice imports; the two
data-contract slices (S4a, S6) are separated by four slices so the risky one lands against an
established seam.

---

## jarvis-oss propagation

`jarvis-oss` is a squashed downstream mirror, refreshed by the graft-and-merge recipe in its
`docs/UPSTREAM-SYNC.md` — one commit per release, upstream content wins on conflict. So **work
lands in `jarvis` and reaches the template at the next sync**; there is no second
implementation to write. Two additions the sync procedure needs, made as part of Slice 5:

- The de-personalization tolerance bar gains a harness clause: a fresh clone must not
  *require* Claude Code. Harness-specific values in live surfaces (binary names, `~/.claude`
  targets, vendor CI actions) must resolve through the adapter or through operator config —
  the same rule already applied to repo slugs and device names.
- `ONBOARDING.md` / `SETUP.md` gain a harness-selection step, since the template's audience is
  exactly the set of operators who may not have the reference harness.

---

## Open questions

**Q1 — quota-gate direction when `quota_probe` is absent.** `executor.spawn` currently refuses
to spawn when the probe errors (fail-safe toward "don't burn quota"). On a harness with no
probe at all, that reading would refuse *every* spawn. Absent ≠ error, so the two need
different handling — but "absent → always spawn" removes a real guardrail for operators on
metered billing. Leaning: absent → allow, and require the adapter to declare a billing model
(`subscription` / `metered` / `local`), with metered defaulting to a conservative cap. Decide
in Slice 8, not before.

**Q2 — how far does skill-body vocabulary genericisation go?** Skill prose says "the Task
tool", "`claude -p`", "subagent_type". Rewriting ~30 skills to neutral vocabulary is a large
prose diff with real regression risk against a working roster. Leaning: a glossary layer
(neutral term → per-harness term) applied only where a skill *instructs an action*, leaving
narrative mentions alone — the same "executes or resolves vs. narrates" rule the OSS tolerance
bar already uses.

**Q3 — support tier for the second adapter.** Reference (CI-tested every run) vs. best-effort
(smoke-tested on release) vs. community. Affects whether OpenCode gets a CI lane. Leaning:
best-effort with a release-gated smoke, because a full second CI lane doubles the gate surface
for one operator's benefit.
