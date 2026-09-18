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
make it likelier: an agent that has read private notes will reuse what it read.
The ways it goes wrong:

- **Public before the check** — the check runs in CI, but the branch it scans is already pushed
  to a public repo; merged or not, it can be fetched.
- **The list leaks** — it is committed, printed in a log, or held somewhere readable by more jobs
  or people than the check that needs it. It is an index of what you wanted hidden.
- **Empty list, green check** — the list is missing or empty, so the scan reports clean.
- **Bypassed** — a local hook is skipped with `--no-verify` or `SKIP=`, or a push-protection
  prompt is clicked through.
- **Coverage gaps** — the check sees files but not history, commit messages, pull request or
  issue text; or it matches exact strings and the text has a variant (case, spacing, a typo).
- **Forks** — a pull request from a fork runs without the repository's secrets, so a list held
  as a secret is not there.
- **Cleanup is partial** — once public, a rewrite does not reach clones, forks or cached views,
  and a name cannot be rotated like a key.

Each option is marked **tried** (we run or ran it; the example says where — running is not proof
it catches anything) or **sourced** (from the tool's documentation or a project's pull request). Quotes
were checked against the linked pages on 2026-09-18, by a review run on the last commit of the
pull request that added this doc.

## The options

### 1. Keep private material out of the writer's reach

