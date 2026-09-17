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
  jobs or people than the one check that needs it. A list is an index of what you wanted hidden.
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

Each option is marked **tried** (we run or ran it; the example says where — running is not proof
it catches anything) or **sourced** (read from the tool's documentation or a project's pull
request). Quotes were checked against the linked pages on 2026-09-17, in the review on the pull
request that added this doc.

## The options

### 1. Keep private material out of the writer's reach

**How it works.** The agent cannot leak what it never read. Deny its file tools the private paths
and run it in a sandbox that cannot see them. Claude Code's `Read` deny rules block its file tools
and recognised shell commands, but "They don't apply to a command that reads files without naming
them, such as `grep -r pattern .`" — for every process, "enable the sandbox"
([permissions](https://code.claude.com/docs/en/permissions)). OWASP's guidance for LLM
applications: "Limit access to sensitive data based on the principle of least privilege"
([LLM02:2025](https://genai.owasp.org/llmrisk/llm022025-sensitive-information-disclosure/)).

**Best pick when** the writer is an agent, the private material sits in files you can name, and
the agent does not need it for the public work.

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

**Lifecycle.** Instruction files. Status: sourced. We rely on it, but our rules sit in private
instruction files, so there is no public trace.

### 3. A local check before the commit or the write

**How it works.** A hook reads the list from outside the repo and refuses the commit.
[git-secrets](https://github.com/awslabs/git-secrets) takes patterns from git config or from a
provider command: `git secrets --add-provider -- cat /path/to/secret/file/patterns`.
[gitleaks](https://github.com/gitleaks/gitleaks) loads its rules from `--config`,
`GITLEAKS_CONFIG` or `GITLEAKS_CONFIG_TOML` before a `.gitleaks.toml` in the repo, so the rules
can live in your home directory. A pre-call hook in the agent's harness can grep what the agent
is about to write, including pull request text, before the tool runs (option 4 of
[`agent-safety-hooks.md`](agent-safety-hooks.md)) — it runs wherever the agent runs, a cloud
sandbox included.

**Best pick when** the string must stop before it leaves the machine, and every place that
writes — each laptop, or the agent's harness — can be set up with the hook and the list.

**Cost.** Free. Every machine and clone needs the setup and the list. Git's hooks are skipped with
`--no-verify`; gitleaks' pre-commit hook with `SKIP=gitleaks`.

**Lifecycle.** A hook plus a private list per machine. Status: sourced.

### 4. A server-side push block

**How it works.** The host refuses the push. For a repository, GitHub custom patterns block
pushes only if "you must ensure that Secret Protection is enabled on your repository"; for an
organization, only in repositories that have push protection turned on
([custom patterns](https://docs.github.com/en/code-security/secret-scanning/using-advanced-secret-scanning-and-push-protection-features/custom-patterns/defining-custom-patterns-for-secret-scanning)).
Secret Protection is $19 per active committer per month and needs GitHub Team or Enterprise
([plans](https://github.com/security/plans)). On a forge you run — GitHub Enterprise Server,
self-managed GitLab — a `pre-receive` hook script can read the pushed objects and reject the push
([GHES pre-receive hooks](https://docs.github.com/en/enterprise-server@3.17/admin/enforcing-policies/enforcing-policy-with-pre-receive-hooks/creating-a-pre-receive-hook-script)).
Two host features look similar but read no file contents: GitLab push rules (Premium, Ultimate)
match commit messages, branch names, emails and a list of secret file names
([push rules](https://docs.gitlab.com/user/project/repository/push_rules/)), and GitHub rulesets
"can also control commit metadata, such as commit messages and author email addresses"
([rulesets](https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/managing-rulesets/about-rulesets)).
[GitGuardian](https://docs.gitguardian.com/secrets-detection/customize-detection/detector-settings)
custom detectors are "only available for workspaces under our Business plan": you submit a
regular expression for their team to validate, and "requests for detecting patterns like Personal
Identifiable Information (PII) … will be rejected" — which likely rules out people's names.

**Best pick when** the repo belongs to an organization that already pays for Secret Protection,
or lives on a forge whose server hooks you control.

**Cost.** The plan, or running the forge. Anyone with write access can bypass GitHub push
protection with a reason, by default, and a push to a personal fork is outside the org's
protection. Push protection covers pushes; pull request and issue text is scanned for alerts after
it is posted. The host holds the list in plaintext.

**Lifecycle.** Organization settings or a server hook. Status: sourced. Dropped on fit for us: a
personal-account repository on GitHub.com cannot get either.

### 5. A CI scan with the list held as a secret

**How it works.** The list is a repository secret; a job reads it and fails on any hit, printing
only file paths. GitLab Ultimate can hold the rules in a separate private project as a remote ruleset
([customize](https://docs.gitlab.com/user/application_security/secret_detection/pipeline/configure/)).

**Best pick when** you have no push block, few or no fork pull requests, and want a required
check nobody skips from a laptop.

**Cost.** One job per pull request. It runs after the push, so the string is already on the
server. Log masking: "Never use structured data as a secret", because redaction "largely relies
on finding an exact match" ([secure use](https://docs.github.com/en/actions/reference/security/secure-use));
a multi-line list is not named there but has the same problem, so never print it. Fork pull
requests: "secrets are not passed to the runner when a workflow is triggered from a forked
repository" ([events](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows)),
so the job must fail or say it skipped — not pass. `pull_request_target` would hand forks the
secret, but runs in the base repository's context, so any step that touches the fork's code can
read the list ([pull_request_target](https://docs.github.com/en/actions/reference/security/securely-using-pull_request_target)).
And the secret is readable "by any workflow that runs with secrets", as one project put it when
rejecting this design ([prism#476](https://github.com/sandydargoport/prism/pull/476)).

**Lifecycle.** A workflow, a script and a secret someone must set and keep current. Status:
tried — this repo; it runs, but no planted hit has yet shown it catches anything.

### 6. A hashed list in the repo

**How it works.** Commit hashes of the strings, not the strings; hash every token of the change
and compare. prism#476 stores "HMAC-SHA256 of each entry, keyed by `PRISM_PII_SALT`" — a secret
key, so a guess cannot be tested, but again no fork coverage.
[agentic-org#1310](https://github.com/mishrasanjeev/agentic-org/pull/1310) commits the salt: the
hashes "are not secret: the salt is committed, so anyone can test a guessed name" — the trade for
a check that runs anywhere, forks and laptops included. #1310 also scans commit messages, the
branch name and the pull request text, and normalises tokens so that `AcmeVerify` and
`ACME-VERIFY` match. [Purview Exact Data Match](https://learn.microsoft.com/en-us/purview/sit-learn-about-exact-data-match-based-sits)
is the same idea as a Microsoft 365 service: a salted hash of a data table, uploaded.

**Best pick when** outside contributors open pull requests, or the check must run the same
locally and in CI.

**Cost.** For a repository, a custom script: we found no packaged tool. #1310 also checks "every
tail of a word", but a term at the start or middle of a longer word is missed. A committed salt
lets anyone confirm a guess.

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

### 8. Detection beyond exact strings

**How it works.** A model finds person names and emails you never listed.
[Presidio](https://data-privacy-stack.github.io/presidio/) does this, and also takes a
`deny_list`; its default configuration ignores organisations ("Has many false positives"). For
variants of names you did list: case folding (Vale's `reject.txt` with `(?i)`), or fuzzy matching
that allows k errors against a pattern file ([agrep](https://github.com/Wikinaut/agrep) `-#`
with `-f`).

**Best pick when** the risk includes names not on any list, or typos of names on it, and a person
reads what it flags.

**Cost.** False positives in code and prose; and "there is no guarantee that Presidio will find
all sensitive information." Client and employer names need the organisation label turned back on.

**Lifecycle.** A service or library plus tuning. Status: sourced.

### 9. Rewrite history after a leak

**How it works.** [git-filter-repo](https://github.com/newren/git-filter-repo) `--replace-text`
(and `--replace-message` for commit messages) or the [BFG](https://rtyley.github.io/bfg-repo-cleaner/)
replaces listed strings across history, then you force-push. The BFG leaves the latest commit
alone by default: fix it by hand first.

**Best pick when** a string already landed. It is the last step, not a check.

**Cost.** Every clone must re-clone. On GitHub the data stays reachable "In any clones or forks of
your repository", by SHA in cached views and "Through any pull requests that reference them"; in
forks it "will continue to be accessible there"
([removing sensitive data](https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/removing-sensitive-data-from-a-repository)).

**Lifecycle.** One-off. Status: sourced.

### 10. Placeholders in files, real strings only on your machine

**How it works.** A git clean filter swaps each real string for a placeholder when a file is
staged, and a smudge filter restores it on checkout, so committed blobs never hold it
([clean/smudge](https://developers.redhat.com/articles/2022/02/02/protect-secrets-git-cleansmudge-filter)).

**Best pick when** a fixed set of strings must stay in files you edit locally (a config, a notes
file) but never be committed.

**Cost.** The filter and its list live on each machine; a clone without them commits the real
string. It covers file contents only, not messages or pull request text.

**Lifecycle.** `.gitattributes` plus local git config. Status: sourced.

Also considered and dropped on merit: detect-secrets' `--word-list`, which is an allowlist — "if a
secret contains a word in the list we ignore it" — not a denylist; and
[git-crypt](https://www.agwa.name/projects/git-crypt/) for the list file, since every job that
checks needs the key, which is option 5's secret with more steps.

## How to choose

First, in order:

1. **Is the writer an agent that can do the public work without reading the private material?**
   Yes → 1, then continue: everything below catches what 1 lets through. A person writing, or an
   agent whose instructions carry the private context → 1 does not apply.
2. **Is the public repo exported from a private one, with no changes made on the public side?**
   Yes → 7. If the public side also takes contributions, 7 needs a reverse flow; answer no.
3. **Can your host refuse a push by its contents?** An organization with GitHub Secret Protection,
   or a forge whose server hooks you run → 4. GitLab push rules and GitHub rulesets check commit
   messages only; add them to another option.
4. **Do people open pull requests from forks?** Yes → 6 with a committed salt; 5 cannot check
   them.

| Option | Fits only if |
|---|---|
| 1 | the writer is an agent, the private material is at paths you can deny, and the task does not read them |
| 2 | combined with a mechanical check |
| 3 | every place that writes — each laptop, or the agent's harness — has the hook and the list |
| 4 | your host reads push contents: GitHub Secret Protection, or server hooks you run |
| 5 | fork pull requests are absent or may stay red; the string is public before it runs |
| 6 | someone writes and maintains the tokeniser; salt committed (guesses testable) or keyed (no forks) |
| 7 | changes flow one way, private to public |
| 8 | names outside the list are a risk, and a person reviews every flag |
| 9 | something already leaked |
| 10 | the strings belong in files on your machine, and every clone gets the filter |

Among what is left:

- **Before anyone can fetch it:** 7 and a server hook (4). GitHub push protection too, but anyone
  with write access can bypass it by default, and a push to a personal fork is outside it.
- **Before it leaves the machine:** 3 and 10, which a writer can skip or never install.
- **After the push, as a required check:** 5 and 6 — not skippable from a laptop, but 5 does not
  check forks.
- **Beyond file contents:** #1310's version of 6 scans commit messages and pull request text; 3 as
  a harness hook sees pull request text before it is posted. The rest check files.

If no mechanical option fits, take 3 on the machines you do control, with 2. If even that is out,
2 is advice with nothing behind it: say in the repo that nothing checks.

**At more than one developer.** A local hook (3, 10) needs installing on every machine, and one
person without it is the gap. The list has an owner who adds to it; everyone else only needs the
check to fail. With a list held as a secret (5), contributors cannot run the check before pushing,
so pair it with 6 or give them a local copy. The more people, the more the list itself is the
risk: prefer hashes (6) or a pipeline (7) over plaintext — and note that 4 also stores the
plaintext, with the host, and with GitGuardian its staff read the pattern.

**Examples.** An organization on GitHub Team with Secret Protection, agents working without
private material: 4 with the list as custom patterns, plus 1 — this points away from our choice.
An open-source mirror of an internal codebase: 7 with Copybara, with 9 as the fallback. A project
that takes fork pull requests: 6 with a committed salt, as agentic-org#1310 chose.

**Our own choice.** A public repository on a personal account, written mostly by an agent. Step 1:
no — the agent's global instructions load a private repository into its context. Step 2: no, the
public repo is written directly. Step 3: no, a personal account on GitHub.com. Step 4: no fork
pull requests so far. That leaves 3, 5, 6 and 10; we took 2 plus 5:
[`gitleaks.yml`](../.github/workflows/gitleaks.yml) runs gitleaks and then the scrub, which was
one step in a job that already ran. Not taken yet: a harness hook (3), which would check pull
request text before it is posted and close the second gap below; 6, which would too, after the
push. Gaps:

- The secret was never set. All 32 scrub runs before the fix, from pull request #34 on 2026-09-16
  to 2026-09-17, logged "Scrub clean" and were green, having checked nothing
  ([`scrub-without-literals-reported-clean.md`](../examples/scrub-without-literals-reported-clean.md)).
  The script now fails on an empty list, which means every pull request — forks included — fails
  until someone sets it.
- It runs after the push, on file contents only — the checkout and plaintext files under `.git`,
  not packed history, commit messages, or pull request and issue text — and on exact strings.

The script and how to tell it ran: [`personal-literal-scrub.md`](../resources/personal-literal-scrub.md).
