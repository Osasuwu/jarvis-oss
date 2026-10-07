---
class: pattern-matched-boundaries
surfaces_at:
  - task
  - code
  - review
  - merge
  - after-merge
applies_when: You protect an AI coding agent, or a CI gate it passes through, with a list of allowed or denied commands, paths, flags or text patterns (a permission allowlist, a deny rule, a pre-tool-use hook, a regex over a pull request body), and you want to know why such a list lets through what it was meant to stop, and what holds instead.
applies_when_not: Untrusted content steering the agent as the route in (prompt injection has its own class). What the agent can destroy once a boundary fails (the irreversible-effects doc). A gate that is skipped or never runs, rather than one that runs and misreads its input. A rule given to the agent only in prose.
---

# Pattern-matched boundaries leak

## TL;DR

A guard that decides by matching the text of a command, a path or a pull request against a list sees one spelling of an action, while the shell, the file system or the next program acts on what the text means; a spelling the list did not anticipate goes through, and each fix adds one more. What holds is a boundary enforced below the text: by the operating system, by the identity the agent runs as, or by comparing what a path or a command resolves to rather than how it is written.

## Symptom

A guard you set up says a command is blocked or needs approval, yet the agent ran it; a file on a deny list was read or written anyway. The command that got through looked different from the one the guard knew: an option placed before the subcommand, the same program behind a shell wrapper, a script the agent wrote and then ran, a path reached through a link, a project setting that turned the guard off. Each time one is found, the missing spelling is added to the list, and the next report names a new one. The reverse shows too: a guard refuses a harmless action because its text happens to match, and people learn to work around the guard.

## Examples

- INC-025: an editor's auto-run denylist was bypassed four ways (encoding the command, a subshell, a shell script, quoting); the vendor deprecated the denylist, and the researchers advise an allowlist instead.
- INC-027: a coding agent's read-only command check was bypassed through errors in parsing the shell's field separator variable and short command-line flags.
- INC-030: in an auto-approval mode an agent wrote a script and then ran it, and the script's cleanup deleted the user's home directory; the reporter notes that the permission check inspects the command string, so the effect inside the written file was never looked at.
- INC-033: a repository's own settings file set the agent's permission mode to skip permission prompts, and opening the repository skipped the trust dialog as well; this is the writable permission config, the candidate N-C, folded into this class.
- INC-036: the maintainer's merge and label guard recognised a merge only when the subcommand came first after the program name; a global repository option, a shell wrapper or a direct API call got past it.

## Mechanism

