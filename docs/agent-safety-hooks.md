---
applies_when: an agent in your repo can write files, run shell commands or call a service's API (GitHub, a tracker, a chat tool), and something it writes can become public or permanent — a secret in git history or an issue body, an edit to the file that configures your checks — before anyone reads it
applies_when_not: the agent can only propose changes that a person applies by hand, or it holds no credentials and cannot reach anything outside a disposable copy of the repo
signed_off:
---

# Stopping an agent's writes before they land

## The problem

Some mistakes cannot be fixed later by review. A secret pushed to a public repo or pasted into
an issue is exposed the moment it lands, and deleting it does not un-leak it. An edit to the
config of the check that would have caught it switches that check off first. A rule in your
agent's instructions ("never commit a secret") holds only while the agent reads and follows it.
What can go wrong with the mechanical alternatives:

- **Bypassed through another tool** — the check watches one way to write (the file-edit tool)
  and the agent uses another (`echo >` in a shell, an API call).
- **Fires too late** — it runs after the write reached the remote, so it only raises an alert.
- **Switched off by the agent** — `git commit --no-verify`, an edit to the settings file that
  wires the check, an edit to the check itself.
- **Silent when absent** — nothing reports that the check is not wired, or that its matcher no
  longer names the tools that exist.
- **Friction** — false positives, or a block that also stops the humans who need to edit.
- **One harness only** — it runs in the agent you use today and nowhere else.

