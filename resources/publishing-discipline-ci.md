---
pairs_with: docs/publishing-discipline.md
harnesses: all — see docs/harnesses.md
cost: CI minutes on every PR (structure-gate + gitleaks/scrub jobs), plus the author's own frontmatter fields and a second, separately-committed ledger line
---

# Publishing-discipline CI

The machinery behind [`publishing-discipline.md`](../docs/publishing-discipline.md):

- Frontmatter + link-resolution contract: [`tests/structure_gate.py`](../tests/structure_gate.py)
- Two-way sign-off ledger: [`docs/SIGNOFF.md`](../docs/SIGNOFF.md)
- Personal-literal scrub: [`scripts/scrub_personal_literals.py`](../scripts/scrub_personal_literals.py)
- CI wiring: [`.github/workflows/structure-gate.yml`](../.github/workflows/structure-gate.yml),
  [`.github/workflows/gitleaks.yml`](../.github/workflows/gitleaks.yml)

## How you know it ran

Both checks post as required GitHub status checks on the PR (`structure-gate`, `gitleaks`). A red
`structure-gate` names the exact violation code (`doc_missing_key:signed_off`,
`signoff_same_commit`, `boundary_evidence_unresolvable`, ...); a red scrub step in `gitleaks`
names only the file path that matched a private literal, never the literal's value.
