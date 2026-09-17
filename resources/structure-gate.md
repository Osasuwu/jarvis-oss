---
pairs_with: docs/doc-structure-gate.md
harnesses: all — the gate is a Python script run by pytest in GitHub Actions and locally; it does not depend on the agent's harness (see docs/harnesses.md)
cost: one Actions job per pull request (checkout with full history, Python 3.12, pytest), a few seconds of local pytest; maintaining the parser and its tests when the doc contract changes
---

# Structure gate

The custom CI script that [`doc-structure-gate.md`](../docs/doc-structure-gate.md) calls option 8,
as this repo runs it:

- **Rules:** [`tests/structure_gate.py`](../tests/structure_gate.py) — `check_tree(root)` returns
  a list of violations, each with a path and a code:
  - docs under `docs/`: `doc_missing_key:<key>` for `applies_when`, `applies_when_not`,
    `signed_off`; `doc_over_size_cap` over 20000 bytes; `boundary_evidence_unresolvable` for a
    relative link that does not resolve to a file;
  - examples: `example_missing_key:fit` / `:pairs_with`, `example_missing_provenance` (needs
    `last_seen`, or `source` + `verified`), `example_last_seen_stale` after 180 days;
  - resources: `resource_missing_key:<key>` for `pairs_with`, `harnesses`, `cost`;
  - both: `pairs_with_unresolvable` for any comma-separated target that is not a file;
  - sign-off: `signoff_missing_entry`, `signoff_missing_facts`, `signoff_same_commit` against
    [`SIGNOFF.md`](../docs/SIGNOFF.md).
- **Tests:** [`tests/test_structure_gate.py`](../tests/test_structure_gate.py) — fixtures for each
  violation code, plus a run against the real tree that must return no violations.
- **CI:** [`structure-gate.yml`](../.github/workflows/structure-gate.yml), on every pull request,
  with `fetch-depth: 0` because the sign-off rules read git history. `structure-gate` is a
  required check on `main`.

To adopt: copy the script and its tests, change the key tuples and the size cap to your contract,
and make the job a required check. The frontmatter parser reads flat `key: value` lines only; a
YAML list or multi-line value will not parse.

Not checked: section headings and their order, option fields, and whether every doc has an
example and a resource pointing at it.

## How you know it ran

- On a pull request, the `structure-gate` check lists `tests/test_structure_gate.py` results. A
  failure prints the violation code and the path, for example
  `doc_missing_key:applies_when` with the doc it names.
- Locally: `python -m pytest tests/test_structure_gate.py -q` passes on a clean tree. Delete
  `applies_when:` from any doc and rerun; it must fail. If it passes, the tree test is not
  pointed at the repo root.
- If the check never appears on a pull request, the workflow is disabled or not triggered; if it
  is red and the merge button still works, it is not required — see
  [`doc-check-in-no-workflow.md`](../examples/doc-check-in-no-workflow.md).