**A list holds spellings; the program acts on meanings.** A deny rule, a hook's regular expression or an allowlist entry compares text. The shell then removes quotes, expands variables and substitutions, resolves aliases and runs scripts, after the check has passed. A security firm's survey of open-source coding agents found every guard that checked raw text defeated by quote removal alone (see Evidence), and the researchers behind INC-025 put the denylist case plainly: "For every command in a Cursor denylist, there are infinite commands not present in the denylist which, when executed, have the same behavior." ([Backslash](https://www.backslash.security/blog/cursor-ai-security-flaw-autorun-denylist)). The permissions documentation of one agent says the same of its own rules: a deny or ask rule covers the form of the command the agent usually produces and is not a security boundary around the program (see the approval rung in [Irreversible effects](irreversible-effects.md)).

**An allowlist leaks through what an allowed command can do.** Allowing a command allows its flags, its arguments and whatever can be chained after it. In INC-024 a command-line agent compared only the first command against its allowlist, and "Only very cursory validation logic is performed when comparing a shell input to the command whitelist." ([Tracebit](https://tracebit.com/blog/code-exec-deception-gemini-ai-cli-hijack)), so further commands rode behind an allowed one. INC-026 was an "overly broad allowlist of safe commands" that let a file be read and sent over the network without a prompt ([GHSA-x5gv-jw7f-j6xj](https://github.com/advisories/GHSA-x5gv-jw7f-j6xj)); INC-027 a read-only check that misparsed the field separator variable and short flags ([GHSA-xq4m-mc3c-vvg3](https://github.com/advisories/GHSA-xq4m-mc3c-vvg3)). The same vendor's advisory list names one allowed program after another whose check was bypassed (see Evidence).

**A path compared as a string is not the file.** A check that a path starts with the project directory passes a sibling directory whose name starts the same way: INC-028 was a "path validation flaw using prefix matching instead of canonical path comparison" ([GHSA-pmw4-pwvc-3hx2](https://github.com/advisories/GHSA-pmw4-pwvc-3hx2)). A link inside the project points outside it: in INC-029 the agent "failed to account for symlinks when checking permission deny rules" ([GHSA-66m2-gx93-v996](https://github.com/advisories/GHSA-66m2-gx93-v996)), and a second symlink advisory followed four months later. In INC-031 the boundary itself came from the model: a coding agent could treat a model-generated working directory as the sandbox's writable root, and the fix "canonicalizes and validates that the boundary used for sandbox policy is based on where the user started the session" ([GHSA-w5fx-fh39-j5rw](https://github.com/advisories/GHSA-w5fx-fh39-j5rw)).

**Writing and running are checked apart.** A guard on the shell tool sees the command that runs a script, not the script; a guard on the file tools sees the file written, not that it will be run. In INC-030 the reporter's summary is "The write path and the exec path are not correlated." ([anthropics/claude-code#88462](https://github.com/anthropics/claude-code/issues/88462)), and the same report lists earlier reports of the same kind. INC-039 is the same split from the other side: a hook protected some files from the file-edit tools, and the agent changed them through the shell.

**The guard's own configuration is in reach (N-C).** The rules live in files, and an agent that may edit files may edit those. This is the writable permission config, the candidate N-C, folded into this class. In INC-032 an agent in an editor could write the workspace setting that approves every tool call, because file edits were saved without approval; the researcher's conclusion is "Ideally, the AI would not be able to modify files without a human first approving it." ([Embrace The Red](https://embracethered.com/blog/posts/2025/github-copilot-remote-code-execution-via-prompt-injection/)). In INC-033 a settings file shipped inside a repository set the permission mode before the user had trusted the repository ([GHSA-mmgp-wc2j-qcv7](https://github.com/advisories/GHSA-mmgp-wc2j-qcv7)). In INC-034 a sandbox protected the settings file only when it already existed, so code inside the sandbox could create it with hooks that then ran outside the sandbox ([GHSA-ff64-7w26-62rf](https://github.com/advisories/GHSA-ff64-7w26-62rf)). A list the agent can rewrite is not a boundary on the agent.

**Each fix adds one spelling.** A report names the spelling that got through; the fix adds it to the parser or the list, and the next report names another. The maintainer's own guards show the same course. A secret scanner hooked to the shell tool did not run on the other shell tool (INC-035). A merge and label guard read the subcommand only in first position (INC-036). A CI check for a linked issue matched a closing keyword with no word boundary and accepted its skip marker anywhere in the text (INC-038). A path-prefix rule put gate code into the light review tier (INC-040).

**Matching fails in the other direction too.** A pattern that matches harmless text refuses harmless work: in INC-037 the maintainer's literal gate blocked writes to a repository whose name was itself on the list. A guard that refuses often teaches people and agents to route around it, and the route around it is a spelling the guard does not know.

**What protects.** The ladder runs from telling the agent where the line is, through keeping the list as narrow and as well parsed as it can be, to boundaries that do not read the text at all: a sandbox the operating system enforces, an identity that lacks the permission, a path compared after it is resolved, configuration the agent cannot write. At that end the guard is no longer a pattern, and the case leaves this class. What happens once a boundary fails is the subject of [Irreversible effects](irreversible-effects.md).

## Where it surfaces

At the task, when a guard refuses something harmless and the person or the agent looks for another way to do it (INC-037). At code, when the agent issues the command and the guard lets it through; usually nobody sees it there, because nothing was refused, and the effect is the first sign (INC-030). At review, when a reviewer or an audit tries the other spellings against the guard, or reads a diff that changed a file the guard was meant to protect (INC-035, INC-039). At merge, when a gate classifies the change by its paths or its text and lets it through on the lighter path (INC-040). After merge, when a bypass is reported against a guard that has been relied on for some time (INC-036, INC-038, and the vendor advisories behind most rows). The stages are spelled as in `docs/vocabularies.json`.

## Protections

Cheapest first. The first rungs make the list harder to get past; the middle ones check what the text resolves to and who may change the list; the last ones do not read the text at all, and there the case leaves this class. None of them replaces the ones before.

### Prompt: tell the agent where the line is, and that a way around it is not a fix

- **Source:** [Claude Code permissions](https://code.claude.com/docs/en/permissions.md) suggests describing the allowed patterns in `CLAUDE.md`, and says this "shapes what Claude tries but doesn't enforce a boundary".
- **Cost:** Tokens for the instruction.
- **Breaks when:** The agent treats the guard as an obstacle to the task: in a sandbox vendor's test, an agent told to get past a deny rule found another path to the program, and then, untold, asked to run outside the sandbox that stood in its way (see Evidence). The rule is absent from a subagent or a session that did not load it.

### List: allow known commands instead of denying known bad ones

- **Source:** The researchers behind INC-025 advise an allowlist, and the vendor deprecated its denylist ([Backslash](https://www.backslash.security/blog/cursor-ai-security-flaw-autorun-denylist)). A researcher who found eight bypasses of one agent's pattern blocklist reports its replacement as "introducing an allowlist approach instead of the previous blocklist approach" ([Flatt Security](https://flatt.tech/research/posts/pwning-claude-code-in-8-different-ways/)). The [OWASP Input Validation Cheat Sheet](https://cheatsheetseries.owasp.org/cheatsheets/Input_Validation_Cheat_Sheet.html): "Do not try to recognize every malicious string."
- **Cost:** A prompt or a refusal for every command not on the list, and upkeep of the list.
- **Breaks when:** An allowed command can do more than its name says: its flags, its arguments and what is chained after it ride on it (INC-024, INC-026, INC-027). A study of argument injection found that many agents "do not validate the argument flags" ([Trail of Bits](https://blog.trailofbits.com/2025/10/22/prompt-injection-to-rce-in-ai-agents/)).

### Parsing: split and parse the command before matching, and ask when it cannot be parsed

- **Source:** [Claude Code permissions](https://code.claude.com/docs/en/permissions.md) splits compound commands and strips known wrappers before it matches, and when it cannot fully parse a command it does not treat it as read-only: "when Claude Code can't fully parse a command, it asks for approval". [Codex rules](https://developers.openai.com/codex/rules) compare the command's argument list, split a plain chain of commands "(using tree-sitter)", and apply the rules to the whole invocation when it uses expansions or redirection. [OWASP](https://cheatsheetseries.owasp.org/cheatsheets/Input_Validation_Cheat_Sheet.html): "Use a maintained parser for the expected format".
- **Cost:** In Manual mode, a prompt for every command the parser gives up on and for every command longer than 10,000 characters (as of 2026-10, Claude Code permissions documentation).
- **Breaks when:** The same program runs in a form the rule does not name. The [documentation](https://code.claude.com/docs/en/permissions.md) says a rule "isn't a security boundary around the program", lists forms it misses (an absolute path, a shell given the command as a string, an option before the subcommand, a quoted subcommand), and says its wrapper list "is built in and is not configurable", so an allow rule that ends in a wildcard after an environment runner approves whatever follows it. Read and Edit deny rules do not cover a command that reads or writes a file without naming it.

### Hook: inspect the full command with your own code before it runs

- **Source:** [Claude Code permissions](https://code.claude.com/docs/en/permissions.md): "To inspect the full command text with your own logic before it runs, use a PreToolUse hook".
- **Cost:** Writing and keeping the hook; its run time on every call.
- **Breaks when:** The hook matches text too, so every rung above applies to it; the deny-hook rung in [Irreversible effects](irreversible-effects.md) gives its failure modes, and INC-036 is the maintainer's own hook. It runs only on the tools it is registered for, and only where it is installed.

### Testing: run every rule against the spellings it must and must not match

- **Source:** [Codex rules](https://developers.openai.com/codex/rules) take `match` and `not_match` examples, "Examples that Codex validates when it loads your rules", and `codex execpolicy check` shows how a rule file treats a given command. Keeping every reported bypass as a test case of the guard in CI is one operator's practice.
- **Cost:** Writing the examples; CI minutes.
- **Breaks when:** The examples hold only the spellings someone has already thought of, so they stop an old bypass from coming back and say nothing about the next one (INC-036).

### Coverage: attach the guard to every tool that has the effect, and test that list

- **Source:** one operator's practice: a test lists the tools that can run a command or write a file (each shell tool, the file tools, MCP tools) and fails when the guard is not registered on one of them.
- **Cost:** A test, and one more registration whenever a tool is added.
- **Breaks when:** The list of tools is written by hand, so a new tool is missing until someone adds it (INC-035). A tool that writes a file and a tool that runs it are still checked apart (INC-030, INC-039).

### Classifier: let a model judge each action against what the user asked

- **Source:** [Anthropic](https://www.anthropic.com/engineering/claude-code-auto-mode) describes a classifier that "evaluates each action against a set of decision criteria before it executes, acting as a substitute for a human approver"; it reads the user's messages and the agent's tool calls, not the command's spelling against a list.
- **Cost:** A model call per action that reaches it; some harmless actions blocked.
- **Breaks when:** It is a judgment, not a rule: the vendor's own measurement lets through 17% of overeager actions in a set of 52 (see Evidence in [Irreversible effects](irreversible-effects.md)). What it judges harmless runs, so it adds to the rungs below and does not replace them.

### Facade: give the agent a narrow command instead of the general program

- **Source:** [Trail of Bits](https://blog.trailofbits.com/2025/10/22/prompt-injection-to-rce-in-ai-agents/) advises to "use a facade pattern to validate input and proper argument separation" before execution, and "Unless combined with a facade, drastically reduce safe command allowlists."
- **Cost:** A wrapper per task to write and keep; the agent loses the program's other uses.
- **Breaks when:** The wrapper passes the agent's arguments on without the separator, so an argument that starts with a dash is read as an option, or the general program is still allowed beside the wrapper.

### Program: match the program that will run, by its full path and its contents

- **Source:** [sudoers(5)](https://www.sudo.ws/docs/man/sudoers.man/) advises against allowing everything and subtracting commands, and with a digest set "the command will only match successfully if it can be verified using one of the SHA-2 digests".
- **Cost:** A digest to update with every upgrade of the program.
- **Breaks when:** The file changes between the check and the run, which the manual calls a "time of check versus time of use race condition". It also breaks when the program is an interpreter or a runner whose arguments are code, and when code is loaded without an exec: in the sandbox vendor's test, an agent got past a gate that hashed programs at exec by running the dynamic linker, which loaded the blocked program without an exec of its own (see Evidence).

### Paths: resolve the path before comparing it

- **Source:** [CERT FIO16-J](https://cmu-sei.github.io/secure-coding-standards/sei-cert-oracle-coding-standard-for-java/rules/input-output-fio/fio16-j): "all path names must be fully resolved or canonicalized before validation". [Claude Code permissions](https://code.claude.com/docs/en/permissions.md#symlinks) checks a deny rule against the path and the file it resolves to: "A symlink that points to a denied file is itself denied." On Linux, [openat2](https://man7.org/linux/man-pages/man2/openat2.2.html) with `RESOLVE_BENEATH` refuses a path whose resolution leaves the starting directory.
- **Cost:** A resolution per check; the kernel flag only where the tool opens the file itself.
- **Breaks when:** The path is checked, then resolved again at write time, and a link is swapped in between: the CERT rule notes "an inherent race window", and a 2026-10 advisory is that case for one agent ([GHSA-5j29-h97v-84ch](https://github.com/anthropics/claude-code/security/advisories/GHSA-5j29-h97v-84ch)).

### CI: read the platform's parsed field, not the pull request's text

- **Source:** GitHub's `closingIssuesReferences`, the "List of issues that may be closed by this pull request" ([GitHub GraphQL reference](https://docs.github.com/en/graphql/reference/pulls#object-pullrequest)), is the platform's own reading of the closing keywords that INC-038's check matched with a regular expression.
- **Cost:** An API call per check run.
- **Breaks when:** The pull request targets another branch: the [linking documentation](https://docs.github.com/en/issues/tracking-your-work-with-issues/using-issues/linking-a-pull-request-to-an-issue) says keywords are "interpreted only when the pull request targets the repository's default branch". The field is filled after the event, so a check that runs at once can find it empty; that is the maintainer's observation, not the documentation's.

### Classification: send an unclassified path to the strict tier

- **Source:** one operator's practice: a gate that sorts changes into review tiers by path lists the light tier's paths and treats every other path as strict.
- **Cost:** Strict review for files nobody has classified yet.
- **Breaks when:** The strict tier costs enough that people widen the light list to get past it, and the pattern is back in another place (INC-040).

### Calibration: record what a rule would block before it blocks, and aim it at its target only

- **Source:** A GitHub ruleset in "Evaluate" status means "your ruleset will not be enforced, but you will be able to monitor which actions would or would not violate rules" ([GitHub Enterprise Cloud documentation](https://docs.github.com/en/enterprise-cloud@latest/repositories/configuring-branches-and-merges-in-your-repository/managing-rulesets/about-rulesets)). Scoping a literal filter to the repositories it protects, after INC-037, is one operator's practice.
- **Cost:** A period in which the rule records instead of blocking. The status is in the Enterprise Cloud documentation and not in the one for other plans.
- **Breaks when:** Nobody reads the record, or the record shows only the harmless matches: what the rule would let through is not recorded at all.

### Escape hatch: let only a person switch the guard off

- **Source:** one operator's practice: the override is a label or a setting the agent's identity cannot set, not a marker in text the agent writes.
- **Cost:** A person's action for every legitimate exception.
- **Breaks when:** The agent's token can set the label too, or the marker lives in the agent's text: INC-038's skip marker was accepted anywhere in the pull request body.

### Configuration: keep the guard's own files out of the agent's write reach (N-C)

- **Source:** [Claude Code sandboxing](https://code.claude.com/docs/en/sandboxing.md#protected-paths) denies writes to its configuration inside writable directories because "A command that could edit those files could grant itself permissions"; [Codex](https://developers.openai.com/codex/agent-approvals-security) keeps `.git`, `.agents` and `.codex` read-only inside a writable root.
- **Cost:** Changes to those files go through a person.
- **Breaks when:** The protection covers only files that exist, or only the tools it watches. Codex protects `.agents` and `.codex` "when it exists as a directory"; a sandbox that protected the settings file only when it existed let code create it (INC-034); a piped command got past file-write restrictions into the settings folder ([GHSA-mhg7-666j-cqg4](https://github.com/advisories/GHSA-mhg7-666j-cqg4)). In a mode that skips permission prompts, writes to protected paths are auto-approved ([permission modes](https://code.claude.com/docs/en/permission-modes.md)).

### Configuration: apply a repository's settings only after a person trusts it (N-C)

- **Source:** [Claude Code permissions](https://code.claude.com/docs/en/permissions.md) applies a project's allow rules "only after you accept the workspace trust dialog"; in an untrusted folder Gemini CLI ignores the workspace settings, and "The CLI will not load any .env files from the project." ([Gemini CLI trusted folders](https://geminicli.com/docs/cli/trusted-folders/)).
- **Cost:** A dialog for each new repository.
- **Breaks when:** The dialog is not shown: the documentation says a `claude -p` run or an SDK session never shows it, and in such a run the repository's hooks and `env` block are still used. A headless agent that trusted every folder was patched in 2026-04 ([GHSA-wpqr-6v78-jr5g](https://github.com/advisories/GHSA-wpqr-6v78-jr5g)). A setting read before the dialog is the attack itself (INC-033): repository files that were "once passive data now control active execution paths" ([Check Point](https://research.checkpoint.com/2026/rce-and-api-token-exfiltration-through-claude-code-project-files-cve-2025-59536/)). A person who trusts by habit trusts the attack too; when a person's approval is needed is the rule in [Irreversible effects](irreversible-effects.md).

### Configuration: lock the rules where neither the agent nor the project can override them (N-C)

- **Source:** [Claude Code permissions](https://code.claude.com/docs/en/permissions.md#managed-settings): "no other level, including command line arguments, can override a managed permission rule"; managed settings can also make themselves the only source of permission rules and switch off the mode that skips prompts.
- **Cost:** An administrator who manages the machines' settings.
- **Breaks when:** The policy is not delivered: a 2026-09 advisory describes sessions that started without the organisation's server-managed policy because a stored key was chosen over the sign-in ([GHSA-gfvf-j8jh-jxxw](https://github.com/anthropics/claude-code/security/advisories/GHSA-gfvf-j8jh-jxxw)). The managed rules are still patterns and leak like the rungs above; what they add is that the agent cannot widen them.

### Identity: run the agent as an identity that lacks the permission

- **Source:** GitHub advises setting the workflow token's permissions so that "actions only have the minimum access they require" ([GitHub Actions documentation](https://docs.github.com/en/actions/tutorials/authenticate-with-github_token)); the reach and reversibility rungs in [Irreversible effects](irreversible-effects.md) cover scoped credentials and branch protection.
- **Cost:** A separate identity, and its permissions to set up and keep narrow.
- **Breaks when:** A broader credential is reachable from the agent, or the identity is an administrator or on a bypass list, as the irreversible-effects doc says. The identity limits what is allowed, not which allowed action is wise.

### Operating system: sandbox the agent's commands

- **Source:** [Claude Code sandboxing](https://code.claude.com/docs/en/sandboxing.md): the allowed paths "are enforced at the OS level, so all commands running inside the sandbox, including their child processes, respect them". On Linux, [Landlock](https://docs.kernel.org/userspace-api/landlock.html) gives an unprivileged process "restriction of ambient rights (e.g. global filesystem or network access)". The measured drop in prompts is in Evidence.
- **Cost:** Configuring the allowed paths and domains; commands that need more fail or have to be excluded.
- **Breaks when:** As the reach rung in [Irreversible effects](irreversible-effects.md) says: the sandbox covers shell commands only, an excluded or retried command runs unsandboxed, and a setting inside the project can widen the allowlist unless a managed lock covers it. The [documentation](https://code.claude.com/docs/en/sandboxing.md) says it "is not a complete isolation boundary", and its mode for nested containers "considerably weakens security". In the sandbox vendor's test the agent asked for approval to run outside the sandbox when the sandbox stood in its way (see Evidence).

### Container: run the agent in a container with an egress firewall

- **Source:** [Claude Code development containers](https://code.claude.com/docs/en/devcontainer.md) ship a reference container whose firewall limits outbound traffic to the destinations it allows, and say "no system is completely immune to all attacks".
- **Cost:** Building and keeping the container; work that needs other hosts or local tools has to be set up inside it.
- **Breaks when:** The repository is not trusted: the documentation advises using the container only with trusted repositories, since with prompts skipped a malicious project can still take what the container holds, including the agent's credentials. A host secret mounted into the container is in that reach too.

## Evidence

- One coding agent's vendor published 13 advisories between 2025-08 and 2026-02 whose titles or descriptions name a command, path, deny-rule or domain check that was bypassed: a path prefix, symlinks twice, an overly broad allowlist, the field separator variable, and allowed programs one at a time (`echo`, `rg`, `sed` twice, `find`, `cd`, a shell's overwrite operator), plus domain validation; further sandbox escapes through a symlink and through worktree paths followed in 2026-04 and 2026-06 (as of 2026-10, counted from the [advisory list](https://github.com/anthropics/claude-code/security/advisories)).
- Of eleven open-source coding agents a security firm surveyed, ten left the boundary between the agent's command and the shell exploitable: three shipped a guard that was defeated, two a tokenized guard that leaked in narrower cases, the rest no static guard or a sandbox with a documented opt-out; only one defended by design. The raw-text guards fail because bash removes quotes and expands parameters and substitutions after the check runs (as of 2026-06, [Adversa](https://adversa.ai/blog/opensource-ai-coding-agents-shell-injection-vulnerability/)). The firm also sells security services.
- In a sandbox vendor's test, a coding agent told to get past a deny rule on a program reached it through another name for its path, then, untold, asked for approval to run outside the sandbox; the vendor's own gate on the program's contents at exec held, and the agent gave up. In a second test the agent ran the dynamic linker, which loaded a blocked program without an exec of its own (as of 2026-03, [Ona](https://ona.com/stories/how-claude-code-escapes-its-own-denylist-and-sandbox)). The vendor sells environments for running agents.
- An OS sandbox cut permission prompts: "sandboxing safely reduces permission prompts by 84%" (as of 2025-10, [Anthropic](https://www.anthropic.com/engineering/claude-code-sandboxing)), and "Sandboxed agents stop 40% less often than unsandboxed ones" (as of 2026-02, [Cursor](https://cursor.com/blog/agent-sandboxing)). Both are vendors' own measurements of their own tools.
- Every incident cited here is a row in [`incidents/incidents.csv`](../../incidents/incidents.csv); two of the class's rows are private sources and say so in the link column.