Each option is marked **tried** (we run or ran it; the example says where) or **sourced** (read
from the tool's documentation). Quotes were checked on 2026-09-17.

## The options

### 1. Written rules only

**How it works.** The agent's rules file says what not to write or touch.

**Best pick when** everything the agent writes goes through a pull request and CI before it is
public, the repo is private or holds no secrets, and no file reachable by the agent would weaken
that review if changed on a branch.

**Cost.** Every failure mode above; nothing fires if the agent forgets or is steered off course.

**Lifecycle.** Edit a file. Status: tried — our baseline for ordinary changes; dropped *on fit*
for secrets and the files below.

### 2. Approve each call

**How it works.** The harness asks a person before a write or command runs. Claude Code's
default mode prompts for edits and commands; Cursor, Codex and VS Code have equivalents.

**Best pick when** a person is at the keyboard for the whole session and the sessions are short.

**Cost.** People approve what they did not read; no approval exists in unattended runs. Modes
exist to switch it off, and in Claude Code's `bypassPermissions` mode even `.git` and `.claude`
writes are allowed ([permission modes](https://code.claude.com/docs/en/permission-modes)).

**Lifecycle.** Harness setting. Status: sourced.

### 3. Declarative deny rules

**How it works.** A settings file lists tool calls the harness refuses, such as Claude Code's
`Read(./.env)` or `Edit(...)`: "Rules are evaluated in order: deny, then ask, then allow"
([permissions](https://code.claude.com/docs/en/permissions)). Cursor's CLI has `Write(**/.env*)`
([permissions](https://cursor.com/docs/cli/reference/permissions)); Gemini CLI a policy engine
([policy](https://github.com/google-gemini/gemini-cli/blob/main/docs/reference/policy-engine.md));
OpenCode `"edit": "deny"` patterns ([permissions](https://opencode.ai/docs/permissions/)).

**Best pick when** the thing to protect is a known path, and you want no code to maintain.

**Cost.** Path rules see paths, not content, so they cannot find a secret in a file. The shell
is covered only partly: Claude Code's rules reach commands that name a file, but not "a command
that reads files without naming them, such as `grep -r pattern .`", nor "a Python or Node script
that opens files itself". Managed settings can lock them for an organisation.

**Lifecycle.** Edit settings; nothing to install. Status: tried in our private source project
(user-level deny rules for `.env`, keys and cloud credentials); no public trace.

### 4. A program that inspects each call before it runs

**How it works.** The harness runs your script before each matching tool call, passing the call
as JSON; the script blocks it. In Claude Code, "PreToolUse hooks run before every tool call,
whether or not it needs permission"; exit 2 blocks "whether or not you print JSON", and a deny
holds "even in `bypassPermissions` mode"
([hooks](https://code.claude.com/docs/en/hooks), [guide](https://code.claude.com/docs/en/hooks-guide)).
Equivalents block on exit 2 or a deny decision in
[Cursor](https://cursor.com/docs/hooks) (`preToolUse`, `beforeShellExecution`, `beforeMCPExecution`),
[Gemini CLI](https://github.com/google-gemini/gemini-cli/blob/main/docs/hooks/reference.md) (`BeforeTool`),
[Codex](https://developers.openai.com/codex/hooks) (`PreToolUse`),
[Copilot](https://docs.github.com/en/copilot/reference/hooks-reference) (`preToolUse`) and
[Kiro](https://kiro.dev/docs/hooks/) (Pre Tool Use); OpenCode plugins throw from
`tool.execute.before` ([plugins](https://opencode.ai/docs/plugins/)).

**Best pick when** you need to read *content* (a secret-shaped string in a file, command or issue
body) at the moment of the call, including calls to remote APIs that never touch disk.

**Cost.**
- It sees only what its matcher names. A hook on the file-edit tools misses the same write made
  with `echo >` in the shell, and a matcher naming API tools goes quiet when the server renames
  them — see [`mcp-matcher-tool-name-drift.md`](../examples/mcp-matcher-tool-name-drift.md).
- Pattern lists miss secrets with no known shape, and flag test fixtures.
- Failure handling differs by harness. Claude Code: "Without valid JSON on stdout, Claude Code
  treats exit code 1 as a non-blocking error"; Cursor: "Crashes, timeouts, and non-zero exit codes
  other than 2 fail open by default", unless `failClosed: true`. A script that exits 0 on input it
  cannot parse fails open everywhere.
- The agent can edit the hook or its settings unless something else stops it, and a hook in
  project settings can be turned off locally with `disableAllHooks`; only managed settings cannot.
- Vendors call it partial. Codex: "Treat tool hooks as a useful guardrail, not a complete
  enforcement boundary."

**Lifecycle.** Scripts plus a settings entry per harness; updating means re-checking matchers
against the current tool names. Status: tried — this is ours
([resource](../resources/agent-safety-hooks.md)).

### 5. OS sandbox or container

**How it works.** The operating system limits what commands can write or reach. Claude Code's
sandbox applies to "every Bash, PowerShell, or Monitor command and its child processes" and by
default writes only to the working directory and temp
([sandboxing](https://code.claude.com/docs/en/sandboxing)); Codex runs `workspace-write` with no
network by default ([security](https://developers.openai.com/codex/security)); Gemini CLI uses
Seatbelt or containers ([sandbox](https://github.com/google-gemini/gemini-cli/blob/main/docs/cli/sandbox.md)).
A dev container isolates the whole session.

**Best pick when** the risk is a script or command doing something no rule anticipated, and you
can list the paths and hosts it legitimately needs.

**Cost.** Content-blind: it limits where writes go, not what they say, so a secret in an allowed
file or an allowed API call passes. Claude Code's sandbox covers commands, not its own file-edit
tools, and "Native Windows is not supported". A container keeps what you mount: "Avoid mounting
host secrets such as `~/.ssh` or cloud credential files into the container", and with permissions
skipped it does "not prevent a malicious project from exfiltrating anything accessible inside the
container" ([devcontainer](https://code.claude.com/docs/en/devcontainer)).

**Lifecycle.** Harness setting or container config. Status: sourced.

### 6. Don't hand the agent the capability

**How it works.** Give the agent credentials that cannot do the dangerous thing: a fine-grained
token scoped to one repo and the permissions it needs
([tokens](https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/managing-your-personal-access-tokens));
the GitHub MCP server with `--read-only`, where "write tools are skipped", or only the
`--toolsets` you use ([github-mcp-server](https://github.com/github/github-mcp-server/blob/main/README.md));
no secrets in the agent's environment. A gateway between agent and MCP servers can also filter:
Docker's MCP Gateway has a `block-secrets` switch
([run reference](https://github.com/docker/mcp-gateway/blob/main/docs/generator/reference/docker_mcp_gateway_run.yaml)).

**Best pick when** the agent does not need the write at all, or needs it only on a few repos.

**Cost.** It removes the capability, not the mistake: a token that may open issues may still
paste a secret into one. Tokens multiply, and each expires. The same server's lockdown mode "is
**not** an authorization boundary". Gateway filters: sourced from one reference page only.

**Lifecycle.** Token and server config. Status: sourced.

### 7. Local git hooks

**How it works.** A pre-commit hook scans staged changes; `pre-commit install` with gitleaks is
the common setup ([pre-commit](https://pre-commit.com/), [gitleaks](https://github.com/gitleaks/gitleaks)).

**Best pick when** the risk is a secret in a commit, and your team also commits by hand: it
covers humans and every agent harness alike.

**Cost.** Commits only: not issue bodies, API calls or files never committed. Anyone, agent
included, can skip it — `--no-verify` will "Bypass the pre-commit and commit-msg hooks"
([git-commit](https://git-scm.com/docs/git-commit)). Each clone must install it.

**Lifecycle.** Config file in the repo, install per clone. Status: tried in our private source
project; no public trace.

### 8. Server-side checks

**How it works.** The host checks what arrives. GitHub push protection blocks pushes, web
commits, API requests and "Interactions with the GitHub MCP server (public repositories only)"
([push protection](https://docs.github.com/en/code-security/secret-scanning/introduction/about-push-protection));
secret scanning also reads issue and pull request text, as alerts
([secret scanning](https://docs.github.com/en/code-security/secret-scanning/introduction/about-secret-scanning)).
CI can run a scanner on each pull request; rulesets and code owners gate who may merge changes
to chosen paths ([rulesets](https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/managing-rulesets/about-rulesets),
[code owners](https://docs.github.com/en/repositories/managing-your-repositorys-settings-and-features/customizing-your-repository/about-code-owners)).

**Best pick when** several people or agents write to one repo and you need one check none of
them can switch off locally.

**Cost.** For text already posted — issue bodies, comments — it alerts after exposure. CI scans
a pushed branch, which is already on the server. Push protection bypasses need only a reason from
anyone with write access; repository-level protection needs Secret Protection on private repos;
path restrictions on pushes need a paid plan. Merge gates themselves are #44's subject.

**Lifecycle.** Repository settings and workflow files. Status: tried — CI gitleaks and a
personal-literal scrub run on this repo
([`gitleaks.yml`](../.github/workflows/gitleaks.yml)).

## How to choose

These are layers, not rivals: most setups want one before the write and one on the server.

First, in order:

1. **Can the agent reach nothing that becomes public or permanent before a person merges it?**
   Yes → **option 1**, with option 8 as the backstop.
2. **Can the agent write to a public place outside git — issues, comments, chat?** Options 4
   and 6 act before that text lands; 8 does so only for GitHub MCP calls on public repos, and
   otherwise alerts after.
3. **Is there a file whose edit disables your checks?** Options 3 or 4 on edits, and 8 (code
   owners) on the server. Don't count on 4 alone: it misses shell writes unless it parses them.

These facts rule options out:

| Option | Fits only if |
|---|---|
| 2 | a person is present for every session |
| 3 | what you protect is a path, not content |
| 4 | your harness has a blocking pre-call hook ([`harnesses.md`](harnesses.md)), and you will keep its matchers current |
| 5 | your OS or container runtime is supported, and you can list the paths and hosts commands need |
| 6 | the agent can do its job without the write you withhold |
| 7 | the risk ends at a commit, and every clone installs the hook |
| 8 | your host offers the check for your plan and visibility (public or private) |

Among what is left: 4 reads content on every surface it names but only those, and runs only in
the harnesses you configured; 5 and 6 cannot be talked out of their limit but do not read
content; 7 and 8 cover every author, agent or human, but 7 can be skipped and 8 fires after the
push. If nothing is left — no hook, no sandbox, no server checks — withhold the write (6) or
keep a person on every call (2).

**At more than one developer.** Project-settings hooks reach every clone, but each person can
switch them off locally; managed settings are the only harness layer they cannot. Server-side
checks are the only layer applied to every contributor alike. A blocked edit should route to a
reviewer other than its author — the same shift
[`publishing-discipline.md`](publishing-discipline.md) makes for its sign-off.

**Examples.** A private repo where agents only push branches and never post outside git: 1, with
7 on commits and 3 for `.env` — no script to maintain, and a hook would add little. A team with
public repos and people on different harnesses: 8 first (push protection, a secret scan in CI,
code owners on the workflow folder) and 6 for tokens, since only those reach everyone; 4 where a
harness supports it. Shell-heavy unattended work on Linux or macOS with no posting rights: 5
and 6. An unattended agent that posts to a public tracker: 6 and 4, since nothing else acts
before the text lands.

**Our own choice.** Our agent runs unattended, posts issues and pull requests on a public repo,
and we use one harness. So we take 4 — two hooks: a secret scanner on file edits, shell commands
and GitHub MCP writes, and a protected-file block on the files that configure the scanner — plus
8: CI gitleaks and a required human-review check before merge. The protected-file hook blocks
our own edits too, with no bypass for a person at the keyboard: we tried deciding from inside the
hook whether a person was present, and dropped it *on merit* — a hook's stdin is always piped, so
every session looked unattended; see
[`protected-files-fail-closed.md`](../examples/protected-files-fail-closed.md). Known gaps: the
protected-file hook does not see shell writes; both hooks exit 0 on input they cannot parse; and
our GitHub matcher named retired tools until this rewrite. How a scan can miss part of its input:
[`heredoc-stripping-boundary-bug.md`](../examples/heredoc-stripping-boundary-bug.md).
