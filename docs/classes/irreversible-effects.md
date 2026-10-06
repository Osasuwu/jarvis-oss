---
class: irreversible-effects
scope: cross-cutting
surfaces_at:
  - task
  - code
  - ci
  - merge
applies_when: An AI coding agent, or a CI job it drives, can take an action you could not undo afterwards (delete data that is in no repository, rewrite shared history, change a production database, publish a package, expose a secret, spend money, change who has access), and you want to know when it must stop and ask a person and what else limits the damage.
applies_when_not: Mistakes a revert, a failed test or a code review would catch. Prompt injection as the route into the agent (each class doc covers its own route; this doc covers what the injected or mistaken action can reach). Runaway cost that stops by itself when a quota ends.
---

# Irreversible effects

## TL;DR

An agent should stop and ask a person only before an effect that cannot be undone, and whether an effect can be undone is decided by what the acting identity can reach, not by the command's name. Three layers cut the damage, in this order: shrink what the identity can reach, make what it can reach restorable, and put a human approval at the effect itself, in words that name the effect.

## Symptom

Something an agent did cannot be rolled back. A directory outside the workspace is gone, a shared branch was rewritten, a production table was dropped together with the backup that sat next to it, a package went out under the project's name, a credential ended up in a public place. The command was often ordinary: the same one had been harmless in a scratch clone or a sandbox. The agent had frequently been told in prose not to do this, or had asked first, but in a way the person could not tell what it would delete.

## Examples

These rows come from different classes; the common thread is the effect, not the cause.

- INC-001: a cleanup command meant for a temporary directory ran against the real home directory, because an environment variable set in an earlier tool call did not carry over to the next one. The same `rm -rf` on the intended target would have been routine.
- INC-002: an agent rewrote the history of a branch with an open public pull request. It did ask, but the force-push appeared only as a parenthetical inside an option the person picked for another reason.
- INC-003: an agent deleted uncommitted files and only then asked for permission to do it.
- INC-004: an agent working in a development session deleted data from the production database. The vendor's fixes were separate development and production databases, a planning-only mode, and a restore from backup.
- INC-005: an attacker took a long-lived registry publish token out of a CI workflow, and malicious versions went out under the project's name for hours. Publication to a registry cannot be fully taken back, because the bad version stays installable by anyone who already pulled it.

## Mechanism

**Irreversibility is a property of the verb together with the target, and the target is whatever the identity can reach.** `git push --force` on a branch nobody else reads, with the remote's other copy intact, is a tidy-up. The same command on a branch with an open pull request rewrites a public record (INC-002). `rm -rf` inside a throwaway container is a cleanup the next build repeats; the same text against a real home directory is data loss (INC-001). `DROP TABLE` or a schema push against a development database is a Tuesday; against the production database it is an outage (INC-004). A rule written in terms of verbs cannot tell these apart, because the text of the command is identical. Only the identity's reach, and whether a restore point exists outside that reach, can.

**The rule, stated once.** An agent stops and asks before an effect when, for the targets its identity can actually reach, either of these holds, and the person has not already approved this exact effect:

1. the effect destroys or overwrites something with no copy outside the identity's reach (no remote branch with the history, no backup under another credential, no snapshot, no version history), or
2. the effect cannot be recalled once it has left the machine: publication under a shared or public name (a registry, a release, a protected branch), a secret disclosed, money spent, access or identity changed.

If a copy exists outside the identity's reach and the effect stays local, the agent does not need to ask; the layers below are what make that true. An ask for anything else teaches the person to approve every prompt, and they already approve most of them (see Evidence). Kinds of effect that usually meet the test: persistent data with no restore point, local data outside the workspace, secrets, publication through a CI or release identity, money, production availability, and changes to access or identity.

**What a valid ask looks like.** It is its own prompt, never a clause inside an option chosen for another reason (INC-002). It comes before the effect, never after (INC-003). It names the target and the effect in plain words. And it is enforced where the effect happens, by a permission or a check the agent cannot talk its way past, because an agent can be argued or rerouted past a rule it only reads (see Evidence).

**Why prose is not a rung.** In the public cases above, the instruction not to do the thing was present and did not hold, and a classifier that reads consent language can clear a destructive command when the developer sounds sure (see Evidence). A prompt rule costs nothing and may still be worth writing; it is not a layer anything depends on, so the ladder below starts at controls the agent cannot reconfigure.