**How it works.** The agent cannot leak what it never read. Deny its file tools the private paths
and run it in a sandbox that cannot see them. Claude Code's `Read` deny rules block its file tools
and recognised shell commands, but "They don't apply to a command that reads files without naming
them, such as `grep -r pattern .`" — instead, "enable the sandbox"
([permissions](https://code.claude.com/docs/en/permissions)). OWASP: "Limit access to
sensitive data based on the principle of least privilege"
([LLM02:2025](https://genai.owasp.org/llmrisk/llm022025-sensitive-information-disclosure/)).

**Best pick when** the writer is an agent, the private material sits in files you can name, and
the agent does not need it for the public work.

**Cost.** Low to configure. It does not help a person, or an agent whose instructions carry
private context.

**Lifecycle.** Harness settings, per machine. Status: sourced — not in place for us.

### 2. Written rules for the writer

**How it works.** The agent's instructions list what must not be published and how to cite
private evidence. OWASP: "such restrictions may not always be honored and could be
bypassed via prompt injection or other methods."

**Best pick when** alongside any mechanical check — never alone.

**Cost.** Free. It is advice: nothing fails when it is ignored.

**Lifecycle.** Instruction files. Status: sourced. We rely on it, but our rules are private, so
there is no public trace.

### 3. A local check before the commit or the write

**How it works.** A hook reads the list from outside the repo and refuses the commit.
[git-secrets](https://github.com/awslabs/git-secrets) takes patterns from git config or from a
provider command: `git secrets --add-provider -- cat /path/to/secret/file/patterns`.
[gitleaks](https://github.com/gitleaks/gitleaks) loads its rules from `--config`,
`GITLEAKS_CONFIG` or `GITLEAKS_CONFIG_TOML` before a `.gitleaks.toml` in the repo, so the rules
can live in your home directory. A pre-call hook in the agent's harness can grep what the agent
is about to write, pull request text included, before the tool runs (option 4 of
[`agent-safety-hooks.md`](agent-safety-hooks.md)) — it lives in the harness, not in git, so
`--no-verify` does not reach it, but the agent can turn it off with `disableAllHooks` unless
the hook is set in managed settings.

**Best pick when** the string must stop before it leaves the machine, and every writer can be set
up with the hook and the list.

**Cost.** Free. Every machine and clone needs the setup and the list. Git's hooks are skipped with
`--no-verify`; gitleaks' pre-commit hook with `SKIP=gitleaks`.

**Lifecycle.** A hook plus a private list per machine. Status: sourced.

### 4. A server-side push block

**How it works.** The host refuses the push. GitHub custom patterns need Secret Protection
enabled, and then a separate push-protection opt-in per pattern, "visible for published patterns
only", which in an organization "will only apply to repositories … that have secret scanning as
push protection enabled"
([custom patterns](https://docs.github.com/en/code-security/secret-scanning/using-advanced-secret-scanning-and-push-protection-features/custom-patterns/defining-custom-patterns-for-secret-scanning)).
Secret Protection is $19 per active committer per month, on GitHub Team or Enterprise
([plans](https://github.com/security/plans)). On a forge you run — GitHub Enterprise Server,
self-managed GitLab — a `pre-receive` hook can read the pushed objects and reject the push
([GHES pre-receive hooks](https://docs.github.com/en/enterprise-server@3.17/admin/enforcing-policies/enforcing-policy-with-pre-receive-hooks/creating-a-pre-receive-hook-script)).
Two host features look similar but read no file contents: GitLab push rules (Premium, Ultimate)
match commit messages, branch names, emails and a list of secret file names
([push rules](https://docs.gitlab.com/user/project/repository/push_rules/)), and GitHub rulesets
"can also control commit metadata, such as commit messages and author email addresses"
([rulesets](https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/managing-rulesets/about-rulesets)).
[GitGuardian](https://docs.gitguardian.com/secrets-detection/customize-detection/detector-settings)
custom detectors are "only available for workspaces under our Business plan": you submit a
detector name and example matches (a regular expression is optional) for their team to validate, and "requests for detecting patterns like Personal
Identifiable Information (PII) … will be rejected" — which likely rules out people's names.

**Best pick when** the repo belongs to an organization that pays for Secret Protection, or lives
on a forge whose server hooks you control.

**Cost.** The plan, or running the forge. "Anyone with write access to the repository can bypass
push protection by specifying a bypass reason", by default. Push protection covers pushes
([push protection](https://docs.github.com/en/code-security/secret-scanning/introduction/about-push-protection));
secret scanning "also automatically scans" issue and pull request text, but that raises an alert
after the text is posted, it blocks nothing
([secret scanning](https://docs.github.com/en/code-security/secret-scanning/introduction/about-secret-scanning)).
Push protection also covers "Interactions with the GitHub MCP server (public repositories only)";
GitHub's [MCP page](https://docs.github.com/en/code-security/secret-scanning/working-with-secret-scanning-and-push-protection/working-with-push-protection-and-the-github-mcp-server)
adds private repositories covered by GitHub Advanced Security.
The host holds the list in plaintext.

**Lifecycle.** Organization settings or a server hook. Status: sourced. Dropped on fit for us: a
public personal-account repo on GitHub.com gets push protection for GitHub's own patterns, but no
custom patterns and no server hooks.

### 5. A CI scan with the list held as a secret

**How it works.** The list is a repository secret; a job reads it and fails on any hit, printing
only file paths. GitLab Ultimate can hold the rules in a separate private project (remote ruleset)
([customize](https://docs.gitlab.com/user/application_security/secret_detection/pipeline/configure/)).

**Best pick when** you have no push block, few or no fork pull requests, and want a required
check nobody skips from a laptop.

**Cost.** One job per pull request, after the push, so the string is already on the server. Log masking: "Never use structured data as a secret", because redaction "largely relies
on finding an exact match" ([secure use](https://docs.github.com/en/actions/reference/security/secure-use));
a multi-line list has the same problem, so never print it. Fork pull
requests: "secrets are not passed to the runner when a workflow is triggered from a forked
repository" ([events](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows)),
and the workflow file comes from the merge commit, so it includes the fork's edits: a fork can
edit the step to pass, it just never sees the list. A fork's run checks nothing, whatever it shows.
On GitLab a fork's merge request pipeline runs in the fork, with the fork's CI config; a project
member can instead run it in the parent project, with the fork's config and the parent's
unprotected variables, since "Merge request pipelines from forked repositories cannot access these
protected resources"
([merge request pipelines](https://docs.gitlab.com/ci/pipelines/merge_request_pipelines/#control-access-to-protected-variables-and-runners)):
the same risk as below. Under
`pull_request_target` the job gets the secret and runs in the base repository's context: safe only
if the fork's code "is only ever inspected as data and never executed"
([pull_request_target](https://docs.github.com/en/actions/reference/security/securely-using-pull_request_target)).
The same page adds that "GitHub provides a default event policy that blocks the
`pull_request_target` event in public repositories". The policy runs in evaluate mode for now;
for repositories that used the default policy before general availability it blocks the event
from 2026-11-02, and from then on the repository must allow `pull_request_target` in an event
policy. Its default checkout is the base repository's default branch, so swapping the trigger alone scans
nothing from the fork and passes. Checking out the fork's head with `actions/checkout` needs
`allow-unsafe-pr-checkout: true` (`git fetch` or `gh pr checkout` skip that check), and running
the script from it runs the fork's code with
the secret in its environment. The safe form takes the script and its config from the base branch,
checks the head out into a separate directory as data, and runs nothing from it — nor a tool that
reads config from it, such as gitleaks with the fork's `.gitleaks.toml`. Even then the log is
public: a fork can plant candidate strings and read which paths fail, testing guesses in batches.
And the secret is readable "by any workflow that runs with secrets", as
[prism#476](https://github.com/sandydargoport/prism/pull/476) put it in rejecting this design.

**Lifecycle.** A workflow, a script and a secret someone must set and keep current. Status:
tried — this repo; it runs, but no planted hit has shown it catches anything.

### 6. A hashed list in the repo

**How it works.** Commit hashes of the strings, not the strings; hash every token of the change
and compare. prism#476 stores "HMAC-SHA256 of each entry, keyed by `PRISM_PII_SALT`" — a secret
key, so a guess cannot be tested, but again no fork coverage.
[agentic-org#1310](https://github.com/mishrasanjeev/agentic-org/pull/1310) commits the salt: the
hashes "are not secret: the salt is committed, so anyone can test a guessed name" — the trade for
a check that runs anywhere, forks and laptops included. #1310 also scans commit messages, the
branch name and the pull request text, and normalises tokens so `AcmeVerify` and
`ACME-VERIFY` match. [Purview Exact Data Match](https://learn.microsoft.com/en-us/purview/sit-learn-about-exact-data-match-based-sits)
is this as a Microsoft 365 service: a salted hash of an uploaded table.

**Best pick when** outside contributors open pull requests, or the check must run the same
locally and in CI.

**Cost.** For a repository, a custom script: we found no packaged tool. #1310 also checks "every
tail of a word"; prism#476 names what both still miss — "a value buried inside a longer word with
no separator". A committed salt lets anyone confirm a guess.

**Lifecycle.** A script, the hash file and, with HMAC, a key. Status: sourced.

### 7. Publish through an export pipeline

**How it works.** Work happens in the private repo; a tool copies it to the public one and
rewrites or refuses on the way. [Copybara](https://github.com/google/copybara/blob/master/docs/reference.md)
has `core.replace`, `metadata.scrubber` for commit messages and `core.verify_match`, which
"Verifies that a RegEx matches (or not matches) the specified files" and stops the export.

**Best pick when** the public repo is derived from a private one, one way.

**Cost.** Building and running the pipeline; changes on the public side need a
reverse flow — Copybara has `git.github_pr_origin` for GitHub pull requests, and its `core.replace`
"can be automatically reversed". Like 4 it checks before anything is public, but needs no paid
plan and no cooperation from a laptop.

**Lifecycle.** Pipeline config on the private side. Status: sourced. Dropped on merit: FBShipIt —
"This project is no longer maintained."

### 8. Detection beyond exact strings

**How it works.** A model finds person names and emails you never listed.
[Presidio](https://data-privacy-stack.github.io/presidio/) does this, and also takes a
`deny_list`; its upstream [`default.yaml`](https://github.com/microsoft/presidio/blob/main/presidio-analyzer/presidio_analyzer/conf/default.yaml)
ignores organisations ("Has many false positives"). For variants of names you did list: a regular
expression per line in Vale's `reject.txt` (`[Aa]cme`), or fuzzy matching with k errors against a
pattern file ([agrep](https://github.com/Wikinaut/agrep) `-#`
with `-f`).

**Best pick when** the risk includes names not on any list, or typos of names on it, and a person
reads what it flags.

**Cost.** False positives in code and prose; "there is no guarantee that Presidio will find all
sensitive information". Client and employer names need the organisation label turned back on.

**Lifecycle.** A service or library plus tuning. Status: sourced.

### 9. Rewrite history after a leak

**How it works.** [git-filter-repo](https://github.com/newren/git-filter-repo) `--replace-text`
(and `--replace-message` for commit messages) or the [BFG](https://rtyley.github.io/bfg-repo-cleaner/)
replaces listed strings across history, then you force-push. The BFG leaves the latest commit
alone by default; fix it first.

**Best pick when** a string already landed. The last step, not a check.

**Cost.** Every clone must re-clone or be carefully cleaned up. The data stays reachable "In any clones or forks of your
repository", by SHA in cached views and "Through any pull requests that reference them"
([removing sensitive data](https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/removing-sensitive-data-from-a-repository)).
The same page says GitHub Support can "permanently remove cached views and references
to the sensitive data in pull requests on GitHub", only where the risk
"can't be mitigated by rotating affected credentials" — a name cannot be.

**Lifecycle.** One-off. Status: sourced.

### 10. Placeholders in files, real strings only on your machine

**How it works.** A git clean filter swaps each real string for a placeholder when a file is
staged, and a smudge filter restores it on checkout, so committed blobs never hold it
([clean/smudge](https://developers.redhat.com/articles/2022/02/02/protect-secrets-git-cleansmudge-filter)).

**Best pick when** a fixed set of strings must stay in files you edit locally but never be committed.

**Cost.** The filter and its list live on each machine; a clone without them commits the real
string. File contents only, not messages or pull request text.

**Lifecycle.** `.gitattributes` plus local git config. Status: sourced.

Also considered and dropped on merit: detect-secrets' `--word-list`, which is an allowlist — "if a
secret contains a word in the list we ignore it" — not a denylist; and
[git-crypt](https://www.agwa.name/projects/git-crypt/) for the list file, since every job that
checks needs the key — option 5's secret with more steps.

## How to choose

First, in order:

1. **Is the writer an agent, and does nothing it loads — instructions, imported files, the repo
   itself — carry the private strings?** Yes → 1, then continue: the rest catches what 1 lets
   through. A person writing, or an agent whose instructions carry them → 1 does not apply.
2. **Is the public repo exported from a private one?** Yes → 7. Contributions on the public side
   do not rule it out; they add a reverse import to build.
3. **Can your host refuse a push by matching your own list?** An organization with GitHub Secret
   Protection, or a forge whose server hooks you run → 4. GitLab push rules and GitHub rulesets
   match commit metadata, branch names and filenames, not file contents; GitLab secret push
   protection and GitHub's default patterns take no list of yours — add either alongside another.
4. **Do people open pull requests from forks?** Yes → 6 with a committed salt. 5 reaches them only
   through a data-only `pull_request_target` job; otherwise a fork's run checks nothing, since the
   workflow comes from the merge commit, which includes the fork's edits.

| Option | Fits only if |
|---|---|
| 1 | the writer is an agent, the private material is at paths you can deny, and nothing the agent loads carries the strings |
| 2 | combined with a mechanical check |
| 3 | every writer who holds the private strings — each laptop, or the agent's harness — has the hook and the list |
| 4 | your host reads push contents: GitHub Secret Protection, or server hooks you run |
| 5 | fork pull requests are absent, or treated as unchecked whatever their run shows, or get a data-only `pull_request_target` job allowed by an event policy. That job's public log lets a fork test guesses. The string is public before the scan runs |
| 6 | someone writes and maintains the tokeniser; salt committed (guesses testable) or keyed (no forks) |
| 7 | changes flow private to public, and you build the reverse import for what the public side takes |
| 8 | you cannot enumerate the names in advance, and a person reviews every flag |
| 9 | something already leaked |
| 10 | the strings belong in files on your machine, and every clone that holds them gets the filter |

Among what is left:

- **Before anyone can fetch it:** 7 and a server hook (4). GitHub push protection too, but anyone
  with write access can bypass it by default.
- **Before it leaves the machine:** 3 and 10. A git hook is skipped with `--no-verify`, a harness
  hook with `disableAllHooks` outside managed settings; and the harness hook covers only what
  that agent writes.
- **After the push, as a required check:** 5 and 6 — not skippable from a laptop, but 5 reaches
  forks only through a data-only `pull_request_target` job, and its public log lets a fork test
  guesses.
- **Beyond file contents:** 6 (both #1310 and prism#476) and a harness hook (3) cover commit
  messages and pull request text; a server hook (4), push rules and 7's `metadata.scrubber` see
  commit messages; 9 rewrites them. The rest check files.

If no mechanical option fits, take 3 on the machines you control, with 2 — against row 3, so
whoever writes from anywhere else stays uncovered. If even that is out,
2 is advice with nothing behind it: say in the repo that nothing checks.

**At more than one developer.** A local hook (3, 10) needs installing on every machine, and one
person without it is the gap. The list has an owner; everyone else only needs the check to fail. With a list held as a secret (5), contributors cannot run the check before pushing,
so pair it with 6 or give them a local copy. The more people, the more the list itself is the
risk: prefer hashes (6) or a pipeline (7) over plaintext — 4 also stores plaintext, with the host,
and GitGuardian's staff read the pattern.

**Examples.** An organization on GitHub Team with Secret Protection, agents working without
private material: 4 with the list as custom patterns, plus 1 — this points away from our choice.
An open-source mirror of an internal codebase: 7 with Copybara, 9 as the fallback. A project that
takes fork pull requests: 6 with a committed salt, as agentic-org#1310 chose.

**Our own choice.** A public repository on a personal account, written mostly by an agent. Step 1:
no — the agent's global instructions load a private repository into its context. Step 2: no, the
public repo is written directly. Step 3: no, a personal account on GitHub.com. Step 4: no fork
pull requests so far. That leaves 3, 5 and 6 — 10's row does not hold, because our private strings
live in no file here. We took 2 plus 5: [`gitleaks.yml`](../.github/workflows/gitleaks.yml) runs
gitleaks then the scrub, both added in the same pull request (#34). Not taken yet: a harness
hook (3), which would check pull request text before it is posted and close the second gap below;
6 would too, after the push. Gaps:

- The secret was unset until 2026-09-17. All 33 scrub runs before then, from #34 on 2026-09-16,
  logged "Scrub clean" and were green, checking nothing
  ([`scrub-without-literals-reported-clean.md`](../examples/scrub-without-literals-reported-clean.md)).
  The script now fails on an empty list. Fork pull requests never get the secret, and the workflow
  comes from the merge commit, which includes their edits, so a green run from a fork checked nothing.
- It runs after the push, on file contents only — the checkout and plaintext files under `.git`,
  not packed history, commit messages, or pull request and issue text — and on exact strings.

The script and how to tell it ran: [`personal-literal-scrub.md`](../resources/personal-literal-scrub.md).
