---
pairs_with: docs/private-literal-scrub.md
harnesses: all — the scrub is a Python script run by GitHub Actions on pull requests; it does not depend on the agent's harness (see docs/harnesses.md). It needs GitHub Actions secrets, which fork pull requests do not receive.
cost: one step in the gitleaks job per pull request (Python 3.12, a walk of the checkout); a repository secret someone must create and keep current; a red check on every fork pull request
---

# Personal-literal scrub

What [`private-literal-scrub.md`](../docs/private-literal-scrub.md) calls option 5, as this repo
runs it:

- **Script:** [`scripts/scrub_personal_literals.py`](../scripts/scrub_personal_literals.py). Reads
  `PERSONAL_LITERALS` (one literal per line, blank lines ignored), walks `GITHUB_WORKSPACE`, and
  fails if any file contains any literal as an exact substring. A hit prints the file path with
  "values withheld" — never the literal. An empty or unset list fails.
- **Workflow:** [`gitleaks.yml`](../.github/workflows/gitleaks.yml), on every pull request, after
  gitleaks. The secret is passed only to the scrub step's environment.
- **Secret:** `PERSONAL_LITERALS`, a repository secret — one entry per line.

To adopt: copy the script and the step, then create the secret
(`gh secret set PERSONAL_LITERALS < list.txt`, from a file outside the repo) and make the job a
required check.

**Not covered:** git history, commit messages, branch names, pull request and issue text; any
variant of a literal (case, spacing, a split across lines); fork pull requests, which fail. The
push has already happened when it runs. Keep each literal specific enough that it cannot occur by
chance, or every pull request goes red. The job log masks the secret by exact match only, so the
script must never print the list or a transformed form of it.

## How you know it ran

- **Checked something:** the step log ends with
  `Scrub clean — checked N literal(s); none found in the tree.` N is the number of entries in the
  secret. An older log line without a count, `Scrub clean — no personal literals found in the tree.`,
  proves nothing — that is what it printed with no list at all
  ([`scrub-without-literals-reported-clean.md`](../examples/scrub-without-literals-reported-clean.md)).
- **No list:** the step fails with "No personal literals configured: set the PERSONAL_LITERALS
  repository secret…". On a fork pull request this is expected.
- **Catches something:** never plant a real entry — the branch is public the moment you push it.
  Add a canary to the secret, a random string that means nothing (`canary-7f3c9a`), then put that
  string in a file on a throwaway branch and open a pull request; the step must fail and name the
  file. If it passes, the secret does not hold what you think; matching is exact.
