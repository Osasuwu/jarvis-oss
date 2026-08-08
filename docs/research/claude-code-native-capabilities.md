# Claude Code — Native Capabilities & Limitations Reference

> **Purpose.** Single source of truth for *what is native in Claude Code*, so we can honour the **native-first** rule (check skills / hooks / MCP / subagents / built-in tools before writing custom Python or reaching for external services) without re-searching the docs each time.
>
> **Scope.** Compiled from the official Anthropic Claude Code documentation (`code.claude.com/docs`), the public CHANGELOG, and the Agent SDK docs.
>
> **Version covered.** Claude Code **v2.1.148** (released 2026-05-22). **Compiled:** 2026-05-23.
>
> **Maintenance.** This is a point-in-time snapshot. Claude Code ships almost daily — treat anything version-pinned below as "true as of 2.1.148", and re-verify against `/release-notes` or the [changelog](https://code.claude.com/docs/en/changelog.md) before relying on a recent feature. When this drifts badly, re-run the research rather than patching piecemeal.

---

## How to use this document

- **Picking a mechanism?** Jump to the part that matches the extension surface: Part 1 (skills / commands / plugins / output styles), Part 2 (hooks / settings / permissions), Part 3 (subagents / MCP / headless / SDK / CI), Part 4 (memory / sessions / interfaces / built-in tools / models).
- **"Can Claude Code do X natively?"** — search this doc first. If it's not here and not in `/release-notes`, assume it needs an extension (skill/hook/MCP) or isn't supported.
- Each part lists **limitations / gotchas** for its area. A consolidated cross-cutting limitations list lives in Part 4.

## Table of contents

- **Part 1 — Customization surface:** Agent Skills · Slash Commands · Plugins · Output Styles
- **Part 2 — Control plane:** Hooks (all events, inputs, outputs, handler types) · settings.json hierarchy · Permissions · Environment variables
- **Part 3 — Agents & automation:** Subagents · **Agent Teams** (multi-session orchestration) · MCP · Headless/CLI · Agent SDK (Python/TS) · CI/CD (GitHub Actions, GitLab)
- **Part 4 — Runtime & surfaces:** Memory & context (CLAUDE.md, auto-memory, compaction) · Background tasks & sessions · Interfaces (CLI, VS Code, JetBrains, Desktop, Web) · Built-in tools · Models / effort / fast mode / plan mode / checkpointing · Cross-cutting limitations · Changelog highlights
- **Part 5 — Additional native surfaces:** Channels (event push — our Telegram) · Agent View · Worktrees · Remote Control · Sandboxing · Computer use · Chrome/browser · Goal mode · Routines · Slack
- **Sources** — consolidated doc URLs + verification notes

---

---

# Part 1 — Customization Surface: Skills, Slash Commands, Plugins, Output Styles

## 1. Agent Skills

**What skills are:** Reusable, prompt-based task procedures that Claude loads when relevant. Unlike built-in commands (fixed logic), skills give Claude detailed instructions and let it orchestrate work using available tools.

**Key distinction:** Skills are NOT the same as slash commands. A skill is a `.md` file that Claude reads; a slash command is built-in logic. Both create invocable `/name` entries, but skills also load automatically when their `description` matches your request.

### File Format and Location

Skills live in `SKILL.md` files inside named directories. The directory name becomes the command name and appears as `/skill-name` or `/plugin-name:skill-name` (for plugin skills).

**Where skills live (precedence order, highest to lowest):**

1. **Enterprise (managed settings):** Set by admin policy, applies org-wide
2. **Personal:** `~/.claude/skills/<skill-name>/SKILL.md` — available in all projects
3. **Project:** `.claude/skills/<skill-name>/SKILL.md` — available in this project only
4. **Plugin:** `<plugin>/skills/<skill-name>/SKILL.md` — scoped by plugin namespace
5. **Additional directories:** `.claude/skills/` within directories added via `--add-dir` (discovered on demand)

When skills share the same name, higher precedence levels override lower ones. Plugin skills use `plugin-name:skill-name` namespace and do not conflict.

**Live change detection:** Adding, editing, or removing skills in personal, project, or `--add-dir` paths takes effect within the current session without restart. Creating a new top-level `.claude/skills/` directory that didn't exist at startup requires restart.

### SKILL.md Structure

Every skill requires a `SKILL.md` file with two parts: YAML frontmatter (between `---` markers) and markdown content (instructions Claude follows when the skill runs).

```yaml
---
description: Summarizes uncommitted changes and flags anything risky
allowed-tools: Bash(git diff *)
model: inherit
disable-model-invocation: false
---

## Current changes
!`git diff HEAD`

## Instructions
Summarize changes in 2–3 bullets, then list risks...
```

### Frontmatter Fields

| Field | Required | Type | Description |
| :--- | :--- | :--- | :--- |
| `name` | No | string (max 64 chars) | Display name; if omitted, uses directory name. Lowercase letters, numbers, hyphens only |
| `description` | Recommended | string | When to use the skill. Claude uses this to decide auto-invocation. First 1,536 chars count toward skill listing budget |
| `when_to_use` | No | string | Additional auto-invocation context (trigger phrases). Appended to `description`, shares 1,536-char budget |
| `argument-hint` | No | string | Hint shown in `/` autocomplete, e.g. `[issue-number]` |
| `arguments` | No | string or list | Named positional arguments for `$name` substitution |
| `disable-model-invocation` | No | boolean | `true` = only user can invoke (prevents auto-load). For side-effecting workflows. Default `false` |
| `user-invocable` | No | boolean | `false` = only Claude can invoke (hidden from `/` menu). For background knowledge. Default `true` |
| `allowed-tools` | No | string or list | Tools usable without permission prompt while skill active. Format `Tool(argument-pattern *)` |
| `model` | No | string | Model when skill active. Model ID, or `inherit`. One-turn override |
| `effort` | No | string | `low`/`medium`/`high`/`xhigh`/`max`. Overrides session effort. Levels depend on model |
| `context` | No | string | Set to `fork` to run in a forked subagent context (isolated from session history) |
| `agent` | No | string | Subagent type when `context: fork`: `Explore`, `Plan`, `general-purpose`, or custom. Default `general-purpose` |
| `hooks` | No | YAML object | Hooks scoped to this skill's lifecycle (same format as hooks config) |
| `paths` | No | string or list | Glob patterns limiting when skill auto-activates, e.g. `src/**/*.ts,tests/**/*.ts` |
| `shell` | No | string | Shell for `` !`command` `` blocks: `bash` (default) or `powershell`. PowerShell requires `CLAUDE_CODE_USE_POWERSHELL_TOOL=1` |

### String Substitutions in Skill Content

| Variable | Expands to |
| :--- | :--- |
| `$ARGUMENTS` | All arguments passed. If unused, appended as `ARGUMENTS: <value>` |
| `$ARGUMENTS[N]` | Argument at 0-based index `N` |
| `$N` | Shorthand: `$0`, `$1`, `$2` for argument positions |
| `$name` | Named argument declared in `arguments` frontmatter |
| `${CLAUDE_SESSION_ID}` | Current session ID (UUID) |
| `${CLAUDE_EFFORT}` | Current effort level |
| `${CLAUDE_SKILL_DIR}` | Directory containing the skill's `SKILL.md` (resolves at personal/project/plugin levels) |

Arguments use shell-style quoting: `/skill "hello world" second` → `$0`=`hello world`, `$1`=`second`. `$ARGUMENTS` always expands to the full string as typed.

### Dynamic Context Injection

Commands prefixed with `!` execute immediately before Claude sees the skill content:

**Inline:** `` !`git status --short` ``  (must be at line start or after whitespace; `` KEY=!`cmd` `` does NOT expand)

**Fenced (multi-line):**
````markdown
```!
node --version
git status --short
```
````

Runs **once** at skill load time; substitution is preprocessing, not something Claude executes. **Disable** with `"disableSkillShellExecution": true` in settings (bundled/managed skills unaffected).

### Supporting Files

A skill directory can contain `SKILL.md` (required) plus reference files, examples, scripts. Reference them from `SKILL.md` so Claude knows when to load them (progressive disclosure). Keep `SKILL.md` under 500 lines; move detail to separate files.

### Controlling Skill Invocation

| Frontmatter | You can `/name` | Claude auto-loads | In context description |
| :--- | :--- | :--- | :--- |
| (default) | Yes | Yes | Yes |
| `disable-model-invocation: true` | Yes | No | No |
| `user-invocable: false` | No | Yes | Yes |

### Skill Content Lifecycle

When invoked, the rendered `SKILL.md` content enters the conversation as a single message and **stays there for the rest of the session** — Claude Code does NOT re-read the skill file on later turns. Auto-compaction carries forward the most recent invocation of each skill (first 5,000 tokens per skill, shared 25,000-token budget); older invocations drop. Re-invoke after compaction to restore full content.

### Pre-approving Tools

`allowed-tools` grants permission for listed tools while the skill is active without per-use prompts. It does NOT restrict baseline tool access (permission settings still govern that), and deny rules still override it.

### Context Cost / Budget

Skill **names** are always in context; **descriptions** consume tokens. Budget = 1% of model context window. On overflow, least-used skills' descriptions drop first. Raise via `skillListingBudgetFraction` (e.g. `0.02`) or `SLASH_COMMAND_TOOL_CHAR_BUDGET`. Trim via 1,536-char `description`+`when_to_use` cap, or set low-priority skills to `"name-only"` in `skillOverrides`.

---

## 2. Slash Commands

**Built-in commands** are fixed-logic CLI operations invoked with `/` at the start of a message. **Custom commands** are flat `.md` files in `.claude/commands/` that become `/name` entries and support the same frontmatter as skills (and dynamic `` !`command` `` injection). `.claude/commands/deploy.md` ≡ `.claude/skills/deploy/SKILL.md`, except skills are directories with supporting files. New projects should prefer skills.

### Built-in Commands Reference (v2.1.148)

> Entries marked **[Skill]** are bundled skills, not hard-coded logic.

| Command | Purpose | Notes |
| :--- | :--- | :--- |
| `/add-dir <path>` | Add a working directory for file access | `.claude/` config not discovered from added dir |
| `/agents` | Manage subagent configs / view agent tree | |
| `/autofix-pr [prompt]` | Spawn web session watching the branch's PR for CI/review failures | Needs `gh` + web access |
| `/background [prompt]` | Detach session to run as background agent | Alias `/bg`; monitor with `claude agents` |
| `/batch <instruction>` **[Skill]** | Orchestrate large-scale changes across 5–30 worktrees | Opens PRs per unit |
| `/branch [name]` | Branch the conversation; original kept for `/resume` | Alias `/fork` |
| `/btw <question>` | Quick side question without bloating history | |
| `/claude-api [sub]` **[Skill]** | Load Claude API reference / migrations / Managed Agents setup | `migrate`, `managed-agents-onboard` |
| `/clear [name]` | New conversation, empty context; label previous for `/resume` | Alias `/reset`, `/new` |
| `/code-review [effort] [--comment] [target]` **[Skill]** | Review diff for bugs without editing; post inline PR comments | Formerly `/simplify` |
| `/compact [instructions]` | Summarize conversation to free context | Skills & CLAUDE.md survive |
| `/config` | Open Settings UI | Alias `/settings` |
| `/context [all]` | Visualize context usage grid + optimization tips | |
| `/copy [N]` | Copy last (or Nth-latest) response to clipboard | |
| `/debug [description]` **[Skill]** | Enable debug logging, troubleshoot mid-session | |
| `/desktop` | Continue session in Desktop app | macOS/Windows + subscription |
| `/diff` | Interactive diff viewer (git + per-turn) | |
| `/doctor` | Diagnose install + settings; press `f` to auto-fix | |
| `/effort [level\|auto]` | Set reasoning effort | Slider if no arg |
| `/exit` | Exit CLI | Alias `/quit` |
| `/export [filename]` | Export conversation as text | |
| `/fast [on\|off]` | Toggle fast mode (cheaper/faster Opus) | See fast-mode |
| `/feedback [report]` | Submit feedback / bug / share | Alias `/bug`, `/share` |
| `/fewer-permission-prompts` **[Skill]** | Build allowlist for common read-only tools | |
| `/focus` | Toggle one-line tool output summaries | Fullscreen only |
| `/goal [condition\|clear]` | Set a goal; Claude works until met | Autonomous looping |
| `/help` | Show help | |
| `/hooks` | View hook configs | |
| `/ide` | Manage IDE integrations + status | |
| `/init` | Generate starter `CLAUDE.md`; `CLAUDE_CODE_NEW_INIT=1` for interactive | |
| `/insights` | Report analyzing your sessions | |
| `/install-github-app` | Set up Claude GitHub Actions app | |
| `/install-slack-app` | Install Claude Slack app (OAuth) | |
| `/keybindings` | Open/create keybindings config | |
| `/login` `/logout` | Sign in / out | |
| `/loop [interval] [prompt]` **[Skill]** | Repeat a prompt while session open | Omit interval for self-paced |
| `/mcp` | Manage MCP servers + OAuth | |
| `/memory` | Edit CLAUDE.md, toggle/view auto-memory | |
| `/mobile` | QR to download mobile app | Alias `/ios`, `/android` |
| `/model [model]` | Set model for session; `d` to save default | One-turn override |
| `/permissions` | Manage allow/deny/ask rules | Alias `/allowed-tools` |
| `/plan [description]` | Enter plan mode | |
| `/plugin` | Manage plugins (install/enable/inventory) | |
| `/powerup` | Interactive feature lessons | |
| `/recap` | One-line session summary | |
| `/release-notes` | Interactive changelog picker | |
| `/reload-plugins` | Reload plugins without restart | |
| `/remote-control` | Make session controllable from claude.ai | Alias `/rc` |
| `/remote-env` | Configure default remote env for `--remote` | |
| `/rename [name]` | Rename session | |
| `/resume [session]` | Resume by ID/name; picker if no arg | Alias `/continue` |
| `/review [PR]` | Review a PR locally | Deeper: `/ultrareview` |
| `/rewind` | Rewind code + conversation | Alias `/checkpoint`, `/undo` |
| `/run` **[Skill]** | Launch/drive app; confirm change works | v2.1.145+ |
| `/run-skill-generator` **[Skill]** | Teach `/run` & `/verify` to build/launch your project | v2.1.145+ |
| `/sandbox` | Toggle sandbox mode (if supported) | |
| `/schedule [description]` | Create/list/run cloud routines (cron) | Alias `/routines` |
| `/security-review` | Analyze pending changes for vulns | |
| `/setup-bedrock` `/setup-vertex` | Configure Bedrock / Vertex auth | Requires the env flag |
| `/skills` | List skills; sort by tokens | |
| `/status` | Version, model, account, connectivity | |
| `/statusline` | Configure status line | |
| `/stop` | Stop current background session | |
| `/tasks` | List/manage background tasks | Alias `/bashes` |
| `/team-onboarding` | Generate onboarding guide from last 30d of sessions | |
| `/teleport` | Pull a web session into this terminal | Alias `/tp` |
| `/theme` | Change color theme (built-in + `~/.claude/themes/`) | |
| `/tui [default\|fullscreen]` | Set terminal UI renderer | |
| `/ultraplan <prompt>` | Draft plan in browser, then execute | |
| `/ultrareview [PR]` | Deep multi-agent cloud code review | 3 free runs (Pro/Max) then credits |
| `/upgrade` | Open upgrade page | |
| `/usage` | Session cost, plan limits, stats | Alias `/cost`, `/stats` |
| `/usage-credits` | Configure usage credits | Formerly `/extra-usage` |
| `/verify` **[Skill]** | Confirm change works by building/running | v2.1.145+ |
| `/voice [hold\|tap\|off]` | Voice dictation | Requires claude.ai account |
| `/web-setup` | Connect GitHub via local `gh` creds | |

### MCP Prompts as Commands

MCP servers can expose prompts as commands: `/mcp__<server>__<prompt>`, discovered dynamically from connected servers.

### Argument Patterns (custom commands)

`<arg>` = required, `[arg]` = optional. Parsed as space-separated tokens; wrap multi-word values in quotes.

---

## 3. Plugins

**What plugins are:** Packaged bundles of skills, agents, hooks, MCP servers, LSP servers, settings, and executables — for distribution/reuse across projects and teams, with versioned releases and marketplace installation.

### Plugin Structure

```
my-plugin/
├── .claude-plugin/
│   └── plugin.json           (required: manifest — ONLY this goes here)
├── skills/<skill-name>/SKILL.md
├── agents/<agent-name>.md
├── hooks/hooks.json
├── commands/<command-name>.md     (legacy; prefer skills/)
├── .mcp.json                      (MCP servers)
├── .lsp.json                      (LSP servers — require binaries on user's machine)
├── monitors/monitors.json         (background monitors)
├── bin/                           (executables added to Bash PATH)
├── output-styles/                 (custom output styles)
├── settings.json                  (default settings on enable)
└── README.md
```

**Important:** Only `plugin.json` goes inside `.claude-plugin/`. Everything else lives at plugin root.

### Manifest (`plugin.json`)

| Field | Required | Description |
| :--- | :--- | :--- |
| `name` | Yes | Unique id + skill namespace (`/plugin-name:skill-name`). Lowercase, alphanumeric, hyphens |
| `description` | Yes | Shown in plugin manager |
| `version` | No | semver. If set, users get updates only on bump; if omitted, git SHA = version |
| `author` / `homepage` / `repository` / `license` | No | Metadata |

Dependencies: declare in `dependencies` (with optional `{version, optional:true}`); auto-install on install; conflicts enforced by the plugin manager.

### What Plugins Can Bundle

Skills · Agents (subagent defs) · Hooks (`hooks/hooks.json`) · MCP servers (`.mcp.json`) · LSP servers (`.lsp.json`) · Background monitors (`monitors/monitors.json`) · Default settings (`settings.json` — currently `agent` to activate a plugin agent as main, and `subagentStatusLine`) · Executables (`bin/` → Bash `PATH`) · Output styles.

### Create / Test / Install

```bash
# create
mkdir my-plugin/.claude-plugin   # + plugin.json + skills/...
# test locally
claude --plugin-dir ./my-plugin
# load from URL (CI artifacts)
claude --plugin-url https://example.com/my-plugin.zip
# install from marketplace
/plugin install plugin-name
# reload mid-session
/reload-plugins
# inspect
claude plugin details plugin-name      # components + estimated context cost
claude plugin validate                 # run before submitting
```

### Publishing

Host in a Git repo with `.claude-plugin/marketplace.json`; others add via `/plugin marketplace add <url>`. Official marketplaces: `claude-plugins-official` (curated) and `claude-community` (public, reviewed). Private: share a private repo's marketplace URL.

---

## 4. Output Styles

**What they are:** Customizations to Claude's **system prompt** that change role, tone, and response format — but NOT what Claude knows. Use when you repeatedly re-prompt for the same voice/format, or for non-software-engineering roles.

**vs CLAUDE.md:** Output styles modify the system prompt globally and apply every turn; CLAUDE.md is a user message loaded after the system prompt providing persistent project context.

### Built-in Output Styles

1. **Default** — standard software-engineering instructions.
2. **Proactive** — execute immediately, reasonable assumptions, action over planning (still shows permission prompts).
3. **Explanatory** — educational "Insights" between coding tasks.
4. **Learning** — collaborative learn-by-doing; adds `TODO(human)` markers for you to implement.

### Changing / Creating

- UI: `/config → Output style`. Settings: `"outputStyle": "Explanatory"` in `.claude/settings.local.json`. Takes effect after `/clear` or new session (invalidates prompt cache).
- Custom: save `.md` at `~/.claude/output-styles/`, `.claude/output-styles/`, or managed dir. Filename = style name unless `name` set.

### Frontmatter

| Field | Type | Description |
| :--- | :--- | :--- |
| `name` | string | Display name |
| `description` | string | Shown in `/config` picker |
| `keep-coding-instructions` | boolean | Keep built-in SWE instructions. Default `false` |
| `force-for-plugin` | boolean | (Plugin styles) apply automatically when plugin enabled. Default `false` |

Set `keep-coding-instructions: true` when changing *how* Claude communicates but keeping coding behavior; leave `false` when Claude won't be doing software engineering at all.

### Related-feature comparison

| Feature | Mechanism | Use when |
| :--- | :--- | :--- |
| Output styles | Modifies system prompt globally | Different role/tone/format every turn |
| CLAUDE.md | User message after system prompt | Persistent project conventions |
| `--append-system-prompt` | One-off system prompt addition | Single invocation |
| Agents | Separate system prompt + tools + model | Focused helper, different scope |
| Skills | Task-specific instructions loaded on use | Reusable workflow / reference |

---

## 5. Part 1 Limitations & Gotchas

**Skills:** `description`+`when_to_use` capped 1,536 chars/skill; descriptions are budget-limited and dropped least-used-first on overflow; new top-level `.claude/skills/` dir needs restart (edits to existing don't); `context: fork` skills lack conversation history (need explicit instructions); `` !`command` `` runs at load time, output not re-scanned.

**Commands:** only recognized at message start; availability varies by platform/plan (`/desktop` needs macOS/Win+sub, `/upgrade` Pro/Max only); `/model` & `max` effort are one-turn/session-only.

**Plugins:** plugin skills are namespaced `plugin-name:skill-name` (no short form); dependency conflicts block install (use `optional`); LSP components need language-server binaries installed; managed settings can force-enable/disable plugins (overrides `--plugin-dir`/`--plugin-url`).

**Output styles:** replace (not append) the default system prompt — built-in SWE instructions lost unless `keep-coding-instructions: true`; changing style invalidates the prompt cache; verbose styles (Explanatory/Learning) cost more output tokens.

**Skill/command precedence on name clash:** Enterprise → Personal (`~/.claude/skills/`) → Project (`.claude/skills/`) → Plugin (namespaced). Skill beats command on same name. `--plugin-dir` local copy beats installed copy unless managed forces it.

---

# Part 2 — Control Plane: Hooks, Settings, Permissions, Environment Variables

**Version**: Claude Code v2.1.145+ (Documentation last updated: 2026-05-23)  
**Official Source**: https://code.claude.com/docs

This is a comprehensive internal reference documenting native Claude Code capabilities for hooks, settings, and permissions. It is organized by major system component with emphasis on completeness and accuracy.

---

## 1. Hooks System

### 1.1 Overview

Hooks are automation triggers that execute at specific lifecycle points during Claude Code sessions. They run as shell commands, HTTP endpoints, LLM prompts, or MCP tools and receive JSON context about the event via stdin.

**Key principle**: Hooks are **enforcement mechanisms**, not instruction channels. They run regardless of conversation context and can block or allow tool calls, though permission deny rules always take precedence.

**Source**: [Hooks Guide](https://code.claude.com/docs/en/hooks-guide.md), [Settings](https://code.claude.com/docs/en/settings.md)

### 1.2 Hook Lifecycle Events (Complete List)

| Event | Cadence | When It Fires | Blockable | Matcher Examples |
|-------|---------|---------------|-----------|------------------|
| **SessionStart** | Once per session | Session begins, resumes, or after `/clear` or compaction | No | `startup`, `resume`, `clear`, `compact` |
| **UserPromptSubmit** | Once per turn | User submits a prompt, before Claude processes it | Yes | Omit matcher (applies to all prompts) |
| **PreToolUse** | Every tool call | Before a tool executes (can block it) | Yes | `Bash`, `Edit`, `Write`, `Read`, `Glob`, `Grep`, `Agent`, MCP tools like `mcp__server__tool` |
| **PostToolUse** | Every successful tool call | After a tool succeeds | Partial (can block continuing with tool result) | Same as PreToolUse |
| **PostToolUseFailure** | Every failed tool call | After a tool fails with error | Partial | Same as PreToolUse |
| **PostToolBatch** | After parallel batch | After all parallel tool calls complete, before next model call | Yes | Omit matcher |
| **Stop** | Once per turn | When Claude finishes responding (all tools done, about to stop) | Yes | Omit matcher |
| **SubagentStop** | When subagent ends | When a subagent finishes | Yes | Agent type: `general-purpose`, `Explore`, `Plan`, custom agent name |
| **StopFailure** | On API error | When a turn ends due to API failure | No (informational) | Error type: `rate_limit`, `authentication_failed`, `billing_error`, `server_error`, `context_window_exceeded`, `internal_error` |
| **SessionEnd** | Once per session | Session terminates | No (post-hoc) | End reason: `clear`, `resume`, `logout`, `other` |
| **PermissionRequest** | On permission prompt | When Claude Code needs user approval for a tool | Partial | Tool name: `Bash`, `Edit`, etc. |
| **PermissionDenied** | On denied tool | When a tool is denied by permissions or hook | Partial | Same as PermissionRequest |

**Timeout defaults** (applies if hook exceeds time):
- Most events: **600 seconds**
- `UserPromptSubmit`: **30 seconds** (must be fast)

### 1.3 Hook Input Schema

All hook inputs arrive as JSON on stdin with these base fields:

```json
{
  "hook_event_name": "SessionStart|PreToolUse|...",
  "session_id": "unique-session-uuid",
  "cwd": "/absolute/working/directory",
  "permission_mode": "default|acceptEdits|plan|auto|dontAsk|bypassPermissions"
}
```

#### SessionStart Input
```json
{
  "hook_event_name": "SessionStart",
  "session_id": "abc123",
  "transcript_path": "/path/to/transcript.jsonl",
  "cwd": "/current/working/directory",
  "source": "startup|resume|clear|compact",
  "model": "claude-sonnet-4-6",
  "effort": {
    "level": "low|medium|high|xhigh"
  }
}
```

**Matcher values**: `startup` (new), `resume` (resumed session), `clear` (after `/clear`), `compact` (after compaction)

#### UserPromptSubmit Input
```json
{
  "hook_event_name": "UserPromptSubmit",
  "session_id": "abc123",
  "cwd": "/current/directory",
  "permission_mode": "default",
  "prompt": "User's text input here"
}
```

#### PreToolUse Input
```json
{
  "hook_event_name": "PreToolUse",
  "session_id": "abc123",
  "tool_name": "Bash|Edit|Write|Read|Glob|Grep|Notebook*|Agent|mcp__server__tool",
  "tool_use_id": "unique-id-for-this-call",
  "tool_input": {
    "command": "npm test",           // Bash only
    "file_path": "/path/to/file",   // Read, Edit, Write only
    "description": "description",     // All tools
    "timeout": 120000,                // All tools (milliseconds)
    "run_in_background": false        // Bash only
  }
}
```

**Tool-specific `tool_input` fields**:
- **Bash**: `command` (string), `description`, `timeout`, `run_in_background`
- **Read/Edit/Write/Glob/Grep**: `file_path` (string), `description`, `timeout`
- **Notebook***: `file_path`, `description`, `timeout`
- **Agent**: `agent_name`, `prompt`, `description`, `timeout`
- **MCP tools**: `input` (object per tool definition), `description`, `timeout`

#### PostToolUse Input (extends PreToolUse)
```json
{
  "hook_event_name": "PostToolUse",
  "tool_name": "Bash",
  "tool_input": { /* same as PreToolUse */ },
  "tool_use_id": "unique-id",
  "tool_result": {
    "type": "text|error",
    "content": "command output or error message",
    "duration_ms": 1234
  }
}
```

**PostToolBatch Input** (after all parallel calls):
```json
{
  "hook_event_name": "PostToolBatch",
  "session_id": "abc123",
  "tool_batch": [
    { "tool_name": "Bash", "tool_input": {...}, "tool_result": {...} },
    { "tool_name": "Edit", "tool_input": {...}, "tool_result": {...} }
  ]
}
```

#### Stop Input
```json
{
  "hook_event_name": "Stop",
  "session_id": "abc123",
  "tool_use_count": 5,
  "background_tasks": ["task1", "task2"],
  "session_crons": ["*/5 * * * *"],
  "effort": { "level": "high" }
}
```

#### SubagentStop Input
```json
{
  "hook_event_name": "SubagentStop",
  "session_id": "abc123",
  "subagent_name": "code-reviewer",
  "subagent_type": "general-purpose|Explore|Plan|custom",
  "exit_code": 0,
  "background_tasks": [],
  "session_crons": []
}
```

#### StopFailure Input
```json
{
  "hook_event_name": "StopFailure",
  "session_id": "abc123",
  "error_type": "rate_limit|authentication_failed|billing_error|server_error|context_window_exceeded|internal_error",
  "error_message": "Human-readable error description"
}
```

#### SessionEnd Input
```json
{
  "hook_event_name": "SessionEnd",
  "session_id": "abc123",
  "end_reason": "clear|resume|logout|other",
  "transcript_path": "/path/to/transcript.jsonl",
  "token_usage": {
    "input_tokens": 50000,
    "output_tokens": 5000,
    "cache_creation_tokens": 0,
    "cache_read_tokens": 10000
  }
}
```

### 1.4 Hook Output Schema

Hook outputs are JSON written to stdout. Exit code and JSON output are processed separately.

#### Standard Output Fields (All Events)

```json
{
  "decision": "allow|deny|ask|defer|block",
  "reason": "Human-readable explanation",
  "continue": true,
  "suppressOutput": false,
  "systemMessage": "Optional message shown to user",
  "terminalSequence": "\033]777;notify;Title;Body\007",
  "hookSpecificOutput": {
    "hookEventName": "PreToolUse|...",
    // ... event-specific fields below
  }
}
```

**Field breakdown**:
- **`decision`**: Control the outcome
  - `allow` / `ask` / `deny` / `defer`: Modify permission evaluation (PreToolUse only)
  - `block`: Stop execution; prevents any further processing
- **`reason`**: Logged when decision is `block` or `deny`
- **`continue`**: If `false`, blocks proceeding to next action
- **`suppressOutput`**: If `true`, hides hook output from console
- **`systemMessage`**: Shown to user if hook blocks
- **`terminalSequence`**: ANSI escape code (e.g., desktop notification, bell, window title) sent even if hook doesn't block; requires no controlling terminal
- **`hookSpecificOutput`**: Event-specific nested object (required for any event-specific actions)

#### PreToolUse Hook Output
```json
{
  "hookSpecificOutput": {
    "hookEventName": "PreToolUse",
    "permissionDecision": "allow|deny|ask|defer",
    "permissionDecisionReason": "Why this decision",
    "updatedInput": {
      "command": "modified-command",
      // ... modified tool_input fields
    },
    "additionalContext": "Context string for Claude about the command"
  }
}
```

- **`permissionDecision`**: Modifies permission evaluation
  - `defer`: Skip hook; use normal permission rules
  - `deny`: Block the tool call
  - `allow`: Skip permission prompt
  - `ask`: Force a permission prompt
- **`updatedInput`**: Modify the tool's input (e.g., change a command) before it executes
- **`additionalContext`**: Adds context to Claude's conversation

#### PostToolUse Hook Output
```json
{
  "hookSpecificOutput": {
    "hookEventName": "PostToolUse",
    "updatedToolOutput": "replacement output text",
    "additionalContext": "Context for Claude based on result",
    "continueOnBlock": true,
    "permissionDecision": "deny"
  }
}
```

- **`updatedToolOutput`**: Replace the tool's output for all tool types (formerly MCP-only)
- **`continueOnBlock`**: If `true`, feed the denial reason back to Claude and continue the turn (instead of blocking)
- **`permissionDecision`**: Can deny further use

#### SessionStart Hook Output
```json
{
  "hookSpecificOutput": {
    "hookEventName": "SessionStart",
    "additionalContext": "Session context to inject",
    "sessionTitle": "Auto-generated session name",
    "watchPaths": ["/path/to/watch", "/another/path"]
  }
}
```

- **`additionalContext`**: Injected at conversation start (e.g., branch info, recent issues)
- **`sessionTitle`**: Auto-title the session
- **`watchPaths`**: Array of paths to watch for file changes; triggers `FileChanged` events

#### UserPromptSubmit Hook Output
```json
{
  "hookSpecificOutput": {
    "hookEventName": "UserPromptSubmit",
    "additionalContext": "Added to prompt context",
    "sessionTitle": "New title"
  },
  "decision": "block",
  "reason": "Why blocking the prompt"
}
```

#### Other Event Outputs
- **PostToolUseFailure**: Same as PostToolUse
- **PostToolBatch**: Can use top-level `decision: "block"` to prevent next model call
- **Stop**: Can use `decision: "block"` to prevent Claude from stopping
- **SubagentStop**: Can use `decision: "block"` to prevent subagent from stopping
- **StopFailure**, **SessionEnd**: Informational only; output is not used

### 1.5 Hook Handler Types

Hooks are configured with one of five handler types:

#### 1. Command Hooks
```json
{
  "type": "command",
  "command": "bash",
  "args": ["/path/to/script.sh", "arg1", "arg2"],
  "shell": "bash|sh|pwsh|powershell",
  "timeout": 600,
  "async": false,
  "asyncRewake": false
}
```

- **`args` field (exec form)**: Direct spawning without shell tokenization (safer for complex arguments)
- **No `args` field (shell form)**: Shell tokenization enabled (e.g., `"command": "bash -c 'echo $VAR'"`)
- **`shell`**: Explicitly set shell (defaults to user's shell)
- **`async`**: Run in background; doesn't block session
- **`asyncRewake`**: If `true`, re-wake the session if hook completes during idle
- **`timeout`**: Max seconds before hook is killed (default 600, UserPromptSubmit 30)

**Execution environment**:
- No `/dev/tty` (non-interactive)
- Inherits `PATH`, `HOME`, user's environment variables
- Does NOT inherit `OTEL_*` variables (v2.1.128+)
- Access to `tool_input`, `session_id`, etc. via stdin JSON

#### 2. HTTP Hooks
```json
{
  "type": "http",
  "url": "https://hooks.example.com/pre-tool-use",
  "method": "POST",
  "headers": {
    "Authorization": "Bearer $MY_SECRET_TOKEN"
  },
  "allowedEnvVars": ["MY_SECRET_TOKEN", "ANOTHER_VAR"],
  "timeout": 600
}
```

- **`url`**: HTTP(S) endpoint receiving POST request with hook input JSON body
- **`headers`**: Custom headers; `$VARNAME` syntax expands from `allowedEnvVars`
- **`allowedEnvVars`**: Environment variables available for header expansion
- **Response status**:
  - **2xx**: JSON response is processed as hook output
  - **Non-2xx**: Treated as non-blocking error; hook output ignored (output shown in debug log, not to user)
- **Non-blocking by default**: Cannot prevent dangerous operations; for that, use command or MCP tool hooks

#### 3. MCP Tool Hooks
```json
{
  "type": "mcp_tool",
  "server": "security-scanner",
  "tool": "scan_command",
  "input": {
    "command": "${tool_input.command}",
    "file_path": "${tool_input.file_path}"
  },
  "timeout": 600
}
```

- **`server` and `tool`**: Must be already connected (hook never triggers OAuth)
- **`input` object**: Uses `${field}` substitution for hook input fields
- **`${tool_input.FIELD}` syntax**: Access nested fields from PreToolUse `tool_input`
- **`${CLAUDE_PROJECT_DIR}`, `${CLAUDE_PLUGIN_ROOT}`, `${CLAUDE_PLUGIN_DATA}`**: Special placeholders for paths
- **Output**: Parsed as hook output JSON; missing fields are ignored

#### 4. Prompt Hooks (LLM-based)
```json
{
  "type": "prompt",
  "prompt": "Is this command safe and expected? $ARGUMENTS",
  "model": "claude-opus-4-1",
  "timeout": 30
}
```

- **`$ARGUMENTS` placeholder**: Replaced with hook input JSON stringified
- **`model`**: Runs against specified model (default user's session model)
- **Output interpretation**: LLM returns "yes" / "no" / structured JSON; hook framework parses as decision
- **Timeout**: 30 seconds default (shorter; should be fast)

#### 5. Agent Hooks (Experimental)
```json
{
  "type": "agent",
  "agent": "security-checker",
  "timeout": 60
}
```

- **`agent`**: Name of a configured subagent (must be defined in `.claude/agents/` or user settings)
- **Input**: Hook event JSON is provided to the agent
- **Output**: Agent's response is parsed as hook output
- **Status**: Experimental; subject to change

### 1.6 Hook Matchers and Filtering

Hooks are grouped by event and filtered with matchers:

```json
{
  "hooks": {
    "PreToolUse": [
      {
        "matcher": "Bash",
        "hooks": [/* array of handlers */]
      },
      {
        "matcher": "Bash",
        "if": "Bash(rm *)",
        "hooks": [/* array of handlers */]
      }
    ]
  }
}
```

**Matcher types**:

| Matcher | Pattern | Behavior |
|---------|---------|----------|
| Omitted or `"*"` | N/A | Matches all |
| Exact string | `"Bash"`, `"Edit\|Write"` | Exact match or pipe-separated list |
| JavaScript regex | `"^Notebook"`, `"mcp__.*"` | Full regex support |

**`if` field** (optional sub-filter):

The `if` field adds an additional permission-rule-style filter:
- `"if": "Bash(rm *)"` — Only hook if command matches this pattern
- Works with all matcher types
- Prevents unnecessary hook execution on non-matching calls

**Example configurations**:

```json
{
  "hooks": {
    "PreToolUse": [
      {
        "matcher": "Bash",
        "hooks": [{ "type": "command", "command": "/check/all-bash.sh" }]
      },
      {
        "matcher": "Edit|Write",
        "if": "Edit(.env*)|Write(.env*)",
        "hooks": [{ "type": "command", "command": "/check/env-files.sh" }]
      },
      {
        "matcher": "mcp__github__.*",
        "hooks": [{ "type": "command", "command": "/check/github.sh" }]
      }
    ]
  }
}
```

### 1.7 Hook Exit Codes and Error Handling

| Exit Code | Behavior |
|-----------|----------|
| **0** | Success; stdout JSON is parsed as hook output |
| **1** | Non-blocking error; stderr shown in debug log, not to user |
| **2** | Blocking error; stderr shown to user/Claude, hook blocks tool call (PreToolUse only) |
| **Other** | Non-blocking error; debug log only |

**Blocking behavior (exit code 2)**:
- PreToolUse: Tool call is blocked before permission rules are evaluated
- Takes precedence over allow rules (deny rules still have higher precedence)
- If both hook exit 2 and permission deny rule match, block is shown once

**Invalid JSON output**:
- Non-zero exit code + invalid JSON → non-blocking error
- Exit 0 + invalid JSON → error logged; hook output ignored
- Use jq or similar for safe JSON generation

### 1.8 Hook Configuration Locations and Scope

| Location | Scope | Git-tracked | Reloads on Change | Use For |
|----------|-------|-------------|-------------------|---------|
| `~/.claude/settings.json` `hooks` key | All projects | No | Yes | Personal automation across projects |
| `.claude/settings.json` `hooks` key | Single project | Yes | Yes | Team-shared hooks |
| `.claude/settings.local.json` `hooks` key | Single project, personal | No (gitignored) | Yes | Personal project-specific overrides |
| Plugin `hooks.json` or `hooks/` directory | When plugin enabled | Yes (in plugin repo) | Yes (on plugin change) | Plugin-shipped automation |
| Skill/Agent frontmatter `hooks` key | Active skill/agent | Yes | Yes | Skill/agent-specific automation |

**Precedence**: Managed settings (if present) > command-line > local > project > user

**Hook discovery**: Hooks load from current project's `.claude/settings.json` with **no parent directory fallback** (unlike CLAUDE.md). To share hooks across projects, place them in `~/.claude/settings.json` or ship via a plugin.

### 1.9 Hook Security and Limitations

#### Security Considerations

1. **MCP Tool Hooks**: Never trigger OAuth flows; require pre-connected servers. Useful for enforcing policies via trusted tools.

2. **HTTP Hooks**: Non-blocking by default (error responses don't halt tool calls). Only command or MCP tool hooks can block dangerous operations.

3. **Command Execution**: Hooks run without `/dev/tty` and inherit no OTEL variables (v2.1.128+), preventing terminal corruption and telemetry leakage.

4. **Variable Expansion**: Environment variables in headers (`$VARNAME`) are expanded only if listed in `allowedEnvVars`. Prevents accidental credential exposure.

5. **Timeout Enforcement**: All hooks have maximum execution time (600s default, 30s for UserPromptSubmit) to prevent session hangs.

6. **Permission Precedence**: Hook decisions cannot override deny rules (configured at any scope). A deny rule always blocks, regardless of hook output. This preserves deny-first evaluation order.

#### Limitations

- Hooks **cannot modify** the model's system prompt or conversation history
- Hooks **cannot suppress** the running of a tool; they can only modify its input or approve/deny it
- HTTP hooks are **non-blocking by default** (error responses don't stop execution)
- **No direct access to conversation state** beyond what's in hook input JSON
- Hooks **cannot be conditionally skipped** beyond the matcher and `if` field
- **Sandbox cannot be modified** by hooks; hooks run in the same sandbox environment as Claude
- Deny rules from **managed settings cannot be bypassed** by any hook decision

#### Known Gotchas

1. **Hook not firing**: Check `.claude/settings.json` matcher spelling. Hooks don't discover from parent directories. Use `/hooks` to verify.

2. **Permission not enforced**: Deny rules always take precedence. If a deny rule matches, the hook decision is ignored.

3. **Slow hook blocks session**: Long-running hooks (especially for high-frequency events like PreToolUse) slow the session. Use timeouts and consider async execution.

4. **Shell interpretation gotchas**:
   - Without `args` field, `$VAR` and glob patterns are expanded by shell
   - With `args` field, arguments are passed literally (safer but no shell interpretation)
   - Use both forms appropriately for your script

5. **JSON parsing errors**: If hook output is invalid JSON and exit code is 0, the error is silently logged. Always test JSON with `jq -r '.'` or similar.

6. **Hooks in subagents**: Hooks in `.claude/settings.json` apply to subagents too (unless `allowManagedHooksOnly` is set to disable non-managed hooks).

### 1.10 Hook Examples

#### Example 1: Block Destructive Commands
```bash
#!/bin/bash
# ~/.claude/hooks/block-rm.sh
COMMAND=$(jq -r '.tool_input.command' < /dev/stdin)

if echo "$COMMAND" | grep -qE '(rm -rf|rmdir.*\/)'; then
  jq -n '{
    "decision": "block",
    "reason": "Destructive rm -rf command blocked by security hook",
    "hookSpecificOutput": {
      "hookEventName": "PreToolUse",
      "permissionDecision": "deny"
    }
  }' >&2
  exit 2
fi

exit 0
```

Configuration:
```json
{
  "hooks": {
    "PreToolUse": [
      {
        "matcher": "Bash",
        "if": "Bash(rm *)",
        "hooks": [
          {
            "type": "command",
            "command": "bash ~/.claude/hooks/block-rm.sh",
            "timeout": 10
          }
        ]
      }
    ]
  }
}
```

#### Example 2: Auto-lint on File Write
```json
{
  "hooks": {
    "PostToolUse": [
      {
        "matcher": "Write|Edit",
        "if": "Write(*.ts)|Write(*.tsx)|Write(*.js)|Write(*.jsx)",
        "hooks": [
          {
            "type": "command",
            "command": "eslint",
            "args": ["${tool_input.file_path}", "--fix"],
            "timeout": 30,
            "async": true
          }
        ]
      }
    ]
  }
}
```

#### Example 3: SessionStart Context Injection
```bash
#!/bin/bash
# ~/.claude/hooks/session-context.sh
BRANCH=$(git branch --show-current 2>/dev/null || echo "unknown")
ISSUES=$(gh issue list -q "label:urgent" --json number 2>/dev/null | jq -c '.' || echo "[]")

jq -n --arg branch "$BRANCH" --argjson issues "$ISSUES" '{
  "hookSpecificOutput": {
    "hookEventName": "SessionStart",
    "additionalContext": "Active branch: \($branch)\nUrgent issues: \($issues | length)\n\($issues | map(.number) | join(", "))"
  }
}'
```

Configuration:
```json
{
  "hooks": {
    "SessionStart": [
      {
        "matcher": "startup",
        "hooks": [
          {
            "type": "command",
            "command": "bash ~/.claude/hooks/session-context.sh"
          }
        ]
      }
    ]
  }
}
```

#### Example 4: MCP Tool Hook for Security Scanning
```json
{
  "hooks": {
    "PreToolUse": [
      {
        "matcher": "Bash",
        "if": "Bash(curl *)",
        "hooks": [
          {
            "type": "mcp_tool",
            "server": "security-checker",
            "tool": "scan_command",
            "input": {
              "command": "${tool_input.command}"
            },
            "timeout": 30
          }
        ]
      }
    ]
  }
}
```

---

## 2. Settings System

### 2.1 Overview

Claude Code uses a **hierarchical settings system** with JSON configuration files that control model selection, permissions, integrations, automation, and UI behavior. Settings load from multiple scopes and are merged according to precedence rules.

**Key principle**: Manage-deployed settings **cannot be overridden** by users or projects. This allows organizations to enforce security and compliance policies.

**Source**: [Settings](https://code.claude.com/docs/en/settings.md), [Managed Settings](https://code.claude.com/docs/en/server-managed-settings.md)

### 2.2 Settings Scope Hierarchy

Settings load from **five scopes** in this precedence order (highest to lowest):

| Rank | Scope | File | Shared | Overridable | Typical Use |
|------|-------|------|--------|-------------|------------|
| 1 | **Managed** | Server, MDM, plist, registry, `/Library/Application Support/ClaudeCode/` (macOS), `/etc/claude-code/` (Linux/WSL), `C:\Program Files\ClaudeCode\` (Windows) | Yes (org-wide) | No (admin only) | Organization policies, security enforcement |
| 2 | **Command line** | `--model sonnet`, `--allow Bash(*)` | Per-session | Temp override | Temporary session overrides |
| 3 | **Local project** | `.claude/settings.local.json` | No (gitignored) | Yes | Personal project-specific settings |
| 4 | **Shared project** | `.claude/settings.json` | Yes (git-committed) | Yes | Team-shared standards, MCP servers |
| 5 | **User** | `~/.claude/settings.json` | No | Yes | Personal preferences for all projects |

**Precedence rule**: The first matching key in the hierarchy is used. For **permission rules specifically** (allow/deny/ask), rules from all scopes are **merged and evaluated in order** (deny > ask > allow).

**Special case**: If a tool is denied at ANY scope, no other scope can allow it. Deny rules are absolute.

### 2.3 Managed Settings (Organization Policy)

Managed settings are delivered by administrators and cannot be overridden. Supported delivery mechanisms:

**Locations** (checked in order):
1. Server-managed settings (MDM/endpoint management)
2. `.mcp.json` `servers[].managed` field (server-specific policy)
3. macOS: `/Library/Application Support/ClaudeCode/managed-settings.json` or HKLM registry
4. Linux/WSL: `/etc/claude-code/managed-settings.json`
5. Windows: `C:\Program Files\ClaudeCode\managed-settings.json` or HKLM registry

**Managed-only settings** (only read from managed settings; user/project settings ignored):

| Setting | Type | Description |
|---------|------|-------------|
| `allowedChannelPlugins` | Array | Allowlist of channel plugins allowed to push messages; replaces default Anthropic allowlist |
| `allowManagedHooksOnly` | Boolean | When `true`, only managed hooks and force-enabled plugin hooks are loaded; user/project hooks blocked |
| `allowManagedMcpServersOnly` | Boolean | When `true`, only `allowedMcpServers` from managed settings respected |
| `allowManagedPermissionRulesOnly` | Boolean | When `true`, prevents user/project from defining allow/ask/deny rules; only managed rules apply |
| `blockedMarketplaces` | Array | Blocklist of plugin marketplace sources (checked before downloading) |
| `channelsEnabled` | Boolean | Enable channels for organization (default varies by plan) |
| `forceRemoteSettingsRefresh` | Boolean | Block startup until remote settings fetched; exit if fetch fails (fail-closed) |
| `pluginTrustMessage` | String | Custom message appended to plugin trust warning |
| `sandbox.filesystem.allowManagedReadPathsOnly` | Boolean | Only managed `filesystem.allowRead` paths respected |
| `sandbox.network.allowManagedDomainsOnly` | Boolean | Only managed `allowedDomains` and `WebFetch(domain:...)` rules respected |
| `strictKnownMarketplaces` | Boolean \| Array | Control which plugin marketplace sources users can add |
| `strictPluginOnlyCustomization` | Boolean \| Array | Block skills, agents, hooks, MCP servers from user/project sources; only from plugins/managed |
| `wslInheritsWindowsSettings` | Boolean | WSL reads managed settings from Windows policy chain (in addition to `/etc/claude-code/`) |

### 2.4 Settings Files and Content

Each settings file is a JSON object with keys organized by feature area.

#### 2.4.1 Model and Behavior Settings

```json
{
  "model": "claude-sonnet-4-6",
  "effortLevel": "low|medium|high|xhigh",
  "alwaysThinkingEnabled": true,
  "availableModels": [
    "sonnet",
    "haiku",
    "opus"
  ],
  "modelOverrides": {
    "claude-opus-4-6": "arn:aws:bedrock:us-east-1:123456789:inference-profile/anthropic.claude-opus-4-6-20250514-v1:0"
  }
}
```

**Field descriptions**:

- **`model`** (string): Default model for new sessions
  - Built-in: `claude-opus-4-6`, `claude-sonnet-4-6`, `claude-haiku-4-5`
  - Special aliases: `default` (user's default), `opusplan` (uses Opus with extended thinking)
  - Changes require session restart (use `/model` to switch mid-session)

- **`effortLevel`** (string): Reasoning effort for applicable models
  - `low`, `medium` (default), `high`, `xhigh`
  - Affects token usage and quality (higher effort = more reasoning, higher cost)
  - Pro/Max subscribers on Opus 4.6/Sonnet 4.6 default to `high`

- **`alwaysThinkingEnabled`** (boolean): Enable extended thinking by default for applicable models

- **`availableModels`** (array): Restrict which model options users see in `/model`
  - Shorthand: `"sonnet"` (all Sonnet versions), `"haiku"`, `"opus"`, `"default"`
  - Full IDs: `"claude-sonnet-4-6-20250514"`
  - If set in managed settings, users cannot override with command-line arguments

- **`modelOverrides`** (object): Map model names to cloud provider inference profiles
  - Keys: Model ID or alias
  - Values: Provider-specific ARN or endpoint ID
  - Used for Amazon Bedrock, Google Vertex AI, Microsoft Foundry

#### 2.4.2 Permissions Settings

```json
{
  "permissions": {
    "allow": [
      "Bash(npm run *)",
      "Bash(git commit *)",
      "Read(./src/**)",
      "Read(~/.zshrc)"
    ],
    "ask": [
      "Bash(git push *)",
      "Bash(git pull origin main)"
    ],
    "deny": [
      "Bash(curl *)",
      "Bash(wget *)",
      "Read(./.env*)",
      "Read(./secrets/**)",
      "Edit(/private/etc/)",
      "WebFetch"
    ],
    "additionalDirectories": [
      "../docs/",
      "~/Documents/work"
    ],
    "defaultMode": "default|acceptEdits|plan|auto|dontAsk|bypassPermissions",
    "disableBypassPermissionsMode": "disable",
    "disableAutoMode": "disable"
  }
}
```

**Permission rules** (evaluated in order: deny > ask > allow):
- **`allow`**: Claude Code uses the tool without prompting
- **`ask`**: Claude Code prompts before each use
- **`deny`**: Claude Code blocks the tool (or specific uses)
- **Syntax**: `Tool` (all uses) or `Tool(specifier)` (specific uses)
- See section 3 (Permissions) for complete syntax

**Fields**:
- **`additionalDirectories`**: Array of paths where Claude can read/write files (extend working directory)
  - Relative paths: relative to project root
  - `~` expands to home directory
  - Full configuration is NOT loaded from additional directories (skills/CLAUDE.md only if `CLAUDE_CODE_ADDITIONAL_DIRECTORIES_CLAUDE_MD=1`)

- **`defaultMode`**: Permission mode for new sessions (default: `default`)

- **`disableBypassPermissionsMode`**: Set to `"disable"` to prevent `bypassPermissions` mode usage

- **`disableAutoMode`**: Set to `"disable"` to prevent `auto` mode usage

#### 2.4.3 Hooks Settings

```json
{
  "hooks": {
    "SessionStart": [
      {
        "matcher": "startup",
        "hooks": [
          {
            "type": "command",
            "command": "bash ~/.claude/hooks/init.sh",
            "timeout": 30
          }
        ]
      }
    ],
    "PreToolUse": [
      {
        "matcher": "Bash",
        "if": "Bash(rm *)",
        "hooks": [
          {
            "type": "command",
            "command": "bash ~/.claude/hooks/block-rm.sh"
          }
        ]
      }
    ]
  },
  "disableAllHooks": false
}
```

See section 1 (Hooks) for complete hook configuration.

**Fields**:
- **`hooks`**: Object keyed by hook event name (SessionStart, PreToolUse, etc.)
- **`disableAllHooks`**: Boolean; when `true`, all hooks are disabled globally

#### 2.4.4 Environment Variables

```json
{
  "env": {
    "CLAUDE_CODE_ENABLE_TELEMETRY": "1",
    "OTEL_METRICS_EXPORTER": "otlp",
    "OTEL_EXPORTER_OTLP_ENDPOINT": "https://collector.example.com",
    "FOO": "bar",
    "PATH_EXTRA": "/custom/bin"
  }
}
```

**Behavior**:
- Variables apply to every session and are inherited by subprocess
- Subprocesses do NOT automatically inherit `OTEL_*` variables (v2.1.128+); explicitly set if needed
- Path expansion: `~` is expanded; `$VAR` references are NOT expanded (literal values)
- User scope (`~/.claude/settings.json`) env vars available to all projects

#### 2.4.5 Sandboxing Settings

```json
{
  "sandbox": {
    "enabled": true,
    "failIfUnavailable": true,
    "autoAllowBashIfSandboxed": true,
    "excludedCommands": [
      "docker *",
      "sudo *"
    ],
    "filesystem": {
      "allowRead": ["."],
      "allowWrite": ["/tmp/build", "~/.cache"],
      "denyRead": ["~/.aws/credentials", "~/.ssh/**"],
      "denyWrite": ["/etc", "/.dockerenv"]
    },
    "network": {
      "allowedDomains": ["github.com", "*.npmjs.org", "api.anthropic.com"],
      "deniedDomains": ["internal.company.example.com"],
      "allowAllUnixSockets": false
    },
    "bwrapPath": "/usr/bin/bwrap",
    "socatPath": "/usr/bin/socat"
  }
}
```

See Sandboxing section of [official docs](https://code.claude.com/docs/en/sandboxing.md) for full details.

**Key fields**:
- **`enabled`**: Boolean; enable sandboxed Bash execution
- **`failIfUnavailable`**: Boolean; fail to start if sandbox unavailable (fail-closed)
- **`autoAllowBashIfSandboxed`**: Boolean (default `true`); auto-approve sandboxed Bash commands without prompting
- **`excludedCommands`**: Array of Bash patterns to run outside sandbox
- **`filesystem.allowRead` / `allowWrite` / `denyRead` / `denyWrite`**: Arrays of path patterns (gitignore syntax)
- **`network.allowedDomains` / `deniedDomains`**: Arrays of domain patterns; combined with WebFetch permission rules
- **`bwrapPath` / `socatPath`**: Custom paths to bubblewrap and socat binaries (Linux/WSL)

#### 2.4.6 UI and Display Settings

```json
{
  "spinnerTipsEnabled": true,
  "spinnerVerbs": {
    "mode": "append|replace",
    "verbs": ["Pondering", "Crafting", "Iterating"]
  },
  "editorMode": "vim|emacs|default",
  "tui": "fullscreen|default",
  "autoScrollEnabled": true,
  "showTurnDuration": true,
  "prefersReducedMotion": false,
  "language": "en|ja|es|fr|de|it|pt|ko|zh|ru"
}
```

**Fields**:
- **`spinnerTipsEnabled`**: Show tips while waiting
- **`spinnerVerbs`**: Customize action verbs in spinner ("Thinking", "Planning", etc.)
  - `mode: "append"` adds to defaults; `"replace"` uses only custom verbs
- **`editorMode`**: Editor keybindings for prompt input
- **`tui`**: Rendering mode (`fullscreen` for advanced UI, `default` for basic)
- **`autoScrollEnabled`**: Auto-scroll conversation in fullscreen mode
- **`showTurnDuration`**: Display elapsed time per turn
- **`prefersReducedMotion`**: Reduce animation
- **`language`**: UI language

#### 2.4.7 Status Line Settings

```json
{
  "statusLine": {
    "type": "command|script|inline",
    "command": "~/.claude/statusline.sh",
    "format": "{context_usage}% | {model} | {git_branch}"
  }
}
```

Customizes the bottom status line. See [Status Line](https://code.claude.com/docs/en/statusline.md) for full reference.

#### 2.4.8 File Suggestion Settings

```json
{
  "fileSuggestion": {
    "type": "command",
    "command": "~/.claude/file-suggestions.sh"
  }
}
```

Customizes autocomplete for `@file` mentions. Custom script can return JSON list of suggestions.

#### 2.4.9 Attribution Settings

```json
{
  "attribution": {
    "commit": "🤖 Generated with Claude Code",
    "pullRequest": "Generated with Claude Code",
    "includeCoAuthoredBy": false
  }
}
```

**Fields**:
- **`commit`**: Custom commit message suffix
- **`pullRequest`**: Custom PR description text
- **`includeCoAuthoredBy`**: Add `Co-Authored-By` trailer to commits (default `false`)

#### 2.4.10 MCP Server Configuration

**User scope** (`~/.claude.json`):
```json
{
  "mcpServers": {
    "github": {
      "command": "npx",
      "args": ["-y", "@modelcontextprotocol/server-github"],
      "env": {
        "GITHUB_PERSONAL_ACCESS_TOKEN": "$GITHUB_TOKEN"
      }
    },
    "memory": {
      "command": "node",
      "args": ["/path/to/server.js"]
    }
  }
}
```

**Project scope** (`.mcp.json`):
```json
{
  "mcpServers": {
    "github": {
      "command": "npx",
      "args": ["-y", "@modelcontextprotocol/server-github"],
      "env": {
        "GITHUB_PERSONAL_ACCESS_TOKEN": "$GITHUB_TOKEN"
      }
    }
  }
}
```

**Settings**:
```json
{
  "allowedMcpServers": [
    "github",
    "memory"
  ],
  "deniedMcpServers": [
    "shell"
  ],
  "enableAllProjectMcpServers": false,
  "enabledMcpjsonServers": [
    "github"
  ]
}
```

**Fields**:
- **`allowedMcpServers`** (managed only): Whitelist of allowed server names
- **`deniedMcpServers`**: Blocklist of disallowed servers (merges from all scopes)
- **`enableAllProjectMcpServers`**: Auto-approve all servers in project `.mcp.json`
- **`enabledMcpjsonServers`**: Explicitly enable specific `.mcp.json` servers

#### 2.4.11 Plugin Settings

```json
{
  "enabledPlugins": {
    "markdown-formatter@official-marketplace": true,
    "my-custom-plugin": false
  },
  "extraKnownMarketplaces": {
    "custom-plugins": {
      "source": {
        "source": "github",
        "owner": "myorg",
        "repo": "plugin-marketplace"
      }
    }
  },
  "strictKnownMarketplaces": false,
  "strictPluginOnlyCustomization": false
}
```

**Fields**:
- **`enabledPlugins`**: Map of plugin names to enabled (true/false); requires session restart to take effect
- **`extraKnownMarketplaces`**: Additional plugin marketplaces to search
- **`strictKnownMarketplaces`** (managed only): Restrict which marketplace sources users can add
- **`strictPluginOnlyCustomization`** (managed only): Block non-plugin sources for skills, agents, hooks, MCP servers

#### 2.4.12 Subagent Configuration

```json
{
  "subagents": [
    {
      "name": "code-reviewer",
      "type": "general-purpose",
      "model": "claude-opus-4-6",
      "permissions": {
        "allow": ["Bash(npm test)"]
      },
      "capabilities": {
        "tools": ["Read", "Edit", "Bash"]
      }
    }
  ]
}
```

Defines custom subagents. Also loaded from `~/.claude/agents/` and `.claude/agents/` directories with YAML/JSON files.

#### 2.4.13 Credential and Authentication Helpers

```json
{
  "apiKeyHelper": "/bin/generate_temp_api_key.sh",
  "awsCredentialExport": "/bin/assume_role.sh",
  "gcpAuthRefresh": "gcloud auth application-default login",
  "otelHeadersHelper": "/bin/generate_otel_headers.sh"
}
```

**Fields**:
- **`apiKeyHelper`**: Script that returns a temporary API key (Claude API only)
- **`awsCredentialExport`**: Script that exports temporary AWS credentials
- **`gcpAuthRefresh`**: Command to refresh GCP credentials
- **`otelHeadersHelper`**: Script that returns dynamic headers for OTEL collection

#### 2.4.14 Output Style Settings

```json
{
  "outputStyle": "Explanatory|Concise|Technical"
}
```

Built-in styles or custom style name from `~/.claude/output-styles/` or `.claude/output-styles/`.

#### 2.4.15 Worktree Settings

```json
{
  "worktree": {
    "baseRef": "fresh|local",
    "bgIsolation": "full|none|worktree"
  }
}
```

**Fields**:
- **`baseRef`**: Whether worktrees branch from `origin/<default>` (`fresh`, default) or local `HEAD` (`local`)
- **`bgIsolation`**: How background sessions handle worktree edits (`full` = worktree enforced, `none` = edit working copy directly, `worktree` = requires enterWorktree)

#### 2.4.16 Other Settings

| Setting | Type | Description |
|---------|------|-------------|
| `agent` | string | Run new sessions as named subagent |
| `autoMemoryEnabled` | boolean | Enable auto memory (default `true`) |
| `autoUpdatesChannel` | string | `"stable"` or `"latest"` |
| `cleanupPeriodDays` | integer | Days to keep local session data (default 90) |
| `companyAnnouncements` | array | Startup messages for team |
| `disableAllHooks` | boolean | Disable all hooks globally |
| `keyBindingsFile` | string | Custom keybindings JSON file |
| `prUrlTemplate` | string | Custom PR URL template for footer |
| `skipAutoPermissionPrompt` | boolean | Auto-grant read-only tools without prompts |
| `systemPrompt` | string | Custom system prompt template |

### 2.5 Settings Reloading and Persistence

**Automatic reloads** (on file change):
- ✅ `permissions`, `hooks`, `env`, credential helpers, `autoMemoryEnabled`, `spinnerTipsEnabled`
- ❌ `model`, `outputStyle`, `language`, `effortLevel` (require session restart)
- Semi: `availableModels` (reloads but doesn't change current model)

**Persistent UI settings** (via `/config` command):
- `theme`, `editorMode`, `verbose`, etc. now persist to `~/.claude/settings.json`

**Schema validation**: Use official JSON schema for autocomplete:
```json
{
  "$schema": "https://json.schemastore.org/claude-code-settings.json"
}
```

### 2.6 Settings Precedence (Complete)

Final precedence for all settings (highest to lowest):

1. **Managed settings** (cannot be overridden)
2. **Command-line arguments** (e.g., `--model`, `--allow`, `--permissions-mode`)
3. **Local project settings** (`.claude/settings.local.json`)
4. **Shared project settings** (`.claude/settings.json`)
5. **User settings** (`~/.claude/settings.json`)

**Special case — Permission rules** (allow/deny/ask):
- Rules from all scopes are **merged** (not replaced)
- Evaluation order: deny rules (all scopes) > ask rules (all scopes) > allow rules (all scopes)
- A deny rule from any scope blocks; cannot be overridden

**Managed settings merging**: By default, managed settings replace user/project scopes (`first-wins`). Admins can set `parentSettingsBehavior: "merge"` to combine policies. Embedded SDK integrations (e.g., GitHub Actions) use `managedSettings` option.

---

## 3. Permissions System

### 3.1 Overview

Claude Code uses a **permission system** to control which tools Claude can use, which files it can access, and what domains it can fetch from. Permissions are organized by tool type and support fine-grained, wildcard-based rules with hook-based extension.

**Key principle**: Permissions are enforced by Claude Code (the runtime), not by the model. Instructions in prompts or CLAUDE.md shape what Claude *tries* to do, but only permissions decide what Claude *can* do.

**Source**: [Permissions](https://code.claude.com/docs/en/permissions.md), [Permission Modes](https://code.claude.com/docs/en/permission-modes.md)

### 3.2 Permission Modes

Permission modes control the **approval flow** for tool calls. Users can switch modes with `/permissions` or set a default in settings.

| Mode | Approval Flow | Use Case | Prompts |
|------|---------------|----------|---------|
| **`default`** | Standard: deny rules enforced, then ask rules prompt, then allow rules auto-approve | General development | Prompts for first use of each new tool |
| **`acceptEdits`** | Auto-approves file edits + common filesystem commands (`mkdir`, `touch`, `mv`, `cp`) for working dir or `additionalDirectories`; other tools follow `default` | Rapid iteration when you trust the agent | Fewer prompts for file work |
| **`plan`** | Allows Read, Grep, Bash read-only commands; blocks file edits and destructive commands; Claude explores without modifying | Analysis & exploration | No prompts for read operations |
| **`auto`** | Auto-approves with background safety classifier; blocks actions classified as suspicious (research preview) | Autonomous operation | Minimal prompts (fallback on uncertain actions) |
| **`dontAsk`** | Only allows pre-approved tools via `/permissions` or allow rules; denies everything else | Restrictive, explicit allow-list | High control; denies by default |
| **`bypassPermissions`** | Skips all permission prompts (except circuit-breaker blocks on root/home directory removal) | Sandboxed environments (containers, VMs) | No prompts (dangerous outside sandbox) |

**Permission dialog** (in `default` and `acceptEdits` modes):
- User is prompted with tool name and arguments
- Options: "Yes", "Yes, don't ask again" (saves rule), "No", "Ask always"
- "Yes, don't ask again" saves a rule to active settings (until session end, or project settings if user specifies)

**`auto` mode classifier**:
- Uses ML-based safety classifier to evaluate whether an action is safe
- Blocks by default on suspicious actions; asks user if uncertain
- Does NOT bypass deny rules (deny rules always block)
- Respects boundaries stated in conversation ("only edit files in src/")
- Falls back to prompting if uncertain
- Currently a research preview; behavior may change

**`bypassPermissions` mode circuit-breaker**:
- Even in bypass mode, removals targeting `/` (root) or `~` (home dir) still prompt
- Examples: `rm -rf /`, `rm -rf ~`, `rmdir /etc`
- Prevents accidental system destruction

### 3.3 Permission Rule Syntax

Permission rules follow the format `Tool` or `Tool(specifier)`.

#### 3.3.1 Basic Patterns

| Pattern | Syntax | Example | Matches |
|---------|--------|---------|---------|
| All uses | Tool name only | `Bash` | All Bash commands |
| Wildcard match | `Tool(glob-pattern)` | `Bash(npm run *)` | `npm run test`, `npm run build`, etc. |
| Exact match | `Tool(exact-string)` | `Bash(git status)` | Only `git status` |
| Pipe-separated | `Tool\|Tool` | `Edit\|Write` | Edit OR Write |
| Prefix match | `Tool(prefix:*)` | `Bash(ls:*)` | Commands starting with `ls ` |

#### 3.3.2 Wildcard Semantics

Bash rules support `*` at any position in the command:

| Rule | Example | Matches | Does NOT Match |
|------|---------|---------|----------------|
| `Bash(npm run *)` | `npm run test`, `npm run build` | ✓ | `npx run test` (different prefix) |
| `Bash(* test)` | `npm test`, `yarn test`, `pnpm test` | ✓ | `test-runner` (needs space before `test`) |
| `Bash(git * main)` | `git checkout main`, `git merge main` | ✓ | `git checkout develop` (doesn't end with `main`) |
| `Bash(ls *)` | `ls -la`, `ls -1` | ✓ | `lsof` (no space before `*`, enforces word boundary) |
| `Bash(ls*)` | `ls -la`, `lsof` | ✓ | N/A (no word boundary) |

**Key insight**: Space before `*` enforces word boundary. `Bash(ls *)` matches commands starting with `ls ` (with space), while `Bash(ls*)` matches commands starting with `ls` (no space required).

The `:*` suffix is equivalent to trailing ` *` and only works at the end: `Bash(ls:*)` = `Bash(ls *)`.

#### 3.3.3 Tool-Specific Permission Rules

##### Bash

```json
{
  "permissions": {
    "allow": [
      "Bash(npm run test)",
      "Bash(npm run *)",
      "Bash(git commit *)",
      "Bash(git * main)"
    ],
    "ask": [
      "Bash(git push *)"
    ],
    "deny": [
      "Bash(rm *)",
      "Bash(curl *)",
      "Bash(sudo *)"
    ]
  }
}
```

**Special handling**:
- **Compound commands**: Recognized separators (`&&`, `||`, `;`, `|`, `|&`, `&`, newlines) split the command; each subcommand must match a rule independently
- **Process wrappers**: `timeout`, `time`, `nice`, `nohup`, `stdbuf`, bare `xargs` are stripped before matching. A rule like `Bash(npm test *)` matches `timeout 30 npm test`.
- **Exec wrappers**: `watch`, `setsid`, `ionice`, `flock` cannot be auto-approved; they always prompt. Same for `find -exec`, `find -delete`.
- **Read-only commands**: `ls`, `cat`, `echo`, `pwd`, `head`, `tail`, `grep`, `find`, `wc`, `which`, `diff`, `stat`, `du`, `cd`, and read-only `git` forms run without prompts (built-in whitelist, not configurable)
- **Glob patterns**: Unquoted globs (`ls *.ts`) allowed only if all flags are read-only
- **cd into project**: `cd ./path` within working directory or `additionalDirectories` is auto-approved

##### PowerShell

```json
{
  "permissions": {
    "allow": [
      "PowerShell(Get-ChildItem *)",
      "PowerShell(git commit *)"
    ],
    "deny": [
      "PowerShell(Remove-Item *)"
    ]
  }
}
```

**Special handling**:
- Aliases canonicalized: `Get-ChildItem`, `gci`, `ls`, `dir` all match `PowerShell(Get-ChildItem *)`
- Case-insensitive matching
- Compound commands (`|`, `;`, `&&`, `||` on 7+) split independently
- Same wrapper stripping as Bash

##### Read and Edit

```json
{
  "permissions": {
    "allow": [
      "Read(./src/**)",
      "Read(~/.zshrc)",
      "Edit(./src/**/*.ts)",
      "Edit(.env*)"
    ],
    "deny": [
      "Read(~/.ssh/**)",
      "Read(.env*)",
      "Edit(/etc/**)",
      "Edit(/private/var/**)"
    ]
  }
}
```

**Path syntax** (gitignore-style):

| Anchor | Pattern | Example | Matches |
|--------|---------|---------|---------|
| **Absolute** | `//path` | `Read(//Users/alice/secrets/*)` | Filesystem root: `/Users/alice/secrets/...` |
| **Home** | `~/path` | `Read(~/.ssh/**)` | Home directory: `~/.ssh/...` |
| **Project relative** | `/path` | `Edit(/src/**/*.ts)` | Project root: `<project>/src/**/*.ts` |
| **Current dir relative** | `path` or `./path` | `Read(*.env)` | Current working directory: `<cwd>/*.env` |
| **Bare filename** | `name` | `Read(.env)` | Any depth: `<cwd>/.env`, `<cwd>/subdir/.env`, etc. |

**Gitignore semantics**:
- `*` matches within a single directory
- `**` matches recursively across directories
- `!` prefix for negation (can't be used in permission rules; only for gitignore)
- Bare filenames like `Read(.env)` match at any depth (equivalent to `Read(**/.env)`)

**Symlink handling**:
- **Allow rules**: Both symlink path AND target must match (allow falls back to prompting if either fails)
- **Deny rules**: Either symlink path OR target matching denies (deny is absolute)
- Example: `Read(./project/**)` allowed, `Read(~/.ssh/**)` denied → symlink at `./project/key` pointing to `~/.ssh/id_rsa` is **blocked**

**Windows path normalization**:
- `C:\Users\alice` → `/c/Users/alice`
- Pattern `//**/.env` matches `.env` on any drive

##### WebFetch

```json
{
  "permissions": {
    "allow": [
      "WebFetch(domain:github.com)",
      "WebFetch(domain:*.npmjs.org)"
    ],
    "deny": [
      "WebFetch(domain:internal.example.com)",
      "WebFetch"
    ]
  }
}
```

**Syntax**:
- `WebFetch` matches all fetches
- `WebFetch(domain:example.com)` matches fetches to `example.com` (subdomains not matched by default)
- `WebFetch(domain:*.example.com)` matches subdomains
- Wildcard `*` matches single label only; `**` not supported for domains

##### MCP (Model Context Protocol)

```json
{
  "permissions": {
    "allow": [
      "mcp__github",
      "mcp__github__*",
      "mcp__github__create_pull_request",
      "mcp__file_search__search"
    ],
    "deny": [
      "mcp__dangerous_tool"
    ]
  }
}
```

**Syntax**:
- `mcp__servername` matches any tool from the server
- `mcp__servername__*` wildcard form (same as above)
- `mcp__servername__toolname` matches specific tool

##### Agent (Subagents)

```json
{
  "permissions": {
    "allow": [
      "Agent(Explore)",
      "Agent(Plan)"
    ],
    "deny": [
      "Agent(my-custom-agent)"
    ]
  }
}
```

**Syntax**:
- `Agent(Explore)`, `Agent(Plan)` match built-in subagents
- `Agent(my-custom-agent)` matches custom subagent by name

##### Notebook Tools

```json
{
  "permissions": {
    "allow": [
      "NotebookEdit(/notebooks/**)"
    ],
    "deny": [
      "Notebook*"
    ]
  }
}
```

**Syntax**:
- `NotebookEdit(path)` file edits in notebooks
- `NotebookExecute(path)` cell execution
- `Notebook*` matches all notebook tools (regex form)

##### File System Commands in Bash

Built-in recognition for common file operations:
- `mkdir -p ./dir` — recognized as Create, follows Edit permissions
- `touch ./file` — recognized as Create, follows Edit permissions
- `mv ./old ./new` — recognized as both Read (old) and Edit (new)
- `cp ./src ./dst` — recognized as both Read (src) and Edit (dst)
- `rm ./file` — recognized as Edit (delete)

These do NOT require separate Bash permissions if Edit permission is granted, but can be denied with Bash-specific rules.

### 3.4 Permission Rule Evaluation and Precedence

Rules are evaluated in order: **deny > ask > allow**. The first matching rule wins.

```json
{
  "permissions": {
    "deny": [
      "Bash(curl *)",      // Block all curl
      "Bash(rm *)"         // Block all rm
    ],
    "ask": [
      "Bash(git push *)"   // Prompt on git push
    ],
    "allow": [
      "Bash(npm *)",       // Auto-approve npm
      "Bash(git commit *)" // Auto-approve git commit
    ]
  }
}
```

**Example evaluation**:
- `npm test` → matches `allow` rule → ✓ auto-approved
- `curl https://example.com` → matches `deny` rule → ✗ blocked
- `git push origin main` → matches `ask` rule → ? user prompted
- `git commit -m "msg"` → matches `allow` rule → ✓ auto-approved
- `git status` → doesn't match any rule → uses default mode logic (usually prompts in `default` mode)

**Deny rules from managed settings**: Cannot be overridden by user/project allow rules. Deny rules have absolute precedence.

### 3.5 Extended Permissions with Hooks

Hooks can extend or override permission evaluation (but not override deny rules):

```json
{
  "hooks": {
    "PreToolUse": [
      {
        "matcher": "Bash",
        "if": "Bash(rm *)",
        "hooks": [
          {
            "type": "command",
            "command": "bash ~/.claude/hooks/check-rm.sh"
          }
        ]
      }
    ]
  }
}
```

Hook output can return `permissionDecision: "allow" | "deny" | "ask" | "defer"`:
- **Deny rules always win**: Even if hook returns `allow`, a matching deny rule blocks
- **Hook exit code 2 blocks**: Takes precedence over allow rules but not deny rules
- **Defer**: Skip hook; use normal permission rules

See section 1.7 (Hook Exit Codes) for details.

### 3.6 Working Directories and File Access

**Default access**: The directory where Claude Code is invoked (project root)

**Extended access** (configuration):
```json
{
  "permissions": {
    "additionalDirectories": [
      "../docs/",
      "~/Documents/work"
    ]
  }
}
```

**Behavior**:
- Paths become readable without prompts (follow Read permission rules)
- File editing permissions follow current permission mode
- CLAUDE.md loaded only if `CLAUDE_CODE_ADDITIONAL_DIRECTORIES_CLAUDE_MD=1` env var set
- Skills, agents, hooks, MCP servers are NOT discovered from additional directories (use plugins or user scope)

### 3.7 Interaction with Sandboxing and Permission Modes

**Permissions** control which tools Claude can use.  
**Sandboxing** provides OS-level enforcement (filesystem and network isolation for Bash).  
**Permission modes** control the approval flow.

They work together:

- **In `default` mode**: Permission prompt is shown; sandboxed Bash still restricts what subprocess can do
- **In `acceptEdits` mode**: File edits auto-approved (by permission mode); sandbox still enforces filesystem boundary
- **In `auto` mode**: Classifier auto-approves; deny rules still block; sandbox still enforces
- **In `bypassPermissions` mode**: No prompts; sandbox still enforces; root/home removal still prompts
- **With sandboxing disabled**: Only permissions enforce access control

**Interaction example**:
```json
{
  "permissions": {
    "deny": ["Read(~/.ssh/**)", "Edit(/etc/**)"],
    "allow": ["Bash(npm *)"]
  },
  "sandbox": {
    "enabled": true,
    "filesystem": {
      "allowRead": [".", "~/Documents"],
      "denyRead": ["~/.ssh"],
      "allowWrite": ["."]
    }
  }
}
```

- Permission `deny` on `~/.ssh/**` blocks Claude from reading via Read tool
- Sandbox `denyRead` on `~/.ssh` blocks any subprocess from accessing it
- Both layers enforce the boundary

### 3.8 Permission Rule Validation and Debugging

**View active permissions**:
```bash
claude /permissions
```

Displays all active rules, their scope, and which settings.json file they came from.

**Verify settings are loaded**:
```bash
claude /debug config
```

Shows resolved settings hierarchy and which keys loaded from which files.

**Test rules**:
- Use `/permissions` UI to enable/disable rules interactively
- Manually edit settings and use `/reload` to apply (some settings auto-reload)

### 3.9 Managed Permission Rules

Admins can enforce permissions across an organization:

```json
{
  "permissions": {
    "deny": ["Bash(curl *)", "Bash(wget *)"],
    "allow": ["WebFetch(domain:github.com)"]
  },
  "allowManagedPermissionRulesOnly": true
}
```

**Key settings**:
- **`allowManagedPermissionRulesOnly`**: When `true`, user/project settings cannot define allow/ask/deny rules
- **`allowManagedDomainsOnly`**: Only managed `allowedDomains` and `WebFetch(domain:...)` rules respected
- **`sandbox.filesystem.allowManagedReadPathsOnly`**: Only managed Read paths respected

---

## 4. Environment Variables

### 4.1 Claude Code Control Variables

| Variable | Value | Purpose | Scope |
|----------|-------|---------|-------|
| **`ANTHROPIC_API_KEY`** | Token string | API authentication (Claude API) | Global |
| **`ANTHROPIC_BASE_URL`** | URL | Custom API endpoint (e.g., Bedrock, Vertex, Foundry) | Global |
| **`ANTHROPIC_WORKSPACE_ID`** | Workspace ID | Scope minted token to workspace (workload identity federation) | Global |
| **`ANTHROPIC_BEDROCK_SERVICE_TIER`** | `default\|flex\|priority` | Select Bedrock service tier (v2.1.122+) | Per session |
| **`CLAUDE_EFFORT`** | `low\|medium\|high\|xhigh` | Current effort level (available to Bash commands, v2.1.133+) | Per session |
| **`CLAUDE_CODE_SESSION_ID`** | UUID | Session ID (available to Bash subprocesses, v2.1.132+) | Per session |
| **`CLAUDE_PROJECT_DIR`** | Absolute path | Project root directory (passed to MCP stdio servers, v2.1.139+) | Per session |
| **`CLAUDE_CODE_HIDE_CWD`** | Any value | Hide working directory in startup logo (v2.1.119+) | Global |
| **`CLAUDE_CODE_DISABLE_ALTERNATE_SCREEN`** | Any value | Opt out of fullscreen renderer (v2.1.132+) | Global |
| **`CLAUDE_CODE_FORCE_SYNC_OUTPUT`** | Any value | Force synchronized output on auto-detection misses (v2.1.129+) | Global |
| **`CLAUDE_CODE_PLUGIN_PREFER_HTTPS`** | Any value | Clone GitHub plugins over HTTPS instead of SSH (v2.1.141+) | Global |
| **`CLAUDE_CODE_FORK_SUBAGENT`** | `1` | Enable forked subagents on external builds (v2.1.141+) | Global |
| **`CLAUDE_CODE_MAX_CONTEXT_TOKENS`** | Integer | Maximum context window size (default: 150000) | Global |
| **`CLAUDE_CODE_ENABLE_TELEMETRY`** | `0\|1` | Enable telemetry reporting (default 1) | Global |
| **`DISABLE_UPDATES`** | Any value | Block all update paths (v2.1.126+) | Global |
| **`CLAUDE_CODE_ADDITIONAL_DIRECTORIES_CLAUDE_MD`** | `1` | Load CLAUDE.md from additional directories | Global |

### 4.2 Proxy and Network Variables

| Variable | Value | Purpose |
|----------|-------|---------|
| **`HTTP_PROXY` / `HTTPS_PROXY`** | `http://[user:pass@]host:port` | Proxy for all HTTP(S) requests |
| **`NO_PROXY`** | Domain list | Domains to exclude from proxy (comma-separated, v2.1.126+ respected) |
| **`SOCKS_PROXY`** | `socks5://host:port` | SOCKS proxy (if supported) |

**mTLS**: Certificate and key files are passed via config, not env vars (see network-config docs).

### 4.3 OpenTelemetry Variables

| Variable | Value | Purpose |
|----------|-------|---------|
| **`OTEL_METRICS_EXPORTER`** | `otlp` | Enable metrics export |
| **`OTEL_EXPORTER_OTLP_ENDPOINT`** | URL | OTLP collector endpoint |
| **`OTEL_EXPORTER_OTLP_HEADERS`** | `key=value,key=value` | Additional headers |
| **`OTEL_SERVICE_NAME`** | Name | Service identifier |

**Note**: Subprocesses do NOT inherit `OTEL_*` variables by default (v2.1.128+). Explicitly set in `env` settings if needed.

### 4.4 Git and Tool Variables

| Variable | Value | Purpose |
|----------|-------|---------|
| **`GIT_AUTHOR_NAME`** | Name | Git commit author |
| **`GIT_AUTHOR_EMAIL`** | Email | Git commit email |
| **`GIT_COMMITTER_NAME`** | Name | Git committer name |
| **`GIT_COMMITTER_EMAIL`** | Email | Git committer email |
| **`GIT_PAGER`** | Command | Pager for git output |

### 4.5 Custom Application Variables

Any custom variables set in settings `env` section:

```json
{
  "env": {
    "FOO": "bar",
    "MY_API_KEY": "$EXTERNAL_KEY",
    "CUSTOM_VAR": "value"
  }
}
```

- Available to Bash commands and subprocesses
- `~` expanded; `$VAR` references NOT expanded (literal values)
- Set at session start; changes require session restart

---

## 5. Known Limitations and Gotchas

### 5.1 Hooks Limitations

1. **Hook not firing**: Hooks in `.claude/settings.json` don't discover from parent directories. Verify with `/hooks` command.

2. **Hooks in subagents**: Hooks apply to subagents too. To restrict, set `allowManagedHooksOnly: true` in managed settings.

3. **Slow hooks**: Long-running PreToolUse hooks slow every tool call. Use timeouts and test performance.

4. **Permission rules take precedence**: Hook `permissionDecision` cannot override deny rules. If permission deny rule matches, block applies regardless of hook output.

5. **No terminal access**: Hooks run without `/dev/tty`. Use `terminalSequence` field for notifications instead of interactive prompts.

6. **JSON parsing silent failures**: Exit code 0 + invalid JSON → error logged silently. Use `jq -r '.'` to validate.

7. **Hook can't modify model behavior**: Hooks extend permissions and automation but cannot change the model's instructions or system prompt.

8. **PostToolUse hook blocking**: Blocks proceeding to next model call, not the current tool result. Tool already executed.

9. **Windows path handling**: Paths in hook JSON are POSIX form (`/c/Users/...`). Use forward slashes even on Windows.

10. **Hooks disabled in certain modes**: Some experimental modes may disable hooks; check session logs.

### 5.2 Settings Limitations

1. **Hooks don't auto-reload config**: Editing `.claude/settings.json` during a session requires `/reload` command (or session restart for some settings).

2. **Model changes require restart**: Changing `model` setting requires session restart (except `/model` command).

3. **Managed settings cannot be escaped**: Deny rules from managed settings block all contexts. No workaround.

4. **Settings precedence is fixed**: Cannot change evaluation order (managed > CLI > local > project > user).

5. **No conditional settings**: Settings cannot have `if` branches based on cwd or other runtime state.

6. **Environment variable limits**: Subprocesses don't inherit `OTEL_*` vars; must explicitly set in `env`.

7. **Path expansion limits**: `~` expands; `$VAR` does NOT expand in settings values (set via `env` section for runtime vars).

### 5.3 Permissions Limitations

1. **Wildcard patterns are fragile**: `Bash(curl http://github.com/ *)` doesn't match `curl -X GET http://github.com/...` or URL in variable. Prefer WebFetch with domain rules for URL restrictions.

2. **Read/Edit rules don't apply to subprocesses**: Deny rules block Claude's tools but not arbitrary Python/Node scripts that open files. Use sandbox for OS-level enforcement.

3. **Symlink handling asymmetry**: Allow rules require both symlink AND target to match; deny requires either. Unexpected symlinks can bypass allow rules but not deny rules.

4. **Process wrappers limited**: Only built-in wrappers stripped (`timeout`, `time`, `nice`, `nohup`, `stdbuf`). Custom runners like `docker exec`, `devbox run` are not stripped; write specific rules.

5. **Glob patterns unsafe for write operations**: `find -exec rm {} +` not caught by `Bash(find *)`. Requires explicit `find -exec` deny rule.

6. **Windows path normalization quirky**: `C:\path` → `/c/path` but patterns in settings are POSIX. Use `/c/...` form in rules.

7. **Deny rules can't be relaxed**: Once denied at managed scope, user/project cannot allow it. Complete block.

8. **Auto mode uncertainty**: Auto classifier may block safe actions if unsure. Fallback prompts user; not fully autonomous.

### 5.4 Platform and Environment Gotchas

1. **Windows PowerShell vs Bash**: PowerShell is now default shell on Windows (v2.1.126+). Scripts written for Bash may not work without adjustment.

2. **WSL2 filesystem performance**: Accessing Windows files from WSL (e.g., `/mnt/c`) is slower than native. Use native WSL paths when possible.

3. **Firewalls and proxies**: HTTP hooks and remote MCP servers blocked by corporate proxies. Configure proxy in settings.

4. **CRLF line endings**: On Windows, shell scripts may have CRLF (`\r\n`). Bash expects LF (`\n`). Git `core.autocrlf` setting affects this.

5. **SSH and Remote Control**: SSH sessions have additional latency. Remote Control not available on all regions/plans.

6. **Cloud environment limits**: Claude Code on the web has resource limits (CPU, memory, storage); long-running operations may time out.

### 5.5 Compaction and Context Window Gotchas

1. **Hooks not re-injected after compaction**: SessionStart hooks fire on compaction but context from prior hooks is lost. Use SessionStart to re-inject context.

2. **Prompt cache invalidated on compaction**: Reduces benefit of prompt caching after context compression.

3. **Context fills unpredictably**: Large codebases, many files, or verbose output can rapidly fill context. Manage proactively.

### 5.6 Checklist: Common Misconfiguration

- [ ] `.claude/settings.json` syntax valid JSON? Use `jq '.' settings.json` to check.
- [ ] Permission rule specifier correct? Bash rules use spaces before `*` for word boundaries.
- [ ] Path patterns in Read/Edit rules use correct anchor (`/` vs `~/` vs `//`)?
- [ ] Hooks disabled with `disableAllHooks: true`?
- [ ] Deny rule from managed settings is blocking and cannot be overridden?
- [ ] MCP server names in `mcp__servername__tool` rules match actual server names?
- [ ] Additional directories set with relative paths but Claude invoked from different directory?
- [ ] Environmental variables in hooks not listed in `allowedEnvVars`?
- [ ] Hook command path absolute and executable?
- [ ] Subagent hooks not loading because `allowManagedHooksOnly` is true?

---

---

# Part 3 — Agents & Automation: Subagents, MCP, Headless/CLI, Agent SDK, CI/CD

**Comprehensive native capabilities reference for advanced Claude Code usage and automation.**

**Last updated:** May 23, 2026 | **Claude Code version:** 2.1.128+ | **Agent SDK:** v0.2.111+ (Python/TypeScript)

---

## 1. Subagents

Subagents are specialized AI assistants that handle specific types of tasks in their own isolated context window. Each subagent runs with a custom system prompt, specific tool access, and independent permissions, allowing them to work autonomously and return results without flooding your main conversation context.

**Source:** [Create custom subagents](https://code.claude.com/docs/en/sub-agents.md)

### 1.1 Built-in Subagents

Claude Code includes four built-in subagents. Each inherits the parent session's permissions with additional tool restrictions.

| Subagent | Model | Tools | Purpose | Context Notes |
| --------- | ----- | ----- | ------- | ------------- |
| **Explore** | Haiku (fast) | Read-only (denied Write, Edit) | File discovery, code search, codebase exploration | Skips CLAUDE.md and git status to keep fast and inexpensive |
| **Plan** | Inherits from main session | Read-only (denied Write, Edit) | Research agent used in plan mode to gather context before presenting a plan | Skips CLAUDE.md and git status; prevents infinite nesting (can't spawn other subagents) |
| **General-purpose** | Inherits from main session | All tools | Complex multi-step tasks requiring both exploration and modification | Inherits all CLAUDE.md and git status from parent |
| **statusline-setup** | Sonnet | All tools | Internal; used when running `/statusline` | Not typically invoked directly |
| **claude-code-guide** | Haiku | Query tools | Internal; answers questions about Claude Code features | Not typically invoked directly |

### 1.2 Subagent File Format

Subagents are defined as **YAML frontmatter + Markdown body**. Store them in priority order:

1. **Managed settings** (organization-wide) — `.claude/agents/` inside managed settings directory (highest priority)
2. **CLI-defined** (`--agents` flag) — JSON passed at launch, session-only
3. **Project scope** (`.claude/agents/`) — Checked into version control, team-shared
4. **User scope** (`~/.claude/agents/`) — Personal, cross-project (default)
5. **Plugin scope** — From installed plugins (lowest priority)

Subagents are discovered recursively. Subdirectories do not affect identity—only the `name` frontmatter field matters. The `filename` does not have to match `name`.

### 1.3 Subagent Frontmatter Fields

Only `name` and `description` are required. All others are optional.

| Field | Type | Default | Description |
| ----- | ---- | ------- | ----------- |
| `name` | string | *required* | Unique identifier using lowercase letters and hyphens. Matches hook `agent_type` field. |
| `description` | string | *required* | When Claude should delegate to this subagent. Claude uses this to decide automatic delegation. |
| `tools` | comma-separated list | All tools inherited | Tools the subagent can use. Examples: `Read, Grep, Glob, Bash`. Overrides inherited tools if set. |
| `disallowedTools` | comma-separated list | None | Tools to deny. Applied first, then `tools` resolved against the remaining pool. |
| `model` | string | `inherit` | Model: `sonnet`, `opus`, `haiku`, a full model ID like `claude-opus-4-7`, or `inherit` (same as parent). |
| `permissionMode` | string | Inherits from parent | `default`, `acceptEdits`, `auto`, `dontAsk`, `bypassPermissions`, or `plan`. Parent `bypassPermissions` or `acceptEdits` takes precedence; parent `auto` always inherited. |
| `maxTurns` | integer | No limit | Maximum number of agentic turns before the subagent stops. |
| `skills` | newline-separated list | None | Skills to preload into subagent context at startup. Full skill content injected (not just descriptions). |
| `mcpServers` | list (inline or by-name refs) | Parent session servers | MCP servers available to this subagent. Inline servers connect at subagent start, disconnect at finish. String references share parent connection. |
| `hooks` | YAML object | None | Lifecycle hooks scoped to this subagent. Supported events: `PreToolUse`, `PostToolUse`, `Stop` (→ `SubagentStop` at runtime). Ignored for plugin subagents. |
| `memory` | string | None | Persistent memory scope: `user` (cross-project), `project` (via version control), or `local` (not checked in). Enables learning across sessions. |
| `background` | boolean | `false` | Run this subagent as a background task by default. Background tasks run concurrently; foreground block. |
| `effort` | string | Inherit from session | `low`, `medium`, `high`, `xhigh`, `max`. Overrides session effort level when active. |
| `isolation` | string | None | Set to `worktree` to run in a temporary git worktree, giving isolated file edits. Worktree auto-cleaned if no changes. |
| `color` | string | None | Display color in task list: `red`, `blue`, `green`, `yellow`, `purple`, `orange`, `pink`, `cyan`. |
| `initialPrompt` | string | None | Auto-submitted first user turn when agent runs as main session (via `--agent` flag). Commands and skills processed. Prepended to user-provided prompt. |

### 1.4 Example Subagent Definition

```markdown
---
name: code-reviewer
description: Expert code review specialist. Proactively reviews code for quality, security, and maintainability. Use immediately after writing or modifying code.
tools: Read, Grep, Glob, Bash
model: sonnet
permissionMode: default
memory: project
---

You are a senior code reviewer ensuring high standards of code quality and security.

When invoked:
1. Run git diff to see recent changes
2. Focus on modified files
3. Begin review immediately

Review checklist:
- Code is clear and readable
- Functions and variables are well-named
- No duplicated code
- Proper error handling
- No exposed secrets or API keys
- Input validation implemented
- Good test coverage
- Performance considerations addressed

Provide feedback organized by priority:
- Critical issues (must fix)
- Warnings (should fix)
- Suggestions (consider improving)

Include specific examples of how to fix issues.
```

### 1.5 Tool Control in Subagents

#### Allowlist Pattern (exclusive)

Set `tools` to specify ONLY the tools available:

```yaml
tools: Read, Grep, Glob, Bash
```

The subagent can only use these four tools. All others blocked.

#### Denylist Pattern (inherited + denials)

Set `disallowedTools` to remove specific tools from inherited set:

```yaml
disallowedTools: Write, Edit
```

The subagent inherits all parent tools except Write and Edit.

#### Restrict Subagent Spawning

Use `Agent(agent_type)` syntax to allow specific subagents only:

```yaml
tools: Agent(worker, researcher), Read, Bash
```

Only `worker` and `researcher` subagents can be spawned. All other spawn attempts fail. Without `Agent` in tools list, no subagents can be spawned.

To allow any subagent: `Agent` (without parentheses) or include in `tools`.

### 1.6 Model Selection in Subagents

Resolution order (first match wins):

1. `CLAUDE_CODE_SUBAGENT_MODEL` environment variable (if set)
2. Per-invocation `model` parameter
3. Subagent definition's `model` frontmatter
4. Main conversation's model

### 1.7 Subagent Context and Isolation

#### What Loads at Subagent Startup

Each subagent starts with a fresh context:

- **System prompt**: The subagent's own prompt (markdown body) + environment details (working directory, etc.), NOT the full Claude Code system prompt
- **Task message**: Delegation prompt Claude writes when handing off work
- **CLAUDE.md and memory**: Full memory hierarchy loaded (except Explore and Plan, which skip CLAUDE.md and git status)
- **Git status**: Snapshot at parent session start (absent if not a git repo or `includeGitInstructions=false`)
- **Preloaded skills**: Full content of skills named in `skills` frontmatter
- **No conversation history**: Subagents do NOT see parent conversation history unless using fork mode

**Exception:** Explore and Plan built-ins skip CLAUDE.md and git status to stay fast and cheap.

#### Context Preservation

Subagent results return to main conversation. Large result summaries consume parent context. When subagents return detailed findings, parent context can still bloat—use multiple subagents or chain them from main conversation.

#### Auto-compaction

Subagents support automatic compaction at ~95% context capacity (overridable via `CLAUDE_AUTOCOMPACT_PCT_OVERRIDE`). Compaction events logged in subagent transcript with `preTokens` count.

### 1.8 Invoking Subagents

#### Automatic Delegation

Claude automatically delegates based on task description + subagent descriptions. To encourage proactive delegation, include "use proactively" in `description` field:

```yaml
description: Code quality specialist. Use proactively after writing or modifying code.
```

#### Explicit @-Mention

Guarantees a specific subagent runs for one task:

```text
@agent-code-reviewer look at the auth changes
```

Or with plugin scope:

```text
@agent-my-plugin:security-reviewer check for vulnerabilities
```

#### Natural Language Request

Name the subagent in your prompt; Claude decides whether to delegate:

```text
Use the test-runner subagent to fix failing tests
```

#### Session-wide Default

Run the entire session as a subagent:

```bash
claude --agent code-reviewer
```

The subagent's system prompt replaces the default Claude Code prompt. Project CLAUDE.md and memory still load. The agent name appears as `@<name>` in the startup header.

For plugin subagents with scoped names:

```bash
claude --agent my-plugin:review:security
```

### 1.9 Background vs Foreground Execution

Subagents run in **foreground** (blocking) or **background** (concurrent) mode:

| Mode | Behavior | Permission Handling |
| ---- | --------- | ------------------- |
| **Foreground** | Blocks main conversation until complete | Permission prompts surface interactively to you |
| **Background** | Runs concurrently; you continue working | Auto-denies prompts; if permission denied, tool call fails but subagent continues |

Claude decides automatically, or you can:

- Ask Claude: "Run this in the background"
- Press **Ctrl+B** to background a running task
- Set `background: true` in frontmatter

To disable all background tasks: set `CLAUDE_CODE_DISABLE_BACKGROUND_TASKS=1`.

In fork mode, all subagent spawns run in background regardless of `background` field.

### 1.10 Resuming Subagents

Each subagent invocation creates a new instance with fresh context. To continue an existing subagent's work:

```text
Use the code-reviewer subagent to review the authentication module
[Agent completes]

Continue that code review and now analyze the authorization logic
[Claude resumes the subagent with full context from previous conversation]
```

Resumed subagents retain full conversation history (all tool calls, results, reasoning). Subagent transcripts persist independently in `~/.claude/projects/{project}/{sessionId}/subagents/agent-{agentId}.jsonl`.

Resumption requires [agent teams](#agent-teams) enabled via `CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS=1`. Claude uses `SendMessage` tool with subagent ID.

### 1.11 Persistent Memory in Subagents

Enable cross-session learning with the `memory` field:

```yaml
memory: project  # or "user" or "local"
```

Locations:

- `user`: `~/.claude/agent-memory/<name-of-agent>/` — knowledge shared across all projects
- `project`: `.claude/agent-memory/<name-of-agent>/` — shareable via version control
- `local`: `.claude/agent-memory-local/<name-of-agent>/` — project-specific, not checked in

When enabled:
- System prompt includes instructions for reading and writing memory directory
- First 200 lines or 25KB of `MEMORY.md` injected into system prompt (whichever comes first)
- Subagent instructed to maintain `MEMORY.md` if it exceeds that size
- Read, Write, Edit tools automatically enabled for memory management

**Best practice:** Ask subagent to consult and update memory explicitly:
```text
Review this PR, check your memory for patterns you've seen before, and save what you learn.
```

### 1.12 Hooks in Subagents

#### Frontmatter Hooks (subagent-specific)

Define hooks in the subagent's markdown file—they run only while that subagent is active:

```yaml
hooks:
  PreToolUse:
    - matcher: "Bash"
      hooks:
        - type: command
          command: "./scripts/validate-command.sh"
  PostToolUse:
    - matcher: "Edit|Write"
      hooks:
        - type: command
          command: "./scripts/run-linter.sh"
```

Supported events: `PreToolUse`, `PostToolUse`, `Stop` (converted to `SubagentStop` at runtime).

#### Settings Hooks (project-level, subagent lifecycle)

Configure hooks in `settings.json` that respond to subagent lifecycle events in the main session:

| Event | Matcher input | When it fires |
| ----- | ------------- | ------------- |
| `SubagentStart` | Agent type name | When a subagent begins execution |
| `SubagentStop` | Agent type name | When a subagent completes |

```json
{
  "hooks": {
    "SubagentStart": [
      {
        "matcher": "db-agent",
        "hooks": [
          { "type": "command", "command": "./scripts/setup-db-connection.sh" }
        ]
      }
    ],
    "SubagentStop": [
      {
        "hooks": [
          { "type": "command", "command": "./scripts/cleanup-db-connection.sh" }
        ]
      }
    ]
  }
}
```

### 1.13 Subagent Limitations

- **No nesting**: Subagents cannot spawn other subagents. Use [agent teams](#agent-teams) or chain from main conversation.
- **Single session**: Subagents work within one session. For parallel independent sessions with own context, see [background agents](/en/agent-view) or [agent teams](#agent-teams).
- **Plugin restrictions**: Plugin subagents ignore `hooks`, `mcpServers`, and `permissionMode` fields (ignored at load time).
- **Tool inheritance**: Subagents that don't specify `tools` inherit parent's full tool set. Parent `bypassPermissions` or `acceptEdits` mode takes precedence and cannot be overridden.
- **Fork mode**: Forked subagents run in background regardless of settings.

---

## Agent Teams

> **Distinct from subagents.** This is the multi-session orchestration feature (env flag `CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS=1`, `SendMessage` tool) referenced from the Subagents section above — not the single-session `Agent`/Task subagents. Experimental/preview.

Agent Teams coordinate **multiple independent Claude Code sessions** working together, with a shared task list, inter-agent messaging, and centralized management ([docs](https://code.claude.com/docs/en/agent-teams.md)). One session is the **lead** (creates the team, spawns teammates, coordinates, synthesizes); others are **teammates** running in separate processes, each with its own context window.

**vs subagents / background agents / worktrees:**

| Feature | How it works | Use when |
|---|---|---|
| **Subagents** | Helper agents within one session; report results back, never talk to each other | Quick focused workers; results summarized into main context |
| **Agent Teams** | Multiple independent CC sessions, shared tasks + direct messaging | Teammates need to share findings, challenge each other, coordinate independently |
| **Agent View** | Dispatch/monitor background sessions from one screen; you assign tasks | Independent tasks you hand off and check on |
| **Worktrees** | Parallel sessions in isolated git checkouts | You run multiple sessions yourself, no automated coordination |

### Enabling

Experimental, **off by default**. Requires Claude Code **v2.1.32+**. Enable via env or `settings.json`:

```json
{ "env": { "CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS": "1" } }
```

### Architecture / roles

- **Lead** — creates the team, spawns teammates, coordinates. The creating session is lead for its lifetime; leadership can't be transferred.
- **Teammates** — separate CC instances, each own context window. Cannot spawn their own teams (no nesting).
- **Shared task list** — items with states pending / in-progress / completed; tasks can depend on others (blocked until deps done). Claiming uses file locking to avoid races; dependencies unblock automatically.
- **Mailbox** — `SendMessage` tool delivers messages automatically (no polling). Lead assigns each teammate a name; any teammate can message any other by name, and the lead. Idle teammates auto-notify the lead.

### Lifecycle

- **Create:** ask Claude in natural language ("create an agent team to…"); Claude spawns teammates and coordinates, or proposes a team if the task warrants it — always with your approval. Specify count/model/role explicitly if wanted ("4 teammates, use Sonnet"; reference a subagent definition by name to reuse a role).
- **Display modes** (`teammateMode` setting / `--teammate-mode`): `in-process` (all in main terminal; Shift+Down to cycle, Ctrl+T toggles task list), `split-panes` (one pane each — needs tmux or iTerm2), `auto` (default: split if already in tmux, else in-process).
- **Interact:** message any teammate directly to redirect/instruct; Enter to view a session, Esc to interrupt.
- **Shut down:** ask the lead to shut down a teammate (it can approve/reject); when done, ask the lead to "clean up the team". **Always clean up via the lead** — teammates may leave resources inconsistent.

### Context, permissions, storage

- Teammates load project context (CLAUDE.md, MCP servers, skills) like a normal session + the lead's spawn prompt; the **lead's conversation history does NOT carry over** — put task details in the spawn prompt.
- When using a subagent definition as a teammate: its `tools` allowlist and `model` are honored, body appended to the system prompt; `SendMessage` + task tools are always available even under a `tools` restriction. The definition's `skills`/`mcpServers` frontmatter is **not** applied (loaded from project/user settings instead).
- Teammates start with the **lead's** permission settings (incl. `--dangerously-skip-permissions`); per-teammate modes can be changed after spawn, not set at spawn time.
- Storage (local, auto-managed — don't hand-edit): team config `~/.claude/teams/{team-name}/config.json` (has `members` array: name/agent-id/agent-type, plus session & tmux pane IDs), task list `~/.claude/tasks/{team-name}/`. No project-level equivalent.

### Quality-gate hooks (team-specific)

- `TeammateIdle` — fires when a teammate is about to go idle; exit 2 to send feedback and keep it working.
- `TaskCreated` — exit 2 to block creation + send feedback.
- `TaskCompleted` — exit 2 to block completion + send feedback.

### Cost & best practices

Token usage scales with active teammates (each is a full Claude instance) — worth it for research/review/new-feature/parallel debugging; **not** for sequential, same-file, or dependency-heavy work (use a single session or subagents). Start with 3–5 teammates, ~5–6 tasks each; size tasks as self-contained deliverables; assign different files per teammate to avoid overwrite conflicts; start with research/review tasks.

### Limitations / gotchas (experimental)

- **`/resume` & `/rewind` don't restore in-process teammates** — lead may message teammates that no longer exist.
- Task status can lag (teammates sometimes don't mark complete → blocks deps; fix manually).
- Shutdown is slow (teammate finishes current request/tool call first).
- **One team per lead at a time** (clean up before creating another); no nested teams; lead is fixed.
- Split panes need tmux/iTerm2 — **not** supported in VS Code integrated terminal, Windows Terminal, or Ghostty (use in-process there).
- Orphaned tmux sessions: `tmux kill-session -t <name>`.

**Sources:** [agent-teams.md](https://code.claude.com/docs/en/agent-teams.md), [agents.md (parallel agents comparison)](https://code.claude.com/docs/en/agents.md), [agent-view.md](https://code.claude.com/docs/en/agent-view.md), [sub-agents.md](https://code.claude.com/docs/en/sub-agents.md). Requires v2.1.32+; v2.1.145 carried agent-team bug fixes.

---

## 2. MCP (Model Context Protocol)

MCP is an open-source standard for AI-tool integrations. MCP servers expose tools, resources, and prompts that Claude can use directly.

**Source:** [Connect Claude Code to tools via MCP](https://code.claude.com/docs/en/mcp.md)

### 2.1 Installing MCP Servers

#### Option 1: Add a Remote HTTP Server

```bash
# Basic syntax
claude mcp add --transport http <name> <url>

# Example: Notion
claude mcp add --transport http notion https://mcp.notion.com/mcp

# With Bearer token authentication
claude mcp add --transport http secure-api https://api.example.com/mcp \
  --header "Authorization: Bearer your-token"
```

HTTP servers are recommended for remote services. The `type: streamable-http` (in JSON) is an alias for `http` transport.

#### Option 2: Add a Remote SSE Server (Deprecated)

```bash
claude mcp add --transport sse <name> <url>
```

SSE (Server-Sent Events) is deprecated in favor of HTTP servers.

#### Option 3: Add a Local Stdio Server

Stdio servers run as local processes. Claude Code sets `CLAUDE_PROJECT_DIR` in the server's environment—use it to resolve project-relative paths without depending on current working directory.

```bash
# Basic syntax
claude mcp add [options] <name> -- <command> [args...]

# Example: Airtable with API key
claude mcp add --transport stdio --env AIRTABLE_API_KEY=YOUR_KEY airtable \
  -- npx -y airtable-mcp-server
```

**Important:** All options (`--transport`, `--env`, `--scope`) come **before** the server name. The `--` (double dash) separates options from the command.

### 2.2 MCP Installation Scopes

Scopes control which projects load the server and whether it's shared with your team.

| Scope | Loads in | Shared with team | Storage | Priority |
| ----- | -------- | ---------------- | ------- | -------- |
| **Local** | Current project only | No | `~/.claude.json` (project entry) | Highest |
| **Project** | Current project only | Yes (via `.mcp.json`) | `.mcp.json` in project root | Middle |
| **User** | All your projects | No | `~/.claude.json` (global entry) | Lowest |
| **Plugin-provided** | Where plugin enabled | Via plugin installation | Plugin's `.mcp.json` | Below user |
| **Claude.ai connectors** | From Claude.ai account | Yes (if Team/Enterprise admin) | Cloud (Claude.ai) | Lowest |

#### Scope Selection

```bash
# Local scope (default, private to you in this project)
claude mcp add --transport http stripe https://mcp.stripe.com

# Project scope (shared via version control)
claude mcp add --transport http paypal --scope project https://mcp.paypal.com/mcp

# User scope (all your projects, private)
claude mcp add --transport http hubspot --scope user https://mcp.hubspot.com/anthropic
```

#### Scope Precedence

When the same server is defined at multiple scopes, Claude connects once using the highest-precedence definition:

1. Local scope (highest)
2. Project scope
3. User scope
4. Plugin-provided servers
5. Claude.ai connectors (lowest)

Plugins and connectors match by endpoint; local/project/user match by name.

### 2.3 MCP Server Management

```bash
# List all configured servers
claude mcp list

# Get details for a specific server
claude mcp get github

# Remove a server
claude mcp remove github

# Check server status (within Claude Code session)
/mcp
```

The `/mcp` panel shows tool count per server and flags servers that advertise tools but expose none.

### 2.4 Authentication

#### OAuth 2.0 (Recommended for Remote Servers)

When a server requires authentication, Claude Code marks it in `/mcp` with a prompt to complete the OAuth flow:

```bash
# Add a server that requires auth
claude mcp add --transport http sentry https://mcp.sentry.dev/mcp

# Within Claude Code, trigger auth
/mcp
# Follow browser prompt to login
```

Claude Code automatically discovers OAuth endpoints via RFC 9728 (Protected Resource Metadata) or RFC 8414 (authorization server metadata). Tokens are stored securely and refreshed automatically.

#### Pre-configured OAuth Credentials

For servers that don't support Dynamic Client Registration, register an OAuth app first, then provide credentials:

```bash
claude mcp add --transport http \
  --client-id your-client-id --client-secret --callback-port 8080 \
  my-server https://mcp.example.com/mcp
```

The `--client-secret` flag prompts for the secret with masked input. Secrets are stored securely (macOS keychain, Windows credentials file).

#### Custom Authentication via Headers Helper

For non-OAuth schemes (Kerberos, short-lived tokens, internal SSO):

```json
{
  "mcpServers": {
    "internal-api": {
      "type": "http",
      "url": "https://mcp.internal.example.com",
      "headersHelper": "/opt/bin/get-mcp-auth-headers.sh"
    }
  }
}
```

The helper command runs at connection time and outputs JSON with header key-value pairs. Claude Code sets `CLAUDE_CODE_MCP_SERVER_NAME` and `CLAUDE_CODE_MCP_SERVER_URL` environment variables for the helper.

### 2.5 Environment Variable Expansion in `.mcp.json`

Supports `${VAR}` and `${VAR:-default}` syntax in command, args, env, url, and headers:

```json
{
  "mcpServers": {
    "api-server": {
      "type": "http",
      "url": "${API_BASE_URL:-https://api.example.com}/mcp",
      "headers": {
        "Authorization": "Bearer ${API_KEY}"
      }
    }
  }
}
```

If a required variable is not set and has no default, config parsing fails.

### 2.6 Dynamic Tool Updates

MCP servers can send `list_changed` notifications to update tools, prompts, and resources without reconnecting:

```bash
# Within Claude Code
/mcp
# Reconnect or refresh individual servers
```

Claude Code automatically refreshes available capabilities from servers sending `list_changed`.

### 2.7 Automatic Reconnection

HTTP and SSE servers auto-reconnect with exponential backoff (up to 5 attempts, 1s → 2s → 4s → 8s → 16s). After 5 failed attempts, server marked as failed. Retry manually via `/mcp`.

Transient errors (5xx, connection refused, timeout) retry 3 times on initial connection. Authentication and not-found errors do not retry (require config change).

Stdio servers are local processes and do not auto-reconnect.

### 2.8 Plugin-Provided MCP Servers

Plugins can bundle MCP servers in `.mcp.json` or inline in `plugin.json`:

**In plugin root `.mcp.json`:**
```json
{
  "mcpServers": {
    "database-tools": {
      "command": "${CLAUDE_PLUGIN_ROOT}/servers/db-server",
      "args": ["--config", "${CLAUDE_PLUGIN_ROOT}/config.json"],
      "env": {
        "DB_URL": "${DB_URL}"
      }
    }
  }
}
```

**Or inline in `plugin.json`:**
```json
{
  "name": "my-plugin",
  "mcpServers": {
    "plugin-api": {
      "command": "${CLAUDE_PLUGIN_ROOT}/servers/api-server",
      "args": ["--port", "8080"]
    }
  }
}
```

Servers start automatically when plugin is enabled. Environment variables available: `${CLAUDE_PLUGIN_ROOT}`, `${CLAUDE_PLUGIN_DATA}`, `${CLAUDE_PROJECT_DIR}`.

### 2.9 MCP Resources

MCP servers can expose resources that you reference using @ mentions (like file references):

```text
@github:issue://123 suggests a fix
Can you analyze @postgres:schema://users?
```

Resources appear in @ mention autocomplete alongside files. Paths are fuzzy-searchable.

### 2.10 MCP Prompts as Commands

MCP servers can expose prompts that become available as slash commands:

```bash
# Discover available prompts
/

# Execute a prompt without arguments
/mcp__github__list_prs

# Execute with arguments
/mcp__github__pr_review 456
/mcp__jira__create_issue "Bug in login flow" high
```

Server and prompt names are normalized (spaces become underscores).

### 2.11 Tool Search (Context Optimization)

Tool search keeps MCP context usage low by deferring tool definitions until needed.

#### How It Works

- **Default:** All MCP tools deferred; Claude searches to discover relevant ones
- **Tool names only:** Only tool names load in context at session start (~200 bytes each)
- **On demand:** When Claude needs tools, `ToolSearch` discovers and loads schemas

This makes adding many MCP servers nearly zero-cost in context.

#### Configure Tool Search

```bash
# Defer all tools (default)
ENABLE_TOOL_SEARCH=true claude

# Threshold mode: load upfront if <10% of context, defer otherwise
ENABLE_TOOL_SEARCH=auto:10 claude

# Load all tools upfront (no deferral)
ENABLE_TOOL_SEARCH=false claude
```

Or in `settings.json`:
```json
{
  "env": {
    "ENABLE_TOOL_SEARCH": "auto:5"
  }
}
```

#### Exempt a Server from Deferral

If a server's tools should always be visible:

```json
{
  "mcpServers": {
    "core-tools": {
      "type": "http",
      "url": "https://mcp.example.com/mcp",
      "alwaysLoad": true
    }
  }
}
```

Every tool from that server loads at session start. Individual tools can also set `"anthropic/alwaysLoad": true` in their `_meta` object.

**Constraint:** `alwaysLoad: true` blocks startup until server connects (capped at standard 5-second timeout). Other servers continue connecting in background.

### 2.12 MCP Output Limits

MCP tool outputs are monitored to prevent context bloat:

- **Warning threshold:** 10,000 tokens → warning displayed
- **Default max:** 25,000 tokens per tool output
- **Configurable:** `MAX_MCP_OUTPUT_TOKENS` environment variable

Individual tools can declare their own limit via `_meta["anthropic/maxResultSizeChars"]` (up to 500,000 chars):

```json
{
  "name": "get_schema",
  "description": "Returns the full database schema",
  "_meta": {
    "anthropic/maxResultSizeChars": 200000
  }
}
```

The tool-specific limit applies independently of `MAX_MCP_OUTPUT_TOKENS` for text content. Image tools still subject to token limits.

### 2.13 MCP Tool Timeouts

Per-server tool execution timeout (default: system-wide `MCP_TOOL_TIMEOUT`):

```json
{
  "mcpServers": {
    "slow-api": {
      "type": "http",
      "url": "https://slow.example.com/mcp",
      "timeout": 600000
    }
  }
}
```

Units: milliseconds. Minimum: 1000ms. Values below 1000ms floored to 1 second. This is a hard wall-clock limit; progress notifications do not extend it.

### 2.14 MCP via JSON Configuration

Add servers directly from JSON:

```bash
# Add HTTP server from JSON
claude mcp add-json weather-api \
  '{"type":"http","url":"https://api.weather.com/mcp","headers":{"Authorization":"Bearer token"}}'

# Add stdio server from JSON
claude mcp add-json local-weather \
  '{"type":"stdio","command":"/path/to/weather-cli","args":["--api-key","abc123"]}'

# Add HTTP server with pre-configured OAuth
claude mcp add-json my-server \
  '{"type":"http","url":"https://mcp.example.com/mcp","oauth":{"clientId":"your-client-id","callbackPort":8080}}' \
  --client-secret
```

### 2.15 Import MCP Servers from Claude Desktop

If you've configured MCP servers in Claude Desktop:

```bash
claude mcp add-from-claude-desktop
```

Interactive dialog lets you select which servers to import. Works on macOS and WSL. Servers import with same names; duplicates get numerical suffixes.

### 2.16 Use Claude Code as an MCP Server

Make Claude Code itself available to other applications:

```bash
claude mcp serve
```

This starts Claude as a stdio MCP server. Use in Claude Desktop:

```json
{
  "mcpServers": {
    "claude-code": {
      "type": "stdio",
      "command": "claude",
      "args": ["mcp", "serve"]
    }
  }
}
```

The command must reference the Claude Code executable. If `claude` is not in PATH, use full path:

```bash
which claude  # Find full path
```

### 2.17 Elicitation (User Input from MCP)

MCP servers can request structured input from you mid-task using elicitation. Two patterns:

1. **Form mode:** Claude displays a dialog with form fields defined by the server
2. **URL mode:** Claude opens a browser URL for authentication or approval

Dialogs appear automatically; no configuration needed. To auto-respond without showing dialogs:

```bash
# Use the Elicitation hook (configure in settings.json or as project hook)
```

If you're building an MCP server that uses elicitation, see the [MCP elicitation specification](https://modelcontextprotocol.io/docs/learn/client-concepts#elicitation).

### 2.18 Managed MCP Configuration

Organizations can centralize MCP server control via `managed-mcp.json` and settings policies. See [Managed MCP configuration](/en/managed-mcp) for organization-wide server deployment and restrictions.

### 2.19 MCP Limitations

- **Prompt injection risk:** MCP servers that fetch external content can expose you to prompt injection. Verify you trust each server before connecting.
- **Authentication:** Servers returning 401/403 flag in `/mcp` for OAuth flow. Custom schemes require `headersHelper`.
- **Network access:** Requires network connectivity for remote servers. Local servers do not require external access.
- **Output size:** Large tool outputs can bloat context. Use `MAX_MCP_OUTPUT_TOKENS` or per-tool limits.
- **Reserved server name:** Server named `workspace` is reserved; Claude Code skips it at load with a warning.

---

## 3. Headless / CLI Automation

Run Claude Code non-interactively from scripts, CI/CD pipelines, and programmatic contexts. The `-p` (print) flag enables headless mode.

**Source:** [Run Claude Code programmatically](https://code.claude.com/docs/en/headless.md) | [CLI reference](https://code.claude.com/docs/en/cli-reference.md)

### 3.1 Basic Headless Usage

```bash
# Simplest: pass a prompt, get text response
claude -p "Find and fix the bug in auth.py"

# Continue a previous session
claude -p "Now optimize the database queries" --continue

# Resume a specific session by ID
claude -p "Finish the review" --resume "abc123-def456"
```

The `-p` flag (alias `--print`) runs non-interactively and exits after completion. All CLI flags work with `-p`.

### 3.2 Bare Mode

Bare mode (`--bare`) skips auto-discovery for faster startup in CI/CD:

```bash
claude --bare -p "Summarize this file" --allowedTools "Read"
```

Skips:
- Hooks (Setup, SessionStart, PreToolUse, etc.)
- Skills
- Plugins
- MCP servers
- Auto memory
- CLAUDE.md files

**Only explicitly passed flags take effect.** On every machine, bare mode loads consistently. Useful for reproducible CI runs.

**Note:** `--bare` will become the default for `-p` in a future release.

### 3.3 Output Formats

Control output structure with `--output-format`:

#### Text (Default)

```bash
claude -p "Explain this codebase"
```

Plain text response printed to stdout.

#### JSON

```bash
claude -p "Summarize this project" --output-format json
```

Structured JSON response with metadata:

```json
{
  "result": "...",
  "session_id": "abc123",
  "usage": {
    "input_tokens": 1234,
    "output_tokens": 567,
    "total_cost_usd": 0.05
  },
  "model": "claude-sonnet-4-6"
}
```

#### Stream-JSON (Real-time Streaming)

```bash
claude -p "Write a poem" --output-format stream-json --verbose --include-partial-messages
```

Newline-delimited JSON streaming in real-time. Each line is an event object:

```json
{"type":"stream_event","event":{"delta":{"type":"text_delta","text":"The "}}}
{"type":"stream_event","event":{"delta":{"type":"text_delta","text":"moon "}}}
...
{"type":"result","result":"The moon shines..."}
```

**Useful for:** live progress display, real-time logging, streaming UI updates.

Filter for text deltas with `jq`:

```bash
claude -p "Write a poem" --output-format stream-json --verbose --include-partial-messages | \
  jq -rj 'select(.type == "stream_event" and .event.delta.type? == "text_delta") | .event.delta.text'
```

#### Structured Output (JSON Schema)

```bash
claude -p "Extract function names from auth.py" \
  --output-format json \
  --json-schema '{"type":"object","properties":{"functions":{"type":"array","items":{"type":"string"}}},"required":["functions"]}'
```

Response validates against the schema and is returned in the `structured_output` field:

```json
{
  "structured_output": {
    "functions": ["login", "logout", "refresh_token"]
  },
  "result": "...",
  "session_id": "...",
  "usage": {...}
}
```

### 3.4 Tool Permissions

#### Allow Specific Tools Without Prompting

```bash
claude -p "Run the test suite and fix failures" \
  --allowedTools "Bash,Read,Edit"
```

Tools listed execute without permission prompts. Uses [permission rule syntax](#permission-rule-syntax).

#### Permission Mode

Start in a specific permission mode:

```bash
# Accept file edits and common filesystem operations without asking
claude -p "Apply the lint fixes" --permission-mode acceptEdits

# Deny anything not pre-approved
claude -p "Generate a report" --permission-mode dontAsk

# Skip all prompts (use with caution)
claude -p "Cleanup temp files" --permission-mode bypassPermissions
```

| Mode | Behavior | Use Case |
| ---- | --------- | -------- |
| `default` | Require permission for each action | Interactive workflows |
| `acceptEdits` | Auto-approve file edits and filesystem commands | Trusted development |
| `plan` | Read-only exploration | Analysis-only runs |
| `auto` | Background classifier approves/denies each call | Autonomous agents with guardrails |
| `dontAsk` | Deny anything not in `permissions.allow` or read-only commands | Locked-down CI |
| `bypassPermissions` | Skip all checks | Sandboxed CI, fully trusted |

### 3.5 Permission Rule Syntax

Rules use patterns for prefix matching and scoping:

```bash
# Read-only tools
--allowedTools "Read"

# Specific command prefix
--allowedTools "Bash(git log *)"

# Multiple rules, space-separated
--allowedTools "Bash(git diff *)" "Bash(git commit *)" "Read,Edit"

# Deny rules (removes from inherited or allowed set)
--disallowedTools "Bash(rm *)" "Edit"
```

The trailing space before `*` is **required** for prefix matching: `Bash(git diff *)` matches `git diff HEAD` but NOT `git diff-index`.

### 3.6 Piping Data

Pipe stdin through Claude:

```bash
# Pipe a build log for analysis
cat build-error.txt | claude -p "Explain the root cause of this error" > analysis.txt

# Pipe a diff for security review
git diff main | claude -p \
  --append-system-prompt "Review for security vulnerabilities" \
  --output-format json > review.json
```

**Stdin cap:** As of Claude Code v2.1.128, piped stdin is capped at 10MB. Exceeding the cap exits with error and non-zero status. For larger inputs, write to a file and reference the path in your prompt.

### 3.7 Continue and Resume Sessions

#### Continue Most Recent

```bash
# First run
claude -p "Analyze this codebase"

# Continue the most recent session
claude -p "Now find performance bottlenecks" --continue
```

#### Resume by Session ID

```bash
# Capture session ID
session_id=$(claude -p "Start analysis" --output-format json | jq -r '.session_id')

# Use it later
claude -p "Continue analyzing" --resume "$session_id"
```

#### Fork a Session

```bash
# Fork instead of resuming (creates new session ID)
claude -p "Try a different approach" --resume "abc123" --fork-session
```

### 3.8 System Prompt Customization

Four flags for modifying the system prompt:

| Flag | Behavior |
| ---- | --------- |
| `--system-prompt` | Replace entire default prompt |
| `--system-prompt-file` | Replace with file contents |
| `--append-system-prompt` | Append to default prompt |
| `--append-system-prompt-file` | Append file contents |

```bash
# Append custom instructions (recommended for -p scripts)
claude -p "Fix the bug" \
  --append-system-prompt "You are a Python expert. Always follow PEP 8."

# Replace entirely (drops all default guidance)
claude -p "Summarize" \
  --system-prompt "You are a technical writer. Be concise."
```

**Decision:** Use append flags when Claude should remain a coding assistant with extra rules. Use replace flags when changing the agent's role entirely.

### 3.9 Input Formats

Specify input format for stream-json mode:

```bash
# Read events from stdin (e.g., from another agent)
claude -p --input-format stream-json --output-format stream-json
```

Allows chaining agents: one agent's stream-json output becomes another agent's stream-json input.

### 3.10 Max Turns

Limit the number of agentic turns (print mode only):

```bash
# Stop after 3 turns, report error if limit hit
claude -p "Fix the test" --max-turns 3
```

Exits with error when limit reached. Useful for cost control in CI.

### 3.11 Max Budget

Limit spending before stopping:

```bash
# Stop when $5 spent on API calls
claude -p "Analyze this huge codebase" --max-budget-usd 5.00
```

### 3.12 Exit Codes

| Code | Meaning |
| ---- | --------- |
| 0 | Success |
| 1 | API or execution error; session not found; model not available |
| 2 | User canceled (interactive mode only) |
| Varies | Hook or tool execution failure (see hook docs) |

Check exit code to determine success:

```bash
if claude -p "query" --max-turns 5; then
  echo "Success"
else
  echo "Failed with code $?"
fi
```

### 3.13 Hooks in Headless Mode

Hooks run in `-p` mode the same way they do interactively:

```bash
claude -p "query" --init          # Run Setup hooks with "init" matcher first
claude -p "query" --maintenance   # Run Setup hooks with "maintenance" matcher first
```

Include hook events in output stream:

```bash
claude -p --output-format stream-json --include-hook-events "query"
```

### 3.14 API Retry Events

When API requests fail with retryable errors, Claude emits `system/api_retry` events before retrying:

```json
{
  "type": "system",
  "subtype": "api_retry",
  "attempt": 1,
  "max_retries": 3,
  "retry_delay_ms": 1000,
  "error_status": 429,
  "error": "rate_limit"
}
```

Use to display retry progress or implement custom backoff.

### 3.15 Plugin Installation Events

When `CLAUDE_CODE_SYNC_PLUGIN_INSTALL` is set, emits `system/plugin_install` events as plugins install:

```json
{
  "type": "system",
  "subtype": "plugin_install",
  "status": "started|installed|failed|completed",
  "name": "plugin-name",
  "error": "error message if failed"
}
```

Use to surface install progress in custom UI.

### 3.16 Session Persistence

By default, `-p` sessions persist to `~/.claude/projects/` and can be resumed later. To disable:

```bash
claude -p "query" --no-session-persistence
```

Sessions are not saved; they cannot be resumed. Useful for one-off tasks.

### 3.17 Practical Examples

#### Add Claude to a Build Script

```json
{
  "scripts": {
    "lint:claude": "git diff main | claude -p \"you are a typo linter. for each typo in this diff, report filename:line on one line and the issue on the next. return nothing else.\""
  }
}
```

Pipes diff through Claude; no Bash permission needed (data comes via stdin).

#### Commit with Claude Review

```bash
claude -p "Look at my staged changes and create an appropriate commit" \
  --allowedTools "Bash(git diff *),Bash(git log *),Bash(git status *),Bash(git commit *)"
```

Pre-approves git commands for automated commits.

#### Extract Structured Data

```bash
claude -p "Extract the main function names from auth.py" \
  --output-format json \
  --json-schema '{"type":"object","properties":{"functions":{"type":"array","items":{"type":"string"}}},"required":["functions"]}' \
  | jq '.structured_output.functions'
```

Returns validated JSON array of function names.

#### Security Review of PR

```bash
gh pr diff "$1" | claude -p \
  --append-system-prompt "You are a security engineer. Review for vulnerabilities." \
  --output-format json | jq '.result'
```

Pipes GitHub PR diff through Claude for security analysis.

### 3.18 Headless Limitations

- **No interactive prompts:** Permission prompts are not surfaced in headless mode. Use `--permission-mode` and `--allowedTools` to pre-approve.
- **No user input:** Skills and commands requiring user input are not available. Use prompts and `--append-system-prompt` instead.
- **No skills with user interaction:** Custom skills that prompt the user do not work in `-p` mode. Only read-only or scripted skills suitable.
- **Stdin cap:** Piped input capped at 10MB. For larger data, write to files.

---

## 4. Agent SDK

The Agent SDK is a Python and TypeScript library that provides Claude Code capabilities as a programmatic API. Build autonomous agents with full tool execution, session management, and context control.

**Sources:**
- [Agent SDK overview](https://code.claude.com/docs/en/agent-sdk/overview.md)
- [Agent SDK quickstart](https://code.claude.com/docs/en/agent-sdk/quickstart.md)

### 4.1 What is the Agent SDK?

The Agent SDK gives you:

- **Same tools as Claude Code:** Read, Write, Edit, Bash, Glob, Grep, WebSearch, WebFetch, and more
- **Built-in agent loop:** Claude autonomously reads files, runs commands, observes results, and decides next steps
- **Session management:** Persist context across multiple interactions; resume, fork, and introspect sessions
- **Tool execution:** No need to implement tool handlers—the SDK executes tools natively
- **Hooks:** Custom code at key points (before/after tool use, session lifecycle)
- **Subagents:** Spawn specialized agents for focused subtasks
- **MCP integration:** Connect to external systems via the Model Context Protocol
- **Permissions & approval callbacks:** Full control over what agents can do

Available in **Python** and **TypeScript**. Requires Node.js 18+ or Python 3.10+.

### 4.2 Installation

#### Python

```bash
# Using uv (recommended)
uv init && uv add claude-agent-sdk

# Using pip
python3 -m venv .venv && source .venv/bin/activate
pip3 install claude-agent-sdk
```

#### TypeScript/JavaScript

```bash
npm install @anthropic-ai/claude-agent-sdk
```

The TypeScript SDK bundles a native Claude Code binary, so you don't need to install Claude Code separately.

### 4.3 Authentication

Set `ANTHROPIC_API_KEY` from the [Claude Console](https://platform.claude.com/):

```bash
export ANTHROPIC_API_KEY=your-api-key
```

Alternatively, create a `.env` file:

```
ANTHROPIC_API_KEY=your-api-key
```

The SDK also supports cloud provider authentication:

- **Amazon Bedrock:** `CLAUDE_CODE_USE_BEDROCK=1` + AWS credentials
- **Claude Platform on AWS:** `CLAUDE_CODE_USE_ANTHROPIC_AWS=1` + `ANTHROPIC_AWS_WORKSPACE_ID` + AWS credentials
- **Google Vertex AI:** `CLAUDE_CODE_USE_VERTEX=1` + Google Cloud credentials
- **Microsoft Azure:** `CLAUDE_CODE_USE_FOUNDRY=1` + Azure credentials

See setup guides for [Bedrock](/en/amazon-bedrock), [Claude Platform on AWS](/en/claude-platform-on-aws), [Vertex AI](/en/google-vertex-ai), [Azure AI Foundry](/en/microsoft-foundry).

### 4.4 Core Concepts: The `query()` Function

The `query()` function is the entry point for agent execution. It returns an async iterator over message objects:

#### Python

```python
import asyncio
from claude_agent_sdk import query, ClaudeAgentOptions, AssistantMessage, ResultMessage

async def main():
    async for message in query(
        prompt="Find and fix the bug in auth.py",
        options=ClaudeAgentOptions(
            allowed_tools=["Read", "Edit", "Bash"],
            permission_mode="acceptEdits"
        )
    ):
        if isinstance(message, AssistantMessage):
            print(f"Claude: {message.content}")
        elif isinstance(message, ResultMessage):
            print(f"Done: {message.result}")

asyncio.run(main())
```

#### TypeScript

```typescript
import { query } from "@anthropic-ai/claude-agent-sdk";

async function main() {
  for await (const message of query({
    prompt: "Find and fix the bug in auth.ts",
    options: {
      allowedTools: ["Read", "Edit", "Bash"],
      permissionMode: "acceptEdits"
    }
  })) {
    if (message.type === "assistant" && message.message?.content) {
      console.log("Claude:", message.message.content);
    } else if (message.type === "result") {
      console.log("Done:", message.result);
    }
  }
}

main();
```

### 4.5 ClaudeAgentOptions (Python) / options (TypeScript)

Configuration passed to `query()`:

#### Python

```python
ClaudeAgentOptions(
    allowed_tools=["Read", "Edit", "Bash"],
    permission_mode="acceptEdits",
    system_prompt="You are a Python expert. Follow PEP 8.",
    model="claude-sonnet-4-6",
    effort="high",
    mcp_servers={"slack": {"command": "..."}},
    hooks={"PreToolUse": [...]},
    agents={"reviewer": AgentDefinition(...)},
    max_turns=10,
    resume=session_id
)
```

#### TypeScript

```typescript
{
  allowedTools: ["Read", "Edit", "Bash"],
  permissionMode: "acceptEdits",
  systemPrompt: "You are a Python expert. Follow PEP 8.",
  model: "claude-sonnet-4-6",
  effort: "high",
  mcpServers: { slack: { command: "..." } },
  hooks: { PreToolUse: [...] },
  agents: { reviewer: { description: "...", prompt: "..." } },
  maxTurns: 10,
  resume: sessionId
}
```

| Option | Type | Default | Description |
| ------ | ---- | ------- | ----------- |
| `allowed_tools` | list | All tools | Tools the agent can use: `["Read", "Edit", "Bash", "Glob", "Grep", "WebSearch", "WebFetch", "Monitor", "AskUserQuestion"]` |
| `permission_mode` | string | `"default"` | `"default"`, `"acceptEdits"`, `"plan"`, `"auto"`, `"dontAsk"`, `"bypassPermissions"` |
| `system_prompt` | string | Default Claude Code prompt | Replace system prompt entirely |
| `model` | string | Latest Sonnet | Model: `"sonnet"`, `"opus"`, `"haiku"`, or full ID like `"claude-opus-4-7"` |
| `effort` | string | `"medium"` | `"low"`, `"medium"`, `"high"`, `"xhigh"`, `"max"` (model-dependent) |
| `mcp_servers` | dict | None | MCP server config: `{"name": {"type": "stdio", "command": "..."}}` |
| `hooks` | dict | None | Lifecycle hooks: `{"PreToolUse": [{"matcher": "Edit", "hooks": [callback]}]}` |
| `agents` | dict | Built-ins | Custom subagent definitions |
| `max_turns` | int | No limit | Stop after N agentic turns |
| `resume` | str | None | Session ID to resume |
| `can_use_tool` | callback | None | (TypeScript) Approval callback for tool use |
| `setting_sources` | list | `["user", "project", "local"]` | Which settings to load: `["user"]`, `["project", "local"]`, etc. |

### 4.6 Available Tools

Built-in tools the agent can use (if not denied):

| Tool | What it does | Example |
| ---- | ------------ | ------- |
| **Read** | Read file contents | `Read("src/auth.py")` |
| **Write** | Create new files | `Write("test.py", "print('hello')")` |
| **Edit** | Make precise edits to existing files | Edit line ranges, replace text |
| **Bash** | Run terminal commands, scripts, git | `Bash("npm test")`, `Bash("git log")` |
| **Glob** | Find files by pattern | `Glob("**/*.ts")`, `Glob("src/**/*.py")` |
| **Grep** | Search file contents with regex | `Grep("TODO", "**/*.py")` |
| **WebSearch** | Search the web for current info | `WebSearch("Claude API documentation")` |
| **WebFetch** | Fetch and parse web page content | `WebFetch("https://example.com")` |
| **Monitor** | Watch a background script and react per-line | `Monitor("npm start")` |
| **AskUserQuestion** | Ask clarifying questions with choices | For interactive approval flows |
| **Agent** | Spawn subagents | Requires subagent definitions |

### 4.7 Message Types

Messages yielded from `query()`:

#### System Messages (initialization and events)

```python
if message.type == "system":
    if message.subtype == "init":
        print(f"Session: {message.session_id}")
        print(f"Model: {message.data['model']}")
```

#### Assistant Messages (Claude's reasoning and tool calls)

```python
if isinstance(message, AssistantMessage):
    for block in message.content:
        if hasattr(block, "text"):
            print(f"Reasoning: {block.text}")
        elif hasattr(block, "name"):
            print(f"Tool: {block.name}")
```

#### Tool Use Messages

```python
if message.type == "tool_use":
    print(f"Tool: {message.tool_name}")
    print(f"Input: {message.tool_input}")
```

#### Tool Result Messages

```python
if message.type == "tool_result":
    print(f"Result: {message.result}")
```

#### Result Messages (final outcome)

```python
if isinstance(message, ResultMessage):
    print(f"Final result: {message.result}")
    print(f"Session ID: {message.session_id}")
    print(f"Usage: {message.usage}")
```

### 4.8 Streaming vs. Single-Turn Mode

#### Streaming (Recommended)

Agent yields messages in real-time as it works:

```python
async for message in query(prompt="...", options=...):
    # Process each message as it arrives
    if isinstance(message, AssistantMessage):
        print(message.content)
```

Ideal for: live progress display, real-time logging, user-facing UIs.

#### Collect All Messages

```python
messages = []
async for message in query(prompt="...", options=...):
    messages.append(message)

# Process after completion
for msg in messages:
    print(msg)
```

Ideal for: batch processing, CI/CD pipelines, post-analysis.

### 4.9 Hooks (Callbacks)

Run custom code at key points in the agent lifecycle:

#### Python Example: Log File Changes

```python
async def log_edit(input_data, tool_use_id, context):
    file_path = input_data.get("tool_input", {}).get("file_path", "unknown")
    with open("./audit.log", "a") as f:
        f.write(f"{datetime.now()}: modified {file_path}\n")
    return {}  # Must return dict

async def main():
    async for message in query(
        prompt="Refactor utils.py",
        options=ClaudeAgentOptions(
            permission_mode="acceptEdits",
            hooks={
                "PostToolUse": [
                    HookMatcher(matcher="Edit|Write", hooks=[log_edit])
                ]
            }
        )
    ):
        if isinstance(message, ResultMessage):
            print(message.result)
```

#### TypeScript Example

```typescript
const logEdit: HookCallback = async (input) => {
  const filePath = (input as any).tool_input?.file_path ?? "unknown";
  await appendFile("./audit.log", `${new Date().toISOString()}: modified ${filePath}\n`);
  return {};
};

for await (const message of query({
  prompt: "Refactor utils.ts",
  options: {
    permissionMode: "acceptEdits",
    hooks: {
      PostToolUse: [{ matcher: "Edit|Write", hooks: [logEdit] }]
    }
  }
})) {
  if ("result" in message) console.log(message.result);
}
```

Supported hooks: `PreToolUse`, `PostToolUse`, `Stop`, `SessionStart`, `SessionEnd`, `UserPromptSubmit`.

### 4.10 Subagents in the SDK

Spawn specialized agents:

#### Python

```python
async def main():
    async for message in query(
        prompt="Use the code-reviewer agent to review this codebase",
        options=ClaudeAgentOptions(
            allowed_tools=["Read", "Glob", "Grep", "Agent"],
            agents={
                "code-reviewer": AgentDefinition(
                    description="Expert code reviewer for quality and security reviews.",
                    prompt="Analyze code quality and suggest improvements.",
                    tools=["Read", "Glob", "Grep"]
                )
            }
        )
    ):
        if isinstance(message, ResultMessage):
            print(message.result)
```

#### TypeScript

```typescript
for await (const message of query({
  prompt: "Use the code-reviewer agent to review this codebase",
  options: {
    allowedTools: ["Read", "Glob", "Grep", "Agent"],
    agents: {
      "code-reviewer": {
        description: "Expert code reviewer for quality and security reviews.",
        prompt: "Analyze code quality and suggest improvements.",
        tools: ["Read", "Glob", "Grep"]
      }
    }
  }
})) {
  if ("result" in message) console.log(message.result);
}
```

Messages from subagents include `parent_tool_use_id`, allowing you to track which messages belong to which subagent.

### 4.11 MCP Integration

Connect to external tools via MCP:

```python
async for message in query(
    prompt="Open example.com and describe what you see",
    options=ClaudeAgentOptions(
        mcp_servers={
            "playwright": {
                "type": "stdio",
                "command": "npx",
                "args": ["-y", "@playwright/mcp@latest"]
            }
        }
    )
):
    if isinstance(message, ResultMessage):
        print(message.result)
```

### 4.12 Permissions & Approval Callbacks

#### Pre-approve Tools

```python
ClaudeAgentOptions(allowed_tools=["Read", "Edit", "Bash"])
```

#### Require Approval Callbacks (TypeScript)

```typescript
const canUseTool = async (input) => {
  console.log(`Request: ${input.tool_name}`);
  const approved = await userApproval(input.tool_name);
  return approved;
};

for await (const message of query({
  prompt: "Fix the bug",
  options: {
    permissionMode: "default",
    canUseTool
  }
})) {
  // ...
}
```

### 4.13 Sessions & Context Persistence

#### Capture Session ID

```python
async for message in query(
    prompt="Analyze the auth module",
    options=ClaudeAgentOptions(allowed_tools=["Read", "Glob"])
):
    if isinstance(message, SystemMessage) and message.subtype == "init":
        session_id = message.data["session_id"]
        print(f"Session: {session_id}")
```

#### Resume a Session

```python
async for message in query(
    prompt="Now find all places that call it",
    options=ClaudeAgentOptions(resume=session_id)
):
    if isinstance(message, ResultMessage):
        print(message.result)
```

Full context from the first query carries forward.

### 4.14 Claude Code Features via SDK

The SDK can load Claude Code's filesystem-based configuration:

| Feature | Location | How to use |
| ------- | -------- | ---------- |
| **Skills** | `.claude/skills/*/SKILL.md` | Loads automatically; agents can invoke them |
| **Slash commands** | `.claude/commands/*.md` | Loads automatically |
| **CLAUDE.md** | `.claude/CLAUDE.md` or `CLAUDE.md` | Loaded as system context |
| **Plugins** | Via `plugins` option | Programmatic plugin loading |

Control which sources load:

```python
ClaudeAgentOptions(setting_sources=["user", "project"])  # Skip "local"
```

### 4.15 Cost Tracking

Extract cost from message objects:

```python
if isinstance(message, ResultMessage):
    usage = message.usage
    print(f"Input tokens: {usage.input_tokens}")
    print(f"Output tokens: {usage.output_tokens}")
    # Approximate cost (multiply by current rates)
    cost = (usage.input_tokens * 0.003 + usage.output_tokens * 0.015) / 1000
    print(f"Cost: ${cost:.4f}")
```

Or from JSON output:

```bash
claude -p "query" --output-format json | jq '.usage'
```

### 4.16 Billing Note

**As of June 15, 2026:** Agent SDK and `claude -p` usage on subscription plans draw from a new monthly Agent SDK credit, separate from interactive usage limits. See [Use the Claude Agent SDK with your Claude plan](https://support.claude.com/en/articles/15036540-use-the-claude-agent-sdk-with-your-claude-plan) for details.

### 4.17 Agent SDK vs. Claude Code CLI

| Use Case | Best Choice |
| -------- | ----------- |
| Interactive development | Claude Code CLI |
| CI/CD pipelines | Agent SDK |
| Custom applications | Agent SDK |
| One-off tasks | CLI |
| Production automation | Agent SDK |
| Prototyping | CLI or SDK |

Many teams use both: CLI for daily development, SDK for production.

### 4.18 Agent SDK vs. Managed Agents

|  | Agent SDK | Managed Agents |
| ---- | --------- | -------------- |
| **Runs in** | Your process, your infrastructure | Anthropic-managed infrastructure |
| **Interface** | Python/TypeScript library | REST API |
| **Agent works on** | Files on your infrastructure | Managed sandbox per session |
| **Session state** | JSONL on your filesystem | Anthropic-hosted event log |
| **Custom tools** | In-process Python/TypeScript functions | Claude triggers; you execute and return |
| **Best for** | Local prototyping, direct filesystem/service access | Production agents without sandbox/session ops, long-running async |

### 4.19 Agent SDK Limitations

- **Runs in your process:** Requires your infrastructure to run the Python/TypeScript runtime
- **No managed sandbox:** You provide the execution environment
- **Local context only:** Sessions stored on your filesystem, not synced to cloud
- **Custom tool execution:** You implement tool handlers if not using built-ins
- **State management:** Your responsibility (no Anthropic-managed persistence)

---

## 5. CI/CD Integration

Automate Claude Code in GitHub Actions, GitLab CI/CD, and other pipelines.

**Sources:**
- [Claude Code GitHub Actions](https://code.claude.com/docs/en/github-actions.md)

### 5.1 GitHub Actions

Claude Code GitHub Actions enables AI-powered automation in your GitHub workflow. Tag `@claude` in issues or PRs, or trigger via workflow events.

#### Quick Setup

```bash
claude /install-github-app
```

This guides you through installing the GitHub app and adding the `ANTHROPIC_API_KEY` secret.

#### Manual Setup

1. Install the [Claude GitHub app](https://github.com/apps/claude)
2. Add `ANTHROPIC_API_KEY` to repository secrets
3. Copy workflow from [examples/claude.yml](https://github.com/anthropics/claude-code-action/blob/main/examples/claude.yml) to `.github/workflows/`

#### Basic Workflow

```yaml
name: Claude Code
on:
  issue_comment:
    types: [created]
  pull_request_review_comment:
    types: [created]
jobs:
  claude:
    runs-on: ubuntu-latest
    steps:
      - uses: anthropics/claude-code-action@v1
        with:
          anthropic_api_key: ${{ secrets.ANTHROPIC_API_KEY }}
          # Responds to @claude mentions
```

#### Invoke via Comment

In any PR or issue comment:

```
@claude implement this feature
@claude review this PR for security issues
@claude fix the failing test
```

#### Parameters

| Parameter | Description | Required |
| --------- | ----------- | -------- |
| `prompt` | Instructions for Claude (plain text or skill name) | No* |
| `claude_args` | CLI arguments (e.g., `--max-turns 5`) | No |
| `anthropic_api_key` | Claude API key | Yes* |
| `github_token` | GitHub token for API access | No |
| `plugin_marketplaces` | Plugin marketplace Git URLs | No |
| `plugins` | Plugin names to install | No |
| `trigger_phrase` | Custom trigger phrase (default: `@claude`) | No |
| `use_bedrock` | Use Amazon Bedrock | No |
| `use_vertex` | Use Google Vertex AI | No |

*Prompt optional for PR comments (Claude responds to trigger phrase); required for `prompt` input. API key required for direct Claude API; not needed for Bedrock/Vertex.

#### Example: Run Code Review on Every PR

```yaml
name: Code Review
on:
  pull_request:
    types: [opened, synchronize]
jobs:
  review:
    runs-on: ubuntu-latest
    steps:
      - uses: anthropics/claude-code-action@v1
        with:
          anthropic_api_key: ${{ secrets.ANTHROPIC_API_KEY }}
          prompt: "Review this PR for security issues and code quality"
          claude_args: "--max-turns 10"
```

#### Example: Use Installed Plugins/Skills

```yaml
name: Code Review with Plugin
on:
  pull_request:
    types: [opened, synchronize]
jobs:
  review:
    runs-on: ubuntu-latest
    steps:
      - uses: anthropics/claude-code-action@v1
        with:
          anthropic_api_key: ${{ secrets.ANTHROPIC_API_KEY }}
          plugin_marketplaces: "https://github.com/anthropics/claude-code.git"
          plugins: "code-review@claude-code-plugins"
          prompt: "/code-review:code-review ${{ github.repository }}/pull/${{ github.event.pull_request.number }}"
```

#### Example: Custom Automation (Daily Report)

```yaml
name: Daily Report
on:
  schedule:
    - cron: "0 9 * * *"
jobs:
  report:
    runs-on: ubuntu-latest
    steps:
      - uses: anthropics/claude-code-action@v1
        with:
          anthropic_api_key: ${{ secrets.ANTHROPIC_API_KEY }}
          prompt: "Generate a summary of yesterday's commits and open issues"
          claude_args: "--model opus"
```

#### Using with Amazon Bedrock

Prerequisites: Amazon Bedrock enabled, GitHub OIDC Identity Provider in AWS, IAM role with Bedrock permissions.

```yaml
name: Claude PR Action
permissions:
  id-token: write
on:
  issue_comment:
    types: [created]
jobs:
  claude-pr:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - name: Configure AWS Credentials
        uses: aws-actions/configure-aws-credentials@v4
        with:
          role-to-assume: ${{ secrets.AWS_ROLE_TO_ASSUME }}
          aws-region: us-west-2
      - uses: anthropics/claude-code-action@v1
        with:
          github_token: ${{ secrets.GITHUB_TOKEN }}
          use_bedrock: "true"
          claude_args: '--model us.anthropic.claude-sonnet-4-6'
```

#### Using with Google Vertex AI

Prerequisites: Vertex AI API enabled, Workload Identity Federation configured, Service account with Vertex AI permissions.

```yaml
name: Claude PR Action
permissions:
  id-token: write
on:
  issue_comment:
    types: [created]
jobs:
  claude-pr:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - name: Authenticate to Google Cloud
        uses: google-github-actions/auth@v2
        with:
          workload_identity_provider: ${{ secrets.GCP_WORKLOAD_IDENTITY_PROVIDER }}
          service_account: ${{ secrets.GCP_SERVICE_ACCOUNT }}
      - uses: anthropics/claude-code-action@v1
        with:
          github_token: ${{ secrets.GITHUB_TOKEN }}
          trigger_phrase: "@claude"
          use_vertex: "true"
          claude_args: '--model claude-sonnet-4-5@20250929'
        env:
          ANTHROPIC_VERTEX_PROJECT_ID: ${{ steps.auth.outputs.project_id }}
```

#### Cost Management

**GitHub Actions costs:**
- Claude Code runs on GitHub-hosted runners (consume GitHub Actions minutes)
- See [GitHub billing docs](https://docs.github.com/en/billing/managing-billing-for-github-actions)

**API costs:**
- Tokens consumed per interaction (varies by task and codebase size)
- See [Claude pricing](https://claude.com/platform/api)

**Optimization tips:**
- Use specific `@claude` commands to reduce API calls
- Set `--max-turns` to prevent excessive iterations
- Configure workflow timeouts
- Use GitHub concurrency controls

### 5.2 GitLab CI/CD

Claude Code integrates with GitLab via the [Agent SDK](/en/agent-sdk/overview). Example workflow:

```yaml
stages:
  - test

claude_review:
  stage: test
  image: node:18
  script:
    - npm install @anthropic-ai/claude-agent-sdk
    - node review.js
  env:
    ANTHROPIC_API_KEY: $CI_JOB_TOKEN  # Or use explicit API key
```

Where `review.js` uses the Agent SDK to review code.

### 5.3 General CI/CD Pattern

Use `claude -p` for shell-based CI:

```bash
#!/bin/bash
set -e

# Run analysis
claude -p "Analyze this codebase for bugs" \
  --output-format json \
  --allowedTools "Read,Bash,Glob,Grep" \
  > analysis.json

# Check result
if jq -e '.result | contains("critical")' analysis.json > /dev/null; then
  echo "Critical issues found"
  exit 1
fi

exit 0
```

---

---

# Part 4 — Runtime & Surfaces: Memory, Sessions, Interfaces, Tools, Models, Limitations, Changelog

## 1. Memory & Context Management

### 1.1 CLAUDE.md Files

Persistent markdown instructions loaded at session start. They *guide* behavior (a user message after the system prompt) — they are **not enforced** config; for enforcement use `settings.json` `permissions.deny`.

**Location hierarchy (broadest → most specific):**

| Scope | Path | Applies to |
| :--- | :--- | :--- |
| Organization policy | macOS `/Library/Application Support/ClaudeCode/CLAUDE.md`; Linux/WSL `/etc/claude-code/CLAUDE.md`; Windows `C:\Program Files\ClaudeCode\CLAUDE.md` | All sessions on the machine |
| User-level | `~/.claude/CLAUDE.md` | All projects for this user |
| Project | `./CLAUDE.md` or `./.claude/CLAUDE.md` | Everyone on the project (committed) |
| Local-only | `./CLAUDE.local.md` | This user, this project (gitignore) |
| Subdirectory rules | `.claude/rules/*.md` with optional `paths` frontmatter | On-demand when Claude reads matching files |

**Import syntax:** `@path/to/file` imports another file (relative to the importer). Max 5 levels nesting. **Imports load at launch — splitting via `@path` aids organization but does NOT reduce context usage.** HTML comments are stripped before injection.

**Gotchas:** root-level files load in full at start; nested subdir files load on-demand and **do NOT survive `/compact`** (reload on next file read in that dir); vague/conflicting instructions reduce adherence; keep files under ~200 lines.

### 1.2 Auto Memory (MEMORY.md)

Claude writes notes to itself across sessions in `~/.claude/projects/<project>/memory/`, auto-loaded at start. **MEMORY.md** is the entrypoint (first 200 lines / 25KB loaded); topic files load on-demand. **Machine-local only — not synced across devices. Per-repository** (shared across worktrees). Requires v2.1.59+.

- Toggle with `/memory` or `autoMemoryEnabled`. Disable globally: `CLAUDE_CODE_DISABLE_AUTO_MEMORY=1`. Custom dir: `autoMemoryDirectory`.

> **Jarvis note:** this is Claude Code's *native* auto-memory, distinct from our Supabase memory MCP (`mcp__memory__*`, cross-device source of truth) and the file-based `~/.claude/projects/.../memory/MEMORY.md` index we maintain by hand. They are different systems — don't conflate.

### 1.3 Context Window Management

**Loads at startup (in order):** system prompt (~4,200 tok) → auto-memory MEMORY.md (~25KB) → environment info (~280 tok) → CLAUDE.md files (full, no limit) → `.claude/rules/` (on-demand) → MCP tools (~120 tok, **deferred by default**, schemas load on-demand via tool search).

**Auto-compaction (`/compact`):** summarizes history, dropping old messages. **Survives:** system prompt, project-root CLAUDE.md, MEMORY.md index, environment, git status. **Lost:** conversation history, nested subdir CLAUDE.md (reload on next file read).

**Commands:** `/context` (breakdown), `/compact [instructions]` (focused), `/clear` (fresh, previous still resumable), `/btw` (aside not saved to context).

**Limits:** 200K tokens default (1M optional on Opus 4.7 / Sonnet 4.6 with plan/credits). **Compaction thrashing:** if context refills faster than compaction shrinks, Claude Code stops retrying — recover via chunked reads, focused `/compact`, subagents, or `/clear`. Prompt caching auto-applies; disable globally with `DISABLE_PROMPT_CACHING=1`.

### 1.4 `/memory` Command

One interface listing all loaded CLAUDE.md files (user/project/local/rules), the auto-memory folder + toggle; click any file to edit.

---

## 2. Background Tasks, Scheduling & Sessions

### 2.1 Scheduled Tasks (session-scoped)

| Option | How | Persistence | Locality |
| :--- | :--- | :--- | :--- |
| `/loop` | In-session, fixed or dynamic interval | Restored on `--resume` if unexpired (7-day TTL) | Current machine |
| Desktop scheduled tasks | Local machine cron | Persistent across restarts | Current machine |
| Routines (`/schedule`) | Anthropic cloud infra | Persistent, session-independent | Cloud, no local file access |

**`/loop`:** `/loop 5m check deployment` (fixed); `/loop check the PR` (dynamic — Claude picks 1m–1h, self-paced); bare `/loop` runs a built-in maintenance prompt (customizable via `.claude/loop.md` project or `~/.claude/loop.md` user). Intervals `s/m/h/d`, rounded to nearest cron step. Underlying tools: `CronList`/`CronCreate`/`CronDelete`; **Monitor** streams output instead of polling.

**Cron quick-ref:** `minute hour day-of-month month day-of-week`; `0 9 * * 1-5` = weekdays 9am; `*/5 * * * *` = every 5 min; DOW 0/7=Sun, 6=Sat; `?`/named aliases unsupported.

**Gotchas:** tasks fire **only when Claude is idle** (between turns); no catch-up for missed fires; recurring tasks auto-expire 7 days after creation; closing the terminal stops firing; on Bedrock/Vertex/Foundry bare `/loop` doesn't run the built-in (use `/loop 10m <prompt>`).

### 2.2 Background Bash Tasks

Run long commands with `run_in_background: true` (or Ctrl+B). Output lines stream back mid-conversation. `/tasks` to list, `/tasks stop <id>` to stop. **Not restored on `--resume`.** Env vars do NOT persist between Bash commands (use `CLAUDE_ENV_FILE` or a SessionStart hook). Timeout 2 min default, up to 10 (`BASH_DEFAULT_TIMEOUT_MS`/`BASH_MAX_TIMEOUT_MS`); output capped 30K chars (full saved to file, Claude gets path + preview).

### 2.3 Sessions & Resuming

```bash
claude --continue            # most recent in this dir
claude --resume              # interactive picker
claude --resume <name>       # by name
claude --from-pr <number>    # session linked to a PR
claude -n auth-refactor      # name at startup
claude --continue --fork-session   # branch from CLI
/resume   /rename   /branch         # in-session
```

**Picker shortcuts:** ↑/↓ navigate · Enter resume · Space preview · Ctrl+R rename · `/` search (paste PR URL too) · Ctrl+A all projects · Ctrl+W all worktrees · Ctrl+B filter by branch.

**Storage:** `~/.claude/projects/<project>/<session-id>.jsonl` (JSONL, expires after `cleanupPeriodDays`, default 30).

**Gotchas:** Desktop / VS Code / web each keep **separate** session history; forked sessions do **not** carry approved permissions; resumed sessions keep their original model even if your default changed; nested CLAUDE.md don't reload on resume.

---

## 3. Interfaces & Surfaces

### 3.1 Terminal CLI

Primary interface, full feature set (macOS/Linux/WSL/Windows). All commands, full Bash permission control, skills, hooks, MCP config, Vim mode (if set in settings), tab completion, custom keybindings, `!` bash shortcut. Terminal min 120×40.

### 3.2 VS Code Extension

Side-by-side diff review with inline edit before accept; `@`-mentions with line ranges (`@auth.ts#5-10`); permission modes toggle (normal/plan/auto-accept); extended thinking (`Alt/Opt+T`); context indicator; session history search; resume remote/web sessions locally; `/plugins`; Chrome integration via `@browser`.

**Built-in `ide` MCP server** (active while extension runs): opens diffs in VS Code's native viewer, shares selection + active file as context (blocked by `Read` deny rules), executes Jupyter cells (with confirmation). Tools: `mcp__ide__getDiagnostics`, `mcp__ide__executeCode`.

**Shortcuts:** Focus input `Cmd/Ctrl+Esc`; new tab `Cmd/Ctrl+Shift+Esc`; insert @-mention `Opt/Alt+K`. **Gotchas:** `Cmd+Esc` clashes with macOS Game Overlay (Tahoe+); spark icon needs a file open; doesn't work in Restricted Mode; **VS Code extension settings are separate** from `~/.claude/settings.json`. CLI-only features the extension lacks: some commands (`/batch`, `/loop`, `/schedule`), tab completion, `!` shortcut, full MCP config.

### 3.3 JetBrains IDEs

Plugin "Claude Code Beta" for IntelliJ/PyCharm/WebStorm/Android Studio/PhpStorm/GoLand. Requires CLI on PATH + full IDE restart. Diff viewing, selection sharing (blocked by `Read` deny rules), file refs (`Cmd+Opt+K` / `Alt+Ctrl+K` → `@file#line-line`), diagnostic sharing. **Remote dev:** install plugin on the *remote host*, not client. **WSL2:** firewall may block IDE↔WSL2 — use mirrored networking or a firewall rule.

### 3.4 Desktop App (macOS / Windows; no Linux)

Three tabs: Chat, Cowork (cloud agents), Code. Parallel sessions with git isolation, integrated terminal + file browser, drag-and-drop panes, visual diff review, app previews, PR monitoring, computer use, dispatch sessions from phone, side chats, connectors, desktop scheduled tasks (persistent, separate from `/loop`). Windows first launch needs Git for Windows. Each session is isolated; history separate from CLI/VS Code.

### 3.5 Claude Code on the Web (claude.ai/code)

Cloud execution on Anthropic infra (research preview; Pro/Max/Team/Enterprise). Persistent sessions, mobile monitoring, GitHub clone/commit/push without local git, auto-fix PRs (CI/review), configurable network allowlist, setup scripts. **No local file access** (fresh clone per session); **MCP unavailable** except cloud connectors; **no permission prompts** (runs autonomously, auto-denies anything that would prompt). Move between surfaces: `claude --remote <session-id>`, `claude --teleport` / `/teleport`.

---

## 4. Built-in Tools

| Tool | Does | Prompts? | Notes |
| :--- | :--- | :--- | :--- |
| **Read** | Read files (text/images/PDF/Jupyter) | No | Images rescaled; PDFs >10 pages in ranges (≤20/call) |
| **Write** | Create/overwrite files | Yes | Must read existing file before overwrite |
| **Edit** | Exact string replace (no regex) | Yes | Read-before-edit; match must be unique unless `replace_all` |
| **Glob** | Find files by pattern | No | Caps ~100 results by mtime; does NOT respect `.gitignore` by default (`CLAUDE_CODE_GLOB_NO_IGNORE=false` to respect) |
| **Grep** | Ripgrep regex content search | No | Escape regex metachars; modes `files_with_matches`/`content`/`count`; **respects `.gitignore`** |
| **Bash** | Shell commands | Yes | 2-min (→10) timeout; 30K output cap; `cd` carries within project; env NOT persisted |
| **PowerShell** | PowerShell commands | Yes | Windows default if no Git Bash; opt-in elsewhere (`pwsh` 7+) |
| **LSP** | Code intelligence (defs/refs/types) | No | Needs language-server plugin |
| **Monitor** | Watch background output | Yes | Streams; reacts mid-conversation; not on Bedrock/Vertex/Foundry |
| **NotebookEdit** | Edit Jupyter cells by ID | Yes | replace/insert/delete |
| **WebFetch** | Fetch URL + small-model extract | Yes | Lossy; HTTP→HTTPS; 15-min cache; redirect to other host returns notice |
| **WebSearch** | Web search (titles+URLs) | Yes | ≤8 backend searches/call; `allowed_domains` or `blocked_domains`; not on Bedrock |
| **Agent** | Spawn subagent (separate context) | No | Parent sees only final result |
| **Task** (Create/Get/List/Update/Stop) | Task checklist | No | Session-persisted. **Replaces deprecated `TodoWrite`** |
| **Cron** (Create/Delete/List) | Schedule tasks | No | Session-scoped, 7-day TTL; `/loop` wraps it |
| **EnterPlanMode/ExitPlanMode** | Plan mode | No | |
| **EnterWorktree/ExitWorktree** | Git worktree isolation | No | |
| **PushNotification** | Desktop + phone notify | No | Phone needs Remote Control connected |
| **RemoteTrigger** | Create/manage cloud Routines | No | Pro/Max/Team/Enterprise |
| **ToolSearch** | Load deferred tool schemas on demand | No | Active when tool search enabled |

---

## 5. Models, Reasoning & Workflow Modes

### 5.1 Extended Thinking

Toggle session `Alt/Opt+T`; global default `/config` (`alwaysThinkingEnabled`); disable `MAX_THINKING_TOKENS=0`. Collapsed by default; `Ctrl+O` expand/collapse; you pay for thinking tokens even collapsed. Opus 4.7 uses **adaptive reasoning** (effort-driven). Say `ultrathink` to request deeper reasoning on a turn.

### 5.2 Model Selection & Effort

**Aliases:** `default`, `best`, `opus` (4.7), `sonnet` (4.6), `haiku` (3.5), `sonnet[1m]`/`opus[1m]` (1M context), `opusplan` (Opus in plan mode → Sonnet for execution).

Set via `claude --model opus`, `/model sonnet` (`d` saves default), `ANTHROPIC_MODEL`, or `"model"` in settings.

**Effort levels:** `low` / `medium` / `high` / `xhigh` (recommended default for coding) / `max` (session-only, deepest). Set via `/effort`, `--effort`, `CLAUDE_CODE_EFFORT_LEVEL`, or `"effortLevel"`. Availability: Opus 4.7 = all; Opus 4.6 / Sonnet 4.6 = low/medium/high/max; Haiku/older = none.

### 5.3 Fast Mode

`/fast` toggles 2.5× faster Opus at higher per-token cost ($30/$150 per MTok, flat across 1M context). Needs usage credits enabled. Not on Bedrock/Vertex/Foundry; Team/Enterprise admin must enable. Enabling mid-conversation re-reads full context at fast price; separate rate limits with auto-fallback (gray `↯`).

### 5.4 Plan Mode

`/plan` (or toggle). Claude drafts a markdown plan → you review/comment → approve → it implements. Modes: normal/plan/auto/acceptEdits. `opusplan` plans with Opus, executes with Sonnet.

### 5.5 Checkpointing & Rewinding

`/checkpoint [name]`, `/rewind <id>` (code only) or `/rewind <id> --clear` (code + conversation). VS Code: hover a message for rewind options. `/branch` / `--fork-session` copies conversation+code at a point. Checkpoints track **file changes**, not conversation state; approved permissions don't carry to forks.

> **Hooks, Subagents, Skills, MCP** are covered authoritatively in **Parts 2 (hooks), 1 (skills), and 3 (subagents, MCP)** — not duplicated here.

---

## 6. Cross-cutting Known Limitations & Gotchas

**Context & memory:** 200K default (1M optional, newer models, plan/credits); auto-memory machine-local & per-repo; nested CLAUDE.md don't survive `/compact`; compaction can thrash; `@path` imports don't reduce context.

**Bash & environment:** env vars NOT persisted between commands; `cd` outside project resets; 2-min timeout (→10); 30K output cap; ripgrep dependency (set `USE_BUILTIN_RIPGREP=0` + install system ripgrep if Glob/Grep fail).

**Sessions:** history separate per surface (CLI/VS Code/Desktop/Web); nested CLAUDE.md don't reload on resume; resumed sessions keep prior model; permissions don't carry to forks; 30-day transcript expiry (`cleanupPeriodDays`).

**Model/perf:** Opus may auto-downgrade to Sonnet at usage thresholds; effort only on Opus 4.7/4.6 + Sonnet 4.6; WSL cross-boundary disk reads slow (move project to Linux FS); large codebases use lots of RAM (`/compact`, `/heapdump`).

**Web:** research preview, Pro/Max/Team/Enterprise only; no local files; MCP only via connectors; no permission prompts.

**Windows-specific:** Git for Windows required (Desktop first launch); **CRLF in `.env` breaks some MCP servers** (line-split bugs — use LF); PowerShell tool opt-in off-Windows (`CLAUDE_CODE_USE_POWERSHELL_TOOL=1`).

**Deprecated/removed:** `TodoWrite` deprecated (v2.1.142) → use `TaskCreate`/`TaskUpdate`; TS V2 SDK preview removed (current = Agent SDK).

---

## 7. Recent Changelog Highlights (Nov 2025 – May 2026)

| Version | Highlights |
| :--- | :--- |
| **2.1.148** (May 2026) | Fixed Bash exit-code 127 regression |
| **2.1.147** | Pinned background sessions stay alive when idle; `/simplify`→`/code-review` with effort levels; better auto-updater |
| **2.1.145** | `claude agents --json`; richer plugin discovery; fixed permission-prompt bypass for bare variable assignments; `/run`, `/verify`, `/run-skill-generator` skills |
| **2.1.144** | `/resume` for background sessions; elapsed duration in bg notifications; `/model` now session-only (`d` for default); fast mode defaults to Opus 4.7 |
| **2.1.142** | `claude agents` config flags; plugins can surface root-level `SKILL.md`; `TodoWrite` deprecated → `TaskCreate`/`TaskUpdate` |

**Trends (Nov 2025 → May 2026):** dynamic-interval `/loop` + Monitor streaming + session-scoped Cron tools; Opus 4.7 with adaptive reasoning + effort levels + `opusplan`; pinned background sessions & better `/resume`; JetBrains beta + VS Code/browser integration; Desktop parallel sessions; web cloud execution with GitHub + PR monitoring + mobile.

# Part 5 — Additional Native Surfaces

> Features that exist natively but weren't in the original four-cluster scope. Added 2026-05-23 after diffing the full official docs index (`llms.txt`) against this doc. **Out of scope (deliberately):** enterprise/admin surfaces — admin setup, usage analytics, SSO/auth methods, network config, LLM gateway, server-managed settings detail, zero-data-retention, champion/comms kits. Alt backends (Bedrock/Vertex/Foundry/Claude-Platform-on-AWS) and minor surfaces (interactive-mode shortcuts/vim, `.claude` directory reference, deep-links, prompt-library, fullscreen) are noted but not detailed — see [docs index](https://code.claude.com/docs/llms.txt).

## 5A. Multi-session & event I/O

### Channels — push external events into a running session

> **Jarvis-relevant.** This is the native mechanism behind our Telegram integration (CLAUDE.md: "Telegram → Channels"). Distinct from MCP (which Claude *pulls* from) — channels *push* inbound events into the live session.

A channel is a plugin that listens for inbound messages and pushes them as events into your **already-open local session** (not a fresh cloud session) ([channels.md](https://code.claude.com/docs/en/channels.md)). Two-way channels let Claude reply back through the same platform. Built-ins: **Telegram, Discord, iMessage**; custom channels possible for webhooks/proprietary systems.

- **Setup:** install as plugin (`/plugin install <channel>@<marketplace>`), then start with `claude --channels plugin:telegram@claude-plugins-official`. Credentials in `~/.claude/channels/<name>/.env`. Configure per-channel (e.g. `/telegram:configure <token>`); pair by messaging the bot → approve code with `/telegram:access pair <code>`. iMessage (macOS) reads the Messages DB directly + AppleScript to send — needs Full Disk Access, no token.
- **Event format:** arrives as a `<channel source="telegram">` tag (sender ID, text, timestamp). Claude calls the channel's `reply` tool; the reply lands on the platform, not the terminal.
- **Security:** per-channel **sender allowlist** — unapproved senders silently dropped. Pairing bootstraps the list. Org admins gate via `channelsEnabled` (required for Team/Enterprise) + `allowedChannelPlugins`.
- **Limits:** research preview, **v2.1.80+**; needs **Bun** for plugin servers; events only arrive **while the session runs** (use a persistent background process for always-on); permission prompts block the session (use `--dangerously-skip-permissions` for unattended); not on Bedrock/Vertex/Foundry; protocol subject to change.

### Agent View — dashboard for background sessions

`claude agents` opens a dashboard to dispatch, monitor, and manage multiple **background** Claude Code sessions, each a full independent conversation ([agent-view.md](https://code.claude.com/docs/en/agent-view.md)).

- **Use:** type a prompt + Enter to dispatch; `↑/↓` navigate; `Space` peek at latest output/pending question; `Enter`/`→` attach; `←` on empty prompt detaches. Sessions keep running after you close the view (hosted by a per-user **supervisor** process).
- **Rows** grouped by state (Pinned / Ready for review / Needs input / Working / Completed) with Haiku-generated summaries (refresh ≤ once/15s + at turn end) and PR status dots (yellow=checks, green=passed, purple=merged, grey=draft/closed).
- **Shortcuts:** `Ctrl+T` pin (keeps idle session alive), `Ctrl+R` rename, `Ctrl+X` stop+delete, `Ctrl+S` switch grouping (state/dir); filter `a:<name>`, `s:<state>`, `#<pr>`.
- **Worktree isolation:** background sessions auto-move into `.claude/worktrees/` before editing; disable via `worktree.bgIsolation: "none"`. Deleting a session removes its worktree unless it has uncommitted changes.
- **Lifecycle:** idle non-pinned session stops after ~1h, restarts on next interaction; supervisor restarts into new versions after auto-update; state persists through sleep/restart.
- **Limits:** research preview **v2.1.139+**; each session burns quota independently; runs locally (stops on shutdown); interactive pickers (`/mcp`, `/plugin`) don't work from mobile/web.

### Worktrees — parallel sessions in isolated git checkouts

Isolate parallel sessions in separate git working directories so edits never collide; each has its own files + branch, shares repo history ([worktrees.md](https://code.claude.com/docs/en/worktrees.md)).

- **Start:** `claude --worktree <name>` (or `-w`; bare generates a name) → `.claude/worktrees/<name>/`. `claude --worktree "#1234"` branches from a PR. Default base = `origin/HEAD`; set `worktree.baseRef: "head"` to branch from local HEAD (carries unpushed work).
- **Tools:** `EnterWorktree` (create + switch into), `ExitWorktree` (back to main dir). Or manual `git worktree add/list/remove`.
- **Cleanup:** clean tree → auto-removed (prompted if named); dirty → prompted keep/remove; `-p` runs → no auto-cleanup. Orphaned subagent worktrees older than `cleanupPeriodDays` removed if clean. **User-created worktrees never auto-removed.**
- **`.worktreeinclude`** (`.gitignore` syntax) copies gitignored files (e.g. `.env`) into new worktrees. Subagents: `isolation: worktree` frontmatter. Non-git VCS: `WorktreeCreate`/`WorktreeRemove` hooks.
- **vs branching:** separate file tree, not just a branch — true parallel work. Tip: add `.claude/worktrees/` to `.gitignore`.

### Remote Control — drive a local session from phone/browser

Continue a **local** session from `claude.ai/code` or the mobile app; the local process keeps running, the remote UI is a window into it (cf. Claude Code on the web, which is cloud) ([remote-control.md](https://code.claude.com/docs/en/remote-control.md)).

- **Modes:** `claude remote-control` (server mode, shows URL+QR; flags `--name`, `--spawn {same-dir|worktree|session}`, `--capacity N`); `claude --remote-control` (interactive + remotely controllable); `/remote-control` (`/rc`) to enable on the current session.
- **Local stays local:** your filesystem, MCP servers, tools, project config all available; `@` path completion from local project; survives network interruptions (buffers + resyncs).
- **Push notifications** to phone when a task finishes/needs input (**v2.1.110+**), via `/config → Push when Claude decides`.
- **Security:** outbound HTTPS only, no inbound ports; TLS via short-lived scoped credentials.
- **Requires:** **v2.1.51+**, Pro/Max/Team/Enterprise (no API keys), OAuth login, workspace trust. **Limits:** one remote session per interactive process (use server mode `--spawn worktree` for many); local process must stay up (>~10 min outage → timeout exit); ultraplan disconnects RC; interactive pickers local-only.

## 5B. Sandbox, computer/browser control & integrations

### Sandboxing — the sandboxed Bash tool

Run most shell commands without permission prompts by enforcing **filesystem + network isolation at the OS level** ([sandboxing.md](https://code.claude.com/docs/en/sandboxing.md), [sandbox-environments.md](https://code.claude.com/docs/en/sandbox-environments.md)).

- **Enable:** `/sandbox` → auto-allow mode (sandboxed commands run without prompting) or regular-permissions mode. macOS (Seatbelt) / Linux+WSL2 (bubblewrap — install `bubblewrap`+`socat`). **Not on WSL1 or native Windows.**
- **Filesystem:** read anywhere, write only to cwd by default; extend via `sandbox.filesystem.allowWrite` (OS-enforced for all subprocesses incl. kubectl/npm); deny/re-allow paths supported.
- **Network:** no domains pre-allowed; first new domain prompts; pre-allow via `sandbox.filesystem.allowedDomains`. Proxy enforces hostname allowlist but **does not inspect TLS** — broad domains (e.g. `github.com`) risk exfiltration.
- **vs permissions:** complementary — permission rules gate *which tools* (before run); sandbox restricts *what a Bash command can access* (at run).
- **Limits:** sandboxes Bash subprocesses only (Read/Edit/Write/computer-use use other boundaries); some tools incompatible (`docker`, `jest --watchman`); env vars inherited (may leak creds — scrub with `CLAUDE_CODE_SUBPROCESS_ENV_SCRUB`).

### Computer use from the CLI

Claude controls your **macOS** screen — open apps, click, type, screenshot — to validate native builds, test UI, debug visual issues, drive GUI-only tools ([computer-use.md](https://code.claude.com/docs/en/computer-use.md)).

- **Enable:** `/mcp` → enable `computer-use` server; grant macOS Accessibility + Screen Recording on first use. Claude prefers precise tools (MCP, Bash, Chrome) before computer use.
- **Approval:** per-app, per-session (don't carry over); broad-reach apps (Terminal/Finder/System Settings) warn; some apps view-only/click-only.
- **Behavior:** machine-wide lock (one session at a time); your terminal excluded from screenshots; **Esc / Ctrl+C aborts**.
- **Requires:** **macOS only (CLI)**, **v2.1.85+**, Pro/Max, interactive (no `-p`), claude.ai auth (not Bedrock/Vertex/Foundry). Research preview; not on Team/Enterprise. (Windows gets the Desktop-app version.)

### Chrome / browser integration

Integrates the "Claude in Chrome" extension to automate/inspect the browser from the terminal ([chrome.md](https://code.claude.com/docs/en/chrome.md)).

- **Connect:** `claude --chrome` or `/chrome` (extension v1.0.36+, Chrome/Edge). Bridges via native messaging host + MCP server `claude-in-chrome` (restart Chrome after first install).
- **Capabilities:** test web apps, read console errors → fix code, fill forms, extract data, verify designs, multi-tab workflows, record GIFs. **Shares your login state** (access authenticated apps). Pauses on login/CAPTCHA for you to handle.
- **Permissions:** site-level, inherited from extension settings. Enable-by-default via `/chrome` (raises context usage — browser tools always loaded).
- **Limits:** beta; Chrome/Edge only (not Brave/Arc); **no WSL**; not via third-party providers; service worker can idle out on long sessions (`/chrome` to reconnect).

### Goal mode (`/goal`)

`/goal <condition>` sets a completion condition; Claude works toward it autonomously across turns until met, with a small fast model evaluating after each turn ([goal.md](https://code.claude.com/docs/en/goal.md)).

- Setting a goal **starts a turn immediately** (no separate prompt). Write the condition as something Claude's own output demonstrates (e.g. "all tests in test/auth pass") — the evaluator only judges what's in the transcript, it can't call tools. Up to 4,000 chars; bound with `or stop after N turns`.
- `/goal` (no args) shows condition/runtime/turns/tokens/last reason; `/goal clear` (or stop/off/reset/cancel) removes it. Active goal restored on `--resume`/`--continue` (counters reset).
- **vs `/loop`:** `/goal` loops until a *condition* is met (new turn when prev finishes); `/loop` runs on a *time interval*. Works with `-p`. **Requires v2.1.139+**; needs workspace trust; unavailable under `disableAllHooks`/`allowManagedHooksOnly`.

### Routines — cloud-hosted scheduled automation

Saved CC configs (prompt + repos + connectors) that run autonomously on **Anthropic cloud infra**, independent of any open session — triggered by schedule, API call, or GitHub event ([routines.md](https://code.claude.com/docs/en/routines.md)). `/schedule` (`/routines`) / `RemoteTrigger` tool.

- **Run as** full cloud sessions, no permission prompts; can run shell, repo-committed skills, MCP connectors. **No local file access.**
- **Triggers:** cron (hourly/daily/weekly or one-off; local TZ; min interval 1h; custom cron via `/schedule update`); **API** (dedicated HTTP endpoint + bearer token, optional `text` body); **GitHub** (PR/release events, install the GitHub App, filter by author/labels/branch/etc.).
- **Env:** Default = "Trusted" network (package/cloud/registry domains; arbitrary blocked); connectors (Slack/Linear/…) route through Anthropic so no host allowlisting needed.
- **vs `/loop` & desktop tasks:** `/loop` = local, in-session, local files; desktop scheduled tasks = local machine; routines = cloud, session-independent, per-account (not team-shared), count against daily run allowance.
- **Create** at claude.ai/code/routines, Desktop (Routines → New → Remote), or CLI `/schedule`. **Limits:** GitHub only; research preview (limits/API may change); per-account hourly caps on webhook events.

### Slack integration

Native Claude app in Slack: `@Claude` a coding task → it creates a **Claude Code on the web** session ([slack.md](https://code.claude.com/docs/en/slack.md)). `/install-slack-app`.

- **Setup:** admin installs the Claude app; connect your Claude account in App Home; enable CC on the web + connect GitHub repo; routing mode Code-only or Code+Chat; `/invite @Claude` to channels.
- **Flow:** @mention with a task → intent detected → web session created, status posted to the thread → finishes with summary + buttons (View Session / Create PR / Retry as Code / Change Repo). Gathers thread/channel context.
- **Access:** runs under the user's own Claude account + plan limits; only repos the user connected; **channels only, not DMs**; channel-invite model lets admins scope usage.
- **Requires:** Pro/Max/Team/Enterprise, CC-on-web enabled, GitHub connected. **Limits:** GitHub only; one PR per session; Team/Enterprise sessions org-visible; **treat Slack content as untrusted** (injection risk — only use in trusted conversations).

## Part 5 Sources

All from official docs (code.claude.com/docs), verified ~Claude Code v2.1.148 (2026-05-22): [channels.md](https://code.claude.com/docs/en/channels.md) · [channels-reference.md](https://code.claude.com/docs/en/channels-reference.md) · [agent-view.md](https://code.claude.com/docs/en/agent-view.md) · [worktrees.md](https://code.claude.com/docs/en/worktrees.md) · [remote-control.md](https://code.claude.com/docs/en/remote-control.md) · [sandboxing.md](https://code.claude.com/docs/en/sandboxing.md) · [sandbox-environments.md](https://code.claude.com/docs/en/sandbox-environments.md) · [computer-use.md](https://code.claude.com/docs/en/computer-use.md) · [chrome.md](https://code.claude.com/docs/en/chrome.md) · [goal.md](https://code.claude.com/docs/en/goal.md) · [routines.md](https://code.claude.com/docs/en/routines.md) · [slack.md](https://code.claude.com/docs/en/slack.md). Version pins: Channels 2.1.80+, Agent View 2.1.139+, Remote Control 2.1.51+ (push 2.1.110+), Computer use 2.1.85+, Goal 2.1.139+. Several are research preview — re-verify via `/release-notes`.

---

# Sources & Verification

All content compiled from official Anthropic Claude Code documentation (`code.claude.com/docs`), the public CHANGELOG, and the Agent SDK docs. Verified against Claude Code **v2.1.148** (2026-05-22); compiled 2026-05-23.

### Documentation index

- Overview — https://code.claude.com/docs/en/overview.md
- Quickstart — https://code.claude.com/docs/en/quickstart.md
- **Skills** — https://code.claude.com/docs/en/skills.md
- **Commands** — https://code.claude.com/docs/en/commands.md
- **Plugins** — https://code.claude.com/docs/en/plugins.md · plugins reference / marketplaces
- **Output styles** — https://code.claude.com/docs/en/output-styles.md
- **Hooks** — https://code.claude.com/docs/en/hooks-guide.md · https://code.claude.com/docs/en/hooks.md
- **Settings** — https://code.claude.com/docs/en/settings.md
- **Permissions** — https://code.claude.com/docs/en/permissions.md
- **Subagents** — https://code.claude.com/docs/en/sub-agents.md
- **MCP** — https://code.claude.com/docs/en/mcp.md
- **Headless / CLI** — https://code.claude.com/docs/en/cli-reference.md · https://code.claude.com/docs/en/headless.md
- **Agent SDK** — https://code.claude.com/docs/en/sdk/sdk-overview.md (Python + TypeScript)
- **CI/CD** — https://code.claude.com/docs/en/github-actions.md · https://code.claude.com/docs/en/gitlab-ci-cd.md
- Memory — https://code.claude.com/docs/en/memory.md
- Context window — https://code.claude.com/docs/en/context-window.md
- Scheduled tasks — https://code.claude.com/docs/en/scheduled-tasks.md
- Sessions — https://code.claude.com/docs/en/sessions.md
- VS Code — https://code.claude.com/docs/en/vs-code.md · JetBrains — https://code.claude.com/docs/en/jetbrains.md
- Desktop — https://code.claude.com/docs/en/desktop.md · Web — https://code.claude.com/docs/en/claude-code-on-the-web.md
- Tools reference — https://code.claude.com/docs/en/tools-reference.md
- Model config — https://code.claude.com/docs/en/model-config.md · Fast mode — https://code.claude.com/docs/en/fast-mode.md
- Checkpointing — https://code.claude.com/docs/en/checkpointing.md
- Troubleshooting — https://code.claude.com/docs/en/troubleshooting.md
- **Changelog** — https://code.claude.com/docs/en/changelog.md · raw: https://raw.githubusercontent.com/anthropics/claude-code/main/CHANGELOG.md
- Full machine-readable index — https://code.claude.com/docs/llms.txt

### Known gaps / lower-confidence items

- Exact per-component token costs aren't published — measure per-project with `/context` and `/doctor`.
- A few features were cross-confirmed from the changelog rather than a dedicated doc page (some Desktop/Web specifics, recent command renames). Re-verify version-pinned claims against `/release-notes`.
- Managed (enterprise) settings full schema isn't fully exposed in user-facing docs.
- LSP server custom-configuration examples are sparse.

**Confidence: 88/100.** High for the documented extension surfaces (skills, hooks, settings, permissions, subagents, MCP, headless, SDK — sourced directly from official pages). Lower for fast-moving version-pinned details and Desktop/Web specifics, which churn release-to-release.