## Where it surfaces

At the task, when the identity and its credentials are chosen: that is where reach is set, and it is the cheapest place to limit it. At the code stage, when the agent issues the command, which is the last moment a permission prompt or a deny hook can stop it. In CI and at merge, where a release or deployment identity acts and a required reviewer or a required check can hold the effect. An effect that was not caught at one of those points is found afterwards, by its damage. The stages are spelled as in `docs/vocabularies.json`.

## Protections

The rungs follow the rule's own order: limit the reach, then make the effect reversible, then require approval at the effect. A later group is for what the earlier ones leave open. None of them replaces the others.

### Reach: run the agent's shell inside a filesystem and network allowlist

- **Source:** [Claude Code sandboxing documentation](https://code.claude.com/docs/en/sandboxing)
- **Cost:** Writing and maintaining the allowlists; commands that need something outside them fail or need an exception; the agent's retries on blocked commands consume tokens.
- **Breaks when:** The sandbox covers shell commands only, so file tools, MCP servers and hooks run outside it. A command listed as excluded, or retried with the sandbox switched off, runs unsandboxed, and in a permissive mode that retry needs no prompt. On native Windows the documentation says commands run unsandboxed. A setting inside the project can widen the allowlist unless a managed lock covers it.

### Reach: give the agent per-environment, scoped credentials, and none for production

- **Source:** [GitHub fine-grained personal access tokens](https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/managing-your-personal-access-tokens) for repository scope and expiry; [a practitioner write-up on keeping production out of an agent's reach](https://www.bytebase.com/blog/how-to-prevent-ai-agent-from-dropping-your-production-database/)
- **Cost:** More credentials to issue, rotate and keep apart; a task that does need production is slower because a person has to do or approve that part.
- **Breaks when:** A broader credential is reachable from somewhere the agent can read. In INC-006 an agent found an infrastructure token in a file unrelated to its task and used it on a volume it took to be staging, and the backups were on that volume. Fine-grained tokens also cannot cover everything: the vendor lists gaps such as packages and some APIs.

### Reach: keep publish authority out of the agent's hands with trusted publishing

- **Source:** [npm trusted publishing](https://docs.npmjs.com/trusted-publishers); the project in INC-005 [adopted it after the incident](https://nx.dev/blog/s1ngularity-postmortem)
- **Cost:** The release workflow is rebuilt around short-lived, workflow-bound tokens; the setup is per package and per registry.
- **Breaks when:** Anyone who can edit the publishing workflow can still publish. Runners the registry does not support (self-hosted ones, at the time of the page) fall back to long-lived tokens. A workflow that runs untrusted input with write permission hands the same reach to whoever controls the input.

### Reversibility: protect branches and tags against force-push and deletion

- **Source:** [GitHub protected branches](https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/managing-protected-branches/about-protected-branches) and [rulesets](https://docs.github.com/en/enterprise-cloud@latest/repositories/configuring-branches-and-merges-in-your-repository/managing-rulesets/about-rulesets)
- **Cost:** A plan tier that includes the feature for private repositories; administrators lose the shortcut of rewriting a branch directly.
- **Breaks when:** The acting identity is an administrator or is on the bypass list, so an agent running under an owner's token inherits the exemption. It protects the remote only: local, uncommitted work (INC-003) is outside it.

### Reversibility: keep backups outside the identity's reach, and test the restore

- **Source:** The vendor's statement in INC-004 (a restore from backup was the way back); [the account in INC-006](https://letsdatascience.com/news/pocketos-founder-reports-ai-agent-deleted-production-databas-8f0213e2), where the backups shared the deleted volume.
- **Cost:** Storage, a second credential or account, and the time to run a restore drill.
- **Breaks when:** The backup shares a volume, account or credential with the data, so one call removes both (INC-006). It also breaks when nobody has restored from it, because then it is a hope, not a restore point.

### Reversibility: turn on deletion protection and versioning on the resource

- **Source:** [Amazon RDS deletion protection](https://docs.aws.amazon.com/AmazonRDS/latest/UserGuide/USER_DeleteInstance.html); [S3 Object Lock](https://docs.aws.amazon.com/AmazonS3/latest/userguide/object-lock.html)
- **Cost:** Retained snapshots and object versions are billed until someone deletes them; clean-up of retained data becomes a deliberate step.
- **Breaks when:** The same identity can switch the protection off, so an identity that may modify the resource may also delete it. Backups taken automatically can disappear with the instance unless they are retained on purpose. Governance-mode locks can be overridden by an identity with the bypass permission.

### Approval: ask at the tool call, in a prompt that names the effect

- **Source:** [Claude Code permissions documentation](https://code.claude.com/docs/en/permissions)
- **Cost:** A person's attention at each ask; a session that keeps asking trains the person to approve without reading.
- **Breaks when:** The prompt does not state the effect (INC-002), arrives after it (INC-003), or is one of many that the person approves by habit. The documentation itself says a deny or ask rule covers the form of the command the agent usually produces and is not a security boundary around the program, and a mode that skips permissions removes the rung altogether.

### Approval: deny the irreversible kinds with a hook at the tool-call boundary

- **Source:** [Claude Code hooks documentation](https://code.claude.com/docs/en/hooks)
- **Cost:** Writing and testing the hook; keeping its list of irreversible kinds current as new commands and tools appear.
- **Breaks when:** The hook crashes or times out and the call goes on through the normal flow, or the hook does not recognise the command. A guard that inspects the raw text of a command is bypassed by anything the shell expands afterwards; the study in Evidence found this in most agents it tested.

### Approval: show the plan before the apply, and make a person read it

- **Source:** [Terraform plan](https://developer.hashicorp.com/terraform/cli/commands/plan)
- **Cost:** A second step and a reader for every change; the plan and the apply are two runs.
- **Breaks when:** The state changes between the plan and the apply, so the applied change differs from the one that was read, or nobody reads the plan. A plan previews the effect; it does not prevent it.

### Approval: require a reviewer on the protected environment that holds the credential

- **Source:** [GitHub deployment environments](https://docs.github.com/en/actions/managing-workflow-runs-and-deployments/managing-deployments/managing-environments-for-deployment)
- **Cost:** A named reviewer must be available for every deployment to that environment; private repositories need a paid plan.
- **Breaks when:** Self-review is left on, so the person who triggered the run can approve it. The environment's secrets are also stored somewhere outside the environment, where a job can read them without the approval. One approver is enough by default, so there is no second person unless it is configured.

### Approval: hold the change behind a required check until a person reads it

- **Source:** one operator's practice. This repository holds any pull request that touches the paths listed in [`.github/hold-paths.json`](../../.github/hold-paths.json) with a required check, [`waiting-human-review`](../../.github/workflows/waiting-human-review.yml), that does not pass until a person has read it; a pull request still in draft is held the same way.
- **Cost:** The reviewer's time, and a wait on every pull request that touches those paths.
- **Breaks when:** A person with administrator rights merges around the check. It also breaks when the path definition omits a location the effect can come through, or when the check cannot run on a change to its own workflow.

## Evidence

- Users approve most permission prompts: Anthropic reports that Claude Code users approve 93% of them (as of 2026-03, [Anthropic, auto mode](https://www.anthropic.com/engineering/claude-code-auto-mode)). A prompt that appears on every action protects little, which is why the rule above asks only at irreversible effects.
- A classifier that decides for the person is not a replacement for the person on irreversible effects: the same source reports a 17% false-negative rate on overeager actions in its own test set of 52 (as of 2026-03, [Anthropic, auto mode](https://www.anthropic.com/engineering/claude-code-auto-mode)), and says the mode is not a substitute for careful human review on high-stakes infrastructure.
- In a vendor's test of that mode, every force-push the agent actually sent was cleared and none was denied; one scored 78 for harm until the classifier found the developer's consent, then 20, and ran (as of 2026-09, [Backslash](https://www.backslash.security/blog/claude-code-auto-mode-catches-a-lot-but-not-everything)). The vendor sells security tooling, so treat it as a claim from a practitioner, not as a neutral measurement.
- Pattern-matching guards on raw command text fail in most open-source coding agents tested: a security firm's study found that bash expands quotes, parameters and substitutions after the check runs (as of 2026-06, [Adversa](https://adversa.ai/blog/opensource-ai-coding-agents-shell-injection-vulnerability/)). The firm also sells security services.
- Every incident cited here is a row in [`incidents/incidents.csv`](../../incidents/incidents.csv). Their links are the primary reports; INC-006 is a news report of the operator's own account and is marked secondary there.
