---
applies_when: an agent or a person writes to a public repository while able to see private material — client or employer names, people's names, internal hostnames, paths or project names from a private repo — and those exact strings must never appear in the public tree, commits or pull request text
applies_when_not: credentials and tokens with a known shape (a secret scanner's built-in rules cover those; see docs/agent-safety-hooks.md); a private repository nobody outside can read; whether a person reviewed a change before it merged is docs/publishing-discipline.md
signed_off:
---

# Keeping a list of private strings out of a public repository

## The problem

Secret scanners look for things shaped like keys. A client's name, a colleague's surname or the
path of a private repo has no shape — the only way to catch it is to know the exact string. That
means keeping a list of the very strings you must not publish, and checking against it. Agents
make this more likely to matter: an agent that has read private notes will reuse what it read.
The ways it goes wrong:

- **Public before the check** — the check runs in CI, but the branch it scans is already pushed
  to a public repo; merged or not, the commit can be fetched.
- **The list leaks** — it is committed, printed in a log, or held somewhere readable by more
  jobs than the one that needs it. A list is an index of exactly what you wanted hidden.
- **Empty list, green check** — the list is missing or empty, the scan finds nothing, and
  reports clean.
- **Bypassed** — a local hook is skipped with `--no-verify` or `SKIP=`, or a push-protection
  prompt is clicked through.
- **Coverage gaps** — the check sees files but not history, commit messages, pull request or
  issue text; or it matches exact strings and the text has a variant (case, spacing, a typo).
- **Forks** — a pull request from a fork runs without the repository's secrets, so a list held
  as a secret is not there.
- **Cleanup is partial** — once public, a rewrite does not reach clones, forks or cached views,
  and a name cannot be rotated like a key.

Each option is marked **tried** (we run or ran it; the example says where) or **sourced** (read
from the tool's documentation or a project's pull request). Quotes were checked on 2026-09-17.

## The options

### 1. Keep private material out of the writer's reach

**How it works.** The agent cannot leak what it never read. Deny its file tools the private paths
and run it in a sandbox that cannot see them. Claude Code's `Read` deny rules block its file tools
and recognised shell commands, but "They don't apply to a command that reads files without naming
them, such as `grep -r pattern .`" — for every process, "enable the sandbox"
([permissions](https://code.claude.com/docs/en/permissions)). OWASP's guidance for LLM
applications: "Limit access to sensitive data based on the principle of least privilege"
([LLM02:2025](https://genai.owasp.org/llmrisk/llm022025-sensitive-information-disclosure/)).

**Best pick when** the private material sits in files you can name and the agent does not need
it for the public work.

**Cost.** Low to configure. It does not help a person, or an agent whose instructions themselves
carry private context.

**Lifecycle.** Harness settings, per machine. Status: sourced — not in place for us (see our
choice).

### 2. Written rules for the writer

**How it works.** The agent's instructions list what must not be published and how to cite
private evidence. OWASP on this: "such restrictions may not always be honored and could be
bypassed via prompt injection or other methods."

**Best pick when** alongside any mechanical check — never alone.

**Cost.** Free. It is advice: nothing fails when it is ignored.

**Lifecycle.** Instruction files. Status: tried — this repo's agent instructions.

### 3. A local check before the commit or the write

**How it works.** A hook reads the list from outside the repo and refuses the commit.
[git-secrets](https://github.com/awslabs/git-secrets) takes patterns from git config or from a
provider command: `git secrets --add-provider -- cat /path/to/secret/file/patterns`.
[gitleaks](https://github.com/gitleaks/gitleaks) loads its rules from `--config`,
`GITLEAKS_CONFIG` or `GITLEAKS_CONFIG_TOML` before a `.gitleaks.toml` in the repo, so the rules
can live in your home directory. A pre-call hook in the agent's harness can grep what the agent
is about to write, including pull request text, before the tool runs (option 4 of
[`agent-safety-hooks.md`](agent-safety-hooks.md)).

**Best pick when** one or a few people on machines you control, and you want the string stopped
before it leaves the machine.

**Cost.** Free. Every machine and clone needs the setup and the list. Git's hooks are skipped with
`--no-verify`; gitleaks' pre-commit hook with `SKIP=gitleaks`.

**Lifecycle.** A hook plus a private list per machine. Status: sourced.

### 4. A server-side push block

**How it works.** The host refuses the push. GitHub custom patterns for secret scanning can block
pushes, but "you must ensure that Secret Protection is enabled on your repository", and "Push
protection for custom patterns will only apply to repositories in your organization that have
secret scanning as push protection enabled"
([custom patterns](https://docs.github.com/en/code-security/secret-scanning/using-advanced-secret-scanning-and-push-protection-features/custom-patterns/defining-custom-patterns-for-secret-scanning)).
Secret Protection is $19 per active committer per month
([plans](https://github.com/security/plans)). GitLab push rules (Premium, Ultimate) match file
names, commit messages, branch names and emails — the "secret files" rule is a list of file
names, not contents ([push rules](https://docs.gitlab.com/user/project/repository/push_rules/)).
GitGuardian custom detectors are "only available for workspaces under our Business plan"
([detector settings](https://docs.gitguardian.com/secrets-detection/customize-detection/detector-settings)),
and GitGuardian staff build them from your list.

**Best pick when** the repo belongs to an organization that already pays for Secret Protection.

**Cost.** The plan. Anyone with write access can bypass push protection with a reason, by
default. Pull request and issue text is scanned for alerts, not blocked.

**Lifecycle.** Organization settings. Status: sourced. Dropped on fit for us: a personal-account
repository cannot get custom-pattern push protection.

### 5. A CI scan with the list held as a secret

**How it works.** The list is a repository secret; a job reads it and fails on any hit, printing
only file paths. GitLab Ultimate can hold the rules in a separate private project as a remote ruleset
([customize](https://docs.gitlab.com/user/application_security/secret_detection/pipeline/configure/)).

**Best pick when** you have no push block and want a check nobody can skip locally.

**Cost.** One job per pull request. It runs after the push, so the string is already on the
server. Log masking: "Never use structured data as a secret", because redaction "largely relies
on finding an exact match" ([secure use](https://docs.github.com/en/actions/reference/security/secure-use));
a multi-line list is that case, so never print it. Fork pull requests: "secrets are not passed to
the runner when a workflow is triggered from a forked repository"
([events](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows)),
so the job must fail or say it skipped — not pass. And the secret is readable "by any workflow
that runs with secrets", as one project put it when rejecting this design
([prism#476](https://github.com/sandydargoport/prism/pull/476)).

**Lifecycle.** A workflow, a script and a secret someone must set and keep current. Status:
tried — this repo.

### 6. A hashed list in the repo

**How it works.** Commit hashes of the strings, not the strings; hash every token of the change
and compare. prism#476 stores "HMAC-SHA256 of each entry, keyed by `PRISM_PII_SALT`" — a secret
key, so a guess cannot be tested, but again no fork coverage.
[agentic-org#1310](https://github.com/mishrasanjeev/agentic-org/pull/1310) commits the salt: the
hashes "are not secret: the salt is committed, so anyone can test a guessed name" — the trade for
a check that runs anywhere, forks and laptops included. It also scans commit messages, the branch
name and the pull request text, and normalises tokens so that `AcmeVerify` and `ACME-VERIFY` match.

**Best pick when** outside contributors open pull requests, or the check must run the same
locally and in CI.

**Cost.** A custom script — no packaged tool was found. Token matching misses a string buried in a
longer word. A committed salt lets anyone confirm a guess.

**Lifecycle.** A script, the hash file and, with HMAC, a key. Status: sourced.

### 7. Publish through an export pipeline

**How it works.** Work happens in the private repo; a tool copies it to the public one and
rewrites or refuses on the way. [Copybara](https://github.com/google/copybara/blob/master/docs/reference.md)
has `core.replace`, `metadata.scrubber` for commit messages and `core.verify_match`, which
"Verifies that a RegEx matches (or not matches) the specified files", stopping the export if it
fails.

**Best pick when** the public repo really is derived from a private one, one way.

**Cost.** Building and running the pipeline; changes made directly on the public side need a
reverse flow. Like 4 it checks before anything is public, but needs no paid plan and cannot be
skipped from a laptop.

**Lifecycle.** Pipeline config on the private side. Status: sourced. Dropped on merit: FBShipIt —
"This project is no longer maintained."

### 8. Named-entity detection

**How it works.** A model finds person names, organisations and emails you never listed.
[Presidio](https://microsoft.github.io/presidio/) does this, and also takes a `deny_list`.

**Best pick when** the risk is names you cannot list in advance, in an export step or CI.

**Cost.** False positives in code and prose; and "there is no guarantee that Presidio will find
all sensitive information."

**Lifecycle.** A service or library plus tuning. Status: sourced.

### 9. Rewrite history after a leak

**How it works.** [git-filter-repo](https://github.com/newren/git-filter-repo) `--replace-text`
or the [BFG](https://rtyley.github.io/bfg-repo-cleaner/) replaces listed strings across history,
then you force-push.

**Best pick when** a string already landed. It is the last step, not a check.

**Cost.** Every clone must re-clone. On GitHub the data stays reachable "In any clones or forks of
your repository", by SHA in cached views and "Through any pull requests that reference them"; in
forks it "will continue to be accessible there"
([removing sensitive data](https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/removing-sensitive-data-from-a-repository)).

**Lifecycle.** One-off. Status: sourced.

Also considered and dropped on merit: detect-secrets' `--word-list`, which is an allowlist — "if a
secret contains a word in the list we ignore it" — not a denylist.

## How to choose

First, in order:

1. **Can the writer read the private material at all?** If yes and it does not need to, start
   with 1; everything else catches what 1 lets through.
2. **Is the public repo derived from a private one?** Yes → 7.
3. **Does an organization that owns the repo pay for Secret Protection?** Yes → 4.
4. **Do outsiders open pull requests from forks?** Yes → 6 with a committed salt, or accept that
   5 cannot check them.

| Option | Fits only if |
|---|---|
| 1 | the private material has paths you can deny, or the agent can work without it |
| 2 | combined with a mechanical check |
| 3 | you control every machine that commits |
| 4 | an organization repo with the paid plan |
| 5 | you accept the check runs after the push and fails on forks |
| 6 | you will write the tokeniser and accept its trade (guessable, or no forks) |
| 7 | changes flow one way, private to public |
| 8 | you can tune out false positives, and a list alone is not enough |
| 9 | something already leaked |

Among what is left: only 4 and 7 stop the string before it is public; 3 stops it earlier still,
but can be skipped; 5 and 6 are the checks nobody skips, and 6 also covers commit messages and
pull request text. If nothing fits, do 1 and 2, and review every public change against the list
yourself — and say that is what you do.

**At more than one developer.** A local hook (3) needs installing on every machine, and one
person without it is the gap. The list has an owner who adds to it; everyone else only needs the
check to fail. With a list held as a secret (5), contributors cannot run the check before pushing,
so pair it with 6 or give them a local copy. The more people, the more the list itself is the
risk: prefer hashes (6) or a pipeline (7) over sharing plaintext.

**Examples.** An organization on GitHub Team with Secret Protection: 4 with the list as custom
patterns, plus 1 — this points away from our choice, which only a personal account forced. An
open-source mirror of an internal codebase: 7 with Copybara, with 9 as the fallback. A project
that takes fork pull requests: 6 with a committed salt, as agentic-org#1310 chose.

**Our own choice.** A public repository on a personal account, written mostly by an agent, so 2
plus 5: [`gitleaks.yml`](../.github/workflows/gitleaks.yml) runs gitleaks and then the scrub. It
costs one job step and a secret. Gaps:

- The secret was never set. Every one of the 32 scrub runs from 2026-09-16 to 2026-09-17 logged
  "Scrub clean" and was green, having checked nothing
  ([`scrub-without-literals-reported-clean.md`](../examples/scrub-without-literals-reported-clean.md)).
  The script now fails on an empty list, which means every pull request — forks included — fails
  until someone sets it.
- It runs after the push, on the checkout only: not history, commit messages, or pull request and
  issue text, and exact strings only.
- The agent's global instructions load a private repository into its context, so option 1 does not
  hold for us; 2 is what stands between that context and a public page.

The script and how to tell it ran: [`personal-literal-scrub.md`](../resources/personal-literal-scrub.md).
