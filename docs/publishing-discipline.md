---
applies_when: an agent drafts changes in your repo — code, docs, a pull request — and before they merge, publish, or get marked "reviewed" you need something that shows a person actually read them, not just that a button was pressed
applies_when_not: people write and review every change with no agent drafting — ordinary required reviews cover that; which checks must pass before a merge is a separate question (merge gates, Osasuwu/jarvis-oss#44); keeping private strings out of a public repo is docs/private-literal-scrub.md
signed_off:
---

# Proving a person read what an agent drafted

## The problem

An agent can draft a change, open the pull request, and — if it runs with your account — approve,
label, sign and merge it too. Every record of review your forge keeps can then be produced by the
thing under review. The question is not "is there a review step" but "could the agent have
completed it alone". What goes wrong:

- **Self-signed** — the agent that drafted a change also writes the approval, sign-off line or
  "reviewed" date.
- **Mechanically satisfied, substantively empty** — a check passes because its shape is right
  (two commits, a date, a label removed), while nobody read anything.
- **Shared identity** — the agent uses the person's token, SSH key or unlocked signing key, so the
  forge cannot tell their actions apart.
- **Removable hold** — the hold can be cleared, or bypassed by an admin, by whoever holds the
  credential.
- **Stale approval** — a change is approved, then edited, and the approval still counts.
- **Rubber stamp** — a real person approves without reading, or nobody does: one study found
  about 80% of AI-co-authored pull requests from non-owners merged with no explicit review
  ([arXiv 2601.13754](https://arxiv.org/abs/2601.13754)).
- **Nobody else** — one developer has no second person to approve.

Each option is marked **tried** (we run or ran it; the example says where) or **sourced** (read
from the tool's documentation). Quotes were checked against the linked pages on 2026-09-17, in
the review on the pull request that added this doc.

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
Kubernetes' Prow bars the author from `/lgtm`, but approvers in `OWNERS` can approve their own
pull request ([owners](https://github.com/kubernetes/community/blob/master/contributors/guide/owners.md)).

**Best pick when** there are two people, or the agent pushes as its own identity (option 3).

**Cost.** Needs a second account that the agent cannot use. "Repository owners and administrators
can merge a pull request even if it hasn't received an approving review" unless the rule is
applied to administrators; a [ruleset](https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/managing-rulesets/about-rulesets)
names exactly who may bypass, and the list can be empty. On GitHub, protected branches on private
repos need a paid plan; on GitLab the author and committer rules are Premium. If Copilot code
review is allowed to approve, its approval counts toward required approvals
([changelog, 2026-09-01](https://github.blog/changelog/2026-09-01-copilot-code-review-can-now-approve-pull-requests/)),
so "another account" can be a bot — leave that off. Approval proves a click, not reading.

**Lifecycle.** Branch protection or ruleset setting; remove it to undo. Status: sourced; dropped
*on fit* by us — one account both authors and would approve.

### 3. Give the agent its own identity

**How it works.** The agent opens and pushes pull requests as a separate account, so the person is
no longer the author and their approval counts under option 2. Choices: a GitHub App ("not tied to
a user account and do not consume a seat",
[GitHub Apps](https://docs.github.com/en/apps/creating-github-apps/about-creating-github-apps/deciding-when-to-build-a-github-app));
a machine user
([account types](https://docs.github.com/en/get-started/learning-about-github/types-of-github-accounts));
or a hosted agent that already runs as one — Copilot cloud agent "cannot approve or merge a pull
request" and GitHub "Prevents the user who asked Copilot cloud agent to create a pull request from
approving it"
([risks and mitigations](https://docs.github.com/en/copilot/concepts/agents/cloud-agent/risks-and-mitigations)).
[Claude Code Action](https://github.com/anthropics/claude-code-action) runs as an app, but by
default the person opens the pull request, which makes them its author again.

A weaker variant keeps the agent on your machine and takes the merge away from it: a harness deny
rule or pre-call hook on `gh pr merge` and `gh pr review --approve`
([`agent-safety-hooks.md`](agent-safety-hooks.md)). The agent still holds a token that can merge
through any command the rule does not name, so this is a hold (4), not proof.

**Best pick when** you are one developer who wants to know that the merge click was yours: a hosted
agent that cannot merge, with you merging by hand. Add option 2 only if you are allowed to approve
— Copilot bars the person who asked it, so for one developer 2 with Copilot never merges.

**Cost.** The separation holds only while the agent never has your token, SSH key or signing key.
An agent running in your terminal as you inherits your `gh` login and git credentials unless you
remove them. Leave "Allow GitHub Actions to create and approve
pull requests" off, as it is by default
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
work on any plan; a label with a required check needs required checks, which private repos on
GitHub Free lack.

**Cost.** Anyone with write access can mark ready or remove a label — including an agent holding
your token — and the event log shows the account, not the person. It records intent, not reading.

**Lifecycle.** A workflow file plus a required check, or a habit (drafts). Status: tried — our
`waiting-human-review` check; see
[`review-hold-cleared-by-same-account.md`](../examples/review-hold-cleared-by-same-account.md).
Drafts as the hold: sourced.

### 5. An approval step the agent cannot perform

**How it works.** Approval needs something only the person has at that moment. A signature from a
hardware key with touch required, checked in CI: the key releases a result only "if the correct
user PIN is provided and the YubiKey touch sensor is triggered"
([Yubico](https://developers.yubico.com/PGP/Card_edit.html)). GitLab's "Require user
re-authentication to approve" (Premium). A deployment environment with required reviewers and
"**Prevent self-review**"
([environments](https://docs.github.com/en/actions/how-tos/deploy/configure-and-manage-deployments/manage-environments)),
which gates a job rather than the merge. Keyless signing with
[gitsign](https://github.com/sigstore/gitsign) ties a signature to an OIDC login instead of a key,
but with its credential cache "you only need to auth once every 10 minutes" — inside that window an
agent can sign.

**Best pick when** the agent must run with your account and you need proof, not intent.

**Cost.** A CI check you write (for example, "HEAD carries a tag signed by key X"), which blocks
only where required checks exist; hardware — a software key the agent can reach proves nothing; a
touch per sign-off. Environments: "If you are on a GitHub Free, GitHub Pro, or GitHub Team plan,
other deployment protection rules, such as a wait timer or required reviewers, are only available
for public repositories." Still proves presence, not
reading.

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

**Lifecycle.** A file convention and a check. Status: tried — our frontmatter date plus
[`SIGNOFF.md`](SIGNOFF.md) ledger, gamed twice; see
[`signoff-same-commit-violation.md`](../examples/signoff-same-commit-violation.md). Trailers,
freshness metadata and notes: sourced.

### 7. Make the reading checkable

**How it works.** Instead of asking the person to read everything, a context that did not write
the change checks facts against sources and lists what to read closely; the sign-off records that
report. Our [`review-doc`](../.agents/skills/review-doc/SKILL.md) skill does this, and a ledger
entry names it (`facts: <report URL>`). Two tools push toward evidence of reading:
[Reviewable](https://docs.reviewable.io/files.html) tracks "the reviewed state of each file, at
each revision, for each reviewer", and its completion condition can require it before merge;
[pr-quiz](https://github.com/dkamm/pr-quiz) is "A GitHub Action that uses AI to generate a quiz
from your pull request" — which an agent holding your token could also answer.

**Best pick when** changes are long and fact-heavy, and rubber-stamping is the likely failure.

**Cost.** Adds a model run per change and a report to keep. The reviewer can be wrong. It narrows
what the person reads; it does not prove they read it. Evidence that review often does not happen:
about 80% of AI-co-authored pull requests from non-owners "merged without any explicit review"
([arXiv 2601.13754](https://arxiv.org/abs/2601.13754)); 96% of developers do not fully trust AI
code, "only 48%" always check it
([Sonar](https://www.sonarsource.com/company/press-releases/sonar-data-reveals-critical-verification-gap-in-ai-coding/)).

**Lifecycle.** A skill or CI job. Status: tried in this repo.

## What every option depends on

An approval, label, trailer or signature proves only what its credential proves. If the agent can
use the credential that produces it, the record shows intent at best. Separation-of-duties rules
say the same: NIST's dual authorization means "two qualified individuals approve and implement"
([CM-5(4)](https://csf.tools/reference/nist-sp-800-53/r5/cm/cm-5/cm-5-4/)), and SLSA's source
Level 4 wants "two trusted persons to review all changes"
([SLSA](https://slsa.dev/spec/v1.2/source-requirements)).

## How to choose

These are layers: one to prove who acted (2, 3 or 5), one to hold (4), and optionally a record (6)
and a cheaper read (7).

First, in order:

1. **Is there a second person with write access?** Yes → 2, with most-recent-push approval, stale
   approvals dismissed and no bypass. It proves who approved as long as the agent cannot use the
   approver's credentials, even if it runs on the drafter's.
2. **Can the agent use the credentials of whoever merges?** Check which token its `gh`, git remote
   and signing key use. Yes → 1, 4 and 6 record intent only; for proof you need 3 (take the
   credentials away) or 5 (require something the agent lacks).
3. **Can a check or approval be required on this repo?** Not on a private GitHub Free repo, and not
   GitLab's approval rules below Premium. No → drafts (4) are the only hold; state the gap.

| Option | Fits only if |
|---|---|
| 1 | no agent credential can approve or merge, and no one but the merger relies on the record |
| 2 | a second account the agent cannot use exists, and required approvals are available on this repo |
| 3 | the agent runs where your token, SSH key and signing key are absent: a hosted agent, an app in CI, or a separate OS user |
| 4 | drafts: always; a label and required check: required checks are available |
| 5 | required checks are available, and the approval needs hardware touch, re-authentication or an environment reviewer (public repo or Enterprise) |
| 6 | 2, 3 or 5 already proves who acted, or the record is stated to be intent only |
| 7 | a model run per change is affordable, and a person reads the report before clearing the hold |

Among what is left: for two people, 2 is the proof. For one developer, 3 with a hosted agent that
cannot merge makes the merge click yours, and costs an identity; 5 needs a hardware key and a check
you maintain. 4 is free and cheap to clear, which is its weakness; 6 and 7 add history and focus,
not proof. If nothing proves who acted, say so in writing — "a hold, not proof" — and keep the hold.

**At more than one developer.** The approval must come from someone other than the author and the
last pusher (2), and whoever clears a hold (4) or signs a record (6) must not be the one who
drafted — a teammate, not the drafter. Code owners route docs to people who can judge them. The
credential rule still applies: an agent shared by the team must not run as any one of them.

**Examples.** One developer whose agent runs as a GitHub App on a public repo: 3 plus 2 with
most-recent-push approval, applied to administrators — the app cannot approve its own pull request
and the person's approval is theirs, so a label adds nothing; this points away from our choice. A four-person team on
a paid plan: 2 with code owners on `docs/` and stale approvals dismissed, plus 7 for long docs. One
developer whose agent must run as them locally: 5 — a touch-required signed tag checked in CI — or
4 with the gap stated. A mailing-list project: 6's trailers, where the mailing-list reply, not the
trailer, is the proof; see
[`kernel-no-ai-signed-off-by.md`](../examples/kernel-no-ai-signed-off-by.md).

**Our own choice.** One personal account on a public repo, and the agent runs with that account's
token — so 2 is out *on fit*, and every record we can produce is intent. We use 4 (a
`waiting-human-review` label and required check), 6 (an empty `signed_off:` on the drafting pull
request, then a date and a [`SIGNOFF.md`](SIGNOFF.md) line in a separate commit) and 7 (a
`review-doc` report named in the entry). It costs one label clear and one follow-up pull request
per doc. What went wrong: before the hold, #50 merged its own sign-off three minutes after opening
([`self-signed-signoff-pr-50.md`](../examples/self-signed-signoff-pr-50.md)); a ledger line
was removed and re-added seven seconds apart, pushed straight to `main`, to satisfy the
separate-commit rule
([`signoff-same-commit-violation.md`](../examples/signoff-same-commit-violation.md)); we emptied the
ledger. Row 6 does not hold for us — nothing proves who wrote a ledger line — so we keep 6 for its
dates and history and call it intent. Not taken yet: 3 (a hosted agent, with us merging) or 5 (a
hardware-signed tag), either of which would close the first gap below. Open gaps: the same account can clear the label, and #70's was cleared by it with no way to
tell person from agent; administrators bypass protection on this repo; 3 and 5 are not in place.
The hold, the ledger and its checks:
[`review-hold-and-signoff-ledger.md`](../resources/review-hold-and-signoff-ledger.md). The history
behind the hold is in [#53](https://github.com/Osasuwu/jarvis-oss/issues/53); the gate structure
around it is [#44](https://github.com/Osasuwu/jarvis-oss/issues/44).
