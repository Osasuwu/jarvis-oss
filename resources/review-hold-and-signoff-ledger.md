---
pairs_with: docs/publishing-discipline.md
harnesses: all — the hold and the ledger checks run in GitHub Actions, not in the agent's harness; review-doc is a skill file any harness that loads skills can run (see docs/harnesses.md). The hold needs GitHub branch protection with required status checks.
cost: one Actions job per pull request event on the hold, one structure-gate run per pull request; one label clear and one follow-up sign-off pull request per doc; a model run per review-doc pass
---

# Review hold, sign-off ledger and review report

What [`publishing-discipline.md`](../docs/publishing-discipline.md) calls options 4, 6 and 7, as
this repo runs them:

- **Hold (option 4):** [`waiting-human-review.yml`](../.github/workflows/waiting-human-review.yml).
  On a freshly opened or ready-for-review pull request with no reviewer requested, it adds a
  `waiting-human-review` label and fails. On every later event it fails while the label is on or
  a review request is pending. Nothing removes the label automatically. The job name must be a
  required status check on the default branch, or a red run blocks nothing.
- **Ledger (option 6):** [`SIGNOFF.md`](../docs/SIGNOFF.md) — entry format
  `` - `docs/<doc>.md`: <date>; facts: <human | report URL> `` — plus the doc's own `signed_off:`
  field, empty on the drafting pull request.
- **Ledger checks:** [`tests/structure_gate.py`](../tests/structure_gate.py) —
  `signoff_missing_entry` (date set, no ledger line), `signoff_missing_facts` (a line whose date matches and
  which has no `facts:`), `signoff_same_commit` (line added in the commit that last changed the doc body). Run
  by [`structure-gate.yml`](../.github/workflows/structure-gate.yml).
- **Review report (option 7):** [`review-doc`](../.agents/skills/review-doc/SKILL.md), run in a
  context that did not write the doc; its report URL goes in the entry's `facts:`.

To adopt: copy the workflow, create the label, and add `waiting-human-review` to the required
checks of your default branch. The ledger checks need `fetch-depth: 0` on checkout — they read
the doc's git history.

**What none of this proves:** that a person, rather than an agent holding the same token, cleared
the label or wrote the ledger line. See the doc's "What every option depends on".

## How you know it ran

- **Hold:** a new pull request gets the `waiting-human-review` label within a minute, and the
  `waiting-human-review` check is red with "Human review is owed". Clear the label and the check
  re-runs green on the `unlabeled` event. If the label never appears, the workflow is not
  installed or lacks `issues: write`. If the check is red and the merge button still works, it is
  not a required check — or you are an administrator and protection does not apply to you.
- **Ledger checks:** fill in `signed_off:` on a doc without adding a ledger line; the
  `structure-gate` check goes red naming `signoff_missing_entry` and the doc path. A green run
  prints the pytest summary for `tests/test_structure_gate.py` and
  `tests/test_jarvis_setup_skill.py`.
- **Review report:** the report is a comment on the pull request, and the ledger entry's `facts:`
  URL opens it. An entry with `facts: human` means no report was used.
