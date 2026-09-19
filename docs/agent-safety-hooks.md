---
applies_when: an agent in your repo can write files, run shell commands or call a service's API (GitHub, a tracker, a chat tool), and something it writes can become public or permanent — a secret in git history or an issue body, an edit to the file that configures your checks — before anyone reads it
applies_when_not: the agent can only propose changes that a person applies by hand, or it holds no credentials and cannot reach anything outside a disposable copy of the repo, or you are a company with a security team and managed devices
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

**Who this is for.** A solo developer or a team of up to three, on a free or a paid plan, with
the agent attended (a person approves each call) or unattended. Host and plan facts are GitHub's
only, stated where a filter depends on them. On another host the principle carries over; check
that host's docs for its equivalent.

Each option is marked **tried** (a test, workflow or recorded example in this repo is the trace,
linked) or **sourced** (read from the tool's documentation). Quotes and plan facts were checked
on 2026-09-19.

## The options

None of these replaces `.gitignore`: a secrets file that is never staged cannot leak through a
commit.

### 1. Written rules only

**How it works.** The agent's rules file says what not to write or touch. Claude Code's docs say
it treats such files "as context, not enforced configuration"
([memory](https://code.claude.com/docs/en/memory)).

**Fits only if** the repo is private, the agent's credentials reach nothing outside it, its
default branch requires an approved pull request (on GitHub: Pro, Team or Enterprise), and the
account the agent pushes from is not a repo admin.

GitHub's words: "Protected branches are available in public repositories with GitHub Free and
GitHub Free for organizations", and in private ones on the paid plans
([protected branches](https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/managing-protected-branches/about-protected-branches));
"Pull request authors cannot approve their own pull requests"
([required reviews](https://docs.github.com/en/pull-requests/collaborating-with-pull-requests/reviewing-changes-in-pull-requests/approving-a-pull-request-with-required-reviews)).

**Best pick when** no file the agent can change on a branch weakens that review. A workflow
edited on a branch changes the check run on that pull request; close that with code owners
(option 8) or a token without the `workflows` permission (option 6).

**Cost.** Every failure mode above; nothing fires if the agent forgets or is steered off course.

**Lifecycle.** Edit a file. Status: sourced.

### 2. Approve each call

**How it works.** The harness asks a person before a write or command runs. Claude Code's
`default` mode prompts for edits and commands, though "On Pro, Max, and Team plans, the built-in
starting permission mode is auto mode" (option 10) unless you set another
([permission modes](https://code.claude.com/docs/en/permission-modes)).

**Fits only if** your sessions run in a mode that asks before each write or command, and a person
is present for every session.

**Best pick when** a person is at the keyboard for the whole session and the sessions are short.

**Cost.** People approve what they did not read; no approval exists in unattended runs. Modes
exist to switch it off, and in Claude Code's `bypassPermissions` mode even `.git` and `.claude`
writes are allowed (same page).

**Lifecycle.** Harness setting. Status: sourced.

### 3. Declarative deny rules

**How it works.** A settings file lists tool calls the harness refuses, such as Claude Code's
`Read(./.env)` or `Edit(...)`: "Rules are evaluated in order: deny, then ask, then allow"
([permissions](https://code.claude.com/docs/en/permissions)). Cursor's CLI has `Write(**/.env*)`
([permissions](https://cursor.com/docs/cli/reference/permissions)); Gemini CLI a policy engine
([policy](https://github.com/google-gemini/gemini-cli/blob/main/docs/reference/policy-engine.md));
OpenCode `"edit": { "*": "deny" }` patterns ([permissions](https://opencode.ai/docs/permissions/)).
Codex has permission profiles with per-path entries such as `"**/*.env" = "deny"`
([permissions](https://developers.openai.com/codex/permissions)).

**Fits only if** your harness has path deny rules (the list under option 3), and what you protect
has a known path, not a secret that can appear in any file.

**Best pick when** the thing to protect is a known path, and you want no code to maintain.

**Cost.** Path rules see paths, not content, so they cannot find a secret in a file. The shell
is covered only partly: Claude Code's rules reach shell redirections and recognized file commands
such as `sed` and `tee`, but not "a command
that reads files without naming them, such as `grep -r pattern .`", nor "a Python or Node script
that opens files itself".

**Lifecycle.** Edit settings; nothing to install. Status: sourced.

### 4. A program that inspects each call before it runs

**How it works.** The harness runs your script before each matching tool call, passing the call
as JSON; the script blocks it. In Claude Code, `PreToolUse` fires "on every tool call inside the
agentic loop"; exit 2 blocks "whether or not you print JSON", and a deny holds "even in
`bypassPermissions` mode"
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
  exits 0 on input it cannot parse lets the call through. A command that fails to launch —
  its interpreter is missing — exits non-zero but not 2, so it fails open where such exits do;
  chaining `|| exit 2` onto the command makes it deny there too.
- The agent can edit the hook or its settings unless something else stops it, and a hook in
  project settings can be turned off locally with `disableAllHooks`; only managed settings cannot.
- Codex: "Treat tool hooks as a useful guardrail, not a complete
  enforcement boundary."

**Lifecycle.** Scripts plus a settings entry per harness; updating means re-checking matchers
and input fields against the current tools. Status: tried — the scripts are ours
([resource](../resources/agent-safety-hooks.md)), and
[`test_agent_safety_hooks.py`](../tests/test_agent_safety_hooks.py) runs both on constructed tool
calls. This repo ships them unwired: none of its settings loads them. To see whether a hook is
wired, Claude Code's `/hooks` menu "shows every hook event with a count of configured hooks"
([hooks](https://code.claude.com/docs/en/hooks)); the resource says how to trip one on purpose.

### 5. OS sandbox or container

**How it works.** The operating system limits what commands can write or reach. Claude Code's
sandbox "applies only to Bash, PowerShell, and Monitor commands and their child processes"
([sandboxing](https://code.claude.com/docs/en/sandboxing)) and by default writes only to the
working directory and temp. Inside that boundary it still denies writes to "the files Claude Code
loads configuration and code from", whatever `allowWrite` says (the same page has the list).
Of your repo's own gate files, `.claude/hooks` and `.git/hooks` are on it; hook scripts kept
elsewhere and CI workflows are not, and each needs a `denyWrite` entry.
Codex's `workspace-write` sandbox has no network by default
([approvals and security](https://developers.openai.com/codex/agent-approvals-security)); Gemini
CLI uses Seatbelt or containers
([sandbox](https://github.com/google-gemini/gemini-cli/blob/main/docs/cli/sandbox.md)).
A dev container isolates the whole session, and Docker's `readonly` bind-mount option will
"prevent the container from writing to the mount"
([bind mounts](https://docs.docker.com/engine/storage/bind-mounts/)); on Linux, `chattr +i` makes a single file
unwritable to anyone without root; plain `chmod` does not, since the agent's own account can
change it back. Claude Code's sandbox also limits which hosts a command can reach.

**Fits only if** your harness has a sandbox for your OS (Claude Code: macOS, Linux or WSL2) or
you run the agent in a container; (Claude Code) `allowUnsandboxedCommands` is `false` and
`excludedCommands` is empty; and each file you need covered is on the sandbox's protected list,
in a `denyWrite` entry you wrote, or (container) on a read-only mount.

**Best pick when** the risk is a script or command doing something no rule anticipated.

**Cost.** Content-blind: it limits where writes go, not what they say, so a secret in an allowed
file or an allowed API call passes. Claude Code's sandbox covers commands, not its own file-edit
tools, and "Native Windows is not supported". A command it blocks can be retried with
`dangerouslyDisableSandbox`, which "goes through the regular permission flow" — a prompt, or
option 10's classifier. With `allowUnsandboxedCommands: false`, "every command Claude runs must
run sandboxed unless you've listed it in `excludedCommands`". A container keeps what you mount:
with permissions skipped it does "not prevent a malicious project from exfiltrating anything
accessible inside the container" ([devcontainer](https://code.claude.com/docs/en/devcontainer)).

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
which reads content. A credential broker such as Infisical's
[Agent Vault](https://github.com/Infisical/agent-vault) ("Agents should not possess credentials")
gives the agent a dummy value and swaps in the real one on the way out. On GitHub, a token
without the `workflows` permission cannot change files under `.github/workflows`.

**Fits only if** you choose the credentials and tools the agent runs with, and it can do its job
without the write you withhold.

**Best pick when** the agent does not need the write at all, or needs it only on a few repos.

**Cost.** It removes the capability, not the mistake: a token that may open issues may still
paste a secret into one. Tokens multiply. The same server's lockdown mode "is **not** an
authorization boundary". A broker keeps the secret out of what the agent can paste, not the call
out of reach.

**Lifecycle.** Token and server config. Status: sourced.

### 7. Local git hooks

**How it works.** A pre-commit hook scans staged changes; `pre-commit install` with gitleaks is
the common setup ([pre-commit](https://pre-commit.com/), [gitleaks](https://github.com/gitleaks/gitleaks)).

**Fits only if** every clone that commits has the hook installed (`pre-commit install`).

**Best pick when** the risk is a secret in a commit, and your team also commits by hand: it
covers humans and every agent harness alike.

**Cost.** Commits only: not issue bodies, API calls or files never committed. Anyone, agent
included, can skip it — `--no-verify` will "Bypass the pre-commit and commit-msg hooks"
([git-commit](https://git-scm.com/docs/git-commit)). A `git` wrapper placed earlier on `PATH`
can refuse the flag, but it "does not protect against the agent calling git through a path that
bypasses your PATH"
([pydevtools](https://pydevtools.com/handbook/how-to/how-to-stop-ai-agents-from-bypassing-pre-commit-hooks/)).
Each clone must install it.

**Lifecycle.** Config file in the repo, install per clone. Status: sourced.

### 8. Server-side checks

**How it works.** The host checks what arrives. GitHub
[push protection](https://docs.github.com/en/code-security/secret-scanning/introduction/about-push-protection)
blocks a secret in a push, a web commit or an API request. "This protection is on by default
for all interactions between the GitHub MCP server and public repositories; and between the
GitHub MCP server and private repositories covered by GitHub Advanced Security"
([push protection and the MCP server](https://docs.github.com/en/code-security/concepts/secret-security/push-protection-and-the-github-mcp-server)).
Secret scanning also reads issue and pull request text, as alerts
([secret scanning](https://docs.github.com/en/code-security/secret-scanning/introduction/about-secret-scanning)).
CI can run a scanner on each pull request; a required review (linked under option 1) gates who
may merge, and code owners do it per path
([code owners](https://docs.github.com/en/repositories/managing-your-repositorys-settings-and-features/customizing-your-repository/about-code-owners)).
A push ruleset can "Restrict file paths" — "Prevent commits that include changes in specified
file paths from being pushed" — but "Push rulesets are available for the GitHub Team plan in
internal and private repositories"
([rulesets](https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/managing-rulesets/about-rulesets)).

**Fits only if** your plan has the check you want (GitHub's plans; elsewhere, check your host's):

- a CI scan: any plan; on a private repo it spends Actions minutes;
- push protection: a public repo, or a private one with Secret Protection, sold only on Team and Enterprise;
- required review or code owners: a public repo, or a private one on Pro, Team or Enterprise, and the account the agent pushes from is not a repo admin.

**Best pick when** several people or agents write to one repo and you need one check none of
them can switch off locally.

**Cost.** For text already posted — issue bodies, comments — it alerts after exposure. CI scans
a pushed branch, which is already on the server. "by default, anyone with write access to the
repository can bypass push protection by specifying a bypass reason", unless you set up
[delegated bypass](https://docs.github.com/en/code-security/concepts/secret-security/delegated-bypass);
push protection for repositories "Requires GitHub Secret Protection to be enabled"
([push protection](https://docs.github.com/en/code-security/secret-scanning/introduction/about-push-protection)),
and "You must be on a GitHub Team or GitHub Enterprise plan in order to purchase GitHub Code
Security or GitHub Secret Protection"
([Advanced Security](https://docs.github.com/en/get-started/learning-about-github/about-github-advanced-security)).
Actions minutes: [billing](https://docs.github.com/en/billing/concepts/product-billing/github-actions).
A required review that the agent's own account can approve, remove or outrank as admin is a
request, not a barrier.

**Lifecycle.** Repository settings and workflow files. Status: tried — CI gitleaks runs on this repo
([`gitleaks.yml`](../.github/workflows/gitleaks.yml)). In 34 runs its personal-literal scrub had no
list and reported clean; lists of private strings are
[`private-literal-scrub.md`](private-literal-scrub.md).

### 9. Stage the writes; a separate job applies them

**How it works.** The agent runs read-only and records what it wants to post or push; a later
job, holding the write token, scans that output and applies it. GitHub Agentic Workflows do this
with "safe outputs"
([architecture](https://github.blog/ai-and-ml/generative-ai/under-the-hood-security-architecture-of-github-agentic-workflows/)):
"When safe outputs are configured, a threat detection job automatically runs to identify prompt
injection attempts, secret leaks, and malicious code patches"
([threat detection](https://github.github.com/gh-aw/reference/threat-detection/)).

**Fits only if** the agent runs as a CI job with a read-only token, and a second job applies the
writes it recorded (GitHub Agentic Workflows' safe outputs, or a job you build).

**Best pick when** the agent runs unattended in CI and must post text, and you want content read
before it lands without trusting a hook inside the agent's own process.

**Cost.** Writes are delayed and limited to the kinds the job knows how to apply; the scan is only
as good as its detector; you run the pipeline. It does not cover interactive sessions.

**Lifecycle.** Workflow config. Status: sourced.

### 10. A model judges each call

**How it works.** A classifier model, not a person, approves or blocks risky calls: Claude Code's
auto mode ([auto mode](https://claude.com/blog/auto-mode)).

**Fits only if** your harness has a classifier mode (Claude Code's auto mode).

**Best pick when** option 2 fits your risk but no person is present, and an occasional miss is
tolerable.

**Cost.** Probabilistic: it can be wrong both ways. Each check adds "a round-trip before
execution", though "Reads and working-directory edits outside protected paths skip the classifier"
([permission modes](https://code.claude.com/docs/en/permission-modes)). Use it beside a
deterministic layer, not in place of one.

**Lifecycle.** Harness setting. Status: sourced.

## How to choose

These are layers, not rivals: most setups want one before the write and one on the server. 7 and
8's CI scan need no plan or harness feature, so commits keep a layer on any plan.

First, in order:

1. **Does a person approve each call in every session?** Yes → 2 can carry part of the load.
   No (unattended, or a mode that does not ask) → 2 is out: build on the options that block
   with nobody present (3 to 9), with 10 as a weaker stand-in for 2.
2. **Does every part of option 1's line hold?** Yes → **option 1**, with 8's required review
   behind it. If one fails — a public repo, a private repo on GitHub Free, an agent pushing from a
   repo admin's account — read on.
3. **Can the agent post text outside git — issues, comments, chat?** 4, 9 and 6's gateway
   read that text before it lands; 6's tokens limit where it can post. Where its row fits, 8's
   push protection also covers GitHub MCP calls; no GitHub check sees a tracker or a chat tool.
   If none of the three fits, look for what your tooling can put between the agent and the
   service — a pre-call hook or plugin API in the harness, a proxy or gateway in front of the
   MCP server. If there is none, withhold the posting credential (6) and have a person or a
   separate job post.
4. **Is there a file whose edit disables your checks?** 3 and 4 deny the edit before it runs, 5
   for commands, 6 and 8 on the server; each option's Cost says what it misses. If none fits,
   the principle still applies: the file must be writable only by an identity the agent does not
   hold. On GitHub, a token without the `workflows` permission (6) does that for workflow files
   on any plan.

These facts rule options out:

| Option | Fits only if |
|---|---|
| 1 | the repo is private, the agent's credentials reach nothing outside it, its default branch requires an approved pull request (on GitHub: Pro, Team or Enterprise), and the account the agent pushes from is not a repo admin |
| 2 | your sessions run in a mode that asks before each write or command, and a person is present for every session |
| 3 | your harness has path deny rules (the list under option 3), and what you protect has a known path, not a secret that can appear in any file |
| 4 | your harness has a blocking pre-call hook (the list under option 4), and the matchers and fields name the tools your version has |
| 5 | your harness has a sandbox for your OS (Claude Code: macOS, Linux or WSL2) or you run the agent in a container; (Claude Code) `allowUnsandboxedCommands` is `false` and `excludedCommands` is empty; and each file you need covered is on the sandbox's protected list, in a `denyWrite` entry you wrote, or (container) on a read-only mount |
| 6 | you choose the credentials and tools the agent runs with, and it can do its job without the write you withhold |
| 7 | every clone that commits has the hook installed (`pre-commit install`) |
| 8 | your plan has the check you want (GitHub's plans; elsewhere, check your host's): |
| 8, a CI scan | any plan; on a private repo it spends Actions minutes |
| 8, push protection | a public repo, or a private one with Secret Protection, sold only on Team and Enterprise |
| 8, required review or code owners | a public repo, or a private one on Pro, Team or Enterprise, and the account the agent pushes from is not a repo admin |
| 9 | the agent runs as a CI job with a read-only token, and a second job applies the writes it recorded (GitHub Agentic Workflows' safe outputs, or a job you build) |
| 10 | your harness has a classifier mode (Claude Code's auto mode) |

Among what is left: 4 and 9 read content on the surfaces they name, and 4 runs only in the
harnesses you configured; 6 cannot be talked out of its limit, nor can 5 (with unsandboxed
retries off) unless a file-edit tool can rewrite the sandbox's settings, as in Claude Code's
`bypassPermissions` mode; only 6's gateway filters read content; 8 applies to everyone who pushes, agent or
human — push protection blocks the push, though anyone with write access can bypass it with a
reason, and a CI scan fires after the push; 7 covers only the clones where it is installed, and
anyone can skip it with `--no-verify`; 10 is a judgement, not a rule.

**At more than one developer.** Project-settings hooks reach every clone, but each person can
switch them off locally; only server-side checks apply to every contributor alike. A blocked
edit should route to a reviewer other than its author — the same shift
[`publishing-discipline.md`](publishing-discipline.md) makes for its sign-off.

**Examples.** *Solo, GitHub Free, private repo, attended, git only:* 1 is out (no required
review on a Free private repo), and so are 8's push protection and review; left are 2, 3 for
`.env`, 4 or 5 where the harness has them, 6 (a token without `workflows`), 7 and 8's CI scan.
*Solo, GitHub Pro, private repo, unattended, git only:* with a second account for the agent, 1
with 8's required review fits, plus 7, 3, and code owners or the token without `workflows` so a
branch cannot change its own checks. With one shared account, the repo owner's and so an admin, 1 and 8's review are out, and
the walk is the Free one without 2. *Team of three, free organization, public repos, different
harnesses:* 1 is out (a pushed branch is public before review); 8 first
— push protection, a CI scan, and code owners once the agent pushes from a non-admin account —
since it alone reaches every contributor; 6 for each agent's token; 4 where a harness supports
it. *Team on a paid plan, private repos, an unattended agent that posts to Slack:* 1 is out (it
reaches outside the repo); 4, 9 or 6's gateway for the Slack text, 8's required review on the
same condition, and push protection only with Secret Protection bought.

**Our own choice.** This repo is public. Our agent runs in Claude Code on a developer's machine,
not in CI (9 is out), by the maintainer's report often with nobody approving each call (2 is out), and opens issues and pull
requests under the maintainer's own admin account (1 is out on both counts). We ship 4: a secret
scanner on file edits, `Bash` tool commands and every GitHub MCP call, and a protected-file block on
the hook scripts and their settings snippet. They are tested here but not wired into this repo's
settings; a clone gets them by copying the snippet. Under 8, push protection and secret scanning
are on, and `main` requires three checks — `gitleaks`, `structure-gate` and
`waiting-human-review` — admins included
(`gh api repos/Osasuwu/jarvis-oss/branches/main/protection`, read on 2026-09-19). The review check
does not bind the agent: its token is the maintainer's admin token, which can remove the
`waiting-human-review` label or switch the protection off — see
[`review-hold-cleared-by-same-account.md`](../examples/review-hold-cleared-by-same-account.md).
The protected-file hook blocks our own edits too, with no bypass for a person at the keyboard: a
hook's stdin is always piped, so a presence check would see every session as unattended; see
[`protected-files-fail-closed.md`](../examples/protected-files-fail-closed.md).
Known gaps: the protected set is a placeholder — the `.gitleaks.toml` it names does not exist
here, and it does not list `.github/workflows/gitleaks.yml`; the repo has no code owners file;
the protected-file hook does not see shell writes — option 3's `Edit` deny rules, which reach
shell redirections and `sed`/`tee`, would close most of that and are our next step; both hooks
exit 0 on input they cannot parse; the scanner's shell matcher is `Bash` alone, so commands run
through Claude Code's PowerShell tool are not scanned; it strips heredoc bodies before its
dangerous-command check, so a heredoc fed to an interpreter (`bash <<'EOF'`) is executed without
that check — see
[`heredoc-stripping-boundary-bug.md`](../examples/heredoc-stripping-boundary-bug.md); our GitHub matcher named retired tools until this rewrite, see
[`mcp-matcher-tool-name-drift.md`](../examples/mcp-matcher-tool-name-drift.md); and
device-identity leakage — a home-directory path, hostname or username — is out of both hooks'
scope, tracked in [#79](https://github.com/Osasuwu/jarvis-oss/issues/79).
