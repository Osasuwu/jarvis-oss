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

Each option is marked **tried** (we run it in this repo; the linked resource or workflow is the
trace) or **sourced** (read from the tool's documentation). Quotes were checked on 2026-09-19.

## The options

None of these keep a secret out of git in the first place — `.gitignore` (or an equivalent
untracked-files rule) the file before any option below matters; a secret that is never staged
cannot be leaked by a check that only runs after `git add`.

### 1. Written rules only

**How it works.** The agent's rules file says what not to write or touch. Claude Code's docs say
it treats such files "as context, not enforced configuration"
([memory](https://code.claude.com/docs/en/memory)).

**Fits only if** a person reviews everything the agent writes before it becomes public or
permanent, and your host enforces that review with a rule the agent's credentials cannot bypass
(a protected branch or ruleset).

**Best pick when** everything the agent writes goes through a pull request and CI before it is
public, the repo is private or holds no secrets, and no file reachable by the agent would weaken
that review if changed on a branch.

**Cost.** Every failure mode above; nothing fires if the agent forgets or is steered off course.

**Lifecycle.** Edit a file. Status: sourced. (We also use it for ordinary changes in a private
project, not public, and dropped it there for secrets and the files below.)

### 2. Approve each call

**How it works.** The harness asks a person before a write or command runs. Claude Code's
`default` mode prompts for edits and commands, though "On Pro, Max, and Team plans, the built-in
starting permission mode is auto mode" (option 10) unless you set another
([permission modes](https://code.claude.com/docs/en/permission-modes)); Cursor, Codex and VS Code
have equivalents.

**Fits only if** your sessions run in a mode that asks before each write or command, and a person
is present for every session.

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
OpenCode `"edit": { "*": "deny" }` patterns ([permissions](https://opencode.ai/docs/permissions/)).
Codex has permission profiles whose filesystem entries are `read`, `write` or `deny` per path,
such as `"**/*.env" = "deny"`, enforced for commands
([permissions](https://developers.openai.com/codex/permissions)).

**Fits only if** your harness has path deny rules (the list under option 3), and what you protect
has a known path — a secrets file (`Read(./.env)`) or a config file — not a secret that can
appear in any file.

**Best pick when** the thing to protect is a known path, and you want no code to maintain.

**Cost.** Path rules see paths, not content, so they cannot find a secret in a file. The shell
is covered only partly: Claude Code's rules reach commands that name a file, but not "a command
that reads files without naming them, such as `grep -r pattern .`", nor "a Python or Node script
that opens files itself". Managed settings can lock them for an organisation.

**Lifecycle.** Edit settings; nothing to install. Status: sourced. (We also use user-level deny
rules for `.env`, keys and cloud credentials in a private project, not public.)

### 4. A program that inspects each call before it runs

**How it works.** The harness runs your script before each matching tool call, passing the call
as JSON; the script blocks it. In Claude Code, `PreToolUse` fires "on every tool call inside the
agentic loop" — a separate mechanism from the permission system, not a substitute triggered only
when a permission prompt would otherwise appear; exit 2 blocks "whether or not you print JSON",
and a deny holds "even in `bypassPermissions` mode"
([hooks](https://code.claude.com/docs/en/hooks), [guide](https://code.claude.com/docs/en/hooks-guide)).
Equivalents block on exit 2 or a deny decision in
[Cursor](https://cursor.com/docs/hooks) (`preToolUse`, `beforeShellExecution`, `beforeMCPExecution`),
[Gemini CLI](https://github.com/google-gemini/gemini-cli/blob/main/docs/hooks/reference.md) (`BeforeTool`),
[Codex](https://developers.openai.com/codex/hooks) (`PreToolUse`),
[Copilot](https://docs.github.com/en/copilot/reference/hooks-reference) (`preToolUse`);
[Kiro](https://kiro.dev/docs/hooks/)'s Pre Tool Use hook can "block tool execution unless preconditions
are met";
OpenCode plugins throw from `tool.execute.before` ([plugins](https://opencode.ai/docs/plugins/)).
Vendors also ship ready-made scanners for these hooks, such as GitGuardian's
[ggshield](https://github.com/GitGuardian/ggshield).

**Fits only if** your harness has a blocking pre-call hook (the list under option 4), and the
matchers and fields name the tools your version has.

**Best pick when** you need to read *content* (a secret-shaped string in a file, command or issue
body) at the moment of the call, including calls to remote APIs that never touch disk.

**Cost.**
- It sees only what its matcher names. A hook on the file-edit tools misses the same write made
  with `echo >` in the shell, and a matcher naming API tools goes quiet when the server renames
  them — see [`mcp-matcher-tool-name-drift.md`](../examples/mcp-matcher-tool-name-drift.md).
- Pattern lists miss secrets with no known shape, and flag test fixtures.
- Failure handling differs by harness. Claude Code: "Without valid JSON on stdout, Claude Code
  treats exit code 1 as a non-blocking error"; Cursor: "Crashes, timeouts, and non-zero exit codes
  other than 2 fail open by default", unless `failClosed: true`; Copilot's command `preToolUse`
  hooks deny on "a crash or non-zero exit", timeouts excepted
  ([Copilot hooks](https://docs.github.com/en/copilot/reference/hooks-reference)). A script that
  exits 0 on input it cannot parse lets the call through. A command that fails to launch at all —
  the interpreter it names is missing, or too old to run the script — exits non-zero but not 2, so
  it fails open where such exits do (Claude Code, Cursor without `failClosed`); chaining
  `|| exit 2` onto the command makes a launch failure deny there too.
- The agent can edit the hook or its settings unless something else stops it, and a hook in
  project settings can be turned off locally with `disableAllHooks`; only managed settings cannot.
- Vendors call it partial. Codex: "Treat tool hooks as a useful guardrail, not a complete
  enforcement boundary."

**Lifecycle.** Scripts plus a settings entry per harness; updating means re-checking matchers
and input fields against the current tools. Status: tried — this is ours
([resource](../resources/agent-safety-hooks.md)).

### 5. OS sandbox or container

**How it works.** The operating system limits what commands can write or reach. Claude Code's
sandbox "applies only to Bash, PowerShell, and Monitor commands and their child processes"
([sandboxing](https://code.claude.com/docs/en/sandboxing)) and by default writes only to the
working directory and temp. Inside that boundary it separately denies writes to the files it
loads configuration and code from, regardless of any `allowWrite` entry, in four groups: in the
working directory and the directories above it, the `.claude` settings files, the
`.claude/skills`, `.claude/agents`, `.claude/commands`, and `.claude/hooks` directories,
`.mcp.json`, and the files Claude Code runs on its own such as `.claude/workflows` and
`.claude/scheduled_tasks.json`; in the working directory only, shell startup files, `.gitconfig`,
the `.vscode` and `.idea` directories, and `hooks` and `config` inside `.git`; the files that
would turn the working directory into a bare git repository; and, separately, most of
`~/.claude` itself ([sandboxing](https://code.claude.com/docs/en/sandboxing)). That built-in list
does not know about a given repo's own review-gate files — here,
`.agents/hooks/`, `.github/workflows/*` and `.gitleaks.toml` sit outside it, so covering them
under option 5 needs an explicit `denyWrite` entry for each, added by the reader. Codex runs
`workspace-write` with no network by default and keeps `.git`, `.agents` and `.codex` inside the
writable root read-only ([approvals and security](https://developers.openai.com/codex/agent-approvals-security)); Gemini CLI uses
Seatbelt or containers ([sandbox](https://github.com/google-gemini/gemini-cli/blob/main/docs/cli/sandbox.md)).
A dev container isolates the whole session; on Linux, `chattr +i` makes a single file
unwritable to anyone without root — plain `chmod`/ACLs do not: they restrict other accounts, not
the one the agent already runs as, so the agent's own process can `chmod` the file back before
writing it. Network isolation is separate from file protection: Claude Code's sandbox also
restricts which hosts a command can reach by default, closing off exfiltration to an arbitrary
endpoint even from an allowed write path ([sandboxing](https://code.claude.com/docs/en/sandboxing)).

**Fits only if** the sandbox supports your OS or container runtime, you can list the paths and
hosts commands need, (Claude Code) `allowUnsandboxedCommands` is `false`, and the files you need
covered are on the sandbox's own protected list (Claude Code's or Codex's, under option 5) or
added by hand.

**Best pick when** the risk is a script or command doing something no rule anticipated, and you
can list the paths and hosts it legitimately needs.

**Cost.** Content-blind: it limits where writes go, not what they say, so a secret in an allowed
file or an allowed API call passes. Claude Code's sandbox covers commands, not its own file-edit
tools, and "Native Windows is not supported". A command it blocks can be retried outside the
sandbox with `dangerouslyDisableSandbox` unless you set `allowUnsandboxedCommands: false`, so the
limit holds only with that setting. A container keeps what you mount: "Avoid mounting
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
([run reference](https://github.com/docker/mcp-gateway/blob/main/docs/generator/reference/docker_mcp_gateway_run.yaml)),
which reads content. A credential broker keeps secrets out of the agent entirely: Infisical's
[Agent Vault](https://github.com/Infisical/agent-vault) is "An open-source credential broker by
Infisical that sits between your agents and the APIs they call", on the rule "Agents should not
possess credentials" — the agent holds a dummy value; the broker, an HTTPS proxy, swaps in the
real one on the way out. On GitHub, a token without the `workflows`
permission cannot change files under `.github/workflows`. That permission is GitHub's; on GitLab,
a CI/CD configuration file kept in a separate project, with its own permissions, does that job
([pipeline settings](https://docs.gitlab.com/ci/pipelines/settings/)), as do Code Owners (option 8).

**Fits only if** you choose the credentials and tools the agent runs with, and it can do its job
without the write you withhold.

**Best pick when** the agent does not need the write at all, or needs it only on a few repos.

**Cost.** It removes the capability, not the mistake: a token that may open issues may still
paste a secret into one. Tokens multiply, and each expires. The same server's lockdown mode "is
**not** an authorization boundary". A broker keeps the secret out of what the agent can paste,
not the call out of reach.

**Lifecycle.** Token and server config. Status: sourced.

### 7. Local git hooks

**How it works.** A pre-commit hook scans staged changes; `pre-commit install` with gitleaks is
the common setup ([pre-commit](https://pre-commit.com/), [gitleaks](https://github.com/gitleaks/gitleaks)).

**Fits only if** every clone that commits has the hook installed (`pre-commit install`); it needs
no harness support.

**Best pick when** the risk is a secret in a commit, and your team also commits by hand: it
covers humans and every agent harness alike.

**Cost.** Commits only: not issue bodies, API calls or files never committed. Anyone, agent
included, can skip it — `--no-verify` will "Bypass the pre-commit and commit-msg hooks"
([git-commit](https://git-scm.com/docs/git-commit)). Each clone must install it.

**Lifecycle.** Config file in the repo, install per clone. Status: sourced. (We also ran it in a
private project, not public.)

### 8. Server-side checks

**How it works.** The host checks what arrives. GitHub push protection blocks pushes, web
commits, API requests and "Interactions with the GitHub MCP server (public repositories only)"
([push protection](https://docs.github.com/en/code-security/secret-scanning/introduction/about-push-protection)).
The MCP page is wider: "This protection is on by default for all interactions between the GitHub
MCP server and public repositories ; and between the GitHub MCP server and private repositories
covered by GitHub Advanced Security"
([push protection and the MCP server](https://docs.github.com/en/code-security/concepts/secret-security/push-protection-and-the-github-mcp-server)).
secret scanning also reads issue and pull request text, as alerts
([secret scanning](https://docs.github.com/en/code-security/secret-scanning/introduction/about-secret-scanning)).
CI can run a scanner on each pull request; rulesets and code owners gate who may merge changes
to chosen paths ([rulesets](https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/managing-rulesets/about-rulesets),
[code owners](https://docs.github.com/en/repositories/managing-your-repositorys-settings-and-features/customizing-your-repository/about-code-owners)).
These facts are GitHub's. GitLab's equivalent, secret push protection, needs Ultimate tier and is
off by default: a Security Manager, Maintainer, or Owner enables it per project, and a
self-managed instance needs an admin to allow it instance-wide first; once enabled it blocks a
push containing a secret by default, skippable with `git push -o
secret_push_protection.skip_all` or a `[skip secret push protection]` line in a commit message
([secret push protection](https://docs.gitlab.com/user/application_security/secret_detection/secret_push_protection/)).
It checks pushes, not issue or comment text; GitLab's
[client-side secret detection](https://docs.gitlab.com/user/application_security/secret_detection/client/),
"operates entirely within your browser", so an agent's API calls skip it. A
variant on Premium: GitLab [push rules](https://docs.gitlab.com/user/project/repository/push_rules/)
"are pre-receive Git hooks you can enable in a user-friendly interface"; "Prevent pushing secret
files" matches "a predefined list of filename patterns". They match file names, not contents,
and "This push rule does not restrict files
already committed to the repository". GitLab
[Code Owners](https://docs.gitlab.com/user/project/codeowners/) also need Premium.

**Fits only if** your host and plan have the check you want:

- a CI scan: any host and plan;
- GitHub push protection: Secret Protection (free on public repos; sold on Team and Enterprise);
- GitHub rulesets or code owners: a public repo, or a private one on Pro, Team or Enterprise;
- GitLab secret push protection: Ultimate, enabled by a Security Manager, Maintainer, or Owner;
- GitLab push rules or Code Owners: Premium or Ultimate.

**Best pick when** several people or agents write to one repo and you need one check none of
them can switch off locally.

**Cost.** For text already posted — issue bodies, comments — it alerts after exposure. CI scans
a pushed branch, which is already on the server. On GitHub, "by default, anyone with write
access to the repository can bypass push protection by specifying a bypass reason"
([push protection](https://docs.github.com/en/code-security/secret-scanning/introduction/about-push-protection));
[delegated bypass](https://docs.github.com/en/code-security/concepts/secret-security/delegated-bypass)
narrows that on organization repos: only those you grant may bypass; others must request it.
Repository-level push protection "Requires GitHub Secret Protection", sold only on Team and
Enterprise
([Advanced Security](https://docs.github.com/en/get-started/learning-about-github/about-github-advanced-security));
push rulesets with path restrictions need a Team or Enterprise plan. A scanner in CI runs on any
plan. Merge gates themselves are [#44](https://github.com/Osasuwu/jarvis-oss/issues/44)'s subject.

**Lifecycle.** Repository settings and workflow files. Status: tried — CI gitleaks runs on this repo
([`gitleaks.yml`](../.github/workflows/gitleaks.yml)). Its personal-literal scrub ran its first 32
runs with no list and reported clean; lists of private strings are
[`private-literal-scrub.md`](private-literal-scrub.md).

### 9. Stage the writes; a separate job applies them

**How it works.** The agent runs read-only and records what it wants to post or push; a later
job, holding the write token, scans that output and applies it. GitHub Agentic Workflows do this
with "safe outputs"
([architecture](https://github.blog/ai-and-ml/generative-ai/under-the-hood-security-architecture-of-github-agentic-workflows/)):
"When safe outputs are configured, a threat detection job automatically runs to identify prompt
injection attempts, secret leaks, and malicious code patches"
([threat detection](https://github.github.com/gh-aw/reference/threat-detection/)).

**Fits only if** the agent runs as a CI job whose writes can wait for a second job.

**Best pick when** the agent runs unattended in CI and must post text, and you want content read
before it lands without trusting a hook inside the agent's own process.

**Cost.** Writes are delayed and limited to the kinds the job knows how to apply; the scan is only
as good as its detector; you run the pipeline. It does not cover interactive sessions.

**Lifecycle.** Workflow config. Status: sourced.

### 10. A model judges each call

**How it works.** A classifier model, not a person, approves or blocks risky calls: Claude Code's
auto mode ([auto mode](https://claude.com/blog/auto-mode)), or OpenHands' security analyzer with a
confirmation policy ([security](https://docs.openhands.dev/sdk/guides/security)).

**Fits only if** your harness has a classifier mode (Claude Code's auto mode, OpenHands' security
analyzer).

**Best pick when** option 2 fits your risk but no person is present, and an occasional miss is
tolerable.

**Cost.** Probabilistic: it can be wrong both ways, and a crafted input can steer it. Each call
costs a model request. Use it beside a deterministic layer, not in place of one.

**Lifecycle.** Harness setting. Status: sourced.

## How to choose

These are layers, not rivals: most setups want one before the write and one on the server.

First, in order:

1. **Does your host enforce review before merge on your plan, and can the agent reach nothing
   public or permanent before that review?** Plan limits: option 8's list. Both yes →
   **option 1**, with option 8's review behind it — provided no file the agent can edit on a
   branch weakens that review (a workflow edited on a branch changes the check run on that PR).
   Close that gap with code owners; on GitHub, also with a token without the `workflows`
   permission; on GitLab, with the CI configuration file kept in a separate project (option 6).
   Either no → read on.
2. **Can the agent write to a public or permanent place outside git — issues, comments, chat?**
   Options 4 and 9 read the text before it lands; 6 limits where it can write. On GitHub, 8's
   push protection blocks it for MCP calls on public repos and on private repos covered by GitHub
   Advanced Security; secret scanning alerts on issue text once posted. GitLab's server checks
   pushes, not issue or comment text. Neither host sees trackers and chat tools — a Slack message
   or Jira comment gets no server-side layer from 8, only 4 or 9.
3. **Is there a file whose edit disables your checks?** Options 3 and 4 both deny the edit
   before it runs: 3 reaches the edit tools and the shell commands that name the file, not
   scripts that open it themselves; 4 reaches what its matcher names, and misses shell writes
   unless it parses them. Also 5 (only its own protected list, Claude Code's
   or Codex's, and for commands only — a repo's own hook scripts or CI config need an explicit
   entry), 6 (on GitHub a token without the `workflows` permission; on GitLab a CI configuration
   file in a separate project) and 8 (code owners) on the server.

These facts rule options out:

| Option | Fits only if |
|---|---|
| 1 | a person reviews everything the agent writes before it becomes public or permanent, and your host enforces that review with a rule the agent's credentials cannot bypass (a protected branch or ruleset) |
| 2 | your sessions run in a mode that asks before each write or command, and a person is present for every session |
| 3 | your harness has path deny rules (the list under option 3), and what you protect has a known path — a secrets file (`Read(./.env)`) or a config file — not a secret that can appear in any file |
| 4 | your harness has a blocking pre-call hook (the list under option 4), and the matchers and fields name the tools your version has |
| 5 | the sandbox supports your OS or container runtime, you can list the paths and hosts commands need, (Claude Code) `allowUnsandboxedCommands` is `false`, and the files you need covered are on the sandbox's own protected list (Claude Code's or Codex's, under option 5) or added by hand |
| 6 | you choose the credentials and tools the agent runs with, and it can do its job without the write you withhold |
| 7 | every clone that commits has the hook installed (`pre-commit install`); it needs no harness support |
| 8, CI scan | any host and plan |
| 8, GitHub push protection | Secret Protection (free on public repos; sold on Team and Enterprise) |
| 8, GitHub rulesets or code owners | a public repo, or a private one on Pro, Team or Enterprise |
| 8, GitLab secret push protection | Ultimate, enabled by a Security Manager, Maintainer, or Owner |
| 8, GitLab push rules or Code Owners | Premium or Ultimate |
| 9 | the agent runs as a CI job whose writes can wait for a second job |
| 10 | your harness has a classifier mode (Claude Code's auto mode, OpenHands' security analyzer) |

Among what is left: 4 and 9 read content on the surfaces they name, and 4 runs only in the
harnesses you configured; 5 (with unsandboxed retries off) and 6 cannot be talked out of their
limit, and only 6's gateway filters read content; 8 applies to everyone who pushes to the host,
agent or human — push protection blocks the push and a CI scan fires after it; 7 covers only the
clones where it is installed, and anyone can skip it with `--no-verify`; 10 is a judgement, not a
rule. 8's CI scan fits any host and plan, so commits can always keep one layer. If nothing is
left for text posted outside git, move posting behind a person or a separate job so that 2 or 9
fits; otherwise accept the risk in writing, naming the surface you leave open.

**At more than one developer.** Project-settings hooks reach every clone, but each person can
switch them off locally; managed settings are the only harness layer they cannot. Server-side
checks are the only layer applied to every contributor alike. A blocked edit should route to a
reviewer other than its author — the same shift
[`publishing-discipline.md`](publishing-discipline.md) makes for its sign-off.

**Examples.** A private repo on a plan where the host enforces review, with agents that only push
branches and never post outside git: 1 with 8's review, 7 on commits, 3 for `.env`, and code
owners or (on GitHub) a token without the `workflows` permission so a branch cannot change its
own checks — no script to maintain, and a hook would add little. The same repo on GitHub Free
has no enforced review, so step 1 says read on: 7 and 3, 8's CI scan, and 4 or 5 where the
harness has them. A team with public
repos and people on different harnesses: 8 first (push protection, a secret scan in CI, code
owners on the workflow folder) and 6 for tokens, since only those reach everyone; 4 where a
harness supports it. Shell-heavy unattended work on Linux or macOS with no posting rights: 5 and
6. An unattended agent that posts to a public GitHub repo: 8's push protection, which covers its
MCP writes, plus 4 or 9 to read the text on the surfaces it does not cover.

**Our own choice.** Our agent runs unattended, posts issues and pull requests on a public repo,
and we use one harness. So we take 4 — two hooks: a secret scanner on file edits, shell commands
and every GitHub MCP call (the matcher is the server prefix, `^mcp__github__`, and it scans every
string value in the call's input, at any nesting depth, not a named list of fields), and a
protected-file block on the hook scripts and their settings snippet — plus 8: CI gitleaks and a
required human-review check before merge. Each hook command in the settings snippet is
`python3 "…" || exit 2`, so a launch failure (the interpreter missing, or too old to run the
script) denies the call instead of letting it through. The
protected set is a placeholder: its `.gitleaks.toml` does not exist here, and it does not list
`.github/workflows/gitleaks.yml`; the repo has no code owners file. The protected-file hook
blocks our own edits too, with no bypass for a person at the keyboard. The hook's docstring
records why it does not try to detect whether a person is present: a hook's stdin is always
piped, so every session would look unattended (that reasoning comes from a trial in a private
project, not public); see
[`protected-files-fail-closed.md`](../examples/protected-files-fail-closed.md). Known gaps: the
protected-file hook does not see shell writes — option 3's `Edit` deny rules, which reach shell
redirections and `sed`/`tee`, would close most of that and are our next step; both hooks exit 0
on input they cannot parse; and our GitHub matcher named retired tools until this rewrite, see
[`mcp-matcher-tool-name-drift.md`](../examples/mcp-matcher-tool-name-drift.md). Device-identity
leakage — a literal home-directory path, hostname or username, not a secret-shaped string — is
scoped out of both hooks: they match shapes, not device-specific literals, and blocking a Bash
command merely for containing a home-path literal would deny almost every ordinary command;
closing that needs a literal list assembled from outside the repo and its own review, tracked in
[#79](https://github.com/Osasuwu/jarvis-oss/issues/79). How a scan can miss part of its input:
[`heredoc-stripping-boundary-bug.md`](../examples/heredoc-stripping-boundary-bug.md).
