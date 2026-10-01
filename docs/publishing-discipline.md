---
applies_when: an agent drafts changes in your repo — code, docs, a pull request — and before they merge, publish, or get marked "reviewed" you need something that shows a person actually read them, not just that a button was pressed
applies_when_not: people write and review every change with no agent drafting — ordinary required reviews cover that; which checks must pass before a merge is a separate question (merge gates, Osasuwu/jarvis-oss#44); keeping private strings out of a public repo is docs/private-literal-scrub.md
---

# Proving a person read what an agent drafted

## The problem

An agent can draft a change, open the pull request, and — if it runs with your account — approve,
label, sign and merge it too. Every review record your forge keeps can then be produced by the
thing under review. The question is not "is there a review step" but "could the agent have
completed it alone". What goes wrong:

- **Self-signed** — the agent that drafted a change also writes the approval, sign-off line or
  "reviewed" date.
- **Mechanically satisfied, substantively empty** — a check passes because its shape is right
  (two commits, a date, a label removed), while nobody read anything.
- **Shared identity** — the agent uses the person's token, SSH key or unlocked signing key, so the
  forge cannot tell their actions apart.
- **Removable hold** — whoever holds the credential can clear the hold, or bypass it as an admin.
- **Stale approval** — a change is approved, then edited, and the approval still counts.
- **Rubber stamp** — a real person approves without reading, or nobody does: one study found
  about 80% of AI-co-authored pull requests from non-owners merged with no explicit review
  ([arXiv 2601.13754](https://arxiv.org/abs/2601.13754)).
- **Nobody else** — one developer has no second person to approve.

Each option is marked **tried** (we run or ran it; the example says where) or **sourced** (read
from the tool's documentation). Quotes were checked against the linked pages by review runs on the pull requests that added
them, and by the quote-check CI on later changes.

## The options

### 1. The merge click alone

**How it works.** Whoever merges is taken to have read the change. Nothing else is recorded.

**Best pick when** a person makes every change and every merge by hand, and no agent holds a
credential that can merge.

**Cost.** Every failure mode above once an agent can merge. The record shows the account, not who
was at the keyboard.

**Lifecycle.** Nothing to set up. Status: tried — our pull requests
[#33](https://github.com/Osasuwu/jarvis-oss/pull/33),
[#35](https://github.com/Osasuwu/jarvis-oss/pull/35) and
[#50](https://github.com/Osasuwu/jarvis-oss/pull/50) merged with zero reviews, #50 three minutes
after opening; see [`self-signed-signoff-pr-50.md`](../examples/self-signed-signoff-pr-50.md).

### 2. Required approval from another account

**How it works.** The forge blocks the merge until someone other than the author approves.
GitHub: "Pull request authors cannot approve their own pull requests"
([required reviews](https://docs.github.com/en/pull-requests/collaborating-with-pull-requests/reviewing-changes-in-pull-requests/approving-a-pull-request-with-required-reviews)).
Variants close the gaps: "require that the most recent reviewable push must be approved by someone
other than the person who pushed it" and "Dismiss stale pull request approvals when new commits
are pushed"
([protected branches](https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/managing-protected-branches/about-protected-branches));
code owners route the review to named people with write access
([CODEOWNERS](https://docs.github.com/en/repositories/managing-your-repositorys-settings-and-features/customizing-your-repository/about-code-owners)).
Elsewhere: Gerrit `submittableIf = label:Code-Review=MAX,user=non_uploader`
([submit requirements](https://gerrit-review.googlesource.com/Documentation/config-submit-requirements.html));
GitLab "Prevent approvals by users who add commits"
([approval settings](https://docs.gitlab.com/user/project/merge_requests/approvals/settings/),
Premium); Azure DevOps "Prohibit the most recent pusher from approving their own changes"
([branch policies](https://learn.microsoft.com/en-us/azure/devops/repos/git/branch-policies)).
Kubernetes' Prow bars the author from `/lgtm`; its `implicit_self_approve` setting, off by
default, lets an approver in `OWNERS` approve their own pull request ([owners](https://github.com/kubernetes/community/blob/master/contributors/guide/owners.md)).

**Best pick when** there are two people, or the agent pushes as its own identity (option 3).

**Cost.** Needs a second account that the agent cannot use. The required-reviews page above adds
that "Repository owners and administrators can merge a pull request even if it hasn't received an
approving review"; the protected-branches page lets you "optionally apply the restrictions to
administrators"; a [ruleset](https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/managing-rulesets/about-rulesets)
instead names who may bypass — "users with a certain role, such as repository administrator, or …
specific teams or GitHub Apps". The protected-branches page linked above says of its own bypass
lists that "Actors may only be added to bypass lists when the repository belongs to an
organization"; the rulesets page states no such condition. On GitHub, protected branches on private
repos need a paid plan; on GitLab the author and committer rules are Premium. If Copilot code
review may approve, its approval counts toward required approvals
([changelog, 2026-09-01](https://github.blog/changelog/2026-09-01-copilot-code-review-can-now-approve-pull-requests/)),
so "another account" can be a bot — leave that off. Approval proves a click, not reading.

**Lifecycle.** Branch protection or ruleset setting; remove it to undo. Status: sourced; dropped
*on fit* by us — one account both authors and would approve.

### 3. Give the agent its own identity

**How it works.** The agent opens and pushes pull requests as a separate account, so the person is
no longer the author and may approve under option 2. Choices: a GitHub App ("not tied to
a user account and do not consume a seat",
[GitHub Apps](https://docs.github.com/en/apps/creating-github-apps/about-creating-github-apps/deciding-when-to-build-a-github-app));
a machine user
([account types](https://docs.github.com/en/get-started/learning-about-github/types-of-github-accounts));
or a hosted agent that already runs as one — Copilot cloud agent "cannot approve or merge a pull
request" and GitHub "Prevents the user who asked Copilot cloud agent to create a pull request from
approving it"
([risks and mitigations](https://docs.github.com/en/copilot/concepts/agents/cloud-agent/risks-and-mitigations)).
[Claude Code Action](https://github.com/anthropics/claude-code-action) runs as an app, but by
default the person opens the pull request, making them its author again.

A weaker variant keeps the agent on your machine and takes the merge away from it: a harness deny
rule on `gh pr merge` and `gh pr review --approve` (Claude Code's `Bash(...)` rules, such as
`Bash(git push *)`, [match a command's written form](https://code.claude.com/docs/en/permissions),
so the same command written another way can pass), or a pre-call hook
([`agent-safety-hooks.md`](agent-safety-hooks.md), option 4). The agent still holds a token that
merges through any command the rule does not name, so this is a hold (4), not proof.

**Best pick when** you are one developer who wants the merge click to be yours: a hosted agent
that cannot merge, with you merging by hand. Add option 2 only if you are allowed to approve
— Copilot bars the person who asked it, so for one developer 2 with Copilot never merges.

**Cost.** The separation holds only while the agent never has your token, SSH key or signing key;
in your terminal it inherits your `gh` login and git credentials unless you remove them. Leave "Allow GitHub Actions to create and approve
pull requests" off — the default for a repository on a personal account; an organization's
repositories inherit the organization's setting
([Actions settings](https://docs.github.com/en/repositories/managing-your-repositorys-settings-and-features/enabling-features-for-your-repository/managing-github-actions-settings-for-a-repository)).
One more identity to create, scope and rotate.

**Lifecycle.** Create the app or account, install it, move the agent's token; uninstall to undo.
Status: sourced.

### 4. A hold a person clears

**How it works.** The pull request starts blocked, and a person unblocks it after reading. "Draft
pull requests cannot be merged"
([about pull requests](https://docs.github.com/en/pull-requests/collaborating-with-pull-requests/proposing-changes-to-your-work-with-pull-requests/about-pull-requests)).
A label plus a required status check fails while the label is on
([required-labels action](https://github.com/mheap/github-action-required-labels): "fail the build
if/unless a certain combination of labels are applied").

**Best pick when** you have one account and want a pause the agent is told not to clear. Drafts
need no repository settings; the label variant needs required checks, which private repos on
GitHub Free lack.

**Cost.** Anyone with write access can mark ready or remove a label — an agent holding your token
included — and the event log shows the account, not the person. It records intent, not reading.

**Lifecycle.** A workflow file plus a required check, or a habit (drafts). Status: tried — our
`waiting-human-review` check; see
[`review-hold-cleared-by-same-account.md`](../examples/review-hold-cleared-by-same-account.md).
Drafts as the hold: sourced.

**Install (ours).** [`waiting-human-review.yml`](../.github/workflows/waiting-human-review.yml):
on a freshly opened or ready-for-review pull request with no reviewer requested, it adds a
`waiting-human-review` label and fails; on every later event it fails while the label is on or a
review request is pending. Nothing removes the label automatically. Cost: one Actions job per
pull request event, one label clear per pull request. To adopt: copy the workflow, create the
label, and add `waiting-human-review` to the default branch's required checks — unrequired, a red
run blocks nothing.

#### Check

A new pull request gets the label within a minute and the `waiting-human-review` check is red
with "Human review is owed". Clear the label and the check re-runs green on `unlabeled`. No label:
the workflow is not installed or lacks `issues: write`. Red check but the merge button works: the
check is not required, or you are an administrator and protection does not apply to you.

### 5. An approval step the agent cannot perform

**How it works.** Approval needs something only the person has at that moment. A signature from a
hardware key with touch required, checked in CI: the key releases a result only "if the correct
user PIN is provided and the YubiKey touch sensor is triggered"
([Yubico](https://developers.yubico.com/PGP/Card_edit.html)). GitLab's "Require user
re-authentication (password or SAML) to approve" (Premium). A deployment environment with required reviewers and
"**Prevent self-review**"
([environments](https://docs.github.com/en/actions/how-tos/deploy/configure-and-manage-deployments/manage-environments)),
which gates a job, not the merge — for one developer it leaves nobody to approve, and without it
your own token approves over the API. Keyless signing with
[gitsign](https://github.com/sigstore/gitsign) ties a signature to an OIDC login instead of a key,
but its cache means "you only need to auth once every 10 minutes" — inside that window an agent
can sign. For a package, npm's `npm stage publish` can run from CI with OIDC, while approving the
staged version needs "proof of presence", at the CLI or npmjs.com
([trusted publishers](https://docs.npmjs.com/trusted-publishers/)); that step needs a person and the
agent cannot perform it. npm's pages state no plan or price for it, so check yours.

**Best pick when** the agent must run with your account and you need proof, not intent.

**Cost.** A CI check you write, asserting that HEAD carries a tag signed by key X, which blocks
only where required checks exist and only while the agent's token cannot administer the repo or
merge around it; hardware — a software key the agent can reach proves nothing; a touch per
sign-off. Below Enterprise, required reviewers are "only available for public repositories"
([deployments and environments](https://docs.github.com/en/actions/reference/workflows-and-actions/deployments-and-environments)).
Still proves presence, not reading.

**Lifecycle.** Enrol the key, add the check; remove the check to undo. Status: sourced.

### 6. A review record in the tree

**How it works.** A file or commit says who reviewed what, when. Variants: a reviewed date in the
doc's frontmatter, as Google's "freshness dates" that "note the last time a document was reviewed"
([Software Engineering at Google, ch. 10](https://abseil.io/resources/swe-book/html/ch10.html))
or Microsoft Learn's `ms.date`
([metadata](https://learn.microsoft.com/en-us/contribute/content/metadata)); a ledger file whose
entry must land in a different commit from the doc body; commit trailers such as `Reviewed-by:`
([kernel](https://docs.kernel.org/process/submitting-patches.html)) or `Signed-off-by:` checked by
the [DCO app](https://github.com/dcoapp/app); [git notes](https://git-scm.com/docs/git-notes).

**Best pick when** you need staleness tracking or a readable history of sign-off, on top of an
option that proves who acted.

**Cost.** Anyone who can commit can write the record. The kernel says "AI agents MUST NOT add
Signed-off-by tags" and asks for `Assisted-by:` instead
([coding assistants](https://docs.kernel.org/process/coding-assistants.html)) — a policy, not a
barrier. A rule like "separate commit" is met by splitting commits, not by reading.

**Lifecycle.** A file convention and a check. Status: tried — our frontmatter date plus a
ledger file, gamed twice, then removed ([#217](https://github.com/Osasuwu/jarvis-oss/issues/217)); see
[`signoff-same-commit-violation.md`](../examples/signoff-same-commit-violation.md). Trailers,
freshness metadata and notes: sourced.

**Install.** A reviewed-date field in each doc's frontmatter, or a ledger file with one line per
doc, and a CI check that fails when the date is set and the matching record is missing; for
trailers, the DCO app or a check on commit messages. A rule that the record lands in its own
commit reads git history, so the CI checkout needs full history. Cost: a check to maintain, and
a follow-up pull request per doc if the record must land after the body. It does not prove that
a person, rather than an agent holding the same token, wrote the record; see
[What every option depends on](#what-every-option-depends-on).

#### Check

Set the date on a doc without adding its record; the check must go red and name the doc.

### 7. Make the reading checkable

**How it works.** Instead of asking the person to read everything, a context that did not write
the change checks facts against sources and lists what to read closely; the person reads the
change with the report. Our [`review-doc`](../.agents/skills/review-doc/SKILL.md) skill does
this. Tools that push toward evidence of reading:
[Reviewable](https://docs.reviewable.io/files.html) tracks "the reviewed state of each file, at
each revision, for each reviewer", while GitHub's own
[mark as viewed](https://github.blog/news-insights/product-news/mark-files-as-viewed/)
resets when a file changes — either way a checkbox, and whether it blocks a merge is the
repository's own condition;
[pr-quiz](https://github.com/dkamm/pr-quiz) is "A GitHub Action that uses AI to generate a quiz
from your pull request" — which an agent holding your token could also answer.

**Best pick when** changes are long and fact-heavy, and rubber-stamping is the likely failure.

**Cost.** A model run per change and a report to keep. The reviewer can be wrong. It narrows what
the person reads; it does not prove they read it, which is why the row asks for a written answer. 96% of developers do not fully trust AI
code and "only 48%" always check it
([Sonar](https://www.sonarsource.com/company/press-releases/sonar-data-reveals-critical-verification-gap-in-ai-coding/)).

**Lifecycle.** A skill or CI job. Status: tried in this repo.

**Install (ours).** [`review-doc`](../.agents/skills/review-doc/SKILL.md), run in a context that
did not write the doc; its report is a comment on the pull request. Cost: a model run per
review-doc pass. Harness: the skill lives in `.agents/skills/`; Claude Code loads `.claude/skills/`, so
copy it there first, and [`harnesses.md`](harnesses.md) lists each other harness's skills
directory, most of it unverified.

#### Check

The report is a comment on the pull request. No comment means no review ran.

### 8. Make a change cheap to undo

**How it works.** When nobody independent reads a change first, limit what it can do and make
undoing it fast. Ship behind a feature flag or a staged rollout
([Unleash](https://www.getunleash.io/) is one flag service), and keep the revert one action away:
GitHub can open a pull request that undoes a merged one
([revert](https://docs.github.com/en/pull-requests/collaborating-with-pull-requests/incorporating-changes-from-a-pull-request/reverting-a-pull-request)).
For a doc the event to bound is publishing: a preview path before the real one, or a release one
command rolls back. It proves nothing about who acted or who read; it caps the cost of a change
nobody read, and needs no second person at merge time.

**Best pick when** a run is unattended or solo, and a wrong change is recoverable (a doc, a page, a
package version you can yank). Not for a leaked secret, which a revert does not undo.

**Cost.** A flag or preview step to run and a revert someone or something performs; a wrong change
is live until it is noticed.

**Lifecycle.** Config in the publish path. Status: sourced.

**Install.** A preview or flag step in the publish job, and the revert named in your runbook.

#### Check

Revert one test change and time it: the previous version must be back within the time you chose.

## What every option depends on

An approval, label, trailer or signature proves only what its credential proves. If the agent can
use the credential that produces it, the record shows intent at best. Separation-of-duties rules
say the same: NIST's dual authorization means "two qualified individuals approve and implement"
([CM-5(4)](https://csf.tools/reference/nist-sp-800-53/r5/cm/cm-5/cm-5-4/)), and SLSA's source
Level 4 wants "two trusted persons to review all changes"
([SLSA](https://slsa.dev/spec/v1.2/source-requirements)).

## How to choose

These are layers: one to prove who acted (2, 3 or 5), one to hold (4), and optionally a record (6),
a cheaper read (7), and where nobody independent reads, a cap on the damage (8).

First, in order:

1. **Is there a second person with write access, and can required approvals be turned on here?**
   They exist on public repos and on paid private plans; GitLab's approval rules are Premium. Both
   yes → 2, with most-recent-push approval, stale approvals dismissed and no bypass — as long as no
   agent can use the approver's credentials. Two people each running an agent as themselves do not.
2. **Can the agent reach the credentials of whoever merges?** Look at what is on the machine — the
   `gh` login, the git credential helper, a loaded `ssh-agent` or `gpg-agent` — not only the token
   the agent was given. Yes → 1, 4 and 6 record intent only; for proof you need 3 (take the
   credentials away), or 5 (require something the agent lacks) if that same token cannot administer
   the repo and switch the check off. No → the merge click is the
   person's, and 4 and 6 record who it was. If nobody is present when it merges (a scheduled
   job), the answer is Yes, and a draft (4) does not hold, since the job's own token clears it.
   Stop the job merging: 3 where a hosted agent or a branch rule can bar the account; otherwise
   take the merge step out of the job, which then opens the change and leaves it for a person
   (intent, not proof). Cap the damage with 8.
3. **What can this forge and plan block?** A private GitHub Free repo has no protected branches,
   rulesets or required checks, so drafts (4) are the only hold; state the gap. GitLab Free has
   required pipelines and can restrict merging to Maintainers, but no approval rules.

| Option | Fits only if |
|---|---|
| 1 | no agent credential can approve or merge, and no one but the merger relies on the record |
| 2 | a second account the agent cannot use exists, required approvals are available here, and bot approvals are off |
| 3 | the agent runs where your token, SSH key and signing key are absent, and its account cannot merge: a hosted agent the forge bars from merging (Copilot cloud agent, paid Copilot plans) is proof anywhere; an app in CI or a separate OS user is proof only where a protected branch or ruleset stops that account merging, so not on a private GitHub Free repo |
| 4 | drafts: any repo; a label and required check: required checks are available |
| 5 | required checks are available, the approval is a hardware-key signature (any forge), re-authentication (GitLab Premium) or an environment reviewer (GitHub public repo or Enterprise), and the agent's token cannot administer the repo; for an npm package, a staged publish only a person can approve needs no required check (its plan is not stated) |
| 6 | 2, 3 or 5 already proves who acted, or the record is stated to be intent only |
| 7 | the review runs in a context that did not write the change, and its read-closely list is answered in writing |
| 8 | a wrong change is recoverable, and the flag, preview or revert is in place before the merge |

Among what is left: for two people where required approvals are available (a public repo or a
paid plan), 2 is the proof; on a private GitHub Free repo it is out, and the team has 4, 7 and 8,
or goes public or pays. For one developer, 3 with a hosted agent that
cannot merge makes the merge click yours, and costs an identity and a paid Copilot plan; 5 needs a
hardware key and a check you maintain, and required checks, so on a private GitHub Free repo it is
not buildable, except an npm package's staged publish. 4 is free and cheap to clear, which is its weakness; 6 and 7 add history and focus,
not proof. If nothing proves who acted, say so in writing — "a hold, not proof" — and keep the hold.
On a private GitHub Free repo with one developer that is where you end: 4 with the gap stated and
8 to cap what merges unread, unless you make the repo public or pay, for a plan or for a hosted
agent that cannot merge. An unattended run cannot produce proof of reading: stop it merging and
leave the change for a person.

**At more than one developer.** The approval (2, where available) must come from someone other than the author and the
last pusher, and whoever clears a hold (4) or signs a record (6) must not be the one who
drafted — a teammate, not the drafter. Code owners route docs to people who can judge them. The
credential rule still applies: an agent shared by the team must not run as any one of them.

**Examples.** One developer whose agent runs as a GitHub App on a public repo: 3 plus 2 with
most-recent-push approval, applied to administrators — the app cannot approve its own pull request
and the person's approval is theirs, so a label adds nothing; push to the branch yourself,
though, nobody may approve. This points away from our choice. A four-person team on a paid
plan: 2 with code owners on `docs/` and stale approvals dismissed, plus 7 for long docs. One
developer whose agent must run as them locally: 5 — a touch-required signed tag checked in CI, on a
public repo or a paid plan — or 4 with the gap stated, which is all a private GitHub Free repo offers. A mailing-list project: 6's trailers, where the mailing-list reply, not the
trailer, is the proof; see
[`kernel-no-ai-signed-off-by.md`](../examples/kernel-no-ai-signed-off-by.md).

**Our own choice.** One personal account on a public repo, and the agent runs with that account's
token — so 2 is out *on fit*, and every record we can produce is intent. We use 4 (a
`waiting-human-review` label and required check) and 7 (a `review-doc` report on the pull
request). It costs a label clear per pull request. What went wrong: before the hold, #50 merged its own sign-off three minutes after opening
([`self-signed-signoff-pr-50.md`](../examples/self-signed-signoff-pr-50.md)); a ledger line
was removed and re-added seven seconds apart, pushed straight to `main`, satisfying the
separate-commit rule
([`signoff-same-commit-violation.md`](../examples/signoff-same-commit-violation.md)). Row 6 does
not hold for us — nothing proves who wrote a ledger line — so it added no check the hold lacks,
and we removed it ([#217](https://github.com/Osasuwu/jarvis-oss/issues/217)). Not taken yet: 3 (a hosted agent, with us merging) or 5 (a
hardware-signed tag), either of which closes the first gap below; 8, so a doc is live on `main`
when it merges and a person reverts it. Open gaps: the same account
can clear the label, as #70's was, with no way to tell person from agent; an administrator can switch protection off on this repo; 3 and 5 are not in place.
The hold and the report: Install and Check in options 4 and 7. The history
behind the hold is in [#53](https://github.com/Osasuwu/jarvis-oss/issues/53); the gate structure
around it is [#44](https://github.com/Osasuwu/jarvis-oss/issues/44).
